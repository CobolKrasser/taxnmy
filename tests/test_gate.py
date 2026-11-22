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
        decided = gate.decide(product(), PUBLISHER, pages(versions="Firmware 1.0"), new_publisher=False)
        self.assertEqual(decided["TP5"]["verdict"], "yes")

    def test_s5_left_to_the_ai_on_another_domain(self):
        p = product(versions={"source": "https://releases.other.example/widget-2"})
        self.assertNotIn("TP5", gate.decide(p, PUBLISHER, [page(p["versions"]["source"], "Firmware 1.0")], new_publisher=False))

    def test_s5_only_when_the_versions_source_was_read(self):
        """TAX-24: a closed versions source shows nothing, wherever it is (G06)."""
        url = product()["versions"]["source"]
        for sources in (None, [], [page(url, "", "refused")], [page(url, "", "disallowed")], [page(url, "Not found", "404")],
                        [Source(url, "200", "Firmware 1.0", instead_of="https://x.example/")]):
            self.assertNotIn("TP5", gate.decide(product(), PUBLISHER, sources, new_publisher=False), sources)

    def test_s5_never_for_a_publisher_proposed_with_the_product(self):
        """TAX-24: a wrong publisher with a site of its own and the versions on that same site
        passes the domain comparison. Both URLs come from the proposal, so code proves nothing."""
        self.assertNotIn("TP5", gate.decide(product(), PUBLISHER, pages(versions="Firmware 1.0")))
        notes = gate.partly(product(), PUBLISHER, pages(versions="Firmware 1.0"), {})
        self.assertIn("not who publishes them", notes["TP5"])

    def test_s5_on_a_shared_host_needs_the_same_owner(self):
        pub = {**PUBLISHER, "source": "https://github.com/acme"}

        def tr5(versions, publisher=pub):
            p = product(versions={"source": versions})
            return gate.tr5(p, publisher, {versions: page(versions, "v1.0")}, False)

        self.assertEqual(tr5("https://github.com/acme/widget/releases")["verdict"], "yes")
        self.assertIsNone(tr5("https://github.com/someone-else/widget/releases"))
        self.assertIsNone(tr5("https://other.github.io/widget", {**PUBLISHER, "source": "https://acme.github.io/"}))

    def test_s5_never_on_a_site_of_many_publishers(self):
        for pub, versions in (("https://endoflife.date/widget", "https://endoflife.date/widget"),
                              ("https://community.chocolatey.org/", "https://community.chocolatey.org/packages/widget")):
            p = product(versions={"source": versions})
            self.assertIsNone(gate.tr5(p, {**PUBLISHER, "source": pub}, {versions: page(versions, "v1.0")}, False))

    def test_registrable(self):
        self.assertEqual(gate.registrable("support.d-imaging.sony.co.jp"), "sony.co.jp")
        self.assertEqual(gate.registrable("www.example.com:8443"), "example.com")

    def test_s6_yes_when_both_names_appear(self):
        decided = gate.decide(product(), PUBLISHER, pages(product="The  WIDGET 2 by", publisher="About acme corp. here"))
        self.assertEqual(decided["TK2"]["verdict"], "yes")
        self.assertEqual(decided["TK2"]["evidence"][0], {"url": "https://www.acme.example/widget-2", "quote": "Widget 2"})

    def test_s6_left_to_the_ai(self):
        self.assertNotIn("TK2", gate.decide(product(), PUBLISHER, pages(product="Widget 2", publisher="About us")),
                         "the publisher name is missing")
        self.assertNotIn("TK2", gate.decide(product(), PUBLISHER, pages(product="Widget 20 by Acme Corp.")),
                         "a longer name is another name")
        long_name = "Widget 2 with an extended warranty and a carrying case"
        self.assertNotIn("TK2", gate.decide(product(name=long_name), PUBLISHER, pages(product=f"{long_name} Acme Corp.")),
                         "over 40 characters: maybe a description")
        self.assertNotIn("TK2", gate.decide(product(), PUBLISHER, None), "no texts")

    def test_literal_values_found_in_their_own_source(self):
        cpe = "cpe:2.3:h:acme:widget_2"
        p = product(identifiers=[{"cpe": cpe, "source": "https://nvd.example/cpe"}])
        srcs = pages(product="Widget 2", versions="Firmware 1.0") + [page("https://nvd.example/cpe", f"{cpe}:1.0:*:*")]
        self.assertEqual([e["quote"] for e in gate.literal(p, gate.own_sources(srcs))["evidence"]], ["Widget 2", cpe])

    def test_s8_is_never_yes_by_code_because_of_kind(self):
        """TAX-24: every literal value found does not make TE1 yes: `kind` is a value too, and no
        code reads it off. TE1 goes to the AI, with a note of what code found."""
        srcs = pages(product="Widget 2", versions="Firmware 1.0")
        for kind in ("device", "software", "operating-system"):
            p = product(kind=kind)
            decided = gate.decide(p, PUBLISHER, srcs, new_publisher=False)
            self.assertNotIn("TE1", decided)
            self.assertIn("`kind`", gate.partly(p, PUBLISHER, srcs, decided, new_publisher=False)["TE1"])

    def test_literal_values_not_found(self):
        def found(p, srcs):
            return gate.literal(p, gate.own_sources(srcs)) is not None

        p = product(identifiers=[{"cpe": "cpe:2.3:h:acme:widget", "source": "https://nvd.example/cpe"}])
        longer = pages(product="Widget 2", versions="x") + [page("https://nvd.example/cpe", "cpe:2.3:h:acme:widget_2")]
        self.assertFalse(found(p, longer), "a longer code is another code")
        elsewhere = pages(product="Widget 2 cpe:2.3:h:acme:widget", versions="x") + [page("https://nvd.example/cpe", "nothing")]
        self.assertFalse(found(p, elsewhere), "a value counts only in its own source")
        gtin = product(identifiers=[{"gtin": "0812345678904", "source": "https://www.acme.example/widget-2"}])
        self.assertFalse(found(gtin, pages(product="Widget 2 EAN 08123456789041", versions="x")))
        self.assertTrue(found(gtin, pages(product="Widget 2 EAN 0812345678904.", versions="x")))
        self.assertFalse(found(product(), pages(product="Widget 2")), "the versions source was not read")
        refused = [page(product()["source"], "Widget 2"), page(product()["versions"]["source"], "", "refused")]
        self.assertFalse(found(product(), refused))
        found_instead = [page(product()["source"], "Widget 2"),
                         Source(product()["versions"]["source"], "200", "Widget 2", instead_of="https://x.example/")]
        self.assertFalse(found(product(), found_instead), "a source found instead of another is not its own")
        self.assertNotIn("TE1", gate.partly(product(), PUBLISHER, refused, {}), "no note when code found nothing")

    def test_an_ai_chat_is_never_a_source(self):
        for url in ("https://chatgpt.com/share/abc", "https://claude.ai/share/abc", "https://www.perplexity.ai/search/abc"):
            p = product(identifiers=[{"gtin": "0812345678904", "source": url}])
            self.assertEqual(gate.decide(p, PUBLISHER, None), {"TE1": gate.decide(p, PUBLISHER, None)["TE1"]})
            self.assertEqual(gate.decide(p, PUBLISHER, None)["TE1"]["verdict"], "no")
        self.assertEqual(gate.decide(product(), {**PUBLISHER, "source": "https://chat.openai.com/c/1"}, None)["TE1"]["verdict"], "no")
        self.assertNotIn("TE1", gate.decide(product(source="https://notchatgpt.com/widget"), PUBLISHER, None))

    def test_the_rules_name_no_product_of_the_data(self):
        """TG4: the gate and the guard hold classes of sites (AI chats, hosting, registries), never a product."""
        text = "".join((REPO / f"tools/{m}.py").read_text(encoding="utf-8") for m in ("gate", "guard")).casefold()
        keys = [f"{p.parent.name}/{p.stem}" for p in (REPO / "referee/testset/data/products").glob("*/*.yaml")]  # fixed (TAX-56)
        self.assertGreater(len(keys), 400)
        self.assertEqual([k for k in keys if k in text], [])


if __name__ == "__main__":
    unittest.main()
