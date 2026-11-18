"""TAX-32: the limits of the intake (TV8-TV11), with the fakes of the referee tests. No network, no AI."""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import intake  # noqa: E402
import lint  # noqa: E402
import pr_referee  # noqa: E402
from test_pr_referee import Args, FakeBatchClient  # noqa: E402
from test_referee import FIXTURES, NOW, QUIET, FakeFetcher  # noqa: E402

LIMITS = intake.load()


def product(n: int, sources: int = 0) -> dict:
    """A new product of the fixture publisher, with `sources` extra identifier sources."""
    ids = "".join(f"- cpe: cpe:2.3:h:synology:ds{n}x{i}:-\n  source: https://www.synology.com/x/{n}/{i}\n"
                  for i in range(sources))
    rel = f"products/synology/ds{n}.yaml"
    text = (f"key: synology/ds{n}\npublisher: synology\nname: DS{n}\nkind: device\n"
            f"source: https://www.synology.com/en-global/products/DS{n}\n"
            f"versions:\n  source: https://www.synology.com/en-global/releaseNote/DSM?model=DS{n}\n"
            f"identifiers:{' []' if not sources else ''}\n{ids}aliases: []\n")
    return {"id": rel, "files": {rel: text}}


def limits(**kw) -> dict:
    out = copy.deepcopy(LIMITS)
    out.update(kw)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "root"
        shutil.copytree(FIXTURES / "data", self.root)
        (self.root / "referee").mkdir()
        pr_referee.save_parked([], self.root / "referee/parked.yaml")

    def gate(self, proposals, sub=None, lim=None, fetcher=None):
        self.fetcher = fetcher or FakeFetcher()
        items = pr_referee.gate_proposals(proposals, self.fetcher, self.root, log=QUIET, decisions=[],
                                          submitter=sub, limits=lim or LIMITS)
        return {i["id"]: i for i in items}

    def park(self, entries):
        pr_referee.save_parked(entries, self.root / "referee/parked.yaml")

    def entry(self, n, submitter, known=False):
        p = product(n)
        return pr_referee.park_entry({**p, "key": f"synology/ds{n}", "publisher": "synology", "waits_for": "event",
                                      "rule": "TR5", "reason": "fixture", "submitter": submitter, "known": known,
                                      "pr": 3}, "2026-11-10")


