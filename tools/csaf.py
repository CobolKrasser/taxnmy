#!/usr/bin/env python3
"""CSAF as a filler (TAX-23): the products that CSAF advisories name become products in TAX, in one
round, with no AI call. The first filling by the maintainer, with only the basic logic of TAX-19: the
CSAF address (a provider-metadata.json) that names a product is the source of the product and of its
versions (criterion 2). The lint guards what goes in.

A product is a branch `product_name` of the product tree of an advisory, under its `vendor`; the
versions below it are its versions (TP3). An order number or a version in brackets in the name marks a
variant (TP3, Fred 22 November 2026) and is left out, as is the word "firmware" at the end: the firmware
is the version of the device. Names that give the same key are one product, also from two addresses.

The kind follows from the advisory (criterion 4), by the first of these rules that applies (Fred,
22 November 2026):

    R1  a CPE in product_identification_helper: its part (h a device, o an operating system, a software)
    R2  a purl: a package, so software
    R3  the word "firmware" in the name: a device (the firmware is its version)
    R4  model_numbers, skus or serial_numbers: a device
    R6  the product is installed on another product (relationship installed_on): software
    R8  the CPE dictionary of the NVD has the vendor and the product, exactly: its part

A product without a kind is set apart, with that reason. Every product gets one outcome with a reason
(TR9): taken-in, in-tax (TAX has its key, its long form or its CPE or purl) or apart.

A publisher TAX knows keeps its key and its source (criterion 7); a new one gets the CSAF address as its
source. Every publisher of a product taken in gets the CSAF address as a source for machines (TE7,
criterion 8). The addresses are data in referee/csaf.yaml (criterion 9); the code reads per format,
never per brand (TG4).

    python tools/csaf.py fetch --dump DUMP                 # the documents of the addresses, into DUMP
    python tools/csaf.py round --dump DUMP --cpe-dictionary nvdcpe-2.0.tar.gz --out OUT [--write]

`fetch` reads per address its provider metadata, then each directory (index.txt) and each ROLIE feed it
names, and stores each advisory as DUMP/docs/<sha1>.json.gz, with DUMP/index.tsv (address, url, file).
A directory or feed whose address holds a path of `skip_paths` (referee/csaf.yaml) is not read. An
address or advisory that refuses (robots.txt, 401, 403, 429 twice) is written to DUMP/refused.tsv and
is not tried again (TE5, criterion 10). Without --write the round writes to OUT/data.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sys
import tarfile
import urllib.parse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import census  # noqa: E402
import keyrule  # noqa: E402
import lint  # noqa: E402
import winget  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIST = ROOT / "referee/csaf.yaml"
KIND = {"h": "device", "o": "operating-system", "a": "software"}
ORDER = ("device", "operating-system", "software")
FORMAT = "csaf-provider"
FEED_BYTES = 200_000_000  # a ROLIE feed with 15,000 entries is about 35 MB
IDENTIFIER_ERROR = re.compile(r"^identifiers/(\d+)/")


def load_list(path: Path = LIST) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise SystemExit(f"{path}: not version 1")
    return data


# --------------------------------------------------------------------------- reading an advisory

@dataclass
class Entry:
    """A product as one advisory of one address names it."""
    address: str
    vendor: str
    name: str
    cpes: set = field(default_factory=set)
    purls: set = field(default_factory=set)
    helpers: set = field(default_factory=set)
    installed_on: bool = False


def _helper(node: dict) -> dict:
    return (node.get("product") or {}).get("product_identification_helper") or {}


def entries(data: dict, address: str) -> list[Entry] | None:
    """The products of a CSAF document, or None when it is no CSAF document."""
    doc = data.get("document") if isinstance(data, dict) else None
    if not isinstance(doc, dict) or "csaf_version" not in doc:
        return None
    publisher = ((doc.get("publisher") or {}).get("name") or "").strip()
    tree = data.get("product_tree") or {}
    found: list[tuple[Entry, set]] = []

    def collect(node: dict, e: Entry, ids: set) -> None:
        h = _helper(node)
        if h.get("cpe"):
            e.cpes.add(str(h["cpe"]))
        for p in [h.get("purl"), *(h.get("purls") or [])]:
            if p:
                e.purls.add(str(p))
        e.helpers.update(h.keys())
        pid = (node.get("product") or {}).get("product_id")
        if pid:
            ids.add(pid)
        for b in node.get("branches") or []:
            collect(b, e, ids)

    def walk(node: dict, vendor: str) -> None:
        for br in node.get("branches") or []:
            cat, name = br.get("category", ""), str(br.get("name") or "").strip()
            if cat == "vendor":
                walk(br, name)
            elif cat == "product_name":
                e, ids = Entry(address, vendor or publisher, name), set()
                collect(br, e, ids)
                found.append((e, ids))
            else:
                walk(br, vendor)

    walk(tree, "")
    if not found:
        for fp in tree.get("full_product_names") or []:
            e = Entry(address, publisher, str(fp.get("name") or "").strip())
            collect({"product": fp}, e, set())
            found.append((e, {fp.get("product_id")}))
    on = {r.get("product_reference") for r in tree.get("relationships") or [] if r.get("category") == "installed_on"}
    for e, ids in found:
        e.installed_on = bool(ids & on)
    return [e for e, _ in found]


# --------------------------------------------------------------------------- names, kinds, codes

ORDER_NUMBER = re.compile(r"\(\s*([^()]*\d[^()]*)\)")
VERSION_IN_BRACKETS = re.compile(r"\(\s*v\.?\s*[\d.]+\s*\)", re.I)
FIRMWARE_AT_END = re.compile(r"[\s,-]+firmware\s*$", re.I)


def product_name(name: str) -> str:
    """The name without the parts that mark a variant or a version (TP3): an order number in brackets (no
    lower-case letters in it), a version in brackets, and the word firmware at the end."""
    out = VERSION_IN_BRACKETS.sub(" ", name)
    out = ORDER_NUMBER.sub(lambda m: " " if not re.search(r"[a-z]", m.group(1)) else m.group(0), out)
    out = FIRMWARE_AT_END.sub("", out)
    out = re.sub(r"\s+", " ", out).strip(" -–,:/")
    return winget.base_name(out, "") if out else name.strip()  # a version in the name (TP3), as for winget


def cpe23(cpe: str) -> tuple[str, str, str] | None:
    """(part, vendor, product) of a CPE in either binding."""
    if cpe.startswith("cpe:2.3:"):
        f = cpe.split(":")
        return (f[2], f[3], f[4]) if len(f) > 4 else None
    if cpe.startswith("cpe:/"):
        f = cpe[5:].split(":")
        return (f[0], f[1], f[2]) if len(f) > 2 else None
    return None


def purl_base(purl: str) -> str:
    for sep in "@?#":
        purl = purl.split(sep, 1)[0]
    return purl.rstrip("/")


def _norm(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^0-9a-z]+", "_", (text or "").replace("\\", "").lower())).strip("_")


def read_dictionary(path: Path) -> dict[str, set]:
    """The CPE dictionary of the NVD (its feed, a tar.gz of JSON chunks) as `vendor|product` -> parts."""
    index: dict[str, set] = defaultdict(set)
    with tarfile.open(path) as t:
        for m in t:
            if not m.isfile() or not m.name.endswith(".json"):
                continue
            for p in json.load(t.extractfile(m)).get("products") or []:
                f = (p.get("cpe") or {}).get("cpeName", "").split(":")
                if len(f) > 4:
                    index[f"{_norm(f[3])}|{_norm(f[4])}"].add(f[2])
    return index


def _first(parts) -> str | None:
    kinds = {KIND[p] for p in parts if p in KIND}
    return next((k for k in ORDER if k in kinds), None)


def in_dictionary(vendor: str, name: str, dictionary: dict) -> str | None:
    """R8: the part of the CPE whose vendor and product are the vendor and the name, exactly (after
    folding). A device is often written twice there: the hardware, and `<product>_firmware` as its OS."""
    v = _norm(keyrule.strip_legal_form(vendor))
    prod = _norm(name)
    for vv in dict.fromkeys([v, v.split("_")[0], v.replace("_", "")]):
        if not vv:
            continue
        if f"{vv}|{prod}" in dictionary:
            return _first(dictionary[f"{vv}|{prod}"])
        if f"{vv}|{prod}_firmware" in dictionary:
            return "device"
    return None


RULES = ("R1 a CPE of the product: its part", "R2 a purl: software", "R3 the word firmware in the name: a device",
         "R4 a model number, SKU or serial number: a device", "R6 installed on another product: software",
         "R8 the CPE dictionary of the NVD: its part")


def kind_of(entries_: list[Entry], name: str, vendor: str, dictionary: dict) -> tuple[str | None, str]:
    """(kind, rule) by the first rule that applies to the product, over all its entries."""
    cpes = {c for e in entries_ for c in e.cpes}
    parts = [p[0] for p in map(cpe23, cpes) if p]
    if _first(parts):
        return _first(parts), RULES[0]
    if any(e.purls for e in entries_):
        return "software", RULES[1]
    if any(re.search(r"\bfirmware\b", e.name, re.I) for e in entries_):
        return "device", RULES[2]
    if any(e.helpers & {"model_numbers", "skus", "serial_numbers"} for e in entries_):
        return "device", RULES[3]
    if any(e.installed_on for e in entries_):
        return "software", RULES[4]
    got = in_dictionary(vendor, name, dictionary)
    if got:
        return got, RULES[5]
    return None, ""


# --------------------------------------------------------------------------- the round

@dataclass
class Item:
    """A product as one address names it: the unit that gets an outcome (TR9)."""
    address: str
    vendor: str
    name: str
    outcome: str = ""
    reason: str = ""
    product: str = ""


@dataclass
class Product:
    key: str
    publisher: str
    name: str
    source: str = ""
    kind: str = ""
    rule: str = ""
    identifiers: list = field(default_factory=list)
    entries: list = field(default_factory=list)
    items: list = field(default_factory=list)
    aliases: list = field(default_factory=list)  # the other models of a row of models (TAX-62)

    def text(self) -> str:
        data = {"key": self.key, "publisher": self.publisher, "name": self.name, "kind": self.kind,
                "source": self.source, "versions": {"source": self.source}}
        if self.identifiers:
            data["identifiers"] = [{k: v, "source": self.source} for k, v in self.identifiers]
        data["aliases"] = [a for a in [keyrule.long_form(self.key, self.name), *self.aliases] if a]  # TAX-54
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=200)


class Tax:
    """What TAX has, for the comparison: keys and aliases, CPEs and purls, publishers."""

    def __init__(self, root: Path):
        self.census = census.Tax(root)
        self.keys: dict[str, str] = {}
        self.codes: dict[str, str] = {}
        for f in sorted((root / "products").glob("*/*.yaml")):
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            key = d.get("key", "")
            for k in [key, *(d.get("aliases") or [])]:
                self.keys.setdefault(k, key)
            for ident in d.get("identifiers") or []:
                if "cpe" in ident and cpe23(str(ident["cpe"])):
                    _, v, p = cpe23(str(ident["cpe"]))
                    self.codes.setdefault(f"cpe:{v}:{p}".lower(), key)
                if "purl" in ident:
                    self.codes.setdefault(purl_base(str(ident["purl"])).lower(), key)

    def publisher(self, vendor: str) -> str | None:
        found = self.census.publisher([vendor])
        if found:
            return found
        try:
            part = keyrule.publisher_part(vendor)
        except keyrule.KeyRuleError:
            return None
        return part if part in self.census.publishers else None

    def has(self, key: str, name: str, codes: list[str]) -> str:
        for k in (key, keyrule.long_form(key, name)):
            if k and k in self.keys:
                return self.keys[k]
        return next((self.codes[c] for c in codes if c in self.codes), "")


def _set(item: Item, outcome: str, reason: str = "", product: str = "") -> None:
    item.outcome, item.reason, item.product = outcome, reason, product


def _codes(entries_: list[Entry]) -> tuple[list[tuple[str, str]], list[str]]:
    """(identifiers for the file, folded codes for the comparison with TAX)."""
    idents, seen, fold = [], set(), []
    for e in entries_:
        for c in sorted(e.cpes):
            p = cpe23(c)
            if p and f"cpe:{p[1]}:{p[2]}" not in seen:
                seen.add(f"cpe:{p[1]}:{p[2]}")
                idents.append(("cpe", f"cpe:2.3:{p[0]}:{p[1]}:{p[2]}"))
                fold.append(f"cpe:{p[1]}:{p[2]}".lower())
        for u in sorted(e.purls):
            b = purl_base(u)
            if b not in seen:
                seen.add(b)
                idents.append(("purl", b))
                fold.append(b.lower())
    return idents, fold


def several(vendor: str) -> bool:
    """A vendor field that names more than one vendor: a semicolon, or two commas or more that do not
    only stand before a legal form ("Software, LLC")."""
    if ";" in vendor:
        return True
    parts = [p for p in re.split(r",|\band\b", vendor) if p.strip()]
    return sum(1 for p in parts if keyrule.strip_legal_form(p).strip(" .")) >= 3 and vendor.count(",") >= 2


KINDS = ("device", "operating-system", "software")
AI_RULE = "R11 the kind an AI run gave (Fred, 23 November 2026)"


def read_kinds(path: Path) -> dict[tuple[str, str], str]:
    """(vendor, name) -> the kind an AI run gave, from a CSV with the columns vendor, name and kind (TAX-23).
    The file stays outside the repository; a kind other than the three of TAX (`unknown`) is kept as given."""
    with path.open(newline="", encoding="utf-8") as f:
        return {(r["vendor"], r["name"]): r["kind"].strip() for r in csv.DictReader(f)}


def plan(docs, root: Path = ROOT, data: dict | None = None, dictionary: dict | None = None,
         kinds: dict | None = None):
    """(items, products by key, new publishers by key: {name, source}, publishers by key: their addresses).
    `docs` yields (address, CSAF document as dict). `kinds`: the kind an AI run gave per (vendor, name), used
    only when no rule gives one (R11)."""
    data = data or load_list()
    dictionary = dictionary or {}
    kinds = kinds or {}
    tax = Tax(root)
    role = {a["url"]: a.get("role", "vendor") for a in data.get("addresses") or []}
    rank = {u: (role[u] != "vendor", i) for i, u in enumerate(role)}
    skip = {census.fold(n) for n in data.get("not_publishers") or []}
    teams = [census.fold(n) for n in data.get("team_words") or []]
    grouped: dict[tuple, list[Entry]] = defaultdict(list)  # (address, vendor, name) -> entries
    for address, doc in docs:
        for e in entries(doc, address) or []:
            grouped[(address, e.vendor, e.name)].append(e)
    items: list[Item] = []
    by_key: dict[str, list[tuple[Item, list[Entry], str]]] = defaultdict(list)
    for (address, vendor, name), es in sorted(grouped.items(), key=lambda x: (rank.get(x[0][0], (True, 99)), x[0])):
        item = Item(address, vendor, name)
        items.append(item)
        if not vendor or not name:
            _set(item, "apart", "the product tree gives no vendor or no name, so no key (TK2)")
            continue
        if census.fold(vendor) in skip or any(w in census.fold(vendor) for w in teams):
            _set(item, "apart", "the vendor in the product tree names no publisher (referee/csaf.yaml)")
            continue
        if several(vendor):
            _set(item, "apart", "the vendor in the product tree names several vendors, so no one publisher")
            continue
        clean = product_name(name)
        try:
            pub = tax.publisher(vendor) or keyrule.publisher_part(vendor)
            key = f"{pub}/{keyrule.product_part(clean, pub)}"
        except keyrule.KeyRuleError:
            _set(item, "apart", "a name outside the Latin script, so no key (TK2)")
            continue
        by_key[key].append((item, es, clean))
    products: dict[str, Product] = {}
    spellings: dict[str, Counter] = defaultdict(Counter)
    first_source: dict[str, str] = {}
    addresses: dict[str, list] = defaultdict(list)
    for key, members in by_key.items():
        pub = key.split("/")[0]
        es = [e for _, group, _ in members for e in group]
        item0, _, clean = members[0]
        idents, fold = _codes(es)
        have = tax.has(key, clean, fold)
        if have:
            for item, _, _ in members:
                _set(item, "in-tax", "TAX has its key, its long form, or its CPE or purl (TK3): no new file", have)
            continue
        kind, rule = kind_of(es, clean, item0.vendor, dictionary)
        given = [kinds[(i.vendor, i.name)] for i, _, _ in members if (i.vendor, i.name) in kinds]
        if not kind and given:
            known = Counter(k for k in given if k in KINDS)
            if not known:
                for item, _, _ in members:
                    _set(item, "apart", "no kind: the AI run could not tell the kind either (unknown)", key)
                continue
            kind, rule = known.most_common(1)[0][0], AI_RULE
        if not kind:
            for item, _, _ in members:
                _set(item, "apart", "no kind: no CPE, purl, firmware, model number or relation, and not in the "
                                    "CPE dictionary (criterion 4)", key)
            continue
        p = Product(key, pub, clean, item0.address, kind, rule, idents, es, [m[0] for m in members])
        products[key] = p
        for item, _, _ in members:
            _set(item, "taken-in", rule if item is item0 else "the same key as another entry: one product (TP3)", key)
        for item, _, _ in members:
            if item.address not in addresses[pub]:
                addresses[pub].append(item.address)
        if not tax.publisher(item0.vendor) and pub not in tax.census.publishers:
            for item, _, _ in members:
                spellings[pub][item.vendor] += 1
            first_source.setdefault(pub, item0.address)
    new = {pub: {"name": c.most_common(1)[0][0], "source": first_source[pub]} for pub, c in spellings.items()}
    for pub, urls in addresses.items():  # its own CSAF address when it has one (criterion 8), else the coordinator's
        own = [u for u in urls if role.get(u) == "vendor"]
        addresses[pub] = own or urls
    return items, products, new, dict(addresses)


def machine_sources(urls: list[str]) -> list[dict]:
    return [{"url": u, "format": FORMAT, "source": u} for u in urls]


def publisher_text(key: str, pub: dict, urls: list[str]) -> str:
    data = {"key": key, "name": pub["name"], "source": pub["source"]}
    if urls:
        data["machine_sources"] = machine_sources(urls)
    data["aliases"] = []
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=200)


def with_machine_sources(text: str, urls: list[str]) -> str:
    """The file of a publisher in TAX with the CSAF addresses as sources for machines (TE7); its key and
    its source stay (criterion 7). Unchanged when it names them all already."""
    data = yaml.safe_load(text) or {}
    have = {m.get("url") for m in data.get("machine_sources") or []}
    todo = [u for u in urls if u not in have]
    if not todo:
        return text
    entries_ = "".join(f"- url: {u}\n  format: {FORMAT}\n  source: {u}\n" for u in todo)
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    top = [i for i, line in enumerate(lines) if re.match(r"[a-z_]+:", line)]
    at = next((i for i in top if lines[i].startswith("machine_sources:")), None)
    if at is not None:
        end = next((i for i in top if i > at), len(lines))
        return "".join(lines[:end] + [entries_] + lines[end:])
    end = next((i for i in top if lines[i].startswith(("second_source:", "aliases:"))), len(lines))
    return "".join(lines[:end] + ["machine_sources:\n", entries_] + lines[end:])


def files_of(products: dict, publishers: dict, addresses: dict, root: Path = ROOT) -> dict[str, str]:
    files = {}
    for pub, urls in sorted(addresses.items()):
        if not any(p.publisher == pub for p in products.values()):
            continue
        rel = f"publishers/{pub}.yaml"
        if pub in publishers:
            files[rel] = publisher_text(pub, publishers[pub], urls)
        elif (root / rel).exists():
            text = (root / rel).read_text(encoding="utf-8")
            new = with_machine_sources(text, urls)
            if new != text:
                files[rel] = new
    files.update({f"products/{k}.yaml": p.text() for k, p in products.items()})
    return files


def guard(products: dict, publishers: dict, addresses: dict, root: Path = ROOT, dropped: list | None = None) -> list:
    """The lint on everything the round writes, against TAX. Only an error sets a product apart (basic
    logic, TAX-19); a signal stays for a person. An identifier the schema refuses is left out (into
    `dropped`) and the product stays. Repeated until the lint finds no error. Returns the last findings."""
    data = lint.Dataset(root, detailed=set())
    dropped = [] if dropped is None else dropped
    while True:
        files = files_of(products, publishers, addresses, root)
        found = data.check(report=set(files), overrides=files)
        errors: dict[str, lint.Finding] = {}
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
                addresses.pop(pub, None)
                for key in [k for k, p in products.items() if p.publisher == pub]:
                    for item in products.pop(key).items:
                        _set(item, "apart", f"its publisher: {reason}", key)
                continue
            key = rel[len("products/"):-len(".yaml")]
            code = IDENTIFIER_ERROR.match(f.message)
            if f.rule == "TE1" and code and key in products and int(code.group(1)) < len(products[key].identifiers):
                gone = products[key].identifiers.pop(int(code.group(1)))
                dropped.append((key, f"{gone[0]} {gone[1]!r}"))
                continue
            for item in products.pop(key, Product(key, "", "")).items:
                _set(item, "apart", reason, key)


# --------------------------------------------------------------------------- the dump

def read_dump(dump: Path):
    """(address, document) for every advisory in the dump."""
    with (dump / "index.tsv").open(encoding="utf-8") as f:
        for line in f:
            address, _url, rel = line.rstrip("\n").split("\t")
            raw = (dump / rel).read_bytes()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            try:
                yield address, json.loads(raw)
            except ValueError:
                continue


def refused(dump: Path) -> list[tuple[str, str]]:
    path = dump / "refused.tsv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [tuple(line.rstrip("\n").split("\t", 1)) for line in f if line.strip()]


def fetch(dump: Path, data: dict | None = None, fetcher=None, log=print) -> Counter:
    """The advisories of the addresses into the dump. Each request with the conduct of tools/fetch.py: an
    honest User-Agent, robots.txt, one request per second per site. A refusal is written down and not
    tried again (criterion 10). Advisories already in the dump are not fetched again."""
    import fetch as fetching
    fetching.MAX_BYTES = max(fetching.MAX_BYTES, FEED_BYTES)  # a ROLIE feed of a coordinator is larger than a page
    data = data or load_list()
    f = fetcher or fetching.Fetcher(use_browser=False, timeout=60)
    dump.mkdir(parents=True, exist_ok=True)
    (dump / "docs").mkdir(exist_ok=True)
    done = set()
    if (dump / "index.tsv").exists():
        with (dump / "index.tsv").open(encoding="utf-8") as lines:
            done = {line.split("\t")[1] for line in lines}
    gone = {u for u, _ in refused(dump)}
    index = (dump / "index.tsv").open("a", encoding="utf-8")
    no = (dump / "refused.tsv").open("a", encoding="utf-8")
    count: Counter = Counter()

    def get(url: str):
        if url in gone:
            return None
        if not f.allowed(url):
            no.write(f"{url}\trobots.txt\n")
            gone.add(url)
            count["refused"] += 1
            return None
        r = f._request(url)
        if r.status == 429:
            r = f._request(url)
        if r.status != 200:
            no.write(f"{url}\t{r.status or r.error}\n")
            gone.add(url)
            count["refused"] += 1
            return None
        return r.body

    skip = tuple(data.get("skip_paths") or ())
    for a in data.get("addresses") or []:
        address = a["url"]
        body = get(address)
        try:
            meta = json.loads(body) if body else {}
        except ValueError:
            meta = {}
        urls: list[str] = []
        for dist in meta.get("distributions") or []:
            directory = dist.get("directory_url")
            if directory and not any(s in directory for s in skip):
                listing = get(directory.rstrip("/") + "/index.txt")
                for line in (listing or b"").decode("utf-8", "replace").split():
                    urls.append(urllib.parse.urljoin(directory.rstrip("/") + "/", line.strip()))
            for feed in (dist.get("rolie") or {}).get("feeds") or []:
                if any(s in feed.get("url", "") for s in skip):
                    continue
                text = get(feed.get("url", ""))
                try:
                    found = json.loads(text) if text else {}
                except ValueError:
                    found = {}
                for e in (found.get("feed") or {}).get("entry") or []:
                    urls += [link["href"] for link in e.get("link") or [] if link.get("rel") == "self"]
        for url in dict.fromkeys(u for u in urls if u.endswith(".json") and not any(s in u for s in skip)):
            if url in done:
                continue
            body = get(url)
            if body is None:
                continue
            rel = f"docs/{hashlib.sha1(url.encode()).hexdigest()}.json.gz"
            (dump / rel).write_bytes(gzip.compress(body))
            index.write(f"{address}\t{url}\t{rel}\n")
            index.flush()
            done.add(url)
            count["documents"] += 1
        log(f"{address}: {len(urls)} advisories named")
    index.close()
    no.close()
    return count


# --------------------------------------------------------------------------- report

def report(items: list[Item], products: dict, publishers: dict, refusals: list, signals: list, dropped: list,
           devices_before: int) -> str:
    by = Counter(i.outcome for i in items)
    kinds = Counter(p.kind for p in products.values())
    rules = Counter(p.rule for p in products.values())
    out = ["## CSAF as a filler (TAX-23)", "",
           f"{len(items):,} products as the addresses name them. {len(products):,} products and {len(publishers):,} "
           f"new publishers. No AI call in the round"
           + (f"; {rules[AI_RULE]:,} kinds from an AI run before it (R11)" if rules[AI_RULE] else "")
           + f". {len(signals)} signals of the lint, for a person.", "",
           "| outcome | products as named |", "| --- | ---: |"]
    out += [f"| {o} | {by[o]:,} |" for o in ("taken-in", "in-tax", "apart")]
    out += ["", "| kind | products taken in |", "| --- | ---: |"]
    out += [f"| {k} | {kinds[k]:,} |" for k in ORDER]
    out += ["", f"Devices in TAX: {devices_before:,} before, {devices_before + kinds['device']:,} after.", "",
            "| rule that gave the kind | products |", "| --- | ---: |"]
    out += [f"| {r} | {rules[r]:,} |" for r in (*RULES, AI_RULE) if r in RULES or rules[r]]
    per: dict[str, Counter] = defaultdict(Counter)
    for i in items:
        per[i.address][i.outcome] += 1
    out += ["", "| address | taken-in | in-tax | apart |", "| --- | ---: | ---: | ---: |"]
    out += [f"| {a} | {c['taken-in']:,} | {c['in-tax']:,} | {c['apart']:,} |" for a, c in sorted(per.items())]
    reasons = Counter((i.outcome, re.sub(r"'[^']*'|\S+\.yaml(?!\))", "…", i.reason)) for i in items
                      if i.outcome != "taken-in" or not i.reason.startswith("R"))
    out += ["", "| outcome | reason | count |", "| --- | --- | ---: |"]
    out += [f"| {o} | {r} | {n:,} |" for (o, r), n in sorted(reasons.items(), key=lambda x: (x[0][0], -x[1]))]
    if refusals:
        why = Counter((urllib.parse.urlsplit(u).netloc, r) for u, r in refusals)
        out += ["", "Refused, not tried again (criterion 10):", "", "| site | answer | count |", "| --- | --- | ---: |"]
        out += [f"| {h} | {r} | {n:,} |" for (h, r), n in sorted(why.items())]
    if dropped:
        out += ["", f"Identifiers left out because the schema refuses them (the product stays): {len(dropped)}."]
    return "\n".join(out) + "\n"


def devices(root: Path) -> int:
    return sum(1 for f in (root / "products").glob("*/*.yaml") if re.search(r"(?m)^kind: device$", f.read_text(encoding="utf-8")))


def run(docs, out: Path, root: Path = ROOT, write: bool = False, data: dict | None = None,
        dictionary: dict | None = None, refusals: list = (), kinds: dict | None = None) -> dict:
    data = data or load_list()
    before = devices(root)
    items, products, publishers, addresses = plan(docs, root, data, dictionary, kinds)
    dropped: list = []
    signals = [f for f in guard(products, publishers, addresses, root, dropped) if f.level == "signal"]
    files = files_of(products, publishers, addresses, root)
    out.mkdir(parents=True, exist_ok=True)
    target = root if write else out / "data"
    for rel, text in files.items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text, encoding="utf-8")
    text = report(items, products, publishers, list(refusals), signals, dropped, before)
    (out / "report.md").write_text(text, encoding="utf-8")
    (out / "outcomes.json").write_text(json.dumps([asdict(i) for i in items], indent=0, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    with (out / "not-taken-in.csv").open("w", newline="", encoding="utf-8") as f:  # for referee/import/
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["address", "vendor", "name", "outcome", "reason", "product"])
        w.writerows([i.address, i.vendor, i.name, i.outcome, i.reason, i.product] for i in items if i.outcome != "taken-in")
    return {"items": items, "products": products, "publishers": publishers, "files": files, "signals": signals,
            "dropped": dropped, "report": text}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="the advisories of the addresses into a dump")
    f.add_argument("--dump", required=True)
    r = sub.add_parser("round", help="the round on a dump")
    r.add_argument("--dump", required=True)
    r.add_argument("--cpe-dictionary", help="the feed of the CPE dictionary of the NVD (tar.gz), for R8")
    r.add_argument("--out", required=True)
    r.add_argument("--root", default=str(ROOT))
    r.add_argument("--write", action="store_true", help="write into the dataset (else OUT/data)")
    r.add_argument("--kinds", help="a CSV (vendor, name, kind) of an AI run, for what no rule gives a kind (R11)")
    a = p.parse_args(argv)
    if a.cmd == "fetch":
        print(dict(fetch(Path(a.dump))))
        return 0
    dictionary = read_dictionary(Path(a.cpe_dictionary)) if a.cpe_dictionary else {}
    got = run(read_dump(Path(a.dump)), Path(a.out), Path(a.root), a.write, dictionary=dictionary,
              refusals=refused(Path(a.dump)), kinds=read_kinds(Path(a.kinds)) if a.kinds else None)
    print(got["report"], end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
