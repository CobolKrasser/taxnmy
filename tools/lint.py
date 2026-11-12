#!/usr/bin/env python3
"""Lint the TAXNMY data: the writing rule and referee rules S1-S3.

Usage: python tools/lint.py [ROOT]

ROOT is the directory that holds products/, publishers/ and schema/
(default: the repository this script is in). Prints one line per finding:

    <file>:<line>: <rule>: <message>

and exits with 1 if there is at least one error. A signal does not fail.
No network, no AI, no keys.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keyrule  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


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
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    try:
        node = yaml.compose(text)
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        findings.append(Finding(rel, mark.line + 1 if mark else 1, "S1", f"not valid YAML: {exc}"))
        return None
    if not isinstance(data, dict):
        findings.append(Finding(rel, 1, "S1", "the file must hold a mapping of fields"))
        return None
    return Record(rel, data, _line_map(node))


def check_schema(rec: Record, validator, findings: list) -> bool:
    errors = sorted(validator.iter_errors(rec.data), key=lambda e: list(map(str, e.absolute_path)))
    for err in errors:
        where = "/".join(map(str, err.absolute_path))
        msg = f"{where}: {err.message}" if where else err.message
        findings.append(Finding(rec.file, rec.line(*err.absolute_path), "S1", msg))
    return not errors


def _s2(rec, findings, path, problems):
    for p in problems:
        findings.append(Finding(rec.file, rec.line(*path), "S2", p))


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
        findings.append(Finding(rec.file, rec.line("publisher"), "S1", f"publisher {publisher!r} has no file publishers/{publisher}.yaml"))
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


def check_unique(records: list, kind: str, findings: list) -> None:
    """S3: no match on key or alias; purl duplicates; CPE and GTIN signals."""
    names: dict[str, list] = {}
    for rec in records:
        names.setdefault(rec.data["key"], []).append((rec, ("key",), "key"))
        for i, alias in enumerate(rec.data.get("aliases", [])):
            names.setdefault(alias, []).append((rec, ("aliases", i), "alias"))
    for value, hits in sorted(names.items()):
        if len({h[0].file for h in hits}) < 2:
            continue
        for rec, path, what in hits:
            others = sorted({h[0].file for h in hits if h[0].file != rec.file})
            findings.append(Finding(rec.file, rec.line(*path), "S3", f"{what} {value!r} is already used as key or alias by {', '.join(others)}"))

    if kind != "product":
        return
    codes: dict[tuple, list] = {}
    for rec in records:
        for i, ident in enumerate(rec.data.get("identifiers", [])):
            for field, norm in (("purl", _norm_purl), ("cpe", _norm_cpe), ("gtin", _norm_gtin)):
                if field in ident:
                    codes.setdefault((field, norm(ident[field])), []).append((rec, ("identifiers", i, field)))
    for (field, value), hits in sorted(codes.items()):
        if len({h[0].file for h in hits}) < 2:
            continue
        duplicate = field == "purl" and not value.startswith("pkg:generic/")
        for rec, path in hits:
            others = ", ".join(sorted({h[0].file for h in hits if h[0].file != rec.file}))
            if duplicate:
                findings.append(Finding(rec.file, rec.line(*path), "S3", f"purl {value!r} already belongs to {others}; this is a duplicate"))
            else:
                findings.append(Finding(rec.file, rec.line(*path), "S3", f"{field} {value!r} also appears in {others}; the referee checks with S4 whether this is a product of its own", "signal"))


def lint(root: Path, schema_dir: Path | None = None) -> list[Finding]:
    root = root.resolve()
    schema_dir = schema_dir or (root / "schema")
    validators = {
        kind: jsonschema.Draft202012Validator(json.loads((schema_dir / f"{kind}.schema.json").read_text(encoding="utf-8")))
        for kind in ("product", "publisher")
    }
    findings: list[Finding] = []

    publishers = []
    for path in sorted((root / "publishers").glob("**/*")):
        if path.is_file():
            rec = load(path, root, findings)
            if rec and check_schema(rec, validators["publisher"], findings):
                check_publisher(rec, findings)
                publishers.append(rec)
    known = {rec.data["key"] for rec in publishers}

    products = []
    for path in sorted((root / "products").glob("**/*")):
        if path.is_file():
            rec = load(path, root, findings)
            if rec and check_schema(rec, validators["product"], findings):
                check_product(rec, known, findings)
                products.append(rec)

    check_unique(publishers, "publisher", findings)
    check_unique(products, "product", findings)
    return sorted(findings, key=lambda f: (f.file, f.line, f.rule, f.message))


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO
    schema_dir = root / "schema" if (root / "schema").is_dir() else REPO / "schema"
    findings = lint(root, schema_dir)
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    for f in findings:
        print(f)
        if annotate:
            level = "error" if f.level == "error" else "warning"
            print(f"::{level} file={f.file},line={f.line},title={f.rule}::{f.message}")
    errors = sum(f.level == "error" for f in findings)
    signals = len(findings) - errors
    files = sum(1 for d in ("products", "publishers") for p in (root / d).glob("**/*") if p.is_file())
    print(f"{files} files checked: {errors} errors, {signals} signals")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
