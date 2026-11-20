#!/usr/bin/env python3
"""The importer of a CVE round (TAX-27): from a list of names to product files, aliases and parked
entries, past the gate (TAX-17) and the parking list (TAX-29).

Three inputs, none of them a source for a name or a publisher on its own:

    the found rows   CSV files of a find round (TAX-28): per listed product, the publisher, the name as
                     the publisher writes it, the kind and the pages a finder pointed to. Semicolons,
                     a header: lijst_leverancier;lijst_product;uitgever;uitgever_url;productnaam;soort;
                     productpagina_url;versiepagina_url;opmerking
    the list         the KEV list of CISA and the CVE records of its entries (CVE List v5): the names
                     that are asked of the grouping (tools/group.py, TAX-26)
    the grouping     optional: classes.json of `importer.py group --live`; see below what a class
                     series, edition or doubt does; an alias is added to the product it names

Per found row, code decides one outcome, in this order:

    apart      series            the row is more than one product, or a part (the find round says so)
               edition           the row is an edition or a version of another product (idem)
               not-found         no name, no product page, or no publisher
               grouped-series    the grouping classed the name series of no product: a range of products
               grouped-edition   the grouping classed the name edition or series of a product that TAX has
                                 or this proposal brings (TAX-42); of another product, the row is a product
    parked     grouping          the grouping doubts, and the find round gave no product and versions page;
                                 with both, the row is proposed with the doubt for the referee (TAX-44)
    alias      same-row          an earlier row gives the same key
               existing          TAX has the product already (by key, alias or name, TV2)
               grouping          the grouping classed the name alias of another product
    parked     no-kind           no kind
    apart      no-versions       no versions page
    parked     no-publisher-page a new publisher, and no page of its own names it (TV4, decision 28)
               closed            a source refuses bots (401, 403, 429, robots.txt); never worked around
               unreadable        a source gives no text (404, an error, too little text)
               moved             a source redirects, and the address it ends at redirects again
               name-not-on-page  the product page, after its redirects, does not show the name
    candidate                    product file (and publisher file) for the gate and the referee

A source of the find round that redirects (TAX-38) is proposed once at the address it ends at
(TAX-42): here the round is the submitter. That address is checked like any other source, and as
the round chose it, not the finder, it must show the name, the versions page too. When it redirects
again, the row stays parked: there is no second try.

Every value that is not a URL counts as empty in a URL field, and a placeholder ("niet gevonden",
"n/a", ...) counts as empty in a name field. With --fetch every page is read by the reader of the
referee (tools/fetch.py; no browser in CI): what the importer parks for a closed or unreadable source
or a name that is not on the page never reaches the referee. What is left goes through the gate of
the pull request (tools/pr_referee.py gate_proposals) as it would in the workflow: lint, guard, TR5, TR6,
TR8; what it leaves is pending for the referee, and costs one request each.

Usage:

    python tools/importer.py list --found CSV... --since DATE --out DIR      # the KEV list and its CVE records (free)
    python tools/importer.py group --found CSV... --kev KEV.json --cve DIR [--since DATE] [--first N] --out DIR
    python tools/importer.py group ... --live --cap 1.00 # the grouping as one batch (costs money)
    python tools/importer.py propose --found CSV... [--first N] [--classes classes.json] [--fetch] --out DIR
    python tools/importer.py propose ... --write          # write the files and the parking list into the data
    python tools/importer.py propose ... --fetch --gated gated.json   # then: tools/pr_referee.py judge --gated gated.json
    python tools/importer.py count --outcomes outcomes.json --verdicts verdicts.json   # the count after the verdict

The paid trial runs as the workflow referee-import (TAX-27): list, group, propose, judge, and a pull
request with the outcome; Fred approves each job with the key in environment referee.

Without --write nothing in the data changes: the files go to --out, with report.md and outcomes.json.
The importer names no brand or product (R3): every rule here is generic.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
from fetch import moved as ends_elsewhere  # noqa: E402
import group  # noqa: E402
import intake  # noqa: E402
import keyrule  # noqa: E402
import pr_referee  # noqa: E402
import referee  # noqa: E402

REPO = referee.REPO
COLUMNS = ("lijst_leverancier", "lijst_product", "uitgever", "uitgever_url", "productnaam", "soort",
           "productpagina_url", "versiepagina_url", "opmerking")
KINDS = ("device", "operating-system", "software")
URL = re.compile(r"^https?://[^\s;]+$")
PLACEHOLDERS = {"niet gevonden", "not found", "n/a", "na", "none", "unknown", "onbekend", "-", "?", "geen"}
NOTE_SERIES = re.compile(r"^\s*reeks\b", re.I)
NOTE_SAME = re.compile(r"^\s*zelfde product als\s+(.+?)\s*$", re.I)
NOTE_EDITION = re.compile(r"^\s*editie of versie van\s+(.+?)\s*$", re.I)
NOTE_UNSUPPORTED = re.compile(r"niet meer ondersteund", re.I)
COST_PER_PROPOSAL = 0.027  # dollars per proposal for the referee: Opus 5.5 at effort low, measured (TAX-18)
CLOSED = {"refused", "disallowed", "429"}
# What the find round itself set aside as no product or without a versions page (decision 33), and
# what the grouping showed to be a range of products or an edition of a product that is there
# (TAX-42): apart in the count, and not counted for the threshold.
FOUND_ASIDE = {"series", "edition", "not-found", "no-versions", "grouped-series", "grouped-edition"}
DOUBT = "de namen alleen beslissen het niet"  # the grouping's reason when classes.json gives none

# reason -> (what the report says, what the parked entry waits for)
PARK = {
    "series": ("reeks of onderdeel", "better-proposal"),
    "edition": ("versie of editie", "better-proposal"),
    "not-found": ("niet gevonden: geen naam, productpagina of uitgever", "better-proposal"),
    "grouped-series": ("reeks volgens het groeperen", "better-proposal"),
    "grouped-edition": ("editie, reeks of onderdeel van een product in TAX of in deze voordracht (het groeperen)",
                        "better-proposal"),
    "grouping": ("het groeperen twijfelt, en de vindronde gaf geen productpagina en versiepagina", "better-proposal"),
    "no-kind": ("geen kind", "better-proposal"),
    "no-versions": ("geen versions.source", "better-proposal"),
    "no-publisher-page": ("nieuwe uitgever zonder eigen pagina met zijn naam", "better-proposal"),
    "closed": ("gesloten bron", "event"),
    "unreadable": ("bron niet leesbaar", "event"),
    "name-not-on-page": ("naam niet op de productpagina", "better-proposal"),
    "moved": ("bron verwijst door, ook vanaf het eindadres", "better-proposal"),
}
ALIAS = {
    "same-row": "zelfde product als een eerdere rij",
    "existing": "bestaat al in TAX",
    "grouping": "alias volgens het groeperen",
}


# --------------------------------------------------------------------------- the found rows

@dataclass
class Row:
    file: str
    line: int
    vendor: str  # as the list writes it
    listed: str  # the product as the list writes it
    publisher: str
    publisher_url: str
    name: str
    kind: str
    source: str
    versions: str
    note: str

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


def url_or_empty(value: str) -> str:
    value = (value or "").strip()
    return value if URL.fullmatch(value) else ""


def name_or_empty(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return "" if value.casefold() in PLACEHOLDERS else value


def read_found(paths: list) -> list[Row]:
    """The rows of the CSV files, in order, cleaned: a URL field that holds no URL and a name field
    that holds a placeholder are empty; a kind outside the three is empty."""
    rows = []
    for path in paths:
        path = Path(path)
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise SystemExit(f"{path}: columns missing: {', '.join(missing)}")
            for n, r in enumerate(reader, 2):
                if not any((v or "").strip() for v in r.values() if isinstance(v, str)):
                    continue
                kind = (r["soort"] or "").strip().lower()
                rows.append(Row(
                    file=path.name, line=n, vendor=name_or_empty(r["lijst_leverancier"]),
                    listed=name_or_empty(r["lijst_product"]), publisher=name_or_empty(r["uitgever"]),
                    publisher_url=url_or_empty(r["uitgever_url"]), name=name_or_empty(r["productnaam"]),
                    kind=kind if kind in KINDS else "", source=url_or_empty(r["productpagina_url"]),
                    versions=url_or_empty(r["versiepagina_url"]), note=(r["opmerking"] or "").strip()))
    return rows


# --------------------------------------------------------------------------- the plan

@dataclass
class Outcome:
    row: Row
    outcome: str  # candidate, alias or parked
    reason: str = ""  # a key of PARK or ALIAS
    key: str = ""  # the product key; for a parked row without a name, the key of the listed name
    publisher: str = ""  # the publisher key
    new_publisher: bool = False
    of: str = ""  # alias: the key it is another writing of
    detail: str = ""
    aliases: list = field(default_factory=list)  # candidate: keys of other writings (grouping)
    doubt: str = ""  # the reason of the grouping when it doubted, and the row is proposed all the same (TAX-44)
    moves: list = field(default_factory=list)  # [address of the find round, address it ended at] (TAX-42)

    @property
    def label(self) -> str:
        if self.outcome == "parked":
            return PARK[self.reason][0]
        if self.outcome == "alias":
            return ALIAS[self.reason]
        return self.reason or "kandidaat"


def product_key(publisher: str, name: str) -> str:
    try:
        return keyrule.make_key(publisher, name)
    except keyrule.KeyRuleError:
        return ""


def existing_index(data_root: Path) -> dict:
    """publisher -> {key part of a product key, an alias or a name: the product key} (TV2)."""
    out: dict = {}
    for path in sorted((data_root / "products").glob("*/*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        key, pub = data.get("key"), data.get("publisher")
        if not isinstance(key, str) or not isinstance(pub, str):
            continue
        parts = out.setdefault(pub, {})
        for value in [key, *(data.get("aliases") or [])]:
            parts.setdefault(str(value).split("/", 1)[-1], key)
        parts.setdefault(group.part(str(data.get("name", ""))), key)
    return out


def publisher_of(row: Row, index: dict) -> tuple[str, bool]:
    """(the publisher key, already in TAX), from the publisher the finder names. The list's vendor
    is no source for a publisher: it is used only when the finder names none, and then the row has
    no publisher page either."""
    name = row.publisher or row.vendor
    if not name:
        return "", False
    return group.publisher_key(name, index, {})


def first(rows: list[Row], n: int | None) -> list[Row]:
    return rows if not n else rows[:n]


def plan(rows: list[Row], data_root: Path = REPO, classes: dict | None = None) -> list[Outcome]:
    """One outcome per row, by code and the grouping only (no page read yet)."""
    index = group.publisher_index(data_root)
    have = existing_index(data_root)
    classes = classes or {}
    keys = existing_keys(data_root)
    out, taken = [], {}  # taken: key -> the outcome of the candidate that holds it
    later = []  # (outcome, the key the grouping points to): settled once every row is planned
    editions = []  # (outcome, the key of the product the grouping says it belongs to): idem (TAX-42)
    for row in rows:
        pub, known = publisher_of(row, index)
        listed_key = product_key(pub, row.listed) if pub and row.listed else ""
        key = product_key(pub, row.name) if pub and row.name else ""
        o = Outcome(row=row, outcome="parked", key=key or listed_key, publisher=pub, new_publisher=bool(pub) and not known)
        sorted_as = (classes.get(pub) or {}).get(group.part(row.name)) if row.name else None
        grouped = sorted_as["class"] if sorted_as else ""
        if grouped == "doubt":
            o.doubt = sorted_as.get("reason") or DOUBT
        if NOTE_SERIES.match(row.note):
            o.reason, o.key = "series", listed_key or key
        elif m := NOTE_EDITION.match(row.note):
            o.reason, o.key, o.detail = "edition", listed_key or key, f"van {m.group(1)}"
        elif not (row.name and row.source and key and (known or row.publisher)):
            # A new publisher needs the finder's name for it: the vendor of the list is no source.
            o.reason = "not-found"
            o.detail = ", ".join(w for w, v in (("naam", row.name), ("productpagina", row.source),
                                                ("uitgever", known or row.publisher)) if not v)
        elif grouped == "series" and not sorted_as.get("of"):
            o.reason, o.detail = "grouped-series", sorted_as.get("reason") or ""
        elif grouped in ("series", "edition"):
            of = sorted_as.get("of") or ""
            target = of if "/" in of else (product_key(pub, of) if of else "")
            if target and target != key and target in keys:
                o.reason, o.of, o.detail = "grouped-edition", target, f"{grouped} van {target} (in TAX)"
            else:
                editions.append((o, target))  # in this proposal, or no product at all: decided below
        elif grouped == "doubt" and not (row.source and row.versions):
            # TAX-44: only with a product page and a versions page can the referee judge TR4 on the sources.
            o.reason = "grouping"
            o.detail = o.doubt + "; " + ", ".join(w for w, v in (("productpagina", row.source),
                                                                  ("versiepagina", row.versions)) if not v) + " ontbreekt"
        elif key in taken:
            o.outcome, o.reason, o.of = "alias", "same-row", key
            o.detail = f"rij {taken[key].row.where}" + (f"; {row.note}" if NOTE_SAME.match(row.note) else "")
        elif hit := existing_hit(have, pub, key, row.name):
            o.outcome, o.reason, o.of = "alias", "existing", hit
        elif sorted_as and sorted_as["class"] == "alias" and sorted_as.get("of"):
            target = sorted_as["of"] if "/" in sorted_as["of"] else product_key(pub, sorted_as["of"])
            if target in keys:
                o.outcome, o.reason, o.of = "alias", "grouping", target
            else:
                later.append((o, target))  # in this proposal, or no product at all: decided below
        else:
            as_product(o, taken)
        out.append(o)
    # TAX-39: a row is an alias only of a product TAX has or this proposal brings. Otherwise it is
    # the product itself, and the name the grouping pointed to goes with it as an alias.
    for o, target in later:
        if target in taken and target != o.key:
            o.outcome, o.reason, o.of = "alias", "grouping", target
            o.detail = f"rij {taken[target].row.where}"
            continue
        as_product(o, taken)
        if o.outcome == "candidate" and target and target != o.key and not keyrule.key_problems(target):
            o.aliases.append(target)
    # TAX-42: an edition or a series only of a product TAX has or this proposal brings. Otherwise the
    # row gets the outcome it would get as a product: a candidate, set aside, or parked with the reason.
    for o, target in editions:
        grouped = "editie of reeks"
        if target and target != o.key and target in taken:
            o.reason, o.of = "grouped-edition", target
            o.detail = f"{grouped} van {target} (rij {taken[target].row.where})"
        elif hit := existing_hit(have, o.publisher, o.key, o.row.name):
            o.outcome, o.reason, o.of = "alias", "existing", hit
        else:
            as_product(o, taken)
            if o.outcome == "candidate" or not o.detail:
                note = f"het groeperen: {grouped} van {target or 'een product'}, dat niet in TAX staat en niet is voorgedragen"
                o.detail = "; ".join(d for d in (o.detail, note) if d)
    return out


def existing_hit(have: dict, pub: str, key: str, name: str) -> str:
    """The key of the product in TAX this row names (by key, alias or name, TV2), or ''."""
    parts = have.get(pub) or {}
    return parts.get(key.split("/", 1)[1]) or parts.get(group.part(name)) or ""


def as_product(o: Outcome, taken: dict) -> None:
    """The outcome of a row as a product of its own: a candidate, set aside, or parked with the reason."""
    row = o.row
    if o.key in taken and taken[o.key] is not o:
        o.outcome, o.reason, o.of = "alias", "same-row", o.key
        o.detail = f"rij {taken[o.key].row.where}" + (f"; {row.note}" if NOTE_SAME.match(row.note) else "")
    elif not row.kind:
        o.reason = "no-kind"
    elif not row.versions:
        o.reason = "no-versions"
    elif o.new_publisher and not row.publisher_url and not row.source:
        o.reason = "no-publisher-page"
    else:
        o.outcome, o.reason = "candidate", ""
        if NOTE_UNSUPPORTED.search(row.note):
            o.detail = "volgens de vinder niet meer ondersteund (TR7 is aan de scheidsrechter)"
        taken[o.key] = o


def existing_keys(data_root: Path) -> set:
    return {k for parts in existing_index(data_root).values() for k in parts.values()}


def dangling(outcomes: list, keys: set) -> list:
    """Rows whose outcome is alias of a product that is neither in TAX nor proposed now (TAX-39)."""
    proposed = {o.key for o in outcomes if o.outcome == "candidate"}
    return [o for o in outcomes if o.outcome == "alias" and o.of not in keys and o.of not in proposed]


def orphans(outcomes: list, keys: set) -> list:
    """Rows set apart as an edition or a series of a product that is neither in TAX nor proposed now
    (TAX-42): the product would disappear unseen."""
    proposed = {o.key for o in outcomes if o.outcome == "candidate"}
    return [o for o in outcomes if o.reason == "grouped-edition" and o.of not in keys and o.of not in proposed]


def add_grouped_aliases(outcomes: list[Outcome], classes: dict) -> dict:
    """Aliases from the grouping: a name classed alias of a candidate gets the candidate an alias
    key; a name classed alias of an existing product gets that product one. -> existing key -> aliases."""
    by_key = {o.key: o for o in outcomes if o.outcome == "candidate"}
    names = {}  # (publisher, part of a candidate name) -> its key
    for o in by_key.values():
        names[(o.publisher, group.part(o.row.name))] = o.key
    extra: dict = {}
    for pub, sorted_names in classes.items():
        for part_, d in sorted_names.items():
            if d.get("class") != "alias" or not d.get("of"):
                continue
            alias_key = f"{pub}/{part_}" if not keyrule.part_problems(part_, "product") else ""
            target = d["of"] if d["of"] in by_key or "/" in d["of"] else names.get((pub, group.part(d["of"])))
            if not alias_key or not target or alias_key == target:
                continue
            if target in by_key:
                if alias_key not in by_key[target].aliases and alias_key not in by_key:
                    by_key[target].aliases.append(alias_key)
            else:
                extra.setdefault(target, [])
                if alias_key not in extra[target]:
                    extra[target].append(alias_key)
    return extra


def follow(outcomes: list[Outcome]) -> None:
    """A row that is another writing of an earlier row shares its fate: when that row is parked after
    its pages were read or the gate settled it, so is this one, with the same reason."""
    by_key: dict = {}
    for o in outcomes:
        if o.outcome != "alias":
            by_key.setdefault(o.key, o)
    for o in outcomes:
        if o.outcome == "alias" and o.reason in ("same-row", "grouping"):
            target = by_key.get(o.of)
            if target is not None and target.outcome != "candidate":
                o.outcome, o.reason, o.of = target.outcome, target.reason, ""
                o.detail = f"volgt rij {target.row.where}" + (f"; {target.detail}" if target.detail else "")


# --------------------------------------------------------------------------- the files

def dump(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=200)


def product_text(o: Outcome) -> str:
    r = o.row
    return dump({"key": o.key, "publisher": o.publisher, "name": r.name, "kind": r.kind, "source": r.source,
                 "versions": {"source": r.versions}, "aliases": sorted(o.aliases)})


def partial_text(o: Outcome) -> str:
    """What is known of a parked row, as a proposal that can be improved and proposed again."""
    r = o.row
    data = {"key": o.key, "publisher": o.publisher, "name": r.name or r.listed}
    if r.kind:
        data["kind"] = r.kind
    if r.source:
        data["source"] = r.source
    if r.versions:
        data["versions"] = {"source": r.versions}
    data["aliases"] = []
    return dump(data)


def publisher_text(key: str, name: str, source: str) -> str:
    return dump({"key": key, "name": name, "source": source, "aliases": []})


# --------------------------------------------------------------------------- reading the pages

class Recorder:
    """A `get` for the Fetcher that remembers where each request ended after its redirects."""

    def __init__(self, get=None):
        from fetch import _urlopen_get
        self._get = get or _urlopen_get
        self.final: dict = {}

    def __call__(self, url: str, timeout: float):
        resp = self._get(url, timeout)
        if getattr(resp, "final_url", "") and resp.final_url != url:
            self.final[url] = resp.final_url
        return resp


def names_of(publisher: str) -> list[str]:
    out = [publisher]
    stripped = keyrule.strip_legal_form(publisher).strip(" ,.")
    if stripped and stripped != publisher:
        out.append(stripped)
    return out


def check_pages(outcomes: list[Outcome], fetcher, final: dict | None = None) -> dict:
    """Read the pages of each candidate; park what is closed, unreadable, or does not show the name.
    For a new publisher, choose its source: the first page of the finder, most named first, then the
    product pages, that is read and shows its name. -> publisher key -> (name, source)."""
    final = final if final is not None else {}

    def problem(url: str) -> tuple[str, str] | None:
        src = fetcher.fetch(url, "")
        if src.status in CLOSED:
            return "closed", f"{url}: {src.status}"
        if not gate.read(src):
            return "unreadable", f"{url}: {src.status}"
        if end := ends_elsewhere(src):  # TAX-38: the text is that of another address
            return "moved", end
        return None

    for o in outcomes:
        if o.outcome != "candidate":
            continue
        for url in dict.fromkeys([o.row.source, o.row.versions]):
            p = problem(url)
            if p and p[0] == "moved":
                # TAX-42: the round proposes the address it ended at, once; that page is checked like any other.
                end = p[1]
                again = problem(end)
                if again and again[0] == "moved":
                    p = ("moved", f"{url} -> {end} -> {again[1]}: het eindadres verwijst opnieuw door, geen tweede poging")
                else:
                    p = again
                    o.moves.append([url, end])
                    o.row.source = end if o.row.source == url else o.row.source
                    o.row.versions = end if o.row.versions == url else o.row.versions
            if p:
                o.outcome, (o.reason, o.detail) = "parked", p
                break
        else:
            # The page the round proposed itself must show the name: the finder did not choose it.
            for url in dict.fromkeys([o.row.source, *(end for _, end in o.moves)]):
                if not gate.appears(o.row.name, fetcher.fetch(url, "").text):
                    o.outcome, o.reason = "parked", "name-not-on-page"
                    asked, moved = next((a for a, e in o.moves if e == url), ""), final.get(url)
                    o.detail = f"{o.row.name!r} niet op {url}" + (f" (eindadres van {asked})" if asked
                                                                 else f" (omgeleid naar {moved})" if moved else "")
                    break
        if o.moves:
            moved = "; ".join(f"{a} -> {e}" for a, e in o.moves)
            o.detail = "; ".join(d for d in (o.detail, f"eindadres voorgedragen: {moved}") if d)

    publishers: dict = {}
    by_pub: dict = {}
    for o in outcomes:
        if o.outcome == "candidate" and o.new_publisher:
            by_pub.setdefault(o.publisher, []).append(o)
    for pub, members in by_pub.items():
        name = Counter(m.row.publisher for m in members if m.row.publisher).most_common(1)[0][0]
        tries = [u for u, _ in Counter(m.row.publisher_url for m in members if m.row.publisher_url).most_common()]
        tries += [m.row.source for m in members if m.row.source not in tries]
        chosen = closed = ""
        for url in tries:
            src = fetcher.fetch(url, "")
            if end := ends_elsewhere(src):  # TAX-42: the address it ends at, once
                url, src = end, fetcher.fetch(end, "")
            if src.status in CLOSED:
                closed = closed or url
                continue
            if gate.read(src) and not ends_elsewhere(src) and any(gate.appears(n, src.text) for n in names_of(name)):
                chosen = url
                break
        if chosen:
            publishers[pub] = (name, chosen)
            continue
        for m in members:
            m.outcome = "parked"
            if closed:
                m.reason, m.detail = "closed", f"uitgeverspagina {closed}"
            else:
                m.reason, m.detail = "no-publisher-page", f"{name!r} op geen van: {', '.join(tries)}"
    return publishers


def publisher_sources(outcomes: list[Outcome]) -> dict:
    """Without reading the pages: the page the finder named most for the publisher, or a product page."""
    out: dict = {}
    for pub in dict.fromkeys(o.publisher for o in outcomes if o.outcome == "candidate" and o.new_publisher):
        members = [o for o in outcomes if o.outcome == "candidate" and o.publisher == pub]
        name = Counter(m.row.publisher for m in members if m.row.publisher).most_common(1)[0][0]
        urls = Counter(m.row.publisher_url for m in members if m.row.publisher_url).most_common(1)
        out[pub] = (name, urls[0][0] if urls else members[0].row.source)
    return out


def files_of(outcomes: list[Outcome], publishers: dict, extra_aliases: dict, data_root: Path = REPO) -> dict:
    """rel -> text: the candidates, their new publishers, and existing products that get an alias."""
    files = {}
    for o in outcomes:
        if o.outcome != "candidate":
            continue
        files[f"products/{o.key}.yaml"] = product_text(o)
        if o.new_publisher and o.publisher in publishers:
            files[f"publishers/{o.publisher}.yaml"] = publisher_text(o.publisher, *publishers[o.publisher])
    for key, aliases in extra_aliases.items():
        path = data_root / "products" / f"{key}.yaml"
        if not path.is_file():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        merged = list(data.get("aliases") or [])
        merged += [a for a in aliases if a not in merged and a != key]
        if merged != list(data.get("aliases") or []):
            data["aliases"] = merged
            files[f"products/{key}.yaml"] = dump(data)
    return files


def parked_entries(outcomes: list[Outcome], publishers: dict, day: str) -> list[dict]:
    """Entries for the parking list (TAX-29), one per key: a later row of the same key adds nothing."""
    out = {}
    for o in outcomes:
        if o.outcome != "parked" or not o.key or o.key in out:
            continue
        files = {f"products/{o.key}.yaml": partial_text(o)}
        if o.new_publisher and o.row.publisher:
            src = publishers.get(o.publisher, (o.row.publisher, o.row.publisher_url))[1]
            if src:
                files[f"publishers/{o.publisher}.yaml"] = publisher_text(o.publisher, o.row.publisher, src)
        out[o.key] = {"key": o.key, "path": f"products/{o.key}.yaml", "publisher": o.publisher,
                      "waits_for": PARK[o.reason][1], "rule": "TAX-27",
                      "reason": PARK[o.reason][0] + (f": {o.detail}" if o.detail else ""), "since": day, "checked": day,
                      "pr": None, "fingerprint": pr_referee.fingerprint(files), "files": files}
    return list(out.values())


# --------------------------------------------------------------------------- the gate of the pull request

def through_gate(files: dict, fetcher, data_root: Path = REPO, log=print, submitter: str = "") -> list[dict]:
    """The gate of the pull request, as in the workflow (no key): one item per proposal. With a
    submitter (TAX-32), its proposals carry its name, so that its values of its own in
    referee/intake.yaml (an agreed round) are the limits of the referee."""
    sub = intake.Submitter(submitter, known=True) if submitter else None
    return pr_referee.gate_proposals(pr_referee.group(files), fetcher, data_root, log=log, submitter=sub,
                                     limits=intake.load(data_root) if sub else None)


def apply_gate(outcomes: list[Outcome], items: list[dict]) -> None:
    """What the gate settled becomes the outcome of its row: parked or rejected by code."""
    by_rel = {i["id"]: i for i in items}
    for o in outcomes:
        item = by_rel.get(f"products/{o.key}.yaml") if o.outcome == "candidate" else None
        if item is None or item["outcome"] == "pending":
            continue
        if item["outcome"] == "accept":
            o.reason = "door de poort aanvaard"
        else:
            o.outcome = "gate-" + item["outcome"]
            o.reason = f"{item['rule']}: {item['reason'][:160]}"


def mark_doubt(outcomes: list[Outcome], items: list[dict]) -> None:
    """TAX-44: a proposal the grouping doubted says so to the referee, with the grouping's reason,
    as a line on TR4 in the block of what code decided (referee/prompt.md, User message)."""
    doubt = {f"products/{o.key}.yaml": o.doubt for o in outcomes if o.outcome == "candidate" and o.doubt}
    for item in items:
        if item["outcome"] != "pending" or item["id"] not in doubt or "TR4" not in item.get("ai_rules", []):
            continue
        item["doubt"] = doubt[item["id"]]
        line = ("TR4 in part: the grouping, which sees only the names of this publisher, could not decide whether "
                f"this is a product of its own: {referee.inert(doubt[item['id']])}. Judge TR4 on the sources.")
        message = item["message"]
        if "\nJudge only: " in message:
            item["message"] = message.replace("\nJudge only: ", f"\n{line}\nJudge only: ", 1)
        else:
            rules = ", ".join(item["ai_rules"])
            item["message"] = message.replace("<identity>\n", f"<decided>\n{line}\nJudge only: {rules}\n</decided>\n\n"
                                              "<identity>\n", 1)


# --------------------------------------------------------------------------- the report

def tally(outcomes: list[Outcome]) -> dict:
    by = Counter()
    for o in outcomes:
        if o.outcome == "candidate":
            by[("opgenomen-kandidaat", "voor de scheidsrechter" + (", na twijfel bij het groeperen" if o.doubt else ""))] += 1
        elif o.outcome == "alias":
            by[("alias", ALIAS[o.reason])] += 1
        elif found_aside(o):
            by[("apart", PARK[o.reason][0])] += 1
        elif o.outcome == "parked":
            by[("geparkeerd", PARK[o.reason][0])] += 1
        else:
            by[(o.outcome, o.reason.split(":")[0])] += 1
    return by


def found_aside(o: Outcome) -> bool:
    """The find round showed this row is no product or has no versions page, or the grouping showed it
    is a range of products or an edition of a product that is there: it is not proposed."""
    return o.outcome == "parked" and o.reason in FOUND_ASIDE


# --------------------------------------------------------------------------- the count per product (TAX-39)

CLOSED_TEXT = re.compile(r"\b(refused|disallowed|429|not read|could not be read)\b", re.I)
STATUS = (  # in the order of the report
    ("aanvaard", "door de scheidsrechter aanvaard: nieuw product, of een alias bij een bestaand"),
    ("alias", "door de scheidsrechter als bestaand product herkend"),
    ("bestaat al", "staat al in TAX; niets nieuws voorgedragen"),
    ("voor de scheidsrechter", "nog geen oordeel"),
    ("geparkeerd", "door de poort of de scheidsrechter, om een andere reden dan een gesloten bron"),
    ("gesloten bron", "geparkeerd: een bron weigert bots"),
    ("afgewezen", "door de poort of de scheidsrechter"),
    ("verloren", "alias van een product dat niet bestaat en niet is voorgedragen"),
    ("apart", "de vindronde of het groeperen wees het aan als geen eigen product, of zonder versiepagina"),
)


def product_of(o: Outcome) -> str:
    return o.of if o.outcome == "alias" and o.of else o.key


def products(outcomes: list[Outcome], verdicts: dict | None, keys: set) -> dict:
    """product key -> {rows, status, why, publisher, new}: one outcome per product, after the verdict
    when there is one. A product listed in several rows counts once."""
    by_rel = {v["id"]: v for v in (verdicts or {}).get("verdicts", [])}
    lost = {id(o) for o in dangling(outcomes, keys)}
    out: dict = {}
    for o in outcomes:
        p = out.setdefault(product_of(o), {"rows": [], "publisher": o.publisher, "new": False})
        p["rows"].append(o)
        p["new"] = p["new"] or (o.new_publisher and not found_aside(o))
    for key, p in out.items():
        rows = [o for o in p["rows"] if not found_aside(o)]
        v = by_rel.get(f"products/{key}.yaml")
        parked = [o for o in rows if (o.outcome == "parked" and o.reason != "closed") or o.outcome == "gate-parked"]
        if not rows:
            o = p["rows"][0]
            p["status"], p["why"] = "apart", PARK[o.reason][0]
        elif v is not None:
            rule, text = v.get("rule") or "", (v.get("reason") or "").strip()
            reason = text if not rule or text.startswith(rule) else f"{rule}: {text}"
            if v["outcome"] == "accept":
                p["status"], p["why"] = "aanvaard", ""
            elif v["outcome"] == "alias":
                p["status"], p["why"] = "alias", reason
            elif v["outcome"] == "reject":
                p["status"], p["why"] = "afgewezen", reason
            elif v.get("waits_for") == "event" and CLOSED_TEXT.search(v.get("reason") or ""):
                p["status"], p["why"] = "gesloten bron", reason
            else:
                p["status"], p["why"] = "geparkeerd", reason
        elif parked:
            o = parked[0]
            p["status"], p["why"] = "geparkeerd", PARK[o.reason][0] if o.reason in PARK else o.reason
        elif any(o.outcome == "gate-reject" for o in rows):
            p["status"], p["why"] = "afgewezen", next(o.reason for o in rows if o.outcome == "gate-reject")
        elif any(o.outcome == "parked" for o in rows):
            p["status"], p["why"] = "gesloten bron", next(o.detail for o in rows if o.outcome == "parked")
        elif all(id(o) in lost for o in rows if o.outcome == "alias") and not any(o.outcome == "candidate" for o in rows):
            p["status"], p["why"] = "verloren", f"wijst naar {key}"
        elif any(o.outcome == "candidate" for o in rows):
            p["status"], p["why"] = "voor de scheidsrechter", ""
        else:
            p["status"], p["why"] = "bestaat al", ""
    return out


def threshold(outcomes: list[Outcome], verdicts: dict | None = None, keys: set | None = None) -> dict:
    """Decision 25 as decisions 33 and 35 count it: products, not rows, after the verdict of the
    referee. Parked: by the gate or the referee, for another reason than a closed source. Over the
    products proposed; what the find round set aside does not count. Without the verdict of every
    candidate the count is provisional and says nothing about going on."""
    ps = products(outcomes, verdicts, keys if keys is not None else set())
    proposed = [p for p in ps.values() if p["status"] != "apart"]
    parked = sum(1 for p in proposed if p["status"] == "geparkeerd")
    final = verdicts is not None and not any(p["status"] == "voor de scheidsrechter" for p in proposed)
    return {"parked": parked, "proposed": len(proposed), "aside": len(ps) - len(proposed), "rows": len(outcomes),
            "products": len(ps), "final": final, "stop": final and parked * 3 > len(proposed)}


def tr5_new_publishers(outcomes: list[Outcome], verdicts: dict | None, keys: set) -> list:
    """Decision 35, point 3: new publishers whose proposal the referee parked on TR5, with its reason."""
    out = []
    for key, p in products(outcomes, verdicts, keys).items():
        v = next((v for v in (verdicts or {}).get("verdicts", []) if v["id"] == f"products/{key}.yaml"), None)
        if p["new"] and v and v["outcome"] == "parked" and "TR5" in (v.get("rule") or ""):
            out.append({"publisher": p["publisher"], "product": key, "reason": (v.get("reason") or "").strip()})
    return out


def count_lines(outcomes: list[Outcome], verdicts: dict | None, keys: set) -> list[str]:
    ps = products(outcomes, verdicts, keys)
    t = threshold(outcomes, verdicts, keys)
    rows, prods = Counter(), Counter()
    for p in ps.values():
        prods[p["status"]] += 1
        rows[p["status"]] += len(p["rows"])
    lines = ["| uitkomst per product | wat | rijen | producten |", "| --- | --- | --- | --- |"]
    for status, what in STATUS:
        if prods[status]:
            lines.append(f"| {status} | {what} | {rows[status]} | {prods[status]} |")
    lines.append(f"| **samen** | | **{t['rows']}** | **{t['products']}** |")
    share = f"{t['parked']} van {t['proposed']} voorgedragen producten geparkeerd om een andere reden dan een gesloten bron"
    if t["final"]:
        lines += ["", f"**Drempel, na het oordeel van de scheidsrechter:** {share} "
                      f"({'meer' if t['stop'] else 'niet meer'} dan een derde): **{'stop' if t['stop'] else 'de ronde gaat door'}**."]
    else:
        lines += ["", f"**Voorlopige stand, vóór het oordeel van de scheidsrechter:** {share}. "
                      "Geen uitspraak over doorgaan of stoppen: die volgt na het oordeel (`importer.py count`)."]
    lines += ["", f"Apart gezet door de vindronde of het groeperen (telt niet mee): {t['aside']} producten."]
    # Proposed after the grouping doubted: every row it doubted, but those parked for want of pages.
    doubted = {key: p for key, p in ps.items() if any(o.doubt and o.reason != "grouping" for o in p["rows"])}
    if doubted:
        made = Counter(p["status"] for p in doubted.values())
        lines += ["", f"**Voorgedragen na twijfel bij het groeperen** (TAX-44): {len(doubted)} producten; "
                      + ", ".join(f"{s}: {n}" for s, n in made.most_common())]
        lines += [f"- {key}: {p['status']}" + (f" ({p['why'][:160]})" if p["why"] else "") for key, p in sorted(doubted.items())]
    moves = [(o, a, e) for o in outcomes for a, e in o.moves]
    if moves:
        lines += ["", f"**Eindadres voorgedragen** (TAX-42): {len(moves)} bronnen"]
        lines += [f"- {o.row.where} {o.key}: {a} -> {e}" for o, a, e in moves]
    tr5 = tr5_new_publishers(outcomes, verdicts, keys)
    if verdicts is not None:
        lines += ["", f"**Nieuwe uitgevers geparkeerd op TR5** (besluit 35): {len(tr5)}"]
        lines += [f"- {c['publisher']} ({c['product']}): {c['reason']}" for c in tr5]
    lines += ["", "| product | rijen | uitkomst | reden |", "| --- | --- | --- | --- |"]
    for key, p in sorted(ps.items()):
        lines.append(f"| {key} | {', '.join(str(o.row.line) for o in p['rows'])} | {p['status']} | {p['why'][:200]} |")
    return lines


def load_outcomes(path: Path) -> list[Outcome]:
    return [Outcome(**{**o, "row": Row(**o["row"])}) for o in json.loads(Path(path).read_text(encoding="utf-8"))]


def final_report(outcomes: list[Outcome], verdicts: dict, keys: set) -> str:
    return "\n".join(["# KEV-ronde, telling na het oordeel", "",
                      f"Scheidsrechter: {verdicts.get('counts')}, kosten ${verdicts.get('cost', 0):.4f}.", "",
                      *count_lines(outcomes, verdicts, keys)]) + "\n"


def report(outcomes: list[Outcome], publishers: dict, files: dict, grouping: dict | None, fetched: bool,
           keys: set | None = None) -> str:
    lines = ["# Importer, droge run", "",
             f"- rijen: {len(outcomes)}; pagina's gelezen: {'ja' if fetched else 'nee'}",
             f"- bestanden voorgedragen: {len(files)} "
             f"({sum(1 for f in files if f.startswith('products/'))} product, {sum(1 for f in files if f.startswith('publishers/'))} uitgever)", "",
             "| uitkomst | reden | aantal |", "| --- | --- | --- |"]
    for (what, why), n in sorted(tally(outcomes).items()):
        lines.append(f"| {what} | {why} | {n} |")
    lines += ["", *count_lines(outcomes, None, keys if keys is not None else set()), ""]
    pending = sum(1 for o in outcomes if o.outcome == "candidate")
    lines += [f"Kosten van de scheidsrechter: {pending} voordrachten x ${COST_PER_PROPOSAL} = ${pending * COST_PER_PROPOSAL:.2f}."]
    if grouping:
        lines += [f"Kosten van het groeperen: {grouping['publishers']} uitgevers, {grouping['names']} namen, "
                  f"±${grouping['expected']:.2f} (hoogstens ${grouping['ceiling']:.2f})."]
    lines += ["", "| rij | lijst | naam | uitkomst | reden |", "| --- | --- | --- | --- | --- |"]
    for o in outcomes:
        r = o.row
        what = o.outcome + (f" van {o.of}" if o.of else "")
        why = o.label if o.outcome in ("parked", "alias") else o.reason
        lines.append(f"| {r.where} | {r.vendor}: {r.listed} | {r.name} | {what} | {why}{'; ' + o.detail if o.detail else ''} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- the grouping

def grouping_candidates(rows: list[Row], kev: dict | None, cve_dir: Path | None, since: str, data_root: Path = REPO):
    """Candidates for tools/group.py: the names of the found rows and, of the list, the vendors those
    rows name. The mapping sends a vendor of the list to the publisher the finder found for it."""
    index = group.publisher_index(data_root)
    mapping: dict = {}
    votes: dict = {}
    out = []
    for r in rows:
        pub, _ = publisher_of(r, index)
        if not pub:
            continue
        votes.setdefault(r.vendor, Counter())[pub] += 1
        if r.name:
            out.append({"publisher": pub, "name": r.name, "origin": "found"})
    for vendor, c in votes.items():
        mapping[vendor] = c.most_common(1)[0][0]
    for key in set(mapping.values()):
        mapping[key] = key
    if kev is not None and cve_dir is not None:
        for c in group.from_kev(kev, cve_dir, since):
            if c["publisher"] in mapping:
                out.append(c)
    return out, mapping


def as_cases(groups: list) -> list:
    return [group.Case(id=g.publisher, group=g, expect={}, status="proposed") for g in groups]


def classes_by_name(groups: list, classes: dict) -> dict:
    """publisher -> {key part of a name: {"class", "of"}}, with `of` an existing key or a name."""
    out = {}
    for g in groups:
        names = {n.id: n for n in g.names}
        mine = out.setdefault(g.publisher, {})
        for i, d in classes.get(g.publisher, {}).items():
            n = names.get(i)
            if n is None:
                continue
            of = d.get("of")
            of = names[of].name if of in names else of
            for spelling in [n.name, *n.spellings]:
                mine[group.part(spelling)] = {"class": d["class"], "of": of, "reason": d.get("reason") or ""}
    return out


def with_reasons(classes: dict, names: Path, results: Path) -> dict:
    """classes.json of a run before TAX-44 holds no reason: take it from the run of the grouping
    (results.jsonl, one line per publisher with its classes by name id) and names.json."""
    reasons: dict = {}
    for line in Path(results).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            reasons[r["id"]] = {i: d.get("reason") or "" for i, d in (r.get("classes") or {}).items()}
    for g in json.loads(Path(names).read_text(encoding="utf-8"))["groups"]:
        mine = classes.get(g["publisher"]) or {}
        for n in g["names"]:
            why = (reasons.get(g["publisher"]) or {}).get(n["id"])
            for spelling in [n["name"], *(n.get("spellings") or [])]:
                d = mine.get(group.part(spelling))
                if why and d is not None and not d.get("reason"):
                    d["reason"] = why
    return classes


def grouping_requests(groups: list, decided: dict, system: str, schema: dict, model: str, effort: str):
    """([(custom id, request)], custom id -> publisher) for the publishers code did not settle. The
    custom id is a number, not the publisher: a publisher key may hold any letter (a name without a
    Latin key) or be longer than the Message Batches API takes."""
    asked = [g for g in groups if len(decided[g.publisher]) < len(g.names)]
    requests = [(f"g{n}", group.request(g, decided[g.publisher], system, schema, model, effort))
                for n, g in enumerate(asked)]
    return requests, {f"g{n}": g.publisher for n, g in enumerate(asked)}


def run_grouping(args, client=None, data_root: Path = REPO, now=None, log=print, sleep=None) -> dict:
    rows = first(read_found(args.found), args.first)
    kev = json.loads(Path(args.kev).read_text(encoding="utf-8")) if args.kev else None
    candidates, mapping = grouping_candidates(rows, kev, Path(args.cve) if args.cve else None, args.since, data_root)
    groups = group.collect(candidates, data_root, mapping)
    system = referee.load_system_prompt(Path(args.prompt))
    schema = group.load_schema(Path(args.schema))
    prices = referee.load_prices(Path(args.prices))
    est = group.estimate(as_cases(groups), system, args.model, prices, 1, args.max_tokens)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "names.json").write_text(json.dumps(group.groups_json(groups), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    decided = {g.publisher: group.literal(g) for g in groups}
    log(f"{est['publishers']} publishers, {est['names']} names, {sum(len(d) for d in decided.values())} decided by code; "
        f"one batch on {args.model} at {args.effort}: about ${est['expected']:.2f}, at most ${est['ceiling']:.2f}")
    result = {"estimate": est, "groups": len(groups)}
    if not args.live:
        classes = {pub: d for pub, d in decided.items()}
        (out / "classes.json").write_text(json.dumps(classes_by_name(groups, classes), indent=1, ensure_ascii=False) + "\n",
                                          encoding="utf-8")
        return result
    client = client or referee.AnthropicClient()
    runs = Path(args.runs)
    spent = referee.spent_before(runs)
    # The tightest of two: what is left of the budget for every run together, and the cap of this run.
    room = max(min(args.budget - spent, args.cap if args.cap is not None else args.budget), 0.0)
    log(f"budget ${args.budget:.2f} (spent ${spent:.4f}); cap of this run "
        f"{'none' if args.cap is None else f'${args.cap:.2f}'}: at most ${room:.4f}")
    budget = referee.Budget(spent + room, spent, args.model, prices, max_tokens=args.max_tokens, batch=True)
    requests, by_id = grouping_requests(groups, decided, system, schema, args.model, args.effort)
    answered, never_ids = referee.batch_rounds(client, budget, requests, log=log, wait=args.wait, sleep=sleep)
    answers = {by_id[cid]: a for cid, a in answered.items()}
    never = [by_id[cid] for cid in never_ids]
    classes, cost, notes = {}, 0.0, {}
    for g in groups:
        a = answers.get(g.publisher)
        if a is None and len(decided[g.publisher]) == len(g.names):
            classes[g.publisher] = decided[g.publisher]
            continue
        text = a["text"] if a and not a["error"] else ""
        classes[g.publisher], notes[g.publisher] = group.check_answer(g, text, schema, decided[g.publisher])
        cost += a["cost"] if a else 0.0
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    run_dir = runs / f"{stamp}-import-group-{args.model}-{args.effort}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps({"kind": "import-group", "model": args.model, "effort": args.effort,
                                                  "batch": True, "budget": args.budget, "cap": args.cap,
                                                  "spent_before": spent,
                                                  "time": stamp}, indent=2) + "\n", encoding="utf-8")
    (run_dir / "results.jsonl").write_text("".join(
        json.dumps({"id": g.publisher, "classes": classes[g.publisher], "notes": notes.get(g.publisher, []),
                    "cost": (answers.get(g.publisher) or {}).get("cost", 0.0),
                    "usage": (answers.get(g.publisher) or {}).get("usage", referee.empty_usage())}, ensure_ascii=False) + "\n"
        for g in groups), encoding="utf-8")
    (out / "classes.json").write_text(json.dumps(classes_by_name(groups, classes), indent=1, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    log(f"grouping: {len(answers)} answered, {len(never)} without room in the budget, ${cost:.4f} -> {run_dir}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
            gh.write(f"run_dir={run_dir}\nrun_name={run_dir.name}\ncost={cost:.6f}\n")
    return {**result, "cost": cost, "dir": run_dir, "never": never}


# --------------------------------------------------------------------------- the list

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CVE_URL = "https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/{year}/{bucket}/{cve}.json"
CVE_ID = re.compile(r"^CVE-(\d{4})-(\d{4,})$")


def _download(url: str) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "TAXNMY-importer/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - two fixed public hosts
        return resp.read()


def cve_url(cve: str) -> str:
    m = CVE_ID.fullmatch(cve)
    if not m:
        raise ValueError(f"not a CVE id: {cve!r}")
    return CVE_URL.format(year=m.group(1), bucket=m.group(2)[:-3] + "xxx", cve=cve)


def fetch_list(args, get=_download, log=print) -> dict:
    """The KEV list, and the CVE record of each of its entries whose vendor a row names. A record
    that cannot be fetched is left out: the grouping then has the name KEV writes only."""
    rows = first(read_found(args.found), args.first)
    vendors = {r.vendor for r in rows if r.vendor}
    out = Path(args.out)
    (out / "cve").mkdir(parents=True, exist_ok=True)
    kev = json.loads(get(KEV_URL))
    (out / "kev.json").write_text(json.dumps(kev), encoding="utf-8")
    wanted = sorted({e["cveID"] for e in kev.get("vulnerabilities", [])
                     if e.get("vendorProject") in vendors and e.get("dateAdded", "") >= args.since})
    missing = []
    for cve in wanted:
        try:
            body = get(cve_url(cve))
            json.loads(body)
        except Exception as exc:  # noqa: BLE001 - a missing record is reported, not fatal
            missing.append(f"{cve} ({type(exc).__name__})")
            continue
        (out / "cve" / f"{cve}.json").write_bytes(body)
    log(f"KEV {kev.get('catalogVersion', '?')}: {len(wanted)} records for {len(vendors)} vendors, "
        f"{len(wanted) - len(missing)} fetched" + (f"; missing: {', '.join(missing)}" if missing else ""))
    return {"wanted": wanted, "missing": missing}


# --------------------------------------------------------------------------- propose

def propose(args, fetcher=None, data_root: Path = REPO, now=None, log=print) -> dict:
    rows = first(read_found(args.found), args.first)
    classes = json.loads(Path(args.classes).read_text(encoding="utf-8")) if args.classes else {}
    if getattr(args, "group_results", None):
        if not args.names:
            raise SystemExit("--group-results needs --names")
        classes = with_reasons(classes, Path(args.names), Path(args.group_results))
    outcomes = plan(rows, data_root, classes)
    if lost := dangling(outcomes, existing_keys(data_root)):
        raise SystemExit("alias of a product that is neither in TAX nor proposed (TAX-39): "
                         + ", ".join(f"{o.row.where} -> {o.of}" for o in lost))
    if lost := orphans(outcomes, existing_keys(data_root)):
        raise SystemExit("edition or series of a product that is neither in TAX nor proposed (TAX-42): "
                         + ", ".join(f"{o.row.where} -> {o.of}" for o in lost))
    extra = add_grouped_aliases(outcomes, classes)
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    items = []
    if args.fetch:
        if fetcher is None:
            from fetch import Fetcher
            recorder = Recorder()
            fetcher = Fetcher(use_browser=args.browser, get=recorder, log=log)
            fetcher.final = recorder.final
        publishers = check_pages(outcomes, fetcher, getattr(fetcher, "final", {}))
        follow(outcomes)
    else:
        publishers = publisher_sources(outcomes)
    files = files_of(outcomes, publishers, extra, data_root)
    if len(files) > pr_referee.MAX_FILES:
        raise SystemExit(f"{len(files)} files; at most {pr_referee.MAX_FILES} per pull request: use --first to split")
    if args.fetch:
        # The gate as in the workflow: a forecast for the report. The files stay in the pull request,
        # where the gate settles them again and the referee judges what it leaves.
        items = through_gate(files, fetcher, data_root, log=log, submitter=getattr(args, "submitter", "") or "")
        apply_gate(outcomes, items)
        mark_doubt(outcomes, items)
        follow(outcomes)
        if hasattr(fetcher, "close"):
            fetcher.close()
    grouping = None
    if args.names:
        groups = [group.Group(**{**g, "names": [group.Name(**n) for n in g["names"]]})
                  for g in json.loads(Path(args.names).read_text(encoding="utf-8"))["groups"]]
        grouping = group.estimate(as_cases(groups), referee.load_system_prompt(REPO / "referee/prompt-group.md"),
                                  group.MODEL, referee.load_prices(), 1, group.MAX_TOKENS)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    parked = parked_entries(outcomes, publishers, day)
    text = report(outcomes, publishers, files, grouping, bool(args.fetch), existing_keys(data_root))
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "outcomes.json").write_text(json.dumps([{**asdict(o), "row": asdict(o.row)} for o in outcomes], indent=1,
                                                  ensure_ascii=False) + "\n", encoding="utf-8")
    target = data_root if args.write else out / "data"
    for rel, t in files.items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(t, encoding="utf-8")
    parked_path = (data_root if args.write else out / "data") / "referee/parked.yaml"
    parked_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {e["key"]: e for e in pr_referee.load_parked(data_root / "referee/parked.yaml")}
    for e in parked:
        existing.setdefault(e["key"], e)
    pr_referee.save_parked(list(existing.values()), parked_path, pr_referee.load_terms(data_root))
    if args.gated and args.fetch:
        # For the referee of the pull request (tools/pr_referee.py judge): the items the gate left pending.
        gated = Path(args.gated)
        gated.parent.mkdir(parents=True, exist_ok=True)
        gated.write_text(json.dumps({"version": 1, "pr": None, "head_sha": "", "fork": False, "items": items},
                                    ensure_ascii=False) + "\n", encoding="utf-8")
    log(text.split("\n| rij |")[0])
    return {"outcomes": outcomes, "files": files, "parked": parked, "items": items, "report": text}


# --------------------------------------------------------------------------- the command line

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("group", "propose"):
        s = sub.add_parser(name)
        s.add_argument("--found", nargs="+", required=True, help="CSV files of the find round, in order")
        s.add_argument("--first", type=int, help="only the first N rows (the trial: 40)")
        s.add_argument("--out", required=True, help="directory for the report and, without --write, the files")
    g = sub.choices["group"]
    g.add_argument("--kev", help="the KEV list of CISA (known_exploited_vulnerabilities.json)")
    g.add_argument("--cve", help="a folder of CVE records, one CVE-YYYY-N.json per entry")
    g.add_argument("--since", default="", help="only list entries added on or after this date (YYYY-MM-DD)")
    g.add_argument("--prompt", default=str(referee.REFEREE / "prompt-group.md"))
    g.add_argument("--schema", default=str(referee.REFEREE / "group.schema.json"))
    g.add_argument("--prices", default=str(referee.REFEREE / "prices.yaml"))
    g.add_argument("--runs", default=str(referee.REFEREE / "runs"))
    g.add_argument("--model", default=group.MODEL)
    g.add_argument("--effort", default=group.EFFORT)
    g.add_argument("--max-tokens", type=int, default=group.MAX_TOKENS)
    g.add_argument("--budget", type=float, default=referee.BUDGET, help="dollars, for every run together")
    g.add_argument("--cap", type=float, help="dollars, for this run (the trial: 1.00)")
    g.add_argument("--wait", type=float, default=referee.BATCH_WAIT)
    g.add_argument("--live", action="store_true", help="send the grouping as one batch (costs money)")
    q = sub.choices["propose"]
    q.add_argument("--classes", help="classes.json of the group step")
    q.add_argument("--names", help="names.json of the group step, for the estimate of the grouping")
    q.add_argument("--group-results", help="results.jsonl of the run of the grouping, with --names: its reasons, "
                                           "for a classes.json that holds none (before TAX-44)")
    q.add_argument("--fetch", action="store_true", help="read every page, and run the gate of the pull request (free)")
    q.add_argument("--write", action="store_true", help="write the files and the parking list into the data")
    q.add_argument("--browser", action="store_true", help="with --fetch: a headless browser for pages without text")
    q.add_argument("--gated", help="with --fetch: write the gated items for tools/pr_referee.py judge here")
    q.add_argument("--submitter", default="", help="the submitter of an agreed round in referee/intake.yaml (TAX-32)")
    c = sub.add_parser("count", help="the count per product after the verdict of the referee (TAX-39; free)")
    c.add_argument("--outcomes", required=True, help="outcomes.json of the propose step")
    c.add_argument("--verdicts", required=True, help="verdicts.json of tools/pr_referee.py judge")
    c.add_argument("--out", help="write the report here; else print it")
    k = sub.add_parser("list", help="the KEV list and the CVE records of the vendors of the rows (public, free)")
    k.add_argument("--found", nargs="+", required=True)
    k.add_argument("--first", type=int)
    k.add_argument("--since", default="")
    k.add_argument("--out", required=True, help="writes kev.json and cve/CVE-YYYY-N.json")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "group":
        if args.live and not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("--live needs ANTHROPIC_API_KEY")
        run_grouping(args)
    elif args.command == "count":
        text = final_report(load_outcomes(Path(args.outcomes)),
                            json.loads(Path(args.verdicts).read_text(encoding="utf-8")), existing_keys(REPO))
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
    elif args.command == "list":
        fetch_list(args)
    else:
        propose(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
