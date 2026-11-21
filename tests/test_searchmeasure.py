"""The measurement of the model of step 4 (TAX-49): tools/searchmeasure.py and its workflow."""

import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import referee  # noqa: E402
import searchmeasure  # noqa: E402
from test_referee import NOW, QUIET, Block  # noqa: E402

CLOSED_A = "https://business.adobe.com/products/commerce.html"
CLOSED_F = "https://www.fortinet.com/products/web-application-firewall/fortiweb"
BASE = {
    CLOSED_A: {"product": "Adobe Commerce", "query": '"Adobe Commerce" Adobe', "domains": ["adobe.com"],
               "urls": ["https://helpx.adobe.com/a", "https://www.sec.gov/1.htm", "https://learning.adobe.com/b"]},
    CLOSED_F: {"product": "FortiWeb", "query": '"FortiWeb" Fortinet', "domains": ["fortinet.com"],
               "urls": ["https://docs.fortinet.com/fortiweb"]},
}
BASE_RESULTS = [{"id": "search", "for": [CLOSED_A], "cost": 0.0596}, {"id": "search", "for": [CLOSED_F], "cost": 0.0595}]


class SearchClient:
    """Answers each search with the URLs of `answers` for its query; counts the calls."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def count(self, request):
        return 300

    def create(self, request):
        self.calls.append(request)
        query = request["messages"][0]["content"].split("query: ")[1].split("\n")[0]
        block = Block("web_search_tool_result")
        block.content = [type("R", (), {"url": u})() for u in self.answers[query]]
        usage = {"input_tokens": 11_967, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                 "output_tokens": 85, "web_search_requests": 1}
        return referee.Response(content=[block], stop_reason="end_turn", usage=usage)


class Fetcher:
    """Pages that show their product's name, except those in `blank`; records what is asked."""

    def __init__(self, blank=()):
        self.asked, self.blank = [], set(blank)

    def fetch(self, url, product=""):
        self.asked.append(url)
        text = "" if url in self.blank else f"Adobe Commerce and FortiWeb, documented at length. {'x ' * 200}"
        return referee.Source(url, "200", text, "http")


class SearchMeasureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "search.json").write_text(json.dumps(BASE))
        (self.tmp / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in BASE_RESULTS))
        (self.tmp / "runs").mkdir()

    def args(self, *extra):
        return searchmeasure.parser().parse_args(
            ["search", "--base", str(self.tmp / "search.json"), "--base-results", str(self.tmp / "results.jsonl"),
             "--base-label", "claude-opus-5-5, run 44764232264", "--cap", "0.75", "--out", str(self.tmp / "runs"),
             "--output", str(self.tmp / "measure.json"), *extra])

    def client(self):
        return SearchClient({'"Adobe Commerce" Adobe': ["https://helpx.adobe.com/a", "https://www.sec.gov/2.htm"],
                             '"FortiWeb" Fortinet': ["https://docs.fortinet.com/fortiweb"]})

    def test_the_defaults(self):
        args = self.args()
        self.assertEqual((args.repeats, args.model, args.live), (3, "", False))

    def test_a_dry_run_sends_nothing(self):
        client, lines = self.client(), []
        got = searchmeasure.search(self.args(), client, NOW, lines.append)
        self.assertEqual(client.calls, [])
        self.assertIn("2 searches x 3 runs on claude-haiku-5-5 (web_search_20250305): about $0.07", lines[0])
        self.assertEqual(got["runs"], [])

    def test_over_the_cap_nothing_is_sent(self):
        client = self.client()
        args = self.args("--live")
        args.cap = 0.05
        got = searchmeasure.search(args, client, NOW, QUIET)
        self.assertEqual(client.calls, [])
        self.assertIn("passes the cap", got["stopped"])

    def test_the_searches_run_repeats_times_with_the_query_of_the_base(self):
        client = self.client()
        got = searchmeasure.search(self.args("--live"), client, NOW, QUIET)
        self.assertEqual(len(client.calls), 6, "2 searches x 3 runs, one approval")
        self.assertEqual({c["model"] for c in client.calls}, {referee.SEARCH_MODEL})
        self.assertIn('"Adobe Commerce" Adobe', client.calls[0]["messages"][0]["content"])
        self.assertEqual(len(got["runs"]), 3)
        self.assertEqual(len({r["dir"] for r in got["runs"]}), 3, "one run directory per repeat, for the budget")
        self.assertEqual(got["runs"][0]["found"][CLOSED_A]["urls"], ["https://helpx.adobe.com/a"])
        self.assertAlmostEqual(got["spent"], 6 * (11_967 * 0.10e-6 + 85 * 0.50e-6 + 0.01))
        self.assertAlmostEqual(referee.spent_before(self.tmp / "runs"), got["spent"])

    def test_the_report_sets_each_run_next_to_the_base(self):
        searchmeasure.search(self.args("--live"), self.client(), NOW, QUIET)
        fetcher = Fetcher(blank={"https://docs.fortinet.com/fortiweb"})
        args = searchmeasure.parser().parse_args(["read", "--measure", str(self.tmp / "measure.json"),
                                                  "--output", str(self.tmp / "report.md")])
        text = searchmeasure.read(args, fetcher, QUIET)
        hosts = {urllib.parse.urlsplit(u).hostname for u in fetcher.asked}
        self.assertNotIn("www.sec.gov", hosts, "a URL elsewhere is never read")
        self.assertEqual(hosts, {"helpx.adobe.com", "docs.fortinet.com"})
        self.assertIn("| claude-opus-5-5, run 44764232264 | 2 | 1 of 2 | 3 | 1 | $0.1191 |", text)
        self.assertIn("| claude-haiku-5-5, run 1 | 2 | 1 of 2 | 2 | 1 | $0.0225 |", text)
        self.assertIn("| Adobe Commerce | 2 / yes / 1 / $0.0596 | 1 / yes / 1 / $0.0112 |", text)
        self.assertIn("| FortiWeb | 1 / **no** / 0 / $0.0595 |", text)
        self.assertIn("- Adobe Commerce, claude-opus-5-5, run 44764232264: https://www.sec.gov/1.htm", text)
        self.assertIn("- Adobe Commerce, claude-haiku-5-5, run 2: https://www.sec.gov/2.htm", text)

    def test_the_workflow_asks_one_approval_and_reads_without_the_key(self):
        wf = yaml.safe_load((REPO / ".github/workflows/referee-search-measure.yml").read_text())
        inputs = wf[True]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"base_run", "base_file", "repeats", "model", "cap"})
        self.assertEqual(inputs["base_run"]["default"], "44764232264")
        search, read = wf["jobs"]["search"], wf["jobs"]["read"]
        self.assertEqual(search["environment"], "referee")
        self.assertNotIn("environment", read)
        self.assertNotIn("ANTHROPIC_API_KEY", json.dumps(read), "the reader never has the key")
        self.assertNotIn("requirements-fetch", json.dumps(search), "the key never comes near a browser")
        uploads = [s for s in search["steps"] if "upload-artifact" in s.get("uses", "")
                   and "referee-run-" in s["with"]["name"]]
        self.assertEqual(len(uploads), searchmeasure.MAX_REPEATS, "every run directory is kept, for the budget")
        for job in (search, read):
            self.assertIn("timeout-minutes", job)


if __name__ == "__main__":
    unittest.main()
