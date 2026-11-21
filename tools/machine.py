"""Step 0 of the ladder: sources made for machines.

One reader per format, never per brand. Each reader recognises its
format from the content, takes out the part about one product, and writes it as plain lines of
text, so that the referee can quote from it.

| format | example | what is taken out |
| --- | --- | --- |
| update catalog (Manifest of SoftwareComponent) | a catalog of driver and firmware updates | the components whose supported system is the product: name, version, date, type |
| AppStream metadata (components) | firmware metadata as a firmware service publishes it | the components whose name is the product: name, developer, releases |
| CSAF 2.0 document | a security advisory, often under /.well-known/csaf/ | the publisher, the products in the tree that match, and the vulnerabilities with their status |
| CSAF provider metadata | /.well-known/csaf/provider-metadata.json | the publisher and where its advisories are |
| CPE dictionary (JSON) | an API of the CPE dictionary, its format named in referee/sites.yaml | the CPE names and titles |

A reader returns None when the content is not its format. Matching a product is by name: all
words of the product name must occur in the entry, ignoring case. That is generic and cheap; it
may take in an entry too many, never invents one.
"""

from __future__ import annotations

import gzip
import json
import re
import xml.etree.ElementTree as ET

import sites

MAX_UNPACKED = 300_000_000
MAX_LINES = 400


def words(name: str) -> list[str]:
    return [w for w in re.split(r"[^0-9a-z]+", name.casefold()) if w]


def matches(product: str, *texts: str) -> bool:
    want = words(product)
    have = set(words(" ".join(t for t in texts if t)))
    return bool(want) and all(w in have for w in want)


def unpack(body: bytes) -> bytes:
    if body[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=__import__("io").BytesIO(body)) as gz:
            return gz.read(MAX_UNPACKED)
    return body


def _display(el) -> str:
    if el is None:
        return ""
    d = el.find("Display")
    return (d.text or "").strip() if d is not None else (el.text or "").strip()


# --------------------------------------------------------------------------- XML formats

def read_update_catalog(root, product: str) -> list[str] | None:
    if root.tag != "Manifest" or root.find("SoftwareComponent") is None:
        return None
    hits = []
    for sc in root.iter("SoftwareComponent"):
        systems = []
        for brand in sc.iter("Brand"):
            for model in brand.iter("Model"):
                systems.append(f"{_display(brand)} {_display(model)}".strip())
        if not any(matches(product, s) and words(s) == words(product) for s in systems):
            continue
        ctype = sc.find("ComponentType")
        kind = ctype.get("value", "") if ctype is not None else ""
        hits.append((0 if kind == "BIOS" else 1 if kind == "FRMW" else 2,
                     f"{_display(sc.find('Name'))} | version {sc.get('vendorVersion', '')} | released {sc.get('releaseDate', '')} | type {kind}"))
    base = root.get("baseLocation", "")
    head = [f"Update catalog {base}, dated {root.get('dateTime', '')}: {len(hits)} components for {product}."]
    return head + [line for _, line in sorted(hits, key=lambda h: h[0])]


def read_appstream(root, product: str) -> list[str] | None:
    if root.tag != "components":
        return None
    lines = []
    for comp in root.iter("component"):
        name = comp.findtext("name", "")
        if not matches(product, name, comp.findtext("summary", "")):
            continue
        releases = [f"{r.get('version', '')} ({r.get('date') or r.get('timestamp', '')})" for r in comp.iter("release")]
        lines.append(f"{name} | developer {comp.findtext('developer_name', '')} | {comp.findtext('summary', '')} | releases: {', '.join(releases)}")
    return [f"AppStream metadata: {len(lines)} components for {product}."] + lines


# --------------------------------------------------------------------------- JSON formats

def _csaf_products(node, path=()):
    """(product_id, full name) for every product in a CSAF product tree."""
    for fp in node.get("full_product_names", []) or []:
        yield fp.get("product_id", ""), fp.get("name", "")
    for br in node.get("branches", []) or []:
        here = path + (br.get("name", ""),)
        if "product" in br:
            yield br["product"].get("product_id", ""), br["product"].get("name", "") or " ".join(here)
        yield from _csaf_products(br, here)


def read_csaf(data: dict, product: str) -> list[str] | None:
    doc = data.get("document")
    if not isinstance(doc, dict) or "csaf_version" not in doc:
        return None
    pub = doc.get("publisher", {})
    lines = [f"CSAF advisory {doc.get('tracking', {}).get('id', '')}: {doc.get('title', '')}",
             f"publisher: {pub.get('name', '')} ({pub.get('namespace', '')}), released {doc.get('tracking', {}).get('initial_release_date', '')}"]
    ids = {pid: name for pid, name in _csaf_products(data.get("product_tree", {})) if matches(product, name)}
    lines += [f"product: {name}" for name in sorted(set(ids.values()))]
    for v in data.get("vulnerabilities", []) or []:
        for status, pids in (v.get("product_status") or {}).items():
            hit = sorted({ids[p] for p in pids if p in ids})
            if hit:
                lines.append(f"{v.get('cve', '')}: {status} for {', '.join(hit)}")
    return lines


def read_csaf_provider(data: dict, product: str) -> list[str] | None:
    if "canonical_url" not in data or "publisher" not in data or "metadata_version" not in data:
        return None
    pub = data["publisher"]
    lines = [f"CSAF provider metadata {data['canonical_url']}",
             f"publisher: {pub.get('name', '')} ({pub.get('namespace', '')}), role {data.get('role', '')}"]
    for d in data.get("distributions", []) or []:
        if d.get("directory_url"):
            lines.append(f"advisories: {d['directory_url']}")
        for feed in (d.get("rolie") or {}).get("feeds", []) or []:
            lines.append(f"feed: {feed.get('summary', '')} {feed.get('url', '')}")
    return lines


def read_cpe_dictionary(data: dict, product: str) -> list[str] | None:
    if data.get("format") != sites.value("cpe_dictionary_format"):
        return None
    lines = [f"CPE dictionary: {data.get('totalResults', 0)} matching records."]
    for p in data.get("products", []) or []:
        cpe = p.get("cpe", {})
        title = next((t.get("title", "") for t in cpe.get("titles", []) if t.get("lang") == "en"), "")
        lines.append(f"{cpe.get('cpeName', '')} | {title}{' | deprecated' if cpe.get('deprecated') else ''}")
    return lines


XML_READERS = (read_update_catalog, read_appstream)
JSON_READERS = (read_csaf, read_csaf_provider, read_cpe_dictionary)


def read(body: bytes, ctype: str, product: str) -> str | None:
    """The text of a source for machines about one product, or None when it is no such source."""
    if not (ctype.endswith("xml") or ctype.endswith("json") or ctype in ("application/gzip", "application/x-gzip", "application/octet-stream")):
        return None
    try:
        raw = unpack(body)
    except (OSError, EOFError):
        return None
    head = raw[:200].lstrip(b"\xef\xbb\xbf\xff\xfe\xfe\xff\x00 \t\r\n")
    lines = None
    if head.startswith(b"{"):
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if isinstance(data, dict):
            lines = next((r for r in (reader(data, product) for reader in JSON_READERS) if r is not None), None)
    elif head.startswith(b"<"):
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return None
        lines = next((r for r in (reader(root, product) for reader in XML_READERS) if r is not None), None)
    if lines is None:
        return None
    return "\n".join(lines[:MAX_LINES])
