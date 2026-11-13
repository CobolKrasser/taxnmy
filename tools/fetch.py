"""Reading the sources of a proposal: the ladder of referee/README.md § Reading sources.

For each URL the fetcher goes down the steps until it has usable text:

0. machine  a source made for machines (an update catalog, AppStream metadata, CSAF, the NVD
            API): one reader per format takes out the part about the product (tools/machine.py);
1. api      a known API instead of a page (an NVD search page becomes the NVD API);
2. http     a plain HTTP request; JSON and text are read as text, the text of a PDF is
            extracted, and a page with one frame or iframe is followed;
3. browser  a headless Chromium (Playwright), when the steps before give fewer than 200
            characters of HTML text: the page is rendered and its visible text taken.

A refusal stops the ladder: 401 and 403 give "refused"; 429 is tried once more after the
Retry-After time, and gives "refused" if it comes again. A URL that robots.txt forbids gives
"disallowed" and is not requested.

Conduct (tax-fase2-pagina-s-lezen.md §2): an honest User-Agent with a link to the repository;
robots.txt is respected; at most one request per second per site; each URL once per run (the
cache). No imitated browser headers, no proxies, no stealth.

No network access happens at import. Costs nothing.
"""

from __future__ import annotations

import email.utils
import html.parser
import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone

import machine

USER_AGENT = "TAXNMY-referee/1.0 (+https://github.com/CobolKrasser/taxnmy)"
ROBOTS_AGENT = "TAXNMY-referee"
MIN_CHARS = 200  # less text than this is no source: a title alone
SOURCE_CHARS = 24_000  # about 6,000 tokens per source text (referee/prompt.md)
MAX_BYTES = 30_000_000  # an update catalog is a few MB, gzipped
PER_HOST_INTERVAL = 1.0  # seconds between two requests to the same site
RETRY_AFTER_DEFAULT = 5.0  # seconds, when a 429 does not say how long to wait
RETRY_AFTER_MAX = 60.0
API_RETRY_WAIT = 5.0  # seconds before the one retry of an API that timed out (NVD limits requests without a key)
BROWSER_TIMEOUT_MS = 15_000  # wait at most this long for the network to be quiet
REFUSED = {401, 403}


@dataclass
class Source:
    url: str  # the URL as the proposal gives it
    status: str  # an HTTP status, "unreadable", "refused", "disallowed" or "error: ..."
    text: str = ""
    method: str = ""  # the step that gave the text: machine, api, http, browser or search
    fetched_url: str = ""  # what was requested in the end, when it differs from url
    chars: int = 0  # characters of text before the cut to SOURCE_CHARS
    product: str = ""  # step 0: the product whose part was taken out
    instead_of: str = ""  # step 4: the refused URL this source was found for


def source_key(url: str, product: str = "") -> str:
    """The key in sources.json: a source for machines depends on the product, a page does not."""
    return f"{url} | {product}" if product else url


# --------------------------------------------------------------------------- text from a response

class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "svg", "template", "iframe"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.out = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth and data.strip():
            self.out.append(data.strip())


def html_to_text(page: str) -> str:
    parser = _Text()
    parser.feed(page)
    return re.sub(r"\s+", " ", " ".join(parser.out)).strip()


