#!/usr/bin/env python3
"""A source of the publisher itself for every publisher (TAX-20; TP5, TE3, TE5, TE8).

A publisher whose `source` is not its own (a dataset of third parties, or a site with many owners)
gets a page of its own, found by code. The finders are the data that TAX and the datasets it was
filled from already hold. Each gives candidates only (TE8); a candidate becomes the source after the
reader read it and the publisher's name stands on it.

    release-policy  the release policy link of the product in the dataset its `source` points to
    winget          `PublisherUrl` of the newest manifest of a winget identifier
    homebrew        `homepage` of a Homebrew formula or cask
    scoop, npm, composer
                    `homepage` of the package
    github          `homepage` of a GitHub repository, then `blog` of its owner
    cna             the advisory and contact pages of the publisher in the CNA list of the CVE
                    programme, matched by exact name

When a candidate counts as on the publisher's own domain (its owner, as tools/guard.py gives it):

    known     a product of the publisher already has its `source` or `versions.source` there
    cna       the CNA of the publisher has its e-mail on that domain
    finders   two finders or more point to it
    name      the publisher's key, name or an alias stands in the name of the domain

A candidate on any other domain is not taken: a link of one finder to someone else's site. The
classes of sites are data (`referee/sites.yaml`); the rules name no brand or product (TG4).

The reading is the ladder of tools/fetch.py: robots.txt and a refusal are respected (TE5); a closed
page is skipped for the next candidate of the same publisher. The name check is that of the
importer for a new publisher: the name, or the name without its legal form, in the text or (TAX-43)
in the footer of the page.

    python tools/ownsource.py --eol eol.json --cnas CNAsList.json \\
        --brew formula.json --cask cask.json --cache finders.json --out ownsource.json [--write]

Costs nothing: no AI. The downloads are public; per package one request to its registry.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import keyrule  # noqa: E402
import sites  # noqa: E402
from census import fold, fold_company  # noqa: E402
from fetch import USER_AGENT, moved, publisher_texts  # noqa: E402
from guard import host_of, owner, registrable  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATASETS = sites.names("datasets")
CLOSED = {"refused", "disallowed", "429"}

FINDERS = ("cna", "release-policy", "winget", "homebrew", "scoop", "npm", "composer", "github")
REASONS = ("known", "cna", "finders", "name")
MIN_PREFIX = 5  # a name of fewer characters counts only as the whole name of the domain


@dataclass
class Candidate:
    finder: str
    url: str
    via: str = ""  # the product or the CNA it came from
    vouch: tuple = ()  # the domains the finder vouches for (the e-mail domains of a CNA)


@dataclass
class Result:
    publisher: str
    name: str
    before: str
    source: str = ""
    finder: str = ""
    reason: str = ""
    outcome: str = ""  # found, none, closed, not-named, untrusted
    detail: str = ""
    rejected: list = field(default_factory=list)  # [url, why] of the candidates not taken


# --------------------------------------------------------------------------- whose a URL is


def third_party(url: str) -> bool:
    """A URL that is not the publisher's own: no owner can be read off it, or a dataset of others."""
    own = owner(url or "")
    return own is None or own in DATASETS


def label_of(own: str) -> str:
    """The name part of an owner: `acme` of code.example/acme (a site with the owner in the path),
    of acme.pages.example (the owner in the host) and of acme.co.jp."""
    if "/" in own:
        return own.split("/", 1)[1]
    return own.split(".")[0]


def folded_names(publisher: dict) -> list[str]:
    names = [publisher.get("key", ""), publisher.get("name", ""), *(publisher.get("aliases") or [])]
    out = []
    for n in names:
        for f in (fold(n), fold_company(n)):
            if len(f) >= 3 and f not in out:
                out.append(f)
    return out


def name_in(own: str, publisher: dict) -> bool:
    """The name of the domain is the publisher's key, name or an alias, or begins with it
    (`acmeserver` for Acme), or the publisher's name begins with it (`grafana` for Grafana Labs).
    A beginning counts from 5 characters; a shorter name must be the whole label."""
    lab = fold(label_of(own))
    return any(n == lab or (len(n) >= MIN_PREFIX and lab.startswith(n)) or
               (len(lab) >= MIN_PREFIX and n.startswith(lab)) for n in folded_names(publisher))


