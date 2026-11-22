#!/usr/bin/env python3
"""winget as a filler (TAX-19): the manifests of winget-pkgs become products in TAX, in one round, with
no AI call. The first filling by the maintainer: the page on which winget describes a package is the
source of the product, of its versions and of a new publisher. The lint guards what goes in.

Per package (its newest version, read as in the count of TAX-25, tools/census.py) one outcome with a
reason (TR9):

    taken-in   it becomes a product, or part of one (TP3: packages that differ only in a version)
    in-tax     its winget id is already on a product in TAX
    apart      set apart, with the reason: a duplicate of a product in TAX (the same key, TK3), a name
               like an existing one (TV3, for a person), a name outside the Latin script, or another
               finding of the lint
    later      outside the scope of this round (`--scope known`: only publishers TAX knows)

A publisher TAX knows keeps its key and its source. Which publisher that is, is decided as in the
count: by its name or the domain of its PublisherUrl, compared character by character after folding
(tools/census.py, Tax). A package whose id alone names a publisher in TAX, or whose PublisherUrl is on
another domain than that publisher's, is set apart for a person. The rules name no brand and no product (TG4).

    python tools/winget.py --dump winget-pkgs.tar.gz --scope known --out OUT
    python tools/winget.py --dump winget-pkgs.tar.gz --scope known --out OUT --write --batch 1

Without --write the files go to OUT/data. --batch N writes only the Nth batch of at most --size files
(2,000 by default; a publisher and its products stay in one batch). OUT/outcomes.json holds every
package with its outcome; OUT/report.md the count per outcome and per reason; OUT/sample.json
50 products for the tester, each with its packages and their pages.
"""

from __future__ import annotations

import argparse
import json
import random
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
PAGE = sites.value("fillers")["winget"]  # where winget describes a package (referee/sites.yaml, TG4)
SIZE = 2000

# TP3: the parts of a name that are a version, by the general rules of the count (tools/census.py).
VERSION_PARTS = tuple(rule for label, rule in census.VERSION_RULES if label in ("dotted number", "v-number", "year"))
NUMBER_PART = re.compile(r"v?(\d+)", re.I)
EMAIL = re.compile(r"[^\s<>@]+@[^\s<>@]+\.[a-z]{2,}", re.I)


@dataclass
class Package:
    id: str
    folder: str  # manifests/<letter>/<id path>
    version: str
    publisher: str
    name: str
    publisher_url: str = ""
    outcome: str = ""
    reason: str = ""
    product: str = ""  # the key of the product it is (part of), or is in TAX as

    @property
    def page(self) -> str:
        return PAGE.format(folder=self.folder)


@dataclass
class Product:
    key: str
    publisher: str
    name: str
    members: list = field(default_factory=list)  # Package, the first one is the source

    def text(self) -> str:
        page = self.members[0].page
        data = {"key": self.key, "publisher": self.publisher, "name": self.name, "kind": "software",
                "source": page, "versions": {"source": page},
                "identifiers": [{"winget": p.id, "source": p.page} for p in sorted(self.members, key=lambda p: p.id)],
                "aliases": []}
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=200)


def base_name(name: str, ident: str) -> str:
    """The name without the parts that are a version (TP3); the name itself when nothing is left."""
    out = name
    for rule in VERSION_PARTS:
        out = rule.sub(" ", out)
    for number in {m.group(1) for m in map(NUMBER_PART.fullmatch, ident.split(".")[1:]) if m}:
        # a number that is a part of the id of its own is a version or a line of versions
        out = re.sub(rf"(?<![\w.]){re.escape(number)}(?![\w.])", " ", out)
    last = ident.rsplit(".", 1)[-1] if "." in ident else ""
    if last:  # the last part of the id, in brackets at the end of the name, marks a variant (a language, a platform)
        out = re.sub(rf"\(\s*{re.escape(last)}\s*\)\s*$", " ", out, flags=re.I)
    out = re.sub(r"\(\s*\)|\[\s*\]", " ", out)
    out = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", out))
    out = re.sub(r"\s+", " ", out).strip(" -–,:/")
    return out if out and keyrule.fold(out).strip("-") else name.strip()


