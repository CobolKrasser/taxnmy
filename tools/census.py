#!/usr/bin/env python3
"""The count before the bulk (TAX-25): how many candidates a source of third parties offers, and what
code can fill from it. It writes nothing in the dataset: it reads three public downloads and prints
numbers. No API, no key.

    cve     the CVE List v5 as one zip (the release `all_CVEs_at_midnight` of CVEProject/cvelistV5),
            with the official list of CNAs (`CNAsList.json` of CVEProject/cve-website). Records
            published in the last three years before the dump, of CNAs whose type in that list is
            only Vendor, and of mixed CNAs (Vendor and another type) whose publisher TAX knows.
    winget  the winget-pkgs repository as one tar.gz, read as a stream: per package its newest version.

Per candidate it asks: does `kind`, `source` or `versions.source` follow from the record, and is the
name a version or a range rather than a product? The rules for that are general (classes of words and
the shape of a number); they name no brand and no product (R3). Whether the publisher and the product
are already in TAX is decided by exact comparison of normalised names, aliases, domains and codes.

The cost of a round follows `referee/prices.yaml`: one call per publisher (grouping the names, TAX-26)
and one per candidate (the referee), as a batch. The tokens per call are below, with their source.

    python tools/census.py cve --dump cves.zip --cnas CNAsList.json --out census-cve.json
    python tools/census.py winget --dump winget-pkgs.tar.gz --out census-winget.json
    python tools/census.py report census-cve.json census-winget.json > census.md
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tarfile
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard import owner  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- tokens per call

# The referee, per candidate: the measurement of TAX-18 gave $0.0271 per proposal on Opus 5.5 low as a
# batch. At the batch prices of `referee/prices.yaml` these tokens give that amount (the input of the
# fetched runs of 13 November, the output solved from the measured cost).
CANDIDATE_TOKENS = {"input": 8000, "cache_read": 2700, "output": 1080}
# Grouping, per publisher (TAX-26): a prompt with the products TAX has, plus every name with its
# records; out comes one line per name. An estimate until TAX-26 measures it.
GROUP_BASE = {"input": 2500, "cache_read": 0, "output": 200}
GROUP_PER_NAME = {"input": 30, "output": 40}

# The groups of TAX-22, by the number of unique product names of a CNA.
GROUPS = ((1, 20, "small"), (21, 200, "medium"), (201, 10**9, "large"))

# --------------------------------------------------------------------------- names

# Words that say what kind of company a name is, not which one.
LEGAL = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "lp", "plc",
         "gmbh", "ag", "kg", "se", "sa", "sas", "srl", "spa", "bv", "nv", "ab", "as", "asa", "oy", "oyj",
         "kk", "pte", "pty", "group", "holdings", "the", "international", "technologies", "technology",
         "systems", "software", "electronics", "electric", "industries", "networks", "labs", "solutions"}

VERSION_RULES = (
    ("dotted number", re.compile(r"(?<![\w.])[vr]?\d+(?:\.\d+)+[a-z]?(?![\w.])", re.I)),
    ("v-number", re.compile(r"(?<![\w.])v\d+\b", re.I)),
    ("year", re.compile(r"(?<!\d)(?:19[89]\d|20[0-4]\d)(?!\d)")),
    ("version word", re.compile(r"\b(?:version|versions|release|build|update|patch|hotfix|service pack|sp\d+|firmware v)\b", re.I)),
)
RANGE_RULES = (
    ("range word", re.compile(r"\b(?:series|family|families|multiple|various|products|models|devices|line[- ]?up|all)\b", re.I)),
    ("wildcard", re.compile(r"(?:\b[\w-]*\d[\w-]*x{1,3}\b|\*|\bx{2,}\b)", re.I)),
    ("list", re.compile(r",|;| / | & |\band\b|\bor\b", re.I)),
)


def fold(text: str) -> str:
    """Letters and digits only, lower case, accents removed: the comparison of names."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text.lower() if c.isalnum())


def fold_company(text: str) -> str:
    """A company name without the words that only say it is a company."""
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKD", text or "").lower())
    kept = [w for w in words if w not in LEGAL]
    return "".join(kept or words)


def shape(name: str) -> str:
    """'version', 'range' or '' for a product name, by the first general rule that matches."""
    for _, rule in VERSION_RULES:
        if rule.search(name):
            return "version"
    for _, rule in RANGE_RULES:
        if rule.search(name):
            return "range"
    return ""


