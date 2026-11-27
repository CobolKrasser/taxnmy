#!/usr/bin/env python3
"""Two kinds of reference read in once (TAX-68): the GUID of LVFS and the page name of endoflife.

A product of LVFS (its `source` is the page of LVFS for one id) carries every GUID that LVFS names at that
id: the GUID a device reports to fwupd itself (`<provides><firmware type="flashed">`). A product that took in
another product of LVFS as an alias (`folded` in referee/lvfs.yaml) carries the GUIDs of that id as well. A
GUID that LVFS names at more than one id points to no single product: it goes onto no product and is listed. So
does a GUID that would go onto more than one product, as when one id is the source of several products (TAX-68,
comment 12535).
The source of a GUID is the page of LVFS for its id.

A product of endoflife (its `source` is a page of endoflife) carries the name of that page, when the page is
one product: its category is not a line of devices (`lines` in referee/endoflife.yaml). A page of a line
names many models and so no product. The source of the name is the page.

The round only adds: an identifier the file has stays as it is, and so do key, aliases and every other
field. It fetches nothing; it reads the files a maintainer gives it.

    python tools/references.py --metadata firmware.xml.zst --dump full.json --out OUT [--write]
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import endoflife  # noqa: E402
import lvfs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


TOP = re.compile(r"^[A-Za-z_]+:")


def insert(text: str, new: list[dict]) -> str:
    """The file with the new identifiers after the last one it has, as lines: every other line stays as it is.
    A file without identifiers gets the list after `versions` (the order of the schema)."""
    item = "".join(yaml.safe_dump([i], sort_keys=False, allow_unicode=True, width=200) for i in new)
    lines = text.splitlines(keepends=True)
    heads = [n for n, line in enumerate(lines) if line.startswith("identifiers:")]
    if heads and lines[heads[-1]].rstrip() != "identifiers: []":
        end = heads[-1] + 1
        while end < len(lines) and not TOP.match(lines[end]):
            end += 1
        return "".join(lines[:end]) + item + "".join(lines[end:])
    if heads:  # `identifiers: []`
        n = heads[-1]
        return "".join(lines[:n]) + "identifiers:\n" + item + "".join(lines[n + 1:])
    end = next(n for n, line in enumerate(lines) if line.startswith("versions:")) + 1
    while end < len(lines) and not TOP.match(lines[end]):
        end += 1
    return "".join(lines[:end]) + "identifiers:\n" + item + "".join(lines[end:])


def guids(metadata: bytes) -> dict[str, set]:
    """id -> the GUIDs LVFS names at it, from every component with that id."""
    root = ET.fromstring(lvfs.decompress(metadata))
    out: dict[str, set] = defaultdict(set)
    for c in root.iter("component"):
        ident = (c.findtext("id") or "").strip()
        for provides in c.iter("provides"):
            for fw in provides.iter("firmware"):
                g = (fw.text or "").strip().lower()
                if fw.get("type") == "flashed" and ident and GUID.match(g):
                    out[ident].add(g)
    return dict(out)


def shared(by_id: dict[str, set]) -> dict[str, list]:
    """GUID -> the ids it is named at, for a GUID LVFS names at more than one id."""
    ids: dict[str, set] = defaultdict(set)
    for ident, gs in by_id.items():
        for g in gs:
            ids[g].add(ident)
    return {g: sorted(i) for g, i in sorted(ids.items()) if len(i) > 1}


def single_pages(dump: dict, eol: dict) -> set:
    """The pages of endoflife that are one product: their category is not a line of devices."""
    lines = set(eol.get("lines") or [])
    return {p["name"] for p in dump["result"] if p.get("category") not in lines}


def _pattern(template: str, group: str) -> re.Pattern:
    head, tail = template.split("{" + group + "}")
    return re.compile("^" + re.escape(head) + "(?P<v>[^/?#]+)" + re.escape(tail) + "$")


def plan(root: Path, by_id: dict[str, set] | None, pages: set | None, lv: dict | None = None) -> dict:
    """What each product gets. {"add": {file: (identifiers, text)}, "withheld": {key: [(guid, ids)]},
    "several": {guid: [keys]}, counts}. A GUID that would go onto more than one product (an id that is the
    source of several products) points to no single product either: it goes onto none and is listed."""
    lv = lv or lvfs.load_list()
    page_lvfs = lv["page"]
    is_lvfs = _pattern(page_lvfs, "id")
    is_eol = _pattern(endoflife.PAGE, "slug")
    folded = lv.get("folded") or {}
    many = shared(by_id) if by_id is not None else {}
    add: dict[str, list] = {}
    withheld: dict[str, list] = defaultdict(list)
    counts = defaultdict(int)
    planned = []
    owners: dict[str, set] = defaultdict(set)  # GUID -> the products that carry it or would get it
    for f in sorted((root / "products").glob("*/*.yaml")):
        text = f.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        have = {(k, str(v)) for i in data.get("identifiers") or [] for k, v in i.items() if k != "source"}
        for k, v in have:
            if k == "lvfs":
                owners[v].add(data["key"])
        new = []
        source = str(data.get("source", ""))
        m = is_lvfs.match(source)
        of_lvfs = bool(m and by_id is not None)
        if of_lvfs:
            counts["lvfs products"] += 1
            ids = [m.group("v")] + [i for a, i in folded.items() if a in (data.get("aliases") or [])]
            for ident in dict.fromkeys(ids):
                for g in sorted(by_id.get(ident, ())):
                    if g in many:
                        withheld[data["key"]].append((g, many[g]))
                    elif ("lvfs", g) not in have and g not in {x["lvfs"] for x in new}:
                        new.append({"lvfs": g, "source": page_lvfs.format(id=ident)})
                        owners[g].add(data["key"])
        m = is_eol.match(source)
        if m and pages is not None:
            counts["endoflife products"] += 1
            slug = m.group("v")
            if slug in pages:
                counts["endoflife products of a page that is one product"] += 1
                if ("endoflife", slug) not in have:
                    new.append({"endoflife": slug, "source": source})
        planned.append((f, text, data, new, of_lvfs))
    several = {g: sorted(k) for g, k in sorted(owners.items()) if len(k) > 1}
    for f, text, data, new, of_lvfs in planned:
        new = [i for i in new if i.get("lvfs") not in several]
        if of_lvfs:
            counts["lvfs products with a guid"] += any("lvfs" in i for i in new)
        if new:
            after = insert(text, new)
            want = {**data, "identifiers": [*(data.get("identifiers") or []), *new]}
            removed = [d for d in difflib.ndiff(text.splitlines(), after.splitlines()) if d.startswith("- ")]
            if yaml.safe_load(after) != want or removed not in ([], ["- identifiers: []"]):
                raise SystemExit(f"{f}: the identifiers cannot be added without changing the file; it is left to a person")
            add[f.relative_to(root).as_posix()] = (new, after)
    return {"add": add, "withheld": dict(withheld), "several": several, "shared": many, "counts": dict(counts)}


def report(result: dict) -> str:
    c = result["counts"]
    adds = [i for new, _ in result["add"].values() for i in new]
    n_lvfs = sum("lvfs" in i for i in adds)
    n_eol = sum("endoflife" in i for i in adds)
    out = ["## Two kinds of reference (TAX-68)", "",
           f"{len(result['add']):,} products get a reference. No identifier is changed or taken out.", "",
           "| what | count |", "| --- | ---: |",
           f"| products of LVFS | {c.get('lvfs products', 0):,} |",
           f"| of which with at least one GUID | {c.get('lvfs products with a guid', 0):,} |",
           f"| GUIDs added | {n_lvfs:,} |",
           f"| GUIDs LVFS names at more than one id (all of LVFS) | {len(result['shared']):,} |",
           f"| of which at a product of TAX, withheld | {len({g for v in result['withheld'].values() for g, _ in v}):,} |",
           f"| GUIDs of one id that would go onto more than one product, withheld | {len(result['several']):,} |",
           f"| products of endoflife | {c.get('endoflife products', 0):,} |",
           f"| of which of a page that is one product | {c.get('endoflife products of a page that is one product', 0):,} |",
           f"| page names added | {n_eol:,} |", ""]
    if result["withheld"]:
        out += ["### GUIDs withheld: LVFS names them at more than one id", "", "| product | GUID | ids |", "| --- | --- | --- |"]
        out += [f"| {k} | {g} | {', '.join(ids)} |" for k, v in sorted(result["withheld"].items()) for g, ids in v]
        out += [""]
    if result["several"]:
        out += ["### GUIDs withheld: they would go onto more than one product", "", "| GUID | products |", "| --- | --- |"]
        out += [f"| {g} | {', '.join(keys)} |" for g, keys in result["several"].items()]
    return "\n".join(out) + "\n"


def run(root: Path, out: Path, metadata: bytes | None, dump: dict | None, write: bool = False,
        lv: dict | None = None, eol: dict | None = None) -> dict:
    """`lv` and `eol`: the lists of referee/lvfs.yaml and referee/endoflife.yaml (for a test)."""
    by_id = guids(metadata) if metadata is not None else None
    pages = single_pages(dump, eol or endoflife.load_list()) if dump is not None else None
    result = plan(root, by_id, pages, lv)
    out.mkdir(parents=True, exist_ok=True)
    target = root if write else out / "data"
    for rel, (_, text) in result["add"].items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text, encoding="utf-8")
    text = report(result)
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "withheld.json").write_text(json.dumps({"at more than one id": result["withheld"],
                                                   "onto more than one product": result["several"]}, indent=1) + "\n",
                                      encoding="utf-8")
    result["report"] = text
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--metadata", help="firmware.xml.zst (or .gz) as a fwupd client fetches it")
    p.add_argument("--dump", help="the full list of the API of endoflife (api/v1/products/full)")
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--write", action="store_true", help="write into the dataset (else OUT/data)")
    a = p.parse_args(argv)
    if not a.metadata and not a.dump:
        p.error("give --metadata, --dump or both")
    result = run(Path(a.root), Path(a.out), Path(a.metadata).read_bytes() if a.metadata else None,
                 json.loads(Path(a.dump).read_text(encoding="utf-8")) if a.dump else None, a.write)
    print(result["report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
