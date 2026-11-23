#!/usr/bin/env python3
"""Electric cars as a filler (TAX-59): the models of two lists become products in TAX, in one round,
with no AI call. The first filling by the maintainer, with only the basic logic of TAX-19: a model
only comes in with a page of the manufacturer that publishes its versions (TP1), and that page is the
source of the product and of its versions. The lint guards what goes in.

The lists are Open EV Data (the JSON of a release) and the FE Guide of fueleconomy.gov (the sheet EV
of each model year, as .xlsx). A row of either list is a trim of a model: a drivetrain, a battery or a
wheel size is a variant (TP3). A row belongs to the model of its brand whose words it all has, the
longest first: on the version page of the brand (referee/ev.yaml), else in Open EV Data.

What the version page publishes decides the product (Fred, 22 and 23 November 2026): one series of
versions for several models is one product, with the name the manufacturer gives the series, and the
model names as aliases; versions per model make each model a product of its own.

Every model gets one outcome with a reason (TR9):

    taken-in   it becomes a product, or part of one
    in-tax     TAX has the key or the alias of its product
    apart      set apart, with the reason: the page of the brand refuses our reader, the brand
               publishes no versions, the version page does not name the model, or an error of the lint

    python tools/ev.py --oev open-ev-data.json --fe 26.xlsx --fe 27.xlsx --out OUT [--write]

Without --write the files go to OUT/data. OUT/outcomes.json holds every model with its outcome;
OUT/report.md the count per outcome, brand and reason; OUT/apart.csv every model that is not taken in.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keyrule  # noqa: E402
import lint  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIST = ROOT / "referee/ev.yaml"
OEV, FE = "open-ev-data", "fueleconomy"
XL = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


# --------------------------------------------------------------------------- reading the lists

def sheet(path: Path, name: str) -> list[list[str | None]]:
    """The rows of one sheet of an .xlsx, as text, with only the standard library."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", XL):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{XL['m']}}}t")))
        book = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        targets = {s.get("name"): rels[s.get(f"{{{XL['r']}}}id")] for s in book.find("m:sheets", XL)}
        if name not in targets:
            raise SystemExit(f"{path}: no sheet {name!r}")
        target = targets[name].lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        rows = []
        for row in ET.fromstring(z.read(target)).iter(f"{{{XL['m']}}}row"):
            cells: dict[int, str | None] = {}
            for c in row.findall("m:c", XL):
                col = 0
                for ch in re.match(r"[A-Z]+", c.get("r")).group():
                    col = col * 26 + ord(ch) - 64
                v, kind = c.find("m:v", XL), c.get("t")
                if kind == "s" and v is not None:
                    cells[col - 1] = shared[int(v.text)]
                elif kind == "inlineStr":
                    cells[col - 1] = "".join(t.text or "" for t in c.iter(f"{{{XL['m']}}}t"))
                else:
                    cells[col - 1] = v.text if v is not None else None
            rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
        return rows


@dataclass
class Row:
    source: str  # OEV or FE
    brand: str  # as the list writes it
    model: str  # the model as Open EV Data writes it; the carline for the FE Guide
    trim: str = ""  # trim and variant (Open EV Data); the year


def read_oev(path: Path) -> list[Row]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for v in data["vehicles"]:
        trim = " ".join(x for x in ((v.get("trim") or {}).get("name"), (v.get("variant") or {}).get("name")) if x)
        out.append(Row(OEV, v["make"]["name"], v["model"]["name"], trim))
    return out


def read_fe(path: Path) -> list[Row]:
    rows = sheet(path, "EV")
    head = rows[0]
    at = {name: head.index(name) for name in ("Model Year", "Division", "Carline")}
    out = []
    for r in rows[1:]:
        cell = lambda k: (r[at[k]] if len(r) > at[k] else None) or ""  # noqa: E731
        if cell("Model Year") and cell("Division") and cell("Carline"):  # a title row has only the carline
            out.append(Row(FE, cell("Division").strip(), re.sub(r"\s+", " ", cell("Carline")).strip()))
    return out


# --------------------------------------------------------------------------- models and products

def words(name: str) -> frozenset:
    """The words of a name as the writing rule gives them ('ID.4 AWD' -> id, 4, awd); empty if none."""
    try:
        return frozenset(keyrule.product_part(name).split("-"))
    except keyrule.KeyRuleError:
        return frozenset()


def best(names, have: frozenset) -> str | None:
    """The name of which `have` holds all words, the one with the most words first."""
    fits = [n for n in names if words(n) and words(n) <= have]
    return max(fits, key=lambda n: (len(words(n)), n)) if fits else None


@dataclass
class Model:
    brand: str
    name: str
    sources: set = field(default_factory=set)
    rows: int = 0
    outcome: str = ""
    reason: str = ""
    product: str = ""