EMPTY = {"", "na", "none", "unknown", "unspecified", "notapplicable"}

# --------------------------------------------------------------------------- what TAX has


class Tax:
    """The publishers and products of the dataset, indexed for exact comparison."""

    def __init__(self, root: Path = ROOT):
        self.publishers: dict[str, dict] = {}
        self.by_name: dict[str, str] = {}
        self.by_domain: dict[str, str] = {}
        self.products: dict[str, set[str]] = defaultdict(set)   # publisher -> folded names and aliases
        self.cpe_vendor: dict[str, str] = {}                     # cpe vendor -> publisher
        self.cpe_product: set[tuple[str, str]] = set()           # (vendor, product)
        self.winget: set[str] = set()
        for f in sorted((root / "publishers").glob("*.yaml")):
            p = yaml.safe_load(f.read_text()) or {}
            key = p.get("key") or f.stem
            self.publishers[key] = p
            for n in [key, p.get("name", "")] + list(p.get("aliases") or []):
                for folded in (fold(n), fold_company(n)):
                    if folded:
                        self.by_name.setdefault(folded, key)
            dom = owner(p.get("source") or "")
            if dom:
                self.by_domain.setdefault(dom, key)
        for f in sorted((root / "products").glob("*/*.yaml")):
            p = yaml.safe_load(f.read_text()) or {}
            pub = p.get("publisher") or f.parent.name
            pubname = (self.publishers.get(pub) or {}).get("name", pub)
            for n in [p.get("name", ""), (p.get("key") or "").split("/")[-1]] + list(p.get("aliases") or []):
                folded = fold(n)
                if folded:
                    self.products[pub].add(folded)
                    self.products[pub].add(fold(f"{pubname} {n}"))
            dom = owner(p.get("source") or "")
            if dom and dom not in self.by_domain:
                self.by_domain[dom] = pub
            for ident in p.get("identifiers") or []:
                if "cpe" in ident:
                    parts = str(ident["cpe"]).split(":")
                    if len(parts) >= 5:
                        self.cpe_vendor.setdefault(parts[3], pub)
                        self.cpe_product.add((parts[3], parts[4]))
                if "winget" in ident:
                    self.winget.add(str(ident["winget"]).lower())

    def publisher(self, names=(), domains=()) -> str | None:
        for n in names:
            for folded in (fold(n), fold_company(n)):
                if folded and folded in self.by_name:
                    return self.by_name[folded]
        for d in domains:
            if d and d in self.by_domain:
                return self.by_domain[d]
        return None

    def has_product(self, publisher: str | None, name: str, cpes=()) -> bool:
        if any(c in self.cpe_product for c in cpes):
            return True
        if not publisher:
            return False
        pubname = (self.publishers.get(publisher) or {}).get("name", publisher)
        return fold(name) in self.products[publisher] or fold(f"{pubname} {name}") in self.products[publisher]


# --------------------------------------------------------------------------- CVE


def cna_list(path: Path) -> dict[str, dict]:
    """shortName (lower case) -> {'org', 'types', 'class', 'domains'} from the official list."""
    out = {}
    for c in json.loads(Path(path).read_text()):
        types = sorted(t for t in (c.get("CNA") or {}).get("type") or [] if t and t != "N/A")
        klass = "vendor" if types == ["Vendor"] else "mixed" if "Vendor" in types else "other"
        urls = [a.get("url", "") for a in (c.get("securityAdvisories") or {}).get("advisories") or []]
        urls += [u.get("url", "") for k in c.get("contact") or [] for u in k.get("contact") or []]
        emails = [e.get("emailAddr", "") for k in c.get("contact") or [] for e in k.get("email") or []]
        domains = {owner(u) for u in urls if u} | {owner("https://" + e.split("@")[-1]) for e in emails if "@" in e}
        key = c["shortName"].lower()
        if key in out:   # two CNAs with one short name: a record cannot tell them apart
            out[key]["domains"] = sorted(set(out[key]["domains"]) | {d for d in domains if d})
            out[key]["org"] += " / " + c.get("organizationName", "")
            if out[key]["class"] != klass:
                out[key]["class"] = "mixed" if "vendor" in (out[key]["class"], klass) else "other"
            continue
        out[key] = {"org": c.get("organizationName", ""), "short": c["shortName"],
                                       "types": types, "class": klass, "domains": sorted(d for d in domains if d)}
    return out


