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
        self.assertEqual(items["B01"]["rule"], "S1")
        self.assertEqual((items["B20"]["outcome"], items["B20"]["rule"]), ("reject", "S7"))
        self.assertEqual(items["G01"]["outcome"], "pending")
        self.assertIn("<decided>", items["G01"]["message"])
        self.assertNotIn("S5", items["G01"]["ai_rules"], "the versions are on the publisher's domain")

    def test_a_source_on_an_ai_chat_is_rejected_and_not_fetched(self):
        fetcher = FakeFetcher()
        proposal = {"id": "products/synology/ds140.yaml", "files": {"products/synology/ds140.yaml": (
            "key: synology/ds140\npublisher: synology\nname: DS140\nkind: device\n"
            "source: https://chatgpt.com/share/abc\nversions:\n  source: https://www.synology.com/en-global/releaseNote/DSM\n")}}
        item = pr_referee.gate_proposals([proposal], fetcher, FIXTURES / "data", log=QUIET)[0]
        self.assertEqual((item["outcome"], item["rule"], item["by"]), ("reject", "S8", "code"))
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
        self.assertIn("S7", items["G02"]["rule"])
        self.assertIn("quote not found", items["G02"]["reason"])
        self.assertEqual(items["B07"]["duplicate_of"], "synology/ds224-plus")
        self.assertNotIn("human", {i["outcome"] for i in items.values()}, "no case goes to a person")
        info = json.loads((run_dir / "run.json").read_text())
        self.assertEqual((info["condition"], info["pr"], info["batch"], info["effort"]), ("pr", 7, True, "low"))

    def test_a_batch_costs_half(self):
        items, run_dir, _ = self.judged(ids=["G01"])
        normal = referee.cost(items["G01"]["usage"], "claude-opus-5-5", referee.load_prices())
        self.assertAlmostEqual(items["G01"]["cost"], normal / 2)
        self.assertAlmostEqual(referee.spent_before(self.tmp / "runs"), normal / 2)

    def test_the_whole_batch_fits_what_is_left(self):
        budget = referee.Budget(0.10, 0.0, "claude-opus-5-5", referee.load_prices(), 8000, batch=True)
        client = FakeBatchClient(counted=2000)
        fitted, left_out = referee.fit_batch(client, budget, [(f"p{n}", {"messages": []}) for n in range(5)])
        p_in = referee.input_price("claude-opus-5-5", budget.prices, True)
        p_out = referee.output_price("claude-opus-5-5", budget.prices, True)
        worst = sum((2000 * 1.05 + 100) * p_in + r["max_tokens"] * p_out for _, r in fitted)
        self.assertLessEqual(worst, 0.10)
        self.assertEqual(len(fitted) + len(left_out), 5)
        self.assertGreaterEqual(fitted[0][1]["max_tokens"], referee.MIN_OUTPUT)

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
            {"id": "products/acme/widget.yaml", "key": "acme/widget", "outcome": "parked", "rule": "S5",
             "reason": "S5: the source does not show who publishes", "files": files},
            {"id": "products/synology/ds999.yaml", "key": "synology/ds999", "outcome": "reject", "rule": "S8",
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
        self.assertEqual([(e["key"], e["rule"], e["date"], e["pr"]) for e in parked], [("acme/widget", "S5", "2026-11-13", 7)])
        self.assertEqual(parked[0]["files"], files)

    def test_the_parking_list_is_judged_again_with_one_command(self):
        root = self.root_copy()
        ds124 = (root / "products/synology/ds124.yaml").read_text()
        (root / "products/synology/ds124.yaml").unlink()
        widget = {"products/acme/widget.yaml": "key: acme/widget\npublisher: acme\nname: Widget\nkind: software\n"
                  "source: https://acme.example/widget\nversions:\n  source: https://acme.example/widget\n",
                  "publishers/acme.yaml": "key: acme\nname: Acme\nsource: https://acme.example/about\naliases: []\n"}
        pr_referee.save_parked([
            {"key": "synology/ds124", "path": "products/synology/ds124.yaml", "rule": "S7", "reason": "doubt",
             "date": "2026-11-01", "pr": 3, "files": {"products/synology/ds124.yaml": ds124}},
            {"key": "acme/widget", "path": "products/acme/widget.yaml", "rule": "S5", "reason": "doubt",
             "date": "2026-11-01", "pr": 4, "files": widget},
        ], root / "referee/parked.yaml")
        counts = pr_referee.reprocess(Args(self.tmp / "runs", output=str(self.tmp / "v")), fetcher=FakeFetcher(),
                                      client=FakeBatchClient(), data_root=root, now=NOW, log=QUIET, sleep=lambda s: None)
        self.assertEqual(counts["accept"], 1)
        self.assertEqual((root / "products/synology/ds124.yaml").read_text(), ds124, "accepted: written to the data")
        left = pr_referee.load_parked(root / "referee/parked.yaml")
        self.assertEqual([(e["key"], e["date"], e["pr"]) for e in left], [("acme/widget", "2026-11-13", 4)])

    def test_the_parking_list_in_the_repo_is_valid(self):
        data = yaml.safe_load((REPO / "referee/parked.yaml").read_text())
        self.assertEqual(data["version"], 1)
        for e in data["parked"] or []:
            self.assertTrue({"key", "path", "rule", "reason", "date", "files"} <= set(e))

    # the real test set, as one pull request

    def test_the_test_set_becomes_60_proposals(self):
        proposals = pr_referee.testset_proposals(REPO / "referee/testset/cases.yaml")
        self.assertEqual(len(proposals), 60)
        self.assertEqual(len([p for p in proposals if p["id"].startswith("B")]), 30)
        self.assertTrue(all(p["case"]["expect"] for p in proposals))

    def test_score_counts_parked_as_human(self):
        items = [{"id": "B07", "outcome": "parked", "case": {"expect": ["reject", "human"]}},
                 {"id": "B08", "outcome": "accept", "case": {"expect": ["reject"]}},
                 {"id": "G01", "outcome": "accept", "case": {"expect": ["accept"]}}]
        text = pr_referee.score(items)
        self.assertIn("Bad cases accepted (target 0): 1 B08", text)
        self.assertIn("Not in `expect` (parked counts as human): 1 B08", text)


class WorkflowTest(unittest.TestCase):
    """The design rules of hosting-taxnmy-publiek.md §4 (keepitfresh), checked on the workflow files."""

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

    def test_search_and_judge_of_the_measurement_wait_for_fred(self):
        _, wf = self.load("referee.yml")
        self.assertEqual(wf["jobs"]["search"]["environment"], "referee")
        self.assertEqual(wf["jobs"]["judge"]["environment"], "referee")


if __name__ == "__main__":
    unittest.main()
