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
    a = argparse.Namespace(models=MODELS, repeats=3, efforts=["low"], max_tokens=4000, budget=20.0, sources=None,
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
        self.assertEqual(row["new"], 0)

    def test_the_estimate_scales_the_output_by_effort(self):
        prices = referee.load_prices()
        low, medium = (referee.estimate("claude-sonnet-5-5", prices, ["G01"], 1, 4000, effort=e) for e in ("low", "medium"))
        self.assertEqual(low["output"], round(medium["output"] * 0.5))
        self.assertEqual(low["input"], medium["input"])
        self.assertLess(low["expected"], medium["expected"])

    def test_a_case_a2_did_not_have_counts_as_its_mean_case(self):
        prices = referee.load_prices()
        row = referee.estimate("claude-sonnet-5-5", prices, ["G01", "B44"], 1, 4000)
        self.assertEqual(row["new"], 1)
        self.assertGreater(row["input"], referee.estimate("claude-sonnet-5-5", prices, ["G01"], 1, 4000)["input"])

    def test_counted_tokens_replace_those_of_a2(self):
        prices = referee.load_prices()
        row = referee.estimate("claude-haiku-5-5", prices, ["G01"], 1, 4000, counted={"G01": 1000})
        self.assertEqual(row["input"], 1000)
        self.assertTrue(row["counted"])

    def test_the_new_measurement_is_about_five_dollars(self):
        """TAX-18, again: Sonnet 5.5 at low and at medium, three runs each, about $5.20 (the analyst).
        It fits the $50 by far: the runs in the repo and in the artifacts are about $13.15."""
        cases = [c.id for c in referee.load_cases(REPO / "referee/testset/cases.yaml") if not c.lint.startswith("error")]
        prices = referee.load_prices()
        rows = {e: referee.estimate("claude-sonnet-5-5", prices, cases, 3, 4000, effort=e) for e in ("low", "medium")}
        total = sum(r["expected"] for r in rows.values())
        self.assertLess(rows["low"]["expected"], rows["medium"]["expected"])
        self.assertTrue(4.0 < total < 7.0, total)
        self.assertLess(total, referee.BUDGET - 13.15)

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
        self.assertEqual([len(g) for g in report["runs"]["claude-haiku-5-5 low"]], [2, 1, 1], "probe + rest, then two more")
        self.assertEqual(len(client.batches), 2 * 4)
        self.assertEqual({r[1]["model"] for r in client.batches[0]}, {"claude-haiku-5-5"})
        self.assertEqual({r[1]["output_config"]["effort"] for b in client.batches for r in b}, {"low"})
        text = Path(report["report"]).read_text()
        self.assertIn("| claude-haiku-5-5 low | 3 | 1 / 1 / 1 |", text, "B21 let through in each run")
        self.assertIn("let through: B21", text)

    def test_one_model_at_two_efforts_is_two_stands(self):
        """TAX-18, again: Sonnet at low and at medium in one measurement, three runs each."""
        report, client = self.measure(models=["claude-sonnet-5-5"], efforts=["low", "medium"])
        self.assertEqual(report["stopped"], "")
        self.assertEqual(list(report["runs"]), ["claude-sonnet-5-5 low", "claude-sonnet-5-5 medium"])
        self.assertEqual(len(client.batches), 2 * 4, "per stand a probe, the rest of run 1, and runs 2 and 3")
        efforts = [{r[1]["output_config"]["effort"] for r in b} for b in client.batches]
        self.assertEqual(efforts, [{"low"}, {"medium"}] + [{"low"}] * 3 + [{"medium"}] * 3)
        dirs = [Path(d).name for groups in report["runs"].values() for g in groups for d in g]
        self.assertEqual(len(set(dirs)), len(dirs), "every run has a directory of its own")
        self.assertTrue(all(d.endswith(("-low", "-medium")) for d in dirs), dirs)
        text = Path(report["report"]).read_text()
        self.assertIn("| claude-sonnet-5-5 low | 3 |", text)
        self.assertIn("| claude-sonnet-5-5 medium | 3 |", text)
        self.assertIn("S4 without a version found", text)

    def test_a_dry_measure_estimates_every_stand(self):
        report, _ = self.measure(live=False, models=["claude-sonnet-5-5"], efforts=["low", "medium"])
        self.assertEqual([(r["model"], r["effort"]) for r in report["rows"]],
                         [("claude-sonnet-5-5", "low"), ("claude-sonnet-5-5", "medium")])
        self.assertAlmostEqual(report["total"], sum(r["expected"] for r in report["rows"]))

    def test_the_measure_defaults_are_sonnet_and_the_budget(self):
        args = referee.parser().parse_args(["measure"])
        self.assertEqual((args.models, args.efforts, args.repeats, args.budget), (["claude-sonnet-5-5"], ["low"], 3, 50.0))

    def test_the_probe_stops_a_measurement_that_would_pass_the_budget(self):
        report, client = self.measure(budget=0.05)
        self.assertIn("passes the budget", report["stopped"])
        self.assertEqual(len(client.batches), 2, "only the probes")
        self.assertLessEqual(referee.spent_before(self.out), 0.05)

    def test_a_probe_that_does_not_fit_stops_the_measurement(self):
        report, client = self.measure(budget=0.01)
        self.assertIn("no answer to measure for claude-sonnet-5-5 low", report["stopped"])
        self.assertEqual(len(client.batches), 1, "the Sonnet probe did not fit")

    def test_the_report_counts_the_spread_between_runs(self):
        report, _ = self.measure(models=["claude-haiku-5-5"])
        groups = report["runs"]["claude-haiku-5-5 low"]
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
        self.assertEqual(job["if"], "github.ref == 'refs/heads/main'")
        self.assertEqual(job["concurrency"]["group"], "referee-judge")
        on = wf.get("on", wf.get(True))
        self.assertEqual(set(on), {"workflow_dispatch"})
        inputs = on["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["budget"]["default"], "50.00")
        self.assertEqual((inputs["models"]["default"], inputs["efforts"]["default"]), ("claude-sonnet-5-5", "low medium"))
        self.assertFalse(inputs["live"]["default"])
        self.assertNotIn("web_search", text)
        for name, j in wf["jobs"].items():
            for step in j["steps"]:
                self.assertNotRegex(step.get("run", ""), r"\$\{\{", name)

    def test_the_sources_are_fetched_without_the_key(self):
        wf = yaml.safe_load((REPO / ".github/workflows/referee-models.yml").read_text())
        job = wf["jobs"]["sources"]
        self.assertNotIn("environment", job)
        self.assertNotIn("secrets.", json.dumps(job))
        self.assertNotIn("if", job, "free, so it may run from any branch")
        self.assertEqual(wf["jobs"]["measure"]["needs"], "sources")
        for j in wf["jobs"].values():
            for step in j["steps"]:
                if "setup-python" in step.get("uses", ""):
                    self.assertNotIn("cache", step.get("with", {}), "no cache in a job that reads artifacts")

    def test_every_run_of_two_stands_is_kept(self):
        """Two stands, three runs each, run 1 in two directories (probe and rest): eight artifacts."""
        wf = yaml.safe_load((REPO / ".github/workflows/referee-models.yml").read_text())
        keep = [s for s in wf["jobs"]["measure"]["steps"] if s.get("name", "").startswith("Keep run ")]
        self.assertGreaterEqual(len(keep), 2 * 4)


if __name__ == "__main__":
    unittest.main()
