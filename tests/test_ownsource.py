"""A source of the publisher itself (TAX-20): tools/ownsource.py."""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import ownsource as os_  # noqa: E402
from fetch import Source  # noqa: E402
from ownsource import Candidate  # noqa: E402

TEXT = "x " * 150


class Stub:
    """A fetcher with fixed pages: url -> (status, text, footer, final_url)."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def fetch(self, url, product=""):
        self.asked.append(url)
        status, text, footer, final = self.pages.get(url, ("404", "", "", ""))
        return Source(url, status, text, "http", footer=footer, final_url=final)


PUB = {"key": "acme-labs", "name": "Acme Labs", "source": "https://endoflife.date/acme", "aliases": []}


class OwnersTest(unittest.TestCase):
    def test_a_dataset_is_no_own_source(self):
        self.assertTrue(os_.third_party("https://endoflife.date/acme"))
        self.assertTrue(os_.third_party(f"https://{'github.com'}/endoflife-date/x/blob/a.md"))
        self.assertFalse(os_.third_party("https://acme.example/"))

    def test_the_name_in_the_domain(self):
        self.assertTrue(os_.name_in("acme.example", PUB))        # the name without the word Labs
        self.assertTrue(os_.name_in("acmelabs.example", PUB))
        grafana = {"key": "grafana-labs", "name": "Grafana Labs", "aliases": []}
        self.assertTrue(os_.name_in("grafana.example", grafana), "the name begins with the label")
        self.assertTrue(os_.name_in("grafanaserver.example", grafana), "the label begins with the name")
        apache = {"key": "the-apache-software-foundation", "name": "The Apache Software Foundation", "aliases": []}
        self.assertFalse(os_.name_in("chef.example", apache), "a label inside the name is no beginning")
        self.assertFalse(os_.name_in("acme.pages.example", {"key": "pages", "name": "Pages", "aliases": []}),
                         "the owner in the host is the first label")
        self.assertFalse(os_.name_in("other.example", PUB))
        self.assertFalse(os_.name_in("ac.example", PUB), "a short label proves nothing")
        short = {"key": "abcd", "name": "Abcd", "aliases": []}
        self.assertTrue(os_.name_in("abcd.example", short))
        self.assertFalse(os_.name_in("abcdforwindows.example", short), "a short name only as the whole label")

    def test_the_reasons(self):
        cands = [Candidate("homebrew", "https://shop.example/"), Candidate("winget", "https://shop.example/a"),
                 Candidate("cna", "https://psirt.vendor.example/", vouch=("vendor.example",)),
                 Candidate("npm", "https://lone.example/"), Candidate("scoop", "https://acme.example/")]
        got = os_.trusted(PUB, cands, known={"known.example"})
        self.assertEqual(got, {"shop.example": "finders", "vendor.example": "cna", "acme.example": "name"})
        self.assertEqual(os_.trusted(PUB, [Candidate("npm", "https://known.example/")], {"known.example"}),
                         {"known.example": "known"})

    def test_a_domain_of_another_publisher_not_by_name(self):
        c = [Candidate("release-policy", "https://acmelabs-addon.example/")]
        self.assertEqual(os_.trusted(PUB, c, set()), {"acmelabs-addon.example": "name"})
        self.assertEqual(os_.trusted(PUB, c, set(), others={"acmelabs-addon.example"}), {})

    def test_a_cna_vouches_only_for_its_mail_domain(self):
        c = Candidate("cna", "https://bugbounty.example/acme", vouch=("vendor.example",))
        self.assertEqual(os_.trusted(PUB, [c], set()), {})


class ChooseTest(unittest.TestCase):
    def test_the_break_another_domain_is_not_taken(self):
        """The deliberate break: one finder links to a page of someone else that does show the name."""
        cands = [Candidate("release-policy", "https://reseller.example/acme-labs")]
        stub = Stub({"https://reseller.example/acme-labs": ("200", TEXT + " Acme Labs", "", "")})
        res = os_.choose(PUB, cands, set(), stub)
        self.assertEqual((res.source, res.outcome), ("", "untrusted"))
        self.assertEqual(stub.asked, [], "a page on another domain is not even read")
        self.assertIn("not shown to be the publisher's", res.rejected[0][1])

    def test_found_with_the_name_in_the_footer(self):
        cands = [Candidate("homebrew", "https://acme.example/")]
        stub = Stub({"https://acme.example/": ("200", TEXT, "© 2026 Acme Labs", "")})
        res = os_.choose(PUB, cands, set(), stub)
        self.assertEqual((res.source, res.finder, res.reason), ("https://acme.example/", "homebrew", "name"))

    def test_the_name_must_stand_on_the_page(self):
        stub = Stub({"https://acme.example/": ("200", TEXT, "", "")})
        res = os_.choose(PUB, [Candidate("homebrew", "https://acme.example/")], set(), stub)
        self.assertEqual(res.outcome, "not-named")

    def test_a_refusal_is_respected_and_the_next_page_read(self):
        cands = [Candidate("cna", "https://acme.example/security", vouch=("acme.example",)),
                 Candidate("homebrew", "https://acme.example/")]
        stub = Stub({"https://acme.example/security": ("refused", "", "", ""),
                     "https://acme.example/": ("200", TEXT + " Acme Labs", "", "")})
        res = os_.choose(PUB, cands, set(), stub)
        self.assertEqual(res.source, "https://acme.example/")
        self.assertEqual(stub.asked.count("https://acme.example/security"), 1)

    def test_the_home_page_of_an_own_domain_after_the_pages_found(self):
        cands = [Candidate("release-policy", "https://wiki.acme.example/releases")]
        stub = Stub({"https://wiki.acme.example/releases": ("200", TEXT, "", ""),
                     "https://acme.example/": ("200", TEXT, "© Acme Labs", "")})
        res = os_.choose(PUB, cands, set(), stub)
        self.assertEqual((res.source, res.finder), ("https://acme.example/", "release-policy"))

    def test_only_https(self):
        stub = Stub({"https://acme.example/": ("200", TEXT + " Acme Labs", "", "")})
        res = os_.choose(PUB, [Candidate("github", "http://acme.example/")], set(), stub)
        self.assertEqual(res.source, "https://acme.example/")

    def test_all_closed(self):
        stub = Stub({"https://acme.example/": ("disallowed", "", "", "")})
        res = os_.choose(PUB, [Candidate("homebrew", "https://acme.example/")], set(), stub)
        self.assertEqual(res.outcome, "closed")

    def test_a_redirect_to_another_owner_is_not_followed(self):
        stub = Stub({"https://acme.example/": ("200", TEXT + " Acme Labs", "", "https://parking.example/")})
        res = os_.choose(PUB, [Candidate("homebrew", "https://acme.example/")], set(), stub)
        self.assertEqual(res.source, "")

    def test_an_own_domain_before_a_path_on_a_shared_site(self):
        shared = f"https://{'github.com'}/acme"
        cands = [Candidate("github", shared), Candidate("homebrew", "https://acme.example/")]
        stub = Stub({shared: ("200", TEXT + " Acme Labs", "", ""), "https://acme.example/": ("200", TEXT + " Acme Labs", "", "")})
        self.assertEqual(os_.choose(PUB, cands, set(), stub).source, "https://acme.example/")


class FindersTest(unittest.TestCase):
    def test_purl_parts(self):
        self.assertEqual(os_.purl_parts("pkg:npm/%40scope/name@1.0?x=y"), ("npm", "@scope/name"))
        self.assertEqual(os_.purl_parts("pkg:github/org/repo"), ("github", "org/repo"))
        self.assertEqual(os_.purl_parts("pkg:brew/tool@2"), ("brew", "tool"))

    def test_cna_matched_by_exact_name(self):
        cnas = [{"shortName": "acme", "organizationName": "Acme Labs, Inc.",
                 "contact": [{"email": [{"emailAddr": "psirt@acme.example"}], "contact": []}],
                 "securityAdvisories": {"advisories": [{"url": "https://acme.example/security"}], "alerts": []}},
                {"shortName": "acmee", "organizationName": "Acmee Labs",
                 "securityAdvisories": {"advisories": [{"url": "https://acmee.example/"}]}},
                {"shortName": "acme-labs", "organizationName": "Acme Labs Technologies, Inc.",
                 "securityAdvisories": {"advisories": [{"url": "https://acmetech.example/"}]}}]
        got = os_.cna_candidates(PUB, cnas)
        self.assertEqual([(c.url, c.vouch) for c in got], [("https://acme.example/security", ("acme.example",))])

    def test_winget_newest_manifest(self):
        lookup = os_.Lookup(get=lambda url: "PublisherUrl: https://acme.example/\n" if "/1.10/" in url else None,
                            gh=lambda path: '[{"name": "1.9", "type": "dir"}, {"name": "1.10", "type": "dir"}]',
                            sleep=lambda s: None)
        self.assertEqual(os_.winget_publisher_url(lookup, "Acme.Tool"), "https://acme.example/")

    def test_release_policy_from_the_dataset_entry(self):
        eol = {"https://endoflife.date/tool": {"links": {"releasePolicy": "https://acme.example/policy"}}}
        product = {"key": "acme-labs/tool", "source": "https://endoflife.date/tool", "versions": {}}
        got = os_.product_candidates(product, eol, {}, os_.Lookup(get=lambda u: None, gh=lambda p: None))
        self.assertEqual([(c.finder, c.url) for c in got], [("release-policy", "https://acme.example/policy")])


if __name__ == "__main__":
    unittest.main()
