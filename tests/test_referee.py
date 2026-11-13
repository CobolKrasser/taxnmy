import json
import re
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import referee  # noqa: E402

FIXTURES = REPO / "tests/fixtures/referee"
PAGE = "Firmware releases are published here."
NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 11, 13, 13, 0, 0, tzinfo=timezone.utc)
QUIET = lambda *a: None  # noqa: E731


def judgment(verdict="yes", url="https://www.synology.com/en-global/releaseNote/DSM", quote=PAGE):
    return {"verdict": verdict, "reason": "fixture", "evidence": [] if verdict == "doubt" else [{"url": url, "quote": quote}]}


def reply(duplicate_of=None, **rules):
    data = {r: judgment() for r in referee.RULES}
    data.update(rules)
    data["duplicate_of"] = duplicate_of
    return json.dumps(data)


# What the fake referee answers, per proposed product key.
REPLIES = {
    "synology/ds124": reply(**{r: judgment(url="https://www.synology.com/en-global/products/DS124") for r in referee.RULES}),
    "synology/ds1522-plus": reply(S7=judgment(quote="a quote that is not on the page")),
    "acme/widget": reply(S4=judgment("doubt")),
    "synology/plus-ds224": reply(duplicate_of="synology/ds224-plus", S4=judgment("no")),
    "synology/ds128": reply(),  # wrongly lets an invented product through
    "synology/ds129": "this is not JSON",
}


class Block:
    def __init__(self, type, text=""):
        self.type, self.text = type, text


class FakeClient:
    """Counts every request as `counted` input tokens and answers with `output` output tokens
    (None: as many as max_tokens allows)."""

    def __init__(self, counted=1000, output=500, searches=0):
        self.counted, self.output, self.searches = counted, output, searches
        self.calls = []

    def count(self, request):
        return self.counted

    def create(self, request):
        self.calls.append(request)
        message = request["messages"][0]["content"]
        key = re.search(r"# products/[^\n]+\nkey: (\S+)", message).group(1)
        out = request["max_tokens"] if self.output is None else self.output
        usage = {"input_tokens": self.counted, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                 "output_tokens": out, "web_search_requests": self.searches}
        return referee.Response(content=[Block("text", REPLIES[key])], stop_reason="end_turn", usage=usage)


class FakeFetcher:
    def __init__(self):
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        if url.endswith("DS128"):
            return referee.Source(url, "404")
        return referee.Source(url, "200", f"Page of {url}. {PAGE}")


class Args:
    def __init__(self, out, **kw):
        self.testset = str(FIXTURES / "cases.yaml")
        self.prompt = str(FIXTURES / "prompt.md")
        self.schema = str(REPO / "referee/verdict.schema.json")
        self.prices = str(REPO / "referee/prices.yaml")
        self.out = str(out)
        self.condition = "fetched"
        self.cases = None
        self.limit = None
        self.model = "claude-opus-5-5"
        self.effort = "medium"
        self.max_tokens = 8000
        self.max_uses = 3
        self.budget = 5.0
        self.live = True
        self.__dict__.update(kw)