@dataclass
class Product:
    key: str
    publisher: str
    name: str
    versions: str
    series: bool
    models: list = field(default_factory=list)  # Model
    names: list = field(default_factory=list)  # the page's names of the models that came in

    def aliases(self) -> list[str]:
        out = [keyrule.long_form(self.key, self.name)]  # TAX-54
        if self.series:
            out += [f"{self.publisher}/{keyrule.product_part(n, self.publisher)}" for n in self.names]
        return [a for a in dict.fromkeys(out) if a and a != self.key]

    def text(self) -> str:
        data = {"key": self.key, "publisher": self.publisher, "name": self.name, "kind": "device",
                "source": self.versions, "versions": {"source": self.versions}, "aliases": self.aliases()}
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=200)


def load_list(path: Path = LIST) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise SystemExit(f"{path}: not version 1")
    for brand, entry in (data.get("brands") or {}).items():
        if bool(entry.get("products")) == bool(entry.get("apart")):
            raise SystemExit(f"{path}: {brand} needs either products or apart")
        if entry.get("apart") and entry["apart"] not in data.get("reasons", {}):
            raise SystemExit(f"{path}: {brand}: unknown reason {entry['apart']!r}")
    return data


def brand_of(data: dict) -> dict[str, str]:
    """Every name of a brand in the lists (folded) -> the brand as referee/ev.yaml writes it."""
    out = {}
    for brand, entry in data["brands"].items():
        for name in [brand, *(entry.get("fueleconomy") or [])]:
            out[keyrule.fold(name)] = brand
    return out


def models_of(rows: list[Row], data: dict) -> tuple[list[Model], dict, list[Row]]:
    """(the models, (brand, model) -> page product of the brand, the rows of an unknown brand)."""
    brands = brand_of(data)
    oev_models: dict[str, set] = defaultdict(set)
    unknown = []
    for r in rows:
        if r.source == OEV and keyrule.fold(r.brand) in brands:
            oev_models[brands[keyrule.fold(r.brand)]].add(r.model)
    models: dict[tuple, Model] = {}
    page: dict[tuple, dict] = {}
    for r in rows:
        brand = brands.get(keyrule.fold(r.brand))
        if not brand:
            unknown.append(r)
            continue
        products = data["brands"][brand].get("products") or []
        have = words(f"{r.model} {r.trim}")
        names = {n: p for p in products for n in p["models"]}
        hit = best(names, have)
        if hit:
            name = hit
            page[(brand, name)] = names[hit]
        elif r.source == OEV:
            name = r.model
        else:  # a carline: the model of Open EV Data it has, else its first word (two if the first is the brand)
            name = best(oev_models[brand], have)
            if not name:
                first = r.model.split()
                name = " ".join(first[:2]) if len(first) > 1 and keyrule.fold(first[0]) == keyrule.fold(brand) else first[0]
        m = models.setdefault((brand, keyrule.fold(name)), Model(brand, name))
        m.sources.add(r.source)
        m.rows += 1
    return sorted(models.values(), key=lambda m: (m.brand, m.name)), {(b, keyrule.fold(n)): p for (b, n), p in page.items()}, unknown


class Tax:
    """What TAX has: its keys and aliases, and its publishers."""

    def __init__(self, root: Path):
        self.keys: dict[str, str] = {}
        for f in sorted((root / "products").glob("*/*.yaml")):
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            for k in [d.get("key", ""), *(d.get("aliases") or [])]:
                self.keys.setdefault(k, d.get("key", ""))
        self.publishers = {f.stem for f in (root / "publishers").glob("*.yaml")}


def _set(m: Model, outcome: str, reason: str = "", product: str = "") -> None:
    m.outcome, m.reason, m.product = outcome, reason, product


def plan(rows: list[Row], root: Path = ROOT, data: dict | None = None) -> tuple[list[Model], dict, dict, list[Row]]:
    """(every model, products by key, new publishers by key: {name, source}, rows of an unknown brand)."""
    data = data or load_list()
    tax = Tax(root)
    models, page, unknown = models_of(rows, data)
    products: dict[str, Product] = {}
    publishers: dict[str, dict] = {}
    for m in models:
        entry = data["brands"][m.brand]
        if entry.get("apart"):
            _set(m, "apart", data["reasons"][entry["apart"]])
            continue
        p = page.get((m.brand, keyrule.fold(m.name)))
        if not p:
            _set(m, "apart", data["reasons"]["other"])
            continue
        pub = entry["publisher"]["key"]
        key = f"{pub}/{keyrule.product_part(p['name'], pub)}"
        for k in [key, keyrule.long_form(key, p["name"])]:
            if k and k in tax.keys:
                _set(m, "in-tax", "TAX has the key or the long form of its product", tax.keys[k])
                break
        else:
            prod = products.setdefault(key, Product(key, pub, p["name"], p["versions"], bool(p.get("series"))))
            prod.models.append(m)
            if m.name not in prod.names:
                prod.names.append(m.name)
            _set(m, "taken-in", "", key)
            if pub not in tax.publishers and pub not in publishers:
                publishers[pub] = {"name": entry["publisher"]["name"], "source": p["versions"]}
    return models, products, publishers, unknown


def publisher_text(key: str, pub: dict) -> str:
    return yaml.safe_dump({"key": key, "name": pub["name"], "source": pub["source"], "aliases": []},
                          sort_keys=False, allow_unicode=True, width=200)


