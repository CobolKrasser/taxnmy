"""TAX-38: a source that redirects to another address is doubt, with the address it ends at."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import fetch  # noqa: E402
import gate  # noqa: E402
import importer  # noqa: E402
import pr_referee  # noqa: E402
import referee  # noqa: E402
from fetch import Response  # noqa: E402
from test_referee import FIXTURES, PAGE, QUIET, FakeFetcher  # noqa: E402

LONG = "Firmware 7.2.1 is available for this model. " * 10
PRODUCT = "https://www.synology.com/en-global/products/DS501"
VERSIONS = "https://www.synology.com/en-global/releaseNote/DSM?model=DS501"
PUBLISHER = "https://www.acme-devices.example/about"


class SameAddressTest(unittest.TestCase):
    def test_only_the_protocol_and_the_writing_may_differ(self):
        same = [("http://a.example/x", "https://a.example/x"), ("https://A.Example/x", "https://a.example/x"),
                ("https://a.example:443/x", "https://a.example/x"), ("https://a.example", "https://a.example/"),
                ("https://a.example/x#top", "https://a.example/x"), ("https://a.example/x/", "https://a.example/x"), ("https://a.example/%7Euser", "https://a.example/~user")]
        for a, b in same:
            with self.subTest(a=a, b=b):
                self.assertTrue(fetch.same_address(a, b))

    def test_another_host_path_or_query_is_another_address(self):
        other = [("https://a.example/x", "https://www.a.example/x"), ("https://a.example/s9", "https://a.example/s10"), ("https://a.example/x?m=1", "https://a.example/x?m=2"),
                 ("https://a.example/x", "https://b.example/x")]
        for a, b in other:
            with self.subTest(a=a, b=b):
                self.assertFalse(fetch.same_address(a, b))


class ReaderTest(unittest.TestCase):
    def fetcher(self, pages):
        def get(url, timeout):
            return pages.get(url) or Response(404, final_url=url)
        return fetch.Fetcher(get=get, use_browser=False, sleep=lambda s: None)

    def test_the_reader_keeps_the_address_it_ended_at(self):
        f = self.fetcher({PRODUCT: Response(200, "text/html", body=f"<p>{LONG}</p>".encode(), final_url=PRODUCT + "-new")})
        src = f.fetch(PRODUCT)
        self.assertEqual((src.status, src.final_url), ("200", PRODUCT + "-new"))
        self.assertEqual(fetch.moved(src), PRODUCT + "-new")

    def test_no_redirect_and_a_protocol_change_are_not_moved(self):
        body = f"<p>{LONG}</p>".encode()
        f = self.fetcher({PRODUCT: Response(200, "text/html", body=body, final_url=PRODUCT),
                          VERSIONS: Response(200, "text/html", body=body, final_url=VERSIONS.replace("https:", "http:"))})
        self.assertEqual(fetch.moved(f.fetch(PRODUCT)), "")
        self.assertEqual(fetch.moved(f.fetch(VERSIONS)), "")

    def test_an_api_the_reader_chose_is_not_a_redirect(self):
        page = "https://nvd.nist.gov/products/cpe/search/results?keyword=cpe:2.3:o:acme:os"
        api = fetch.api_url(page)
        src = fetch.Source(page, "200", LONG, "api", fetched_url=api, final_url=api)
        self.assertEqual(fetch.moved(src), "")


class Moving(FakeFetcher):
    """The fake reader of the referee tests, with some URLs that end elsewhere."""

    def __init__(self, ends):
        super().__init__()
        self.ends = ends

    def fetch(self, url, product=""):
        src = super().fetch(url, product)
        src.final_url = self.ends.get(url, "")
        return src


def product(source=PRODUCT, versions=VERSIONS, ident=None, publisher="synology"):
    ids = f"identifiers:\n- cpe: cpe:2.3:h:synology:ds501:-\n  source: {ident}\n" if ident else "identifiers: []\n"
    return (f"key: {publisher}/ds501\npublisher: {publisher}\nname: DS501\nkind: device\nsource: {source}\n"
            f"versions:\n  source: {versions}\n{ids}aliases: []\n")


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "root"
        shutil.copytree(FIXTURES / "data", self.root)
        (self.root / "referee").mkdir()
        pr_referee.save_parked([], self.root / "referee/parked.yaml")

    def gate(self, files, ends):
        rel = next(r for r in files if r.startswith("products/")) if any(r.startswith("products/") for r in files) \
            else next(iter(files))
        return pr_referee.gate_proposals([{"id": rel, "files": files}], Moving(ends), self.root, log=QUIET, decisions=[])[0]

    def assertParkedFor(self, item, url, end):
        self.assertEqual((item["outcome"], item["rule"], item["waits_for"]), ("parked", "redirect", "better-proposal"))
        self.assertIn(url, item["reason"])
        self.assertIn(end, item["reason"])

    def test_a_product_source_that_redirects(self):
        end = "https://www.synology.com/en-global/products/DS502"
        self.assertParkedFor(self.gate({"products/synology/ds501.yaml": product()}, {PRODUCT: end}), PRODUCT, end)

    def test_a_versions_source_that_redirects(self):
        end = "https://www.synology.com/en-global/support/overview"
        self.assertParkedFor(self.gate({"products/synology/ds501.yaml": product()}, {VERSIONS: end}), VERSIONS, end)

    def test_a_code_source_that_redirects(self):
        ident = "https://nvd.example/cpe/ds501"
        end = "https://nvd.example/cpe/search"
        item = self.gate({"products/synology/ds501.yaml": product(ident=ident)}, {ident: end})
        self.assertParkedFor(item, ident, end)

    def test_a_publisher_source_that_redirects(self):
        files = {"products/acme-devices/ds501.yaml": product(publisher="acme-devices"),
                 "publishers/acme-devices.yaml": f"key: acme-devices\nname: Acme Devices\nsource: {PUBLISHER}\naliases: []\n"}
        end = "https://www.other-owner.example/"
        self.assertParkedFor(self.gate(files, {PUBLISHER: end}), PUBLISHER, end)

    def test_a_publisher_file_alone_that_redirects(self):
        files = {"publishers/acme-devices.yaml": f"key: acme-devices\nname: Acme Devices\nsource: {PUBLISHER}\naliases: []\n"}
        end = "https://www.other-owner.example/"
        self.assertParkedFor(self.gate(files, {PUBLISHER: end}), PUBLISHER, end)

    def test_the_address_it_ends_at_is_judged_like_any_other(self):
        end = "https://www.synology.com/en-global/products/DS502"
        moved = self.gate({"products/synology/ds501.yaml": product()}, {PRODUCT: end})
        again = self.gate({"products/synology/ds501.yaml": product(source=end)}, {})
        plain = self.gate({"products/synology/ds501.yaml": product()}, {})
        self.assertEqual(moved["outcome"], "parked")
        self.assertEqual((again["outcome"], plain["outcome"]), ("pending", "pending"))

    def test_a_source_in_taxnmy_that_the_proposal_does_not_bring_is_not_held_against_it(self):
        pub = referee.Stager(self.root).stage_files({"products/synology/ds501.yaml": product()}).publisher_source
        item = self.gate({"products/synology/ds501.yaml": product()}, {pub: pub + "elsewhere"})
        self.assertEqual(item["outcome"], "pending")

    def test_a_rejection_still_wins(self):
        dead = "https://www.synology.com/en-global/products/DS128"  # the fake answers 404
        item = self.gate({"products/synology/ds501.yaml": product(source=dead)}, {VERSIONS: VERSIONS + "&x=1"})
        self.assertEqual((item["outcome"], item["rule"]), ("reject", "TP1"))


class PublisherDomainTest(unittest.TestCase):
    """A source that ends on another domain counts nowhere as a source on the publisher's domain."""

    def test_tr5_needs_the_versions_to_stay_on_the_domain(self):
        product = {"versions": {"source": "https://www.acme.example/releases"}}
        publisher = {"source": "https://www.acme.example/about"}
        src = referee.Source("https://www.acme.example/releases", "200", PAGE)
        self.assertEqual(gate.tr5(product, publisher, {src.url: src}, False)["verdict"], "yes")
        src.final_url = "https://www.acme.example/releases/2026"  # another path, same domain: still the domain
        self.assertEqual(gate.tr5(product, publisher, {src.url: src}, False)["verdict"], "yes")
        src.final_url = "https://downloads.reseller.example/acme"
        self.assertIsNone(gate.tr5(product, publisher, {src.url: src}, False))


class ImporterTest(unittest.TestCase):
    def test_a_candidate_whose_page_redirects_is_parked_for_a_better_proposal(self):
        self.assertEqual(importer.PARK["moved"][1], "better-proposal")

        class F:
            def fetch(self, url, product=""):
                return referee.Source(url, "200", f"DS501 {PAGE}", final_url=url + "-other" if "products" in url else "")

        row = type("Row", (), {"source": PRODUCT, "versions": VERSIONS, "name": "DS501"})()
        o = type("O", (), {"outcome": "candidate", "row": row, "new_publisher": False, "reason": "", "detail": "", "moves": [], "instead": [], "publisher": ""})()
        importer.check_pages([o], F())
        self.assertEqual((o.outcome, o.reason), ("parked", "moved"), "TAX-42: the address it ends at redirects again")
        self.assertIn(PRODUCT + "-other", o.detail)
        self.assertIn("no second try", o.detail)


if __name__ == "__main__":
    unittest.main()
