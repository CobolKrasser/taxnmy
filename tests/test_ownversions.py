"""Sources of the publisher for a product (TAX-21): tools/ownversions.py."""

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import ownversions as ov  # noqa: E402
from test_ownsource import TEXT, Stub  # noqa: E402

PUB = {"key": "acme", "name": "Acme", "source": "https://www.acme.example/about"}
PRODUCT = {"key": "acme/tool", "name": "Acme Tool", "source": "https://endoflife.date/tool",
           "versions": {"source": "https://endoflife.date/tool"}}
EOL = {"https://endoflife.date/tool": {"links": {"releasePolicy": "https://docs.acme.example/policy"}}}


class JudgeTest(unittest.TestCase):
    def test_both_when_the_name_stands_on_the_page(self):
        stub = Stub({"https://docs.acme.example/policy": ("200", TEXT + " Acme Tool 2.1", "", "")})
        res = ov.judge(PRODUCT, PUB, ov.policy_link(PRODUCT, EOL), stub)
        self.assertEqual((res.outcome, res.source, res.versions), ("both", "https://docs.acme.example/policy",
                                                                    "https://docs.acme.example/policy"))

    def test_only_the_versions_without_the_name(self):
        stub = Stub({"https://docs.acme.example/policy": ("200", TEXT, "© Acme Tool", "")})
        res = ov.judge(PRODUCT, PUB, "https://docs.acme.example/policy", stub)
        self.assertEqual((res.outcome, res.source), ("versions", ""), "the footer proves no product name")

    def test_the_break_a_link_to_another_domain(self):
        stub = Stub({})
        res = ov.judge(PRODUCT, PUB, "https://acme-fans.example/policy", stub)
        self.assertEqual(res.outcome, "other-domain")
        self.assertEqual(stub.asked, [], "a page on another domain is not even read")

    def test_a_redirect_to_another_domain(self):
        stub = Stub({"https://docs.acme.example/policy": ("200", TEXT + " Acme Tool", "", "https://parked.example/")})
        self.assertEqual(ov.judge(PRODUCT, PUB, "https://docs.acme.example/policy", stub).outcome, "other-domain")

    def test_closed_keeps_the_source(self):
        stub = Stub({"https://docs.acme.example/policy": ("refused", "", "", "")})
        res = ov.judge(PRODUCT, PUB, "https://docs.acme.example/policy", stub)
        self.assertEqual((res.outcome, res.versions), ("closed", ""))

    def test_a_publisher_without_an_own_source(self):
        pub = {**PUB, "source": "https://endoflife.date/acme"}
        self.assertEqual(ov.judge(PRODUCT, pub, "https://docs.acme.example/policy", Stub({})).outcome,
                         "publisher-not-own")

    def test_no_link(self):
        self.assertEqual(ov.judge(PRODUCT, PUB, ov.policy_link(PRODUCT, {}), Stub({})).outcome, "no-link")

    def test_http_is_read_as_https(self):
        stub = Stub({"https://docs.acme.example/policy": ("200", TEXT + " Acme Tool", "", "")})
        self.assertEqual(ov.judge(PRODUCT, PUB, "http://docs.acme.example/policy", stub).versions,
                         "https://docs.acme.example/policy")


class WriteTest(unittest.TestCase):
    def test_only_the_two_sources_change(self):
        text = ("key: acme/tool\npublisher: acme\nname: Acme Tool\nkind: software\nsource: https://endoflife.date/tool\n"
                "versions:\n  source: https://endoflife.date/tool\nidentifiers:\n- cpe: cpe:2.3:a:acme:tool\n"
                "  source: https://endoflife.date/tool\naliases: []\n")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "tool.yaml"
            f.write_text(text)
            ov.set_sources(f, "https://acme.example/a", "https://acme.example/b")
            self.assertEqual(f.read_text(), text.replace("source: https://endoflife.date/tool\nversions:\n  source: https://endoflife.date/tool",
                                                         "source: https://acme.example/a\nversions:\n  source: https://acme.example/b"))


if __name__ == "__main__":
    unittest.main()
