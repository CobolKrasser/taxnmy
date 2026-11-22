import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import keyrule  # noqa: E402

# The table of hard names the writing rule was tested on.
TABLE = [
    (("Synology Inc.", "DS224+"), "synology/ds224-plus"),
    (("AVM GmbH", "FRITZ!Box 7590 AX"), "avm/fritz-box-7590-ax"),
    (("Ring LLC", "Video Doorbell (2nd Gen)"), "ring/video-doorbell-2nd-gen"),
    (("RATIONAL AG", "iCombi Pro 6-1/1"), "rational/icombi-pro-6-1-1"),
    (("Samsung", "Galaxy Tab S9 FE+"), "samsung/galaxy-tab-s9-fe-plus"),
    (("Miele & Cie. KG", "Dialogoven DO 7860"), "miele/dialogoven-do-7860"),
    (("Microsoft Corporation", ".NET Framework"), "microsoft/net-framework"),
    (("Microsoft", "C#"), "microsoft/c-sharp"),
    (("Don Ho", "Notepad++"), "don-ho/notepad-plus-plus"),
    (("OpenJS Foundation", "Node.js"), "openjs-foundation/node-js"),
    (("AT&T", "Wi‑Fi Gateway"), "at-and-t/wi-fi-gateway"),
    (("HP Inc.", "Color LaserJet Pro MFP M479fdw"), "hp/color-laserjet-pro-mfp-m479fdw"),
    (("Hewlett Packard Enterprise", "ProLiant DL380 Gen11"), "hewlett-packard-enterprise/proliant-dl380-gen11"),
    (("BRÖTJE", "WGB‑K 20 E"), "brotje/wgb-k-20-e"),
    (("Hikvision", "DS-2CD2143G2-I"), "hikvision/ds-2cd2143g2-i"),
    (("The PHP Group", "PHP"), "the-php-group/php"),
    (("Synology", "DS224+", "7.2.1-69057 Update 5"), "synology/ds224-plus@7.2.1-69057-update-5"),
    (("Acme B.V.", "Baz", "2.149 (Build 602)"), "acme/baz@2.149-build-602"),
]


class TableTest(unittest.TestCase):
    def test_table(self):
        for args, expected in TABLE:
            with self.subTest(args=args):
                self.assertEqual(keyrule.make_key(*args), expected)

    def test_every_table_key_has_a_valid_form(self):
        for _, expected in TABLE:
            with self.subTest(key=expected):
                self.assertEqual(keyrule.key_problems(expected), [])


class RuleTest(unittest.TestCase):
    def test_legal_form_makes_one_publisher(self):
        self.assertEqual(keyrule.publisher_part("Synology Inc."), keyrule.publisher_part("Synology"))

    def test_legal_form_only_for_publisher(self):
        self.assertEqual(keyrule.product_part("Widget Inc."), "widget-inc")

    def test_legal_form_alone_is_kept(self):
        self.assertEqual(keyrule.publisher_part("AG"), "ag")

    def test_plus_keeps_products_apart(self):
        self.assertNotEqual(keyrule.product_part("AviSynth"), keyrule.product_part("AviSynth+"))
        self.assertNotEqual(keyrule.product_part("RT-AC51U"), keyrule.product_part("RT-AC51U+"))

    def test_version_with_at_sign(self):
        self.assertEqual(keyrule.version_part("12.16.4@153.0"), "12.16.4-153.0")

    def test_version_keeps_dot_plus_underscore(self):
        self.assertEqual(keyrule.version_part("1.0.0+build_7"), "1.0.0+build_7")

    def test_non_latin_is_rejected(self):
        for name in ("阿里旺旺", "360极速浏览器"):
            with self.subTest(name=name):
                with self.assertRaises(keyrule.KeyRuleError):
                    keyrule.product_part(name)

    def test_empty_is_rejected(self):
        with self.assertRaises(keyrule.KeyRuleError):
            keyrule.product_part("!!!")

    def test_part_longer_than_64(self):
        self.assertTrue(keyrule.part_problems("a" * 65, "product part"))
        self.assertFalse(keyrule.part_problems("a" * 64, "product part"))

    def test_the_publisher_in_front_is_dropped(self):
        """TAX-54: the product part does not repeat the publisher part; when nothing is left, it stays."""
        self.assertEqual(keyrule.make_key("Acme Inc.", "Acme Widget Pro"), "acme/widget-pro")
        self.assertEqual(keyrule.make_key("Acme", "Acme"), "acme/acme")
        self.assertEqual(keyrule.make_key("Acme Labs", "Acme Widget"), "acme-labs/acme-widget", "only the whole publisher part")
        self.assertEqual(keyrule.make_key("Acme", "Acmeware"), "acme/acmeware", "only whole words")
        self.assertEqual(keyrule.product_part("Acme Widget"), "acme-widget", "without a publisher the part is whole")

    def test_the_long_form(self):
        self.assertEqual(keyrule.long_form("acme/widget", "Acme Widget"), "acme/acme-widget")
        self.assertIsNone(keyrule.long_form("acme/widget", "Widget"))
        self.assertIsNone(keyrule.long_form("acme/acme", "Acme"))

    def test_key_form(self):
        self.assertTrue(keyrule.key_problems("Synology/DS224+"))
        self.assertTrue(keyrule.key_problems("synology"))
        self.assertTrue(keyrule.key_problems("synology/ds224--plus"))


if __name__ == "__main__":
    unittest.main()
