"""winget as a filler (TAX-19): tools/winget.py on a small made-up dump and dataset, without network."""

import io
import json
import re
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
FIXED = REPO / "referee/testset/data"  # the fixed data of the test set (TAX-56)
sys.path.insert(0, str(REPO / "tools"))
import lint  # noqa: E402
import winget  # noqa: E402


def write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def manifest(ident: str, publisher: str, name: str, url: str = "", version: str = "1.0") -> dict:
    folder = "manifests/" + ident[0].lower() + "/" + ident.replace(".", "/")
    text = f"PackageIdentifier: {ident}\nPackageVersion: '{version}'\nPublisher: {publisher}\nPackageName: {name}\n"
    if url:
        text += f"PublisherUrl: {url}\n"
    return {f"{folder}/{version}/{ident}.yaml": text}


PACKAGES = [
    ("Acme.Rocket", "Acme Inc.", "Rocket"),                 # its winget id is in TAX
    ("Acme.Sled", "Acme Inc.", "Sled", "https://example.com/"),  # a known publisher, a new product
    ("Acme.Glider.2", "Acme Inc.", "Glider 2"),             # TP3: one product, two packages
    ("Acme.Glider.3", "Acme Inc.", "Glider 3"),
    ("Acme.Kite.fr", "Acme Inc.", "Kite (fr)"),             # a variant marker: one product
    ("Acme.Kite.de", "Acme Inc.", "Kite (de)"),
    ("Acme.Hammer", "Acme Inc.", "Hammer"),                 # the key of a product in TAX: it gets the winget id
    ("Other.Drill", "Acme Inc.", "Drill", "https://example.org/"),  # the name of a publisher in TAX: another domain stops nothing
    ("Zed.Pad", "Zed Labs", "Pad 2024"),                       # a new publisher
    ("Zed.Pen.Lite", "Zed Labs", "Pen Lite"),                  # the same words in another order: one product
    ("Zed.LitePen", "Zed Labs", "Lite Pen"),
    ("Zed.Note", "Zed Labs", "Note"),
    ("Acmee.Tool", "Acmee", "Tool"),                           # a publisher like an existing one (TV3): a signal only
    ("Han.Zi", "Han", "汉字"),                                  # a name outside the Latin script
    ("Mail.Box", "Jo Doe jo@mail.example", "Box"),             # an e-mail address in a name
    ("Acme.Anvil", "Acme Inc.", "Acme Anvil"),                  # TAX-54: the short key, the long form an alias
    ("Acme.Saw", "Saw Makers", "Saw"),                          # only the id names a publisher in TAX (TV2): a new one
]


class WingetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "data"
        shutil.copytree(REPO / "schema", self.root / "schema")
        write(self.root, "publishers/acme.yaml", "key: acme\nname: Acme\nsource: https://example.com/about\naliases: []\n")
        for prod, extra in (("rocket", "identifiers:\n- winget: Acme.Rocket\n  source: https://winget.example/acme-rocket\n"),
                            ("hammer", "")):
            write(self.root, f"products/acme/{prod}.yaml",
                  f"key: acme/{prod}\npublisher: acme\nname: {prod.title()}\nkind: software\nsource: https://example.com/{prod}\n"
                  f"versions:\n  source: https://example.com/{prod}/notes\n{extra}aliases: []\n")
        files = {}
        for p in PACKAGES:
            files.update(manifest(*p))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for rel, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo("winget-pkgs-master/" + rel)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        self.dump = self.tmp / "winget.tar.gz"
        self.dump.write_bytes(buf.getvalue())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_round(self, scope="all", **kw):
        got = winget.run(self.dump, self.tmp / "out", self.root, scope, **kw)
        return got, {p.id: p for p in got["packages"]}

    def test_every_package_has_one_outcome_with_a_reason(self):
        _, by = self.run_round()
        self.assertEqual(len(by), len(PACKAGES))
        for p in by.values():
            self.assertIn(p.outcome, ("taken-in", "in-tax", "apart"), p.id)
            self.assertTrue(p.outcome == "taken-in" or p.reason, p.id)

    def test_the_outcomes(self):
        _, by = self.run_round()
        outcome = {i: (p.outcome, p.product) for i, p in by.items()}
        self.assertEqual(outcome["Acme.Rocket"], ("in-tax", "acme/rocket"))
        self.assertEqual(outcome["Acme.Sled"], ("taken-in", "acme/sled"))
        self.assertEqual(outcome["Acme.Glider.2"], ("taken-in", "acme/glider"))
        self.assertEqual(outcome["Acme.Glider.3"], ("taken-in", "acme/glider"))
        self.assertEqual(outcome["Acme.Kite.fr"], ("taken-in", "acme/kite"))
        self.assertEqual(outcome["Acme.Hammer"], ("in-tax", "acme/hammer"))
        self.assertEqual(outcome["Other.Drill"], ("taken-in", "acme/drill"))
        self.assertEqual(outcome["Acmee.Tool"], ("taken-in", "acmee/tool"))
        self.assertEqual(outcome["Acme.Saw"], ("taken-in", "saw-makers/saw"))
        self.assertEqual(outcome["Acme.Anvil"], ("taken-in", "acme/anvil"))
        self.assertEqual(outcome["Zed.Pad"], ("taken-in", "zed-labs/pad"))
        self.assertEqual(outcome["Zed.LitePen"], ("taken-in", "zed-labs/lite-pen"))
        self.assertEqual(outcome["Zed.Pen.Lite"], ("taken-in", "zed-labs/lite-pen"))
        for ident, word in (("Han.Zi", "Latin"), ("Mail.Box", "e-mail")):
            self.assertEqual(by[ident].outcome, "apart", ident)
            self.assertIn(word, by[ident].reason, ident)

    def test_the_files_name_the_page_of_winget_as_source(self):
        got, _ = self.run_round()
        glider = yaml.safe_load(got["products"]["acme/glider"].text())
        page = winget.PAGE.format(folder="manifests/a/Acme/Glider/2")
        self.assertEqual((glider["name"], glider["source"], glider["versions"]["source"]), ("Glider", page, page))
        self.assertEqual([i["winget"] for i in glider["identifiers"]], ["Acme.Glider.2", "Acme.Glider.3"])
        self.assertEqual(glider["identifiers"][1]["source"], winget.PAGE.format(folder="manifests/a/Acme/Glider/3"))
        self.assertEqual(set(got["publishers"]), {"zed-labs", "acmee", "saw-makers"}, "a known publisher gets no second file")
        self.assertEqual(got["publishers"]["saw-makers"]["name"], "Saw Makers", "as the manifest writes it")
        zed = yaml.safe_load(winget.publisher_text("zed-labs", got["publishers"]["zed-labs"]))
        self.assertEqual((zed["name"], zed["source"]), ("Zed Labs", winget.PAGE.format(folder="manifests/z/Zed/LitePen")))

    def test_the_short_key_has_the_long_form_as_alias(self):
        got, _ = self.run_round()
        anvil = yaml.safe_load(got["products"]["acme/anvil"].text())
        self.assertEqual((anvil["key"], anvil["name"], anvil["aliases"]), ("acme/anvil", "Acme Anvil", ["acme/acme-anvil"]))

    def test_what_is_written_passes_the_lint(self):
        self.run_round(write=True)
        self.assertTrue((self.root / "products/acme/sled.yaml").is_file())
        self.assertEqual([f for f in lint.lint(self.root) if f.level == "error"], [])

    def test_a_product_in_tax_gets_the_winget_id(self):
        before = (self.root / "products/acme/hammer.yaml").read_text()
        rocket = (self.root / "products/acme/rocket.yaml").read_text()
        self.run_round(write=True)
        after = (self.root / "products/acme/hammer.yaml").read_text()
        page = winget.PAGE.format(folder="manifests/a/Acme/Hammer")
        self.assertEqual(after, before.replace("aliases: []", f"identifiers:\n- winget: Acme.Hammer\n  source: {page}\naliases: []"))
        self.assertEqual((self.root / "products/acme/rocket.yaml").read_text(), rocket, "its winget id is there already")

    def test_an_id_joins_the_identifiers_there(self):
        text = "key: a/b\nidentifiers:\n- cpe: x\n  source: https://example.com/\naliases:\n- a/c\n"
        p = winget.Package(id="A.B", folder="manifests/a/A/B", version="1", publisher="A", name="B")
        got = yaml.safe_load(winget.with_winget(text, [p]))
        self.assertEqual(got["identifiers"][1], {"winget": "A.B", "source": p.page})
        self.assertEqual(got["aliases"], ["a/c"])

    def test_scope_known_leaves_new_publishers_for_later(self):
        got, by = self.run_round("known")
        self.assertEqual((by["Zed.Pad"].outcome, got["publishers"]), ("later", {}))
        self.assertEqual(by["Acme.Sled"].outcome, "taken-in")

    def test_batches_keep_a_publisher_with_its_products(self):
        got, _ = self.run_round(size=4)
        parts = got["batches"]
        self.assertEqual(sum(map(len, parts)), len(got["products"]) + len(got["publishers"]) + len(got["extend"]))
        zed = [i for i, b in enumerate(parts) if any(rel.endswith(("zed-labs.yaml",)) or "/zed-labs/" in rel for rel in b)]
        self.assertEqual(len(zed), 1)
        self.assertTrue(all(len(b) <= 4 for b in parts if not any("/acme/" in r for r in b)))

    def test_the_report_counts_per_outcome_and_reason(self):
        got, _ = self.run_round()
        self.assertIn("| taken-in | 13 |", got["report"])
        self.assertIn("| in-tax | 2 |", got["report"])
        self.assertIn("| apart | 2 |", got["report"])
        outcomes = json.loads((self.tmp / "out/outcomes.json").read_text())
        self.assertEqual(len(outcomes), len(PACKAGES))


class BaseNameTest(unittest.TestCase):
    def test_versions_and_variants_leave_the_name(self):
        cases = {("Tool 2024", "A.Tool.2024"): "Tool", ("Kit 3.1", "A.Kit.3"): "Kit", ("Pro v7", "A.Pro"): "Pro",
                 ("Box 6", "A.Box.6"): "Box", ("Viewer (x86)", "A.Viewer.x86"): "Viewer",
                 ("Run (CUDA 12.2)", "A.Run.CUDA.12.2"): "Run (CUDA)", ("2048", "A.Game"): "2048",
                 ("Plain", "A.Plain"): "Plain", ("Zulu JDK 16", "A.Zulu.16.JDK"): "Zulu JDK"}
        for (name, ident), want in cases.items():
            self.assertEqual(winget.base_name(name, ident), want, name)


class NoBrandTest(unittest.TestCase):
    """TG4: the importer names no publisher of a fixed list (the data of the test set, TAX-56)."""

    def test_no_publisher_name_in_the_code(self):
        code = (REPO / "tools/winget.py").read_text().lower()
        for f in (FIXED / "publishers").glob("*.yaml"):  # a fixed list, not the data (TAX-56)
            p = yaml.safe_load(f.read_text())
            for n in {p["key"].replace("-", " "), p["name"]}:
                if len(n) >= 4:
                    self.assertIsNone(re.search(rf"(?<![\w-]){re.escape(n.lower())}(?![\w-])", code), n)


if __name__ == "__main__":
    unittest.main()
