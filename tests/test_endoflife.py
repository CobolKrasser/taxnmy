"""endoflife as a filler (TAX-57): tools/endoflife.py on a small made-up dump, list and dataset, without network."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import endoflife  # noqa: E402
import lint  # noqa: E402


def write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def release(label: str, maintained: bool = True) -> dict:
    return {"name": label.lower().replace(" ", "-"), "label": label, "isMaintained": maintained}


DUMP = {"generated_at": "2026-11-22T00:00:00+00:00", "total": 7, "result": [
    # a line of devices: each model is a product
    {"name": "acme-phone", "label": "Acme Phone", "category": "device", "identifiers": [],
     "releases": [release("Phone 3"), release("Phone 3+"), release("Phone 2", False), release("Mini"),
                  release("Phone  3")]},  # the same key as "Phone 3": one product
    # a software page that TAX has: its source names the page
    {"name": "rocket", "label": "Rocket", "category": "app", "identifiers": [], "releases": []},
    # a software page with a publisher in the list, and a cpe the schema refuses
    {"name": "sled", "label": "Sled", "category": "server-app", "releases": [],
     "identifiers": [{"type": "cpe", "id": "cpe:/a:acme:sled"}, {"type": "purl", "id": "pkg:npm/sled"}]},
    # a page of a new publisher
    {"name": "zed-os", "label": "Zed OS", "category": "os", "identifiers": [], "releases": []},
    # a page without a publisher in the list, whose name TAX has
    {"name": "hammer", "label": "Acme Hammer", "category": "app", "identifiers": [], "releases": []},
    # a page without a publisher in the list
    {"name": "drill", "label": "Drill", "category": "app", "identifiers": [], "releases": []},
    # a page out of the round
    {"name": "std", "label": "STD", "category": "standard", "identifiers": [], "releases": []},
]}

LIST = {"version": 1, "also": ["https://mirror.example/products/{slug}.md"],
        "kinds": {"device": "device", "os": "operating-system"}, "lines": ["device"],
        "publishers": {"zed": {"name": "Zed", "page": "zed-os"}, "unused": {"name": "Unused", "page": "drill"}},
        "pages": {"acme-phone": {"publisher": "acme", "prefix": "Acme"}, "sled": {"publisher": "acme"},
                  "rocket": {"publisher": "acme"}, "zed-os": {"publisher": "zed"}},
        "apart": {"std": "a standard, not a product"}}


class EndOfLifeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "data"
        shutil.copytree(REPO / "schema", self.root / "schema")
        write(self.root, "publishers/acme.yaml", "key: acme\nname: Acme\nsource: https://example.com/about\naliases: []\n")
        for prod, name, src in (("rocket", "Rocket", "https://mirror.example/products/rocket.md"),
                                ("hammer", "Hammer", "https://example.com/hammer"),
                                ("mini", "Acme Mini", "https://example.com/mini")):
            write(self.root, f"products/acme/{prod}.yaml",
                  f"key: acme/{prod}\npublisher: acme\nname: {name}\nkind: software\nsource: {src}\n"
                  f"versions:\n  source: {src}\nidentifiers: []\naliases: []\n")
        self.dump = self.tmp / "full.json"
        self.dump.write_text(json.dumps(DUMP))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_round(self, **kw):
        got = endoflife.run(self.dump, self.tmp / "out", self.root, data=LIST, **kw)
        return got, {(i.page, i.row): i for i in got["items"]}

    def test_every_page_and_model_has_one_outcome_with_a_reason(self):
        got, by = self.run_round()
        self.assertEqual(len(got["items"]), 5 + 6)
        for i in got["items"]:
            self.assertIn(i.outcome, ("taken-in", "in-tax", "apart"), i.name)
            self.assertTrue(i.outcome == "taken-in" or i.reason, i.name)

    def test_the_outcomes(self):
        _, by = self.run_round()
        outcome = {k: (i.outcome, i.product) for k, i in by.items()}
        self.assertEqual(outcome[("acme-phone", "Phone 3")], ("taken-in", "acme/phone-3"))
        self.assertEqual(outcome[("acme-phone", "Phone 3+")], ("taken-in", "acme/phone-3-plus"), "3+ is not 3")
        self.assertEqual(outcome[("acme-phone", "Phone  3")], ("taken-in", "acme/phone-3"), "the same key: one product")
        self.assertEqual(outcome[("acme-phone", "Phone 2")], ("taken-in", "acme/phone-2"), "taken in, also unsupported")
        self.assertEqual(outcome[("acme-phone", "Mini")], ("in-tax", "acme/mini"), "the name with its prefix is in TAX")
        self.assertEqual(outcome[("rocket", "")], ("in-tax", "acme/rocket"))
        self.assertIn("names the page", by[("rocket", "")].reason)
        self.assertEqual(outcome[("sled", "")], ("taken-in", "acme/sled"))
        self.assertEqual(outcome[("zed-os", "")], ("taken-in", "zed/os"))
        self.assertEqual(outcome[("hammer", "")], ("in-tax", "acme/hammer"), "the name, with the publisher in front")
        self.assertEqual(by[("drill", "")].outcome, "apart")
        self.assertIn("no publisher", by[("drill", "")].reason)
        self.assertEqual(by[("std", "")].outcome, "apart")
        self.assertIn("a standard", by[("std", "")].reason)

    def test_the_files_name_the_page_as_source(self):
        got, _ = self.run_round()
        phone = yaml.safe_load(got["products"]["acme/phone-3"].text())
        page = endoflife.PAGE.format(slug="acme-phone")
        self.assertEqual((phone["name"], phone["kind"], phone["source"], phone["versions"]["source"]),
                         ("Acme Phone 3", "device", page, page))
        self.assertEqual(phone["aliases"], ["acme/acme-phone-3"], "the long form is an alias (TAX-54)")
        self.assertNotIn("archived", phone)
        old = yaml.safe_load(got["products"]["acme/phone-2"].text())
        self.assertEqual(old["archived"], {"source": page}, "a model without support comes in archived (TP10)")
        zed = yaml.safe_load(got["products"]["zed/os"].text())
        self.assertEqual(zed["kind"], "operating-system")
        self.assertNotIn("archived", zed, "a page that is one product is never archived by the round")
        self.assertEqual(set(got["publishers"]), {"zed"}, "only the new publishers the round uses")
        pub = yaml.safe_load(endoflife.publisher_text("zed", got["publishers"]["zed"]))
        self.assertEqual((pub["name"], pub["source"]), ("Zed", endoflife.PAGE.format(slug="zed-os")))

    def test_an_identifier_the_schema_refuses_is_left_out(self):
        got, _ = self.run_round()
        sled = yaml.safe_load(got["products"]["acme/sled"].text())
        self.assertEqual(sled["identifiers"], [{"purl": "pkg:npm/sled", "source": endoflife.PAGE.format(slug="sled")}])
        self.assertEqual([d[0] for d in got["dropped"]], ["acme/sled"])

    def test_what_is_written_passes_the_lint(self):
        self.run_round(write=True)
        self.assertTrue((self.root / "products/acme/phone-3-plus.yaml").is_file())
        self.assertEqual([f for f in lint.lint(self.root) if f.level == "error"], [])

    def test_the_report_counts_per_line(self):
        got, _ = self.run_round()
        self.assertIn("| acme-phone | 5 | 4 | 3 | 1 | 1 | 0 |", got["report"])
        self.assertIn("No AI call", got["report"])

    def test_the_list_of_the_repository_names_a_publisher_for_every_line(self):
        data = endoflife.load_list()
        self.assertTrue(set(data["pages"]).isdisjoint(data["apart"]))
        for page, entry in data["pages"].items():
            pub = entry["publisher"]
            self.assertTrue(pub in data["publishers"] or (REPO / f"publishers/{pub}.yaml").is_file(), page)


if __name__ == "__main__":
    unittest.main()
