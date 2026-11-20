"""The referee on a pull request (TAX-17): tools/pr_referee.py and its workflows."""

import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dataclasses import asdict

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import pr_referee  # noqa: E402
import referee  # noqa: E402
from test_referee import FIXTURES, NOW, QUIET, REPLIES, Block, FakeFetcher, asked, reply  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"


class FakeBatchClient:
    """The Message Batches API: every request counted as `counted` input tokens, answered with
    `output` output tokens and the reply of test_referee for its product key. `pending` is how often
    the batch says it is still running before it ends."""

    def __init__(self, counted=1000, output=500, pending=0, missing=()):
        self.counted, self.output, self.pending, self.missing = counted, output, pending, set(missing)
        self.batches, self.cancelled, self.counts = [], [], 0

    def count(self, request):
        self.counts += 1
        return self.counted

    def submit(self, requests):
        self.batches.append(requests)
        return f"batch_{len(self.batches)}"

    def status(self, batch_id):
        if self.pending:
            self.pending -= 1
            return "in_progress"
        return "ended"

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)
        self.pending = 0

    def results(self, batch_id):
        for cid, request in self.batches[int(batch_id.split("_")[1]) - 1]:
            if cid in self.missing:
                yield cid, "expired"
                continue
            message = request["messages"][0]["content"]
            key = re.search(r"# products/[^\n]+\nkey: (\S+)", message).group(1)
            usage = {"input_tokens": self.counted, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                     "output_tokens": self.output, "web_search_requests": 0}
            yield cid, referee.Response(content=[Block("text", asked(REPLIES.get(key, reply()), request))],
                                        stop_reason="end_turn", usage=usage)


class Args:
    def __init__(self, out, **kw):
        self.model, self.effort, self.max_tokens = "claude-opus-5-5", "low", pr_referee.PR_MAX_TOKENS
        self.budget, self.pr_cap, self.day_cap, self.wait, self.live = 20.0, 2.0, 5.0, 3600, True
        self.prompt = str(FIXTURES / "prompt.md")
        self.schema = str(REPO / "referee/verdict.schema.json")
        self.prices = str(REPO / "referee/prices.yaml")
        self.out, self.pr = str(out), 7
        self.__dict__.update(kw)


class PrRefereeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def gated(self, ids=None):
        proposals = pr_referee.testset_proposals(FIXTURES / "cases.yaml", FIXTURES / "data")
        if ids:
            proposals = [p for p in proposals if p["id"] in ids]
        return pr_referee.gate_proposals(proposals, FakeFetcher(), FIXTURES / "data", log=QUIET)

    def judged(self, client=None, ids=None, **kw):
        items = self.gated(ids)
        client = client or FakeBatchClient()
        run_dir = pr_referee.judge(items, Args(self.tmp / "runs", **kw), client=client, now=NOW, log=QUIET,
                                   sleep=lambda s: None)
        return {i["id"]: i for i in items}, run_dir, client

    # the artifact of the collect step, which comes from code of the pull request

    def write(self, data):
        path = self.tmp / "proposal.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_a_good_proposal_loads(self):
        data = {"version": 1, "pr": 12, "head_sha": SHA, "files": {"products/acme/widget.yaml": "key: acme/widget\n"}}
        self.assertEqual(pr_referee.load_proposal(self.write(data))["pr"], 12)

    def test_the_proposal_is_checked_strictly(self):
        good = {"version": 1, "pr": 12, "head_sha": SHA, "files": {"products/acme/widget.yaml": "x"}}
        for bad in (
            {**good, "pr": "12; rm -rf /"},
            {**good, "pr": True},
            {**good, "head_sha": "main"},
            {**good, "removed": ["products/synology/ds124.yaml"]},  # only the test set may take files out
            {**good, "files": {"tools/referee.py": "x"}},
            {**good, "files": {"products/../tools/referee.py": "x"}},
            {**good, "files": {".github/workflows/referee.yml": "x"}},
            {**good, "files": {"products/acme/widget.yaml": "x" * (pr_referee.MAX_BYTES + 1)}},
            {**good, "files": {f"products/acme/w{i}.yaml": "x" for i in range(pr_referee.MAX_FILES + 1)}},
        ):
            with self.subTest(bad=str(bad)[:80]), self.assertRaises(SystemExit):
                pr_referee.load_proposal(self.write(bad))

    def test_a_product_takes_its_publisher_file_along(self):
        files = {"products/acme/widget.yaml": "key: acme/widget\npublisher: acme\n", "publishers/acme.yaml": "key: acme\n",
                 "publishers/other.yaml": "key: other\n"}
        self.assertEqual([(p["id"], sorted(p["files"])) for p in pr_referee.group(files)], [
            ("products/acme/widget.yaml", ["products/acme/widget.yaml", "publishers/acme.yaml"]),
            ("publishers/other.yaml", ["publishers/other.yaml"]),
        ])

    # the gate: code first, for free

    def test_code_settles_what_it_can_before_the_referee(self):
        items = {i["id"]: i for i in self.gated()}
        self.assertEqual(items["B01"]["outcome"], "reject")
        self.assertEqual(items["B01"]["rule"], "TE1")
        self.assertEqual((items["B20"]["outcome"], items["B20"]["rule"]), ("reject", "TP1"))
        self.assertEqual(items["G01"]["outcome"], "pending")
        self.assertIn("<decided>", items["G01"]["message"])
        self.assertNotIn("TP5", items["G01"]["ai_rules"], "the versions are on the publisher's domain")

    def test_a_source_on_an_ai_chat_is_rejected_and_not_fetched(self):
        fetcher = FakeFetcher()
        proposal = {"id": "products/synology/ds140.yaml", "files": {"products/synology/ds140.yaml": (
            "key: synology/ds140\npublisher: synology\nname: DS140\nkind: device\n"
            "source: https://chatgpt.com/share/abc\nversions:\n  source: https://www.synology.com/en-global/releaseNote/DSM\n")}}
        item = pr_referee.gate_proposals([proposal], fetcher, FIXTURES / "data", log=QUIET)[0]
        self.assertEqual((item["outcome"], item["rule"], item["by"]), ("reject", "TE1", "code"))
        self.assertEqual(fetcher.urls, [])

    def test_a_fork_is_judged_by_code_only(self):
        items = self.gated()
        pr_referee.park_pending(items, "a pull request from a fork is judged by code only")
        self.assertNotIn("pending", {i["outcome"] for i in items})
        self.assertEqual({i["outcome"] for i in items if i["by"] == "code"} - {"parked"}, {"reject"})
        g01 = next(i for i in items if i["id"] == "G01")
        self.assertEqual(g01["outcome"], "parked")
        self.assertIn("fork", g01["reason"])

    # the referee: one batch for the rest

    def test_outcomes_of_the_batch(self):
        items, run_dir, client = self.judged()
        self.assertEqual({i: (it["outcome"], it["by"]) for i, it in items.items()}, {
            "G01": ("accept", "ai"),
            "G02": ("parked", "ai"),  # a quote that is not on the page: doubt, so parked
            "H01": ("parked", "ai"),  # the reply is not JSON
            "B01": ("reject", "code"),
            "B07": ("alias", "ai"),
            "B20": ("reject", "code"),
            "B21": ("accept", "ai"),  # the fake referee lets it through
            "B22": ("accept", "ai"),
        })
        self.assertEqual(len(client.batches), 1, "one batch for all of them")
        self.assertEqual(len(client.batches[0]), 6)
        self.assertIn("TP1", items["G02"]["rule"])
        self.assertIn("quote not found", items["G02"]["reason"])
        self.assertEqual(items["B07"]["duplicate_of"], "synology/ds224-plus")
        info = json.loads((run_dir / "run.json").read_text())
        self.assertEqual((info["condition"], info["pr"], info["batch"], info["effort"]), ("pr", 7, True, "low"))

    def test_a_batch_costs_half(self):
        items, run_dir, _ = self.judged(ids=["G01"])
        normal = referee.cost(items["G01"]["usage"], "claude-opus-5-5", referee.load_prices())
        self.assertAlmostEqual(items["G01"]["cost"], normal / 2)
        self.assertAlmostEqual(referee.spent_before(self.tmp / "runs"), normal / 2)

    def test_the_whole_batch_fits_what_is_left(self):
        budget = referee.Budget(0.30, 0.0, "claude-opus-5-5", referee.load_prices(), 8000, batch=True)
        client = FakeBatchClient(counted=2000)
        fitted, left_out = referee.fit_batch(client, budget, [(f"p{n}", {"messages": []}) for n in range(5)])
        p_in = referee.input_price("claude-opus-5-5", budget.prices, True)
        p_out = referee.output_price("claude-opus-5-5", budget.prices, True)
        worst = sum((2000 * 1.05 + 100) * p_in + r["max_tokens"] * p_out for _, r in fitted)
        self.assertLessEqual(worst, 0.30)
        self.assertEqual((len(fitted), len(left_out)), (3, 2), "each at full max_tokens, the rest waits")
        self.assertEqual({r["max_tokens"] for _, r in fitted}, {8000})

    def test_one_request_may_go_with_less_than_full_max_tokens(self):
        budget = referee.Budget(0.05, 0.0, "claude-opus-5-5", referee.load_prices(), 8000, batch=True)
        fitted, left_out = referee.fit_batch(FakeBatchClient(counted=2000), budget, [("p0", {}), ("p1", {})])
        self.assertEqual(len(fitted), 1)
        self.assertTrue(referee.MIN_OUTPUT <= fitted[0][1]["max_tokens"] < 8000)
        self.assertEqual(left_out, [("p1", {})])

    def test_batches_go_in_rounds_until_the_budget_is_used(self):
        budget = referee.Budget(0.30, 0.0, "claude-opus-5-5", referee.load_prices(), 8000, batch=True)
        client = FakeBatchClient(counted=2000, output=500)
        request = {"messages": [{"content": "# products/synology/ds124.yaml\nkey: synology/ds124\n"}],
                   "output_config": {"format": {"schema": {"properties": dict.fromkeys(referee.RULES)}}}}
        requests = [(f"p{n}", request) for n in range(5)]
        answers, never = referee.batch_rounds(client, budget, requests, log=QUIET, sleep=lambda s: None)
        self.assertGreater(len(client.batches), 1, "the real cost frees room for a next round")
        self.assertEqual(len(answers) + len(never), 5)
        self.assertEqual(len(answers), 5)
        self.assertLessEqual(budget.spent, 0.30)

    def test_the_pull_request_ceiling_parks_what_does_not_fit(self):
        items, _, client = self.judged(pr_cap=0.01)
        self.assertEqual(client.batches, [], "nothing fits, so nothing is sent")
        self.assertEqual(items["G01"]["outcome"], "parked")
        self.assertIn("pull request ceiling", items["G01"]["reason"])

    def test_the_day_ceiling_counts_the_runs_of_today(self):
        self.judged(ids=["G01"], day_cap=0.05)
        spent = referee.spent_before(self.tmp / "runs")
        on_pr, on_day = pr_referee.spent_on(self.tmp / "runs", 7, NOW.strftime("%Y%m%d"))
        self.assertAlmostEqual(on_pr, spent)
        self.assertAlmostEqual(on_day, spent)
        self.assertEqual(pr_referee.spent_on(self.tmp / "runs", 8, "20261114"), (0.0, 0.0))
        later = datetime(2026, 11, 13, 13, 0, 0, tzinfo=timezone.utc)
        items = self.gated(["G01"])
        client = FakeBatchClient()
        pr_referee.judge(items, Args(self.tmp / "runs", pr=8, day_cap=spent + 0.001), client=client, now=later,
                         log=QUIET, sleep=lambda s: None)
        self.assertEqual(items[0]["outcome"], "parked")
        self.assertIn("day ceiling", items[0]["reason"])

    def test_a_dry_run_sends_nothing(self):
        items = self.gated()
        self.assertIsNone(pr_referee.judge(items, Args(self.tmp / "runs", live=False), client=None, log=QUIET))
        self.assertIn("dry run", next(i for i in items if i["id"] == "G01")["reason"])

    def test_a_batch_that_takes_too_long_is_cancelled(self):
        client = FakeBatchClient(pending=10, missing={"p0"})
        items, _, _ = self.judged(client=client, ids=["G01", "B07"], wait=60)
        self.assertEqual(client.cancelled, ["batch_1"])
        self.assertEqual(items["G01"]["outcome"], "parked")
        self.assertIn("did not answer", items["G01"]["reason"])
        self.assertEqual(items["B07"]["outcome"], "alias")

    def test_the_key_is_never_written(self):
        items, run_dir, _ = self.judged()
        out = pr_referee.write_verdicts(list(items.values()), self.tmp / "verdicts", 7, SHA, run_dir=run_dir)
        self.assertEqual(out["counts"], {"accept": 3, "reject": 2, "alias": 1, "parked": 2})
        for path in [*run_dir.iterdir(), *(self.tmp / "verdicts").iterdir()]:
            self.assertNotIn("sk-ant", path.read_text())

    def test_the_comment_has_a_line_per_file(self):
        items, _, _ = self.judged()
        text = pr_referee.comment(list(items.values()), 7, 0.01)
        self.assertIn("## Referee on #7", text)
        self.assertEqual(len([line for line in text.splitlines() if line.startswith("| `")]), 8)
        self.assertIn("python tools/pr_referee.py parked --live", text)

    # the parking list

    def root_copy(self):
        root = self.tmp / "root"
        shutil.copytree(FIXTURES / "data", root)
        (root / "referee").mkdir()
        return root

    def test_apply_moves_parked_to_the_list_and_takes_rejected_out(self):
        root = self.root_copy()
        files = {"products/acme/widget.yaml": "key: acme/widget\npublisher: acme\n", "publishers/acme.yaml": "key: acme\n"}
        for rel, text in {**files, "products/synology/ds999.yaml": "key: synology/ds999\n"}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        verdicts = {"pr": 7, "verdicts": [
            {"id": "products/acme/widget.yaml", "key": "acme/widget", "outcome": "parked", "rule": "TP5",
             "reason": "TP5: the source does not show who publishes", "files": files},
            {"id": "products/synology/ds999.yaml", "key": "synology/ds999", "outcome": "reject", "rule": "TE1",
             "reason": "x", "files": {"products/synology/ds999.yaml": "key: synology/ds999\n"}},
            {"id": "products/synology/ds124.yaml", "key": "synology/ds124", "outcome": "accept", "rule": "", "reason": "",
             "files": {"products/synology/ds124.yaml": "x"}},
        ]}
        pr_referee.apply(verdicts, root, now=NOW, log=QUIET)
        self.assertFalse((root / "products/acme/widget.yaml").exists())
        self.assertFalse((root / "publishers/acme.yaml").exists(), "no product of acme stays")
        self.assertFalse((root / "products/synology/ds999.yaml").exists())
        self.assertTrue((root / "products/synology/ds124.yaml").exists())
        self.assertTrue((root / "publishers/synology.yaml").exists())
        parked = pr_referee.load_parked(root / "referee/parked.yaml")
        self.assertEqual([(e["key"], e["rule"], e["since"], e["pr"]) for e in parked], [("acme/widget", "TP5", "2026-11-13", 7)])
        self.assertEqual(parked[0]["files"], files)

    def test_the_parking_list_is_judged_again_with_one_command(self):
        root = self.root_copy()
        ds124 = (root / "products/synology/ds124.yaml").read_text()
        (root / "products/synology/ds124.yaml").unlink()
        widget = {"products/acme/widget.yaml": "key: acme/widget\npublisher: acme\nname: Widget\nkind: software\n"
                  "source: https://acme.example/widget\nversions:\n  source: https://acme.example/widget\n",
                  "publishers/acme.yaml": "key: acme\nname: Acme\nsource: https://acme.example/about\naliases: []\n"}
        pr_referee.save_parked([
            {"key": "synology/ds124", "path": "products/synology/ds124.yaml", "rule": "TP1", "reason": "doubt",
             "date": "2026-11-01", "pr": 3, "files": {"products/synology/ds124.yaml": ds124}},
            {"key": "acme/widget", "path": "products/acme/widget.yaml", "rule": "TP5", "reason": "doubt",
             "date": "2026-11-01", "pr": 4, "files": widget},
        ], root / "referee/parked.yaml")
        counts = pr_referee.reprocess(Args(self.tmp / "runs", output=str(self.tmp / "v")), fetcher=FakeFetcher(),
                                      client=FakeBatchClient(), data_root=root, now=NOW, log=QUIET, sleep=lambda s: None)
        self.assertEqual(counts["accept"], 1)
        self.assertEqual((root / "products/synology/ds124.yaml").read_text(), ds124, "accepted: written to the data")
        left = pr_referee.load_parked(root / "referee/parked.yaml")
        self.assertEqual([(e["key"], e["since"], e["checked"], e["pr"]) for e in left],
                         [("acme/widget", "2026-11-01", "2026-11-13", 4)], "the first day stays")

    def test_the_parking_list_in_the_repo_is_valid(self):
        data = yaml.safe_load((REPO / "referee/parked.yaml").read_text())
        self.assertIn(data["version"], (1, 2))
        for e in data["parked"] or []:
            self.assertTrue({"key", "path", "waits_for", "owner", "rule", "reason", "since", "files"} <= set(e))

    def test_the_ceilings_per_pull_request_and_day_stay_and_the_budget_is_50(self):
        """17 November 2026: the limit for every run goes to $50; the ceilings of TAX-17 stay."""
        import argparse
        p = argparse.ArgumentParser()
        pr_referee.add_judge_args(p)
        args = p.parse_args([])
        self.assertEqual((args.pr_cap, args.day_cap, args.budget), (2.00, 5.00, 50.00))
        for name in ("referee-pr.yml", "referee-pr-testset.yml"):
            text = (REPO / ".github/workflows" / name).read_text()
            self.assertIn("vars.REFEREE_BUDGET || '50.00'", text, name)
            self.assertIn("vars.REFEREE_DAY_CAP || '5.00'", text, name)

    # the real test set, as one pull request

    def test_the_test_set_becomes_104_proposals(self):
        proposals = pr_referee.testset_proposals(REPO / "referee/testset/cases.yaml")
        self.assertEqual(len(proposals), 104)
        self.assertEqual(len([p for p in proposals if p["id"].startswith("B")]), 63)
        self.assertTrue(all(p["case"]["expect"] for p in proposals))

    def test_score_counts_parked_as_human(self):
        items = [{"id": "B07", "outcome": "parked", "case": {"expect": ["reject", "parked"]}},
                 {"id": "B08", "outcome": "accept", "case": {"expect": ["reject"]}},
                 {"id": "G01", "outcome": "accept", "case": {"expect": ["accept"]}}]
        text = pr_referee.score(items)
        self.assertIn("Bad cases accepted (target 0): 1 B08", text)
        self.assertIn("Not in `expect`: 1 B08", text)


