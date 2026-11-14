import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import gate  # noqa: E402
from fetch import Source  # noqa: E402

PUBLISHER = {"key": "acme", "name": "Acme Corp.", "source": "https://www.acme.example/about"}


def product(**kw):
    data = {"key": "acme/widget-2", "publisher": "acme", "name": "Widget 2", "kind": "device",
            "source": "https://www.acme.example/widget-2", "versions": {"source": "https://support.acme.example/widget-2/firmware"},
            "identifiers": []}
    data.update(kw)
    return data


def page(url, text, status="200"):
    return Source(url, status, text)


def pages(**texts):
    p = product()
    urls = {"product": p["source"], "versions": p["versions"]["source"], "publisher": PUBLISHER["source"]}
    return [page(urls[k], v) for k, v in texts.items()]


class GateTest(unittest.TestCase):
    def test_s5_yes_on_the_publishers_own_domain(self):
        self.assertEqual(gate.decide(product(), PUBLISHER, None)["S5"]["verdict"], "yes")

    def test_s5_left_to_the_ai_on_another_domain(self):
        p = product(versions={"source": "https://releases.other.example/widget-2"})
        self.assertNotIn("S5", gate.decide(p, PUBLISHER, None))

    def test_s5_on_a_shared_host_needs_the_same_owner(self):
        pub = {**PUBLISHER, "source": "https://github.com/acme"}
        self.assertEqual(gate.s5(product(versions={"source": "https://github.com/acme/widget/releases"}), pub)["verdict"], "yes")
        self.assertIsNone(gate.s5(product(versions={"source": "https://github.com/someone-else/widget/releases"}), pub))
        self.assertIsNone(gate.s5(product(versions={"source": "https://other.github.io/widget"}), {**PUBLISHER, "source": "https://acme.github.io/"}))

    def test_s5_never_on_a_site_of_many_publishers(self):
        pub = {**PUBLISHER, "source": "https://endoflife.date/widget"}
        self.assertIsNone(gate.s5(product(versions={"source": "https://endoflife.date/widget"}), pub))
        pub = {**PUBLISHER, "source": "https://community.chocolatey.org/"}
        self.assertIsNone(gate.s5(product(versions={"source": "https://community.chocolatey.org/packages/widget"}), pub))

    def test_registrable(self):
        self.assertEqual(gate.registrable("support.d-imaging.sony.co.jp"), "sony.co.jp")
        self.assertEqual(gate.registrable("www.example.com:8443"), "example.com")

    def test_s6_yes_when_both_names_appear(self):
        decided = gate.decide(product(), PUBLISHER, pages(product="The  WIDGET 2 by", publisher="About acme corp. here"))
        self.assertEqual(decided["S6"]["verdict"], "yes")
        self.assertEqual(decided["S6"]["evidence"][0], {"url": "https://www.acme.example/widget-2", "quote": "Widget 2"})

    def test_s6_left_to_the_ai(self):
        self.assertNotIn("S6", gate.decide(product(), PUBLISHER, pages(product="Widget 2", publisher="About us")),
                         "the publisher name is missing")
        self.assertNotIn("S6", gate.decide(product(), PUBLISHER, pages(product="Widget 20 by Acme Corp.")),
                         "a longer name is another name")
        long_name = "Widget 2 with an extended warranty and a carrying case"
        self.assertNotIn("S6", gate.decide(product(name=long_name), PUBLISHER, pages(product=f"{long_name} Acme Corp.")),
                         "over 40 characters: maybe a description")
        self.assertNotIn("S6", gate.decide(product(), PUBLISHER, None), "no texts")

    def test_s8_yes_when_every_value_is_in_its_own_source(self):
        cpe = "cpe:2.3:h:acme:widget_2"
        p = product(identifiers=[{"cpe": cpe, "source": "https://nvd.example/cpe"}])
        srcs = pages(product="Widget 2", versions="Firmware 1.0") + [page("https://nvd.example/cpe", f"{cpe}:1.0:*:*")]
        decided = gate.decide(p, PUBLISHER, srcs)
        self.assertEqual(decided["S8"]["verdict"], "yes")
        self.assertEqual([e["quote"] for e in decided["S8"]["evidence"]], ["Widget 2", cpe])

    def test_s8_left_to_the_ai(self):
        p = product(identifiers=[{"cpe": "cpe:2.3:h:acme:widget", "source": "https://nvd.example/cpe"}])
        longer = pages(product="Widget 2", versions="x") + [page("https://nvd.example/cpe", "cpe:2.3:h:acme:widget_2")]
        self.assertNotIn("S8", gate.decide(p, PUBLISHER, longer), "a longer code is another code")
        elsewhere = pages(product="Widget 2 cpe:2.3:h:acme:widget", versions="x") + [page("https://nvd.example/cpe", "nothing")]
        self.assertNotIn("S8", gate.decide(p, PUBLISHER, elsewhere), "a value counts only in its own source")
        gtin = product(identifiers=[{"gtin": "0812345678904", "source": "https://www.acme.example/widget-2"}])
        self.assertNotIn("S8", gate.decide(gtin, PUBLISHER, pages(product="Widget 2 EAN 08123456789041", versions="x")))
        self.assertEqual(gate.decide(gtin, PUBLISHER, pages(product="Widget 2 EAN 0812345678904.", versions="x"))["S8"]["verdict"], "yes")
        self.assertNotIn("S8", gate.decide(product(), PUBLISHER, pages(product="Widget 2")), "the versions source was not read")
        refused = [page(product()["source"], "Widget 2"), page(product()["versions"]["source"], "", "refused")]
        self.assertNotIn("S8", gate.decide(product(), PUBLISHER, refused))
        found_instead = [page(product()["source"], "Widget 2"),
                         Source(product()["versions"]["source"], "200", "Widget 2", instead_of="https://x.example/")]
        self.assertNotIn("S8", gate.decide(product(), PUBLISHER, found_instead), "a source found instead of another is not its own")

    def test_an_ai_chat_is_never_a_source(self):
        for url in ("https://chatgpt.com/share/abc", "https://claude.ai/share/abc", "https://www.perplexity.ai/search/abc"):
            p = product(identifiers=[{"gtin": "0812345678904", "source": url}])
            self.assertEqual(gate.decide(p, PUBLISHER, None), {"S8": gate.decide(p, PUBLISHER, None)["S8"]})
            self.assertEqual(gate.decide(p, PUBLISHER, None)["S8"]["verdict"], "no")
        self.assertEqual(gate.decide(product(), {**PUBLISHER, "source": "https://chat.openai.com/c/1"}, None)["S8"]["verdict"], "no")
        self.assertNotIn("S8", gate.decide(product(source="https://notchatgpt.com/widget"), PUBLISHER, None))

    def test_the_rules_name_no_product_of_the_data(self):
        """S9, R3: the gate holds classes of sites (AI chats, hosting, registries), never a product."""
        text = (REPO / "tools/gate.py").read_text(encoding="utf-8").casefold()
        keys = [f"{p.parent.name}/{p.stem}" for p in (REPO / "products").glob("*/*.yaml")]
        self.assertGreater(len(keys), 400)
        self.assertEqual([k for k in keys if k in text], [])


if __name__ == "__main__":
    unittest.main()
