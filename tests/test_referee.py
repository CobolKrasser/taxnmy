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


def reply(duplicate_of=None, version=PAGE, **rules):
    data = {r: judgment() for r in referee.RULES}
    data.update(rules)
    # TP3 quotes a version from the versions source; every page of the fake fetcher holds PAGE.
    data["TP3"] = {**data["TP3"], "version": data["TP3"].get("version", version if data["TP3"]["verdict"] == "yes" else None)}
    data["duplicate_of"] = duplicate_of
    return json.dumps(data)


# What the fake referee answers, per proposed product key.
REPLIES = {
    "synology/ds124": reply(**{r: judgment(url="https://www.synology.com/en-global/products/DS124") for r in referee.RULES}),
    "synology/ds1522-plus": reply(TP1=judgment(quote="a quote that is not on the page")),
    "acme/widget": "this is not JSON",
    "synology/plus-ds224": reply(duplicate_of="synology/ds224-plus", TP3=judgment("no")),
    "synology/ds128": reply(),  # never asked: its product source answers 404
    "synology/ds129": reply(),  # wrongly lets an invented product through
    "synology/ds126": reply(TE1=judgment("no", url="https://www.synology.com/en-global/products/DS126")),  # a wrong kind
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
            "G02": ("parked", "ai", False),  # a quote that is not on the page turns yes into doubt
            "H01": ("parked", "error", True),
            "B01": ("reject", "code", True),
            "B07": ("alias", "ai", True),
            "B20": ("reject", "code", True),
            "B21": ("accept", "ai", False),
            "B22": ("", "invalid", False),
        })
        self.assertEqual(len(client.calls), 5, "B01 stops at the lint, B20 at its 404, and B22 does not match its lint field")

    def test_product_source_404_is_rejected_by_code(self):
        results, _, client = self.run_fake(cases=["B20"])
        self.assertEqual((results["B20"].outcome, results["B20"].rule), ("reject", "TP1"))
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
        self.assertEqual(results["G02"].checked["TP1"], "doubt")
        self.assertEqual(results["G02"].verdict["TP1"]["verdict"], "yes")
        self.assertIn("TP1: quote not found", results["G02"].quote_notes[0])

    def test_quote_check_ignores_whitespace_and_case(self):
        verdict = json.loads(reply(TP5=judgment(quote="  FIRMWARE releases\n are published  ")))
        url = "https://www.synology.com/en-global/releaseNote/DSM"
        checked, notes = referee.check_quotes(verdict, [referee.Source(url, "200", PAGE)], versions=url)
        self.assertEqual((checked["TP5"]["verdict"], notes), ("yes", []))

    def test_s4_yes_needs_a_version_from_the_versions_source(self):
        """B30: news about a service is not a version. TP3 is yes only with a version quoted from
        the text of versions.source, checked like every quote."""
        versions, product = "https://example.org/releases", "https://example.org/product"
        sources = [referee.Source(versions, "200", "Release notes. Version 7.2.1 (build 69057) is out."),
                   referee.Source(product, "200", "The product page. Version 9.9 is mentioned here.")]

        def tr4(version, srcs=sources, url=versions):
            verdict = json.loads(reply(version=version, **{r: judgment(url=product, quote="The product page.") for r in referee.RULES}))
            checked, notes = referee.check_quotes(verdict, srcs, versions=url)
            return checked["TP3"]["verdict"], notes

        self.assertEqual(tr4("version  7.2.1 (BUILD 69057)"), ("yes", []))
        self.assertEqual(tr4(None), ("doubt", ["TP3: yes without a version from the versions source"]))
        self.assertEqual(tr4(""), ("doubt", ["TP3: yes without a version from the versions source"]))
        self.assertEqual(tr4("Version 9.9"), ("doubt", [f"TP3: version not found at {versions}"]), "only the versions source counts")
        self.assertEqual(tr4("7.2.1", url=""), ("doubt", ["TP3: version not found at the versions source"]))
        instead = [referee.Source(versions, "refused"), sources[1],
                   referee.Source("https://example.org/feed.xml", "200", "Release notes. Version 7.2.1", instead_of=versions)]
        self.assertEqual(tr4("7.2.1", srcs=instead), ("yes", []), "a source found instead of the versions source counts")

    def test_s4_no_and_doubt_need_no_version(self):
        verdict = json.loads(reply(TP3=judgment("no"), duplicate_of="synology/ds224-plus"))
        self.assertIsNone(verdict["TP3"]["version"])
        checked, notes = referee.check_quotes(verdict, [referee.Source("https://www.synology.com/en-global/releaseNote/DSM", "200", PAGE)])
        self.assertEqual((checked["TP3"]["verdict"], [n for n in notes if n.startswith("TP3")]), ("no", []))

    def test_the_schema_asks_s4_for_a_version(self):
        schema = referee.api_schema(referee.load_schema(REPO / "referee/verdict.schema.json"))
        self.assertIn("version", schema["properties"]["TP3"]["required"])
        self.assertNotIn("version", schema["properties"]["TP5"]["properties"])
        self.assertIsNone(referee.parse_reply(json.dumps({**json.loads(reply()), "TP3": judgment()}), schema)[0],
                          "an TP3 without the field version does not parse")

    def test_versions_url(self):
        files = {"products/a/b.yaml": "key: a/b\npublisher: a\nversions:\n  source: https://a.example/v\n"}
        self.assertEqual(referee.versions_url(files), "https://a.example/v")
        self.assertEqual(referee.versions_url({"products/a/b.yaml": "key: a/b\n"}), "")

    def test_yes_without_quote_is_doubt(self):
        verdict = json.loads(reply(TK2={"verdict": "yes", "reason": "", "evidence": []}))
        checked, _ = referee.check_quotes(verdict, [])
        self.assertEqual(checked["TK2"]["verdict"], "doubt")

    def test_outcome_rules(self):
        base = {r: {"verdict": "yes"} for r in referee.RULES}
        self.assertEqual(referee.outcome({**base, "duplicate_of": None}), ("accept", ""))
        self.assertEqual(referee.outcome({**base, "TK2": {"verdict": "no"}, "TP5": {"verdict": "no"}, "duplicate_of": None}), ("reject", "TP5"))
        self.assertEqual(referee.outcome({**base, "TP3": {"verdict": "no"}, "duplicate_of": "a/b"}), ("alias", "TP3"))
        self.assertEqual(referee.outcome({**base, "TE1": {"verdict": "doubt"}, "duplicate_of": None}), ("parked", ""))

    def test_memory_and_web_skip_the_quote_check(self):
        results, _, client = self.run_fake(condition="memory", cases=["G02"])
        self.assertEqual(results["G02"].outcome, "accept")
        self.assertIn(referee.NO_SOURCES["memory"], client.calls[0]["messages"][0]["content"])
        results, _, client = self.run_fake(condition="web", cases=["G02"], now=LATER)
        self.assertEqual([t["name"] for t in client.calls[0]["tools"]], ["web_search", "web_fetch"])
        self.assertEqual(client.calls[0]["tools"][1]["max_uses"], 3)
        self.assertIn(referee.NO_SOURCES["web"], client.calls[0]["messages"][0]["content"])
        self.assertIn("Read the URLs in the proposal with web fetch", referee.NO_SOURCES["web"])

    def test_te4_cases_are_the_default_outside_fetched(self):
        cases = referee.load_cases(REPO / "referee/testset/cases.yaml")
        self.assertEqual([c.id for c in referee.select(cases, None, "memory")], list(referee.TE4_CASES))
        self.assertEqual(len(referee.select(cases, None, "fetched")), 110)
        self.assertEqual(len(referee.select(cases, ["all"], "web")), 110)

    # staging and the message

    def test_staging(self):
        cases = {c.id: c for c in referee.load_cases(FIXTURES / "cases.yaml")}
        stager = referee.Stager(FIXTURES / "data")
        self.addCleanup(stager.close)
        g01 = stager.stage(cases["G01"])
        self.assertEqual(list(g01.files), ["products/synology/ds124.yaml"])
        self.assertIn("# products of publisher synology that resemble the proposal (at most 20)\nnone", g01.existing,
                      "DS224+ shares no word with DS124")
        self.assertTrue(g01.existing.startswith("# publishers/synology.yaml\nkey: synology"))
        self.assertEqual(g01.publisher_source, "https://www.synology.com/en-global/company")
        self.assertNotIn("synology/ds124: DS124", g01.existing, "a hold-out is not part of what exists")
        g02 = stager.stage(cases["G02"])
        self.assertIn("synology/ds923-plus: DS923+", g02.existing, "context files are part of what exists")
        self.assertIn("synology/ds224-plus: DS224+", g02.existing, "DS1522+ and DS224+ share the word plus")
        self.assertFalse((FIXTURES / "data/products/synology/ds923-plus.yaml").exists(), "staging touches no file")
        self.assertNotIn("ds923-plus", stager.stage(cases["G01"]).existing, "staging is undone")
        b07 = stager.stage(cases["B07"])
        self.assertEqual(b07.lint_seen, "signal")
        self.assertIn("# products/synology/ds224-plus.yaml\nkey: synology/ds224-plus", b07.existing)
        h01 = stager.stage(cases["H01"])
        self.assertIn("# publishers that share a word with acme\nnone", h01.existing)
        self.assertNotIn("# publishers/acme.yaml", h01.existing, "a new publisher is in the proposal")
        self.assertEqual(h01.publisher_source, "")
        self.assertFalse(stager.stage(cases["B22"]).lint_ok)

    def test_existing_shows_only_products_that_resemble_and_at_most_20(self):
        data = self.out / "data"
        shutil.copytree(FIXTURES / "data", data)
        for i in range(30):
            (data / f"products/synology/ds124-r{i}.yaml").write_text(
                f"key: synology/ds124-r{i}\npublisher: synology\nname: DS124 R{i}\nkind: device\n"
                f"source: https://www.synology.com/p/{i}\nversions:\n  source: https://www.synology.com/v/{i}\n")
        (data / "products/synology/rs3621xs.yaml").write_text(
            "key: synology/rs3621xs\npublisher: synology\nname: RS3621xs\nkind: device\n"
            "source: https://www.synology.com/p/rs\nversions:\n  source: https://www.synology.com/v/rs\n")
        stager = referee.Stager(data)
        g01 = stager.stage({c.id: c for c in referee.load_cases(FIXTURES / "cases.yaml")}["G01"])
        block = g01.existing.split("that resemble the proposal (at most 20)\n")[1].split("\n\n")[0].splitlines()
        self.assertEqual(len(block), 20)
        self.assertTrue(all(line.startswith("synology/ds124-r") for line in block), block)
        self.assertNotIn("rs3621xs", g01.existing)

    def test_resembles(self):
        self.assertEqual(referee.resembles(referee.words_of("ds124"), referee.words_of("ds124j", "DS124j")), 1)
        self.assertEqual(referee.resembles(referee.words_of("ds124"), referee.words_of("ds224-plus", "DS224+")), 0)
        self.assertEqual(referee.resembles(referee.words_of("galaxy-tab-s9"), referee.words_of("galaxy-tab-s9-fe")), 3)

    def test_the_data_is_read_once_per_run(self):
        with unittest.mock.patch.object(referee.lint, "Dataset", wraps=referee.lint.Dataset) as dataset, \
             unittest.mock.patch.object(referee.lint, "lint", side_effect=AssertionError("no full lint per case")):
            self.run_fake()
        self.assertEqual(dataset.call_count, 1)

    def test_message_follows_the_prompt(self):
        _, _, client = self.run_fake(cases=["B07"])
        call = client.calls[0]
        self.assertEqual(call["system"][0]["text"], "Judge TP1, TP3, TP5, TK2 and TE1.\n")
        message = call["messages"][0]["content"]
        for tag in ("<proposal>", "<signals>", "<existing>", "<sources>"):
            self.assertIn(tag, message)
        self.assertLess(message.index("<existing>"), message.index("<sources>"))
        self.assertIn("TK3: key 'synology/plus-ds224' has the same words", message)
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

    def test_fetch_keeps_a_base_and_fetches_only_what_it_lacks(self):
        """TAX-18: the measurement reads the sources of A2 and fetches only those of the new cases."""
        url = "https://www.synology.com/en-global/products/DS124"
        base = self.out / "a2.json"
        base.write_text(json.dumps({url: {"url": url, "status": "200", "text": "As read for A2.", "method": "http"}}))

        class Caching(FakeFetcher):
            def __init__(self):
                super().__init__()
                self.cache = {}

            def fetch(self, url, product=""):
                if url not in self.cache:
                    self.cache[url] = super().fetch(url, product)
                return self.cache[url]

        fetcher = Caching()
        args = Args(self.out, output=str(self.out / "sources.json"), cases=["G01"], no_browser=True, base=str(base))
        data = referee.fetch_sources(args, fetcher=fetcher, data_root=FIXTURES / "data", log=QUIET)
        self.assertEqual(data[url]["text"], "As read for A2.")
        self.assertNotIn(url, fetcher.urls)
        self.assertIn("https://www.synology.com/en-global/releaseNote/DSM?model=DS124", data)

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
        elsewhere = "https://www.sec.gov/Archives/synology-10k.htm"
        search.write_text(json.dumps({refused: {"product": "DS124", "domains": ["synology.com"],
                                                "urls": [elsewhere, "https://kb.synology.com/en-global/DS124"]}}))
        args = self.search_args(sources, search=str(search), output=str(self.out / "final.json"), limit=None, no_browser=True)
        fetcher = FakeFetcher()
        data = referee.fetch_sources(args, fetcher=fetcher, data_root=FIXTURES / "data", log=QUIET)
        self.assertNotIn(elsewhere, fetcher.urls, "TAX-49: a URL off the publisher's domains is not read")
        self.assertFalse([k for k in data if k.startswith(elsewhere)])
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

    def test_one_search_per_query_with_the_agreed_model(self):
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
        self.assertEqual(client.calls[0]["model"], referee.SEARCH_MODEL, "the model of step 4, not the referee's")
        self.assertNotEqual(referee.SEARCH_MODEL, referee.MODEL)
        self.assertEqual(client.calls[0]["tools"][0]["type"], referee.search_tool(referee.SEARCH_MODEL))
        self.assertEqual(set(found), {product, versions})
        lines = (next(self.out.glob("*-search-*")) / "results.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["for"], [product, versions])

    def test_the_tool_version_follows_the_model(self):
        self.assertEqual(referee.search_tool("claude-opus-5-5"), "web_search_20260209")
        self.assertEqual(referee.search_tool("claude-sonnet-5-5"), "web_search_20260209")
        self.assertEqual(referee.search_tool("claude-fable-5-2"), "web_search_20260209")
        self.assertEqual(referee.search_tool("claude-haiku-5-5"), "web_search_20250305")
        self.assertEqual(referee.search_tool("claude-opus-5-55"), "web_search_20250305", "an exact name, no prefix")

    def test_the_estimate_counts_the_tokens_of_the_results(self):
        prices = referee.load_prices()
        self.assertAlmostEqual(referee.search_cost("claude-opus-5-5", prices), 0.060, places=3)  # run 44764232264: $0.0596
        self.assertAlmostEqual(referee.search_cost("claude-haiku-5-5", prices), 0.011, places=3)
        with self.assertRaises(SystemExit):
            referee.search_cost("claude-unknown", prices)

    def test_only_the_publishers_domains_count(self):
        self.assertTrue(referee.on_domains("https://helpx.adobe.com/x", ["adobe.com"]))
        self.assertFalse(referee.on_domains("https://www.sec.gov/Archives/adbe.htm", ["adobe.com"]))
        self.assertFalse(referee.on_domains("https://adobe.com.example.net/x", ["adobe.com"]))
        self.assertFalse(referee.on_domains("https://helpx.adobe.com/x", []), "no domains: never")

    def search_client(self, urls, tokens=11_967):
        class SearchClient(FakeClient):
            def create(self, request):
                self.calls.append(request)
                block = Block("web_search_tool_result")
                block.content = [type("R", (), {"url": u})() for u in urls]
                usage = {"input_tokens": tokens, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                         "output_tokens": 85, "web_search_requests": 1}
                return referee.Response(content=[block], stop_reason="end_turn", usage=usage)
        return SearchClient()

    def test_a_search_keeps_only_urls_on_the_publishers_domains(self):
        """Run 44764232264: allowed_domains adobe.com, yet two URLs of sec.gov came back."""
        urls = ["https://helpx.adobe.com/a", "https://www.sec.gov/1.htm", "https://learning.adobe.com/b",
                "https://www.sec.gov/2.htm", "https://partners.adobe.com/c"]
        wanted = {"https://business.adobe.com/products/magento": {"product": "Adobe Commerce", "publisher": "Adobe",
                                                                  "domains": ["adobe.com"]}}
        records = []
        found, spent = referee.run_searches(wanted, "claude-haiku-5-5", 5.0, REPO / "referee/prices.yaml", self.out,
                                            self.search_client(urls), NOW, QUIET, records=records)
        hit = found["https://business.adobe.com/products/magento"]
        self.assertEqual(hit["urls"], [u for u in urls if "adobe" in u])
        self.assertEqual(records[0]["elsewhere"], ["https://www.sec.gov/1.htm", "https://www.sec.gov/2.htm"])
        self.assertEqual(records[0]["tool"], "web_search_20250305")
        self.assertAlmostEqual(spent, 11_967 * 0.10e-6 + 85 * 0.50e-6 + 0.01)

    def test_one_search_on_its_own(self):
        args = referee.parser().parse_args(["search-one", "--product", "Adobe Commerce", "--publisher", "Adobe",
                                            "--domains", "adobe.com", "--out", str(self.out)])
        self.assertEqual(args.model, "", "empty: the model of step 4")
        client = self.search_client(["https://helpx.adobe.com/a", "https://www.sec.gov/1.htm"])
        lines = []
        self.assertEqual(referee.search_one(args, client=client, now=NOW, log=lines.append), {})
        self.assertEqual(client.calls, [], "without --live nothing is sent")
        self.assertIn("claude-haiku-5-5 and web_search_20250305; about $0.011", lines[0])
        args.live = True
        lines = []
        r = referee.search_one(args, client=client, now=NOW, log=lines.append)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["tools"][0]["allowed_domains"], ["adobe.com"])
        self.assertEqual((r["urls"], r["elsewhere"]), (["https://helpx.adobe.com/a"], ["https://www.sec.gov/1.htm"]))
        text = "\n".join(lines)
        self.assertIn("| claude-haiku-5-5 | web_search_20250305 | 11967 | 85 | 1 | $0.0112 |", text)
        self.assertIn("- https://www.sec.gov/1.htm", text)
        self.assertTrue(list(self.out.glob("*-search-claude-haiku-5-5")), "the run counts against the budget")

    def test_the_search_workflows_take_the_model_of_step_4(self):
        import yaml
        wf = yaml.safe_load((REPO / ".github/workflows/referee-search.yml").read_text())
        job = wf["jobs"]["search"]
        self.assertEqual(job["environment"], "referee")
        self.assertLessEqual(job["timeout-minutes"], 10)
        self.assertEqual(wf["concurrency"]["group"], "referee-judge")
        self.assertEqual(set(wf[True]["workflow_dispatch"]["inputs"]), {"product", "publisher", "domains", "model"})
        text = json.dumps(job)
        self.assertIn("search-one", text)
        self.assertNotIn("requirements-fetch", text, "no page is read")
        self.assertNotIn("pr_referee", text, "nothing is judged")
        for name, job_name in (("referee-import.yml", "search"), ("referee.yml", "search")):
            steps = json.dumps(yaml.safe_load((REPO / ".github/workflows" / name).read_text())["jobs"][job_name])
            self.assertNotIn("--model", steps, f"{name}: the search takes referee.SEARCH_MODEL")

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
        closed = referee.Result(id="G09", type="t", expect=["accept"], outcome="parked", by="ai",
                                sources=[["https://a/1", "refused"], ["https://a/2", "200"]])
        timeout = referee.Result(id="G03", type="t", expect=["accept"], outcome="parked", by="ai",
                                 sources=[["https://a/3", "error: TimeoutError"]])
        doubt = referee.Result(id="G12", type="t", expect=["accept"], outcome="parked", by="ai", sources=[["https://a/4", "200"]])
        m = referee.metrics([closed, timeout, doubt])
        self.assertEqual((m["parked_closed"], m["parked_doubt"]), (["G09", "G03"], ["G12"]))
        summary = referee.summarise([closed, timeout, doubt], "x", "fetched", 20, 0)
        self.assertIn("| parked because a source is closed (refused, disallowed, error) | 2 |", summary)
        self.assertIn("| parked because the referee doubts | 1 |", summary)

    def test_te4_conditions_read_their_own_prompt(self):
        te4 = self.out / "prompt-r13.md"
        te4.write_text("## System prompt\n\n```text\nJudge from what you know.\n```\n")
        default = referee.REFEREE / "prompt.md"
        with unittest.mock.patch.object(referee, "TE4_PROMPT", te4):
            _, run_dir, client = self.run_fake(condition="memory", cases=["G01"], prompt=default)
            self.assertEqual(client.calls[0]["system"][0]["text"], "Judge from what you know.\n")
            self.assertEqual(json.loads((run_dir / "run.json").read_text())["prompt"], str(te4))
            _, _, client = self.run_fake(condition="fetched", cases=["G01"], prompt=default, now=LATER)
            self.assertTrue(client.calls[0]["system"][0]["text"].startswith("You are the referee of TAXNMY"))

    def test_te4_prompt_must_exist(self):
        with unittest.mock.patch.object(referee, "TE4_PROMPT", self.out / "missing.md"):
            with self.assertRaises(SystemExit):
                self.run_fake(condition="memory", cases=["G01"], prompt=referee.REFEREE / "prompt.md")

    def test_registrable(self):
        self.assertEqual(referee.registrable("www.dell.com"), "dell.com")
        self.assertEqual(referee.registrable("support.d-imaging.sony.co.jp"), "sony.co.jp")
        self.assertEqual(referee.registrable("www.sony.nl"), "sony.nl")

    def test_budget_default_is_50(self):
        """17 November 2026: the limit for every run together goes from $20 to $50."""
        self.assertEqual(referee.parser().parse_args(["run"]).budget, 50.0)
        self.assertEqual(referee.BUDGET, 50.0)

    def test_system_prompt_of_the_analyst(self):
        text = referee.load_system_prompt(REPO / "referee/prompt.md")
        self.assertTrue(text.startswith("You are the referee of TAXNMY"))
        self.assertIn("TE1 Every value", text)
        self.assertIn("When the source is unavailable,\n    answer doubt.", text)
        self.assertIn("releases of another publisher unchanged is not a product of its own.", text)
        self.assertNotIn("```", text)

    def test_api_schema(self):
        schema = referee.api_schema(referee.load_schema(REPO / "referee/verdict.schema.json"))
        text = json.dumps(schema)
        self.assertNotIn("$ref", text)
        self.assertNotIn("$schema", text)
        self.assertEqual(schema["properties"]["TP3"]["properties"]["verdict"], {"enum": ["yes", "no", "doubt"], "type": "string"})

    # measuring

    def test_metrics(self):
        results, _, _ = self.run_fake()
        m = referee.metrics(list(results.values()))
        self.assertEqual(m["let_through"], ["B21"])
        self.assertEqual(m["good_rejected"], [])
        self.assertEqual(m["parked"], ["G02", "H01"])
        self.assertEqual(m["errors"], ["H01"])
        self.assertEqual(m["invalid"], ["B22"])
        self.assertEqual(m["right_rule"], {"TE1": [2, 1], "TP3": [1, 1], "TP1": [2, 1]})  # the lint and the AI both check TE1
        self.assertEqual(m["per_type"]["hallucinated"], [2, 1])
        self.assertAlmostEqual(m["cost_per_call"], 0.014)  # 1000 in at $4/M, 500 out at $20/M
        te4 = m["te4"]
        self.assertEqual(te4["per_rule"]["TP1"], {"right": 1, "wrong": 1, "doubt": 1})
        self.assertIn("B21 TP1", te4["confident_wrong"])

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
        self.assertIn("| B07 | alias (TP3 no) | alias (TP3 no) |", text)

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
        self.assertIn("| G01 | - | yes | yes | referee | TP3, TP1, TE1, TV1 | - |", summary, "TE1 stays with the referee: `kind`")
        self.assertIn("| B01 | lint TE1 | - | - | - | - | - |", summary)
        self.assertIn("| G02 | - | referee | referee | referee | TP3, TP5, TK2, TP1, TE1, TV1 | - |", summary, "its sources were not fetched")

    def test_the_referee_gets_only_the_rules_code_left(self):
        results, _, client = self.run_fake(cases=["G01"])
        request = client.calls[0]
        self.assertEqual(sorted(request["output_config"]["format"]["schema"]["properties"]), ["TE1", "TK2", "TP1", "TP3", "TV1", "duplicate_of"])
        self.assertEqual(request["output_config"]["format"]["schema"]["required"], ["TP3", "TK2", "TP1", "TE1", "TV1", "duplicate_of"])
        message = request["messages"][0]["content"]
        self.assertIn("<decided>\nTP5: yes (code: the versions are published on synology.com", message)
        self.assertIn("\nTE1 in part: code found the name and every code", message)
        self.assertIn("`kind` is not a literal value", message)
        self.assertIn("Judge only: TP3, TK2, TP1, TE1, TV1\n</decided>", message)
        g01 = results["G01"]
        self.assertEqual((g01.gate, g01.ai_rules), ({"TP5": "yes"}, ["TP3", "TK2", "TP1", "TE1", "TV1"]))
        self.assertEqual(g01.checked, {r: "yes" for r in referee.RULES})
        self.assertEqual(g01.outcome, "accept")

    def test_a_wrong_kind_with_every_other_value_right_is_not_accepted(self):
        """TAX-24: code finds the name in its source and the versions source read, but TE1 still
        goes to the referee, so a wrong `kind` can be rejected."""
        cases = self.out / "cases.yaml"
        cases.write_text("""cases:
- id: B31
  type: wrong-kind
  expect: [reject, parked]
  rules: [TE1]
  lint: pass
  proposal:
    products/synology/ds126.yaml: |
      key: synology/ds126
      publisher: synology
      name: DS126
      kind: software
      source: https://www.synology.com/en-global/products/DS126
      versions:
        source: https://www.synology.com/en-global/releaseNote/DSM?model=DS126
      identifiers: []
      aliases: []
""")
        results, _, client = self.run_fake(testset=str(cases))
        b31 = results["B31"]
        self.assertEqual((b31.lint, b31.gate), ("pass", {"TP5": "yes"}))
        self.assertIn("TE1", b31.ai_rules)
        self.assertIn("`kind`", client.calls[0]["messages"][0]["content"])
        self.assertEqual((b31.outcome, b31.by, b31.rule, b31.right), ("reject", "ai", "TE1", True))

    def test_a_source_on_an_ai_chat_is_rejected_by_code_and_not_fetched(self):
        cases = self.out / "cases.yaml"
        cases.write_text("""cases:
- id: B06
  type: ai-source
  expect: [reject]
  rules: [TE1]
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
        self.assertEqual((b06.outcome, b06.by, b06.rule, b06.gate, b06.right), ("reject", "code", "TE1", {"TE1": "no"}, True))
        self.assertEqual((client.calls, fetcher.urls), ([], []))
        self.assertIn("is an AI chat", b06.error)
        self.assertEqual(referee.metrics([b06])["right_rule"], {"TE1": [1, 1]})
        self.assertIn("| TE1 | 0 | 1 | 0 | 0 |", (run_dir / "summary.md").read_text())

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
                  Block("text", '{"TP3"'), Block("text", ": {}}")]
        self.assertEqual(referee.final_text(blocks), '{"TP3": {}}')


class TestsetTest(unittest.TestCase):
    """The real test set, staged on the real data, without network or API."""

    def test_every_case_matches_its_lint_field(self):
        self.assertEqual(len(self.cases), 110)
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

    def gate_rows(self, stored=None):
        return {c.id: referee.gate_row(c, self.staged[c.id], stored) for c in self.cases}

    def test_b06_is_rejected_by_code_on_s8(self):
        row = self.gate_rows()["B06"]
        self.assertEqual((row.gate, row.ai_rules), ({"TE1": "no"}, []))

    def test_breaking_the_domain_comparison_lowers_the_count(self):
        """TAX-15: with every source read, the gate settles TP5 by domain for the publishers TAX
        already holds. Breaking the comparison (no URL has an owner any more) must make that count
        drop to 0, and so must closing every source (TAX-24)."""
        count = lambda rows: referee.gate_counts(list(rows.values()))["per_rule"]["TP5"]["yes"]  # noqa: E731
        settled = count(self.gate_rows(Readable()))
        with unittest.mock.patch.object(referee.gate, "owner", lambda url: None):
            broken = count(self.gate_rows(Readable()))
        self.assertGreaterEqual(settled, 15, "on 16 November 2026 the test set gave 21 with the texts of run A2")
        self.assertEqual(broken, 0)
        self.assertEqual(count(self.gate_rows(Readable(status="refused"))), 0, "a closed source shows nothing")
        self.assertEqual(count(self.gate_rows()), 0, "no texts at hand")

    def test_no_rule_is_left_untested_for_the_cases_of_tax_24(self):
        """TAX-24: with every source read and every value in it, code still leaves TP5 to the
        referee for a publisher proposed on a domain of its own, and TE1 (`kind`) for every case."""
        rows = self.gate_rows(Readable())
        self.assertEqual([i for i, r in rows.items() if r.gate.get("TE1") == "yes"], [])
        for case in ("B31", "B32", "B33"):
            self.assertIn("TP5", rows[case].ai_rules, case)
        for case in ("B34", "B35"):
            self.assertIn("TE1", rows[case].ai_rules, case)
        self.assertEqual(sum(c.id.startswith("B3") and c.type.startswith(("wrong-publisher-own", "wrong-kind"))
                             for c in self.cases), 5)

    def test_the_cases_of_tax_18_reach_the_referee(self):
        """TAX-18 and TAX-31: a service without a version leaves TP3 to the referee. A name or
        domain one or two characters from an existing one gets a TV3 signal from the lint and is
        held for a person by code (TV3), and still reaches the referee: code settles neither TP5 nor
        TK2 for it, so no rule is left untested (TR11)."""
        rows = self.gate_rows(Readable())
        for case in ("B30", "B36", "B37", "B38", "B43"):
            self.assertIn("TP3", rows[case].ai_rules, case)
        for case in ("B39", "B40", "B41", "B42", "B44"):
            self.assertIn("TP5", rows[case].ai_rules, case)
        for case in ("B39", "B40", "B41", "B42", "B43", "B44"):
            self.assertIn("TV3", rows[case].held, case)
            self.assertEqual(self.staged[case].lint_seen, "signal", case)
        for case in ("B39", "B40", "B41", "B42", "B43"):
            self.assertIn("TK2", rows[case].ai_rules, case)
        self.assertEqual(sum(c.type == "saas" for c in self.cases), 5)
        self.assertEqual(sum(c.type.startswith("lookalike-") for c in self.cases), 6)


class Readable:
    """A fetcher whose every page is read and holds every value: the most code could find."""

    def __init__(self, status="200"):
        self.status = status

    def fetch(self, url, product=""):
        if self.status != "200":
            return referee.Source(url, self.status)
        return referee.Source(url, "200", f"{product} {url} {PAGE}")

if __name__ == "__main__":
    unittest.main()
