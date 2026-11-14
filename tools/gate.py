#!/usr/bin/env python3
"""The gate: rules S5, S6 and S8, decided by code where the answer is in the source, before the AI.

The lint (tools/lint.py) checks S1-S3. The gate takes the next step: for a proposal that passed the
lint it settles what can be read off literally, and leaves the rest to the AI referee
(tax-analyse-fable-2026-11-14.md §4, story TAX-15). Code only says what it can prove; everything
else stays with the AI, never a guess.

| rule | code says yes | code says no | otherwise |
| --- | --- | --- | --- |
| S5 | the registrable domain of `versions.source` is that of the publisher's `source` | - | AI |
| S6 | the product name and the publisher name, each at most 40 characters, appear (capitals and whitespace aside) in a source text that was read | - | AI |
| S8 | every literal value appears in the text of its own URL, and every source of the product was read | a source is on the domain of an AI chat | AI |

The literal values for S8 are the product name, in the text of the product's `source`, and each
code (cpe, purl, gtin, winget, homebrew), in the text of its own `source`. `kind` is a
classification, not a literal value; the source of `versions` has no value but itself. Both must
be read, but are not searched for.

The rules name no brand or product (S9, R3). The domain lists below are classes of sites, not
exceptions for one product: chat sites of AI models, which are never a source, and hosting sites
on which one domain carries many owners, where a shared domain proves nothing about who publishes.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.parse

from fetch import Source

GATE_RULES = ("S5", "S6", "S8")
MAX_NAME = 40  # S6: a longer name may be a description, so the AI judges it

# README "What the lint does not check yet", proposal 1: an AI chat is never a source (S8).
AI_CHAT_DOMAINS = (
    "chatgpt.com", "chat.openai.com", "claude.ai", "gemini.google.com", "bard.google.com", "perplexity.ai",
    "copilot.microsoft.com", "chat.deepseek.com", "chat.mistral.ai", "grok.com", "poe.com", "you.com",
)

# Sites where one registrable domain carries the pages of many owners. On these a shared domain
# says nothing about who publishes, so S5 stays with the AI unless the owner is the same too.
OWNER_IN_PATH = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")  # owner: the first path segment
OWNER_IN_HOST = ("github.io", "gitlab.io", "readthedocs.io", "netlify.app", "vercel.app", "pages.dev",
                 "sourceforge.io", "blogspot.com", "wordpress.com")  # owner: the label in front
MANY_OWNERS = (  # never the publisher's own site:
    # catalogues and encyclopaedias of products
    "endoflife.date", "wikipedia.org", "wikidata.org", "nist.gov",
    # package registries and app stores, which carry the packages of many publishers
    "npmjs.com", "pypi.org", "packagist.org", "nuget.org", "crates.io", "rubygems.org", "docker.com", "docker.io",
    "chocolatey.org", "brew.sh", "snapcraft.io", "flathub.org", "sourceforge.net", "launchpad.net",
    # sites where anyone writes
    "medium.com", "reddit.com",
)

CODES = ("cpe", "purl", "gtin", "winget", "homebrew")


def registrable(host: str) -> str:
    """example.com from www.example.com; example.co.jp from support.example.co.jp."""
    labels = host.lower().split(":")[0].strip(".").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in {"co", "com", "org", "net", "ac", "gov", "edu", "ne", "or"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or ""


def on_domain(url: str, domains) -> bool:
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in domains)


def owner(url: str) -> str | None:
    """Who a URL belongs to, as far as its address shows; None when the address cannot show it."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    domain = registrable(host)
    if domain in MANY_OWNERS:
        return None
    if domain in OWNER_IN_PATH:
        segment = parts.path.strip("/").split("/")[0].lower()
        return f"{domain}/{segment}" if segment else None
    if domain in OWNER_IN_HOST:
        labels = host.split(".")
        return ".".join(labels[-3:]) if len(labels) >= 3 else None
    return domain or None