def _cpes(cna: dict) -> list[str]:
    found = []
    for a in cna.get("affected") or []:
        found += [c for c in a.get("cpes") or [] if isinstance(c, str)]
    for app in cna.get("cpeApplicability") or []:
        for node in app.get("nodes") or []:
            found += [m.get("criteria", "") for m in node.get("cpeMatch") or []]
    return [c for c in found if c.startswith("cpe:2.3:")]


KIND_OF_PART = {"a": "software", "o": "operating-system", "h": "device"}


def _records(dump: Path):
    """Every CVE record in the release zip; a zip that holds one zip is opened through a temp file."""
    with zipfile.ZipFile(dump) as z:
        names = z.namelist()
        if len(names) == 1 and names[0].endswith(".zip"):
            with tempfile.TemporaryDirectory() as tmp:
                inner = Path(tmp) / "inner.zip"
                with z.open(names[0]) as src, inner.open("wb") as dst:
                    while chunk := src.read(1 << 22):
                        dst.write(chunk)
                yield from _records(inner)
            return
        for n in names:
            if n.endswith(".json") and "/CVE-" in "/" + n:
                try:
                    yield json.loads(z.read(n))
                except ValueError:
                    continue


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:   # 29 February
        return day.replace(year=day.year - years, day=28)


def own_vendor(vendor: str, info: dict) -> bool:
    """Whether the vendor field of a record names the CNA itself: the same name once the words that
    only say it is a company are gone, or the one the start of the other."""
    v = fold_company(vendor)
    if len(v) < 2:
        return False
    for n in (fold_company(info["org"]), fold(info["short"])):
        if len(n) >= 2 and (v == n or (min(len(v), len(n)) >= 3 and (v.startswith(n) or n.startswith(v)))):
            return True
    return False


def count_cve(dump: Path, cnas_path: Path, dump_date: date, years: int = 3, tax: Tax | None = None,
              sample: int = 50) -> dict:
    tax = tax or Tax()
    cnas = cna_list(cnas_path)
    since = _years_before(dump_date, years).isoformat()
    seen = Counter()
    # per CNA: name -> {'cves', 'kind', 'source', 'source_any', 'versions', 'cpes', 'display'}
    per: dict[str, dict] = defaultdict(dict)
    records_for_check = []
    for rec in _records(dump):
        head = rec.get("cveMetadata") or {}
        if head.get("state") != "PUBLISHED":
            continue
        if (head.get("datePublished") or "")[:10] < since:
            continue
        seen["records"] += 1
        short = (head.get("assignerShortName") or "").lower()
        info = cnas.get(short)
        klass = info["class"] if info else "unlisted"
        seen[f"records {klass}"] += 1
        if klass not in ("vendor", "mixed"):
            continue
        cna = (rec.get("containers") or {}).get("cna") or {}
        cpes = _cpes(cna)
        kinds = {KIND_OF_PART.get(c.split(":")[2]) for c in cpes} - {None}
        refs = cna.get("references") or []
        own = set(info["domains"])
        on_own = [r for r in refs if owner(r.get("url", "")) in own]
        tagged = lambda tag: any(tag in (r.get("tags") or []) for r in on_own)  # noqa: E731
        products = []
        for a in cna.get("affected") or []:
            vendor = (a.get("vendor") or "").strip()
            product = (a.get("product") or "").strip()
            if fold(product) in EMPTY:
                seen[f"names n/a, {klass}"] += 1
                continue
            mine = own_vendor(vendor, info)
            seen[f"names, {klass}, vendor field {'is' if mine else 'is not'} the CNA"] += 1
            if klass == "mixed" and not mine:
                continue   # a mixed CNA also writes about others: only its own products count
            products.append((vendor, product))
            key = fold(product)
            e = per[short].setdefault(key, {"cves": set(), "kind": False, "source": False, "source_any": False,
                                            "versions": False, "cpes": set(), "display": Counter()})
            e["cves"].add(head.get("cveId"))
            e["display"][product] += 1
            e["kind"] |= bool(kinds)
            e["source"] |= tagged("product")
            e["source_any"] |= bool(on_own)
            e["versions"] |= tagged("release-notes")
            e["cpes"].update((c.split(":")[3], c.split(":")[4]) for c in cpes if len(c.split(":")) > 4)
        if products and klass == "vendor":
            records_for_check.append({"cve": head.get("cveId"), "cna": info["org"], "cna_short": info["short"],
                                      "cna_domains": info["domains"], "vendor": sorted({v for v, _ in products}),
                                      "products": sorted({p for _, p in products})[:5],
                                      "refs": [r.get("url", "") for r in refs][:3]})

    cna_rows = []
    for short, names in per.items():
        info = cnas[short]
        known = tax.publisher([info["org"], info["short"]], info["domains"])
        if info["class"] == "mixed" and not known:
            cna_rows.append({"cna": info["short"], "org": info["org"], "class": "mixed", "known": None,
                             "names": len(names), "out_of_scope": True})
            continue
        rows = []
        for key, e in names.items():
            display = e["display"].most_common(1)[0][0]
            rows.append({"name": display, "cves": len(e["cves"]), "kind": e["kind"], "source": e["source"],
                         "source_any": e["source_any"], "versions": e["versions"], "shape": shape(display),
                         "in_tax": tax.has_product(known, display, e["cpes"])})
        cna_rows.append({"cna": info["short"], "org": info["org"], "class": info["class"], "known": known,
                         "names": len(rows), "group": next(g for lo, hi, g in GROUPS if lo <= len(rows) <= hi),
                         "products": sorted(rows, key=lambda r: -r["cves"])})
    rng = random.Random(25)
    check = rng.sample(records_for_check, min(sample, len(records_for_check)))
    return {"source": "cve", "dump": str(dump.name), "dump_date": dump_date.isoformat(), "since": since,
            "cna_list": str(cnas_path.name), "cnas": Counter(c["class"] for c in cnas.values()),
            "seen": seen, "rows": sorted(cna_rows, key=lambda r: -r["names"]), "check": check}


