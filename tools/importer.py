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
    the grouping     optional: classes.json of `importer.py group --live`; a name classed series,
                     edition or doubt is parked, an alias is added to the product it names

Per found row, code decides one outcome, in this order:

    parked     series            the row is more than one product, or a part
               edition           the row is an edition or a version of another product
               not-found         no name, no product page, or no publisher
               grouping          the grouping classed the name series, edition or doubt
    alias      same-row          an earlier row gives the same key
               existing          TAX has the product already (by key, alias or name, TV2)
               grouping          the grouping classed the name alias of another product
    parked     no-kind           no kind
               no-versions       no versions page
               no-publisher-page a new publisher, and no page of its own names it (TV4, decision 28)
               closed            a source refuses bots (401, 403, 429, robots.txt); never worked around
               unreadable        a source gives no text (404, an error, too little text)
               name-not-on-page  the product page, after its redirects, does not show the name
    candidate                    product file (and publisher file) for the gate and the referee

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
# What the find round itself set aside as no product or without a versions page (decision 33): apart
# in the count, and not counted for the threshold of the trial.
FOUND_ASIDE = {"series", "edition", "not-found", "no-versions"}

# reason -> (what the report says, what the parked entry waits for)
PARK = {
    "series": ("reeks of onderdeel", "better-proposal"),
    "edition": ("versie of editie", "better-proposal"),
    "not-found": ("niet gevonden: geen naam, productpagina of uitgever", "better-proposal"),
    "grouping": ("het groeperen: reeks, editie of twijfel", "better-proposal"),
    "no-kind": ("geen kind", "better-proposal"),
    "no-versions": ("geen versions.source", "better-proposal"),
    "no-publisher-page": ("nieuwe uitgever zonder eigen pagina met zijn naam", "better-proposal"),
    "closed": ("gesloten bron", "event"),
    "unreadable": ("bron niet leesbaar", "event"),
    "name-not-on-page": ("naam niet op de productpagina", "better-proposal"),
    "moved": ("bron verwijst door naar een ander adres", "better-proposal"),
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
    out, taken = [], {}  # taken: key -> the outcome of the candidate that holds it
    for row in rows:
        pub, known = publisher_of(row, index)
        listed_key = product_key(pub, row.listed) if pub and row.listed else ""
        key = product_key(pub, row.name) if pub and row.name else ""
        o = Outcome(row=row, outcome="parked", key=key or listed_key, publisher=pub, new_publisher=bool(pub) and not known)
        sorted_as = (classes.get(pub) or {}).get(group.part(row.name)) if row.name else None
        if NOTE_SERIES.match(row.note):
            o.reason, o.key = "series", listed_key or key
        elif m := NOTE_EDITION.match(row.note):
            o.reason, o.key, o.detail = "edition", listed_key or key, f"van {m.group(1)}"
        elif not (row.name and row.source and key and (known or row.publisher)):
            # A new publisher needs the finder's name for it: the vendor of the list is no source.
            o.reason = "not-found"
            o.detail = ", ".join(w for w, v in (("naam", row.name), ("productpagina", row.source),
                                                ("uitgever", known or row.publisher)) if not v)
        elif sorted_as and sorted_as["class"] in ("series", "edition", "doubt"):
            o.reason, o.detail = "grouping", sorted_as["class"]
        elif key in taken:
            o.outcome, o.reason, o.of = "alias", "same-row", key
            o.detail = f"rij {taken[key].row.where}" + (f"; {row.note}" if NOTE_SAME.match(row.note) else "")
        elif (hit := (have.get(pub) or {}).get(key.split("/", 1)[1]) or (have.get(pub) or {}).get(group.part(row.name))):
            o.outcome, o.reason, o.of = "alias", "existing", hit
        elif sorted_as and sorted_as["class"] == "alias" and sorted_as.get("of"):
            o.outcome, o.reason, o.of = "alias", "grouping", sorted_as["of"]
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
            taken[key] = o
        out.append(o)
    return out


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
        if o.outcome == "alias" and o.reason == "same-row":
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
            return "moved", f"{url} -> {end}"
        return None

    for o in outcomes:
        if o.outcome != "candidate":
            continue
        for url in dict.fromkeys([o.row.source, o.row.versions]):
            if p := problem(url):
                o.outcome, (o.reason, o.detail) = "parked", p
                break
        else:
            text = fetcher.fetch(o.row.source, "").text
            if not gate.appears(o.row.name, text):
                o.outcome, o.reason = "parked", "name-not-on-page"
                moved = final.get(o.row.source)
                o.detail = f"{o.row.name!r} niet op {o.row.source}" + (f" (omgeleid naar {moved})" if moved else "")

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

def through_gate(files: dict, fetcher, data_root: Path = REPO, log=print) -> list[dict]:
    """The gate of the pull request, as in the workflow (no key): one item per proposal."""
    return pr_referee.gate_proposals(pr_referee.group(files), fetcher, data_root, log=log)


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


# --------------------------------------------------------------------------- the report

def tally(outcomes: list[Outcome]) -> dict:
    by = Counter()
    for o in outcomes:
        if o.outcome == "candidate":
            by[("opgenomen-kandidaat", "voor de scheidsrechter")] += 1
        elif o.outcome == "alias":
            by[("alias", ALIAS[o.reason])] += 1
        elif found_aside(o):
            by[("apart (vindronde)", PARK[o.reason][0])] += 1
        elif o.outcome == "parked":
            by[("geparkeerd", PARK[o.reason][0])] += 1
        else:
            by[(o.outcome, o.reason.split(":")[0])] += 1
    return by


def found_aside(o: Outcome) -> bool:
    """The find round itself showed this row is no product or has no versions page: it is not proposed."""
    return o.outcome == "parked" and o.reason in FOUND_ASIDE


def threshold(outcomes: list[Outcome]) -> tuple[int, int, int, bool]:
    """(parked for another reason than a closed source, rows proposed, rows set aside by the find round,
    the round stops). Decision 25, as decision 33 counts it: only over what is proposed; what the find
    round set aside and closed sources do not count."""
    proposed = [o for o in outcomes if not found_aside(o)]
    parked = sum(1 for o in proposed if (o.outcome == "parked" and o.reason != "closed") or o.outcome == "gate-parked")
    return parked, len(proposed), len(outcomes) - len(proposed), parked * 3 > len(proposed)


def report(outcomes: list[Outcome], publishers: dict, files: dict, grouping: dict | None, fetched: bool) -> str:
    lines = ["# Importer, droge run", "",
             f"- rijen: {len(outcomes)}; pagina's gelezen: {'ja' if fetched else 'nee'}",
             f"- bestanden voorgedragen: {len(files)} "
             f"({sum(1 for f in files if f.startswith('products/'))} product, {sum(1 for f in files if f.startswith('publishers/'))} uitgever)", "",
             "| uitkomst | reden | aantal |", "| --- | --- | --- |"]
    for (what, why), n in sorted(tally(outcomes).items()):
        lines.append(f"| {what} | {why} | {n} |")
    parked, proposed, aside, stop = threshold(outcomes)
    lines += ["", f"Apart gezet door de vindronde (telt niet mee): {aside}. "
                  f"Geparkeerd om een andere reden dan een gesloten bron: {parked} van {proposed} voorgedragen "
                  f"({'meer' if stop else 'niet meer'} dan een derde: de ronde {'stopt' if stop else 'gaat door'}).", ""]
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
                mine[group.part(spelling)] = {"class": d["class"], "of": of}
    return out


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
    requests = [(g.publisher, group.request(g, decided[g.publisher], system, schema, args.model, args.effort))
                for g in groups if len(decided[g.publisher]) < len(g.names)]
    answers, never = referee.batch_rounds(client, budget, requests, log=log, wait=args.wait, sleep=sleep)
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
    outcomes = plan(rows, data_root, classes)
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
        items = through_gate(files, fetcher, data_root, log=log)
        apply_gate(outcomes, items)
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
    text = report(outcomes, publishers, files, grouping, bool(args.fetch))
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
    q.add_argument("--fetch", action="store_true", help="read every page, and run the gate of the pull request (free)")
    q.add_argument("--write", action="store_true", help="write the files and the parking list into the data")
    q.add_argument("--browser", action="store_true", help="with --fetch: a headless browser for pages without text")
    q.add_argument("--gated", help="with --fetch: write the gated items for tools/pr_referee.py judge here")
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
    elif args.command == "list":
        fetch_list(args)
    else:
        propose(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