def normalise(text: str) -> str:
    """Capitals and whitespace aside (and the Unicode forms of one character)."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()


def appears(value: str, text: str, after: str = r"\w") -> bool:
    """The value stands in the text as a whole: no letter or digit glued to its front or its back.
    `after` is what may not follow it."""
    v = normalise(value)
    if not v:
        return False
    return re.search(r"(?<!\w)" + re.escape(v) + r"(?!" + after + r")", normalise(text)) is not None


# What may not follow a code in a text, so that a longer code is not taken for it. A CPE may be
# followed by its version fields (a colon); the others may not run on.
CODE_TAIL = {"cpe": r"[\w.\-*]", "purl": r"[\w./\-+%@]", "gtin": r"\d", "winget": r"[\w.\-+]", "homebrew": r"[\w@.\-+]"}


def read(src: Source | None) -> bool:
    return src is not None and src.status.isdigit() and int(src.status) < 300 and bool(src.text.strip())


def sources_of(product: dict, publisher: dict) -> list[str]:
    """Every source URL of the product, its versions, its codes and its publisher."""
    urls = [product.get("source"), (product.get("versions") or {}).get("source")]
    urls += [i.get("source") for i in product.get("identifiers") or [] if isinstance(i, dict)]
    urls.append(publisher.get("source"))
    return [u for u in urls if isinstance(u, str)]


def judgment(verdict: str, reason: str, evidence: list | None = None) -> dict:
    return {"verdict": verdict, "reason": f"code: {reason}", "evidence": evidence or []}


def ai_chat(product: dict, publisher: dict) -> dict | None:
    """S8 no when a source is on the domain of an AI chat. Needs no source text, so it runs before
    anything is fetched."""
    for url in sources_of(product, publisher):
        if on_domain(url, AI_CHAT_DOMAINS):
            return judgment("no", f"{url} is an AI chat, which is never a source")
    return None


def s5(product: dict, publisher: dict) -> dict | None:
    versions = (product.get("versions") or {}).get("source")
    pub_source = publisher.get("source")
    if not isinstance(versions, str) or not isinstance(pub_source, str):
        return None
    a, b = owner(versions), owner(pub_source)
    if a and a == b:
        return judgment("yes", f"the versions are published on {a}, the publisher's own domain")
    return None


def s6(product: dict, publisher: dict, texts: dict) -> dict | None:
    name, pub_name = product.get("name"), publisher.get("name")
    if not isinstance(name, str) or not isinstance(pub_name, str):
        return None
    if len(name) > MAX_NAME or len(pub_name) > MAX_NAME:
        return None
    found = {}
    for value in (name, pub_name):
        url = next((u for u, t in texts.items() if appears(value, t)), None)
        if url is None:
            return None
        found[value] = url
    return judgment("yes", "the product name and the publisher name appear as proposed",
                    [{"url": url, "quote": value} for value, url in found.items()])


def s8(product: dict, sources: dict) -> dict | None:
    needed = [u for u in sources_of(product, {}) if u]
    if not needed or not all(read(sources.get(u)) for u in needed):
        return None
    values = [(product.get("name"), product.get("source"), r"\w")]
    for ident in product.get("identifiers") or []:
        if not isinstance(ident, dict):
            return None
        code = next((c for c in CODES if c in ident), None)
        if code is None:
            return None
        values.append((ident[code], ident.get("source"), CODE_TAIL[code]))
    evidence = []
    for value, url, tail in values:
        if not isinstance(value, str) or not isinstance(url, str) or not appears(value, sources[url].text, tail):
            return None
        evidence.append({"url": url, "quote": value})
    return judgment("yes", "every value appears in the text of its own source", evidence)


def decide(product: dict, publisher: dict, sources: list[Source] | None) -> dict:
    """rule -> judgment for the rules code settles; the rules it leaves out go to the AI.
    `sources` are the fetched sources of the case; None when no texts are at hand (S6 and S8
    then stay with the AI, except an AI chat)."""
    out = {}
    chat = ai_chat(product, publisher)
    if chat:
        return {"S8": chat}
    j = s5(product, publisher)
    if j:
        out["S5"] = j
    if sources is not None:
        own = {}
        for s in sources:  # the source of each URL itself, not what was found instead of it
            if not s.instead_of and s.url not in own:
                own[s.url] = s
        texts = {u: s.text for u, s in own.items() if read(s)}
        for rule, j in (("S6", s6(product, publisher, texts)), ("S8", s8(product, own))):
            if j:
                out[rule] = j
    return out
