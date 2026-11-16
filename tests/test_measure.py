"""The model measurement (TAX-18): run --batch, measure, and the workflow referee-models."""

import argparse
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import referee  # noqa: E402
from test_pr_referee import FakeBatchClient  # noqa: E402
from test_referee import FIXTURES, NOW, QUIET, Args, FakeFetcher  # noqa: E402

MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5"]


def measure_args(out, **kw):
    a = argparse.Namespace(models=MODELS, repeats=3, effort="low", max_tokens=4000, budget=20.0, sources=None,
                           compare_with="", wait=3600, live=True, count=False,
                           testset=str(FIXTURES / "cases.yaml"), prompt=str(FIXTURES / "prompt.md"),
                           schema=str(REPO / "referee/verdict.schema.json"), prices=str(REPO / "referee/prices.yaml"),
                           out=str(out))
    a.__dict__.update(kw)
    return a


class BatchRunTest(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out)

    def run_batch(self, client=None, **kw):
        client = client or FakeBatchClient()
        results, run_dir = referee.run(Args(self.out, batch=True, sleep=lambda s: None, **kw), client=client,
                                       fetcher=FakeFetcher(), data_root=FIXTURES / "data", now=NOW, log=QUIET)
        return {r.id: r for r in results}, run_dir, client

    def test_a_batch_run_gives_the_same_outcomes(self):
        results, run_dir, client = self.run_batch()
        self.assertEqual({i: (r.outcome, r.by, r.right) for i, r in results.items()}, {
            "G01": ("accept", "ai", True),
            "G02": ("human", "ai", False),
            "H01": ("human", "error", True),
            "B01": ("reject", "code", True),
            "B07": ("alias", "ai", True),
            "B20": ("reject", "code", True),
            "B21": ("accept", "ai", False),
            "B22": ("", "invalid", False),
        })
        self.assertEqual(len(client.batches), 1)
        self.assertEqual(len(client.batches[0]), 5)
        self.assertEqual(list(results), ["G01", "G02", "H01", "B01", "B07", "B20", "B21", "B22"], "in case order")
        self.assertTrue(json.loads((run_dir / "run.json").read_text())["batch"])
        lines = (run_dir / "results.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 8)

    def test_a_batch_costs_half_of_a_normal_run(self):
        results, _, _ = self.run_batch(cases=["G01"])
        normal = referee.cost(results["G01"].usage, "claude-opus-5-5", referee.load_prices())
        self.assertAlmostEqual(results["G01"].cost, normal / 2)

    def test_the_budget_stays_hard_in_a_batch(self):
        results, run_dir, client = self.run_batch(budget=0.025)
        self.assertLessEqual(referee.spent_before(self.out), 0.025)
        self.assertEqual(sum(r.by == "ai" for r in results.values()), 1, "room for one request, then no more")
        self.assertIn("Stopped by the budget", (run_dir / "summary.md").read_text())

    def test_web_cannot_be_a_batch(self):
        with self.assertRaises(SystemExit):
            self.run_batch(condition="web")

    def test_nested_runs_are_counted_once(self):
        self.run_batch(cases=["G01"])
        spent = referee.spent_before(self.out)
        nested = self.out / "models-1"
        shutil.copytree(next(self.out.iterdir()), nested / "a-run")
        self.assertAlmostEqual(referee.spent_before(self.out), 2 * spent)


class MeasureTest(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out)

    def measure(self, client=None, **kw):
        client = client or FakeBatchClient()
        report = referee.measure(measure_args(self.out, **kw), client=client, fetcher=FakeFetcher(),
                                 data_root=FIXTURES / "data", now=NOW, log=QUIET, sleep=lambda s: None)
        return report, client

    def test_the_estimate_uses_the_tokens_of_a2(self):
        prices = referee.load_prices()
        row = referee.estimate("claude-sonnet-5-5", prices, ["G01"], 3, 4000)
        fresh, read, out = referee.a2_tokens()["G01"]
        expected = ((fresh * 2.00 + read * 0.10) / 1e6 * 0.5 + out * 10.0 / 1e6 * 0.5) * 3
        self.assertAlmostEqual(row["expected"], expected)
        self.assertGreater(row["ceiling"], row["expected"])

    def test_counted_tokens_replace_those_of_a2(self):
        prices = referee.load_prices()
        row = referee.estimate("claude-haiku-5-5", prices, ["G01"], 1, 4000, counted={"G01": 1000})
        self.assertEqual(row["input"], 1000)
        self.assertTrue(row["counted"])

    def test_the_real_measurement_fits_the_three_dollars(self):
        """The estimate of the story (about $2.65) against what is left of $13.70, from the runs in the repo."""
        cases = [c.id for c in referee.load_cases(REPO / "referee/testset/cases.yaml") if not c.lint.startswith("error")]
        prices = referee.load_prices()
        total = sum(referee.estimate(m, prices, cases, 3, 4000)["expected"] for m in MODELS)
        left = 13.70 - referee.spent_before(REPO / "referee/runs")
        self.assertLess(total, left)
        self.assertAlmostEqual(left, 3.00, places=1)

    def test_a_dry_measure_sends_nothing(self):
        report, client = self.measure(live=False)
        self.assertEqual(client.batches, [])
        self.assertEqual([r["model"] for r in report["rows"]], MODELS)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_dry_measure_can_count_tokens_for_free(self):
        report, client = self.measure(live=False, count=True)
        self.assertEqual(client.batches, [])
        self.assertGreater(client.counts, 0)
        self.assertTrue(all(r["counted"] for r in report["rows"]))

    def test_the_probe_first_then_three_runs_per_model(self):
        report, client = self.measure()
        self.assertEqual(report["stopped"], "")
        self.assertEqual([len(g) for g in report["runs"]["claude-haiku-5-5"]], [2, 1, 1], "probe + rest, then two more")
        self.assertEqual(len(client.batches), 2 * 4)
        self.assertEqual({r[1]["model"] for r in client.batches[0]}, {"claude-haiku-5-5"})
        self.assertEqual({r[1]["output_config"]["effort"] for b in client.batches for r in b}, {"low"})
        text = Path(report["report"]).read_text()
        self.assertIn("| claude-haiku-5-5 | 3 | 1 / 1 / 1 |", text, "B21 let through in each run")
        self.assertIn("let through: B21", text)

    def test_the_probe_stops_a_measurement_that_would_pass_the_budget(self):
        report, client = self.measure(budget=0.05)
        self.assertIn("passes the budget", report["stopped"])
        self.assertEqual(len(client.batches), 2, "only the probes")
        self.assertLessEqual(referee.spent_before(self.out), 0.05)

    def test_a_probe_that_does_not_fit_stops_the_measurement(self):
        report, client = self.measure(budget=0.01)
        self.assertIn("no answer to measure for claude-sonnet-5-5", report["stopped"])
        self.assertEqual(len(client.batches), 1, "the Sonnet probe did not fit")

    def test_the_report_counts_the_spread_between_runs(self):
        report, _ = self.measure(models=["claude-haiku-5-5"])
        groups = report["runs"]["claude-haiku-5-5"]
        # Make run 3 differ on G01.
        path = groups[2][0] / "results.jsonl"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        for r in lines:
            if r["id"] == "G01":
                r["outcome"] = "human"
        path.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
        text = referee.measurement_report(report["runs"])
        self.assertIn("| 1: G01 |", text)


class WorkflowTest(unittest.TestCase):
    def test_the_measurement_waits_for_fred_and_has_a_hard_budget(self):
        text = (REPO / ".github/workflows/referee-models.yml").read_text()
        wf = yaml.safe_load(text)
        job = wf["jobs"]["measure"]
        self.assertEqual(job["environment"], "referee")
        on = wf.get("on", wf.get(True))
        self.assertEqual(set(on), {"workflow_dispatch"})
        self.assertEqual(on["workflow_dispatch"]["inputs"]["budget"]["default"], "13.70")
        self.assertFalse(on["workflow_dispatch"]["inputs"]["live"]["default"])
        self.assertNotIn("web_search", text)
        for step in job["steps"]:
            self.assertNotRegex(step.get("run", ""), r"\$\{\{")


if __name__ == "__main__":
    unittest.main()