class _Frames(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.srcs = []

    def handle_starttag(self, tag, attrs):
        if tag in ("frame", "iframe"):
            src = dict(attrs).get("src")
            if src and not src.startswith(("about:", "javascript:", "data:")):
                self.srcs.append(src)


def single_frame(page: str, base: str) -> str:
    """The URL of the one frame or iframe of a page, or '' when there is none or more than one."""
    parser = _Frames()
    parser.feed(page)
    return urllib.parse.urljoin(base, parser.srcs[0]) if len(parser.srcs) == 1 else ""


def pdf_to_text(body: bytes) -> str:
    try:
        import pypdf
    except ImportError:  # pypdf is in requirements-referee.txt
        return ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        return re.sub(r"\s+", " ", " ".join((page.extract_text() or "") for page in reader.pages)).strip()
    except Exception:  # noqa: BLE001 - a PDF that cannot be read confirms nothing
        return ""


def page_text(body: bytes, ctype: str, charset: str) -> str:
    """The readable text of a response: HTML without navigation and scripts, JSON and plain text
    as they are, the text of a PDF. Anything else gives no text."""
    if ctype == "text/html":
        return html_to_text(body.decode(charset, errors="replace"))
    if ctype.startswith("text/") or ctype == "application/json" or ctype.endswith("+json"):
        return re.sub(r"\s+", " ", body.decode(charset, errors="replace")).strip()
    if ctype == "application/pdf":
        return pdf_to_text(body)
    return ""


# --------------------------------------------------------------------------- step 1: known APIs

NVD_SEARCH = re.compile(r"^https://nvd\.nist\.gov/products/cpe/search/results\?(.*)$")
API_HOSTS = {"services.nvd.nist.gov"}


def api_url(url: str) -> str:
    """The API URL for a page that has one, or ''."""
    m = NVD_SEARCH.match(url)
    if m:
        keyword = urllib.parse.parse_qs(m.group(1)).get("keyword", [""])[0]
        if keyword.startswith("cpe:2.3:"):
            return ("https://services.nvd.nist.gov/rest/json/cpes/2.0?cpeMatchString="
                    + urllib.parse.quote(keyword, safe=":*") + "&resultsPerPage=1")
    return ""


def retry_after(value: str | None, now: datetime | None = None) -> float:
    """Seconds to wait from a Retry-After header: a number or an HTTP date."""
    if not value:
        return RETRY_AFTER_DEFAULT
    value = value.strip()
    if value.isdigit():
        seconds = float(value)
    else:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return RETRY_AFTER_DEFAULT
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    return min(max(seconds, 0.0), RETRY_AFTER_MAX)


# --------------------------------------------------------------------------- the fetcher

@dataclass
class Response:
    status: int  # 0 when there was no answer
    ctype: str = ""
    charset: str = "utf-8"
    body: bytes = b""
    final_url: str = ""
    retry_after: str | None = None
    error: str = ""


def _urlopen_get(url: str, timeout: float) -> Response:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URLs of the test set
            return Response(status=resp.status, ctype=resp.headers.get_content_type(),
                            charset=resp.headers.get_content_charset() or "utf-8",
                            body=resp.read(MAX_BYTES), final_url=resp.geturl())
    except urllib.error.HTTPError as exc:
        headers = exc.headers
        exc.close()
        return Response(status=exc.code, final_url=url, retry_after=headers.get("Retry-After") if headers else None)
    except Exception as exc:  # noqa: BLE001 - any failure means the source confirms nothing
        return Response(status=0, final_url=url, error=f"error: {type(exc).__name__}")


class PlaywrightBrowser:
    """Headless Chromium through Playwright, started on first use. Honest User-Agent, no stealth."""

    def __init__(self):
        self._pw = self._browser = None

    @staticmethod
    def available() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False
        return True

    def render(self, url: str) -> tuple[int, str]:
        """(HTTP status of the page, its visible text, including frames)."""
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        if self._browser is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
        context = self._browser.new_context(user_agent=USER_AGENT)
        try:
            page = context.new_page()
            try:
                response = page.goto(url, wait_until="networkidle", timeout=BROWSER_TIMEOUT_MS)
            except PlaywrightTimeout:
                response = None  # the network did not get quiet; take what has rendered
            status = response.status if response else 200
            texts = []
            for frame in page.frames:
                try:
                    texts.append(frame.evaluate("() => document.body ? document.body.innerText : ''") or "")
                except PlaywrightError:
                    pass
            return status, re.sub(r"\s+", " ", " ".join(texts)).strip()
        finally:
            context.close()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._pw.stop()
            self._browser = self._pw = None


class Fetcher:
    """The ladder. Each URL is fetched at most once; the result is kept in `cache`."""

    def __init__(self, *, browser=None, use_browser: bool = True, timeout: float = 20.0,
                 get=None, sleep=time.sleep, clock=time.monotonic, log=None):
        self.timeout = timeout
        self._get = get or _urlopen_get
        self._sleep, self._clock = sleep, clock
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.browser = browser if browser is not None else (PlaywrightBrowser() if use_browser and PlaywrightBrowser.available() else None)
        self.cache: dict[str, Source] = {}
        self._raw: dict[str, tuple] = {}  # url -> (response, fetched_url) of a source for machines
        self.log = log or (lambda *a: None)

    # conduct

    def _wait_for(self, url: str) -> None:
        """At most one request per second per site."""
        host = urllib.parse.urlsplit(url).netloc.lower()
        last = self._last.get(host)
        if last is not None:
            wait = PER_HOST_INTERVAL - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last[host] = self._clock()

    def _request(self, url: str) -> Response:
        self._wait_for(url)
        return self._get(url, self.timeout)

    def allowed(self, url: str) -> bool:
        """robots.txt, read once per site. Following RFC 9309: a 4xx means no rules; a 5xx or no
        answer means the whole site is off limits."""
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            resp = self._request(origin + "/robots.txt")
            if resp.status == 200:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(resp.body.decode(resp.charset, errors="replace").splitlines())
                self._robots[origin] = rp
            elif 400 <= resp.status < 500:
                self._robots[origin] = None  # no rules
            else:
                rp = urllib.robotparser.RobotFileParser()
                rp.disallow_all = True
                self._robots[origin] = rp
        rp = self._robots[origin]
        return rp is None or rp.can_fetch(ROBOTS_AGENT, url)

    # the ladder

    def fetch(self, url: str, product: str = "") -> Source:
        """The source at url; for a source made for machines, the part about `product`."""
        if source_key(url, product) in self.cache:
            return self.cache[source_key(url, product)]
        if url in self.cache:
            return self.cache[url]
        if url in self._raw:
            src = self._machine(url, product, *self._raw[url])
        else:
            src = self._ladder(url, product)
        self.cache[source_key(url, product) if src.method == "machine" else url] = src
        self.log(f"{src.status:>10}  {src.method or '-':7}  {src.chars:>6}  {url}" + (f"  ({product})" if src.method == "machine" else ""))
        return src

    def _machine(self, url: str, product: str, resp: "Response", fetched: str) -> Source:
        text = machine.read(resp.body, resp.ctype, product) or ""
        return Source(url, str(resp.status), text[:SOURCE_CHARS], "machine", fetched, len(text), product=product)

    def _ladder(self, url: str, product: str = "") -> Source:
        target = api_url(url) or url
        method = "api" if target != url or urllib.parse.urlsplit(target).netloc in API_HOSTS else "http"
        fetched = target if target != url else ""
        if not self.allowed(target):
            return Source(url, "disallowed", method=method, fetched_url=fetched)

        resp = self._request(target)
        if resp.error.endswith("TimeoutError") and method == "api":
            self._sleep(API_RETRY_WAIT)
            resp = self._request(target)
        if resp.status == 429:
            self._sleep(retry_after(resp.retry_after))
            resp = self._request(target)
            if resp.status == 429:
                return Source(url, "refused", method=method, fetched_url=fetched)
        if resp.status in REFUSED:
            return Source(url, "refused", method=method, fetched_url=fetched)
        if resp.error:
            return Source(url, resp.error, method=method, fetched_url=fetched)
        if resp.status >= 400:
            return Source(url, str(resp.status), method=method, fetched_url=fetched)

        status = str(resp.status)
        if product:
            found = machine.read(resp.body, resp.ctype, product)
            if found is not None:
                self._raw[url] = (resp, fetched)
                return Source(url, status, found[:SOURCE_CHARS], "machine", fetched, len(found), product=product)
        text = page_text(resp.body, resp.ctype, resp.charset)
        is_html = resp.ctype == "text/html"

        if is_html and len(text) < MIN_CHARS:
            frame = single_frame(resp.body.decode(resp.charset, errors="replace"), resp.final_url or target)
            if frame and self.allowed(frame):
                fr = self._request(frame)
                if fr.status == 200:
                    frame_text = page_text(fr.body, fr.ctype, fr.charset)
                    if len(frame_text) > len(text):
                        text, fetched = f"{text} {frame_text}".strip(), frame

        if is_html and len(text) < MIN_CHARS and self.browser is not None:
            self._wait_for(target)
            try:
                b_status, b_text = self.browser.render(target)
            except Exception as exc:  # noqa: BLE001 - a page the browser cannot open confirms nothing
                self.log(f"browser failed on {target}: {type(exc).__name__}")
            else:
                if b_status in REFUSED or b_status == 429:
                    return Source(url, "refused", method="browser", fetched_url=fetched)
                if len(b_text) >= MIN_CHARS:
                    return Source(url, str(b_status), b_text[:SOURCE_CHARS], "browser", fetched, len(b_text))

        if len(text) < MIN_CHARS:
            return Source(url, "unreadable", method=method, fetched_url=fetched, chars=len(text))
        return Source(url, status, text[:SOURCE_CHARS], method, fetched, len(text))

    def close(self) -> None:
        if self.browser is not None and hasattr(self.browser, "close"):
            self.browser.close()


class StoredFetcher:
    """The sources fetched earlier, by another job: a sources.json of `Fetcher.cache`."""

    def __init__(self, data: dict):
        self.cache = {key: Source(**src) for key, src in data.items()}

    def fetch(self, url: str, product: str = "") -> Source:
        return self.cache.get(source_key(url, product)) or self.cache.get(url) or Source(url, "not fetched")

    def alternatives(self, url: str) -> list[Source]:
        """Step 4: the sources found for a refused URL."""
        return [s for s in self.cache.values() if s.instead_of == url]

    def close(self) -> None:
        pass
