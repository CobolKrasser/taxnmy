"""LVFS as a filler (TAX-23): tools/lvfs.py on made-up AppStream metadata and dataset, without network.
No test reads products/ or publishers/ of the repository (TAX-56)."""

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
import lvfs  # noqa: E402

LIST = {"version": 1, "page": "https://lvfs.example/devices/{id}", "devices": ["X-System", "X-Device", "X-NetworkInterface"],
        "named": ["X-ManagementEngine"], "parts": ["X-EmbeddedController"], "settings": ["X-Configuration"]}


def component(ident, name, developer, *cats):
    c = "".join(f"<category>{x}</category>" for x in cats)
    return (f"<component type='firmware'><id>{ident}</id><name>{name}</name><developer_name>{developer}</developer_name>"
            f"<categories>{c}</categories><releases><release version='1.0'/></releases></component>")


METADATA = gzip.compress(("<?xml version='1.0'?><components origin='lvfs'>" + "".join([
    component("com.acme.book9.firmware", "Book 9", "Acme", "X-System"),
    component("com.acme.book9.eu.firmware", "Book 9", "Acme", "X-System"),          # the same product
    component("com.acme.dock.firmware", "Dock 2 Firmware", "Acme", "X-Device"),     # 'Firmware' is off
    component("com.acme.me.firmware", "Book 9", "Acme", "X-ManagementEngine"),      # the firmware of Book 9
    component("com.acme.me2.firmware", "Book 7", "Acme", "X-ManagementEngine"),     # Book 7 is not in the round
    component("com.acme.ec.firmware", "Book 9 EC", "Acme", "X-EmbeddedController"),  # a part
    component("com.acme.wwan.firmware", "Modem X", "Acme", "X-Device", "X-NetworkInterface"),  # a device
    component("com.acme.new.firmware", "Thing", "Acme", "X-Device", "X-Hologram"),  # a category not in the list
    component("com.acme.kek.firmware", "Secure Boot KEK", "Acme", "X-Configuration", "X-System"),
    component("com.zed.pad.firmware", "Pad", "Zed Labs", "X-Device"),               # a new publisher
    component("com.acme.old.firmware", "Old Book", "Acme", "X-System"),             # in TAX
]) + "</components>").encode())


class LvfsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "data"
        shutil.copytree(REPO / "schema", self.root / "schema")
        (self.root / "publishers").mkdir()
        (self.root / "publishers/acme.yaml").write_text("key: acme\nname: Acme\nsource: https://acme.example/\naliases: []\n")
        (self.root / "products/acme").mkdir(parents=True)
        (self.root / "products/acme/old-book.yaml").write_text(
            "key: acme/old-book\npublisher: acme\nname: Old Book\nkind: device\nsource: https://acme.example/old\n"
            "versions:\n  source: https://acme.example/old\naliases: []\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_round(self, **kw):
        got = lvfs.run(METADATA, self.tmp / "out", self.root, data=LIST, **kw)
        return got, {i.address.rsplit("/", 1)[1]: i for i in got["items"]}

    def test_every_component_has_one_outcome_with_a_reason(self):
        got, _ = self.run_round()
        self.assertEqual(len(got["items"]), 11)
        for i in got["items"]:
            self.assertIn(i.outcome, ("taken-in", "in-tax", "apart"))
            self.assertTrue(i.reason, i.name)

    def test_the_outcomes(self):
        got, by = self.run_round()
        self.assertEqual(set(got["products"]), {"acme/book-9", "acme/book-7", "acme/dock-2", "acme/modem-x", "zed-labs/pad"})
        self.assertEqual(by["com.acme.book9.eu.firmware"].product, "acme/book-9", "the same key: one product")
        self.assertEqual(by["com.acme.me.firmware"].product, "acme/book-9", "the device its name names")
        self.assertIn("names", by["com.acme.me.firmware"].reason)
        self.assertEqual(got["products"]["acme/book-9"].source, "https://lvfs.example/devices/com.acme.book9.firmware")
        self.assertIn("a part", by["com.acme.ec.firmware"].reason)
        self.assertIn("X-Hologram", by["com.acme.new.firmware"].reason)
        self.assertIn("a setting", by["com.acme.kek.firmware"].reason)
        self.assertEqual((by["com.acme.old.firmware"].outcome, by["com.acme.old.firmware"].product), ("in-tax", "acme/old-book"))

    def test_the_page_of_lvfs_is_the_source(self):
        got, _ = self.run_round()
        book = yaml.safe_load(got["products"]["acme/book-9"].text())
        page = "https://lvfs.example/devices/com.acme.book9.firmware"
        self.assertEqual((book["kind"], book["source"], book["versions"]["source"]), ("device", page, page))
        self.assertEqual(got["products"]["acme/dock-2"].name, "Dock 2")
        zed = yaml.safe_load(got["files"]["publishers/zed-labs.yaml"])
        self.assertEqual((zed["name"], zed["source"]), ("Zed Labs", "https://lvfs.example/devices/com.zed.pad.firmware"))
        self.assertNotIn("machine_sources", zed, "the metadata is LVFS's, not the publisher's")
        self.assertNotIn("publishers/acme.yaml", got["files"], "a publisher TAX knows stays as it is")

    def test_what_is_written_passes_the_lint(self):
        got, _ = self.run_round(write=True)
        self.assertIn("No AI call; nothing fetched", got["report"])
        self.assertEqual([f for f in lint.lint(self.root) if f.level == "error"], [])

    @unittest.skipUnless(sys.version_info >= (3, 14), "compression.zstd is Python 3.14 and later")
    def test_the_full_metadata_is_zstd(self):
        from compression import zstd
        self.assertEqual(lvfs.components(zstd.compress(gzip.decompress(METADATA))), lvfs.components(METADATA))

    def test_the_list_of_the_repository(self):
        data = lvfs.load_list()
        self.assertIn("{id}", data["page"])
        lists = [set(data[k]) for k in ("devices", "named", "parts", "settings")]
        self.assertEqual(sum(map(len, lists)), len(set().union(*lists)), "a category is in one list only")


if __name__ == "__main__":
    unittest.main()
