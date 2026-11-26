import contextlib
import io
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import lint  # noqa: E402

PUBLISHER = """\
key: synology
name: Synology Inc.
source: https://www.synology.com/en-global/company
aliases: []
"""

PRODUCT = """\
key: synology/ds224-plus
publisher: synology
name: DS224+
kind: device
source: https://www.synology.com/en-global/products/DS224+
versions:
  source: https://www.synology.com/en-global/releaseNote/DSM
identifiers: []
aliases: []
"""


class LintTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.write("publishers/synology.yaml", PUBLISHER)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")

    def run_lint(self):
        return lint.lint(self.root, REPO / "schema")

    def errors(self):
        return [f for f in self.run_lint() if f.level == "error"]

    def assertOnly(self, rule, line, file="products/synology/ds224-plus.yaml", contains=""):
        found = self.errors()
        self.assertEqual(len(found), 1, found)
        self.assertEqual((found[0].file, found[0].line, found[0].rule), (file, line, rule), found)
        self.assertIn(contains, found[0].message)

    def test_a_key_that_starts_with_its_publisher(self):
        """TAX-54: the lint rejects the long form; the short form passes, with the long one as alias."""
        long = PRODUCT.replace("ds224-plus", "synology-ds224-plus").replace("name: DS224+", "name: Synology DS224+")
        self.write("products/synology/synology-ds224-plus.yaml", long)
        self.assertOnly("TK2", 1, "products/synology/synology-ds224-plus.yaml", "starts with its publisher")
        (self.root / "products/synology/synology-ds224-plus.yaml").unlink()
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("name: DS224+", "name: Synology DS224+")
                   .replace("aliases: []", "aliases:\n- synology/synology-ds224-plus"))
        self.assertEqual(self.errors(), [])

    def test_the_lint_counts_the_lookalike_pairs(self):
        """TAX-56: how many names look alike is a count in the output, each pair once, without a limit."""
        self.write("publishers/synoloqy.yaml", PUBLISHER.replace("synology", "synoloqy").replace("Synology", "Synoloqy"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            lint.main(["lint.py", str(self.root)])
        self.assertIn("pairs of names that look alike (TV3): 2, a count without a limit", out.getvalue(), "the name and its domain")
        self.assertEqual(lint.lookalike_pairs(lint.lint(self.root, REPO / "schema")), 2)

    def test_changed_files_only(self):
        """TAX-16: with a list of changed files, findings only on those, compared with all files."""
        cpe = "identifiers:\n- cpe: cpe:2.3:o:synology:diskstation_manager\n  source: https://nvd.example/\n"
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("identifiers: []\n", cpe))
        self.write("products/synology/ds124.yaml", PRODUCT.replace("ds224-plus", "ds124").replace("DS224+", "DS124")
                   .replace("identifiers: []\n", cpe))
        both = self.run_lint()
        self.assertEqual({(f.rule, f.level) for f in both}, {("TK4", "signal")}, both)
        self.assertEqual(sorted(f.file for f in both), ["products/synology/ds124.yaml", "products/synology/ds224-plus.yaml"])
        only = lint.lint(self.root, REPO / "schema", {"products/synology/ds124.yaml"})
        self.assertEqual(only, [f for f in both if f.file == "products/synology/ds124.yaml"])
        self.assertEqual(lint.lint(self.root, REPO / "schema", set()), [])

    def test_dataset_overrides_match_the_files_on_disk(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT)
        data = lint.Dataset(self.root, REPO / "schema", detailed=set())
        wrong = PRODUCT.replace("name: DS224+", "name: DS 224")
        in_memory = data.check(report={"products/synology/ds224-plus.yaml"}, overrides={"products/synology/ds224-plus.yaml": wrong})
        self.write("products/synology/ds224-plus.yaml", wrong)
        self.assertEqual(in_memory, self.run_lint())
        self.assertTrue(in_memory)
        self.assertEqual(data.check(), [], "the dataset itself is unchanged")

    def test_read_changed_keeps_data_files(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT)
        listing = self.root / "changed.txt"
        listing.write_text("README.md\nproducts/synology/ds224-plus.yaml\nproducts/gone/deleted.yaml\n")
        self.assertEqual(lint.read_changed(str(listing), self.root), {"products/synology/ds224-plus.yaml"})

    def test_good_product_passes(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT)
        self.assertEqual(self.run_lint(), [])

    # TE1
    def test_s1_missing_field(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("kind: device\n", ""))
        self.assertOnly("TE1", 1, contains="'kind' is a required property")

    def test_s1_missing_source_of_versions(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("  source: https://www.synology.com/en-global/releaseNote/DSM\n", "  {}\n").replace("versions:\n  {}", "versions: {}"))
        self.assertOnly("TE1", 6, contains="needs `source`")

    # TP1, TAX-67: a product of which the publisher publishes no versions, but says it gets updates
    NO_VERSIONS = "versions:\n  published: false\n  updates: https://www.synology.com/en-global/support/updates\n"
    WITH_VERSIONS = "versions:\n  source: https://www.synology.com/en-global/releaseNote/DSM\n"

    def test_tp1_product_without_published_versions_passes(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace(self.WITH_VERSIONS, self.NO_VERSIONS))
        self.assertEqual(self.run_lint(), [])

    def test_tp1_no_versions_needs_the_updates_page(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace(self.WITH_VERSIONS, "versions:\n  published: false\n"))
        self.assertOnly("TE1", 6, contains="needs `source`")

    def test_tp1_updates_page_needs_the_mark(self):
        """Without `published: false` a reader cannot see that no versions are published."""
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace(
            self.WITH_VERSIONS, "versions:\n  updates: https://www.synology.com/en-global/support/updates\n"))
        self.assertOnly("TE1", 6, contains="needs `source`")

    def test_tp1_published_true_is_not_a_form(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace(
            self.WITH_VERSIONS, "versions:\n  published: true\n  updates: https://www.synology.com/en-global/support/updates\n"))
        self.assertOnly("TE1", 6, contains="needs `source`")

    def test_tp1_versions_source_and_mark_together_fail(self):
        """A version source ends the mark: a file does not hold both (TAX-67, point 5)."""
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace(
            self.WITH_VERSIONS, self.WITH_VERSIONS + "  published: false\n  updates: https://www.synology.com/en-global/support/updates\n"))
        self.assertOnly("TE1", 6, contains="needs `source`")

    def test_tp1_updates_page_is_checked_as_an_address(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace(
            self.WITH_VERSIONS, "versions:\n  published: false\n  updates: https://192.168.1.10/updates\n"))
        self.assertOnly("TE1", 8, contains="versions/updates")

    def test_s1_identifier_without_source(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("identifiers: []", "identifiers:\n  - gtin: \"4711174724857\""))
        self.assertOnly("TE1", 9, contains="identifiers/0")

    def test_s1_unknown_kind(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("kind: device", "kind: hardware"))
        self.assertOnly("TE1", 4, contains="'hardware' is not one of")

    def test_s1_source_must_be_url(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("source: https://www.synology.com/en-global/products/DS224+", "source: the box"))
        self.assertOnly("TE1", 5, contains="source")

    def test_s1_unknown_publisher(self):
        (self.root / "publishers/synology.yaml").unlink()
        self.write("products/synology/ds224-plus.yaml", PRODUCT)
        self.assertOnly("TE1", 2, contains="has no file publishers/synology.yaml")

    def test_s1_software_needs_no_parent(self):
        self.write("publishers/the-php-group.yaml", "key: the-php-group\nname: The PHP Group\nsource: https://www.php.net/license/\n")
        self.write("products/the-php-group/php.yaml", "key: the-php-group/php\npublisher: the-php-group\nname: PHP\nkind: software\nsource: https://www.php.net/\nversions:\n  source: https://www.php.net/releases/\n")
        self.assertEqual(self.errors(), [])

    def test_s1_publisher_machine_sources(self):
        entry = ("machine_sources:\n  - url: https://downloads.example.com/catalog/Catalog.xml.gz\n"
                 "    format: update-catalog\n    source: https://www.synology.com/en-global/company\n")
        self.write("publishers/synology.yaml", PUBLISHER + entry)
        self.assertEqual(self.errors(), [])
        self.write("publishers/synology.yaml", PUBLISHER + entry.replace("update-catalog", "rss"))
        self.assertOnly("TE1", 7, file="publishers/synology.yaml", contains="machine_sources/0/format")
        self.write("publishers/synology.yaml", PUBLISHER + entry.replace("    source: https://www.synology.com/en-global/company\n", ""))
        self.assertOnly("TE1", 6, file="publishers/synology.yaml", contains="'source' is a required property")

    # TK2
    def test_s2_key_does_not_follow_name(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("key: synology/ds224-plus", "key: synology/ds224"))
        found = self.errors()
        self.assertTrue(found and all(f.rule == "TK2" and f.line == 1 for f in found), found)
        self.assertIn("expected synology/ds224-plus", found[0].message)

    def test_s2_key_with_capitals(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("key: synology/ds224-plus", "key: synology/DS224+"))
        self.assertOnly("TK2", 1, contains="has characters other than")

    def test_s2_part_longer_than_64(self):
        name = "A" * 65
        self.write(f"products/synology/{name.lower()}.yaml", PRODUCT.replace("ds224-plus", name.lower()).replace("DS224+", name))
        self.assertOnly("TK2", 1, file=f"products/synology/{name.lower()}.yaml", contains="the maximum is 64")

    def test_s2_non_latin_name(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("name: DS224+", "name: DS224+ 专业版"))
        self.assertOnly("TK2", 3, contains="only the Latin script")

    def test_s2_file_name_must_match_key(self):
        self.write("products/synology/other.yaml", PRODUCT)
        self.assertOnly("TK2", 1, file="products/synology/other.yaml", contains="does not match the file name")

    def test_s2_publisher_key_follows_name(self):
        self.write("publishers/synology.yaml", PUBLISHER.replace("name: Synology Inc.", "name: Synology Europe"))
        self.assertOnly("TK2", 1, file="publishers/synology.yaml", contains="expected 'synology-europe'")

    # TK3
    def test_s3_key_matches_alias(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT)
        self.write("products/synology/ds224-plus-ii.yaml", PRODUCT.replace("ds224-plus", "ds224-plus-ii").replace("name: DS224+", "name: DS224+ II").replace("aliases: []", "aliases:\n  - synology/ds224-plus"))
        found = self.errors()
        self.assertEqual({(f.file, f.line, f.rule) for f in found}, {
            ("products/synology/ds224-plus.yaml", 1, "TK3"),
            ("products/synology/ds224-plus-ii.yaml", 10, "TK3"),
        })

    def _two_with(self, ident_a, ident_b):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("identifiers: []", "identifiers:\n" + ident_a))
        self.write("products/synology/ds423.yaml", PRODUCT.replace("ds224-plus", "ds423").replace("DS224+", "DS423").replace("identifiers: []", "identifiers:\n" + ident_b))
        return self.run_lint()

    def test_s3_purl_on_another_product_is_a_signal(self):
        ident = "  - purl: pkg:deb/ubuntu/php\n    source: https://example.org/a\n"
        found = self._two_with(ident, ident.replace("ubuntu/php", "ubuntu/PHP"))
        self.assertEqual({(f.rule, f.level, f.line) for f in found}, {("TK4", "signal", 9)}, "TK4: a signal, not an error")

    def test_s3_generic_purl_is_a_signal(self):
        ident = "  - purl: pkg:generic/php\n    source: https://example.org/a\n"
        found = self._two_with(ident, ident)
        self.assertEqual({(f.rule, f.level) for f in found}, {("TK4", "signal")})

    def test_s3_cpe_is_a_signal(self):
        found = self._two_with("  - cpe: cpe:2.3:h:synology:ds224\n    source: https://example.org/a\n",
                               "  - cpe: cpe:2.3:o:synology:ds224:7.2\n    source: https://example.org/b\n")
        self.assertEqual({(f.rule, f.level) for f in found}, {("TK4", "signal")})

    def test_s3_winget_on_another_product_is_a_signal(self):
        found = self._two_with("  - winget: Synology.Assistant\n    source: https://example.org/a\n",
                               "  - winget: synology.assistant\n    source: https://example.org/b\n")
        self.assertEqual({(f.rule, f.level, f.line) for f in found}, {("TK4", "signal", 9)})
        self.assertIn("winget 'synology.assistant' also appears in", found[0].message)

    def test_s3_homebrew_on_another_product_is_a_signal(self):
        ident = "  - homebrew: synology-drive\n    source: https://example.org/a\n"
        found = self._two_with(ident, ident)
        self.assertEqual({(f.rule, f.level) for f in found}, {("TK4", "signal")})

    def test_s1_winget_must_be_an_identifier(self):
        self.write("products/synology/ds224-plus.yaml", PRODUCT.replace("identifiers: []", "identifiers:\n  - winget: not an id\n    source: https://example.org/a"))
        self.assertOnly("TE1", 9, contains="identifiers/0")

    def test_s3_gtin_is_a_signal(self):
        found = self._two_with("  - gtin: \"4711174724857\"\n    source: https://example.org/a\n",
                               "  - gtin: \"04711174724857\"\n    source: https://example.org/b\n")
        self.assertEqual({(f.rule, f.level) for f in found}, {("TK4", "signal")})

    def _product(self, product, name, aliases=()):
        text = PRODUCT.replace("ds224-plus", product).replace("name: DS224+", f"name: {name}")
        if aliases:
            text = text.replace("aliases: []", "aliases:\n" + "".join(f"  - {a}\n" for a in aliases))
        self.write(f"products/synology/{product}.yaml", text)

    def test_s3_same_words_other_order_is_a_signal(self):
        self._product("latitude-5430-rugged", "Latitude 5430 Rugged")
        self._product("latitude-rugged-5430", "Latitude Rugged 5430")
        found = self.run_lint()
        self.assertEqual({(f.file, f.line, f.rule, f.level) for f in found}, {
            ("products/synology/latitude-5430-rugged.yaml", 1, "TK3", "signal"),
            ("products/synology/latitude-rugged-5430.yaml", 1, "TK3", "signal"),
        })
        new = [f for f in found if f.file == "products/synology/latitude-rugged-5430.yaml"][0]
        self.assertIn("'synology/latitude-5430-rugged' (products/synology/latitude-5430-rugged.yaml)", new.message)
        self.assertIn("another order", new.message)

    def test_s3_real_difference_in_order_is_also_a_signal(self):
        self._product("csv-to-json-converter", "CSV to JSON Converter")
        self._product("json-to-csv-converter", "JSON to CSV Converter")
        found = self.run_lint()
        self.assertEqual(len(found), 2, found)
        self.assertTrue(all(f.rule == "TK3" and f.level == "signal" for f in found), found)

    def test_s3_other_order_of_an_alias_is_a_signal(self):
        self._product("ds224-plus", "DS224+", aliases=["synology/diskstation-ds224-plus"])
        self._product("ds224-plus-diskstation", "DS224+ DiskStation")
        found = self.run_lint()
        self.assertEqual({(f.line, f.level) for f in found if f.file == "products/synology/ds224-plus.yaml"}, {(10, "signal")})
        self.assertIn("synology/diskstation-ds224-plus", [f for f in found if f.file.endswith("diskstation.yaml")][0].message)

    def test_s3_other_order_needs_the_same_publisher(self):
        self.write("publishers/acme.yaml", PUBLISHER.replace("synology", "acme").replace("Synology Inc.", "Acme"))
        self._product("rugged-5430", "Rugged 5430")
        self.write("products/acme/5430-rugged.yaml",
                   PRODUCT.replace("synology/ds224-plus", "acme/5430-rugged").replace("publisher: synology", "publisher: acme").replace("name: DS224+", "name: 5430 Rugged"))
        self.assertEqual(self.run_lint(), [])

    def test_s3_extra_word_is_not_other_order(self):
        self._product("ds224-plus", "DS224+")
        self._product("ds224-plus-ii", "DS224+ II")
        self.assertEqual(self.run_lint(), [])

    def test_rejected_fixture(self):
        found = lint.lint(REPO / "tests/fixtures/rejected", REPO / "schema")
        self.assertTrue(found)
        self.assertTrue(all(f.rule == "TK2" and f.line == 1 for f in found), found)


if __name__ == "__main__":
    unittest.main()