def known_owners(publisher: dict, products: list[dict]) -> set[str]:
    """The owners of the sources TAX already took for the publisher's products (TP1, TP5)."""
    urls = [u for p in products for u in (p.get("source"), (p.get("versions") or {}).get("source"))]
    urls += [m.get("page", "") for m in publisher.get("machine_sources") or [] if isinstance(m, dict)]
    return {owner(u) for u in urls if u and not third_party(u)}


def trusted(publisher: dict, candidates: list[Candidate], known: set[str], others: set[str] = frozenset()) -> dict[str, str]:
    """owner -> the reason it counts as the publisher's own domain (see the docstring). A domain that
    TAX gives to another publisher (`others`) does not count by its name alone (TV2)."""
    by_owner: dict[str, set] = {}
    for c in candidates:
        if not third_party(c.url):
            by_owner.setdefault(owner(c.url), set()).add(c.finder)
    out = {}
    for own, finders in by_owner.items():
        if own in known:
            out[own] = "known"
        elif any(c.finder == "cna" and owner(c.url) == own and registrable(host_of(c.url)) in c.vouch
                 for c in candidates):
            out[own] = "cna"
        elif len(finders) >= 2:
            out[own] = "finders"
        elif name_in(own, publisher) and own not in others:
            out[own] = "name"
    return out


def ordered(candidates: list[Candidate], reasons: dict[str, str]) -> list[Candidate]:
    """The candidates on the publisher's own domains: a domain of its own before a path on a site
    with many owners, then by the strength of the reason and the order of the finders."""
    kept = [c for c in candidates if not third_party(c.url) and owner(c.url) in reasons]
    return sorted(kept, key=lambda c: ("/" in owner(c.url), REASONS.index(reasons[owner(c.url)]),
                                       FINDERS.index(c.finder)))


# --------------------------------------------------------------------------- reading


def home_pages(candidates: list[Candidate], reasons: dict[str, str]) -> list[Candidate]:
    """After the pages the finders gave: the home page of each own domain (not of a path on a site
    with many owners), where a publisher writes its name. Counted under the finder that gave the domain."""
    out = []
    for c in ordered(candidates, reasons):
        own = owner(c.url)
        if "/" not in own and own not in [owner(h.url) for h in out]:
            host = re.match(r"^https?://([^/:?#]+)", c.url).group(1)
            out.append(Candidate(c.finder, f"https://{host if host.startswith('www.') else own}/", c.via, c.vouch))
    return out


def names_of(name: str) -> list[str]:
    out = [name]
    stripped = keyrule.strip_legal_form(name).strip(" ,.")
    if stripped and stripped != name:
        out.append(stripped)
    return out


def named(src, name: str) -> bool:
    return gate.read(src) and any(gate.appears(n, t) for n in names_of(name) for t in publisher_texts(src))


def choose(publisher: dict, candidates: list[Candidate], known: set[str], fetcher,
           others: set[str] = frozenset()) -> Result:
    res = Result(publisher["key"], publisher.get("name", ""), publisher.get("source", ""))
    reasons = trusted(publisher, candidates, known, others)
    for c in candidates:
        if third_party(c.url):
            res.rejected.append([c.url, f"{c.finder}: not an own site"])
        elif owner(c.url) not in reasons:
            res.rejected.append([c.url, f"{c.finder}: {owner(c.url)} is not shown to be the publisher's"])
    if not candidates:
        res.outcome, res.detail = "none", "no finder gave a page"
        return res
    tried = set()
    closed = unnamed = ""
    for c in ordered(candidates, reasons) + home_pages(candidates, reasons):
        url = "https://" + c.url[len("http://"):] if c.url.startswith("http://") else c.url  # TE1: https only
        if url in tried:
            continue
        tried.add(url)
        src = fetcher.fetch(url, "")
        if end := moved(src):
            if owner(end) != owner(url):
                res.rejected.append([url, f"redirects to another owner: {end}"])
                continue
            url, src = end, fetcher.fetch(end, "")
            if moved(src):
                continue
        if src.status in CLOSED:
            closed = closed or f"{url}: {src.status}"
            continue
        if named(src, res.name):
            res.source, res.finder, res.reason, res.outcome = url, c.finder, reasons[owner(c.url)], "found"
            return res
        unnamed = unnamed or f"{url}: {src.status}, {res.name!r} not on the page"
    if not reasons:
        res.outcome, res.detail = "untrusted", "no candidate on a domain shown to be the publisher's"
    elif closed and not unnamed:
        res.outcome, res.detail = "closed", closed
    else:
        res.outcome, res.detail = "not-named", "; ".join(d for d in (unnamed, closed) if d)
    return res


