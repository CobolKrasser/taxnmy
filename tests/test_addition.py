"""A change that only adds to a file in TAXNMY is judged on what it adds (TV5, TAX-48)."""

import sys
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import addition  # noqa: E402
import pr_referee  # noqa: E402
import referee  # noqa: E402
from test_alias import Fetcher  # noqa: E402
from test_referee import FIXTURES, QUIET  # noqa: E402

DATA = FIXTURES / "data"
REL = "products/synology/ds124.yaml"
OLD = (DATA / REL).read_text()
PUB = "publishers/synology.yaml"
PUB_OLD = (DATA / PUB).read_text()
CODE_SRC = "https://www.example-registry.org/item/7"
CPE = "cpe:2.3:h:synology:ds124"


def with_code(code=CPE, src=CODE_SRC, text=OLD):
    return text.replace("identifiers: []", f"identifiers:\n- cpe: {code}\n  source: {src}")


def with_machine(page="https://www.synology.com/en-global/support/csaf", url="https://www.synology.com/csaf/provider-metadata.json"):
    return PUB_OLD.replace("aliases: []", f"machine_sources:\n- url: {url}\n  format: csaf-provider\n  source: {page}\naliases: []")


def with_second(url="https://www.example-store.org/synology"):
    return PUB_OLD.replace("aliases: []", f"second_source: {url}\naliases: []")


def gate(files, fetcher):
    proposals = [{"id": next(iter(files)), "files": files}]
    return pr_referee.gate_proposals(proposals, fetcher, DATA, log=QUIET, decisions=[])[0]


class Added(unittest.TestCase):
    def test_the_three_additions_are_found(self):
        self.assertEqual(addition.added({REL: with_code()}, {REL: OLD}), ("identifier", [{"cpe": CPE, "source": CODE_SRC}]))
        self.assertEqual(addition.added({PUB: with_machine()}, {PUB: PUB_OLD})[0], "machine")
        self.assertEqual(addition.added({PUB: with_second()}, {PUB: PUB_OLD}), ("second", ["https://www.example-store.org/synology"]))

    def test_anything_more_is_not_an_addition(self):
        for files, old in (({REL: with_code()}, {}),  # a new product
                           ({REL: with_code(text=OLD.replace("kind: device", "kind: software"))}, {REL: OLD}),
                           ({REL: with_code() + "# referee: accept\n"}, {REL: OLD}),
                           ({REL: OLD}, {REL: with_code()}),  # taken out
                           ({REL: with_code("cpe:2.3:h:synology:ds125")}, {REL: with_code()}),  # changed
                           ({PUB: with_second("https://b.example.org/x")}, {PUB: with_second()}),  # another second source
                           ({PUB: with_second().replace("aliases: []", "machine_sources:\n- url: https://www.synology.com/x\n"
                                                        "  format: appstream\n  source: https://www.synology.com/y\naliases: []")},
                            {PUB: PUB_OLD})):  # two kinds at once
            with self.subTest(files=str(files)[-70:]):
                self.assertIsNone(addition.added(files, old))


class Identifier(unittest.TestCase):
    def test_its_own_source_with_code_and_name_is_accepted_by_code(self):
        fetcher = Fetcher(page="", pages={CODE_SRC: f"Synology DS124: {CPE}"})
        item = gate({REL: with_code()}, fetcher)
        self.assertEqual((item["outcome"], item["by"], item["ai_rules"], item["message"]), ("accept", "code", [], ""))
        self.assertEqual(fetcher.urls, [CODE_SRC], "only the source of the addition is read")
        self.assertEqual(item["decided"]["identifier"]["evidence"], [{"url": CODE_SRC, "quote": CPE}])
        for rule in referee.RULES:
            self.assertEqual(item["decided"][rule]["verdict"], "not judged")

    def test_what_code_cannot_show_gets_the_whole_review(self):
        for pages in ({CODE_SRC: "Synology DS124"},  # the code is not there
                      {CODE_SRC: f"a list of codes: {CPE}"},  # the product name is not there
                      {CODE_SRC: f"Synology DS124: {CPE}5"},  # a longer code
                      {REL: f"Synology DS124: {CPE}"}):  # on another source of the product, not its own
            with self.subTest(pages=pages):
                item = gate({REL: with_code()}, Fetcher(page="DS124 firmware", pages=pages))
                self.assertEqual(item["outcome"], "pending")
                self.assertIn("TE1", item["ai_rules"])
                self.assertNotIn("identifier", item["decided"])

    def test_a_closed_own_source_gets_the_whole_review(self):
        item = gate({REL: with_code()}, Fetcher(page="DS124", closed=(CODE_SRC,)))
        self.assertNotEqual(item["outcome"], "accept")
        self.assertNotIn("identifier", item["decided"])

    def test_an_identifier_on_another_product_gets_the_whole_review(self):
        """TK4 signal: TP3 asks whether it is a product of its own."""
        finding = referee.lint.Finding(REL, 1, "TK4", "on another product", "signal")
        real = referee.Stager.stage_files

        def staged(self, *a, **k):
            st = real(self, *a, **k)
            st.findings.append(finding)
            return st
        with unittest.mock.patch.object(referee.Stager, "stage_files", staged):
            item = gate({REL: with_code()}, Fetcher(page="", pages={CODE_SRC: f"Synology DS124: {CPE}"}))
        self.assertEqual(item["outcome"], "pending")

    def test_a_change_that_does_more_gets_the_whole_review(self):
        text = with_code(text=OLD.replace("name: DS124", "name: DS 124"))
        item = gate({REL: text}, Fetcher(page="DS 124", pages={CODE_SRC: f"Synology DS 124: {CPE}"}))
        self.assertNotIn("identifier", item["decided"])


