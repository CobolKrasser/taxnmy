"""TAX-34 (K4, K5): only public addresses for the reader and the lint; names as plain text.

The reader is tested with real urllib against local servers on 127.0.0.1; a name that "resolves" is
a fake getaddrinfo, so no test leaves this machine.
"""
import http.server
import shutil
import socket
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import address  # noqa: E402
import fetch  # noqa: E402
import lint  # noqa: E402
import pr_referee  # noqa: E402

LONG = "Firmware 7.2.1 is available for this model. " * 10

PUBLISHER = """\
key: synology
name: Synology Inc.
source: https://www.synology.com/en-global/company
aliases: []
"""


def product(source="https://www.synology.com/en-global/products/DS224+", name="DS224+", aliases="[]"):
    return f"""\
key: synology/ds224-plus
publisher: synology
name: {name}
kind: device
source: {source}
versions:
  source: https://www.synology.com/en-global/releaseNote/DSM
identifiers: []
aliases: {aliases}
"""


# --------------------------------------------------------------------------- local servers

class Server:
    """A local HTTP server that counts its requests and answers each path as given."""

    def __init__(self, routes):
        self.hits = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                server.hits.append(self.path)
                status, headers, body = routes.get(self.path, (404, {}, b""))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeDNS:
    """getaddrinfo for chosen names; 127.0.0.1 stands in for a public address, nothing else does."""

    def __init__(self, names):
        self.names = names  # host -> ip

    def getaddrinfo(self, host, port, *a, **k):
        if host not in self.names:
            raise socket.gaierror(f"no such name {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (self.names[host], port))]

    def patch(self, test):
        real = address.is_public
        for p in (mock.patch.object(address.socket, "getaddrinfo", self.getaddrinfo),
                  mock.patch.object(address, "is_public", lambda ip: ip == "127.0.0.1" or real(ip))):
            p.start()
            test.addCleanup(p.stop)


# --------------------------------------------------------------------------- the check without network

class ProblemTest(unittest.TestCase):
    def test_public_names_pass(self):
        for url in ("https://www.synology.com/en-global/company", "https://nvd.nist.gov/x?y=1",
                    "https://a.example/p", "http://downloads.example.org:8080/catalog.xml"):
            with self.subTest(url=url):
                self.assertEqual(address.problem(url), "")

    def test_ip_addresses_are_refused(self):
        for url in ("https://127.0.0.1/", "https://10.0.0.8/admin", "https://93.184.215.14/", "https://[::1]/",
                    "https://[fd00::1]:8443/", "https://2130706433/", "https://0x7f.1/", "https://169.254.169.254/latest"):
            with self.subTest(url=url):
                self.assertIn("IP address", address.problem(url))

    def test_localhost_and_internal_names_are_refused(self):
        for url in ("https://localhost/", "https://LOCALHOST./x", "https://api.localhost/", "https://nas.local/",
                    "https://wiki.internal/", "https://router.home.arpa/", "https://intranet/", "https://db.corp/"):
            with self.subTest(url=url):
                self.assertIn("public address", address.problem(url))

    def test_other_schemes_and_credentials_are_refused(self):
        self.assertIn("scheme", address.problem("file:///etc/passwd"))
        self.assertIn("scheme", address.problem("ftp://ftp.example.org/"))
        self.assertIn("user name", address.problem("https://user:pw@www.example.org/"))

    def test_public_ip_ranges(self):
        self.assertTrue(address.is_public("93.184.215.14"))
        for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                   "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1", "2002:7f00:1::1"):
            with self.subTest(ip=ip):
                self.assertFalse(address.is_public(ip))


# --------------------------------------------------------------------------- the lint (TR1)

class LintAddressTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.write("publishers/synology.yaml", PUBLISHER)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")

    def errors(self):
        return [f for f in lint.lint(self.root, REPO / "schema") if f.level == "error"]

    def assertRefused(self, source, contains):
        self.write("products/synology/ds224-plus.yaml", product(source=source))
        found = self.errors()
        self.assertEqual([(f.file, f.line, f.rule) for f in found], [("products/synology/ds224-plus.yaml", 5, "TR1")], found)
        self.assertIn(contains, found[0].message)

    def test_a_good_product_passes(self):
        self.write("products/synology/ds224-plus.yaml", product())
        self.assertEqual(self.errors(), [])

    def test_an_internal_name_is_refused(self):
        self.assertRefused("https://wiki.internal/ds224", "is not a public address")

    def test_an_ip_address_is_refused(self):
        self.assertRefused("https://192.168.1.20/ds224", "IP address")

    def test_localhost_is_refused(self):
        self.assertRefused("https://localhost/ds224", "is not a public address")

    def test_every_url_field_is_checked(self):
        self.write("publishers/synology.yaml", PUBLISHER + textwrap.dedent("""\
            second_source: https://10.0.0.1/
            machine_sources:
            - url: https://updates.local/catalog.xml
              format: update-catalog
              source: https://www.synology.com/en-global/company
            """))
        found = self.errors()
        self.assertEqual(sorted((f.line, f.rule) for f in found), [(5, "TR1"), (7, "TR1")], found)

    def test_http_is_refused_by_the_schema(self):
        self.assertRefused("http://www.synology.com/en-global/products/DS224+", "does not match")

    def test_a_long_name_is_refused(self):
        self.write("products/synology/ds224-plus.yaml", product(name="D" * 101))
        self.assertIn("is too long", " ".join(f.message for f in self.errors() if f.rule == "TR1"))

    def test_a_name_with_html_or_a_control_character_is_refused(self):
        for name in ('"DS224+ <img src=x onerror=alert(1)>"', '"DS224+\\u0007"', '"DS224+\\u202Etxt"'):
            with self.subTest(name=name):
                self.write("products/synology/ds224-plus.yaml", product(name=name))
                self.assertIn("does not match", " ".join(f.message for f in self.errors() if f.rule == "TR1"))

    def test_an_alias_with_html_is_refused(self):
        self.write("products/synology/ds224-plus.yaml", product(aliases='["synology/<b>x</b>"]'))
        self.assertTrue(any(f.rule == "TR1" and "does not match" in f.message for f in self.errors()))


# --------------------------------------------------------------------------- the reader

class ReaderTest(unittest.TestCase):
    """Real urllib, local servers. The internal server must never see a request."""

    def setUp(self):
        self.inside = Server({"/": (200, {"Content-Type": "text/html"}, f"<p>{LONG}</p>".encode())})
        self.addCleanup(self.inside.close)
        p = self.inside.port
        self.public = Server({
            "/to-ip": (302, {"Location": f"http://127.0.0.1:{p}/"}, b""),
            "/to-localhost": (302, {"Location": f"http://localhost:{p}/"}, b""),
            "/to-internal-name": (302, {"Location": f"http://wiki.internal:{p}/"}, b""),
            "/to-name-inside": (302, {"Location": f"http://inside.example:{p}/"}, b""),
            "/page": (200, {"Content-Type": "text/html"}, f"<p>{LONG}</p>".encode()),
        })
        self.addCleanup(self.public.close)
        # www.public.example stands for a public site; inside.example resolves to a private address.
        FakeDNS({"www.public.example": "127.0.0.1", "inside.example": "10.0.0.7"}).patch(self)

    def get(self, url):
        return fetch._urlopen_get(url, 5)

    def test_the_setup_reads_a_public_page(self):
        resp = self.get(f"http://www.public.example:{self.public.port}/page")
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.public.hits, ["/page"])

    def test_an_ip_address_is_not_requested(self):
        self.assertEqual(self.get(f"http://127.0.0.1:{self.inside.port}/").error, fetch.BLOCKED)
        self.assertEqual(self.inside.hits, [])

    def test_localhost_is_not_requested(self):
        self.assertEqual(self.get(f"http://localhost:{self.inside.port}/").error, fetch.BLOCKED)
        self.assertEqual(self.inside.hits, [])

    def test_an_internal_name_is_not_requested(self):
        self.assertEqual(self.get(f"http://wiki.internal:{self.inside.port}/").error, fetch.BLOCKED)
        self.assertEqual(self.get(f"http://inside.example:{self.inside.port}/").error, fetch.BLOCKED)
        self.assertEqual(self.inside.hits, [])

    def test_a_redirect_to_an_internal_address_is_not_followed(self):
        for path in ("/to-ip", "/to-localhost", "/to-internal-name", "/to-name-inside"):
            with self.subTest(path=path):
                resp = self.get(f"http://www.public.example:{self.public.port}{path}")
                self.assertEqual((resp.status, resp.error), (0, fetch.BLOCKED))
        self.assertEqual(self.inside.hits, [])
        self.assertEqual(len(self.public.hits), 4)

    def test_a_redirect_is_checked_before_it_is_followed(self):
        """The first of two checks; the connection checks again (test above)."""
        import urllib.request
        handler = address._CheckedRedirect()
        req = urllib.request.Request("https://www.public.example/")
        for target in ("http://127.0.0.1/", "https://localhost/", "https://wiki.internal/", "file:///etc/passwd"):
            with self.subTest(target=target), self.assertRaises(address.Blocked):
                handler.redirect_request(req, None, 302, "Found", {}, target)
        self.assertIsNotNone(handler.redirect_request(req, None, 302, "Found", {}, "https://www.public.example/b"))

    def test_the_ladder_says_blocked_and_reads_no_robots_txt(self):
        f = fetch.Fetcher(use_browser=False, sleep=lambda s: None)
        for url in (f"http://127.0.0.1:{self.inside.port}/", f"http://localhost:{self.inside.port}/",
                    f"http://inside.example:{self.inside.port}/", f"http://www.public.example:{self.public.port}/to-ip"):
            with self.subTest(url=url):
                self.assertEqual(f.fetch(url).status, fetch.BLOCKED)
        self.assertEqual(self.inside.hits, [])

    def test_the_importer_reads_through_the_same_check(self):
        import importer
        rec = importer.Recorder()
        self.assertEqual(rec(f"http://www.public.example:{self.public.port}/to-ip", 5).error, fetch.BLOCKED)
        self.assertEqual(self.inside.hits, [])