def lookalike(n: str) -> dict:
    """A product of a publisher whose key and domain look like an existing one (TV3)."""
    return {f"products/synoiogy/ds{n}.yaml": f"key: synoiogy/ds{n}\npublisher: synoiogy\nname: DS{n}\nkind: device\n"
            f"source: https://www.synoiogy.com/products/DS{n}\nversions:\n  source: https://www.synoiogy.com/releaseNote/DSM\n"
            "identifiers: []\naliases: []\n",
            "publishers/synoiogy.yaml": "key: synoiogy\nname: Synoiogy\nsource: https://www.synoiogy.com/company\naliases: []\n"}


class ClosedFetcher(FakeFetcher):
    """A reader for which the sites in `closed` refuse."""

    def __init__(self, *closed):
        super().__init__()
        self.closed = closed

    def fetch(self, url, product=""):
        if any(c in url for c in self.closed):
            self.urls.append(url)
            return referee.Source(url, "refused")
        return super().fetch(url, product)


class ParkingListTest(unittest.TestCase):
    """TAX-29: every parked proposal waits for one thing, with an owner and a term (decided 18 November 2026)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "root"
        shutil.copytree(FIXTURES / "data", self.root)
        (self.root / "referee").mkdir()
        self.terms = pr_referee.load_terms()
        ds124 = (self.root / "products/synology/ds124.yaml").read_text()
        (self.root / "products/synology/ds124.yaml").unlink()
        self.ds124 = {"products/synology/ds124.yaml": ds124}

    def entry(self, waits, since="2026-11-01", files=None, **kw):
        files = files or self.ds124
        rel = next(r for r in files if r.startswith("products/"))
        return {"key": pr_referee.key_of(files), "path": rel, "publisher": pr_referee.publisher_of(files), "waits_for": waits,
                "rule": "TP1", "reason": "fixture", "since": since, "pr": 3, "files": files, **kw}

    def run_list(self, entries, fetcher=None, client=None):
        pr_referee.save_parked(entries, self.root / "referee/parked.yaml")
        self.fetcher, self.client = fetcher or FakeFetcher(), client or FakeBatchClient()
        counts = pr_referee.reprocess(Args(self.tmp / "runs", output=str(self.tmp / "v")), fetcher=self.fetcher,
                                      client=self.client, data_root=self.root, now=NOW, log=QUIET, sleep=lambda s: None)
        return counts, {e["key"]: e for e in pr_referee.load_parked(self.root / "referee/parked.yaml")}

    def gate(self, files):
        rel = next(r for r in files if r.startswith("products/"))
        return pr_referee.gate_proposals([{"id": rel, "files": files}], FakeFetcher(), self.root, log=QUIET)[0]

    # 1. exactly one of three reasons, and the submitter reads it on the pull request

    def test_every_parked_proposal_waits_for_exactly_one_reason(self):
        proposals = pr_referee.testset_proposals(FIXTURES / "cases.yaml", FIXTURES / "data")
        proposals.append({"id": "products/synoiogy/ds124.yaml", "files": lookalike("124")})
        items = pr_referee.gate_proposals(proposals, FakeFetcher(), FIXTURES / "data", log=QUIET)
        pr_referee.judge(items, Args(self.tmp / "runs"), client=FakeBatchClient(), now=NOW, log=QUIET, sleep=lambda s: None)
        waits = {i["id"]: i["waits_for"] for i in items if i["outcome"] == "parked"}
        self.assertEqual(waits, {"G02": "better-proposal",  # doubt on sources that were read
                                 "H01": "event",  # the answer was unusable
                                 "products/synoiogy/ds124.yaml": "decision"})  # held by code (TV3)
        self.assertEqual({i["waits_for"] for i in items if i["outcome"] != "parked"}, {""})
        text = pr_referee.comment(items, 7)
        self.assertIn("| `G02` | parked, waits for better-proposal |", text)
        for w in pr_referee.WAITS_FOR:
            self.assertIn(f"waits for *{w}*", text)

    def test_a_ceiling_and_an_unread_source_are_events(self):
        items = pr_referee.gate_proposals(pr_referee.testset_proposals(FIXTURES / "cases.yaml", FIXTURES / "data"),
                                          FakeFetcher(), FIXTURES / "data", log=QUIET)
        pr_referee.judge(items, Args(self.tmp / "runs", pr_cap=0.01), client=FakeBatchClient(), now=NOW, log=QUIET)
        self.assertEqual({i["waits_for"] for i in items if i["outcome"] == "parked"}, {"event"})
        item = {"sources": [asdict(referee.Source("https://a.example/", "refused")),
                            asdict(referee.Source("https://b.example/", "200", "text"))]}
        self.assertEqual(pr_referee.unread(item), ["https://a.example/"])
        item["sources"].append(asdict(referee.Source("https://c.example/", "200", "text", instead_of="https://a.example/")))
        self.assertEqual(pr_referee.unread(item), [], "a source found instead counts as read")

    # 2. an event is judged again without a person; the AI only when the source is read now

    def test_an_event_is_judged_again_only_when_its_sources_are_read(self):
        counts, left = self.run_list([self.entry("event")], fetcher=ClosedFetcher("synology.com"))
        self.assertEqual(self.client.batches, [], "the source is still closed: no call to the AI")
        self.assertEqual((left["synology/ds124"]["waits_for"], left["synology/ds124"]["since"]), ("event", "2026-11-01"))
        self.assertIn("still not read", left["synology/ds124"]["reason"])
        counts, left = self.run_list(list(left.values()))
        self.assertEqual(len(self.client.batches), 1, "the source is read now: the referee judges it")
        self.assertEqual((counts["accept"], left), (1, {}))
        self.assertTrue((self.root / "products/synology/ds124.yaml").is_file())

    # 3. a better proposal is not judged again while it has not changed

    def test_a_better_proposal_is_not_judged_again_until_it_changes(self):
        same = self.entry("better-proposal", fingerprint=pr_referee.fingerprint(self.ds124))
        counts, left = self.run_list([same])
        self.assertEqual((self.fetcher.urls, self.client.batches, counts["untouched"]), ([], [], 1))
        self.assertEqual(left["synology/ds124"]["waits_for"], "better-proposal")
        changed = self.entry("better-proposal", fingerprint="0" * 16)
        counts, left = self.run_list([changed])
        self.assertEqual((len(self.client.batches), counts["accept"]), (1, 1))

    # 4. past its term an entry lapses, with the reason on its pull request; it may come again

    def test_an_entry_past_its_term_lapses_with_a_comment(self):
        widget = lookalike("200")
        entries = [self.entry("event", since="2026-08-15", pr=5),  # 90 days to the day: stays
                   self.entry("event", since="2026-08-14", files={"products/synology/ds124.yaml": "key: synology/ds777\n"}, pr=5),
                   self.entry("decision", since="2026-10-13", files=widget, pr=6, holds={"TV3": ["x"]})]  # 31 days
        counts, left = self.run_list(entries, fetcher=ClosedFetcher("synology.com"))
        self.assertEqual(counts["lapsed"], 2)
        self.assertEqual(sorted(left), ["synology/ds124"])
        self.assertEqual(sorted(p.name for p in (self.tmp / "v/lapsed").iterdir()), ["5.md", "6.md"])
        text = (self.tmp / "v/lapsed/5.md").read_text()
        self.assertIn("`synology/ds777` waited for event since 2026-08-14; its term of 90 days", text)
        self.assertIn("you may propose it again", text)
        verdicts = {"pr": 9, "verdicts": [{"id": "products/synoiogy/ds200.yaml", "key": "synoiogy/ds200", "outcome": "parked",
                                           "rule": "TV3", "reason": "again", "waits_for": "decision", "files": widget}]}
        pr_referee.apply(verdicts, self.root, now=NOW, log=QUIET)
        again = {e["key"]: e for e in pr_referee.load_parked(self.root / "referee/parked.yaml")}["synoiogy/ds200"]
        self.assertEqual((again["since"], again["pr"], again["lapses"]), ("2026-11-13", 9, "2026-12-13"), "a new term")

    # 5. a collision or an audit finding never lapses, and has its last day

    def test_a_collision_or_an_audit_never_lapses(self):
        entries = [self.entry("decision", since="2026-01-01", kind="collision"),
                   self.entry("decision", since="2026-11-10", kind="audit", files=lookalike("300"))]
        counts, left = self.run_list(entries)
        self.assertEqual((counts["lapsed"], counts["untouched"], self.fetcher.urls), (0, 2, []))
        self.assertEqual(left["synology/ds124"]["decide_by"], "2026-01-15")
        self.assertNotIn("lapses", left["synology/ds124"])
        waiting = pr_referee.waiting(list(left.values()), self.terms)
        self.assertEqual([(p, [x["decide_by"] for x in xs]) for p, xs in waiting.items()],
                         [("synology", ["2026-01-15"]), ("synoiogy", ["2026-11-24"])])

    # 6. a decision on a publisher is kept, and holds for its next proposal

    def test_a_decision_on_a_publisher_holds_for_its_next_proposal(self):
        first = self.gate(lookalike("124"))
        self.assertIn("TV3", first["held"])
        pr_referee.save_parked([pr_referee.park_entry({**first, "outcome": "parked", "rule": "TV3", "reason": "held",
                                                       "waits_for": "decision"}, "2026-11-10")],
                               self.root / "referee/parked.yaml")
        decided = pr_referee.decide("synoiogy", "approve", "its own company", self.root, by="beheerder", now=NOW, log=QUIET)
        self.assertEqual(len(decided), 1)
        kept = pr_referee.load_decisions(self.root / pr_referee.DECISIONS)
        self.assertTrue(kept and all((d["publisher"], d["decision"], d["date"], d["reason"]) ==
                                     ("synoiogy", "approve", "2026-11-13", "its own company") for d in kept))
        self.assertEqual(pr_referee.load_parked(self.root / "referee/parked.yaml")[0]["waits_for"], "event",
                         "approved: the referee is at turn again")
        nxt = self.gate(lookalike("224"))
        self.assertEqual((nxt["held"], nxt["outcome"]), ({}, "pending"), "the same reason holds it no more")
        other = self.gate({**lookalike("224"), "products/synoiogy/ds224.yaml":
                           lookalike("224")["products/synoiogy/ds224.yaml"].replace("synoiogy.com/products", "synologv.com/products")})
        self.assertIn("synologv.com", other["held"].get("TV3", ""), "another reason still holds it")

    def test_a_rejection_of_a_publisher_rejects_its_next_proposal(self):
        first = self.gate(lookalike("124"))
        pr_referee.save_parked([pr_referee.park_entry({**first, "outcome": "parked", "rule": "TV3", "reason": "held",
                                                       "waits_for": "decision"}, "2026-11-10")],
                               self.root / "referee/parked.yaml")
        pr_referee.decide("synoiogy", "reject", "imitates an existing publisher", self.root, now=NOW, log=QUIET)
        self.assertEqual(pr_referee.load_parked(self.root / "referee/parked.yaml"), [])
        nxt = self.gate(lookalike("224"))
        self.assertEqual((nxt["outcome"], nxt["rule"], nxt["by"]), ("reject", "TV3", "person"))
        self.assertIn("2026-11-13", nxt["reason"])
        self.assertIn("imitates an existing publisher", nxt["reason"])

    def test_decide_needs_something_that_waits(self):
        with self.assertRaises(SystemExit):
            pr_referee.decide("nobody", "approve", "x", self.root, log=QUIET)

    # 6b. a collision or an audit finding is settled per product (TAX-37)

    def settled(self, answer, kind, **kw):
        (self.root / "products/synology/ds124.yaml").write_text(self.ds124["products/synology/ds124.yaml"])  # in TAX
        pr_referee.save_parked([self.entry("decision", since="2026-11-10", kind=kind), self.entry("event", files=lookalike("300"))],
                               self.root / "referee/parked.yaml")
        out = pr_referee.settle("synology/ds124", answer, "checked the sources", self.root, by="beheerder", now=NOW, log=QUIET, **kw)
        left = {e["key"]: e for e in pr_referee.load_parked(self.root / "referee/parked.yaml")}
        return out, left, pr_referee.load_decisions(self.root / pr_referee.DECISIONS)

    def test_a_collision_where_the_party_in_taxnmy_stays(self):
        out, left, kept = self.settled("stays", "collision")
        self.assertEqual(len(out), 1)
        self.assertEqual(list(left), ["synoiogy/ds300"], "the proposal leaves the list; the rest is untouched")
        self.assertEqual(kept, [{"publisher": "synology", "rule": "collision", "subject": "synology/ds124", "decision": "stays",
                                 "date": "2026-11-13", "reason": "checked the sources", "by": "beheerder"}])
        self.assertTrue((self.root / "products/synology/ds124.yaml").is_file(), "the product in TAX stays")

    def test_a_collision_won_by_the_proposal_waits_for_the_referee(self):
        _, left, kept = self.settled("proposal", "collision")
        e = left["synology/ds124"]
        self.assertEqual((e["waits_for"], e["owner"], "kind" in e, "decide_by" in e), ("event", "referee", False, False))
        self.assertIn("checked the sources", e["reason"])
        self.assertEqual(kept[0]["decision"], "proposal")

    def test_an_audit_kept_or_to_fix_leaves_the_list(self):
        for answer in ("keep", "fix"):
            _, left, kept = self.settled(answer, "audit")
            self.assertNotIn("synology/ds124", left, answer)
            self.assertEqual([(d["rule"], d["decision"]) for d in kept][-1], ("audit", answer), "the last decision counts")
            self.assertTrue((self.root / "products/synology/ds124.yaml").is_file(), answer)

    def test_an_audit_removed_deletes_the_product(self):
        _, left, kept = self.settled("remove", "audit")
        self.assertNotIn("synology/ds124", left)
        self.assertFalse((self.root / "products/synology/ds124.yaml").exists())

    def test_settle_takes_only_an_answer_of_its_kind_and_changes_nothing_else(self):
        pr_referee.save_parked([self.entry("decision", since="2026-11-10", kind="collision"),
                                self.entry("decision", files=lookalike("300"), holds={"TV3": ["x"]})],
                               self.root / "referee/parked.yaml")
        before = (self.root / "referee/parked.yaml").read_text()
        for key, answer in (("synology/ds124", "remove"), ("synology/ds124", "approve"),
                            ("synoiogy/ds300", "keep"), ("nobody/x", "keep")):
            with self.assertRaises(SystemExit, msg=(key, answer)):
                pr_referee.settle(key, answer, "x", self.root, log=QUIET)
        self.assertEqual((self.root / "referee/parked.yaml").read_text(), before)
        self.assertFalse((self.root / pr_referee.DECISIONS).is_file() and pr_referee.load_decisions(self.root / pr_referee.DECISIONS))

    def test_decide_still_leaves_a_kind_alone(self):
        pr_referee.save_parked([self.entry("decision", since="2026-11-10", kind="collision", holds={"TV3": ["x"]})],
                               self.root / "referee/parked.yaml")
        with self.assertRaises(SystemExit):
            pr_referee.decide("synology", "approve", "x", self.root, log=QUIET)

    # 7. what waits for a decision, in one go, per publisher

    def test_what_waits_for_a_decision_is_one_list_per_publisher(self):
        a = self.gate(lookalike("124"))
        entries = [pr_referee.park_entry({**a, "outcome": "parked", "rule": "TV3", "reason": "held", "waits_for": "decision"},
                                         "2026-11-12"),
                   self.entry("decision", since="2026-11-05", files=lookalike("500"), holds={"TV3": ["x"]}),
                   self.entry("event")]
        pr_referee.save_parked(entries, self.root / "referee/parked.yaml")
        waiting = pr_referee.waiting(pr_referee.load_parked(self.root / "referee/parked.yaml"), self.terms)
        self.assertEqual(list(waiting), ["synoiogy"], "an event waits for no person")
        self.assertEqual([x["key"] for x in waiting["synoiogy"]], ["synoiogy/ds124", "synoiogy/ds500"])
        self.assertEqual(waiting["synoiogy"][0]["lapses"], "2026-12-12")
        self.assertIn("publisher 'synoiogy' looks like 'synology'", " ".join(waiting["synoiogy"][0]["holds"]["TV3"]))

    # the terms are in one place

    def test_the_terms_are_those_decided(self):
        """18 November 2026: 90, 90 and 30 days; a collision or an audit: 14 days."""
        self.assertEqual({w: t["lapses_after"] for w, t in self.terms["waits_for"].items()},
                         {"event": 90, "better-proposal": 90, "decision": 30})
        self.assertEqual({w: t["owner"] for w, t in self.terms["waits_for"].items()},
                         {"event": "referee", "better-proposal": "submitter", "decision": "person"})
        self.assertEqual(self.terms["decide_within"], {"collision": 14, "audit": 14})
        self.assertEqual(set(self.terms["waits_for"]), set(pr_referee.WAITS_FOR))


class KeyStaysTest(unittest.TestCase):
    """TAX-45 (TK3, TG13): a key stays; only a logged decision of a maintainer takes a product out."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "root"
        shutil.copytree(FIXTURES / "data", self.root)
        (self.root / "referee").mkdir()
        ds124 = self.root / "products/synology/ds124.yaml"
        ds124.write_text(ds124.read_text().replace("aliases: []", "aliases:\n- synology/diskstation-ds124"))
        self.ds124 = ds124.read_text()

    def gate(self, files=None, deleted=()):
        return {i["id"]: i for i in pr_referee.gate_proposals(pr_referee.group(files or {}, deleted), FakeFetcher(),
                                                              self.root, log=QUIET)}

    def test_a_deleted_product_is_rejected_on_tk3(self):
        items = self.gate(deleted=["products/synology/ds124.yaml"])
        i = items["products/synology/ds124.yaml"]
        self.assertEqual((i["outcome"], i["rule"], i["key"]), ("reject", "TK3", "synology/ds124"))
        self.assertIn("only a maintainer", i["reason"])

    def test_a_deleted_publisher_is_rejected_on_tk3(self):
        i = self.gate(deleted=["publishers/synology.yaml"])["publishers/synology.yaml"]
        self.assertEqual((i["outcome"], i["rule"]), ("reject", "TK3"))

    def test_a_renamed_product_is_rejected_on_tk3(self):
        items = self.gate({"products/synology/ds-124.yaml": self.ds124}, deleted=["products/synology/ds124.yaml"])
        self.assertEqual((items["products/synology/ds124.yaml"]["outcome"], items["products/synology/ds124.yaml"]["rule"]),
                         ("reject", "TK3"))
        self.assertEqual(items["products/synology/ds-124.yaml"]["outcome"], "reject", "the new path holds a key in use")

    def test_an_alias_taken_out_is_rejected_on_tk3(self):
        changed = self.ds124.replace("aliases:\n- synology/diskstation-ds124", "aliases: []")
        i = self.gate({"products/synology/ds124.yaml": changed})["products/synology/ds124.yaml"]
        self.assertEqual((i["outcome"], i["rule"]), ("reject", "TK3"))
        self.assertIn("synology/diskstation-ds124", i["reason"])

    def test_a_changed_key_is_rejected_on_tk3(self):
        changed = self.ds124.replace("key: synology/ds124", "key: synology/ds125")
        self.assertEqual(self.gate({"products/synology/ds124.yaml": changed})["products/synology/ds124.yaml"]["rule"], "TK3")

    def test_apply_puts_a_deleted_file_back(self):
        items = list(self.gate(deleted=["products/synology/ds124.yaml"]).values())
        data = pr_referee.write_verdicts(items, self.tmp / "v", 7, SHA)
        (self.root / "products/synology/ds124.yaml").unlink()  # the branch of the pull request
        pr_referee.apply(data, self.root, now=NOW, log=QUIET)
        self.assertEqual((self.root / "products/synology/ds124.yaml").read_text(), self.ds124)

    def test_collect_lists_what_a_pull_request_deletes_or_renames(self):
        import subprocess
        repo = self.tmp / "git"
        shutil.copytree(self.root, repo)
        git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-q"); git("add", "-A"); git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        base = git("rev-parse", "HEAD")
        git("mv", "products/synology/ds124.yaml", "products/synology/ds-124.yaml")
        git("rm", "-q", "publishers/synology.yaml")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "head")
        data = pr_referee.collect(base, git("rev-parse", "HEAD"), 7, repo)
        self.assertEqual(data["deleted"], ["products/synology/ds124.yaml", "publishers/synology.yaml"])
        self.assertEqual(list(data["files"]), ["products/synology/ds-124.yaml"])

    def test_the_gate_reads_the_list_of_github_and_checks_it(self):
        path = self.tmp / "deleted.txt"
        path.write_text("products/synology/ds124.yaml\n.github/workflows/lint.yml\nREADME.md\n")
        self.assertEqual(pr_referee.deleted_list(path), ["products/synology/ds124.yaml"])
        good = {"version": 1, "pr": 12, "head_sha": SHA, "files": {}}
        for bad in ({**good, "deleted": "products/synology/ds124.yaml"}, {**good, "deleted": ["tools/lint.py"]}):
            proposal = self.tmp / "p.json"
            proposal.write_text(json.dumps(bad))
            with self.subTest(bad=bad["deleted"]), self.assertRaises(SystemExit):
                pr_referee.load_proposal(proposal)

    def test_remove_logs_what_the_product_was_and_frees_the_key(self):
        entry = pr_referee.remove("synology/ds124", "abuse of the name", self.root, by="maintainer", now=NOW, log=QUIET)
        self.assertFalse((self.root / "products/synology/ds124.yaml").exists())
        log = pr_referee.load_decisions(self.root / pr_referee.DECISIONS)
        self.assertEqual(log, [entry])
        for field, value in (("subject", "synology/ds124"), ("name", "DS124"), ("removed_publisher", "synology"),
                             ("date", "2026-11-13"), ("reason", "abuse of the name"), ("by", "maintainer")):
            self.assertEqual(entry[field], value, field)
        self.assertIn("https://www.synology.com/en-global/products/DS124", entry["sources"])
        # the key is free: a new proposal with it is judged as any new product
        i = self.gate({"products/synology/ds124.yaml": self.ds124})["products/synology/ds124.yaml"]
        self.assertEqual((i["outcome"], i["old"]), ("pending", {}))

    def test_a_deletion_after_the_logged_decision_is_accepted(self):
        pr_referee.save_decisions([{"publisher": "synology", "rule": "remove", "subject": "synology/ds124",
                                    "decision": "remove", "date": "2026-11-13", "reason": "abuse", "by": "maintainer"}],
                                  self.root / pr_referee.DECISIONS)
        i = self.gate(deleted=["products/synology/ds124.yaml"])["products/synology/ds124.yaml"]
        self.assertEqual((i["outcome"], i["by"]), ("accept", "person"))

    def test_the_log_keeps_every_decision(self):
        pr_referee.remove("synology/ds124", "first", self.root, now=NOW, log=QUIET)
        (self.root / "products/synology/ds124.yaml").write_text(self.ds124)
        pr_referee.remove("synology/ds124", "second", self.root, now=NOW, log=QUIET)
        self.assertEqual([d["reason"] for d in pr_referee.load_decisions(self.root / pr_referee.DECISIONS)],
                         ["first", "second"])

    def test_the_four_cases_of_the_test_set_are_rejected_on_tk3(self):
        proposals = [p for p in pr_referee.testset_proposals(REPO / "referee/testset/cases.yaml")
                     if p["id"] in ("B55", "B56", "B57", "B58", "G36")]
        items = {i["id"]: i for i in pr_referee.gate_proposals(proposals, FakeFetcher(), log=QUIET)}
        for cid in ("B55", "B56", "B57", "B58"):
            self.assertEqual((items[cid]["outcome"], items[cid]["rule"]), ("reject", "TK3"), cid)
        self.assertNotEqual(items["G36"]["outcome"], "reject", "a freed key is judged as any new product")


