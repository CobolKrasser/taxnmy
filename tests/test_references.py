"""Two kinds of reference (TAX-68): tools/references.py on made-up LVFS metadata, endoflife dump and dataset,
without network. No test reads products/ or publishers/ of the repository (TAX-56)."""

import gzip
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import lint  # noqa: E402
import references  # noqa: E402

PAGE = "https://lvfs.example/devices/{id}"
LV = {"version": 1, "page": PAGE, "folded": {"acme/book-9-old": "com.acme.book9.old.firmware"}}
EOL = {"version": 1, "lines": ["device"]}

G_BOOK = "11111111-1111-5111-8111-111111111111"
G_BOOK2 = "a2b2c2d2-e2f2-5222-8222-abcdefabcdef"
G_OLD = "33333333-3333-5333-8333-333333333333"
G_SHARED = "44444444-4444-5444-8444-444444444444"
G_DOCK = "55555555-5555-5555-8555-555555555555"
G_OTHER = "66666666-6666-5666-8666-666666666666"


def component(ident, *guids, kind="flashed"):
    provides = "".join(f"<firmware type='{kind}'>{g}</firmware>" for g in guids)
    return f"<component type='firmware'><id>{ident}</id><provides>{provides}</provides></component>"


METADATA = gzip.compress(("<?xml version='1.0'?><components origin='lvfs'>" + "".join([
    component("com.acme.book9.firmware", G_BOOK, G_BOOK2.upper()),  # LVFS may write capitals
    component("com.acme.book9.firmware", G_BOOK),                   # the same id once more: one GUID
    component("com.acme.book9.old.firmware", G_OLD),                # a product that Book 9 took in (TAX-66)
    component("com.acme.dock.firmware", G_DOCK, G_SHARED),
    component("com.zed.dock.firmware", G_SHARED),                    # G_SHARED: at two ids
    component("com.acme.pad.firmware", G_OTHER, kind="runtime"),     # not a GUID the device reports as flashed
    component("com.acme.other.firmware", "not-a-guid"),
]) + "</components>").encode())

DUMP = {"result": [
    {"name": "rocket", "category": "app"},
    {"name": "acme-phone", "category": "device"},  # a line of devices: many models, no product
    {"name": "zed-os", "category": "os"},
]}


def product(key, source, identifiers="identifiers: []\n", aliases="aliases: []\n", kind="device"):
    pub, _ = key.split("/")
    return (f"key: {key}\npublisher: {pub}\nname: {key.split('/')[1].title()}\nkind: {kind}\nsource: {source}\n"
            f"versions:\n  source: {source}\n{identifiers}{aliases}")


class ReferencesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "data"
        for pub in ("acme", "zed", "openjs"):
            self.write(f"publishers/{pub}.yaml", f"key: {pub}\nname: {pub.title()}\nsource: https://{pub}.example/\naliases: []\n")
        self.write("products/acme/book-9.yaml", product(
            "acme/book-9", PAGE.format(id="com.acme.book9.firmware"),
            identifiers="identifiers:\n- cpe: cpe:2.3:h:acme:book_9\n  source: https://nvd.example/\n",
            aliases="aliases:\n- acme/book-9-old\n"))
        self.write("products/acme/dock.yaml", product("acme/dock", PAGE.format(id="com.acme.dock.firmware")))
        self.write("products/zed/dock.yaml", product("zed/dock", PAGE.format(id="com.zed.dock.firmware"),
                                                     identifiers="", aliases="aliases: []\narchived:\n  source: https://zed.example/end\n"))
        self.write("products/acme/pad.yaml", product("acme/pad", PAGE.format(id="com.acme.pad.firmware")))
        self.write("products/acme/gone.yaml", product("acme/gone", PAGE.format(id="com.acme.gone.firmware")))
        self.write("products/acme/elsewhere.yaml", product("acme/elsewhere", "https://acme.example/book-9"))
        self.write("products/openjs/rocket.yaml", product("openjs/rocket", "https://endoflife.date/rocket", kind="software"))
        self.write("products/acme/phone-3.yaml", product("acme/phone-3", "https://endoflife.date/acme-phone"))
        self.write("products/zed/os.yaml", product("zed/os", "https://endoflife.date/zed-os", kind="operating-system",
                                                  identifiers="identifiers: []\nidentifiers:\n- purl: pkg:generic/zed-os\n"
                                                              "  source: https://endoflife.date/zed-os\n"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def read(self, rel):
        return yaml.safe_load((self.root / rel).read_text(encoding="utf-8"))

    def codes(self, rel, kind):
        return [(i[kind], i["source"]) for i in self.read(rel).get("identifiers") or [] if kind in i]

    def run_round(self, write=True):
        return references.run(self.root, self.tmp / "out", METADATA, DUMP, write=write, lv=LV, eol=EOL)

    def test_a_product_of_lvfs_carries_every_guid_of_its_id(self):
        """Criterion 2: the GUIDs of its id, in lower case, each with the page of LVFS of its id as source; the
        GUIDs of a product it took in as an alias (TAX-66) as well."""
        self.run_round()
        page = PAGE.format(id="com.acme.book9.firmware")
        self.assertEqual(self.codes("products/acme/book-9.yaml", "lvfs"),
                         [(G_BOOK, page), (G_BOOK2, page), (G_OLD, PAGE.format(id="com.acme.book9.old.firmware"))])
        self.assertEqual(self.codes("products/acme/pad.yaml", "lvfs"), [], "only a GUID the device reports as flashed")
        self.assertEqual(self.codes("products/acme/gone.yaml", "lvfs"), [], "an id LVFS no longer names")
        self.assertEqual(self.codes("products/acme/elsewhere.yaml", "lvfs"), [], "not a product of LVFS")

    def test_a_guid_at_more_than_one_id_goes_onto_no_product_and_is_listed(self):
        """Criterion 2: G_SHARED is named at two ids: on neither product, and on the list of the story."""
        got = self.run_round()
        self.assertEqual(self.codes("products/acme/dock.yaml", "lvfs"), [(G_DOCK, PAGE.format(id="com.acme.dock.firmware"))])
        self.assertEqual(self.codes("products/zed/dock.yaml", "lvfs"), [])
        self.assertEqual(got["shared"], {G_SHARED: ["com.acme.dock.firmware", "com.zed.dock.firmware"]})
        listed = {(k, g) for k, v in got["withheld"].items() for g, _ in v}
        self.assertEqual(listed, {("acme/dock", G_SHARED), ("zed/dock", G_SHARED)})
        self.assertIn(f"| zed/dock | {G_SHARED} | com.acme.dock.firmware, com.zed.dock.firmware |", got["report"])

    def test_a_product_of_a_page_that_is_one_product_carries_its_name(self):
        """Criterion 3: the page name, with the page as source; a page of a line of devices gives none."""
        self.run_round()
        self.assertEqual(self.codes("products/openjs/rocket.yaml", "endoflife"), [("rocket", "https://endoflife.date/rocket")])
        self.assertEqual(self.codes("products/zed/os.yaml", "endoflife"), [("zed-os", "https://endoflife.date/zed-os")])
        self.assertEqual(self.codes("products/acme/phone-3.yaml", "endoflife"), [], "a page with many products")
        self.assertEqual(self.codes("products/acme/book-9.yaml", "endoflife"), [], "not a product of endoflife")

    def test_nothing_is_changed_or_taken_out(self):
        """Criterion 4: every line of every file stays; the round only adds lines (and fills `identifiers: []`)."""
        before = {p: p.read_text(encoding="utf-8") for p in self.root.rglob("*.yaml")}
        self.run_round()
        for p, old in before.items():
            new = p.read_text(encoding="utf-8").splitlines()
            rest = iter(new)
            kept = [line for line in old.splitlines() if line != "identifiers: []"]
            self.assertTrue(all(line in rest for line in kept), p)  # the old lines, in order
            old_d, new_d = yaml.safe_load(old), yaml.safe_load(p.read_text(encoding="utf-8"))
            self.assertEqual({k: v for k, v in new_d.items() if k != "identifiers"},
                             {k: v for k, v in old_d.items() if k != "identifiers"}, p)
            self.assertEqual(new_d.get("identifiers", [])[:len(old_d.get("identifiers") or [])], old_d.get("identifiers") or [], p)
        self.assertEqual(self.read("products/zed/dock.yaml")["archived"], {"source": "https://zed.example/end"})
        self.assertEqual([f for f in lint.lint(self.root, REPO / "schema") if f.level == "error"], [])

    def test_a_second_round_adds_nothing(self):
        self.run_round()
        self.assertEqual(self.run_round()["add"], {})

    def test_without_write_the_dataset_stays(self):
        before = {p: p.read_text(encoding="utf-8") for p in self.root.rglob("*.yaml")}
        got = self.run_round(write=False)
        self.assertEqual({p: p.read_text(encoding="utf-8") for p in self.root.rglob("*.yaml")}, before)
        self.assertTrue((self.tmp / "out/data/products/openjs/rocket.yaml").exists())
        self.assertEqual(sorted(got["add"]), ["products/acme/book-9.yaml", "products/acme/dock.yaml", "products/openjs/rocket.yaml", "products/zed/os.yaml"])


if __name__ == "__main__":
    unittest.main()
