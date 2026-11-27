#!/usr/bin/env python3
"""LVFS as a filler (TAX-23): the devices of the AppStream metadata of LVFS become products in TAX, in one
round, with no AI call. The first filling by the maintainer, with only the basic logic of TAX-19.

The round reads the metadata that a fwupd client fetches (`fwupdmgr refresh`), as a local file; it
fetches nothing itself (Fred, 22 November 2026). Each component is a device (criterion 4), unless its
categories say it is a part of a device with its own firmware (outside TAX-23), a setting, or a category
the list does not know; those are set apart with that reason. A component whose category says it is the
firmware of the device its name names (a management engine, a TPM) becomes a product only when that
device is not in TAX or in the round yet. The page on which LVFS describes the device is the source of the product and of
its versions (criterion 2); its releases are its versions (TP3). The name and the publisher are as the
metadata writes them (`name`, `developer_name`, criterion 3); components that give the same key are one
product. A publisher TAX knows keeps its key and its source; a new one gets the page of LVFS as source.

The categories and the page are data in referee/lvfs.yaml (TG4). The writing of the files and the lint
are those of tools/csaf.py.

    python tools/lvfs.py --metadata firmware.xml.zst --out OUT [--write [--replace-rows]]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import csaf  # noqa: E402
import keyrule  # noqa: E402
import modelrow  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIST = ROOT / "referee/lvfs.yaml"
RULE = "a component of LVFS: a device"
ROW = "a row of models: the first model is the name and the key, the others are aliases (TAX-62)"


def load_list(path: Path = LIST) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise SystemExit(f"{path}: not version 1")
    return data


def decompress(metadata: bytes) -> bytes:
    """The metadata as XML: plain, gzip or zstd. The full metadata a current fwupd client reads is
    firmware.xml.zst; firmware.xml.gz is an extract for old clients."""
    if metadata[:2] == b"\x1f\x8b":
        metadata = gzip.decompress(metadata)
    elif metadata[:4] == b"\x28\xb5\x2f\xfd":
        try:
            from compression import zstd  # Python 3.14 and later
        except ImportError:
            raise SystemExit("reading firmware.xml.zst needs Python 3.14 or later (compression.zstd)") from None
        metadata = zstd.decompress(metadata)
    return metadata


def components(metadata: bytes) -> list[dict]:
    """(id, name, developer, categories) of every component of the metadata (see `decompress`)."""
    root = ET.fromstring(decompress(metadata))
    if root.tag != "components":
        raise SystemExit("no AppStream metadata")
    return [{"id": (c.findtext("id") or "").strip(), "name": (c.findtext("name") or "").strip(),
             "developer": (c.findtext("developer_name") or "").strip(),
             "categories": {x.text for x in c.iter("category") if x.text}} for c in root.iter("component")]


def plan(comps: list[dict], root: Path = ROOT, data: dict | None = None):
    """(items, products by key, new publishers by key, publishers by key: [] for the files)."""
    data = data or load_list()
    tax = csaf.Tax(root)
    devices, named, parts, settings = (set(data.get(k) or []) for k in ("devices", "named", "parts", "settings"))
    items: list[csaf.Item] = []
    products: dict[str, csaf.Product] = {}
    spellings: dict[str, Counter] = defaultdict(Counter)
    first: dict[str, str] = {}
    additions = data.get("row_additions") or []
    listed = data.get("rows") or {}  # rows written with a space or "and", read by a person or a model (TAX-62)
    names: dict[str, list] = defaultdict(list)
    for c in comps:
        names[c["developer"]].append(c["name"])
    series = {dev: modelrow.series_of(n, [dev], additions) for dev, n in names.items()}
    rows: dict[frozenset, str] = {}  # the models of a row -> its product
    aliases: dict[str, str] = {}  # the other models of the rows in the round -> their product
    # a component of `named` comes after the devices, so that it finds the device its name names; a row of
    # models comes after the single models, so that it finds a model the round already has (TAX-62)
    order = sorted(comps, key=lambda c: (bool(c["categories"] & named), modelrow.is_row(c["name"]) or c["name"] in listed,
                                         c["developer"], c["name"], len(c["id"]), c["id"]))
    for c in order:  # the shortest id is the source
        page = data["page"].format(id=c["id"])
        item = csaf.Item(page, c["developer"], c["name"])
        items.append(item)
        cats = c["categories"]
        unknown = sorted(cats - devices - named - parts - settings)
        if cats & settings:
            csaf._set(item, "apart", "a setting, no product (its category)")
            continue
        if cats & parts:
            csaf._set(item, "apart", "a part of a device with its own firmware, outside TAX-23 (its category)")
            continue
        if unknown or not cats:
            csaf._set(item, "apart", f"a category that referee/lvfs.yaml does not list: {', '.join(unknown) or 'none'}")
            continue
        if not c["developer"] or not c["name"] or not c["id"]:
            csaf._set(item, "apart", "the metadata gives no developer, no name or no id, so no key (TK2)")
            continue
        try:
            pub = tax.publisher(c["developer"]) or keyrule.publisher_part(c["developer"])
            if modelrow.is_row(c["name"]) or c["name"] in listed:
                found = _row(c["name"], pub, [c["developer"], pub], series.get(c["developer"], set()), additions,
                             listed.get(c["name"]))
                if isinstance(found, str):
                    csaf._set(item, "apart", found)
                    continue
                models, keys = found
                same = rows.get(frozenset(keys))
                if same:
                    products[same].items.append(item)
                    csaf._set(item, "taken-in", "names the same models as another row: one product (TAX-62)", same)
                    continue
                owners = {tax.has(k, m, []) for k, m in zip(keys, models)}
                if len(owners) == 1 and "" not in owners:
                    csaf._set(item, "in-tax", "TAX has its models as one product (TK3): no new file", owners.pop())
                    continue
                have = next((tax.has(k, m, []) or k for k, m in zip(keys, models)
                             if tax.has(k, m, []) or k in products or k in aliases), "")
                if have:
                    csaf._set(item, "apart", f"a row of models with a model TAX or the round already has: {have} (TAX-62)")
                    continue
                key, name = keys[0], models[0]
                products[key] = csaf.Product(key, pub, name, page, "device", ROW, [], [], [item], keys[1:])
                rows[frozenset(keys)] = key
                aliases.update((k, key) for k in keys[1:])
                csaf._set(item, "taken-in", ROW, key)
                _publisher(c, pub, page, tax, spellings, first)
                continue
            name = csaf.product_name(c["name"])
            key = f"{pub}/{keyrule.product_part(name, pub)}"
        except keyrule.KeyRuleError:
            csaf._set(item, "apart", "a name outside the Latin script, so no key (TK2)")
            continue
        have = tax.has(key, name, [])
        if have:
            csaf._set(item, "in-tax", "TAX has its key or its long form (TK3): no new file", have)
            continue
        if key in aliases:
            csaf._set(item, "apart", f"a model of a row of models in the round: {aliases[key]} (TAX-62)")
            continue
        if key in products:
            products[key].items.append(item)
            csaf._set(item, "taken-in", "the firmware of the device its name names (its category)" if cats & named
                      else "the same key as another component: one product (TP3)", key)
            continue
        products[key] = csaf.Product(key, pub, name, page, "device", RULE, [], [], [item])
        csaf._set(item, "taken-in", RULE, key)
        _publisher(c, pub, page, tax, spellings, first)
    new = {pub: {"name": n.most_common(1)[0][0], "source": first[pub]} for pub, n in spellings.items()}
    return items, products, new, {pub: [] for pub in new}


def _publisher(c: dict, pub: str, page: str, tax, spellings: dict, first: dict) -> None:
    if not tax.publisher(c["developer"]) and pub not in tax.census.publishers:
        spellings[pub][c["developer"]] += 1
        first.setdefault(pub, page)


def _row(name: str, pub: str, brands: list, series: set, additions: list, listed: list | None = None):
    """(the models, their keys) of a row of models, or the reason it is set apart (TAX-62). `listed`: the
    models of the row as referee/lvfs.yaml gives them."""
    models, why = (listed, "") if listed else modelrow.models(name, brands, series, additions)
    if why:
        return why
    by_key: dict[str, str] = {}
    for m in models:
        by_key.setdefault(f"{pub}/{keyrule.product_part(m, pub)}", m)  # one model per key, the first
    keys, models = list(by_key), list(by_key.values())
    bad = next((p for k in keys for p in keyrule.key_problems(k)), "")
    if bad:
        return f"a row of models: no valid key: {bad}"
    return models, keys


def report(items, products, publishers, signals, before: int, removed: int = 0) -> str:
    by = Counter(i.outcome for i in items)
    out = ["## LVFS as a filler (TAX-23)", "",
           f"{len(items):,} components. {len(products):,} devices and {len(publishers):,} new publishers. "
           f"No AI call; nothing fetched. {len(signals)} signals of the lint, for a person.", "",
           "| outcome | components |", "| --- | ---: |"]
    out += [f"| {o} | {by[o]:,} |" for o in ("taken-in", "in-tax", "apart")]
    if removed:
        out += ["", f"{removed:,} products of LVFS whose name is a row of models were taken out first, and made anew "
                "with the rule of TAX-62 where it can read them."]
    out += ["", f"Devices in TAX: {before:,} before, {before - removed + len(products):,} after.", "",
            "| publisher | devices |", "| --- | ---: |"]
    out += [f"| {p} | {n:,} |" for p, n in Counter(p.publisher for p in products.values()).most_common()]
    reasons = Counter((i.outcome, i.reason) for i in items if i.outcome != "taken-in" or i.reason != RULE)
    out += ["", "| outcome | reason | count |", "| --- | --- | ---: |"]
    out += [f"| {o} | {r} | {n:,} |" for (o, r), n in sorted(reasons.items(), key=lambda x: (x[0][0], -x[1]))]
    return "\n".join(out) + "\n"


def rows_in_tax(root: Path, data: dict) -> list[Path]:
    """The products of LVFS in TAX whose name is a row of models: written before the rule of TAX-62."""
    prefix = data["page"].split("{")[0]
    listed = data.get("rows") or {}
    out = []
    for f in sorted((root / "products").glob("*/*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        name = str(d.get("name", ""))
        if str(d.get("source", "")).startswith(prefix) and (modelrow.is_row(name) or name in listed):
            out.append(f)
    return out


def run(metadata: bytes, out: Path, root: Path = ROOT, write: bool = False, data: dict | None = None,
        replace_rows: bool = False) -> dict:
    """With `replace_rows` (and `write`), the products of LVFS whose name is a row of models are taken out of
    TAX first, and the round makes them anew with the rule (TAX-62): a product with a wrong key is thrown
    away and made anew, without the old key as an alias (the PO, 23 November 2026)."""
    data = data or load_list()
    before = csaf.devices(root)
    removed = []
    if replace_rows:
        if not write:
            raise SystemExit("--replace-rows takes products out of TAX: only with --write (on a copy for a dry run)")
        removed = [f.relative_to(root).as_posix() for f in rows_in_tax(root, data)]
        for rel in removed:
            (root / rel).unlink()
    items, products, publishers, addresses = plan(components(metadata), root, data)
    signals = [f for f in csaf.guard(products, publishers, addresses, root) if f.level == "signal"]
    files = csaf.files_of(products, publishers, addresses, root)
    out.mkdir(parents=True, exist_ok=True)
    target = root if write else out / "data"
    for rel, text in files.items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text, encoding="utf-8")
    text = report(items, products, publishers, signals, before, len(removed))
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "outcomes.json").write_text(json.dumps([asdict(i) for i in items], indent=0, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    with (out / "not-taken-in.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["page", "developer", "name", "outcome", "reason", "product"])
        w.writerows([i.address, i.vendor, i.name, i.outcome, i.reason, i.product] for i in items if i.outcome != "taken-in")
    if removed:
        (out / "removed.txt").write_text("".join(f"{r}\n" for r in removed), encoding="utf-8")
    return {"items": items, "products": products, "publishers": publishers, "files": files, "signals": signals,
            "report": text, "removed": removed}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--metadata", required=True, help="firmware.xml.zst (or .gz) as a fwupd client fetches it")
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--write", action="store_true", help="write into the dataset (else OUT/data)")
    p.add_argument("--replace-rows", action="store_true",
                   help="take the LVFS products whose name is a row of models out first, and make them anew (TAX-62)")
    a = p.parse_args(argv)
    got = run(Path(a.metadata).read_bytes(), Path(a.out), Path(a.root), a.write, replace_rows=a.replace_rows)
    print(got["report"], end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
