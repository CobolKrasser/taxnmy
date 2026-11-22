#!/usr/bin/env python3
"""LVFS as a filler (TAX-23): the devices of the AppStream metadata of LVFS become products in TAX, in one
round, with no AI call. The first filling by the maintainer, with only the basic logic of TAX-19.

The round reads the metadata that a fwupd client fetches (`fwupdmgr refresh`), as a local file; it
fetches nothing itself (Fred, 22 November 2026). Each component is a device (criterion 4), unless its
categories say it is a part of a device with its own firmware (outside TAX-23) or a setting; those are set
apart with that reason. The page on which LVFS describes the device is the source of the product and of
its versions (criterion 2); its releases are its versions (TP3). The name and the publisher are as the
metadata writes them (`name`, `developer_name`, criterion 3); components that give the same key are one
product. A publisher TAX knows keeps its key and its source; a new one gets the page of LVFS as source.

The categories and the page are data in referee/lvfs.yaml (TG4). The writing of the files and the lint
are those of tools/csaf.py.

    python tools/lvfs.py --metadata firmware.xml.gz --out OUT [--write]
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

ROOT = Path(__file__).resolve().parent.parent
LIST = ROOT / "referee/lvfs.yaml"
RULE = "a component of LVFS: a device"


def load_list(path: Path = LIST) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise SystemExit(f"{path}: not version 1")
    return data


def components(metadata: bytes) -> list[dict]:
    """(id, name, developer, categories) of every component of the metadata, gzipped or not."""
    if metadata[:2] == b"\x1f\x8b":
        metadata = gzip.decompress(metadata)
    root = ET.fromstring(metadata)
    if root.tag != "components":
        raise SystemExit("no AppStream metadata")
    return [{"id": (c.findtext("id") or "").strip(), "name": (c.findtext("name") or "").strip(),
             "developer": (c.findtext("developer_name") or "").strip(),
             "categories": {x.text for x in c.iter("category") if x.text}} for c in root.iter("component")]


def plan(comps: list[dict], root: Path = ROOT, data: dict | None = None):
    """(items, products by key, new publishers by key, publishers by key: [] for the files)."""
    data = data or load_list()
    tax = csaf.Tax(root)
    whole, parts, settings = (set(data.get(k) or []) for k in ("whole", "parts", "settings"))
    items: list[csaf.Item] = []
    products: dict[str, csaf.Product] = {}
    spellings: dict[str, Counter] = defaultdict(Counter)
    first: dict[str, str] = {}
    for c in sorted(comps, key=lambda c: (c["developer"], c["name"], len(c["id"]), c["id"])):  # the shortest id is the source
        page = data["page"].format(id=c["id"])
        item = csaf.Item(page, c["developer"], c["name"])
        items.append(item)
        if c["categories"] & settings:
            csaf._set(item, "apart", "a setting, no product (its category)")
            continue
        if c["categories"] & parts and not c["categories"] & whole:
            csaf._set(item, "apart", "a part of a device with its own firmware, outside TAX-23 (its category)")
            continue
        if not c["developer"] or not c["name"] or not c["id"]:
            csaf._set(item, "apart", "the metadata gives no developer, no name or no id, so no key (TK2)")
            continue
        name = csaf.product_name(c["name"])
        try:
            pub = tax.publisher(c["developer"]) or keyrule.publisher_part(c["developer"])
            key = f"{pub}/{keyrule.product_part(name, pub)}"
        except keyrule.KeyRuleError:
            csaf._set(item, "apart", "a name outside the Latin script, so no key (TK2)")
            continue
        have = tax.has(key, name, [])
        if have:
            csaf._set(item, "in-tax", "TAX has its key or its long form (TK3): no new file", have)
            continue
        if key in products:
            products[key].items.append(item)
            csaf._set(item, "taken-in", "the same key as another component: one product (TP3)", key)
            continue
        products[key] = csaf.Product(key, pub, name, page, "device", RULE, [], [], [item])
        csaf._set(item, "taken-in", RULE, key)
        if not tax.publisher(c["developer"]) and pub not in tax.census.publishers:
            spellings[pub][c["developer"]] += 1
            first.setdefault(pub, page)
    new = {pub: {"name": n.most_common(1)[0][0], "source": first[pub]} for pub, n in spellings.items()}
    return items, products, new, {pub: [] for pub in new}


def report(items, products, publishers, signals, before: int) -> str:
    by = Counter(i.outcome for i in items)
    out = ["## LVFS as a filler (TAX-23)", "",
           f"{len(items):,} components. {len(products):,} devices and {len(publishers):,} new publishers. "
           f"No AI call; nothing fetched. {len(signals)} signals of the lint, for a person.", "",
           "| outcome | components |", "| --- | ---: |"]
    out += [f"| {o} | {by[o]:,} |" for o in ("taken-in", "in-tax", "apart")]
    out += ["", f"Devices in TAX: {before:,} before, {before + len(products):,} after.", "",
            "| publisher | devices |", "| --- | ---: |"]
    out += [f"| {p} | {n:,} |" for p, n in Counter(p.publisher for p in products.values()).most_common()]
    reasons = Counter((i.outcome, i.reason) for i in items if i.outcome != "taken-in" or i.reason != RULE)
    out += ["", "| outcome | reason | count |", "| --- | --- | ---: |"]
    out += [f"| {o} | {r} | {n:,} |" for (o, r), n in sorted(reasons.items(), key=lambda x: (x[0][0], -x[1]))]
    return "\n".join(out) + "\n"


def run(metadata: bytes, out: Path, root: Path = ROOT, write: bool = False, data: dict | None = None) -> dict:
    data = data or load_list()
    before = csaf.devices(root)
    items, products, publishers, addresses = plan(components(metadata), root, data)
    signals = [f for f in csaf.guard(products, publishers, addresses, root) if f.level == "signal"]
    files = csaf.files_of(products, publishers, addresses, root)
    out.mkdir(parents=True, exist_ok=True)
    target = root if write else out / "data"
    for rel, text in files.items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text, encoding="utf-8")
    text = report(items, products, publishers, signals, before)
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "outcomes.json").write_text(json.dumps([asdict(i) for i in items], indent=0, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    with (out / "not-taken-in.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["page", "developer", "name", "outcome", "reason", "product"])
        w.writerows([i.address, i.vendor, i.name, i.outcome, i.reason, i.product] for i in items if i.outcome != "taken-in")
    return {"items": items, "products": products, "publishers": publishers, "files": files, "signals": signals,
            "report": text}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--metadata", required=True, help="firmware.xml.gz as a fwupd client fetches it")
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--write", action="store_true", help="write into the dataset (else OUT/data)")
    a = p.parse_args(argv)
    got = run(Path(a.metadata).read_bytes(), Path(a.out), Path(a.root), a.write)
    print(got["report"], end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
