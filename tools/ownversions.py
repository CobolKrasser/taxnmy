#!/usr/bin/env python3
"""Sources of the publisher for the name and the versions of a product (TAX-21; TP1, TP5, TE1, TE3, TE8).

A product whose `source` or `versions.source` is a page of a third party (a dataset, or a site with
many owners) gets the release policy page that the dataset names for it, when that page is the
publisher's own. Only that link counts as a source of the versions; a pattern per release is no page.

    versions.source  the release policy link, when it is on the domain of the publisher's `source`
                     (its owner, as tools/guard.py gives it) and the reader read it, also after its
                     redirects, on that domain
    source           the same page, when also the product's name stands in its text (TK2 and the
                     literal part of TE1; the footer of a page proves no product name)

A link to another domain is not taken. What is not taken keeps its source; the reason is in the
report. The rules name no brand or product (TG4).

    python tools/ownversions.py --eol eol.json --out ownversions.json [--write]

Costs nothing: no AI. Pages are read with the ladder of tools/fetch.py (robots.txt and refusals are
respected, TE5).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
from fetch import moved  # noqa: E402
from guard import owner  # noqa: E402
from ownsource import CLOSED, load_data, load_eol, third_party  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Result:
    product: str
    link: str = ""
    versions: str = ""  # the new versions.source, or ''
    source: str = ""  # the new source, or ''
    outcome: str = ""  # both, versions, no-link, other-domain, closed, unreadable, publisher-not-own, kept
    detail: str = ""


def policy_link(product: dict, eol: dict) -> str:
    for url in (product.get("source", ""), (product.get("versions") or {}).get("source", "")):
        entry = eol.get((url or "").rstrip("/"))
        if entry:
            return ((entry.get("links") or {}).get("releasePolicy")) or ""
    return ""


def judge(product: dict, publisher: dict, link: str, fetcher) -> Result:
    res = Result(product.get("key", ""), link)
    pub_source = publisher.get("source", "")
    if third_party(pub_source):
        res.outcome, res.detail = "publisher-not-own", f"the publisher's source is {pub_source}"
        return res
    if not link:
        res.outcome = "no-link"
        return res
    link = "https://" + link[len("http://"):] if link.startswith("http://") else link  # TE1: https only
    own = owner(pub_source)
    if third_party(link) or owner(link) != own:
        res.outcome, res.detail = "other-domain", f"{link} is not on {own}"
        return res
    src = fetcher.fetch(link, "")
    if end := moved(src):
        if owner(end) != own:
            res.outcome, res.detail = "other-domain", f"{link} redirects to {end}"
            return res
        link, src = end, fetcher.fetch(end, "")
        if moved(src):
            res.outcome, res.detail = "unreadable", f"{link} redirects again"
            return res
    if src.status in CLOSED:
        res.outcome, res.detail = "closed", f"{link}: {src.status}"
        return res
    if not gate.read(src):
        res.outcome, res.detail = "unreadable", f"{link}: {src.status}"
        return res
    res.versions, res.outcome = link, "versions"
    if gate.appears(product.get("name", ""), src.text):
        res.source, res.outcome = link, "both"
    else:
        res.detail = f"{product.get('name')!r} not in the text"
    return res


def set_sources(path: Path, source: str, versions: str) -> None:
    """Replace the top-level `source` and `versions.source`, and nothing else in the file."""
    text = path.read_text(encoding="utf-8")
    if source:
        text, n = re.subn(r"(?m)^source: .*$", lambda m: "source: " + source, text, count=1)
        assert n == 1, path
    if versions:
        text, n = re.subn(r"(?m)^(versions:\n  source: ).*$", lambda m: m.group(1) + versions, text, count=1)
        assert n == 1, path
    path.write_text(text, encoding="utf-8")


def run(args, fetcher=None, root: Path = ROOT, log=print) -> list[Result]:
    pubs, prods = load_data(root)
    eol = load_eol(args.eol)
    if fetcher is None:
        from fetch import Fetcher
        fetcher = Fetcher(log=log if args.verbose else None)
    out = []
    todo = [(k, p) for k, ps in prods.items() for p in ps
            if third_party(p.get("source", "")) or third_party((p.get("versions") or {}).get("source", ""))]
    if args.only:
        todo = [(k, p) for k, p in todo if p.get("key") in args.only or k in args.only]
    for n, (slug, p) in enumerate(todo, 1):
        res = judge(p, pubs.get(slug, {}), policy_link(p, eol), fetcher)
        log(f"[{n}/{len(todo)}] {res.product}: {res.outcome} {res.versions} {res.detail}")
        out.append(res)
        if args.write and res.versions:
            name = p["key"].split("/", 1)[1]
            # Only what is still a third party's is replaced.
            set_sources(root / "products" / slug / f"{name}.yaml",
                        res.source if third_party(p.get("source", "")) else "",
                        res.versions if third_party(p["versions"].get("source", "")) else "")
    return out


def count(root: Path = ROOT) -> dict:
    _, prods = load_data(root)
    ps = [p for v in prods.values() for p in v]
    return {"products": len(ps), "source": sum(third_party(p.get("source", "")) for p in ps),
            "versions.source": sum(third_party((p.get("versions") or {}).get("source", "")) for p in ps)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--eol", type=Path, required=True, help="the full product list of the dataset the sources point to (JSON)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only", nargs="*", help="only these product or publisher keys")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    before = count()
    results = run(args)
    if args.out:
        args.out.write_text(json.dumps([asdict(r) for r in results], indent=1))
    print(f"before (third party): {before}")
    print("outcomes: " + ", ".join(f"{k} {v}" for k, v in Counter(r.outcome for r in results).most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