class LadderFrameTest(unittest.TestCase):
    def test_a_frame_on_an_internal_address_is_not_followed(self):
        requests = []

        def get(url, timeout):
            requests.append(url)
            if url == "https://a.example/doc":
                return fetch.Response(200, "text/html", body=b'<html><body><iframe src="https://10.0.0.1/x"></iframe></body></html>',
                                      final_url=url)
            return fetch.Response(404, final_url=url)

        s = fetch.Fetcher(get=get, use_browser=False, sleep=lambda s: None).fetch("https://a.example/doc")
        self.assertEqual(s.status, "unreadable")
        self.assertNotIn("https://10.0.0.1/x", requests)
        self.assertFalse(any("10.0.0.1" in r for r in requests), requests)


class BrowserRouteTest(unittest.TestCase):
    """Every request of the browser goes through the same check (Playwright's route)."""

    class Route:
        def __init__(self, url):
            self.request = type("R", (), {"url": url})()
            self.done = ""

        def continue_(self):
            self.done = "continue"

        def abort(self, reason=""):
            self.done = "abort"

    def route(self, url, public_only=True):
        r = self.Route(url)
        fetch.PlaywrightBrowser(public_only=public_only)._route(r)
        return r.done

    def test_internal_requests_are_aborted(self):
        FakeDNS({"www.public.example": "127.0.0.1", "inside.example": "10.0.0.7"}).patch(self)
        self.assertEqual(self.route("https://www.public.example/app.js"), "continue")
        for url in ("http://127.0.0.1:8080/", "https://localhost/", "https://nas.local/", "https://inside.example/"):
            with self.subTest(url=url):
                self.assertEqual(self.route(url), "abort")
        self.assertEqual(self.route("data:text/plain,x"), "continue")

    def test_a_local_test_page_may_be_opened_when_asked(self):
        self.assertEqual(self.route("http://127.0.0.1:8080/", public_only=False), "continue")


# --------------------------------------------------------------------------- the comment (K5)

class CommentTest(unittest.TestCase):
    NAME = "**Bold** <img src=x onerror=alert(1)> [link](https://evil.example) @someone `tick` | pipe"

    def item(self):
        return {"id": "products/acme/widget.yaml", "outcome": "reject", "rule": "TR1", "by": "code",
                "reason": f"name {self.NAME!r} is not the name on the source", "waits_for": None}

    def test_a_name_with_markup_or_a_mention_shows_as_written(self):
        text = pr_referee.comment([self.item()], 7)
        row = next(line for line in text.splitlines() if line.startswith("| `products/acme/widget.yaml`"))
        cell = row.split(" | ")[-1].rstrip(" |")
        # A code span longer than any run of backticks inside: GitHub shows it as it is written.
        self.assertTrue(cell.startswith("``") and cell.endswith("``"), cell)
        inner = cell.strip("`").strip().replace("\\|", "|")
        self.assertEqual(inner, f"name {self.NAME!r} is not the name on the source")
        # Outside the code span nothing of the name is left.
        self.assertNotIn("@someone", row.replace(cell, ""))
        self.assertEqual(row.count(" | "), 4)  # the | of the name does not make a new column

    def test_plain(self):
        self.assertEqual(pr_referee.plain(""), "-")
        self.assertEqual(pr_referee.plain("a\nb\x07c"), "`a bc`")
        self.assertEqual(pr_referee.plain("`x`"), "`` `x` ``")
        self.assertEqual(pr_referee.plain("a|b"), "`a\\|b`")
        self.assertEqual(pr_referee.plain("a|b", cell=False), "`a|b`")

    def test_a_lapsed_entry_shows_its_key_and_reason_as_written(self):
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out)
        terms = pr_referee.load_terms()
        entry = {"pr": 9, "key": "acme/<b>w</b>", "waits_for": "event", "since": "2026-11-01", "reason": "@someone **x**"}
        text = pr_referee.lapsed_comments([entry], terms, out)[0].read_text()
        self.assertIn("- `acme/<b>w</b>` waited", text)
        self.assertIn("Last reason: `@someone **x**`", text)


if __name__ == "__main__":
    unittest.main()