class RefereeTest(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out)

    def run_fake(self, client=None, fetcher=None, now=NOW, log=QUIET, **kw):
        client = client or FakeClient()
        fetcher = fetcher or FakeFetcher()
        results, run_dir = referee.run(Args(self.out, **kw), client=client, fetcher=fetcher,
                                       data_root=FIXTURES / "data", now=now, log=log)
        return {r.id: r for r in results}, run_dir, client

    # the outcome

    def test_outcomes(self):
        results, _, client = self.run_fake()
        self.assertEqual({i: (r.outcome, r.by, r.right) for i, r in results.items()}, {
            "G01": ("accept", "ai", True),
            "G02": ("human", "ai", False),  # a quote that is not on the page turns yes into doubt
            "H01": ("human", "ai", True),
            "B01": ("reject", "code", True),
            "B07": ("alias", "ai", True),
            "B20": ("accept", "ai", False),
            "B21": ("human", "error", True),
            "B22": ("", "invalid", False),
        })
        self.assertEqual(len(client.calls), 6, "B01 stops at the lint and B22 does not match its lint field")

    def test_quote_check(self):
        results, _, _ = self.run_fake()
        self.assertEqual(results["G02"].checked["S7"], "doubt")
        self.assertEqual(results["G02"].verdict["S7"]["verdict"], "yes")
        self.assertIn("S7: quote not found", results["G02"].quote_notes[0])

    def test_quote_check_ignores_whitespace_and_case(self):
        verdict = json.loads(reply(S5=judgment(quote="  FIRMWARE releases\n are published  ")))
        checked, notes = referee.check_quotes(verdict, [referee.Source("https://www.synology.com/en-global/releaseNote/DSM", "200", PAGE)])
        self.assertEqual((checked["S5"]["verdict"], notes), ("yes", []))

    def test_yes_without_quote_is_doubt(self):
        verdict = json.loads(reply(S6={"verdict": "yes", "reason": "", "evidence": []}))
        checked, _ = referee.check_quotes(verdict, [])
        self.assertEqual(checked["S6"]["verdict"], "doubt")

    def test_outcome_rules(self):
        base = {r: {"verdict": "yes"} for r in referee.RULES}
        self.assertEqual(referee.outcome({**base, "duplicate_of": None}), ("accept", ""))
        self.assertEqual(referee.outcome({**base, "S6": {"verdict": "no"}, "S5": {"verdict": "no"}, "duplicate_of": None}), ("reject", "S5"))
        self.assertEqual(referee.outcome({**base, "S4": {"verdict": "no"}, "duplicate_of": "a/b"}), ("alias", "S4"))
        self.assertEqual(referee.outcome({**base, "S8": {"verdict": "doubt"}, "duplicate_of": None}), ("human", ""))

    def test_memory_and_web_skip_the_quote_check(self):
        results, _, client = self.run_fake(condition="memory", cases=["G02"])
        self.assertEqual(results["G02"].outcome, "accept")
        self.assertIn(referee.NO_SOURCES["memory"], client.calls[0]["messages"][0]["content"])
        results, _, client = self.run_fake(condition="web", cases=["G02"], now=LATER)
        self.assertEqual([t["name"] for t in client.calls[0]["tools"]], ["web_search"])
        self.assertIn(referee.NO_SOURCES["web"], client.calls[0]["messages"][0]["content"])

    def test_r13_cases_are_the_default_outside_fetched(self):
        cases = referee.load_cases(REPO / "referee/testset/cases.yaml")
        self.assertEqual([c.id for c in referee.select(cases, None, "memory")], list(referee.R13_CASES))
        self.assertEqual(len(referee.select(cases, None, "fetched")), 60)
        self.assertEqual(len(referee.select(cases, ["all"], "web")), 60)

    # staging and the message

    def test_staging(self):
        cases = {c.id: c for c in referee.load_cases(FIXTURES / "cases.yaml")}
        stager = referee.Stager(FIXTURES / "data")
        self.addCleanup(stager.close)
        g01 = stager.stage(cases["G01"])
        self.assertEqual(list(g01.files), ["products/synology/ds124.yaml"])
        self.assertIn("synology/ds224-plus: DS224+", g01.existing)
        self.assertNotIn("synology/ds124: DS124", g01.existing, "a hold-out is not part of what exists")
        g02 = stager.stage(cases["G02"])
        self.assertIn("synology/ds923-plus: DS923+", g02.existing, "context files are part of what exists")
        self.assertFalse((stager.root / "products/synology/ds923-plus.yaml").exists(), "staging is undone")
        self.assertTrue((stager.root / "products/synology/ds124.yaml").exists(), "staging is undone")
        b07 = stager.stage(cases["B07"])
        self.assertEqual(b07.lint_seen, "signal")
        self.assertIn("# products/synology/ds224-plus.yaml\nkey: synology/ds224-plus", b07.existing)
        h01 = stager.stage(cases["H01"])
        self.assertIn("# publishers that share a word with acme\nnone", h01.existing)
        self.assertFalse(stager.stage(cases["B22"]).lint_ok)

    def test_message_follows_the_prompt(self):
        _, _, client = self.run_fake(cases=["B07"])
        call = client.calls[0]
        self.assertEqual(call["system"][0]["text"], "Judge S4-S8.\n")
        message = call["messages"][0]["content"]
        for tag in ("<proposal>", "<signals>", "<existing>", "<sources>"):
            self.assertIn(tag, message)
        self.assertLess(message.index("<existing>"), message.index("<sources>"))
        self.assertIn("S3: key 'synology/plus-ds224' has the same words", message)
        self.assertIn(f'<source url="https://www.synology.com/en-global/products/DS224+" status="200">\nPage of', message)

    def test_sources_are_the_distinct_urls_of_the_proposal(self):
        fetcher = FakeFetcher()
        self.run_fake(fetcher=fetcher, cases=["H01"])
        self.assertEqual(fetcher.urls, ["https://acme.example/about", "https://acme.example/widget"])

    def test_html_to_text(self):
        page = "<html><head><style>x{}</style><script>var a</script></head><body><nav>Menu</nav><h1>DS124</h1><p>Caf&eacute;  1</p></body></html>"
        self.assertEqual(referee.html_to_text(page), "DS124 Café 1")

    def test_system_prompt_of_the_analyst(self):
        text = referee.load_system_prompt(REPO / "referee/prompt.md")
        self.assertTrue(text.startswith("You are the referee of TAXNMY"))
        self.assertIn("S8  Every value", text)
        self.assertNotIn("```", text)

    def test_api_schema(self):
        schema = referee.api_schema(referee.load_schema(REPO / "referee/verdict.schema.json"))
        text = json.dumps(schema)
        self.assertNotIn("$ref", text)
        self.assertNotIn("$schema", text)
        self.assertEqual(schema["properties"]["S4"]["properties"]["verdict"], {"enum": ["yes", "no", "doubt"], "type": "string"})

    # measuring

    def test_metrics(self):
        results, _, _ = self.run_fake()
        m = referee.metrics(list(results.values()))
        self.assertEqual(m["let_through"], ["B20"])
        self.assertEqual(m["good_rejected"], [])
        self.assertEqual(m["human"], ["G02", "H01", "B21"])
        self.assertEqual(m["invalid"], ["B22"])
        self.assertEqual(m["right_rule"], {"S1": [1, 1], "S4": [1, 1], "S7": [2, 0], "S8": [1, 0]})
        self.assertEqual(m["per_type"]["hallucinated"], [2, 1])
        self.assertAlmostEqual(m["cost_per_call"], 0.014)  # 1000 in at $4/M, 500 out at $20/M
        r13 = m["r13"]
        self.assertEqual(r13["per_rule"]["S7"], {"right": 1, "wrong": 1, "doubt": 1})
        self.assertIn("B20 S7", r13["confident_wrong"])

    def test_files_of_a_run(self):
        _, run_dir, _ = self.run_fake()
        self.assertEqual(run_dir.name, "20261113-120000-fetched-claude-opus-5-5")
        self.assertEqual(len((run_dir / "results.jsonl").read_text().splitlines()), 8)
        summary = (run_dir / "summary.md").read_text()
        self.assertIn("| bad cases let through (target 0) | 1 |", summary)
        sources = json.loads((run_dir / "sources.json").read_text())
        self.assertEqual(sources["https://www.synology.com/en-global/products/DS128"]["status"], "404")

    def test_compare(self):
        _, first, _ = self.run_fake(condition="memory", cases=["G01", "B07", "B20"])
        _, second, _ = self.run_fake(condition="web", cases=["G01", "B07", "B20"], now=LATER)
        text = referee.compare([first, second])
        self.assertIn("Comparison on 3 shared cases", text)
        self.assertIn("Different outcomes: none", text)

    def test_testset_problems_stop_the_run(self):
        bad = self.out / "cases.yaml"
        bad.write_text("cases:\n- id: X1\n  type: t\n  expect: [maybe]\n  lint: pass\n")
        with self.assertRaises(SystemExit) as ctx:
            referee.load_cases(bad)
        self.assertIn("'maybe' is not one of", str(ctx.exception))

    def test_dry_run_sends_nothing(self):
        client, fetcher = FakeClient(), FakeFetcher()
        results, run_dir = referee.run(Args(self.out, live=False), client=client, fetcher=fetcher,
                                       data_root=FIXTURES / "data", now=NOW, log=QUIET)
        self.assertEqual((results, run_dir, client.calls, fetcher.urls), ([], None, [], []))
        self.assertEqual(list(self.out.iterdir()), [])

    # the budget

    def test_budget_counts_earlier_runs_and_stops(self):
        self.run_fake()  # 6 calls of $0.014
        client = FakeClient()
        # $0.03 left: less than 1,150 input and 2,000 output tokens cost on Opus 5.5 ($0.0458)
        results, run_dir, _ = self.run_fake(client=client, budget=0.084 + 0.03, now=LATER)
        self.assertEqual(client.calls, [])
        self.assertIn("Stopped before G01", (run_dir / "summary.md").read_text())
        self.assertAlmostEqual(referee.spent_before(self.out), 0.084)

    def test_max_tokens_fits_what_is_left(self):
        client = FakeClient()
        self.run_fake(client=client, budget=0.1, cases=["G01"])
        # (0.1 - (1000 * 1.05 + 100) * $5/M) / $20/M = 4712 output tokens
        self.assertEqual(client.calls[0]["max_tokens"], 4712)

    def test_budget_is_a_hard_limit(self):
        # The fake uses every output token it is allowed; the run still stays under the budget.
        client = FakeClient(counted=3000, output=None)
        _, run_dir, _ = self.run_fake(client=client, budget=0.40)
        self.assertLessEqual(referee.spent_before(self.out), 0.40)
        self.assertGreater(len(client.calls), 0)
        self.assertLess(len(client.calls), 6)
        self.assertIn("Stopped before", (run_dir / "summary.md").read_text())

    def test_budget_is_logged(self):
        lines = []
        self.run_fake(budget=1.25, log=lines.append)
        self.assertIn("budget: $1.25", lines[2])
        self.assertTrue(any("spent $0.0140 of $1.25" in line for line in lines))
        run_dir = next(self.out.iterdir())
        self.assertEqual(json.loads((run_dir / "run.json").read_text())["budget"], 1.25)

    def test_web_reserves_room_for_search(self):
        client = FakeClient()
        self.run_fake(client=client, condition="web", budget=1.0, cases=["G01"])
        allowance = referee.web_allowance("claude-opus-5-5", referee.load_prices(), 3)
        expected = int((1.0 - (1000 * 1.05 + 100) * 5e-6 - allowance) / 20e-6)
        self.assertEqual(client.calls[0]["max_tokens"], min(8000, expected))

    def test_cost_of_cache_and_search(self):
        usage = {"input_tokens": 100, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 10000,
                 "output_tokens": 1000, "web_search_requests": 2}
        expected = (100 * 4 + 2000 * 5 + 10000 * 0.2 + 1000 * 20) / 1e6 + 2 * 0.01
        self.assertAlmostEqual(referee.cost(usage, "claude-opus-5-5", referee.load_prices()), expected)

    def test_unknown_model_has_no_price(self):
        with self.assertRaises(SystemExit):
            referee.cost({"input_tokens": 1}, "claude-unknown", referee.load_prices())

    # the key

    def test_key_is_never_written(self):
        secret = "sk-ant-test-0123456789-never-in-a-file"
        lines = []
        with unittest.mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": secret}):
            self.run_fake(log=lines.append)
        self.assertNotIn(secret, "\n".join(map(str, lines)))
        for path in self.out.rglob("*"):
            if path.is_file():
                self.assertNotIn(secret, path.read_text(), path)

    def test_anthropic_client_counts_without_tools_and_reports_usage(self):
        class Usage:
            input_tokens, output_tokens = 10, 20
            cache_creation_input_tokens = cache_read_input_tokens = 0
            server_tool_use = type("S", (), {"web_search_requests": 2})()

        class Messages:
            counted = None

            def count_tokens(self, **kw):
                Messages.counted = kw
                return type("C", (), {"input_tokens": 42})()

            def create(self, **kw):
                return type("M", (), {"content": [Block("text", "{}")], "stop_reason": "end_turn", "usage": Usage()})()

        fake = type(sys)("anthropic")
        fake.Anthropic = lambda: type("A", (), {"messages": Messages()})()
        with unittest.mock.patch.dict("sys.modules", {"anthropic": fake}):
            client = referee.AnthropicClient()
        request = {"model": "m", "system": [], "messages": [], "output_config": {}, "tools": [{}], "max_tokens": 5}
        self.assertEqual(client.count(request), 42)
        self.assertEqual(set(Messages.counted), {"model", "system", "messages", "output_config"})
        response = client.create(request)
        self.assertEqual((response.usage["web_search_requests"], response.usage["output_tokens"]), (2, 20))

    def test_final_text_skips_talk_between_searches(self):
        blocks = [Block("text", "Let me search."), Block("server_tool_use"), Block("web_search_tool_result"),
                  Block("text", '{"S4"'), Block("text", ": {}}")]
        self.assertEqual(referee.final_text(blocks), '{"S4": {}}')


class TestsetTest(unittest.TestCase):
    """The real test set, staged on the real data, without network or API."""

    def test_every_case_matches_its_lint_field(self):
        cases = referee.load_cases(REPO / "referee/testset/cases.yaml")
        self.assertEqual(len(cases), 60)
        stager = referee.Stager(REPO)
        self.addCleanup(stager.close)
        mismatched = {}
        for case in cases:
            staged = stager.stage(case)
            if not staged.lint_ok:
                mismatched[case.id] = (case.lint, staged.lint_seen)
        self.assertEqual(mismatched, {})


if __name__ == "__main__":
    unittest.main()
