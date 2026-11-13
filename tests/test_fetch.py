"""The ladder of tools/fetch.py, with a fake network, a fake browser and a fake clock."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import fetch  # noqa: E402
from fetch import Response  # noqa: E402

LONG = "Firmware 7.2.1 is available for this model. " * 10  # well over 200 characters


def html(body: str) -> bytes:
    return f"<html><head><title>T</title></head><body>{body}</body></html>".encode()


class Net:
    """URL -> Response, or a list of Responses for successive requests. robots.txt is a 404
    (no rules) unless given."""

    def __init__(self, pages):
        self.pages, self.requests = dict(pages), []

    def get(self, url, timeout):
        self.requests.append(url)
        page = self.pages.get(url)
        if page is None:
            return Response(status=404, final_url=url)
        if isinstance(page, list):
            return page.pop(0) if len(page) > 1 else page[0]
        return page


class Browser:
    def __init__(self, pages=None):
        self.pages, self.rendered = pages or {}, []

    def render(self, url):
        self.rendered.append(url)
        return self.pages.get(url, (200, ""))


class Clock:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


def ok(body, ctype="text/html", url=""):
    return Response(status=200, ctype=ctype, body=body, final_url=url)


def fetcher(net, browser=None, clock=None):
    clock = clock or Clock()
    return fetch.Fetcher(get=net.get, browser=browser, use_browser=False, sleep=clock.sleep, clock=clock.time)


class LadderTest(unittest.TestCase):
    def test_http_reads_a_page(self):
        s = fetcher(Net({"https://a.example/p": ok(html(LONG))})).fetch("https://a.example/p")
        self.assertEqual((s.status, s.method), ("200", "http"))
        self.assertEqual(s.chars, len(s.text))

    def test_nvd_search_page_becomes_the_api(self):
        api = "https://services.nvd.nist.gov/rest/json/cpes/2.0?cpeMatchString=cpe:2.3:h:fortinet:fortigate_60f&resultsPerPage=1"
        net = Net({api: ok(('{"products": "' + LONG + '"}').encode(), "application/json")})
        s = fetcher(net).fetch("https://nvd.nist.gov/products/cpe/search/results?namingFormat=2.3&keyword=cpe:2.3:h:fortinet:fortigate_60f")
        self.assertEqual((s.status, s.method, s.fetched_url), ("200", "api", api))
        self.assertNotIn("https://nvd.nist.gov/products/cpe/search/results?namingFormat=2.3&keyword=cpe:2.3:h:fortinet:fortigate_60f", net.requests)

    def test_api_url_in_the_data_is_method_api(self):
        api = "https://services.nvd.nist.gov/rest/json/cpes/2.0?cpeMatchString=cpe:2.3:a:oracle:graalvm&resultsPerPage=1"
        s = fetcher(Net({api: ok(LONG.encode(), "application/json")})).fetch(api)
        self.assertEqual((s.method, s.fetched_url), ("api", ""))

    def test_one_frame_is_followed(self):
        net = Net({
            "https://a.example/doc.html": ok(html('<iframe src="../inner/eng.html"></iframe>'), url="https://a.example/x/doc.html"),
            "https://a.example/inner/eng.html": ok(html(LONG)),
        })
        s = fetcher(net).fetch("https://a.example/doc.html")
        self.assertEqual((s.status, s.method, s.fetched_url), ("200", "http", "https://a.example/inner/eng.html"))
        self.assertIn("Firmware 7.2.1", s.text)

    def test_two_frames_are_not_followed(self):
        net = Net({"https://a.example/d": ok(html('<iframe src="/a"></iframe><iframe src="/b"></iframe>'))})
        self.assertEqual(fetcher(net).fetch("https://a.example/d").status, "unreadable")

    def test_browser_when_http_gives_too_little(self):
        browser = Browser({"https://a.example/js": (200, LONG)})
        s = fetcher(Net({"https://a.example/js": ok(html("Loading"))}), browser).fetch("https://a.example/js")
        self.assertEqual((s.status, s.method), ("200", "browser"))
        self.assertEqual(browser.rendered, ["https://a.example/js"])

    def test_no_browser_when_http_is_enough(self):
        browser = Browser()
        fetcher(Net({"https://a.example/p": ok(html(LONG))}), browser).fetch("https://a.example/p")
        self.assertEqual(browser.rendered, [])

    def test_no_browser_for_a_pdf_or_json(self):
        browser = Browser()
        f = fetcher(Net({"https://a.example/x.pdf": ok(b"%PDF-broken", "application/pdf")}), browser)
        self.assertEqual(f.fetch("https://a.example/x.pdf").status, "unreadable")
        self.assertEqual(browser.rendered, [])

    def test_browser_that_finds_nothing_leaves_it_unreadable(self):
        s = fetcher(Net({"https://a.example/js": ok(html("Loading"))}), Browser()).fetch("https://a.example/js")
        self.assertEqual((s.status, s.method), ("unreadable", "http"))

    def test_refused_stops_the_ladder(self):
        for code in (401, 403):
            browser = Browser({"https://a.example/p": (200, LONG)})
            s = fetcher(Net({"https://a.example/p": Response(status=code)}), browser).fetch("https://a.example/p")
            self.assertEqual(s.status, "refused")
            self.assertEqual(browser.rendered, [], "a refusal is not tried again with a browser")

    def test_browser_refused(self):
        browser = Browser({"https://a.example/js": (403, "")})
        s = fetcher(Net({"https://a.example/js": ok(html("Loading"))}), browser).fetch("https://a.example/js")
        self.assertEqual((s.status, s.method), ("refused", "browser"))

    def test_429_is_tried_once_more_after_retry_after(self):
        clock = Clock()
        net = Net({"https://a.example/p": [Response(status=429, retry_after="7"), ok(html(LONG))]})
        s = fetcher(net, clock=clock).fetch("https://a.example/p")
        self.assertEqual(s.status, "200")
        self.assertIn(7.0, clock.slept)
        self.assertEqual(net.requests.count("https://a.example/p"), 2)

    def test_429_twice_is_refused(self):
        net = Net({"https://a.example/p": [Response(status=429), Response(status=429)]})
        s = fetcher(net).fetch("https://a.example/p")
        self.assertEqual(s.status, "refused")
        self.assertEqual(net.requests.count("https://a.example/p"), 2, "only one retry")

    def test_retry_after(self):
        now = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(fetch.retry_after("12"), 12.0)
        self.assertEqual(fetch.retry_after(None), fetch.RETRY_AFTER_DEFAULT)
        self.assertEqual(fetch.retry_after("Fri, 13 Nov 2026 12:00:30 GMT", now), 30.0)
        self.assertEqual(fetch.retry_after("3600"), fetch.RETRY_AFTER_MAX)
        self.assertEqual(fetch.retry_after("soon"), fetch.RETRY_AFTER_DEFAULT)

    def test_404_and_errors(self):
        f = fetcher(Net({"https://a.example/e": Response(status=0, error="error: TimeoutError")}))
        self.assertEqual(f.fetch("https://a.example/gone").status, "404")
        self.assertEqual(f.fetch("https://a.example/e").status, "error: TimeoutError")

    def test_robots_txt_is_respected(self):
        net = Net({
            "https://a.example/robots.txt": ok(b"User-agent: *\nDisallow: /private/\n", "text/plain"),
            "https://a.example/public": ok(html(LONG)),
        })
        f = fetcher(net, Browser({"https://a.example/private/p": (200, LONG)}))
        self.assertEqual(f.fetch("https://a.example/private/p").status, "disallowed")
        self.assertNotIn("https://a.example/private/p", net.requests)
        self.assertEqual(f.fetch("https://a.example/public").status, "200")
        self.assertEqual(net.requests.count("https://a.example/robots.txt"), 1, "robots.txt is read once per site")

    def test_robots_txt_for_our_agent(self):
        net = Net({"https://a.example/robots.txt": ok(b"User-agent: TAXNMY-referee\nDisallow: /\n", "text/plain")})
        self.assertEqual(fetcher(net).fetch("https://a.example/p").status, "disallowed")

    def test_robots_txt_that_fails_closes_the_site(self):
        net = Net({"https://a.example/robots.txt": Response(status=503), "https://a.example/p": ok(html(LONG))})
        self.assertEqual(fetcher(net).fetch("https://a.example/p").status, "disallowed")

    def test_one_request_per_second_per_site(self):
        clock = Clock()
        net = Net({"https://a.example/1": ok(html(LONG)), "https://a.example/2": ok(html(LONG)),
                   "https://b.example/1": ok(html(LONG))})
        f = fetcher(net, clock=clock)
        f.fetch("https://a.example/1")  # robots.txt, then the page: one wait of 1 s
        f.fetch("https://b.example/1")  # another site: robots.txt and the page, one wait
        f.fetch("https://a.example/2")  # a.example again: the last request was over 1 s ago
        self.assertEqual(clock.slept, [1.0, 1.0])

    def test_each_url_once(self):
        net = Net({"https://a.example/p": ok(html(LONG))})
        f = fetcher(net)
        f.fetch("https://a.example/p")
        f.fetch("https://a.example/p")
        self.assertEqual(net.requests.count("https://a.example/p"), 1)

    def test_honest_user_agent(self):
        self.assertTrue(fetch.USER_AGENT.startswith("TAXNMY-referee/"))
        self.assertIn("github.com/CobolKrasser/taxnmy", fetch.USER_AGENT)
        source = (REPO / "tools/fetch.py").read_text()
        for trick in ("Mozilla/", "stealth", "proxy"):
            self.assertNotIn(trick, source.replace("no stealth", "").replace("No imitated browser headers, no proxies, no stealth", ""))

    def test_stored_fetcher(self):
        f = fetch.StoredFetcher({"https://a.example/p": {"url": "https://a.example/p", "status": "200", "text": "x",
                                                          "method": "browser", "fetched_url": "", "chars": 1}})
        self.assertEqual(f.fetch("https://a.example/p").method, "browser")
        self.assertEqual(f.fetch("https://a.example/q").status, "not fetched")

    def test_single_frame(self):
        self.assertEqual(fetch.single_frame('<frameset><frame src="m.html"></frameset>', "https://a.example/d/x.html"),
                         "https://a.example/d/m.html")
        self.assertEqual(fetch.single_frame('<iframe id="x"></iframe>', "https://a.example/"), "")
        self.assertEqual(fetch.single_frame('<iframe src="about:blank"></iframe>', "https://a.example/"), "")


@unittest.skipUnless(fetch.PlaywrightBrowser.available() and __import__("os").environ.get("TAXNMY_BROWSER_TEST"),
                     "set TAXNMY_BROWSER_TEST=1 with Playwright and Chromium installed")
class RealBrowserTest(unittest.TestCase):
    """Renders a local page with JavaScript in a real headless Chromium. No network."""

    def test_renders_javascript(self):
        import http.server
        import threading

        page = b"<html><body><div id=t></div><script>document.getElementById('t').textContent = 'Rendered ' + 'by script. '.repeat(30)</script></body></html>"

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        browser = fetch.PlaywrightBrowser()
        self.addCleanup(browser.close)
        status, text = browser.render(f"http://127.0.0.1:{server.server_port}/")
        self.assertEqual(status, 200)
        self.assertIn("Rendered by script.", text)


if __name__ == "__main__":
    unittest.main()
