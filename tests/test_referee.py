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
    "acme/widget": "this is not JSON",
    "synology/plus-ds224": reply(duplicate_of="synology/ds224-plus", S4=judgment("no")),
    "synology/ds128": reply(),  # never asked: its product source answers 404
    "synology/ds129": reply(),  # wrongly lets an invented product through
}


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF with one line of text. pypdf rebuilds the cross-reference table itself."""
    stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


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
        return referee.Response(content=[Block("text", asked(REPLIES[key], request))], stop_reason="end_turn", usage=usage)


def asked(text, request):
    """The reply, with only the rules the request asks for: the gate settled the others."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    wanted = request["output_config"]["format"]["schema"]["properties"]
    return json.dumps({k: v for k, v in data.items() if k in wanted})


class FakeFetcher:
    def __init__(self):
        self.urls = []

    def fetch(self, url, product=""):
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

    def run_fake(self, client=None, fetcher=..., now=NOW, log=QUIET, **kw):
        client = client or FakeClient()
        fetcher = FakeFetcher() if fetcher is ... else fetcher
        results, run_dir = referee.run(Args(self.out, **kw), client=client, fetcher=fetcher,
                                       data_root=FIXTURES / "data", now=now, log=log)
        return {r.id: r for r in results}, run_dir, client

    # the outcome

    def test_outcomes(self):
        results, _, client = self.run_fake()
        self.assertEqual({i: (r.outcome, r.by, r.right) for i, r in results.items()}, {
            "G01": ("accept", "ai", True),
            "G02": ("human", "ai", False),  # a quote that is not on the page turns yes into doubt
            "H01": ("human", "error", True),
            "B01": ("reject", "code", True),
            "B07": ("alias", "ai", True),
            "B20": ("reject", "code", True),
            "B21": ("accept", "ai", False),
            "B22": ("", "invalid", False),
        })
        self.assertEqual(len(client.calls), 5, "B01 stops at the lint, B20 at its 404, and B22 does not match its lint field")

    def test_product_source_404_is_rejected_by_code(self):
        results, _, client = self.run_fake(cases=["B20"])
        self.assertEqual((results["B20"].outcome, results["B20"].rule), ("reject", "S7"))
        self.assertIn("answers 404", results["B20"].error)
        self.assertEqual(client.calls, [])

    def test_publisher_file_and_its_source_come_along(self):
        fetcher = FakeFetcher()
        _, _, client = self.run_fake(fetcher=fetcher, cases=["G01"])
        self.assertIn("https://www.synology.com/en-global/company", fetcher.urls)
        message = client.calls[0]["messages"][0]["content"]
        existing = message[message.index("<existing>"):message.index("</existing>")]
        self.assertTrue(existing.startswith("<existing>\n# publishers/synology.yaml\nkey: synology"), existing[:80])

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
        self.assertEqual([t["name"] for t in client.calls[0]["tools"]], ["web_search", "web_fetch"])
        self.assertEqual(client.calls[0]["tools"][1]["max_uses"], 3)
        self.assertIn(referee.NO_SOURCES["web"], client.calls[0]["messages"][0]["content"])
        self.assertIn("Read the URLs in the proposal with web fetch", referee.NO_SOURCES["web"])

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
        self.assertTrue(g01.existing.startswith("# publishers/synology.yaml\nkey: synology"))
        self.assertEqual(g01.publisher_source, "https://www.synology.com/en-global/company")
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
        self.assertNotIn("# publishers/acme.yaml", h01.existing, "a new publisher is in the proposal")
        self.assertEqual(h01.publisher_source, "")
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

    def test_page_text(self):
        self.assertEqual(referee.page_text(b'{"cpe": "cpe:2.3:a:oracle:graalvm"}', "application/json", "utf-8"),
                         '{"cpe": "cpe:2.3:a:oracle:graalvm"}')
        self.assertEqual(referee.page_text(b"a  b", "text/plain", "utf-8"), "a b")
        self.assertEqual(referee.page_text(b"\x89PNG", "image/png", "utf-8"), "")
        self.assertEqual(referee.page_text(minimal_pdf("FortiGate 60F datasheet"), "application/pdf", "utf-8"),
                         "FortiGate 60F datasheet")

    def test_run_can_use_sources_fetched_earlier(self):
        stored = self.out / "fetched.json"
        stored.write_text(json.dumps({"https://www.synology.com/en-global/products/DS124": {
            "url": "https://www.synology.com/en-global/products/DS124", "status": "200", "text": PAGE,
            "method": "http", "fetched_url": "", "chars": len(PAGE)}}))
        results, run_dir, client = self.run_fake(fetcher=None, cases=["G01"], sources=str(stored))
        sources = dict(results["G01"].sources)
        self.assertEqual(sources["https://www.synology.com/en-global/products/DS124"], "200")
        self.assertEqual(sources["https://www.synology.com/en-global/company"], "not fetched")

    # step 4

    def refused_sources(self):
        """A sources.json in which the product page of G01 is refused."""
        url = "https://www.synology.com/en-global/products/DS124"
        path = self.out / "sources.json"
        path.write_text(json.dumps({url: {"url": url, "status": "refused", "method": "http"}}))
        return path, url

    def search_args(self, sources, **kw):
        args = Args(self.out, sources=str(sources), output=str(self.out / "search.json"), cases=["G01", "B07"])
        args.__dict__.update(kw)
        return args

    def test_search_finds_other_urls_for_a_refused_source(self):
        sources, refused = self.refused_sources()

        class Result:
            def __init__(self, url):
                self.url, self.page_age, self.encrypted_content = url, None, "never kept"

        class SearchClient(FakeClient):
            def create(self, request):
                self.calls.append(request)
                block = Block("web_search_tool_result")
                block.content = [Result("https://kb.synology.com/en-global/DS124"), Result(refused)]
                usage = {"input_tokens": 3000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                         "output_tokens": 20, "web_search_requests": 1}
                return referee.Response(content=[Block("server_tool_use"), block, Block("text", "done")], stop_reason="end_turn", usage=usage)

        client = SearchClient()
        found = referee.search_refused(self.search_args(sources), client=client, data_root=FIXTURES / "data", now=NOW, log=QUIET)
        self.assertEqual(found[refused]["urls"], ["https://kb.synology.com/en-global/DS124"], "the refused URL itself is dropped")
        self.assertEqual(found[refused]["product"], "DS124")
        request = client.calls[0]
        self.assertEqual(request["tools"][0]["allowed_domains"], ["synology.com"])
        self.assertEqual(request["tools"][0]["max_uses"], 1)
        self.assertIn('"DS124" synology', request["messages"][0]["content"])
        self.assertNotIn("never kept", (self.out / "search.json").read_text(), "snippets are never kept")
        # the search counts against the budget: $0.012 tokens + $0.01 search
        self.assertAlmostEqual(referee.spent_before(self.out), 3000 * 4e-6 + 20 * 20e-6 + 0.01)

    def test_search_also_for_a_disallowed_source(self):
        url = "https://www.synology.com/en-global/products/DS124"
        sources = self.out / "sources.json"
        sources.write_text(json.dumps({url: {"url": url, "status": "disallowed", "method": "http"}}))
        lines = []
        referee.search_refused(self.search_args(sources, live=False), client=FakeClient(), data_root=FIXTURES / "data", log=lines.append)
        self.assertIn("1 refused or disallowed sources to search for", lines[0])
        stored = referee.StoredFetcher({url: {"url": url, "status": "disallowed"},
                                        f"https://docs.synology.com/x | for {url}": {"url": "https://docs.synology.com/x", "status": "200",
                                                                                        "text": "y", "instead_of": url}})
        stager = referee.Stager(FIXTURES / "data")
        self.addCleanup(stager.close)
        g01 = stager.stage({c.id: c for c in referee.load_cases(FIXTURES / "cases.yaml")}["G01"])
        self.assertIn("https://docs.synology.com/x", [s.url for s in referee.gather_sources(stored, g01)])

    def test_search_dry_run_sends_nothing(self):
        sources, refused = self.refused_sources()
        client = FakeClient()
        found = referee.search_refused(self.search_args(sources, live=False), client=client, data_root=FIXTURES / "data", log=QUIET)
        self.assertEqual((found, client.calls), ({}, []))

    def test_found_sources_go_down_the_ladder_and_into_the_message(self):
        sources, refused = self.refused_sources()
        search = self.out / "search.json"
        search.write_text(json.dumps({refused: {"product": "DS124", "urls": ["https://kb.synology.com/en-global/DS124"]}}))
        args = self.search_args(sources, search=str(search), output=str(self.out / "final.json"), limit=None, no_browser=True)
        data = referee.fetch_sources(args, fetcher=FakeFetcher(), data_root=FIXTURES / "data", log=QUIET)
        alt = data[f"https://kb.synology.com/en-global/DS124 | for {refused}"]
        self.assertEqual((alt["instead_of"], alt["method"], alt["status"]), (refused, "search+", "200"))
        results, _, client = self.run_fake(fetcher=None, cases=["G01"], sources=str(self.out / "final.json"))
        message = client.calls[0]["messages"][0]["content"]
        self.assertIn(f'<source url="https://kb.synology.com/en-global/DS124" status="200" instead-of="{refused}">', message)
        self.assertIn(f'<source url="{refused}" status="refused">', message)

    def test_closed_source_reads_the_publishers_machine_sources_first(self):
        import shutil as sh
        data = self.out / "data"
        sh.copytree(FIXTURES / "data", data)
        pub = data / "publishers/synology.yaml"
        pub.write_text(pub.read_text() + "machine_sources:\n  - url: https://dl.synology.example/catalog.json\n"
                       "    format: csaf-provider\n    source: https://www.synology.com/en-global/company\n")
        refused = "https://www.synology.com/en-global/products/DS124"

        class Fetcher(FakeFetcher):
            cache = {}

            def fetch(self, url, product=""):
                self.urls.append(url)
                if url == refused:
                    src = referee.Source(url, "refused", method="http")
                elif url.endswith("catalog.json"):
                    src = referee.Source(url, "200", "CSAF provider metadata ...", "machine", product=product)
                else:
                    src = referee.Source(url, "200", f"Page of {url}. {PAGE}", "http")
                self.cache[referee.source_key(url, product) if src.method == "machine" else url] = src
                return src

        args = Args(self.out, output=str(self.out / "sources.json"), cases=["G01"], limit=None, no_browser=True)
        got = referee.fetch_sources(args, fetcher=Fetcher(), data_root=data, log=QUIET)
        key = f"https://dl.synology.example/catalog.json | DS124 | for {refused}"
        self.assertEqual((got[key]["method"], got[key]["instead_of"]), ("machine", refused))
        lines = []
        found = referee.search_refused(self.search_args(self.out / "sources.json", live=False), client=FakeClient(),
                                       data_root=data, log=lines.append)
        self.assertIn("0 refused or disallowed sources to search for", lines[0], "step 0 found something, so no search")
        self.assertEqual(found, {})

    def test_one_search_per_query_with_sonnet(self):
        product, versions = "https://www.synology.com/en-global/products/DS124", "https://www.synology.com/en-global/releaseNote/DSM?model=DS124"
        sources = self.out / "sources.json"
        sources.write_text(json.dumps({u: {"url": u, "status": "refused"} for u in (product, versions)}))

        class SearchClient(FakeClient):
            def create(self, request):
                self.calls.append(request)
                block = Block("web_search_tool_result")
                block.content = [type("R", (), {"url": "https://kb.synology.com/x"})()]
                usage = {"input_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                         "output_tokens": 10, "web_search_requests": 1}
                return referee.Response(content=[block], stop_reason="end_turn", usage=usage)

        client = SearchClient()
        args = self.search_args(sources, model=referee.parser().parse_args(["search", "--sources", "x"]).model)
        found = referee.search_refused(args, client=client, data_root=FIXTURES / "data", now=NOW, log=QUIET)
        self.assertEqual(len(client.calls), 1, "two closed sources of one product share one search")
        self.assertEqual(client.calls[0]["model"], "claude-sonnet-5-5")
        self.assertEqual(set(found), {product, versions})
        lines = (next(self.out.glob("*-search-*")) / "results.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["for"], [product, versions])

    def test_search_domains_come_from_the_publishers_file_and_products(self):
        import shutil as sh
        data = self.out / "data"
        sh.copytree(FIXTURES / "data", data)
        (data / "products/synology/ds224-plus.yaml").write_text(
            (FIXTURES / "data/products/synology/ds224-plus.yaml").read_text().replace(
                "https://www.synology.com/en-global/releaseNote/DSM", "https://support.synology.co.jp/releases"))
        stager = referee.Stager(data)
        self.addCleanup(stager.close)
        g01 = stager.stage({c.id: c for c in referee.load_cases(FIXTURES / "cases.yaml")}["G01"])
        domains = referee.search_domains("https://www.synology.com/en-global/products/DS124", g01)
        self.assertEqual(domains, ["synology.com", "synology.co.jp"])

    def test_human_by_cause(self):
        closed = referee.Result(id="G09", type="t", expect=["accept"], outcome="human", by="ai",
                                sources=[["https://a/1", "refused"], ["https://a/2", "200"]])
        timeout = referee.Result(id="G03", type="t", expect=["accept"], outcome="human", by="ai",
                                 sources=[["https://a/3", "error: TimeoutError"]])
        doubt = referee.Result(id="G12", type="t", expect=["accept"], outcome="human", by="ai", sources=[["https://a/4", "200"]])
        m = referee.metrics([closed, timeout, doubt])
        self.assertEqual((m["human_closed"], m["human_doubt"]), (["G09", "G03"], ["G12"]))
        summary = referee.summarise([closed, timeout, doubt], "x", "fetched", 20, 0)
        self.assertIn("| human because a source is closed (refused, disallowed, error) | 2 |", summary)
        self.assertIn("| human because the referee doubts | 1 |", summary)

    def test_r13_conditions_read_their_own_prompt(self):
        r13 = self.out / "prompt-r13.md"
        r13.write_text("## System prompt\n\n```text\nJudge from what you know.\n```\n")
        default = referee.REFEREE / "prompt.md"
        with unittest.mock.patch.object(referee, "R13_PROMPT", r13):
            _, run_dir, client = self.run_fake(condition="memory", cases=["G01"], prompt=default)
            self.assertEqual(client.calls[0]["system"][0]["text"], "Judge from what you know.\n")
            self.assertEqual(json.loads((run_dir / "run.json").read_text())["prompt"], str(r13))
            _, _, client = self.run_fake(condition="fetched", cases=["G01"], prompt=default, now=LATER)
            self.assertTrue(client.calls[0]["system"][0]["text"].startswith("You are the referee of TAXNMY"))

    def test_r13_prompt_must_exist(self):
        with unittest.mock.patch.object(referee, "R13_PROMPT", self.out / "missing.md"):
            with self.assertRaises(SystemExit):
                self.run_fake(condition="memory", cases=["G01"], prompt=referee.REFEREE / "prompt.md")

    def test_registrable(self):
        self.assertEqual(referee.registrable("www.dell.com"), "dell.com")
        self.assertEqual(referee.registrable("support.d-imaging.sony.co.jp"), "sony.co.jp")
        self.assertEqual(referee.registrable("www.sony.nl"), "sony.nl")

    def test_budget_default_is_20(self):
        self.assertEqual(referee.parser().parse_args(["run"]).budget, 20.0)

    def test_system_prompt_of_the_analyst(self):
        text = referee.load_system_prompt(REPO / "referee/prompt.md")
        self.assertTrue(text.startswith("You are the referee of TAXNMY"))
        self.assertIn("S8  Every value", text)
        self.assertIn("When the source is unavailable,\n    answer doubt.", text)
        self.assertIn("releases of another publisher unchanged is not a product of its own.", text)
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
        self.assertEqual(m["let_through"], ["B21"])
        self.assertEqual(m["good_rejected"], [])
        self.assertEqual(m["human"], ["G02", "H01"])
        self.assertEqual(m["errors"], ["H01"])
        self.assertEqual(m["invalid"], ["B22"])
        self.assertEqual(m["right_rule"], {"S1": [1, 1], "S4": [1, 1], "S7": [2, 1], "S8": [1, 0]})
        self.assertEqual(m["per_type"]["hallucinated"], [2, 1])
        self.assertAlmostEqual(m["cost_per_call"], 0.014)  # 1000 in at $4/M, 500 out at $20/M
        r13 = m["r13"]
        self.assertEqual(r13["per_rule"]["S7"], {"right": 1, "wrong": 1, "doubt": 1})
        self.assertIn("B21 S7", r13["confident_wrong"])

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

    def test_compare_merges_a_split_run_and_shows_cases(self):
        _, a, _ = self.run_fake(condition="memory", cases=["G01", "B07"])
        _, w1, _ = self.run_fake(condition="web", cases=["G01"], now=LATER)
        _, w2, _ = self.run_fake(condition="web", cases=["B07"], now=datetime(2026, 11, 13, 14, 0, 0, tzinfo=timezone.utc))
        text = referee.compare([[a], [w1, w2]], show=["B07"])
        self.assertIn("Comparison on 2 shared cases", text)
        self.assertIn(f"{w1.name} + {w2.name}", text)
        self.assertIn("| B07 | alias (S4 no) | alias (S4 no) |", text)

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
        self.assertEqual((results, client.calls, fetcher.urls), ([], [], []))
        self.assertEqual([p.name for p in self.out.iterdir()], ["20261113-120000-dry-fetched"])
        self.assertEqual([p.name for p in run_dir.iterdir()], ["summary.md"], "no results.jsonl, so the budget counts nothing")

    # the gate (tools/gate.py)

    def test_dry_run_shows_what_the_gate_settles(self):
        sources = self.out / "sources.json"
        sources.write_text(json.dumps({u: {"url": u, "status": "200", "text": f"Page of {u}. Synology Inc. {PAGE}"} for u in (
            "https://www.synology.com/en-global/products/DS124", "https://www.synology.com/en-global/releaseNote/DSM?model=DS124",
            "https://www.synology.com/en-global/company")}))
        _, run_dir = referee.run(Args(self.out / "runs", live=False, sources=str(sources)), data_root=FIXTURES / "data", now=NOW, log=QUIET)
        summary = (run_dir / "summary.md").read_text()
        self.assertIn("| G01 | - | yes | yes | yes | S4, S7 |", summary)
        self.assertIn("| B01 | lint S1 | - | - | - | - |", summary)
        self.assertIn("| G02 | - | yes | referee | referee | S4, S6, S7, S8 |", summary, "its sources were not fetched")

    def test_the_referee_gets_only_the_rules_code_left(self):
        results, _, client = self.run_fake(cases=["G01"])
        request = client.calls[0]
        self.assertEqual(sorted(request["output_config"]["format"]["schema"]["properties"]), ["S4", "S6", "S7", "duplicate_of"])
        self.assertEqual(request["output_config"]["format"]["schema"]["required"], ["S4", "S6", "S7", "duplicate_of"])
        message = request["messages"][0]["content"]
        self.assertIn("<decided>\nS5: yes (code: the versions are published on synology.com", message)
        self.assertIn("Judge only: S4, S6, S7\n</decided>", message)
        g01 = results["G01"]
        self.assertEqual((g01.gate, g01.ai_rules), ({"S5": "yes", "S8": "yes"}, ["S4", "S6", "S7"]))
        self.assertEqual(g01.checked, {r: "yes" for r in referee.RULES})
        self.assertEqual(g01.outcome, "accept")

    def test_a_source_on_an_ai_chat_is_rejected_by_code_and_not_fetched(self):
        cases = self.out / "cases.yaml"
        cases.write_text("""cases:
- id: B06
  type: ai-source
  expect: [reject]
  rules: [S8]
  lint: pass
  proposal:
    products/synology/ds125.yaml: |
      key: synology/ds125
      publisher: synology
      name: DS125
      kind: device
      source: https://www.synology.com/en-global/products/DS125
      versions:
        source: https://www.synology.com/en-global/releaseNote/DSM
      identifiers:
      - gtin: "0812345678904"
        source: https://chatgpt.com/share/0000
""")
        fetcher = FakeFetcher()
        results, run_dir, client = self.run_fake(fetcher=fetcher, testset=str(cases))
        b06 = results["B06"]
        self.assertEqual((b06.outcome, b06.by, b06.rule, b06.gate, b06.right), ("reject", "code", "S8", {"S8": "no"}, True))
        self.assertEqual((client.calls, fetcher.urls), ([], []))
        self.assertIn("is an AI chat", b06.error)
        self.assertEqual(referee.metrics([b06])["right_rule"], {"S8": [1, 1]})
        self.assertIn("| S8 | 0 | 1 | 0 | 0 |", (run_dir / "summary.md").read_text())

    def test_memory_and_web_have_no_gate(self):
        results, _, client = self.run_fake(condition="memory", cases=["G01"])
        self.assertEqual((results["G01"].gate, results["G01"].ai_rules), ({}, list(referee.RULES)))
        self.assertNotIn("<decided>", client.calls[0]["messages"][0]["content"])

    # the budget

    def test_budget_counts_earlier_runs_and_stops(self):
        self.run_fake()  # 5 calls of $0.014
        client = FakeClient()
        # $0.03 left: less than 1,150 input and 2,000 output tokens cost on Opus 5.5 ($0.0458)
        results, run_dir, _ = self.run_fake(client=client, budget=0.07 + 0.03, now=LATER)
        self.assertEqual(client.calls, [])
        self.assertIn("Stopped before G01", (run_dir / "summary.md").read_text())
        self.assertAlmostEqual(referee.spent_before(self.out), 0.07)

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
        self.assertLess(len(client.calls), 5)
        self.assertIn("Stopped before", (run_dir / "summary.md").read_text())

    def test_budget_is_logged(self):
        lines = []
        self.run_fake(budget=1.25, log=lines.append)
        self.assertTrue(any("budget: $1.25" in line for line in lines), lines)
        self.assertTrue(any("spent $0.0140 of $1.25" in line for line in lines))
        run_dir = next(self.out.iterdir())
        self.assertEqual(json.loads((run_dir / "run.json").read_text())["budget"], 1.25)

    def test_web_reserves_room_for_search(self):
        client = FakeClient()
        self.run_fake(client=client, condition="web", budget=1.0, cases=["G01"])
        allowance = referee.web_allowance("claude-opus-5-5", referee.load_prices(), 3, 3)
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
        self.assertEqual(len(self.cases), 60)
        mismatched = {}
        for case in self.cases:
            staged = self.staged[case.id]
            if not staged.lint_ok:
                mismatched[case.id] = (case.lint, staged.lint_seen)
        self.assertEqual(mismatched, {})

    @classmethod
    def setUpClass(cls):
        """The real test set staged once: staging copies and lints the whole dataset per case."""
        cls.cases = referee.load_cases(REPO / "referee/testset/cases.yaml")
        stager = referee.Stager(REPO)
        try:
            cls.staged = {c.id: stager.stage(c) for c in cls.cases}
        finally:
            stager.close()

    def gate_rows(self):
        return {c.id: referee.gate_row(c, self.staged[c.id], None) for c in self.cases}

    def test_b06_is_rejected_by_code_on_s8(self):
        row = self.gate_rows()["B06"]
        self.assertEqual((row.gate, row.ai_rules), ({"S8": "no"}, []))

    def test_breaking_the_domain_comparison_lowers_the_count(self):
        """TAX-15: without source texts the gate settles S5 by domain alone. Breaking the
        comparison (no URL has an owner any more) must make that count drop to 0."""
        count = lambda rows: referee.gate_counts(list(rows.values()))["per_rule"]["S5"]["yes"]  # noqa: E731
        settled = count(self.gate_rows())
        with unittest.mock.patch.object(referee.gate, "owner", lambda url: None):
            broken = count(self.gate_rows())
        self.assertGreaterEqual(settled, 20, "on 13-14 November 2026 the test set gave 23")
        self.assertEqual(broken, 0)


if __name__ == "__main__":
    unittest.main()