# --------------------------------------------------------------------------- finders


def purl_parts(purl: str) -> tuple[str, str]:
    """('npm', '@scope/name') of pkg:npm/%40scope/name@1.0?x=y."""
    m = re.match(r"^pkg:([a-z0-9.+-]+)/([^?#]+)", purl or "", re.I)
    if not m:
        return "", ""
    rest = urllib.parse.unquote(m.group(2))
    scope = "@" if rest.startswith("@") else ""
    rest = scope + rest[len(scope):].split("@")[0]
    return m.group(1).lower(), rest.strip("/")


def version_key(v: str) -> tuple:
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.\-+_]", v))


class Lookup:
    """One request per package, kept in a cache file so that a second run asks nothing again."""

    def __init__(self, cache: Path | None = None, get=None, gh=None, sleep=time.sleep):
        self.path = cache
        self.cache = json.loads(cache.read_text()) if cache and cache.exists() else {}
        self._get = get or self._http
        self._gh = gh if gh is not None else (self._gh_cli if shutil.which("gh") else None)
        self._sleep = sleep

    def save(self) -> None:
        if self.path:
            self.path.write_text(json.dumps(self.cache, indent=1, sort_keys=True))

    def _http(self, url: str):
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - a package that is not there gives nothing
            return None
        finally:
            self._sleep(0.5)

    def _gh_cli(self, path: str):
        out = subprocess.run(["gh", "api", path], capture_output=True, text=True)
        return out.stdout if out.returncode == 0 else None

    def text(self, key: str, url: str) -> str | None:
        if key not in self.cache:
            self.cache[key] = self._get(url)
        return self.cache[key]

    def json(self, kind: str, ident: str):
        """The JSON of `ident` at the first address of the finder `kind` that has it."""
        for tpl in sites.value("finders")[kind]:
            if kind.startswith("github") or kind == "winget_dir":
                key = f"{kind}:{ident}"
                if key not in self.cache:
                    path = tpl.format(id=ident)
                    self.cache[key] = (self._gh(path) if self._gh else
                                       self._get(sites.value("finders")["github_api"][0] + path))
                body = self.cache[key]
            else:
                body = self.text(f"{kind}:{ident}:{tpl}", tpl.format(id=ident))
            if body:
                try:
                    return json.loads(body)
                except ValueError:
                    try:
                        return yaml.safe_load(body)
                    except yaml.YAMLError:
                        return None
        return None


def homepage(data) -> str:
    return data.get("homepage") or "" if isinstance(data, dict) else ""


def winget_publisher_url(lookup: Lookup, ident: str) -> str:
    parts = ident.split(".")
    if len(parts) < 2:
        return ""
    path = f"{parts[0][0].lower()}/{'/'.join(parts)}"
    listing = lookup.json("winget_dir", path)
    if not isinstance(listing, list):
        return ""
    versions = [e["name"] for e in listing if isinstance(e, dict) and e.get("type") == "dir"]
    if not versions:
        return ""
    newest = max(versions, key=version_key)
    for name in (f"{ident}.locale.en-US.yaml", f"{ident}.yaml"):
        data = lookup.json("winget_file", f"{path}/{newest}/{name}")
        if isinstance(data, dict) and data.get("PublisherUrl"):
            return str(data["PublisherUrl"])
    return ""


