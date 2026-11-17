#!/usr/bin/env python3
"""Protection against intent (TV2-TV5): who something is, decided by code, character by character.

An AI reads what seems to be meant rather than what is written: it reads a name with one character
changed as the name it imitates. So every comparison of names, keys and domains lies here, in code,
and the AI only ever judges *what* something is (TV2).

    TV2  identity      the same, or not: an exact comparison of keys, aliases and domains
    TV3  lookalikes    one or two characters from an existing key or domain, or the same once
                       characters that look alike are read as one (`LOOKALIKE`): held for a person
    TV4  second source a new publisher is taken in on its own evidence; a second source is optional.
                       One that is given (schema field `second_source`) is not on the publisher's
                       domain and names that domain, or the proposal is wrong
    TV5  replacement   a change that moves a source, the publisher or a code of an existing file to
                       another domain or another value: held for a person

Code here holds a list of characters that look alike. It names no brand
and no product (TG4). Standard library only: the lint uses it.
"""

from __future__ import annotations

import re
import urllib.parse
from functools import lru_cache
from itertools import combinations

# --------------------------------------------------------------------------- domains and owners

# Sites where one registrable domain carries the pages of many owners: the owner is part of the
# address (the first path segment, or the label in front of the domain).
OWNER_IN_PATH = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")
OWNER_IN_HOST = ("github.io", "gitlab.io", "readthedocs.io", "netlify.app", "vercel.app", "pages.dev",
                 "sourceforge.io", "blogspot.com", "wordpress.com")
MANY_OWNERS = (  # never the publisher's own site:
    # catalogues and encyclopaedias of products
    "endoflife.date", "wikipedia.org", "wikidata.org", "nist.gov",
    # package registries and app stores, which carry the packages of many publishers
    "npmjs.com", "pypi.org", "packagist.org", "nuget.org", "crates.io", "rubygems.org", "docker.com", "docker.io",
    "chocolatey.org", "brew.sh", "snapcraft.io", "flathub.org", "sourceforge.net", "launchpad.net",
    # sites where anyone writes
    "medium.com", "reddit.com",
)


