#!/usr/bin/env python3
"""A change that only adds to a file in TAXNMY is judged on what it adds (TV5, TAX-48).

A product or publisher that TAXNMY holds was judged when it was taken in. A change that adds to it
is judged on the addition, as far as code can show it (TR11):

| addition | what it must show | by |
| --- | --- | --- |
| an alias | the name is written at a source of the product | tools/alias.py (TAX-40) |
| an identifier | its own source was read and writes the code literally, on a page that names the product | code; otherwise the whole review |
| a source for machines | the page that names it is on the publisher's domain, was read, and writes its address | code (TE7) |
| a second source | it is off the publisher's domain, was read, and names that domain | code (TV4, tools/guard.py) |

When code cannot show an identifier, or the change does more than add, the whole product is
judged, as for any change: there is no way in without a check. An identifier that is already on
another product (signal TK4) is judged as a whole too: TP3 asks whether it is a product of its own.
A source for machines or a second source that code cannot show is rejected, or parked when its
page could not be read: a publisher file has no review by the AI.
"""

from __future__ import annotations

from collections import Counter

import yaml

import gate
import guard
from fetch import moved

KINDS = {"identifier": ("products/", "identifiers"), "machine": ("publishers/", "machine_sources"),
         "second": ("publishers/", "second_source")}


def _load(text: str):
    try:
        data = yaml.safe_load(text or "")
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _comments(text: str) -> Counter:
    return Counter(line.strip() for line in text.splitlines() if "#" in line)


def added(files: dict, old: dict) -> tuple[str, list] | None:
    """(kind, what is added) when the proposal is one change of a file in TAXNMY that only adds
    identifiers, sources for machines or a second source (one kind); None for any other proposal."""
    if len(files) != 1:
        return None
    (rel, text), = files.items()
    if rel not in old:
        return None
    before, after = _load(old[rel]), _load(text)
    if before is None or after is None or _comments(text) - _comments(old[rel]):
        return None
    for kind, (prefix, field) in KINDS.items():
        if not rel.startswith(prefix) or {k: v for k, v in before.items() if k != field} != \
                {k: v for k, v in after.items() if k != field}:
            continue
        was, now = before.get(field), after.get(field)
        if kind == "second":
            return (kind, [now]) if was is None and isinstance(now, str) else None
        was, now = was or [], now or []
        if not isinstance(was, list) or not isinstance(now, list) or any(e not in now for e in was):
            return None
        new = [e for e in now if e not in was]
        return (kind, new) if new and all(isinstance(e, dict) for e in new) else None
    return None


# --------------------------------------------------------------------------- an identifier

def code_of(entry: dict) -> tuple[str, str]:
    code = next((c for c in gate.CODES if c in entry), "")
    return code, str(entry.get(code, ""))


def shown(entry: dict, name: str, own: dict) -> str:
    """The URL of the identifier's own source when that source was read, ended where it was asked,
    and writes both the code and the name of the product; '' otherwise."""
    code, value = code_of(entry)
    url = entry.get("source")
    src = own.get(url) if isinstance(url, str) else None
    if not code or not gate.read(src) or moved(src):
        return ""
    if not gate.appears(value, src.text, gate.CODE_TAIL[code]):
        return ""
    rest = gate.normalise(src.text).replace(gate.normalise(value), " ")  # the name outside the code itself
    return url if gate.appears(name, rest) else ""


def identifiers(product: dict, entries: list, sources: list) -> list | None:
    """The evidence for every added identifier, or None when code cannot show one of them."""
    own = gate.own_sources(sources)
    name = product.get("name")
    if not isinstance(name, str):
        return None
    evidence = []
    for entry in entries:
        url = shown(entry, name, own)
        if not url:
            return None
        evidence.append({"url": url, "quote": code_of(entry)[1]})
    return evidence


# --------------------------------------------------------------------------- a source for machines

def _bare(url: str) -> str:
    return url.split("://", 1)[-1].rstrip("/").casefold()


def machine_fault(publisher: dict, before: dict | None, sources: list | None) -> tuple[str, str]:
    """TE7: ('', '') when every source for machines that the publisher file adds is named by a page
    of the publisher that was read; else (outcome, reason). A source TAXNMY already holds is not
    read again. Without texts (`sources` None) nothing is checked."""
    if sources is None:
        return "", ""
    held = [e for e in (before or {}).get("machine_sources") or [] if isinstance(e, dict)]
    own = gate.own_sources(sources)
    domain = guard.own_domain(publisher)
    for e in publisher.get("machine_sources") or []:
        if not isinstance(e, dict) or e in held:
            continue
        url, page = e.get("url"), e.get("source")
        if not isinstance(url, str) or not isinstance(page, str):
            return "reject", "a source for machines without a url and a page that names it"
        if domain is None or guard.owner(page) != domain:
            return "reject", (f"the page {page} that names the source for machines {url} is not on the publisher's "
                              f"domain {domain or '(none held)'}")
        src = own.get(page)
        if not gate.read(src):
            return "parked", f"the page {page} that names the source for machines {url} could not be read"
        if moved(src) or _bare(url) not in src.text.casefold():
            return "reject", f"the page {page} does not write the address of the source for machines {url}"
    return "", ""