def codes_of(product: dict, eol_entry: dict | None) -> list[tuple[str, str]]:
    """(kind, id) of every code of a product: TAX's own and those of the dataset entry."""
    out = []
    for i in product.get("identifiers") or []:
        for k in ("purl", "winget", "homebrew"):
            if i.get(k):
                out.append((k, i[k]))
    for i in (eol_entry or {}).get("identifiers") or []:
        if i.get("type") == "purl":
            out.append(("purl", i["id"]))
    seen, uniq = set(), []
    for k, v in out:
        if (k, v.lower()) not in seen:
            seen.add((k, v.lower()))
            uniq.append((k, v))
    return uniq


def product_candidates(product: dict, eol: dict, brew: dict, lookup: Lookup) -> list[Candidate]:
    via = product.get("key", "")
    entry = eol.get(product.get("source", "").rstrip("/")) or eol.get((product.get("versions") or {}).get("source", "").rstrip("/"))
    out = []
    policy = ((entry or {}).get("links") or {}).get("releasePolicy")
    if policy:
        out.append(Candidate("release-policy", policy, via))
    for kind, value in codes_of(product, entry):
        url = ""
        if kind == "winget":
            url, finder = winget_publisher_url(lookup, value), "winget"
        elif kind == "homebrew":
            url, finder = brew.get(value.lower(), ""), "homebrew"
        else:
            ptype, rest = purl_parts(value)
            finder = {"brew": "homebrew", "github": "github"}.get(ptype, ptype)
            if ptype == "winget":
                url, finder = winget_publisher_url(lookup, rest), "winget"
            elif ptype == "brew":
                url = brew.get(rest.split("/")[-1].lower(), "")
            elif ptype in ("scoop", "npm", "composer"):
                url = homepage(lookup.json(ptype, rest.split("/")[-1] if ptype == "scoop" else rest))
                if ptype == "composer" and not url:
                    data = lookup.json(ptype, rest) or {}
                    versions = (data.get("packages") or {}).get(rest) or [{}]
                    url = homepage(versions[0])
            elif ptype == "github" and "/" in rest:
                org, repo = rest.split("/")[:2]
                url = homepage(lookup.json("github_repo", f"{org}/{repo}"))
                if not url:
                    url = (lookup.json("github_owner", org) or {}).get("blog") or ""
            else:
                continue
        if url and url.startswith(("http://", "https://")):
            out.append(Candidate(finder, url, via))
        elif url and "." in url and " " not in url:
            out.append(Candidate(finder, "https://" + url, via))
    return out


def cna_candidates(publisher: dict, cnas: list[dict]) -> list[Candidate]:
    """The pages of the publisher's CNA: matched by exact name, never by likeness (TV2). The name of
    the CNA gives the key of the writing rule (only a legal form at the end dropped), and that key
    is the publisher's key or the key of its name or an alias."""
    keys = {publisher.get("key", "")}
    for n in [publisher.get("name", ""), *(publisher.get("aliases") or [])]:
        try:
            keys.add(keyrule.publisher_part(n))
        except keyrule.KeyRuleError:
            pass
    out = []
    for c in cnas:
        try:
            if keyrule.publisher_part(c.get("organizationName", "")) not in keys:
                continue
        except keyrule.KeyRuleError:
            continue
        mails = {registrable(e.get("emailAddr", "").rsplit("@", 1)[-1])
                 for ct in c.get("contact") or [] for e in ct.get("email") or [] if "@" in e.get("emailAddr", "")}
        urls = [a.get("url") for a in (c.get("securityAdvisories") or {}).get("advisories") or []]
        urls += [a.get("url") for a in (c.get("securityAdvisories") or {}).get("alerts") or []]
        urls += [u.get("url") for ct in c.get("contact") or [] for u in ct.get("contact") or []]
        for u in urls:
            if u and u.startswith(("http://", "https://")):
                out.append(Candidate("cna", u, c.get("shortName", ""), tuple(sorted(mails))))
    return out


# --------------------------------------------------------------------------- the data


def load_data(root: Path = ROOT):
    pubs = {}
    for f in sorted((root / "publishers").glob("*.yaml")):
        # The file name is the key (the writing rule); the lint checks that they agree.
        pubs[f.stem] = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    prods: dict[str, list] = {}
    for f in sorted((root / "products").glob("*/*.yaml")):
        prods.setdefault(f.parent.name, []).append(yaml.safe_load(f.read_text(encoding="utf-8")) or {})
    return pubs, prods


