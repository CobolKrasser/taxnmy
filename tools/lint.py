#!/usr/bin/env python3
"""Lint the TAXNMY data: the writing rule, referee rules TR1-TR3 and lookalikes (TV3).

Usage: python tools/lint.py [ROOT] [--changed FILE]

ROOT is the directory that holds products/, publishers/ and schema/
(default: the repository this script is in). With --changed, FILE lists
the changed paths (one per line, relative to ROOT; - for stdin): the lint
then reports only on those files, compared with the whole dataset, so a
pull request sees only what its own files cause. Prints one line per finding:

    <file>:<line>: <rule>: <message>

and exits with 1 if there is at least one error. A signal does not fail.
No network, no AI, no keys.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard  # noqa: E402
import keyrule  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # libyaml when it is there: the same result, faster


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    rule: str
    message: str
    level: str = "error"  # "error" or "signal"

    def __str__(self) -> str:
        tag = "" if self.level == "error" else " (signal)"
        return f"{self.file}:{self.line}: {self.rule}{tag}: {self.message}"


@dataclass
class Record:
    file: str
    data: dict
    lines: dict  # path tuple -> line number (1-based)

    def line(self, *path) -> int:
        path = tuple(path)
        while path not in self.lines and path:
            path = path[:-1]
        return self.lines.get(path, 1)


def _line_map(node, path=(), out=None) -> dict:
    out = {} if out is None else out
    out.setdefault(path, node.start_mark.line + 1)
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            out[path + (k.value,)] = k.start_mark.line + 1
            _line_map(v, path + (k.value,), out)
            out[path + (k.value,)] = k.start_mark.line + 1
    elif isinstance(node, yaml.SequenceNode):
        for i, v in enumerate(node.value):
            _line_map(v, path + (i,), out)
    return out


def load(path: Path, root: Path, findings: list) -> Record | None:
    return parse(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"), findings)


def parse(rel: str, text: str, findings: list, lines: bool = True) -> Record | None:
    """A record from the text of a file. Without `lines` no line numbers are kept: for a file whose
    findings are not reported, which is then read in one pass."""
    try:
        node = yaml.compose(text, Loader=LOADER) if lines else None
        data = yaml.load(text, Loader=LOADER)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        findings.append(Finding(rel, mark.line + 1 if mark else 1, "TR1", f"not valid YAML: {exc}"))
        return None
    if not isinstance(data, dict):
        findings.append(Finding(rel, 1, "TR1", "the file must hold a mapping of fields"))
        return None
    return Record(rel, data, _line_map(node) if node is not None else {})


def check_schema(rec: Record, validator, findings: list) -> bool:
    errors = sorted(validator.iter_errors(rec.data), key=lambda e: list(map(str, e.absolute_path)))
    for err in errors:
        where = "/".join(map(str, err.absolute_path))
        msg = f"{where}: {err.message}" if where else err.message
        findings.append(Finding(rec.file, rec.line(*err.absolute_path), "TR1", msg))
    return not errors


def _s2(rec, findings, path, problems):
    for p in problems:
        findings.append(Finding(rec.file, rec.line(*path), "TR2", p))


def check_publisher(rec: Record, findings: list) -> None:
    key, name = rec.data["key"], rec.data["name"]
    problems = keyrule.part_problems(key, "publisher key")
    _s2(rec, findings, ("key",), problems)
    try:
        expected = keyrule.publisher_part(name)
    except keyrule.KeyRuleError as exc:
        _s2(rec, findings, ("name",), [f"name {name!r}: {exc}"])
    else:
        if not problems and key != expected:
            _s2(rec, findings, ("key",), [f"key {key!r} does not follow the writing rule for name {name!r}; expected {expected!r}"])
    if rec.file != f"publishers/{key}.yaml":
        _s2(rec, findings, ("key",), [f"key {key!r} does not match the file name; expected publishers/{key}.yaml"])
    for i, alias in enumerate(rec.data.get("aliases", [])):
        _s2(rec, findings, ("aliases", i), keyrule.part_problems(alias, "alias"))


def check_product(rec: Record, publishers: set, findings: list) -> None:
    key, name, publisher = rec.data["key"], rec.data["name"], rec.data["publisher"]
    problems = keyrule.key_problems(key)
    if "@" in key:
        problems.append(f"key {key!r} must not hold a version")
    _s2(rec, findings, ("key",), problems)
    if publisher not in publishers:
        findings.append(Finding(rec.file, rec.line("publisher"), "TR1", f"publisher {publisher!r} has no file publishers/{publisher}.yaml"))
    if problems:
        return
    pub_part, prod_part = key.split("/")
    if pub_part != publisher:
        _s2(rec, findings, ("key",), [f"key {key!r} does not start with the publisher {publisher!r}"])
    try:
        expected = keyrule.product_part(name)
    except keyrule.KeyRuleError as exc:
        _s2(rec, findings, ("name",), [f"name {name!r}: {exc}"])
    else:
        if prod_part != expected:
            _s2(rec, findings, ("key",), [f"key {key!r} does not follow the writing rule for name {name!r}; expected {pub_part}/{expected}"])
    if rec.file != f"products/{key}.yaml":
        _s2(rec, findings, ("key",), [f"key {key!r} does not match the file name; expected products/{key}.yaml"])
    for i, alias in enumerate(rec.data.get("aliases", [])):
        _s2(rec, findings, ("aliases", i), keyrule.key_problems(alias))


def _norm_purl(purl: str) -> str:
    for sep in "#?@":
        purl = purl.split(sep, 1)[0]
    return purl.rstrip("/").lower()


def _norm_cpe(cpe: str) -> str:
    fields = cpe.lower().split(":")
    return ":".join(fields[3:5])  # vendor:product


def _norm_gtin(gtin: str) -> str:
    return gtin.zfill(14)


def check_unique(records: list, kind: str, findings: list, report: set | None = None) -> None:
    """TR3: no match on key or alias; purl duplicates; CPE and GTIN signals. Every record is compared;
    findings are made only for the files in `report` (None: all files)."""
    if report is not None:
        kept: list = []
        check_unique_all(records, kind, kept, lambda rec: rec.file in report)
        findings += kept
        return
    check_unique_all(records, kind, findings, lambda rec: True)


def check_unique_all(records: list, kind: str, findings: list, wanted) -> None:
    names: dict[str, list] = {}
    for rec in records:
        names.setdefault(rec.data["key"], []).append((rec, ("key",), "key"))
        for i, alias in enumerate(rec.data.get("aliases", [])):
            names.setdefault(alias, []).append((rec, ("aliases", i), "alias"))
    for value, hits in sorted(names.items()):
        if len({h[0].file for h in hits}) < 2:
            continue
        for rec, path, what in hits:
            if not wanted(rec):
                continue
            others = sorted({h[0].file for h in hits if h[0].file != rec.file})
            findings.append(Finding(rec.file, rec.line(*path), "TR3", f"{what} {value!r} is already used as key or alias by {', '.join(others)}"))

    if kind != "product":
        return

    # Same words in another order, within one publisher: a signal for TR4.
    # Words are the parts between the dashes of the product part of a key.
    orders: dict[tuple, dict] = {}
    for value, hits in names.items():
        if value.count("/") != 1:
            continue
        publisher, product = value.split("/")
        orders.setdefault((publisher, tuple(sorted(product.split("-")))), {})[value] = hits
    for group in orders.values():
        if len(group) < 2:
            continue
        for value, hits in sorted(group.items()):
            for rec, path, what in hits:
                if not wanted(rec):
                    continue
                others = sorted(
                    f"{other!r} ({h[0].file})"
                    for other, other_hits in group.items() if other != value
                    for h in other_hits if h[0].file != rec.file
                )
                if others:
                    findings.append(Finding(
                        rec.file, rec.line(*path), "TR3",
                        f"{what} {value!r} has the same words as {', '.join(others)} in another order; "
                        "the referee checks with TR4 whether this is a product of its own", "signal"))

    codes: dict[tuple, list] = {}
    for rec in records:
        for i, ident in enumerate(rec.data.get("identifiers", [])):
            for field, norm in (("purl", _norm_purl), ("cpe", _norm_cpe), ("gtin", _norm_gtin), ("winget", str.lower), ("homebrew", str.lower)):
                if field in ident:
                    codes.setdefault((field, norm(ident[field])), []).append((rec, ("identifiers", i, field)))
    for (field, value), hits in sorted(codes.items()):
        if len({h[0].file for h in hits}) < 2:
            continue
        duplicate = field in ("winget", "homebrew") or (field == "purl" and not value.startswith("pkg:generic/"))
        for rec, path in hits:
            if not wanted(rec):
                continue
            others = ", ".join(sorted({h[0].file for h in hits if h[0].file != rec.file}))
            if duplicate:
                findings.append(Finding(rec.file, rec.line(*path), "TR3", f"{field} {value!r} already belongs to {others}; this is a duplicate"))
            else:
                findings.append(Finding(rec.file, rec.line(*path), "TR3", f"{field} {value!r} also appears in {others}; the referee checks with TR4 whether this is a product of its own", "signal"))


def check_lookalikes(publishers: list, products: list, findings: list, report: set | None = None) -> None:
    """TV3: a key or a domain one or two characters from an existing one, or the same once characters
    that look alike are read as one (tools/guard.py). A signal: the gate holds the proposal for a
    person. Compared with the files outside `report` (None: with every other file)."""
    def existing(files: set, rec) -> bool:
        return bool(files - {rec.file}) if report is None else bool(files - report)

    def wanted(rec) -> bool:
        return report is None or rec.file in report

    def note(rec, path, what, value, hits, values):
        for old, why in hits:
            where = ", ".join(sorted(values[old])[:3])
            findings.append(Finding(rec.file, rec.line(*path), "TV3",
                                    f"{what} {value!r} looks like {old!r} ({where}): {why}; a person decides", "signal"))

    keys: dict[str, set] = {}
    for rec in publishers:
        for v in [rec.data["key"], *rec.data.get("aliases", [])]:
            keys.setdefault(v, set()).add(rec.file)
    for rec in publishers:
        if wanted(rec):
            key = rec.data["key"]
            note(rec, ("key",), "publisher", key,
                 guard.lookalikes(key, [v for v, f in keys.items() if existing(f, rec)]), keys)

    parts: dict[str, dict[str, set]] = {}
    for rec in products:
        for v in [rec.data["key"], *rec.data.get("aliases", [])]:
            if v.count("/") == 1:
                pub, part = v.split("/")
                parts.setdefault(pub, {}).setdefault(part, set()).add(rec.file)
    for rec in products:
        if wanted(rec) and rec.data["key"].count("/") == 1:
            pub, part = rec.data["key"].split("/")
            same = parts.get(pub, {})
            note(rec, ("key",), "product", rec.data["key"],
                 [(f"{pub}/{old}", why) for old, why in guard.lookalikes(part, [v for v, f in same.items() if existing(f, rec)])],
                 {f"{pub}/{v}": f for v, f in same.items()})

    owners: dict[str, set] = {}
    mine: dict[str, set] = {}
    for rec in publishers + products:
        mine[rec.file] = {o for o in map(guard.owner, guard.urls_in(rec.data)) if o}
        for o in mine[rec.file]:
            owners.setdefault(o, set()).add(rec.file)
    # A domain is new when no other file uses it (outside the pull request, with `report`).
    known = set(owners) if report is None else {o for o, f in owners.items() if f - report}
    pool = sorted(known)
    for rec in publishers + products:
        if not wanted(rec):
            continue
        for o in sorted(mine[rec.file]):
            if (owners[o] == {rec.file}) if report is None else (o not in known):
                hits = [(old, why) for old, why in guard.lookalikes(o, pool) if existing(owners[old], rec)]
                note(rec, ("source",), "domain", o, hits, owners)


def validators_for(schema_dir: Path) -> dict:
    return {
        kind: jsonschema.Draft202012Validator(json.loads((schema_dir / f"{kind}.schema.json").read_text(encoding="utf-8")))
        for kind in ("product", "publisher")
    }


def kind_of(rel: str) -> str | None:
    if rel.startswith("publishers/"):
        return "publisher"
    if rel.startswith("products/"):
        return "product"
    return None


class Dataset:
    """Every file of a data root, read once. `check` then lints chosen files against all the others,
    with files added, changed or taken out (`overrides`) without touching the disk. This is what lets
    a pull request with thousands of files, and a harness with many cases, lint the dataset once."""

    def __init__(self, root: Path, schema_dir: Path | None = None, detailed: set | None = None):
        """`detailed`: the files whose findings may be reported, read with line numbers (None: all)."""
        self.root = root.resolve()
        self.validators = validators_for(schema_dir or (self.root / "schema"))
        self.texts: dict[str, str] = {}
        self.records: dict[str, Record] = {}  # the files that pass the schema
        self.own: dict[str, list] = {}  # findings of reading a file and of the schema, per file
        for part in ("publishers", "products"):
            for path in sorted((self.root / part).glob("**/*")):
                if path.is_file():
                    rel = path.relative_to(self.root).as_posix()
                    self.texts[rel] = path.read_text(encoding="utf-8")
                    self._read(rel, self.texts[rel], detailed is None or rel in detailed, self.records, self.own)

    def _read(self, rel: str, text: str, lines: bool, records: dict, own: dict) -> None:
        found: list = []
        rec = parse(rel, text, found, lines)
        if rec and check_schema(rec, self.validators[kind_of(rel)], found):
            records[rel] = rec
        own[rel] = found

    def files(self, part: str, overrides: dict | None = None) -> list[str]:
        """The files under products/ or publishers/, with the overrides applied."""
        overrides = overrides or {}
        out = {rel for rel in self.texts if rel.startswith(part + "/") and rel not in overrides}
        out |= {rel for rel, text in overrides.items() if text is not None and rel.startswith(part + "/")}
        return sorted(out)

    def text(self, rel: str, overrides: dict | None = None) -> str | None:
        overrides = overrides or {}
        return overrides[rel] if rel in overrides else self.texts.get(rel)

    def check(self, report: set | None = None, overrides: dict | None = None) -> list[Finding]:
        """The findings on the files in `report` (None: all files), against the whole dataset with
        `overrides` (path -> text, or None to take a file out) applied."""
        overrides = overrides or {}
        records, own = dict(self.records), dict(self.own)
        for rel in overrides:
            records.pop(rel, None)
            own.pop(rel, None)
        for rel, text in overrides.items():
            if text is not None and kind_of(rel):
                self._read(rel, text, True, records, own)
        wanted = (lambda rel: True) if report is None else (lambda rel: rel in report)  # noqa: E731
        findings = [f for rel, found in own.items() if wanted(rel) for f in found]
        publishers = [rec for rel, rec in sorted(records.items()) if kind_of(rel) == "publisher"]
        products = [rec for rel, rec in sorted(records.items()) if kind_of(rel) == "product"]
        known = {rec.data["key"] for rec in publishers}
        for rec in publishers:
            if wanted(rec.file):
                check_publisher(rec, findings)
        for rec in products:
            if wanted(rec.file):
                check_product(rec, known, findings)
        check_unique(publishers, "publisher", findings, report)
        check_unique(products, "product", findings, report)
        check_lookalikes(publishers, products, findings, report)
        return sorted(findings, key=lambda f: (f.file, f.line, f.rule, f.message))


def lint(root: Path, schema_dir: Path | None = None, changed: set | None = None) -> list[Finding]:
    """The findings of the dataset in root. With `changed`, only those on the changed files."""
    return Dataset(root, schema_dir, changed).check(changed)


def read_changed(source: str, root: Path) -> set:
    """The changed data files from a list of paths, relative to the root of the data."""
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    return {line.strip() for line in text.splitlines()
            if line.strip().startswith(("products/", "publishers/")) and (root / line.strip()).is_file()}


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("root", nargs="?", default=str(REPO))
    p.add_argument("--changed", help="a file listing the changed paths, one per line (- for stdin)")
    args = p.parse_args(argv[1:])
    root = Path(args.root)
    schema_dir = root / "schema" if (root / "schema").is_dir() else REPO / "schema"
    changed = read_changed(args.changed, root) if args.changed else None
    findings = lint(root, schema_dir, changed)
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    for f in findings:
        print(f)
        if annotate:
            level = "error" if f.level == "error" else "warning"
            print(f"::{level} file={f.file},line={f.line},title={f.rule}::{f.message}")
    errors = sum(f.level == "error" for f in findings)
    signals = len(findings) - errors
    files = sum(1 for d in ("products", "publishers") for p in (root / d).glob("**/*") if p.is_file())
    if changed is None:
        print(f"{files} files checked: {errors} errors, {signals} signals")
    else:
        print(f"{len(changed)} changed files checked against {files}: {errors} errors, {signals} signals")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