# --------------------------------------------------------------------------- winget

TOP_KEY = re.compile(r"^([A-Za-z]+):[ \t]*(.*?)[ \t]*$")


def _top_level(text: str) -> dict[str, str]:
    """The top-level scalar keys of a manifest; fast enough for every package in one pass."""
    out = {}
    for line in text.splitlines():
        m = TOP_KEY.match(line)
        if m and m.group(2) and not m.group(2).startswith(("|", ">")):
            out.setdefault(m.group(1), m.group(2).strip("'\""))
    return out


def _version_key(v: str):
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.\-_+ ]", v.lower()) if p]


# A package identifier that ends in a number is a line of versions of another package.
VERSION_IN_ID = re.compile(r"\.v?\d+$", re.I)


def count_winget(dump: Path, tax: Tax | None = None) -> dict:
    """One stream over the tar.gz. A file manifests/<l>/<id path>/<version>/<file>.yaml; per package only
    the files of its highest version are kept."""
    tax = tax or Tax()
    newest: dict[str, tuple[list, str, dict[str, str]]] = {}
    with tarfile.open(dump, "r|gz") as tar:
        for m in tar:
            if not m.isfile() or not m.name.endswith(".yaml"):
                continue
            parts = m.name.split("/")
            if len(parts) < 6 or parts[1] != "manifests":
                continue
            pkg, version, fname = "/".join(parts[3:-2]), parts[-2], parts[-1]
            if ".installer." in fname:
                continue
            vk = _version_key(version)
            cur = newest.get(pkg)
            if cur is None or vk > cur[0]:
                cur = newest[pkg] = (vk, version, {})
            elif version != cur[1]:
                continue
            cur[2][fname] = tar.extractfile(m).read().decode("utf-8", "replace")
    pubs: dict[str, dict] = {}
    rows = []
    for pkg, (_, version, files) in newest.items():
        merged: dict[str, str] = {}
        default = None
        for fname, text in files.items():
            if ".locale." not in fname:
                keys = _top_level(text)
                default = keys.get("DefaultLocale", default)
                for k, v in keys.items():
                    merged.setdefault(k, v)
        for fname, text in sorted(files.items(), key=lambda kv: default is None or f".locale.{default}." not in kv[0]):
            if ".locale." in fname:
                for k, v in _top_level(text).items():
                    merged.setdefault(k, v)
        ident = merged.get("PackageIdentifier") or pkg.replace("/", ".")
        publisher = merged.get("Publisher", "")
        name = merged.get("PackageName", "")
        pub_url, pkg_url, notes = merged.get("PublisherUrl", ""), merged.get("PackageUrl", ""), merged.get("ReleaseNotesUrl", "")
        pub_dom = owner(pub_url) if pub_url else None
        known = tax.publisher([publisher, ident.split(".")[0]], [pub_dom] if pub_dom else [])
        source = bool(pkg_url) and owner(pkg_url) is not None and (owner(pkg_url) == pub_dom or not pub_dom)
        versions = bool(notes) and owner(notes) is not None and version.lower() not in notes.lower() \
            and (owner(notes) == pub_dom or owner(notes) == owner(pkg_url or ""))
        rows.append({"id": ident, "publisher": publisher, "name": name, "known": known,
                     "in_tax": ident.lower() in tax.winget or tax.has_product(known, name),
                     "kind": True, "source": source, "source_any": bool(pkg_url), "versions": versions,
                     "versions_any": bool(notes),
                     "shape": shape(name) or ("version" if VERSION_IN_ID.search(ident) else "")})
        pubs.setdefault(fold_company(publisher) or ident.split(".")[0].lower(), {"known": known, "n": 0})["n"] += 1
    return {"source": "winget", "dump": str(dump.name), "packages": len(rows), "rows": rows,
            "publishers": len(pubs), "publishers_known": sum(1 for p in pubs.values() if p["known"])}