def load_eol(path: Path | None) -> dict:
    if not path:
        return {}
    data = json.loads(path.read_text())
    return {(e.get("links") or {}).get("html", "").rstrip("/"): e for e in data.get("result", data)}


def load_brew(*paths: Path | None) -> dict:
    out = {}
    for path in paths:
        if path:
            for e in json.loads(path.read_text()):
                # The field that names a formula or cask is data (`homebrew_name` in referee/sites.yaml).
                package = next((e.get(f) for f in sites.value("finders")["homebrew_name"] if e.get(f)), "")
                if package and e.get("homepage"):
                    out.setdefault(package.lower(), e["homepage"])
    return out


def set_source(path: Path, url: str) -> None:
    """Replace the publisher's `source` and nothing else in the file."""
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r"(?m)^source: .*$", "source: " + url.replace("\\", "\\\\"), text, count=1)
    if n != 1:
        raise SystemExit(f"{path}: no top-level source")
    path.write_text(new, encoding="utf-8")


def run(args, fetcher=None, root: Path = ROOT, log=print) -> list[Result]:
    pubs, prods = load_data(root)
    eol = load_eol(args.eol)
    brew = load_brew(args.brew, args.cask)
    cnas = json.loads(args.cnas.read_text()) if args.cnas else []
    lookup = Lookup(args.cache)
    if fetcher is None:
        from fetch import Fetcher
        fetcher = Fetcher(log=log if args.verbose else None)
    results = []
    todo = [k for k, p in pubs.items() if third_party(p.get("source", ""))]
    if args.only:
        todo = [k for k in todo if k in args.only]
    for n, slug in enumerate(todo, 1):
        pub, products = pubs[slug], prods.get(slug, [])
        cands = cna_candidates(pub, cnas)
        for p in products:
            cands += product_candidates(p, eol, brew, lookup)
        lookup.save()
        others = {owner(p.get("source", "")) for k, p in pubs.items() if k != slug and not third_party(p.get("source", ""))}
        res = choose(pub, cands, known_owners(pub, products), fetcher, others)
        res_found = f"{res.finder}/{res.reason} {res.source}" if res.source else f"{res.outcome}: {res.detail}"
        log(f"[{n}/{len(todo)}] {slug}: {res_found}")
        results.append(res)
        if args.write and res.source:
            set_source(root / "publishers" / f"{slug}.yaml", res.source)
    lookup.save()
    return results


def tally(results: list[Result], total: int, before: int) -> str:
    found = [r for r in results if r.source]
    lines = [f"publishers: {total}; source of a third party before: {before}; found by code: {len(found)}; "
             f"left: {before - len(found)}",
             "by finder: " + ", ".join(f"{k} {v}" for k, v in Counter(r.finder for r in found).most_common()),
             "by reason: " + ", ".join(f"{k} {v}" for k, v in Counter(r.reason for r in found).most_common()),
             "not found: " + ", ".join(f"{k} {v}" for k, v in Counter(r.outcome for r in results if not r.source).most_common()),
             f"candidates not taken (another owner, or not shown to be the publisher's): "
             f"{sum(1 for r in results for _, why in r.rejected if 'not shown' in why or 'another owner' in why)}"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--eol", type=Path, help="the full product list of the dataset the sources point to (JSON)")
    ap.add_argument("--cnas", type=Path, help="CNAsList.json of the CVE programme")
    ap.add_argument("--brew", type=Path, help="Homebrew formula.json")
    ap.add_argument("--cask", type=Path, help="Homebrew cask.json")
    ap.add_argument("--cache", type=Path, help="a JSON file for the lookups per package")
    ap.add_argument("--out", type=Path, help="the results as JSON")
    ap.add_argument("--only", nargs="*", help="only these publisher keys")
    ap.add_argument("--write", action="store_true", help="write the sources found into publishers/")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    pubs, _ = load_data()
    before = sum(1 for p in pubs.values() if third_party(p.get("source", "")))
    results = run(args)
    if args.out:
        args.out.write_text(json.dumps([asdict(r) for r in results], indent=1))
    print(tally(results, len(pubs), before))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