def words(key: str) -> tuple:
    """The publisher and the words of the product part, in any order (as the lint compares them, TK3)."""
    pub, _, part = key.partition("/")
    return (pub, tuple(sorted(part.split("-"))))


def read(dump: Path) -> list[Package]:
    out = []
    for folder, version, m in census.winget_packages(dump):
        ident = m.get("PackageIdentifier") or folder.split("/", 2)[2].replace("/", ".")
        out.append(Package(id=ident, folder=folder, version=version, publisher=m.get("Publisher", "").strip(),
                           name=m.get("PackageName", "").strip(), publisher_url=m.get("PublisherUrl", "").strip()))
    return sorted(out, key=lambda p: p.id.lower())


def _set(p: Package, outcome: str, reason: str = "", product: str = "") -> None:
    p.outcome, p.reason, p.product = outcome, reason, product


def plan(packages: list[Package], root: Path = ROOT, scope: str = "known") -> tuple[dict, dict]:
    """(products by key, new publishers by key: {name, page}); every package gets its outcome here or
    in `guard` (the lint)."""
    tax = census.Tax(root)
    winget_on = {}
    keys_in_tax = set()
    domains: dict[str, set] = defaultdict(set)  # publisher -> the domains of its sources in TAX
    for key, d in tax.publishers.items():
        domains[key].add(census.owner(d.get("source") or ""))
    for f in sorted((root / "products").glob("*/*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        keys_in_tax.update([d.get("key", ""), *(d.get("aliases") or [])])
        for url in (d.get("source"), (d.get("versions") or {}).get("source")):
            domains[d.get("publisher", "")].add(census.owner(url or ""))
        for ident in d.get("identifiers") or []:
            if "winget" in ident:
                winget_on[str(ident["winget"]).lower()] = d.get("key", "")
    words_in_tax = {words(k): k for k in keys_in_tax if k.count("/") == 1}
    by_words: dict[tuple, str] = {}
    products: dict[str, Product] = {}
    spellings: dict[str, Counter] = defaultdict(Counter)
    first_page: dict[str, str] = {}
    for p in packages:
        if p.id.lower() in winget_on:
            _set(p, "in-tax", "its winget id is on a product in TAX", winget_on[p.id.lower()])
            continue
        if not p.publisher or not p.name:
            _set(p, "apart", "the manifest gives no publisher or no name")
            continue
        if EMAIL.search(p.publisher) or EMAIL.search(p.name):
            _set(p, "apart", "a name that holds an e-mail address: TAX is public and keeps none")
            continue
        domain = census.owner(p.publisher_url) if p.publisher_url else None
        known = tax.publisher([p.publisher], [domain] if domain else [])
        if not known:
            try:
                written = keyrule.publisher_part(p.publisher)
            except keyrule.KeyRuleError:
                written = ""
            known = written if written in tax.publishers else None
        if not known and tax.publisher([p.id.split(".")[0]]):
            # The first part of the id names a publisher in TAX, the manifest names another one.
            _set(p, "apart", "only the winget id names a publisher in TAX (TV2): a person decides")
            continue
        if scope == "known" and not known:
            _set(p, "later", "a publisher TAX does not know yet")
            continue
        own = domains.get(known, set()) - {None}  # a site with many owners shows none
        if known and domain and own and domain not in own:
            _set(p, "apart", "the name of a publisher in TAX, on another domain (TV3): a person decides")
            continue
        parent = p.id.rsplit(".", 1)[0].lower()
        if parent in winget_on and base_name(p.name, p.id) != p.name:
            _set(p, "in-tax", "a variant of a product in TAX (TP3)", winget_on[parent])
            continue
        try:
            pub = known or keyrule.publisher_part(p.publisher)
            part = keyrule.product_part(base_name(p.name, p.id))
        except keyrule.KeyRuleError:
            _set(p, "apart", "a name outside the Latin script (TK2)")
            continue
        if not pub or not part:
            _set(p, "apart", "a name that gives no key (TK2)")
            continue
        key = f"{pub}/{part}"
        if key in keys_in_tax:
            _set(p, "apart", "a duplicate: a product in TAX has this key (TK3)", key)
            continue
        if words(key) in words_in_tax and words_in_tax[words(key)] != key:
            _set(p, "apart", "a duplicate: a product in TAX has the same words in another order (TK3)",
                 words_in_tax[words(key)])
            continue
        key = by_words.setdefault(words(key), key)  # the same words in another order: one product
        if not known:
            spellings[pub][p.publisher] += 1
            first_page.setdefault(pub, p.page)
        prod = products.setdefault(key, Product(key=key, publisher=pub, name=base_name(p.name, p.id)))
        prod.members.append(p)
        _set(p, "taken-in", "", key)
    for prod in products.values():
        # The source is the member whose name is the name of the product, else the shortest id.
        prod.members.sort(key=lambda p: (p.name != prod.name, len(p.id), p.id.lower()))
    publishers = {pub: {"name": c.most_common(1)[0][0], "page": first_page[pub]} for pub, c in spellings.items()}
    return products, publishers


def publisher_text(key: str, pub: dict) -> str:
    return yaml.safe_dump({"key": key, "name": pub["name"], "source": pub["page"], "aliases": []},
                          sort_keys=False, allow_unicode=True, width=200)


def files_of(products: dict, publishers: dict) -> dict[str, str]:
    files = {f"publishers/{k}.yaml": publisher_text(k, v) for k, v in publishers.items()}
    files.update({f"products/{k}.yaml": p.text() for k, p in products.items()})
    return files


def guard(products: dict, publishers: dict, root: Path = ROOT, data: lint.Dataset | None = None) -> list:
    """The lint on everything the round writes, against TAX. A product with an error or a TV3 signal is
    set apart with that finding; so is every product of a publisher whose file has one. Repeated until
    the lint finds nothing. Returns the findings that set something apart."""
    data = data or lint.Dataset(root, detailed=set())
    apart = []
    while True:
        files = files_of(products, publishers)
        found = data.check(report=set(files), overrides=files)
        hit: dict[str, lint.Finding] = {}
        for f in found:
            if f.level != "signal" or f.rule in ("TV3", "TK3"):
                hit.setdefault(f.file, f)
        if not hit:
            return apart
        for rel, f in sorted(hit.items()):
            apart.append(f)
            reason = {"TV3": "a name like an existing one (TV3): a person decides",
                      "TK3": "a duplicate: a key or alias in TAX has the same words (TK3)"}.get(f.rule, f"{f.rule}: {f.message}")
            if rel.startswith("publishers/"):
                pub = rel[len("publishers/"):-len(".yaml")]
                publishers.pop(pub, None)
                for key in [k for k, p in products.items() if p.publisher == pub]:
                    for m in products.pop(key).members:
                        _set(m, "apart", f"its publisher: {reason}", key)
            else:
                key = rel[len("products/"):-len(".yaml")]
                for m in products.pop(key, Product(key, "", "")).members:
                    _set(m, "apart", reason, key)


def batches(products: dict, publishers: dict, size: int = SIZE) -> list[dict[str, str]]:
    """The files in batches of at most `size`; a publisher and its products stay together."""
    by_pub: dict[str, list[str]] = defaultdict(list)
    for key, p in sorted(products.items()):
        by_pub[p.publisher].append(key)
    out, cur = [], {}
    for pub in sorted(by_pub):
        group = {f"products/{k}.yaml": products[k].text() for k in by_pub[pub]}
        if pub in publishers:
            group[f"publishers/{pub}.yaml"] = publisher_text(pub, publishers[pub])
        if cur and len(cur) + len(group) > size:
            out.append(cur)
            cur = {}
        cur.update(group)  # a single publisher with more products than `size` stays whole
    if cur:
        out.append(cur)
    return out


def kind_of(reason: str) -> str:
    """A reason without its values, so that the report counts kinds of reasons."""
    out = re.sub(r"'[^']*'|\S+\.yaml|\d+ characters", "…", reason)
    return re.sub(r"^(TE1|TK2): \S+?: ", r"\1: ", out)


def report(packages: list[Package], products: dict, publishers: dict, dump: str, scope: str, parts: list) -> str:
    by = Counter(p.outcome for p in packages)
    reasons = Counter(kind_of(p.reason) for p in packages if p.outcome in ("apart", "in-tax", "later"))
    total = len(packages)
    considered = total - by["later"]
    lines = [f"## winget as a filler ({dump}, scope {scope})", "",
             f"{total:,} packages (the newest version of each). {len(products):,} products and "
             f"{len(publishers):,} new publishers, in {len(parts)} batch(es) of at most {SIZE:,} files. No AI call.", "",
             "| outcome | packages | share |", "| --- | ---: | ---: |"]
    for o in ("taken-in", "in-tax", "apart", "later"):
        lines.append(f"| {o} | {by[o]:,} | {by[o] / total:.1%} |")
    if considered:
        lines += ["", f"Taken in, of the packages in scope: {by['taken-in'] / considered:.1%} "
                      f"({by['taken-in']:,} of {considered:,}). That share is the measure of access."]
    lines += ["", "| reason | packages |", "| --- | ---: |"]
    lines += [f"| {r} | {n:,} |" for r, n in reasons.most_common()]
    return "\n".join(lines) + "\n"


def run(dump: Path, out: Path, root: Path = ROOT, scope: str = "known", write: bool = False,
        batch: int | None = None, size: int = SIZE) -> dict:
    packages = read(dump)
    products, publishers = plan(packages, root, scope)
    guard(products, publishers, root)
    parts = batches(products, publishers, size)
    out.mkdir(parents=True, exist_ok=True)
    target = root if write else out / "data"
    chosen = parts if batch is None else [parts[batch - 1]] if 0 < batch <= len(parts) else []
    for part in chosen:
        for rel, text in part.items():
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            (target / rel).write_text(text, encoding="utf-8")
    text = report(packages, products, publishers, dump.name, scope, parts)
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "outcomes.json").write_text(json.dumps([{k: v for k, v in asdict(p).items()} for p in packages],
                                                  indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
    rng = random.Random(len(packages))  # the same sample for the same dump
    picked = rng.sample(sorted(products), min(50, len(products)))
    (out / "sample.json").write_text(json.dumps(  # for the tester (TAX-19, criterion 8): check against the manifest
        [{"key": k, "name": products[k].name, "publisher": products[k].publisher,
          "packages": [{"id": m.id, "publisher": m.publisher, "name": m.name, "page": m.page} for m in products[k].members]}
         for k in picked], indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "batches.json").write_text(json.dumps([sorted(b) for b in parts], indent=0) + "\n", encoding="utf-8")
    return {"packages": packages, "products": products, "publishers": publishers, "batches": parts, "report": text}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dump", required=True, help="winget-pkgs as one tar.gz")
    p.add_argument("--scope", choices=("known", "all"), default="known",
                   help="known: only packages of publishers TAX knows; all: every package")
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--write", action="store_true", help="write into the dataset (else OUT/data)")
    p.add_argument("--batch", type=int, help="write only this batch (1, 2, ...)")
    p.add_argument("--size", type=int, default=SIZE, help="files per batch at most")
    a = p.parse_args(argv)
    got = run(Path(a.dump), Path(a.out), Path(a.root), a.scope, a.write, a.batch, a.size)
    print(got["report"], end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