# --------------------------------------------------------------------------- cost


def prices(root: Path = ROOT) -> dict:
    return yaml.safe_load((root / "referee" / "prices.yaml").read_text())


def cost(publishers: int, names: int, candidates: int, table: dict) -> dict[str, float]:
    """Dollars per model for a round: grouping per publisher, the referee per candidate, as a batch."""
    tokens = {"input": GROUP_BASE["input"] * publishers + GROUP_PER_NAME["input"] * names + CANDIDATE_TOKENS["input"] * candidates,
              "cache_read": CANDIDATE_TOKENS["cache_read"] * candidates,
              "output": GROUP_BASE["output"] * publishers + GROUP_PER_NAME["output"] * names + CANDIDATE_TOKENS["output"] * candidates}
    out = {}
    for model, p in table["models"].items():
        dollars = sum(tokens[k] * p[k] for k in tokens) / 1e6
        out[model] = round(dollars * table["batch_discount"], 2)
    return out


# --------------------------------------------------------------------------- report


def _fill(products) -> dict:
    p = list(products)
    return {"names": len(p), "in_tax": sum(r["in_tax"] for r in p), "kind": sum(r["kind"] for r in p),
            "source": sum(r["source"] for r in p), "source_any": sum(r["source_any"] for r in p),
            "versions": sum(r["versions"] for r in p),
            "all_three": sum(r["kind"] and r["source"] and r["versions"] for r in p),
            "version": sum(r["shape"] == "version" for r in p), "range": sum(r["shape"] == "range" for r in p)}


def summarise_cve(data: dict, table: dict) -> dict:
    rows = [r for r in data["rows"] if not r.get("out_of_scope")]
    scopes = {
        "vendor, small (≤20 names)": [r for r in rows if r["class"] == "vendor" and r["group"] == "small"],
        "vendor, medium (21–200)": [r for r in rows if r["class"] == "vendor" and r["group"] == "medium"],
        "vendor, large (>200)": [r for r in rows if r["class"] == "vendor" and r["group"] == "large"],
        "mixed, publisher in TAX": [r for r in rows if r["class"] == "mixed"],
    }
    rounds = {
        "vendor small, all names": (scopes["vendor, small (≤20 names)"], 1),
        "vendor medium, names with ≥3 CVEs": (scopes["vendor, medium (21–200)"], 3),
        "vendor large, names with ≥10 CVEs": (scopes["vendor, large (>200)"], 10),
        "mixed known, names with ≥3 CVEs": (scopes["mixed, publisher in TAX"], 3),
    }
    out = {"scopes": {}, "rounds": {}}
    for label, cnas in scopes.items():
        prods = [p for r in cnas for p in r["products"]]
        out["scopes"][label] = {"cnas": len(cnas), "known": sum(1 for r in cnas if r["known"]), **_fill(prods),
                                "cves_ge3": sum(p["cves"] >= 3 for p in prods), "cves_ge10": sum(p["cves"] >= 10 for p in prods)}
    for label, (cnas, floor) in rounds.items():
        prods = [p for r in cnas for p in r["products"] if p["cves"] >= floor]
        pubs = sum(1 for r in cnas if any(p["cves"] >= floor for p in r["products"]))
        cands = [p for p in prods if not p["in_tax"]]
        out["rounds"][label] = {"publishers": pubs, **_fill(prods), "candidates": len(cands),
                                "candidates_product_shape": sum(1 for p in cands if not p["shape"]),
                                "cost": cost(pubs, len(prods), len(cands), table)}
    out["mixed_unknown"] = sum(1 for r in data["rows"] if r.get("out_of_scope"))
    out["mixed_unknown_names"] = sum(r["names"] for r in data["rows"] if r.get("out_of_scope"))
    return out