class WorkflowTest(unittest.TestCase):
    """The design rules of the hosting, checked on the workflow files."""

    def load(self, name):
        text = (REPO / ".github/workflows" / name).read_text()
        return text, yaml.safe_load(text)

    def runs(self, wf):
        return [step.get("run", "") for job in wf["jobs"].values() for step in job.get("steps", [])]

    def test_no_pull_request_target_anywhere(self):
        for path in (REPO / ".github/workflows").glob("*.yml"):
            wf = yaml.safe_load(path.read_text())
            self.assertNotIn("pull_request_target", wf.get("on", wf.get(True)), path.name)

    def test_no_event_data_in_a_run_line(self):
        for path in (REPO / ".github/workflows").glob("*.yml"):
            for run in self.runs(yaml.safe_load(path.read_text())):
                self.assertNotRegex(run, r"\$\{\{\s*github\.event\.", path.name)
                self.assertNotRegex(run, r"\$\{\{\s*(inputs|github\.head_ref)", path.name)

    def test_collect_has_no_key_and_only_keeps_data(self):
        text, wf = self.load("referee-pr-collect.yml")
        on = wf.get("on", wf.get(True))
        self.assertEqual(set(on), {"pull_request"})
        self.assertNotIn("secrets.", text)
        self.assertEqual(wf["permissions"], {"contents": "read"})

    def test_only_the_judge_has_the_key_in_environment_referee(self):
        text, wf = self.load("referee-pr.yml")
        on = wf.get("on", wf.get(True))
        self.assertEqual(on["workflow_run"]["workflows"], ["referee-pr-collect"])
        for name, job in wf["jobs"].items():
            uses_key = "secrets.ANTHROPIC_API_KEY" in yaml.safe_dump(job)
            self.assertEqual(uses_key, name == "judge", name)
        self.assertEqual(wf["jobs"]["judge"]["environment"], "referee")
        for job in wf["jobs"].values():
            for step in job.get("steps", []):
                self.assertNotIn("ref", step.get("with", {}) if "checkout" in step.get("uses", "") else {},
                                 "every checkout is the default branch")

    def test_the_workflow_on_main_cannot_write_the_cache_of_main(self):
        """workflow_run reads the cache of main but cannot write it; a dispatch could (CodeQL:
        cache poisoning). So the workflow that reads the artifact of a pull request has no other trigger."""
        _, wf = self.load("referee-pr.yml")
        self.assertEqual(set(wf.get("on", wf.get(True))), {"workflow_run"})

    def test_the_test_set_runs_by_hand_and_waits_for_a_maintainer(self):
        text, wf = self.load("referee-pr-testset.yml")
        self.assertEqual(set(wf.get("on", wf.get(True))), {"workflow_dispatch"})
        self.assertEqual(wf["jobs"]["judge"]["environment"], "referee")
        self.assertNotIn("run-id", text, "it reads no artifact of another run")
        self.assertNotIn("secrets.", yaml.safe_dump(wf["jobs"]["gate"]))

    def test_the_parking_list_is_judged_weekly_and_only_the_judge_has_the_key(self):
        text, wf = self.load("referee-parked.yml")
        on = wf.get("on", wf.get(True))
        self.assertEqual(set(on), {"schedule", "workflow_dispatch"})
        self.assertEqual(wf["jobs"]["judge"]["environment"], "referee")
        self.assertEqual(wf["jobs"]["judge"]["permissions"], {"contents": "read", "actions": "read"}, "the key cannot write")
        self.assertNotIn("secrets.", yaml.safe_dump(wf["jobs"]["publish"]))
        self.assertIn("pr_referee.py parked --live", text)
        self.assertIn("lapsed/*.md", text)

    def test_search_and_judge_of_the_measurement_wait_for_a_maintainer(self):
        _, wf = self.load("referee.yml")
        self.assertEqual(wf["jobs"]["search"]["environment"], "referee")
        self.assertEqual(wf["jobs"]["judge"]["environment"], "referee")


if __name__ == "__main__":
    unittest.main()