def registrable(host: str) -> str:
    """example.com from www.example.com; example.co.jp from support.example.co.jp."""
    labels = host.lower().split(":")[0].strip(".").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in {"co", "com", "org", "net", "ac", "gov", "edu", "ne", "or"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def host_of(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""


def on_domain(url: str, domains) -> bool:
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in domains)


def owner(url: str) -> str | None:
    """Who a URL belongs to, as far as its address shows; None when the address cannot show it."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
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


def urls_in(node) -> list[str]:
    """Every http(s) URL in the values of a parsed file, in order."""
    found: list[str] = []

    def walk(n):
        if isinstance(n, dict):
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
        elif isinstance(n, str) and n.startswith(("http://", "https://")) and n not in found:
            found.append(n)

    walk(node)
    return found


# --------------------------------------------------------------------------- TV3: lookalikes

# Characters, or pairs of characters, that a reader takes for another one. Applied in this order,
# after lower case: the pairs first, so that `rn` becomes `m` before `n` is read on its own.
LOOKALIKE = (
    ("rn", "m"), ("vv", "w"), ("cl", "d"),
    ("0", "o"), ("1", "l"), ("i", "l"), ("5", "s"), ("3", "e"), ("4", "a"),
    ("q", "g"), ("9", "g"), ("6", "b"), ("8", "b"), ("2", "z"),
)
SEPARATORS = re.compile(r"[-./]")


@lru_cache(maxsize=65536)
def skeleton(text: str) -> str:
    """The text as a reader sees it: characters that look alike made one."""
    out = text.lower()
    for a, b in LOOKALIKE:
        out = out.replace(a, b)
    return out


def edits(a: str, b: str, limit: int = 2) -> int:
    """The number of edits from a to b: a character inserted, deleted or replaced, or two
    neighbours swapped. An edit of digits only (one number for another, a digit more or less) does
    not count as a small edit: a model number that differs by a digit is another model, not a
    lookalike. Returns limit + 1 when there are more than `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    far = limit + 1

    def cost(ch: str) -> int:  # inserting or deleting one character
        return far if ch.isdigit() else 1

    prev2: list[int] = []
    prev = [0]
    for ch in b:
        prev.append(min(prev[-1] + cost(ch), far))
    for i in range(1, len(a) + 1):
        cur = [min(prev[0] + cost(a[i - 1]), far)] + [far] * len(b)
        for j in range(1, len(b) + 1):
            ca, cb = a[i - 1], b[j - 1]
            if ca == cb:
                sub = prev[j - 1]
            else:
                sub = prev[j - 1] + (far if ca.isdigit() and cb.isdigit() else 1)
            dele = prev[j] + cost(ca)
            ins = cur[j - 1] + cost(cb)
            best = min(sub, dele, ins)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb and ca != cb:
                best = min(best, prev2[j - 2] + 1)
            cur[j] = min(best, far)
        prev2, prev = prev, cur
        if min(prev) > limit:
            return far
    return min(prev[len(b)], far)


def allowed(length: int) -> int:
    """How many edits still make a lookalike, by the length of the shorter word: none below 4
    characters (where one edit makes another word), one up to 7, two from 8."""
    return 0 if length < 4 else 1 if length < 8 else 2


def near(a: str, b: str) -> str:
    """Why `a` looks like `b`, or '' when it does not. Equal values are the same, not near (TV2).
    Values are compared per word (the parts between dashes, dots and slashes): when only one word
    differs, the length of that word sets how many edits count, so that two products of a series
    whose words differ (an abbreviation of three letters) are not taken for each other."""
    if a == b:
        return ""
    if skeleton(a) == skeleton(b):
        return "differs only in characters that look alike"
    wa, wb = SEPARATORS.split(a), SEPARATORS.split(b)
    if len(wa) == len(wb):
        differ = [(x, y) for x, y in zip(wa, wb) if x != y]
        if len(differ) == 1:
            x, y = differ[0]
            n = edits(x, y)
            return f"{n} character{'s' if n > 1 else ''} apart" if n <= allowed(min(len(x), len(y))) else ""
    n = edits(a, b)
    return f"{n} character{'s' if n > 1 else ''} apart" if n <= min(allowed(min(len(a), len(b))), 1) else ""


def lookalikes(new: str, existing, limit: int = 2) -> list[tuple[str, str]]:
    """(existing value, why) for each existing value the new one looks like."""
    out = []
    sk = skeleton(new)
    for old in existing:
        if old == new or (abs(len(old) - len(new)) > limit and skeleton(old) != sk):
            continue
        why = near(new, old)
        if why:
            out.append((old, why))
    return sorted(out)


def pairs(values) -> list[tuple[str, str, str]]:
    """(a, b, why) for every two of the values that look alike: what the whole dataset holds now."""
    return [(a, b, why) for a, b in combinations(sorted(set(values)), 2) if (why := near(a, b))]


# --------------------------------------------------------------------------- TV4: a second source, when there is one

# A new publisher is taken in on its own evidence: its own site, where the product is to be had or
# where its versions are published. A second source is not required. When the proposal gives one
# (`second_source`, schema/publisher.schema.json), it is recorded with the publisher, so code checks
# it: a page that is not on the publisher's domain, and that names that domain.


def own_domain(publisher: dict) -> str | None:
    """The owner of the publisher's source: a registrable domain, or host/owner on a site of many owners."""
    source = publisher.get("source")
    return owner(source) if isinstance(source, str) else None


def second_source_place(publisher: dict) -> str:
    """'' when the publisher gives no second source, or one that is not on its own domain; otherwise
    why it is wrong. Needs no text: the lint checks it."""
    url = publisher.get("second_source")
    if url is None:
        return ""
    if not isinstance(url, str):
        return "second_source is not a URL"
    domain = own_domain(publisher)
    if domain is None:
        return "the publisher's source shows no domain of its own"
    if (owner(url) or host_of(url)) == domain:
        return f"second_source {url} is on the publisher's own domain {domain}; a second source is a page the publisher does not write"
    return ""


def second_source(publisher: dict, texts: dict) -> str:
    """'' when the publisher gives no second source, or one that passes; otherwise why it does not.
    `texts`: url -> text that was read. A second source that cannot be read does not pass: it is
    recorded to be read again, so it must be readable."""
    url = publisher.get("second_source")
    if url is None:
        return ""
    wrong = second_source_place(publisher)
    if wrong:
        return wrong
    host = own_domain(publisher).split("/")[0]
    text = texts.get(url)
    if not text:
        return f"second_source {url} could not be read; give a page that can be read, or leave the field out"
    if re.search(r"(?<![\w-])" + re.escape(host) + r"(?![\w-]|\.\w)", text.casefold()) is None:
        return f"second_source {url} does not name the publisher's domain {host}"
    return ""


# --------------------------------------------------------------------------- TV5: replacing what exists

CODES = ("cpe", "purl", "gtin", "winget", "homebrew")


def _owners(urls) -> set:
    return {owner(u) or host_of(u) for u in urls if isinstance(u, str)}


def replaced(old: dict, new: dict) -> list[str]:
    """What a change to an existing file moves to another domain or another value; [] when nothing.
    A URL may change within the same owner (another page of the same site); a value may not."""
    out = []
    if not isinstance(old, dict) or not isinstance(new, dict):
        return out
    if "publisher" in old and new.get("publisher") != old.get("publisher"):
        out.append(f"publisher {old.get('publisher')!r} becomes {new.get('publisher')!r}")
    for label, get in (("source", lambda d: d.get("source")),
                       ("versions.source", lambda d: (d.get("versions") or {}).get("source") if isinstance(d.get("versions"), dict) else None)):
        a, b = get(old), get(new)
        if isinstance(a, str) and a != b and _owners([a]) != _owners([b]):
            out.append(f"{label} moves from {owner(a) or host_of(a)} to {owner(b) or host_of(b) if isinstance(b, str) else 'nothing'}")
    old_codes = {(c, str(i[c])) for i in old.get("identifiers") or [] if isinstance(i, dict) for c in CODES if c in i}
    new_codes = {(c, str(i[c])) for i in new.get("identifiers") or [] if isinstance(i, dict) for c in CODES if c in i}
    for c, v in sorted(old_codes - new_codes):
        out.append(f"{c} {v!r} is changed or taken out")
    old_src = {(c, str(i[c])): i.get("source") for i in old.get("identifiers") or [] if isinstance(i, dict) for c in CODES if c in i}
    for i in new.get("identifiers") or []:
        if not isinstance(i, dict):
            continue
        for c in CODES:
            a, b = old_src.get((c, str(i.get(c)))), i.get("source")
            if c in i and isinstance(a, str) and a != b and _owners([a]) != _owners([b]):
                out.append(f"the source of {c} {i[c]!r} moves from {owner(a) or host_of(a)} to {owner(b) or host_of(b) if isinstance(b, str) else 'nothing'}")
    old_machine = _owners([e.get("url") for e in old.get("machine_sources") or [] if isinstance(e, dict)]
                          + [old.get("source")])
    for e in new.get("machine_sources") or []:
        if isinstance(e, dict) and isinstance(e.get("url"), str) and _owners([e["url"]]) - old_machine:
            out.append(f"a source for machines on {owner(e['url']) or host_of(e['url'])}, a domain the publisher did not use")
    if "second_source" in old and new.get("second_source") != old.get("second_source"):
        out.append("second_source is changed or taken out")
    return out