class OnePlaceTest(unittest.TestCase):
    """TV8: every limit has a value in one place, the values of the story."""

    def test_the_values_of_the_story(self):
        self.assertEqual(LIMITS["open_per_submitter"], {"unknown": 3, "known": 10})
        self.assertEqual(LIMITS["parking_list"], 200)
        self.assertTrue(LIMITS["open"])
        self.assertEqual((pr_referee.PR_CAP, pr_referee.DAY_CAP), (2.00, 5.00))

    def test_a_broken_file_stops_instead_of_opening(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        (root / "referee").mkdir()
        (root / intake.INTAKE).write_text("version: 1\nopen: yes please\n")
        with self.assertRaises(SystemExit):
            intake.load(root)

    def test_a_submitter_name_is_checked(self):
        for bad in ("", "a b", "x" * 40, "-x;rm", "a\nb"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                intake.Submitter(bad, False)
        self.assertEqual(intake.Submitter("dependabot[bot]", True).login, "dependabot[bot]")


class PerSubmitterTest(Base):
    """TV8 and TV9: over the limit of a submitter is rejected, never parked, and its sources are not read."""

    def test_an_unknown_submitter_has_three(self):
        items = self.gate([product(n) for n in range(501, 506)], intake.Submitter("newcomer", False))
        out = [items[f"products/synology/ds{n}.yaml"] for n in range(501, 506)]
        self.assertEqual([i["outcome"] != "reject" for i in out], [True, True, True, False, False])
        for i in out[3:]:
            self.assertEqual(i["rule"], "TV9")
            self.assertIn("the limit of 3 open proposals per submitter (unknown)", i["reason"])
        self.assertFalse([u for u in self.fetcher.urls if "DS504" in u or "DS505" in u], "over the limit, still read")

    def test_a_known_submitter_has_ten(self):
        items = self.gate([product(n) for n in range(501, 513)], intake.Submitter("regular", True))
        self.assertEqual(sum(i["outcome"] == "reject" and i["rule"] == "TV9" for i in items.values()), 2)

    def test_parked_and_earlier_open_proposals_count(self):
        self.park([self.entry(601, "newcomer")])
        items = self.gate([product(n) for n in range(501, 504)], intake.Submitter("newcomer", False, open_before=1))
        self.assertEqual([items[f"products/synology/ds{n}.yaml"]["outcome"] == "reject" for n in range(501, 504)],
                         [False, True, True])
        self.assertIn("1 on the parking list, 1 in earlier open pull requests", items["products/synology/ds502.yaml"]["reason"])

    def test_the_parked_of_someone_else_do_not_count(self):
        self.park([self.entry(601 + n, "other") for n in range(5)])
        items = self.gate([product(501)], intake.Submitter("newcomer", False))
        self.assertNotEqual(items["products/synology/ds501.yaml"]["outcome"], "reject")

    def test_a_rejected_proposal_does_not_go_to_the_parking_list(self):
        items = self.gate([product(n) for n in range(501, 506)], intake.Submitter("newcomer", False), )
        provisional = list(items.values())
        pr_referee.park_pending(provisional, "waits for the referee")
        work = self.tmp / "work"
        shutil.copytree(self.root, work)
        for p in [product(n) for n in range(501, 506)]:
            for rel, text in p["files"].items():
                (work / rel).write_text(text)
        data = pr_referee.write_verdicts(provisional, self.tmp / "v", 9, "")
        pr_referee.apply(data, work, now=NOW, log=QUIET)
        keys = {e["key"] for e in pr_referee.load_parked(work / "referee/parked.yaml")}
        self.assertEqual(keys, {"synology/ds501", "synology/ds502", "synology/ds503"})
        on_list = pr_referee.load_parked(work / "referee/parked.yaml")
        self.assertEqual({e["submitter"] for e in on_list}, {"newcomer"})

    def test_without_a_submitter_there_is_no_limit_per_submitter(self):
        items = self.gate([product(n) for n in range(501, 506)])
        self.assertFalse([i for i in items.values() if i["rule"] == "TV9"])


class PerProposalTest(Base):
    def test_too_many_sources_is_rejected_unread(self):
        items = self.gate([product(501, sources=9), product(502, sources=8)], intake.Submitter("newcomer", False))
        big, fits = items["products/synology/ds501.yaml"], items["products/synology/ds502.yaml"]
        self.assertEqual((big["outcome"], big["rule"]), ("reject", "TV9"))
        self.assertIn("11 sources in one proposal; the limit is 10", big["reason"])
        self.assertNotEqual(fits["outcome"], "reject")
        self.assertFalse([u for u in self.fetcher.urls if "/x/501/" in u])

    def test_a_proposal_refused_for_itself_leaves_the_room_of_its_submitter(self):
        items = self.gate([product(501, sources=9), *[product(n) for n in range(502, 505)]],
                          intake.Submitter("newcomer", False))
        self.assertEqual([items[f"products/synology/ds{n}.yaml"]["outcome"] == "reject" for n in range(502, 505)],
                         [False, False, False])


class ParkingListFullTest(Base):
    def items(self, *known):
        return [{"id": f"p{n}", "outcome": "parked", "known": k, "rule": "TR5", "reason": "doubt"}
                for n, k in enumerate(known)]

    def test_a_full_list_rejects_instead_of_parking(self):
        items = self.items(False, False)
        rejected = intake.cap_parking(items, 199, LIMITS)
        self.assertEqual([i["outcome"] for i in items], ["parked", "reject"])
        self.assertEqual((rejected[0]["rule"], rejected[0]["waits_for"]), ("TV9", ""))
        self.assertIn("the parking list is full (200 proposals)", rejected[0]["reason"])

    def test_the_last_places_go_to_known_submitters(self):
        items = self.items(False, True)
        intake.cap_parking(items, 199, LIMITS)
        self.assertEqual([i["outcome"] for i in items], ["reject", "parked"])

    def test_below_the_limit_nothing_changes(self):
        items = self.items(False, False, True)
        self.assertEqual(intake.cap_parking(items, 0, LIMITS), [])


class KnownFirstTest(Base):
    """TV10: known submitters are judged first; unknown ones only within their part of the day ceiling."""

    def pending(self, known_flags):
        items = []
        for n, known in enumerate(known_flags):
            item = self.gate([product(700 + n)], intake.Submitter("known" if known else f"stranger{n}", known))
            items += list(item.values())
        self.assertTrue(all(i["outcome"] == "pending" for i in items), [i["outcome"] for i in items])
        return items

    def judge(self, items, lim=None, **kw):
        client = FakeBatchClient()
        pr_referee.judge(items, Args(self.tmp / "runs", pr_cap=100.0, **kw), client=client, now=NOW, log=QUIET,
                         sleep=lambda s: None, limits=lim or LIMITS)
        order = [cid for batch in client.batches for cid, _ in batch]
        return client, order

    def test_the_order_puts_known_first_and_keeps_the_rest(self):
        items = [{"id": "a", "known": False}, {"id": "b", "known": True}, {"id": "c", "known": False}, {"id": "d", "known": True}]
        self.assertEqual([i["id"] for i in intake.first_known(items)], ["b", "d", "a", "c"])

    def test_known_are_sent_first(self):
        items = self.pending([False, False, True])
        client, _ = self.judge(items)
        first = client.batches[0]
        self.assertEqual(len(first), 1)
        self.assertIn("key: synology/ds702", first[0][1]["messages"][0]["content"])

    def test_unknown_cannot_use_the_part_of_known(self):
        # Unknown submitters spent their part today already (half the day ceiling): the next two of
        # them get nothing, the known one is judged.
        self.judge(self.pending([False]))
        spent = pr_referee.spent_today_by(self.tmp / "runs", NOW.strftime("%Y%m%d"))["unknown"]
        items = self.pending([False, False, True])
        self.judge(items, pr=8, day_cap=2 * spent + 1.0, lim=limits(unknown_share_of_day=spent / (2 * spent + 1.0)))
        judged = {i["id"]: i for i in items}
        known = judged["products/synology/ds702.yaml"]
        self.assertGreater(known["cost"], 0)  # the referee answered it
        self.assertNotIn("leaves no room", known["reason"])
        for n in (700, 701):
            self.assertEqual(judged[f"products/synology/ds{n}.yaml"]["outcome"], "parked")
            self.assertIn("the part of the day ceiling for unknown submitters", judged[f"products/synology/ds{n}.yaml"]["reason"])

    def test_unknown_spending_today_is_counted(self):
        items = self.pending([False])
        self.judge(items)
        by = pr_referee.spent_today_by(self.tmp / "runs", NOW.strftime("%Y%m%d"))
        self.assertGreater(by["unknown"], 0)
        self.assertEqual(by["unknown"], by["stranger0"])


class FloodTest(Base):
    """Criterion 6: 100 proposals of unknown submitters do not make a known one wait longer. The
    parking list is judged again with and without them; the wait of the known proposal is the number
    of requests sent before it, plus its own, at the same time per request."""

    def wait_of_known(self, strangers: int) -> tuple[int, int]:
        self.park([self.entry(800 + n, f"stranger{n}") for n in range(strangers)] + [self.entry(999, "regular", True)])
        client = FakeBatchClient()
        pr_referee.reprocess(Args(self.tmp / f"runs{strangers}", output=str(self.tmp / f"v{strangers}"), pr_cap=100.0),
                             fetcher=FakeFetcher(), client=client, data_root=self.root, now=NOW, log=QUIET,
                             sleep=lambda s: None)
        order = [request["messages"][0]["content"] for batch in client.batches for _, request in batch]
        position = next(n for n, text in enumerate(order) if "key: synology/ds999" in text) + 1
        return position, len(order)

    def test_a_hundred_strangers_do_not_delay_a_known_one(self):
        alone, _ = self.wait_of_known(0)
        crowded, sent = self.wait_of_known(100)
        self.assertGreater(sent, 1)  # the strangers were there and were sent too
        self.assertEqual((alone, crowded), (1, 1))


class RoundTest(Base):
    """Criterion 5: an agreed round is a submitter with a larger value, and nothing else."""

    def test_330_products_of_a_submitter_with_its_own_value(self):
        lim = limits(submitters={"round-account": {"open": 500}})
        proposals = [product(1000 + n) for n in range(330)]
        refused = intake.refusals(proposals, intake.Submitter("round-account", True), lim, [])
        self.assertEqual(refused, {})
        refused = intake.refusals(proposals, intake.Submitter("someone-else", True), lim, [])
        self.assertEqual(len(refused), 320)

    def test_no_code_names_a_round(self):
        for path in (REPO / "tools").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for login in LIMITS.get("submitters") or {}:
                for quoted in (f'"{login}"', f"'{login}'"):
                    self.assertNotIn(quoted, text, f"{path.name} names {login}: a round must be a value, not code")


class ClosedIntakeTest(Base):
    """TV11: the intake can be closed; reading the data does not change."""

    def test_closed_rejects_every_new_proposal(self):
        items = self.gate([product(501), product(502)], intake.Submitter("regular", True), lim=limits(open=False))
        self.assertEqual({(i["outcome"], i["rule"]) for i in items.values()}, {("reject", "TV11")})
        self.assertEqual(self.fetcher.urls, [])

    def test_reading_the_data_does_not_depend_on_the_intake(self):
        before = [str(f) for f in lint.lint(self.root, REPO / "schema")]
        text = (REPO / intake.INTAKE).read_text().replace("open: true", "open: false")
        (self.root / intake.INTAKE).write_text(text)
        self.assertFalse(intake.load(self.root)["open"])
        self.assertEqual([str(f) for f in lint.lint(self.root, REPO / "schema")], before)
        self.assertNotIn("intake", lint.__dict__)


class WorkflowTest(unittest.TestCase):
    def test_the_gate_passes_who_proposes_without_a_key(self):
        wf = yaml.safe_load((REPO / ".github/workflows/referee-pr.yml").read_text())
        gate = wf["jobs"]["gate"]
        self.assertNotIn("environment", gate)
        steps = {s.get("name"): s for s in gate["steps"]}
        who = steps["Who proposes (TAX-32)"]
        self.assertNotIn("secrets.", who["run"] + str(who.get("env")))
        self.assertIn("--submitter", steps["Lint, gate and sources (no key)"]["run"])


if __name__ == "__main__":
    unittest.main()
