#!/usr/bin/env python3
"""The gate: rules TR5, TR6 and TR8, decided by code where the answer is in the source, before the AI.

The lint (tools/lint.py) checks TR1-TR3. The gate takes the next step: for a proposal that passed the
lint it settles what can be read off literally, and leaves the rest to the AI referee
(tax-analyse-fable-2026-11-14.md §4, story TAX-15). Code only says what it can prove; everything
else stays with the AI, never a guess.

| rule | code says yes | code says no | otherwise |
| --- | --- | --- | --- |
| TR5 | the publisher is already in TAX, `versions.source` was read, and its registrable domain is that of the publisher's `source` | - | AI |
| TR6 | the product name and the publisher name, each at most 40 characters, appear (capitals and whitespace aside) in a source text that was read | - | AI |
| TR8 | never: `kind` is a value no code can read off | a source is on the domain of an AI chat | AI |

No rule is left untested because code settled another one (TAX-24). Where code checked part of a
rule that stays with the AI, `partly` says what it found, and the AI gets that as a note:

- TR8: the literal values are the product name, in the text of the product's `source`, and each
  code (cpe, purl, gtin, winget, homebrew), in the text of its own `source`. Code finds them, but
  `kind` is a classification, not a literal value, so TR8 itself stays with the AI.
- TR5: for a publisher proposed together with the product, both URLs come from the proposal. That
  they share a domain shows where the versions are, not who publishes them: a wrong publisher
  with a site of its own and the versions on that same site passes the comparison. Code says yes
  only for a publisher whose `source` TAX already accepted.

The rules name no brand or product (TG4). The domain lists below are classes of sites, not
exceptions for one product: chat sites of AI models, which are never a source, and hosting sites
on which one domain carries many owners, where a shared domain proves nothing about who publishes.
"""

from __future__ import annotations

import re
import unicodedata

from fetch import Source
from guard import MANY_OWNERS, OWNER_IN_HOST, OWNER_IN_PATH, host_of, on_domain, owner, registrable  # noqa: F401

GATE_RULES = ("TR5", "TR6", "TR8")
MAX_NAME = 40  # TR6: a longer name may be a description, so the AI judges it

# README "What the lint does not check yet", proposal 1: an AI chat is never a source (TR8).
AI_CHAT_DOMAINS = (
    "chatgpt.com", "chat.openai.com", "claude.ai", "gemini.google.com", "bard.google.com", "perplexity.ai",
    "copilot.microsoft.com", "chat.deepseek.com", "chat.mistral.ai", "grok.com", "poe.com", "you.com",
)

CODES = ("cpe", "purl", "gtin", "winget", "homebrew")

# Domains and owners (registrable, owner and the lists of sites with many owners) are in tools/guard.py,
# which the lint shares (TV3). The lists there are classes of sites, not products.


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
    """TR8 no when a source is on the domain of an AI chat. Needs no source text, so it runs before
    anything is fetched."""
    for url in sources_of(product, publisher):
        if on_domain(url, AI_CHAT_DOMAINS):
            return judgment("no", f"{url} is an AI chat, which is never a source")
    return None


def same_owner(product: dict, publisher: dict) -> str | None:
    """The owner of both `versions.source` and the publisher's `source`, or None."""
    versions = (product.get("versions") or {}).get("source")
    pub_source = publisher.get("source")
    if not isinstance(versions, str) or not isinstance(pub_source, str):
        return None
    a, b = owner(versions), owner(pub_source)
    return a if a and a == b else None


def tr5(product: dict, publisher: dict, own: dict, new_publisher: bool) -> dict | None:
    """Yes only for a publisher TAX already holds, and a versions source that was read."""
    domain = same_owner(product, publisher)
    if domain is None or new_publisher or not read(own.get((product.get("versions") or {}).get("source"))):
        return None
    return judgment("yes", f"the versions are published on {domain}, the publisher's own domain, and were read")


def tr6(product: dict, publisher: dict, texts: dict) -> dict | None:
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


def literal(product: dict, sources: dict) -> dict | None:
    """The part of TR8 code can check: every literal value in the text of its own source, and every
    source read. Not a judgment on TR8: `kind` is not among the values."""
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
    return {"evidence": evidence}


def own_sources(sources: list[Source] | None) -> dict:
    """url -> the source of that URL itself, not what was found instead of it."""
    own = {}
    for s in sources or []:
        if not s.instead_of and s.url not in own:
            own[s.url] = s
    return own


def decide(product: dict, publisher: dict, sources: list[Source] | None, new_publisher: bool = True) -> dict:
    """rule -> judgment for the rules code settles; the rules it leaves out go to the AI.
    `sources` are the fetched sources of the case; None when no texts are at hand (TR5, TR6 and TR8
    then stay with the AI, except an AI chat). `new_publisher`: the publisher file is part of the
    proposal, so its `source` is as unchecked as the rest."""
    chat = ai_chat(product, publisher)
    if chat:
        return {"TR8": chat}
    if sources is None:
        return {}
    own = own_sources(sources)
    texts = {u: s.text for u, s in own.items() if read(s)}
    out = {}
    for rule, j in (("TR5", tr5(product, publisher, own, new_publisher)), ("TR6", tr6(product, publisher, texts))):
        if j:
            out[rule] = j
    return out


def partly(product: dict, publisher: dict, sources: list[Source] | None, decided: dict,
           new_publisher: bool = True) -> dict:
    """rule -> what code found of a rule it leaves to the AI: a note for the AI, never a verdict."""
    out = {}
    if "TR8" in decided:
        return out
    if sources is not None and literal(product, own_sources(sources)):
        out["TR8"] = ("code found the name and every code in the text of its own source; `kind` is not a "
                     "literal value and no code checked it")
    domain = same_owner(product, publisher)
    if "TR5" not in decided and domain:
        out["TR5"] = (f"the versions source and the publisher's source are both on {domain}; "
                     + ("both URLs come from this proposal, so that shows where the versions are, not who publishes them"
                        if new_publisher else "the versions source was not read"))
    return out