def files_of(products: dict, publishers: dict) -> dict[str, str]:
    files = {f"publishers/{k}.yaml": publisher_text(k, v) for k, v in publishers.items()}
    files.update({f"products/{k}.yaml": p.text() for k, p in products.items()})
    return files


def guard(products: dict, publishers: dict, root: Path = ROOT) -> list:
    """The lint on everything the round writes, against TAX. Only an error sets a product apart (the
    basic logic of the first filling, TAX-19); signals stay for a person. Repeated until the lint finds
    no error. Returns all findings of the last pass."""
    data = lint.Dataset(root, detailed=set())
    while True:
        files = files_of(products, publishers)
        found = data.check(report=set(files), overrides=files)
        errors = {}
        for f in found:
            if f.level != "signal":
                errors.setdefault(f.file, f)
        if not errors:
            return found
        for rel, f in sorted(errors.items()):
            reason = f"{f.rule}: {f.message}"
            if rel.startswith("publishers/"):
                pub = rel[len("publishers/"):-len(".yaml")]
                publishers.pop(pub, None)
                gone = [k for k, p in products.items() if p.publisher == pub]
                reason = f"its publisher: {reason}"
            else:
                gone = [rel[len("products/"):-len(".yaml")]]
            for key in gone:
                for m in products.pop(key, Product(key, "", "", "", False)).models:
                    _set(m, "apart", reason, key)


# --------------------------------------------------------------------------- report and run

def report(models: list[Model], products: dict, publishers: dict, signals: list, unknown: list, rows: list) -> str:
    by = Counter(m.outcome for m in models)
    per_source = Counter(r.source for r in rows)
    aliases = sum(len(p.aliases()) for p in products.values())
    out = [f"## Electric cars as a filler ({per_source[OEV]:,} rows of Open EV Data, {per_source[FE]:,} of the FE Guide)", "",
           f"{len(models):,} models of {len({m.brand for m in models})} brands. {len(products)} products with "
           f"{aliases} aliases, and {len(publishers)} new publishers. No AI call. {len(signals)} signals of the lint, "
           "for a person.", "",
           "| outcome | models |", "| --- | ---: |"]
    out += [f"| {o} | {by[o]:,} |" for o in ("taken-in", "in-tax", "apart")]
    out += ["", "| outcome | reason | models | brands |", "| --- | --- | ---: | ---: |"]
    reasons: dict[tuple, list] = defaultdict(list)
    for m in models:
        if m.reason:
            reasons[(m.outcome, m.reason)].append(m)
    for (o, r), ms in sorted(reasons.items(), key=lambda x: (x[0][0], -len(x[1]))):
        out.append(f"| {o} | {r} | {len(ms)} | {len({m.brand for m in ms})} |")
    out += ["", "Products:", "", "| key | name | models | aliases | versions |", "| --- | --- | --- | --- | --- |"]
    for key, p in sorted(products.items()):
        out.append(f"| `{key}` | {p.name} | {', '.join(m.name for m in p.models)} | "
                   f"{', '.join(f'`{a}`' for a in p.aliases()) or '-'} | {p.versions} |")
    if unknown:
        out += ["", f"Rows of a brand that referee/ev.yaml does not name: {len(unknown)} "
                    f"({', '.join(sorted({r.brand for r in unknown}))})."]
    return "\n".join(out) + "\n"


def run(oev: Path, fe: list[Path], out: Path, root: Path = ROOT, write: bool = False, data: dict | None = None) -> dict:
    data = data or load_list()
    rows = read_oev(oev) + [r for path in fe for r in read_fe(path)]
    models, products, publishers, unknown = plan(rows, root, data)
    signals = [f for f in guard(products, publishers, root) if f.level == "signal"]
    files = files_of(products, publishers)
    out.mkdir(parents=True, exist_ok=True)
    target = root if write else out / "data"
    for rel, text in files.items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text, encoding="utf-8")
    text = report(models, products, publishers, signals, unknown, rows)
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "outcomes.json").write_text(json.dumps([{**asdict(m), "sources": sorted(m.sources)} for m in models],
                                                  indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out / "apart.csv").open("w", newline="", encoding="utf-8") as f:  # for referee/import/
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["brand", "model", "sources", "outcome", "reason", "product"])
        w.writerows([m.brand, m.name, " ".join(sorted(m.sources)), m.outcome, m.reason, m.product]
                    for m in models if m.outcome != "taken-in")
    return {"models": models, "products": products, "publishers": publishers, "signals": signals,
            "unknown": unknown, "report": text}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--oev", required=True, help="the JSON of a release of Open EV Data")
    p.add_argument("--fe", action="append", default=[], help="an FE Guide of fueleconomy.gov (.xlsx), once per model year")
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--write", action="store_true", help="write into the dataset (else OUT/data)")
    a = p.parse_args(argv)
    got = run(Path(a.oev), [Path(x) for x in a.fe], Path(a.out), Path(a.root), a.write)
    print(got["report"], end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