def summarise_winget(data: dict, table: dict) -> dict:
    rows = data["rows"]
    pairs = {(fold_company(r["publisher"]), fold(r["name"])) for r in rows}
    cands = [r for r in rows if not r["in_tax"]]
    fill = _fill(rows)
    fill["versions_any"] = sum(r["versions_any"] for r in rows)
    return {"packages": len(rows), "unique_pairs": len(pairs), "publishers": data["publishers"],
            "publishers_known": data["publishers_known"], **fill, "candidates": len(cands),
            "candidates_known_publisher": sum(1 for r in cands if r["known"]),
            "cost_all": cost(data["publishers"], len(rows), len(cands), table),
            "cost_known_publishers": cost(data["publishers_known"], sum(1 for r in rows if r["known"]),
                                          sum(1 for r in cands if r["known"]), table)}


def _money(c: dict) -> str:
    return " · ".join(f"{m.replace('claude-', '')} ${v:,.2f}" for m, v in c.items())


def report(paths: list[Path], root: Path = ROOT) -> str:
    table = prices(root)
    lines = ["# TAX-25: the count", ""]
    for path in paths:
        data = json.loads(Path(path).read_text())
        if data["source"] == "cve":
            s = summarise_cve(data, table)
            lines += [f"## CVE (dump {data['dump_date']}, published since {data['since']}; CNA list {data['cna_list']})", "",
                      "Records: " + ", ".join(f"{k} {v:,}" for k, v in sorted(data["seen"].items())), "",
                      "| scope | CNAs | publisher in TAX | names | in TAX | kind | source (product) | source (own domain) | versions.source | all three | version | range | ≥3 CVEs | ≥10 CVEs |",
                      "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
            for label, v in s["scopes"].items():
                lines.append(f"| {label} | {v['cnas']} | {v['known']} | {v['names']:,} | {v['in_tax']} | {v['kind']:,} | {v['source']} | "
                             f"{v['source_any']:,} | {v['versions']} | {v['all_three']} | {v['version']:,} | {v['range']:,} | {v['cves_ge3']:,} | {v['cves_ge10']:,} |")
            lines += ["", f"Mixed CNAs whose publisher TAX does not know (out of scope): {s['mixed_unknown']}, with {s['mixed_unknown_names']:,} names.", "",
                      "| round | publishers | names | in TAX | candidates | of which no version or range | cost as a batch |",
                      "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
            for label, v in s["rounds"].items():
                lines.append(f"| {label} | {v['publishers']} | {v['names']:,} | {v['in_tax']} | {v['candidates']:,} | {v['candidates_product_shape']:,} | {_money(v['cost'])} |")
            lines.append("")
        else:
            s = summarise_winget(data, table)
            lines += [f"## winget ({data['dump']})", "",
                      "| measure | value |", "| --- | ---: |"]
            for k in ("packages", "unique_pairs", "publishers", "publishers_known", "in_tax", "kind", "source", "source_any",
                      "versions", "versions_any", "all_three", "version", "range", "candidates", "candidates_known_publisher"):
                lines.append(f"| {k} | {s[k]:,} |")
            lines += ["", f"Cost, all candidates: {_money(s['cost_all'])}", "",
                      f"Cost, candidates of publishers in TAX: {_money(s['cost_known_publishers'])}", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cve")
    c.add_argument("--dump", type=Path, required=True)
    c.add_argument("--cnas", type=Path, required=True)
    c.add_argument("--date", help="date of the dump (YYYY-MM-DD); default today")
    c.add_argument("--years", type=int, default=3)
    c.add_argument("--out", type=Path, required=True)
    w = sub.add_parser("winget")
    w.add_argument("--dump", type=Path, required=True)
    w.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("report")
    r.add_argument("files", type=Path, nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "cve":
        day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
        data = count_cve(args.dump, args.cnas, day, args.years)
    elif args.cmd == "winget":
        data = count_winget(args.dump)
    else:
        print(report(args.files))
        return 0
    args.out.write_text(json.dumps(data, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else o))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