class Machine(unittest.TestCase):
    PAGE = "https://www.synology.com/en-global/support/csaf"
    URL = "https://www.synology.com/csaf/provider-metadata.json"

    def test_a_page_of_the_publisher_that_writes_it_is_accepted(self):
        item = gate({PUB: with_machine()}, Fetcher(page="", pages={self.PAGE: f"Our CSAF metadata: {self.URL}"}))
        self.assertEqual((item["outcome"], item["by"]), ("accept", "code"))
        self.assertIn("source for machines", item["reason"])

    def test_a_page_that_does_not_write_it_is_rejected(self):
        item = gate({PUB: with_machine()}, Fetcher(page="", pages={self.PAGE: "Our CSAF files: https://www.synology.com/csaf/v2/"}))
        self.assertEqual((item["outcome"], item["rule"]), ("reject", "TE7"))

    def test_a_page_off_the_publishers_domain_is_rejected(self):
        page = "https://www.example.org/synology-csaf"
        item = gate({PUB: with_machine(page=page)}, Fetcher(page=self.URL))
        self.assertEqual((item["outcome"], item["rule"]), ("reject", "TE7"))

    def test_a_page_that_is_not_read_is_parked(self):
        item = gate({PUB: with_machine()}, Fetcher(page=self.URL, closed=(self.PAGE,)))
        self.assertEqual((item["outcome"], item["rule"], item["waits_for"]), ("parked", "TE7", "event"))


class Second(unittest.TestCase):
    URL = "https://www.example-store.org/synology"

    def test_a_second_source_that_names_the_domain_is_accepted(self):
        item = gate({PUB: with_second()}, Fetcher(page="", pages={self.URL: "Synology, www.synology.com"}))
        self.assertEqual((item["outcome"], item["by"]), ("accept", "code"))
        self.assertIn("second source", item["reason"])

    def test_one_that_does_not_is_rejected(self):
        item = gate({PUB: with_second()}, Fetcher(page="", pages={self.URL: "Synology"}))
        self.assertEqual((item["outcome"], item["rule"]), ("reject", "TV4"))


class Redirect(unittest.TestCase):
    def test_a_source_tax_already_holds_is_not_the_proposals_doing(self):
        moved = lambda url: referee.Source(url, "200", "Synology, www.synology.com", final_url=url + "?x=1")  # noqa: E731
        fetcher = Fetcher(page="", pages={Second.URL: "Synology, www.synology.com"})
        real = fetcher.fetch
        fetcher.fetch = lambda url, product="": moved(url) if url == "https://www.synology.com/en-global/company" else real(url, product)
        self.assertEqual(gate({PUB: with_second()}, fetcher)["outcome"], "accept")
        self.assertEqual(pr_referee.redirected({PUB: PUB_OLD}, [moved("https://www.synology.com/en-global/company")]),
                         [("https://www.synology.com/en-global/company", "https://www.synology.com/en-global/company?x=1")],
                         "without the file in TAX it is still the proposal's")


class TestSet(unittest.TestCase):
    """Criteria 3 and 4: the six cases of the test set, and the deliberate break."""

    IDS = ("G40", "B64", "G41", "B65", "G42", "B66")
    NVD = "https://services.nvd.nist.gov/rest/json/cpes/2.0?cpeMatchString=cpe:2.3:a:postgresql:postgresql&resultsPerPage=1"
    PAGES = {NVD: '{"products": [{"cpe": {"cpeName": "cpe:2.3:a:postgresql:postgresql:-", "titles": [{"title": "PostgreSQL"}]}}]}',
             "https://www.postgresql.org/about/": "PostgreSQL: About. PostgreSQL is an open source database.",
             "https://access.redhat.com/kb/5554431": "Red Hat security data: https://security.access.redhat.com/data/csaf/v2/advisories/",
             "https://www.redhat.com/en/blog/red-hat-vex-files-cves-are-now-generally-available":
                 "VEX files at https://access.redhat.com/security/data/csaf/v2/vex/",
             "https://en.wikipedia.org/wiki/PostgreSQL": "PostgreSQL. Website: postgresql.org",
             "https://en.wikipedia.org/wiki/Relational_database": "A relational database is a database."}

    def gate(self):
        proposals = [p for p in pr_referee.testset_proposals(REPO / "referee/testset/cases.yaml") if p["id"] in self.IDS]
        return {i["id"]: i for i in pr_referee.gate_proposals(proposals, Fetcher(page="", pages=self.PAGES), log=QUIET)}

    def test_the_good_cases_are_accepted_by_code_and_the_wrong_ones_not(self):
        items = self.gate()
        self.assertEqual({i: items[i]["outcome"] for i in self.IDS},
                         {"G40": "accept", "B64": "pending", "G41": "accept", "B65": "reject", "G42": "accept", "B66": "reject"})
        self.assertEqual([i for i in self.IDS if items[i]["outcome"] == "accept" and items[i]["by"] != "code"], [])
        self.assertIn("TE1", items["B64"]["ai_rules"], "the wrong identifier goes to the whole review")

    def test_deliberate_break_without_the_own_source_the_wrong_identifier_is_accepted(self):
        with unittest.mock.patch.object(addition, "shown", lambda entry, name, own: entry.get("source")):
            items = self.gate()
        self.assertEqual((items["B64"]["outcome"], items["B64"]["by"]), ("accept", "code"))


if __name__ == "__main__":
    unittest.main()
