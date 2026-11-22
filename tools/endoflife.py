#!/usr/bin/env python3
"""endoflife as a filler (TAX-57): what the filler describes and TAX lacks becomes a product, in one
round, with no AI call. The first filling by the maintainer, with only the basic logic of TAX-19: the
page on which the filler describes a product is the source of the product and of its versions. The
lint guards what goes in.

A page of a category in `lines` (referee/endoflife.yaml) describes a line of devices; each model on it
(a row) is a product of its own (TP3). Every other page is one product. Each page and each model gets
one outcome with a reason (TR9):

    taken-in   it becomes a product, or part of one (rows with the same key are one product: TP3); a
               model the filler no longer supports comes in archived (TP10), with the page as source
    in-tax     TAX has it: a source of a product names the page, or a product has its key or its name
    apart      set apart, with the reason: the list names no publisher for the page, the page is out
               of this round, a name outside the Latin script, or an error of the lint

The publisher per page is data in referee/endoflife.yaml, as are the new publishers and the words a
model is written after (TG4). The code names no brand and no product.

    python tools/endoflife.py --dump full.json --out OUT
    python tools/endoflife.py --dump full.json --out OUT --write

The dump is the full list of the filler's API (`api/v1/products/full`). Without --write the files go
to OUT/data. OUT/outcomes.json holds every page and model with its outcome; OUT/report.md the count
per outcome, per line and per reason; OUT/not-taken-in.csv every page and model that is not taken in.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import census  # noqa: E402
import keyrule  # noqa: E402
import lint  # noqa: E402
import sites  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGE = sites.value("fillers")["endoflife"]  # where the filler describes a product (referee/sites.yaml)
LIST = ROOT / "referee/endoflife.yaml"
IDENTIFIERS = ("cpe", "purl")
IDENTIFIER_ERROR = re.compile(r"^identifiers/(\d+)/")  # the codes of the filler that TAX also keeps (schema/product.schema.json)


@dataclass
class Item:
    page: str  # the slug of the page
    name: str  # the name as the filler writes it (with the prefix for a model, see `model_name`)
    row: str = ""  # the label of the row, for a model
    kind: str = "software"
    maintained: bool | None = None  # what the filler says of a model
    outcome: str = ""
    reason: str = ""
    product: str = ""

    @property
    def url(self) -> str:
        return PAGE.format(slug=self.page)


@dataclass
class Product:
    key: str
    publisher: str
    name: str
    kind: str
    page: str
    identifiers: list = field(default_factory=list)
    members: list = field(default_factory=list)

    @property
    def ended(self) -> bool:
        """A model of which every row says it is no longer supported (TP10, TAX-57: archived on intake)."""
        return bool(self.members) and all(m.maintained is False for m in self.members)

    def text(self) -> str:
        url = PAGE.format(slug=self.page)
        data = {"key": self.key, "publisher": self.publisher, "name": self.name, "kind": self.kind,
                "source": url, "versions": {"source": url},
                "identifiers": [{k: v, "source": url} for k, v in self.identifiers],
                "aliases": [a for a in [keyrule.long_form(self.key, self.name)] if a]}  # TAX-54
        if self.ended:
            data["archived"] = {"source": url}  # TP10: the filler says the model gets no more support
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=200)


def load_list(path: Path = LIST) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise SystemExit(f"{path}: not version 1")
    return data


def model_name(row: str, prefix: str | None) -> str:
    """The name of a model: the row as the page writes it, after the prefix when the row leaves it out."""
    row = re.sub(r"\s+", " ", row).strip()
    if prefix and not census.fold(row).startswith(census.fold(prefix)):
        return f"{prefix} {row}"
    return row


def read(dump: Path) -> dict:
    return json.loads(dump.read_text(encoding="utf-8"))


class Tax:
    """What TAX has, for the comparison: the pages its sources name, its keys, aliases and names."""

    def __init__(self, root: Path, patterns: list[str]):
        self.pages: dict[str, str] = {}  # slug -> key
        self.keys: dict[str, str] = {}  # key or alias -> key
        self.names: dict[str, str] = {}  # name as a key part (with and without its publisher) -> key
        self.codes: dict[str, str] = {}  # cpe or purl -> key
        self.publishers = {f.stem: (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("name", f.stem)
                           for f in (root / "publishers").glob("*.yaml")}
        res = [re.compile("^" + re.escape(p).replace(re.escape("{slug}"), r"([^/#?]+)") + r"(?:[/#?].*)?$")
               for p in patterns]
        for f in sorted((root / "products").glob("*/*.yaml")):
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            key = d.get("key", "")
            for k in [key, *(d.get("aliases") or [])]:
                self.keys.setdefault(k, key)
            pubname = self.publishers.get(d.get("publisher", ""), "")
            for n in (d.get("name", ""), f"{pubname} {d.get('name', '')}"):
                if as_part(n):
                    self.names.setdefault(as_part(n), key)
            urls = [d.get("source"), (d.get("versions") or {}).get("source")]
            for ident in d.get("identifiers") or []:
                urls.append(ident.get("source"))
                for code in IDENTIFIERS:
                    if code in ident:
                        self.codes.setdefault(str(ident[code]).lower(), key)
            for url in urls:
                for r in res:
                    m = r.match(url or "")
                    if m:
                        self.pages.setdefault(m.group(1), key)

    def has(self, key: str, name: str, publisher: str) -> str:
        """The key of the product in TAX that this is, or ''."""
        long = keyrule.long_form(key, name)
        for k in (key, long):
            if k and k in self.keys:
                return self.keys[k]
        found = self.names.get(as_part(name), "")
        return found if found.startswith(publisher + "/") else ""


def as_part(name: str) -> str:
    """A name written as a key part, for the comparison ('Tab 9+' is not 'Tab 9'); '' when it gives none."""
    try:
        return keyrule.product_part(name)
    except keyrule.KeyRuleError:
        return ""


def _set(item: Item, outcome: str, reason: str = "", product: str = "") -> None:
    item.outcome, item.reason, item.product = outcome, reason, product


def plan(dump: dict, root: Path = ROOT, data: dict | None = None) -> tuple[list[Item], dict, dict]:
    """(every page or model as an Item, products by key, new publishers by key)."""
    data = data or load_list()
    tax = Tax(root, [PAGE, *data.get("also", [])])
    kinds, lines = data.get("kinds") or {}, set(data.get("lines") or [])
    pages, apart = data.get("pages") or {}, data.get("apart") or {}
    items: list[Item] = []
    products: dict[str, Product] = {}
    used: set[str] = set()
    for page in sorted(dump["result"], key=lambda p: p["name"]):
        slug, category = page["name"], page.get("category", "")
        kind = kinds.get(category, "software")
        entry = pages.get(slug) or {}
        pub = entry.get("publisher", "")
        if category in lines:
            todo = [Item(slug, model_name(r.get("label") or r["name"], entry.get("prefix")), r.get("label") or r["name"],
                         kind, r.get("isMaintained")) for r in page.get("releases") or []]
            codes = []
        else:
            todo = [Item(slug, page.get("label") or slug, kind=kind)]
            codes = [(i["type"], i["id"]) for i in page.get("identifiers") or [] if i.get("type") in IDENTIFIERS]
        items += todo
        for item in todo:
            if category not in lines and slug in tax.pages:
                _set(item, "in-tax", "a source of a product in TAX names the page", tax.pages[slug])
                continue
            hit = next((tax.codes[c.lower()] for _, c in codes if c.lower() in tax.codes), "")
            if hit:
                _set(item, "in-tax", "a product in TAX has its cpe or purl", hit)
                continue
            if slug in apart:
                _set(item, "apart", f"the page is out of this round: {apart[slug]}")
                continue
            if not pub:
                named = tax.names.get(as_part(item.name), "")
                if named:
                    _set(item, "in-tax", "a product in TAX has its name", named)
                else:
                    _set(item, "apart", "the list names no publisher for the page (referee/endoflife.yaml)")
                continue
            if pub not in tax.publishers and pub not in (data.get("publishers") or {}):
                _set(item, "apart", f"the publisher {pub!r} is neither in TAX nor in the list")
                continue
            try:
                key = f"{pub}/{keyrule.product_part(item.name, pub)}"
            except keyrule.KeyRuleError:
                _set(item, "apart", "a name outside the Latin script (TK2)")
                continue
            have = tax.has(key, item.name, pub)
            if have:
                _set(item, "in-tax", "a product in TAX has its key or its name", have)
                continue
            if key in products:  # TP3: rows that give the same key are one product
                products[key].members.append(item)
                _set(item, "taken-in", "the same key as another row: one product (TP3)", key)
                continue
            products[key] = Product(key, pub, item.name, kind, slug, codes, [item])
            used.add(pub)
            _set(item, "taken-in", "", key)
    new = {k: v for k, v in (data.get("publishers") or {}).items() if k in used and k not in tax.publishers}
    return items, products, new


def publisher_text(key: str, pub: dict) -> str:
    return yaml.safe_dump({"key": key, "name": pub["name"], "source": PAGE.format(slug=pub["page"]), "aliases": []},
                          sort_keys=False, allow_unicode=True, width=200)


def files_of(products: dict, publishers: dict) -> dict[str, str]:
    files = {f"publishers/{k}.yaml": publisher_text(k, v) for k, v in publishers.items()}
    files.update({f"products/{k}.yaml": p.text() for k, p in products.items()})
    return files


def guard(products: dict, publishers: dict, root: Path = ROOT, dropped: list | None = None) -> list:
    """The lint on everything the round writes, against TAX. Only an error sets a product apart (the
    basic logic of the first filling, TAX-19); signals stay for a person. Repeated until the lint finds
    no error. An identifier the schema refuses is left out (into `dropped`); the product stays. Returns all
    findings of the last pass."""
    data = lint.Dataset(root, detailed=set())
    dropped = [] if dropped is None else dropped
    while True:
        files = files_of(products, publishers)
        found = data.check(report=set(files), overrides=files)
        errors: dict[str, lint.Finding] = {}
        for f in found:
            if f.level != "signal":
                errors.setdefault(f.file, f)
        if not errors:
            return found
        for rel, f in sorted(errors.items()):
            reason = f"{f.rule}: {f.message}"
            code = IDENTIFIER_ERROR.match(f.message)
            key = rel[len("products/"):-len(".yaml")]
            if f.rule == "TE1" and code and key in products and int(code.group(1)) < len(products[key].identifiers):
                # an identifier is optional: one the schema refuses is left out, the product stays
                gone = products[key].identifiers.pop(int(code.group(1)))
                dropped.append((key, f"{gone[0]} {gone[1]!r}", f.message.split(": ", 1)[-1]))
                continue
            if rel.startswith("publishers/"):
                pub = rel[len("publishers/"):-len(".yaml")]
                publishers.pop(pub, None)
                for key in [k for k, p in products.items() if p.publisher == pub]:
                    for m in products.pop(key).members:
                        _set(m, "apart", f"its publisher: {reason}", key)
            else:
                key = rel[len("products/"):-len(".yaml")]
                for m in products.pop(key, Product(key, "", "", "", "")).members:
                    _set(m, "apart", reason, key)


def kind_of(reason: str) -> str:
    return re.sub(r"'[^']*'|\S+\.yaml(?!\))", "…", reason)


def report(items: list[Item], products: dict, publishers: dict, dump: dict, data: dict, signals: list,
           dropped: list = ()) -> str:
    lines_ = set(data.get("lines") or [])
    by = Counter(i.outcome for i in items)
    out = [f"## endoflife as a filler (dump of {dump.get('generated_at', '?')}, {dump.get('total', '?')} pages)", "",
           f"{len(items):,} pages and models. {len(products):,} products and {len(publishers):,} new publishers. "
           f"No AI call. {len(signals)} signals of the lint, for a person.", "",
           "| outcome | pages and models |", "| --- | ---: |"]
    out += [f"| {o} | {by[o]:,} |" for o in ("taken-in", "in-tax", "apart")]
    per: dict[str, Counter] = defaultdict(Counter)
    maintained: Counter = Counter()
    ended: Counter = Counter()
    category = {p["name"]: p.get("category", "") for p in dump["result"]}
    for i in items:
        if category.get(i.page) in lines_:
            per[i.page][i.outcome] += 1
            if i.outcome == "taken-in" and i.maintained:
                maintained[i.page] += 1
            if i.outcome == "taken-in" and products.get(i.product) and products[i.product].ended:
                ended[i.page] += 1
    out += ["", "Per line of devices (a model is a product, TP3):", "",
            "| page | models | taken-in | of which still supported | of which archived | in-tax | apart |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for page in sorted(per):
        c = per[page]
        out.append(f"| {page} | {sum(c.values())} | {c['taken-in']} | {maintained[page]} | {ended[page]} | {c['in-tax']} | {c['apart']} |")
    t = sum(per.values(), Counter())
    out.append(f"| **all** | {sum(t.values())} | {t['taken-in']} | {sum(maintained.values())} | {sum(ended.values())} | {t['in-tax']} | {t['apart']} |")
    other = [i for i in items if category.get(i.page) not in lines_]
    c = Counter(i.outcome for i in other)
    out += ["", f"Other pages (one product each): {len(other)}; taken-in {c['taken-in']}, in-tax {c['in-tax']}, "
                f"apart {c['apart']}.", ""]
    taken = sorted(i.page for i in other if i.outcome == "taken-in")
    if taken:
        out += ["Taken in: " + ", ".join(taken) + ".", ""]
    out += ["| outcome | reason | count |", "| --- | --- | ---: |"]
    reasons = Counter((i.outcome, kind_of(i.reason)) for i in items if i.reason)
    out += [f"| {o} | {r} | {n:,} |" for (o, r), n in sorted(reasons.items(), key=lambda x: (x[0][0], -x[1]))]
    if dropped:
        out += ["", "Identifiers of the filler left out, because the schema refuses them (the product stays):", ""]
        out += [f"- `{k}`: {code}" for k, code, _ in dropped]
    return "\n".join(out) + "\n"


def run(dump_path: Path, out: Path, root: Path = ROOT, write: bool = False, data: dict | None = None) -> dict:
    data = data or load_list()
    dump = read(dump_path)
    items, products, publishers = plan(dump, root, data)
    dropped: list = []
    signals = [f for f in guard(products, publishers, root, dropped) if f.level == "signal"]
    files = files_of(products, publishers)
    out.mkdir(parents=True, exist_ok=True)
    target = root if write else out / "data"
    for rel, text in files.items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text, encoding="utf-8")
    text = report(items, products, publishers, dump, data, signals, dropped)
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "outcomes.json").write_text(json.dumps([asdict(i) for i in items], indent=0, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    with (out / "not-taken-in.csv").open("w", newline="", encoding="utf-8") as f:  # for referee/import/
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["page", "model", "outcome", "reason", "product", "name", "source"])
        w.writerows([i.page, i.row, i.outcome, i.reason, i.product, i.name, i.url] for i in items if i.outcome != "taken-in")
    return {"items": items, "products": products, "publishers": publishers, "signals": signals, "dropped": dropped,
            "report": text}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dump", required=True, help="the full list of the filler's API, as JSON")
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--write", action="store_true", help="write into the dataset (else OUT/data)")
    a = p.parse_args(argv)
    got = run(Path(a.dump), Path(a.out), Path(a.root), a.write)
    print(got["report"], end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
