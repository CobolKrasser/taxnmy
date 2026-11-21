#!/usr/bin/env python3
"""Test harness for the AI referee (rules TP1, TP3, TP5, TK2 and TE1 and TV1; TV2-TV5 by code, tools/guard.py).

Reads the test set (referee/testset/cases.yaml), the prompt (referee/prompt.md)
and the output schema (referee/verdict.schema.json), and runs each case as
referee/README.md describes: stage it, lint it, and send the cases that pass
the lint to the referee with one call each. It computes the outcome from the
answer and scores it against the expected outcome.

Usage:

    python tools/referee.py run [--condition fetched|memory|web] [--live] ...
    python tools/referee.py fetch --output sources.json   # read the sources (free, no key)
    python tools/referee.py run --sources sources.json ...
    python tools/referee.py check-urls
    python tools/referee.py compare RUN_DIR RUN_DIR ...

Conditions (referee/r13-research.md):
    fetched  the source texts of the proposal, fetched by code (the base run)
    memory   no source texts; the model answers from what it knows
    web      no source texts; the model may search the web (at most 3 searches)

Without --live nothing is sent to the API. The budget (--budget, default
$50.00) is a hard limit for all runs in the output directory together: before
each request the harness counts its input tokens (free) and sets max_tokens
so that the request cannot cost more than what is left; when that leaves too
little room, it stops. The key is read from ANTHROPIC_API_KEY and is never
written to a file or a log.

The harness reads only data files (YAML, JSON, Markdown). It never imports or
runs code from a proposal.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive  # noqa: E402
import gate  # noqa: E402
import guard  # noqa: E402
import keyrule  # noqa: E402
import lint  # noqa: E402
from fetch import (  # noqa: E402,F401 - Source and the text helpers are part of the harness API
    MIN_CHARS, SOURCE_CHARS, USER_AGENT, Fetcher, Source, StoredFetcher, footer_counts, html_to_text, page_text,
    moved, pdf_to_text, source_key,
)

REPO = Path(__file__).resolve().parent.parent
REFEREE = REPO / "referee"
RULES = ("TP3", "TP5", "TK2", "TP1", "TE1", "TV1")  # the rules the AI referee answers
HELD = ("TV3", "TV5")  # rules code holds a proposal on for a person, whatever the referee answers
OUTCOMES = ("accept", "reject", "alias", "parked")
CONDITIONS = ("fetched", "memory", "web")
# The agreed model of the referee (TG12): every default in code and workflows is this stand.
# referee/README.md § The agreed model says why; tests/test_numbering.py checks the workflows.
MODEL = "claude-opus-5-5"
EFFORT = "low"
BUDGET = 50.00  # dollars, every run in referee/runs together (17 November 2026; it was $20)
MAX_TOKENS = 8000  # per request; the budget may lower it
MIN_OUTPUT = 2000  # a request with less room for output than this is not sent
WEB_ALLOWANCE = 20_000  # web: input tokens reserved per search (not a hard bound)
WEB_TOOL_OVERHEAD = 2_000  # web: input tokens of the tool definition, which token counting cannot see

# The 29 cases of the TE4 research (referee/r13-research.md, "Which cases").
TE4_CASES = (
    "G02", "G06", "G08", "G17", "G18", "G19", "G20", "G21", "G22", "G23", "G24", "G25", "G27", "G28",
    "B06", "B12", "B14", "B15", "B20", "B21", "B22", "B23", "B24", "B25", "B26", "B27", "B28", "B29", "B30",
)
TE4_RULES = ("TP3", "TP5", "TP1", "TE1")

# The system prompt of conditions memory and web (TE4), written by the analyst.
TE4_PROMPT = REFEREE / "prompt-r13.md"

# What the sources block says in the conditions without fetched texts (referee/r13-research.md).
NO_SOURCES = {
    "memory": "No source texts are given. Answer from what you know; give no quotes.",
    "web": ("No source texts are given. Read the URLs in the proposal with web fetch, and find other pages with "
            "web search. Quote only from pages you read with web fetch."),
}

CASE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": "^[GHB][0-9]{2}$"},
        "type": {"type": "string"},
        "expect": {"type": "array", "items": {"enum": list(OUTCOMES)}, "minItems": 1},
        "rules": {"type": "array", "items": {"type": "string", "pattern": "^(TP[13]|TP5|TP10|TK[23]|TE[17]|TV[1-6])$"}},
        "alias_of": {"type": "string"},
        "lint": {"type": "string", "pattern": "^(pass|signal|error (TE1|TK[234]|TV[45]|TP10))$"},
        "holdout": {"type": "array", "items": {"type": "string"}},
        "hide": {"type": "array", "items": {"type": "string"}},
        "context": {"type": "object", "additionalProperties": {"type": "string"}},
        "proposal": {"type": "object", "additionalProperties": {"type": "string"}},
        "delete": {"type": "array", "items": {"type": "string"}},
        "verify": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
    },
    "required": ["id", "type", "expect", "lint"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- the test set

DATA_FILE = re.compile(r"(products/[a-z0-9-]+/[a-z0-9-]+|publishers/[a-z0-9-]+)\.yaml")

@dataclass
class Case:
    id: str
    type: str
    expect: list
    lint: str
    rules: list = field(default_factory=list)
    alias_of: str = ""
    holdout: list = field(default_factory=list)
    hide: list = field(default_factory=list)
    context: dict = field(default_factory=dict)
    proposal: dict = field(default_factory=dict)
    delete: list = field(default_factory=list)
    verify: list = field(default_factory=list)
    note: str = ""

    @property
    def good(self) -> bool:
        return self.id.startswith("G")

    @property
    def bad(self) -> bool:
        return self.id.startswith("B")


def load_cases(path: Path) -> list[Case]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(CASE_SCHEMA)
    cases, problems, seen = [], [], set()
    for i, raw in enumerate(data.get("cases", [])):
        errors = list(validator.iter_errors(raw))
        for err in errors:
            where = "/".join(map(str, err.absolute_path))
            problems.append(f"case {i + 1} ({raw.get('id', '?')}): {where + ': ' if where else ''}{err.message}")
        if errors:
            continue
        if raw["id"] in seen:
            problems.append(f"case {raw['id']} appears twice")
        seen.add(raw["id"])
        if not raw.get("holdout") and not raw.get("proposal") and not raw.get("delete"):
            problems.append(f"case {raw['id']} proposes nothing (no holdout, no proposal, no delete)")
        cases.append(Case(**raw))
    if problems:
        raise SystemExit("The test set has problems:\n  " + "\n  ".join(problems))
    if not cases:
        raise SystemExit(f"No cases in {path}")
    return cases


def select(cases: list[Case], ids: list[str] | None, condition: str) -> list[Case]:
    """The cases of a run: the ids given, or all cases for fetched and the TE4 cases otherwise."""
    if ids == ["all"]:
        return cases
    wanted = ids or (None if condition == "fetched" else list(TE4_CASES))
    if wanted is None:
        return cases
    by_id = {c.id: c for c in cases}
    missing = [i for i in wanted if i not in by_id]
    if missing:
        raise SystemExit(f"Unknown case(s): {', '.join(missing)}")
    return [by_id[i] for i in wanted]


def product_file(files: dict) -> tuple[str, dict]:
    """The proposed product file and its data."""
    for rel, text in files.items():
        if rel.startswith("products/"):
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError:
                data = None
            return rel, data if isinstance(data, dict) else {}
    return "", {}


# --------------------------------------------------------------------------- staging (README § Staging a case)

@dataclass
class Staged:
    files: dict  # path -> text: what is proposed (hold-outs and proposal)
    findings: list  # lint findings on those files
    lint_ok: bool  # the findings match the field `lint`
    lint_seen: str  # what the findings amount to: pass, signal or error S..
    existing: str  # the <existing> block
    publisher_source: str = ""  # the source of an existing publisher file, fetched with the others
    machine_sources: list = field(default_factory=list)  # the publisher's machine_sources (URLs), for step 0 on a closed source
    publisher_urls: list = field(default_factory=list)  # every URL in the publisher's file and its other products: step 4 searches their domains
    publisher: dict = field(default_factory=dict)  # the publisher file, proposed or existing: what the gate reads for TP5 and TK2
    known_publisher: bool = False  # the publisher file is in the data and not part of the proposal (the gate, TP5)
    old: dict = field(default_factory=dict)  # path -> text in the data, for a proposed file that changes an existing one (TV5)
    held: dict = field(default_factory=dict)  # rule -> reason: TV3 and TV5, held for a person by code (tools/guard.py)
    identity: str = ""  # the <identity> block: what code found about who the proposal is (TV2)

    @property
    def product(self) -> dict:
        return product_file(self.files)[1]


MAX_EXISTING = 20  # <existing>: at most this many products of the same publisher


def words_of(*texts: str) -> set:
    """The words of keys and names, in lower case: what 'resembles' compares."""
    return {w for t in texts for w in re.split(r"[^0-9a-z]+", t.casefold()) if len(w) >= 2}


def resembles(a: set, b: set) -> int:
    """How many words of a match a word of b: the same word, or one that begins with the other
    (at least 3 letters), as a model name with a suffix does."""
    return sum(any(x == y or (min(len(x), len(y)) >= 3 and (x.startswith(y) or y.startswith(x))) for y in b) for x in a)


class Stager:
    """The data, read once (lint.Dataset). Each case is staged on it in memory, without copying or
    touching a file, and the lint compares only the files of the case with the rest (TAX-16)."""

    def __init__(self, data_root: Path = REPO):
        self.source = data_root
        self.data = lint.Dataset(data_root, REPO / "schema", detailed=set())
        self._owners = None  # rel -> (publisher key, owners of its URLs), built on first use (TV2)

    def close(self) -> None:
        pass

    def stage(self, case: Case) -> Staged:
        files = {}
        for rel in case.holdout:
            text = self.data.texts.get(rel)
            if text is None:
                raise SystemExit(f"case {case.id}: hold-out {rel} is not in the data")
            files[rel] = text
        files.update(case.proposal)
        data_context = {k: v for k, v in case.context.items() if DATA_FILE.fullmatch(k)}  # the log of a case is no data
        staged = self.stage_files(files, removed=case.holdout + case.hide, context=data_context, deleted=case.delete)
        staged.lint_ok = lint_matches(case.lint, staged.findings)
        return staged

    def stage_files(self, files: dict, removed=(), context: dict | None = None, deleted=()) -> Staged:
        """Proposed files (path -> text) on the data, with `removed` taken out and `context` added first.
        The PR mode (tools/pr_referee.py) stages a pull request this way, without removals or context.
        `deleted` are data files the proposal itself deletes or renames: each is an error TK3, and so is
        a change that takes the key or an alias out of an existing file (TAX-45)."""
        removed = {rel: None for rel in removed}
        context = context or {}
        findings = self.data.check(report=set(files), overrides={**removed, **context, **files})
        # A proposed file that is in the data, and not taken out for the case, changes an existing one.
        old = {rel: self.data.texts[rel] for rel in files
               if rel in self.data.texts and rel not in removed and rel not in context}
        # A file of the context that the proposal changes is an existing one too (the case says so).
        old.update({rel: context[rel] for rel in files if rel in context})
        findings = publisher_replaced(old, files) + kept_keys(
            old, files, {rel: self.data.texts[rel] for rel in deleted if rel in self.data.texts and rel not in removed}) \
            + archive.faults(old, files) + findings
        self.view = {**removed, **context, **{rel: None for rel in files}}  # the proposal is not part of what exists
        return Staged(files=files, findings=findings, lint_ok=True, lint_seen=lint_summary(findings),
                      existing=self._existing(files, findings), publisher_source=self._publisher_source(files),
                      machine_sources=self._machine_sources(files), publisher_urls=self._publisher_urls(files),
                      publisher=self._publisher_data(files), known_publisher=bool(self._publisher_file(files)[0]),
                      old=old, held=held_by_code(files, findings, old), identity=self._identity(files))

    def _identity(self, files: dict) -> str:
        """TV2: whether the publisher and each domain of the proposal are the same as an existing one,
        compared character by character. The referee gets the result, not the question."""
        if self._owners is None:
            self._owners = {}
            for rel, rec in self.data.records.items():
                pub = rec.data.get("publisher") if rel.startswith("products/") else rec.data.get("key")
                self._owners[rel] = (pub, {o for o in map(guard.owner, guard.urls_in(rec.data)) if o})
        by_owner: dict[str, set] = {}
        for rel, (pub, owners) in self._owners.items():
            if rel not in self.view:
                for o in owners:
                    by_owner.setdefault(o, set()).add(pub)
        lines, seen = [], set()
        for rel, text in files.items():
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError:
                continue
            if not isinstance(data, dict):
                continue
            if rel.startswith("publishers/"):
                key = data.get("key")
                there = isinstance(key, str) and self._text(f"publishers/{key}.yaml") is not None
                lines.append(f"publisher {key}: " + ("already in TAX" if there else "new to TAX"))
                second = data.get("second_source")
                lines.append(f"publisher {key}: " + (f"gives a second source, {second}" if isinstance(second, str)
                                                     else "gives no second source"))
            for o in sorted({o for o in map(guard.owner, guard.urls_in(data)) if o} - seen):
                seen.add(o)
                pubs = sorted(p for p in by_owner.get(o, ()) if isinstance(p, str))
                lines.append(f"domain {o}: " + (f"in TAX for the publisher{'s' if len(pubs) > 1 else ''} {', '.join(pubs[:5])}"
                                                if pubs else "new to TAX"))
        return "\n".join(lines)

    def _text(self, rel: str) -> str | None:
        return self.data.text(rel, self.view)

    def _products_of(self, pub: str) -> list[str]:
        prefix = f"products/{pub}/"
        return [rel for rel in self.data.files("products", self.view)
                if rel.startswith(prefix) and rel.endswith(".yaml") and "/" not in rel[len(prefix):]]

    def _publisher_file(self, files: dict) -> tuple[str, str]:
        """The existing publisher file of the proposed product, unless the proposal brings it."""
        _, data = product_file(files)
        pub = data.get("publisher")
        if not isinstance(pub, str) or not re.fullmatch(r"[a-z0-9-]+", pub):
            return "", ""
        rel = f"publishers/{pub}.yaml"
        text = self._text(rel)
        if rel in files or text is None:
            return "", ""
        return rel, text

    def _publisher_data(self, files: dict) -> dict:
        """The publisher file of the proposed product: proposed with it, or existing in the data."""
        pub = product_file(files)[1].get("publisher")
        if not isinstance(pub, str) or not re.fullmatch(r"[a-z0-9-]+", pub):
            return {}
        rel = f"publishers/{pub}.yaml"
        text = files.get(rel) or self._text(rel) or ""
        try:
            data = yaml.safe_load(text) if text else {}
        except yaml.YAMLError:
            return {}
        return data if isinstance(data, dict) else {}

    def _publisher_urls(self, files: dict) -> list:
        """The URLs in the publisher's file (proposed or existing) and in its other products in the data."""
        _, data = product_file(files)
        pub = data.get("publisher")
        if not isinstance(pub, str) or not re.fullmatch(r"[a-z0-9-]+", pub):
            return []
        texts = [files[f"publishers/{pub}.yaml"]] if f"publishers/{pub}.yaml" in files else []
        if not texts and self._text(f"publishers/{pub}.yaml") is not None:
            texts.append(self._text(f"publishers/{pub}.yaml"))
        texts += [self._text(rel) for rel in self._products_of(pub)]
        urls = []
        for text in texts:
            try:
                urls += [u for u in urls_of({"x": text}) if u not in urls]
            except yaml.YAMLError:
                pass
            try:
                for e in (yaml.safe_load(text) or {}).get("machine_sources") or []:
                    if isinstance(e, dict) and isinstance(e.get("url"), str) and e["url"] not in urls:
                        urls.append(e["url"])
            except (yaml.YAMLError, AttributeError):
                pass
        return urls

    def _machine_sources(self, files: dict) -> list:
        """The URLs in machine_sources of the publisher, proposed or existing (schema 1.2.0)."""
        _, data = product_file(files)
        rel = f"publishers/{data.get('publisher')}.yaml"
        if rel in files:
            text = files[rel]
        elif re.fullmatch(r"publishers/[a-z0-9-]+\.yaml", rel) and self._text(rel) is not None:
            text = self._text(rel)
        else:
            return []
        try:
            entries = (yaml.safe_load(text) or {}).get("machine_sources") or []
        except yaml.YAMLError:
            return []
        return [e["url"] for e in entries if isinstance(e, dict) and isinstance(e.get("url"), str)]

    def _publisher_source(self, files: dict) -> str:
        rel, text = self._publisher_file(files)
        if not rel:
            return ""
        source = (yaml.safe_load(text) or {}).get("source", "")
        return source if isinstance(source, str) and source.startswith(("http://", "https://")) else ""

    def _record(self, rel: str) -> dict:
        rec = self.data.records.get(rel)
        if rec is not None and rel not in self.view:
            return rec.data
        data = yaml.safe_load(self._text(rel) or "")
        return data if isinstance(data, dict) else {}

    def _existing(self, files: dict, findings: list) -> str:
        """For each signal the existing product it points to; the products of the same publisher that
        resemble the proposal on key or name, at most MAX_EXISTING; the publishers that share a word
        with the proposed publisher key."""
        parts, shown = [], set()
        rel, text = self._publisher_file(files)
        if rel:
            parts.append(f"# {rel}\n{text.rstrip()}")
        for f in findings:
            if f.level != "signal":
                continue
            for rel in re.findall(r"products/[a-z0-9-]+/[a-z0-9-]+\.yaml", f.message):
                text = self._text(rel)
                if rel not in shown and rel not in files and text is not None:
                    shown.add(rel)
                    parts.append(f"# {rel}\n{text.rstrip()}")
        proposed = product_file(files)[1]
        own = words_of(str(proposed.get("key", "")).split("/")[-1], str(proposed.get("name", "")))
        publishers = sorted({_publisher_of(rel, text) for rel, text in files.items() if rel.startswith("products/")} - {""})
        for pub in publishers:
            scored = []
            for rel in self._products_of(pub):
                data = self._record(rel)
                key, name = str(data.get("key", "")), str(data.get("name", ""))
                score = resembles(own, words_of(key.split("/")[-1], name))
                if score:
                    scored.append((-score, key, name))
            lines = [f"{key}: {name}" for _, key, name in sorted(scored)[:MAX_EXISTING]]
            parts.append(f"# products of publisher {pub} that resemble the proposal (at most {MAX_EXISTING})\n"
                         + ("\n".join(lines) if lines else "none"))
            words = set(pub.split("-"))
            near = sorted(Path(r).stem for r in self.data.files("publishers", self.view)
                          if r.count("/") == 1 and Path(r).stem != pub and words & set(Path(r).stem.split("-")))
            parts.append(f"# publishers that share a word with {pub}\n" + ("\n".join(near) if near else "none"))
        return "\n\n".join(parts)


def held_items(files: dict, findings: list, old: dict) -> list[tuple[str, str]]:
    """TV3 and TV5 (tools/guard.py), one (rule, reason) per thing code holds the proposal on. TV3
    from the lookalike signals of the lint; TV5 from what a change moves to another domain or value."""
    items = [("TV3", f"{f.file}: {f.message}") for f in findings if f.rule == "TV3"]
    for rel, text in old.items():
        try:
            before, after = yaml.safe_load(text), yaml.safe_load(files[rel])
        except yaml.YAMLError:
            continue
        items += [("TV5", f"{rel}: {m}") for m in guard.replaced(before, after)]
    return items


def join_held(items: list[tuple[str, str]]) -> dict:
    """rule -> the reason code holds the proposal for a person, from `held_items`."""
    held = {}
    near = [text for rule, text in items if rule == "TV3"]
    if near:
        held["TV3"] = "; ".join(near)
    moved = [text for rule, text in items if rule == "TV5"]
    if moved:
        held["TV5"] = "a change to an existing file that a person decides on: " + "; ".join(moved)
    return held


def publisher_replaced(old: dict, files: dict) -> list:
    """TV5 (TAX-46): a change that replaces the publisher of an existing product is an error, before
    any other: the product is proposed again under its publisher."""
    out = []
    for rel in sorted(old):
        who, _ = guard.moves(_yaml(old[rel]), _yaml(files.get(rel, "")))
        if who:
            out.append(lint.Finding(rel, 1, "TV5", f"{who}; a change may not replace the publisher. "
                                                   "The product may be proposed again under its publisher"))
    return out


def kept_keys(old: dict, files: dict, deleted: dict) -> list:
    """TK3 (TAX-45): a key stays with its product and an alias stays with its key. A deleted or
    renamed data file (`deleted`: path -> its text in the data), or a change that takes a key or an
    alias out (`old` -> `files`), is an error; only a maintainer takes a product out (TG13)."""
    out = []
    for rel, text in sorted(deleted.items()):
        data = _yaml(text)
        what = data.get("key") if isinstance(data.get("key"), str) else rel
        out.append(lint.Finding(rel, 1, "TK3", f"{what} is deleted or renamed; a key and its aliases stay, and only "
                                               "a maintainer takes a product out (referee/decisions.yaml)"))
    for rel in sorted(old):
        for what in guard.taken_out(_yaml(old[rel]), _yaml(files.get(rel, ""))):
            out.append(lint.Finding(rel, 1, "TK3", f"{what}; a key and its aliases stay"))
    return out


def _yaml(text: str) -> dict:
    try:
        data = yaml.safe_load(text or "")
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def held_by_code(files: dict, findings: list, old: dict) -> dict:
    """TV3 and TV5: rule -> the reason code holds the proposal for a person."""
    return join_held(held_items(files, findings, old))


def hold(staged: "Staged", sources: list | None = None) -> dict:
    """Every rule code holds the proposal on (HELD): TV3 and TV5 of the staging. `sources` is not
    used: what code holds on needs no text."""
    return {r: staged.held[r] for r in HELD if r in staged.held}


def proposed_publishers(staged: "Staged") -> list[tuple[str, dict]]:
    """(path, data) of every publisher file in the proposal, new or changed."""
    out = []
    for rel, text in staged.files.items():
        if rel.startswith("publishers/"):
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError:
                continue
            if isinstance(data, dict):
                out.append((rel, data))
    return out


def replacement_fault(staged: "Staged", fetcher) -> str:
    """TV5 (TAX-46): why a change to an existing product is rejected, or ''. A source moves to
    another domain only if the old source, read now, points there (tools/guard.py `points_to`). Without a reader
    (`fetcher` None) a move is not shown, so it is rejected."""
    for rel in sorted(staged.old):
        _, shifts = guard.moves(_yaml(staged.old[rel]), _yaml(staged.files.get(rel, "")))  # the publisher: `publisher_replaced`
        for label, a, b in shifts:
            src = fetcher.fetch(a, "") if fetcher is not None else None
            if src is None or not (gate.read(src) or moved(src)):
                why = "the old source cannot be read" if src is not None else "the old source was not read"
            elif guard.points_to(src.text, moved(src), b):
                continue
            else:
                why = "the old source does not point to it"
            return (f"{rel}: {label} moves from {guard.owner(a) or guard.host_of(a)} to "
                    f"{(guard.owner(b) or guard.host_of(b)) if b else 'nothing'}, and {why}")
    return ""


def second_source_fault(staged: "Staged", sources: list | None) -> str:
    """TV4: why a second source that a proposed publisher file gives does not pass, or ''. A second
    source is optional; one that is given must be readable, off the publisher's domain, and name it.
    Without texts (`sources` None) nothing is checked."""
    if sources is None:
        return ""
    texts = {u: s.text for u, s in gate.own_sources(sources).items() if gate.read(s)}
    for rel, data in proposed_publishers(staged):
        try:
            before = yaml.safe_load(staged.old[rel]) if rel in staged.old else None
        except yaml.YAMLError:
            before = None
        if isinstance(before, dict) and before.get("second_source") == data.get("second_source"):
            continue  # a second source TAX already holds is not read again
        why = guard.second_source(data, texts)
        if why:
            return f"{rel}: {why}"
    return ""


def held_outcome(outcome: str, rule: str, held: dict) -> tuple[str, str]:
    """A held proposal is never accepted: what would be accept, or parked, is parked on the held rule.
    A reject or an alias stays: it does not take the proposal in."""
    if held and outcome in ("accept", "parked"):
        return "parked", next(iter(held))
    return outcome, rule


def _publisher_of(rel: str, text: str) -> str:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return ""
    pub = data.get("publisher", "") if isinstance(data, dict) else ""
    return pub if isinstance(pub, str) and re.fullmatch(r"[a-z0-9-]+", pub) else ""


def lint_summary(findings: list) -> str:
    errors = sorted({f.rule for f in findings if f.level == "error"})
    if errors:
        return "error " + " ".join(errors)
    return "signal" if findings else "pass"


def lint_matches(expected: str, findings: list) -> bool:
    if expected.startswith("error "):
        return any(f.level == "error" and f.rule == expected.split()[1] for f in findings)
    return lint_summary(findings) == expected


# --------------------------------------------------------------------------- sources

def case_urls(staged: "Staged") -> list[str]:
    """The URLs to fetch for a case: the sources of the proposed files and of the publisher file."""
    urls = urls_of(staged.files)
    if staged.publisher_source and staged.publisher_source not in urls:
        urls.append(staged.publisher_source)
    for _, data in proposed_publishers(staged):
        second = data.get("second_source")
        if isinstance(second, str) and second.startswith(("http://", "https://")) and second not in urls:
            urls.append(second)
    return urls


def product_name(staged: "Staged") -> str:
    """The name of the proposed product: what a source for machines is searched for (step 0)."""
    name = product_file(staged.files)[1].get("name", "")
    return name if isinstance(name, str) else ""


def gather_sources(fetcher, staged: "Staged") -> list[Source]:
    """The sources of a case, and for each refused one the sources step 4 found instead."""
    name = product_name(staged)
    sources = []
    for url in case_urls(staged):
        src = fetcher.fetch(url, name)
        sources.append(src)
        if src.status in CLOSED and hasattr(fetcher, "alternatives"):
            sources += fetcher.alternatives(url)
    return sources


def urls_of(files: dict) -> list[str]:
    """Every distinct `source` URL in the proposed files, in order."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "source" and isinstance(v, str) and v.startswith(("http://", "https://")) and v not in found:
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for text in files.values():
        try:
            walk(yaml.safe_load(text))
        except yaml.YAMLError:
            pass
    return found


# --------------------------------------------------------------------------- the prompt and the message

def load_system_prompt(path: Path, footer: bool = False) -> str:
    """The text block under '## System prompt' in referee/prompt.md; with `footer` (TAX-43), and a
    section '## Page footer' in the file, followed by the text block of that section."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^## System prompt\s*$.*?^```text\n(.*?)^```", text, re.S | re.M)
    if not m:
        raise SystemExit(f"No system prompt (a ```text block under '## System prompt') in {path}")
    prompt = m.group(1).rstrip() + "\n"
    f = re.search(r"^## Page footer\b[^\n]*$.*?^```text\n(.*?)^```", text, re.S | re.M) if footer else None
    return prompt + "\n" + f.group(1).rstrip() + "\n" if f else prompt


MARKERS = re.compile(r"<(?=\s*/?\s*(?:proposal|signals|decided|identity|existing|sources|source|page-footer)\b)", re.I)


def inert(text: str) -> str:
    """TV1: a text from a proposal or a source cannot write the markers of the message. The `<` of
    anything that looks like one becomes `&lt;`, so it cannot close a block or open another."""
    return MARKERS.sub("&lt;", text)


def build_message(staged: Staged, condition: str, sources: list[Source], decided: dict | None = None,
                  footer: bool = False) -> str:
    """The user message, in the order of referee/prompt.md § User message. `decided` holds the
    rules the gate settled (None: no gate); the message then says which rules are left for the
    referee, with what code found of them (`gate.partly`). With `footer` (TAX-43) a source block
    ends with the footer of its page, in <page-footer>."""
    proposal = "\n".join(f"# {rel}\n{inert(text.rstrip())}" for rel, text in staged.files.items())
    signals = "\n".join(f"{f.rule}: {inert(f.message)}" for f in staged.findings if f.level == "signal") or "none"
    gate_block = ""
    notes = (gate.partly(staged.product, staged.publisher, sources if condition == "fetched" else None, decided,
                         new_publisher=not staged.known_publisher) if decided is not None else {})
    if decided or notes:
        lines = [f"{r}: {j['verdict']} ({j['reason']})" for r, j in sorted(decided.items())]
        lines += [f"{r} in part: {note}" for r, note in sorted(notes.items())]
        lines.append("Judge only: " + ", ".join(r for r in RULES if r not in decided))
        gate_block = "<decided>\n" + "\n".join(lines) + "\n</decided>\n\n"
    if condition == "fetched":
        blocks = [f'<source url="{inert(s.url)}" status="{s.status}"'
                  + (f' instead-of="{inert(s.instead_of)}"' if s.instead_of else "") + f'>\n{inert(s.text)}\n'
                  + (f'<page-footer>\n{inert(s.footer)}\n</page-footer>\n' if footer and s.footer else "") + '</source>'
                  for s in sources]
        body = "\n".join(blocks) if blocks else "none"
    else:
        body = NO_SOURCES[condition]
    identity = inert(staged.identity) or "none"
    return (f"<proposal>\n{proposal}\n</proposal>\n\n<signals>\n{signals}\n</signals>\n\n{gate_block}"
            f"<identity>\n{identity}\n</identity>\n\n"
            f"<existing>\n{inert(staged.existing) or 'none'}\n</existing>\n\n<sources>\n{body}\n</sources>\n")


def load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def api_schema(schema: dict) -> dict:
    """The schema as the API takes it: references inlined, metadata dropped, and a type next to
    every enum of strings. The file itself stays as it is."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            out = {k: walk(v) for k, v in node.items() if k not in ("$schema", "$id", "title", "$defs")}
            if "enum" in out and "type" not in out and all(isinstance(v, str) for v in out["enum"]):
                out["type"] = "string"
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


# What structured outputs take in a schema (output_config.format, the Claude API docs, "JSON Schema
# limitations"): no recursion, no numerical or string constraints, no count of items beyond
# minItems 0 or 1, and additionalProperties only false. A schema with anything else is refused with
# a 400 before anything is paid; format_problems finds it before the request is sent.
FORMAT_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "anyOf",
                   "allOf", "$ref", "$defs", "description", "title", "format", "minItems", "default"}
FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}


def format_problems(schema: dict, where: str = "") -> list[str]:
    """Why structured outputs would refuse this schema (as sent: after api_schema), or nothing."""
    problems = []
    if isinstance(schema, list):
        for i, v in enumerate(schema):
            problems += format_problems(v, f"{where}/{i}")
        return problems
    if not isinstance(schema, dict):
        return problems
    for k, v in schema.items():
        here = f"{where}/{k}"
        if k == "properties":
            for name, sub in v.items():
                problems += format_problems(sub, f"{here}/{name}")
            continue
        if k not in FORMAT_KEYWORDS:
            problems.append(f"{here}: {k} is not supported")
        elif k == "minItems" and v not in (0, 1):
            problems.append(f"{here}: minItems may only be 0 or 1")
        elif k == "additionalProperties" and v is not False:
            problems.append(f"{here}: additionalProperties must be false")
        elif k == "format" and v not in FORMATS:
            problems.append(f"{here}: format {v!r} is not supported")
        elif isinstance(v, (dict, list)) and k not in ("enum", "const", "default", "required"):
            problems += format_problems(v, here)
    if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
        problems.append(f"{where or '/'}: an object needs additionalProperties: false")
    return problems


def schema_for(schema: dict, rules) -> dict:
    """The verdict schema with only the rules the referee is asked; the others the gate settled."""
    out = copy.deepcopy(schema)
    drop = [r for r in RULES if r not in rules]
    for r in drop:
        out["properties"].pop(r, None)
    out["required"] = [k for k in out["required"] if k not in drop]
    return out


# --------------------------------------------------------------------------- the outcome (referee/prompt.md § Outcome)

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def product_gone(files: dict, sources: list[Source]) -> str:
    """The product's own source answers 404 or 410: a reason to reject on TP1, or ''."""
    _, data = product_file(files)
    url = data.get("source")
    for s in sources:
        if s.url == url and s.status in ("404", "410"):
            return f"the product source {url} answers {s.status}"
    return ""


def versions_url(files: dict) -> str:
    """The versions source of the proposed product, or ''."""
    _, data = product_file(files)
    versions = data.get("versions") if isinstance(data, dict) else None
    return str(versions.get("source") or "") if isinstance(versions, dict) else ""


FOOTER_RULES = ("TP5", "TV1")  # TAX-43: the rules whose quote may come from the footer of a page


def publisher_names(publisher: dict) -> list[str]:
    """The name of a publisher file, and that name without its legal form (TAX-43)."""
    name = publisher.get("name") if isinstance(publisher, dict) else None
    if not isinstance(name, str) or not name.strip():
        return []
    stripped = keyrule.strip_legal_form(name).strip(" ,.")
    return [name] + ([stripped] if stripped and stripped != keyrule.fold(name) else [])


def check_quotes(verdict: dict, sources: list[Source], rules=RULES, versions: str = "",
                 files: dict | None = None, footer: bool = False, names=()) -> tuple[dict, list[str]]:
    """A yes whose quotes are not all found in the fetched text of their URL becomes doubt.
    A yes without any quote becomes doubt too. Returns the checked verdict and what changed.
    Only `rules` are checked: those the referee answered, not those the gate settled.
    TP3 is yes only with a version quoted from the text of the versions source `versions` (or of a
    source found instead of it): news about a service is not a version (TP4).
    With `footer` (TAX-43, the referee was shown it) a quote for TP5 or TV1 may also stand in the
    footer of its source; for any other rule only in its text. A footer names others too (a hoster,
    the maker of a theme), so a TP5 yes that rests on a footer quote holds only when that quote
    contains the publisher's name, one of `names`."""
    texts = {s.url: _norm(s.text) for s in sources}
    feet = {s.url: _norm(s.footer) for s in sources if footer and s.footer}

    def where(quote: str, url: str, rule: str) -> str:
        q = _norm(quote)
        if q in texts.get(url, ""):
            return "text"
        return "footer" if rule in FOOTER_RULES and q in feet.get(url, "") else ""

    def found(quote: str, url: str, rule: str) -> bool:
        return bool(where(quote, url, rule))

    proposal = _norm("\n".join((files or {}).values()))
    checked, notes = copy.deepcopy(verdict), []
    for rule in rules:
        j = checked[rule]
        if rule == "TV1":
            # TV1 is "no" when a text addresses the referee: that "no" quotes the text, and code finds
            # it, or it is doubt. A "yes" (no such text) has nothing to quote.
            if j["verdict"] == "no" and not any(found(ev["quote"], ev["url"], rule) or
                                                _norm(ev["quote"]) in proposal for ev in j["evidence"]):
                j["verdict"] = "doubt"
                notes.append("TV1: no without a quote found in a source or the proposal")
            continue
        if j["verdict"] != "yes":
            continue
        if not j["evidence"]:
            j["verdict"] = "doubt"
            notes.append(f"{rule}: yes without a quote")
            continue
        for ev in j["evidence"]:
            at = where(ev["quote"], ev["url"], rule)
            if not at:
                j["verdict"] = "doubt"
                notes.append(f"{rule}: quote not found at {ev['url']}")
                break
            if at == "footer" and not any(gate.appears(n, ev["quote"]) for n in names):
                j["verdict"] = "doubt"
                notes.append(f"{rule}: a quote from the footer of {ev['url']} that does not name the publisher")
                break
    tr4 = checked.get("TP3")
    if "TP3" in rules and tr4 and tr4["verdict"] == "yes":
        version = _norm(tr4.get("version") or "")
        own = [_norm(s.text) for s in sources if versions and versions in (s.url, s.instead_of)]
        if not version:
            tr4["verdict"] = "doubt"
            notes.append("TP3: yes without a version from the versions source")
        elif not any(version in text for text in own):
            tr4["verdict"] = "doubt"
            notes.append(f"TP3: version not found at {versions or 'the versions source'}")
    return checked, notes


def outcome(verdict: dict) -> tuple[str, str]:
    """(outcome, the first rule that is no)."""
    noes = [r for r in RULES if verdict[r]["verdict"] == "no"]
    if noes:
        return ("alias" if verdict.get("duplicate_of") else "reject"), noes[0]
    if any(verdict[r]["verdict"] == "doubt" for r in RULES):
        return "parked", ""
    return "accept", ""


def parse_reply(text: str, schema: dict) -> tuple[dict | None, str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"the reply is not JSON: {exc}"
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(data))
    if errors:
        return None, f"the reply does not fit the schema: {errors[0].message}"
    return data, ""


# --------------------------------------------------------------------------- the API

@dataclass
class Response:
    content: list
    stop_reason: str
    usage: dict


class AnthropicClient:
    """The Claude API. The key comes from ANTHROPIC_API_KEY in the environment; the harness never
    reads, writes or logs it."""

    def __init__(self):
        import anthropic  # only needed for a live run

        self._client = anthropic.Anthropic()

    def count(self, request: dict) -> int:
        """Input tokens of a request without server tools. Free (token counting has no charge)."""
        fields = {k: v for k, v in request.items() if k in ("model", "system", "messages", "output_config")}
        return self._client.messages.count_tokens(**fields).input_tokens

    def create(self, request: dict) -> Response:
        response = self._client.messages.create(**request)
        usage = empty_usage()
        add_usage(usage, response.usage)
        return Response(content=list(response.content), stop_reason=response.stop_reason, usage=usage)

    # The Message Batches API: the same requests, answered within 24 hours, at batch_discount of the price.

    def submit(self, requests: list) -> str:
        """requests: [(custom_id, request)]. Returns the id of the batch."""
        batch = self._client.messages.batches.create(
            requests=[{"custom_id": cid, "params": request} for cid, request in requests])
        return batch.id

    def status(self, batch_id: str) -> str:
        return self._client.messages.batches.retrieve(batch_id).processing_status

    def cancel(self, batch_id: str) -> None:
        self._client.messages.batches.cancel(batch_id)

    def results(self, batch_id: str):
        """(custom_id, Response) for a request that succeeded; (custom_id, why) for one that did not."""
        for item in self._client.messages.batches.results(batch_id):
            if item.result.type == "succeeded":
                msg = item.result.message
                usage = empty_usage()
                add_usage(usage, msg.usage)
                yield item.custom_id, Response(content=list(msg.content), stop_reason=msg.stop_reason, usage=usage)
            else:
                error = getattr(getattr(item.result, "error", None), "type", "") or ""
                yield item.custom_id, f"{item.result.type}{': ' + error if error else ''}"


def empty_usage() -> dict:
    return {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0,
            "web_search_requests": 0, "web_fetch_requests": 0}


def add_usage(total: dict, usage) -> None:
    for name in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
        total[name] += getattr(usage, name, 0) or 0
    server = getattr(usage, "server_tool_use", None)
    total["web_search_requests"] += (getattr(server, "web_search_requests", 0) or 0) if server else 0
    total["web_fetch_requests"] += (getattr(server, "web_fetch_requests", 0) or 0) if server else 0


def final_text(content) -> str:
    """The text after the last tool block: with web search, earlier text is talk between searches."""
    blocks = list(content)
    last_tool = max((i for i, b in enumerate(blocks) if b.type != "text"), default=-1)
    return "".join(b.text for b in blocks[last_tool + 1:] if b.type == "text")


# --------------------------------------------------------------------------- prices and the budget

def load_prices(path: Path = REFEREE / "prices.yaml") -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def discount(prices: dict, batch: bool) -> float:
    """What a token costs as part of a batch, as a share of the normal price (prices.yaml)."""
    if not batch:
        return 1.0
    if "batch_discount" not in prices:
        raise SystemExit("No batch_discount in referee/prices.yaml")
    return float(prices["batch_discount"])


def cost(usage: dict, model: str, prices: dict, batch: bool = False) -> float:
    if model not in prices["models"]:
        raise SystemExit(f"No price for model {model!r} in referee/prices.yaml")
    p = prices["models"][model]
    total = 0.0
    for tokens, price in (("input_tokens", "input"), ("cache_creation_input_tokens", "cache_write_5m"),
                          ("cache_read_input_tokens", "cache_read"), ("output_tokens", "output")):
        if usage.get(tokens):
            if price not in p:
                raise SystemExit(f"No {price} price for {model!r} in referee/prices.yaml")
            total += usage[tokens] * p[price] / 1_000_000
    total *= discount(prices, batch)
    total += usage.get("web_search_requests", 0) * prices["web_search_per_1000"] / 1000
    return total


def input_price(model: str, prices: dict, batch: bool = False) -> float:
    p = prices["models"][model]
    return max(p["input"], p.get("cache_write_5m", 0)) / 1_000_000 * discount(prices, batch)


def output_price(model: str, prices: dict, batch: bool = False) -> float:
    return prices["models"][model]["output"] / 1_000_000 * discount(prices, batch)


def web_allowance(model: str, prices: dict, max_uses: int, fetches: int = 0) -> float:
    """What the web tools may add to one request: the tool definitions, the search results and the
    fetched pages as input, and the searches. A fetch has no fee of its own."""
    return ((WEB_TOOL_OVERHEAD + (max_uses + fetches) * WEB_ALLOWANCE) * input_price(model, prices)
            + max_uses * prices["web_search_per_1000"] / 1000)


class BudgetExhausted(Exception):
    pass


class Budget:
    """A hard limit on spending. Before each request it fixes max_tokens so that the request
    cannot cost more than what is left; after each request it adds what the API reports."""

    def __init__(self, limit: float, spent: float, model: str, prices: dict, max_tokens: int = MAX_TOKENS,
                 batch: bool = False):
        self.limit, self.spent, self.model, self.prices, self.max_tokens = limit, spent, model, prices, max_tokens
        self.batch = batch  # a batch costs batch_discount of the normal price (prices.yaml)

    @property
    def left(self) -> float:
        return self.limit - self.spent

    def fit(self, input_tokens: int, extra: float = 0.0) -> int:
        """max_tokens for a request with this many input tokens, or BudgetExhausted.
        input_tokens gets 5% and 100 tokens of margin, since a count is an estimate."""
        fixed = (input_tokens * 1.05 + 100) * input_price(self.model, self.prices, self.batch) + extra
        room = int((self.left - fixed) / output_price(self.model, self.prices, self.batch))
        if room < MIN_OUTPUT:
            raise BudgetExhausted(
                f"${self.spent:.4f} of ${self.limit:.2f} spent; a request with {input_tokens} input tokens "
                f"and {MIN_OUTPUT} output tokens could cost more than the ${self.left:.4f} left")
        return min(self.max_tokens, room)

    def add(self, usage: dict) -> float:
        c = cost(usage, self.model, self.prices, self.batch)
        self.spent += c
        return c


def review(client, budget: Budget, *, model: str, system: str, message: str, web: bool, effort: str,
           max_uses: int, schema: dict, largest_web: float = 0.0) -> tuple[str, str, dict]:
    """One proposal: one request, resumed at most three times if the web search loop pauses.
    Every request is fitted into the budget first. Returns (text, stop reason, usage)."""
    request = dict(
        model=model,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": message}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if web:
        # Condition W of TE4: web search, and web fetch to read the pages it finds and the URLs of the
        # proposal (run A2 review §3). Fetched pages are capped, like the harness caps a source.
        request["tools"] = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_uses, "max_content_tokens": WEB_ALLOWANCE},
        ]
    usage = empty_usage()
    for _ in range(4):
        # Token counting does not take server tools, so the web tools get an allowance,
        # or twice the largest web request so far if that is more.
        extra = max(web_allowance(model, budget.prices, max_uses, max_uses), 2 * largest_web) if web else 0.0
        request["max_tokens"] = budget.fit(client.count(request), extra)
        response = client.create(request)
        for k in usage:
            usage[k] += response.usage.get(k, 0)
        budget.add(response.usage)
        if response.stop_reason != "pause_turn":
            break
        request["messages"] = request["messages"] + [{"role": "assistant", "content": response.content}]
    return final_text(response.content), response.stop_reason, usage


# --------------------------------------------------------------------------- a batch

BATCH_POLL = 30  # seconds between two looks at a batch
BATCH_WAIT = 5 * 3600  # seconds to wait for a batch before it is cancelled (a job may run at most 6 hours)


def fit_batch(client, budget: Budget, requests: list) -> tuple[list, list]:
    """The requests of one batch that fit what is left in the budget, at their worst: every input token
    counted (free) with 5% and 100 tokens of margin, and every request its full max_tokens
    (budget.max_tokens). As many as fit go, in order; when not even one fits at full max_tokens, one
    goes with what is left, if that is at least MIN_OUTPUT. Returns (the requests that fit, with
    max_tokens; the requests left out, without)."""
    p_in = input_price(budget.model, budget.prices, budget.batch)
    p_out = output_price(budget.model, budget.prices, budget.batch)
    fitted, total = [], 0.0
    for n, (cid, request) in enumerate(requests):
        worst = (client.count(request) * 1.05 + 100) * p_in
        if total + worst + budget.max_tokens * p_out <= budget.left:
            total += worst + budget.max_tokens * p_out
            fitted.append((cid, {**request, "max_tokens": budget.max_tokens}))
            continue
        if not fitted:
            room = int((budget.left - worst) / p_out)
            if room >= MIN_OUTPUT:
                fitted.append((cid, {**request, "max_tokens": room}))
                n += 1
        return fitted, requests[n:]
    return fitted, []


CUSTOM_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}")  # what the Message Batches API takes as a custom_id


def id_problems(requests: list) -> list[str]:
    """Why the Message Batches API would refuse the custom ids of these requests (a 400 for the whole
    batch, after the batches before it were paid), or nothing: each must match CUSTOM_ID, and be unique."""
    problems, seen = [], set()
    for n, (cid, _) in enumerate(requests):
        if not isinstance(cid, str) or not CUSTOM_ID.fullmatch(cid):
            problems.append(f"request {n}: custom_id {str(cid)[:80]!r} does not match ^[a-zA-Z0-9_-]{{1,64}}$")
        elif cid in seen:
            problems.append(f"request {n}: custom_id {cid!r} twice")
        seen.add(cid)
    return problems


def batch_rounds(client, budget: Budget, requests: list, log=print, wait: float = BATCH_WAIT,
                 poll: float = BATCH_POLL, sleep=None) -> tuple[dict, list]:
    """As many batches as it takes: each holds what fits the budget at its worst, and once it has
    ended, what it really cost frees the rest for the next. Stops when nothing fits or `wait` is used
    up. Returns (custom_id -> answer, as run_batch; the custom ids that never fitted). Every custom id
    is checked before anything is sent: one the API refuses would stop a later round."""
    import time

    if wrong := id_problems(requests):
        raise SystemExit("custom ids the Message Batches API refuses, nothing sent: " + "; ".join(wrong[:5]))
    answers, left, started = {}, list(requests), time.monotonic()
    while left:
        fitted, left = fit_batch(client, budget, left)
        if not fitted:
            break
        log(f"round of {len(fitted)} requests, max_tokens {fitted[0][1]['max_tokens']}; {len(left)} wait for the next")
        remaining = max(wait - (time.monotonic() - started), poll)
        answers.update(run_batch(client, budget, fitted, log=log, wait=remaining, poll=poll, sleep=sleep))
        if time.monotonic() - started >= wait:
            break
    return answers, [cid for cid, _ in left]


def run_batch(client, budget: Budget, requests: list, log=print, wait: float = BATCH_WAIT,
              poll: float = BATCH_POLL, sleep=None) -> dict:
    """Send fitted requests as one batch, wait for it, and add what each answer cost to the budget.
    A batch that is not done within `wait` seconds is cancelled; what was answered by then counts.
    Returns custom_id -> {"text", "stop", "usage", "cost", "error"}."""
    import time

    sleep = sleep or time.sleep
    batch_id = client.submit(requests)
    log(f"batch {batch_id}: {len(requests)} requests")
    waited, cancelled = 0.0, False
    while (status := client.status(batch_id)) != "ended":
        if waited >= wait and not cancelled:
            log(f"batch {batch_id} is not done after {waited / 60:.0f} minutes ({status}): cancelled")
            client.cancel(batch_id)
            cancelled = True
        sleep(poll)
        waited += poll
    out = {cid: {"text": "", "stop": "", "usage": empty_usage(), "cost": 0.0, "error": "no result in the batch"}
           for cid, _ in requests}
    for cid, answer in client.results(batch_id):
        if isinstance(answer, Response):
            c = budget.add(answer.usage)
            out[cid] = {"text": final_text(answer.content), "stop": answer.stop_reason, "usage": answer.usage,
                        "cost": c, "error": ""}
        else:
            out[cid]["error"] = f"the batch did not answer: {answer}"
    log(f"batch {batch_id} ended: ${sum(r['cost'] for r in out.values()):.4f}; "
        f"spent ${budget.spent:.4f} of ${budget.limit:.2f}")
    return out


# --------------------------------------------------------------------------- a run

@dataclass
class Result:
    id: str
    type: str
    expect: list
    outcome: str  # accept, reject, alias, parked; "" if the case did not run
    by: str  # code, ai, error or invalid (the lint does not match the case)
    right: bool = False
    rule: str = ""  # the rule the outcome rests on
    rules: list = field(default_factory=list)  # the rules of a bad case
    alias_of: str = ""
    duplicate_of: str | None = None
    lint: str = ""
    verdict: dict = field(default_factory=dict)  # the reply as given
    checked: dict = field(default_factory=dict)  # rule -> verdict after the quote check
    quote_notes: list = field(default_factory=list)
    sources: list = field(default_factory=list)  # [url, status]
    error: str = ""
    usage: dict = field(default_factory=dict)
    cost: float = 0.0
    gate: dict = field(default_factory=dict)  # rule -> verdict, for the rules the gate settled (tools/gate.py)
    ai_rules: list = field(default_factory=list)  # the rules the referee was asked
    held: dict = field(default_factory=dict)  # rule -> reason: held for a person by code (TV3, TV5)


def spent_before(out: Path) -> float:
    total = 0.0
    for path in out.rglob("results.jsonl"):  # nested too: one artifact may hold several runs
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                total += json.loads(line).get("cost", 0.0)
    return total


def largest_before(out: Path, model: str, condition: str) -> float:
    """The most one case has cost in earlier runs with this model and condition."""
    largest = 0.0
    for path in out.glob("*/results.jsonl"):
        meta = path.parent / "run.json"
        if not meta.is_file():
            continue
        info = json.loads(meta.read_text(encoding="utf-8"))
        if (info.get("model"), info.get("condition")) != (model, condition):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                largest = max(largest, json.loads(line).get("cost", 0.0))
    return largest


def ceiling(chars: int, model: str, prices: dict, web: bool, max_uses: int, max_tokens: int) -> float:
    """For a dry run, without calling the API: every character a token, the full output used."""
    total = chars * input_price(model, prices) + max_tokens * output_price(model, prices)
    return total + (web_allowance(model, prices, max_uses) if web else 0.0)


def run(args, client=None, fetcher=None, data_root: Path = REPO, now=None, log=print) -> tuple[list[Result], Path | None]:
    cases = select(load_cases(Path(args.testset)), args.cases, args.condition)
    if args.limit:
        cases = cases[: args.limit]
    prompt = Path(args.prompt)
    if args.condition != "fetched" and prompt == REFEREE / "prompt.md":
        # TE4: memory and web judge without fetched texts, so they get their own system prompt.
        prompt = TE4_PROMPT
    if not prompt.is_file():
        raise SystemExit(f"No prompt at {prompt}")
    footer = footer_counts() and args.condition == "fetched"  # TAX-43
    system = load_system_prompt(prompt, footer)
    log(f"prompt: {prompt}" + (" (with the page footer, TAX-43)" if footer else ""))
    schema = load_schema(Path(args.schema))
    prices = load_prices(Path(args.prices))
    if args.model not in prices["models"]:
        raise SystemExit(f"No price for model {args.model!r} in {args.prices}")
    web = args.condition == "web"
    batch = bool(getattr(args, "batch", False))
    if batch and web:
        raise SystemExit("A batch cannot run condition web: its search loop needs a turn per pause")
    out = Path(args.out)
    before = spent_before(out)

    use_gate = args.condition == "fetched"  # TE4 measures the AI alone, so memory and web have no gate
    stager = Stager(data_root)
    try:
        staged = {c.id: stager.stage(c) for c in cases}
    finally:
        stager.close()
    invalid = [c.id for c in cases if not staged[c.id].lint_ok]
    to_ai = [c for c in cases if staged[c.id].lint_ok and not staged[c.id].lint_seen.startswith("error")]
    log(f"{len(cases)} cases, condition {args.condition}: {len(cases) - len(to_ai) - len(invalid)} stop at the lint, "
        f"{len(to_ai)} for the referee" + (f", {len(invalid)} whose lint does not match the case: {', '.join(invalid)}" if invalid else ""))
    log(f"model {args.model}, effort {args.effort}, max_tokens {args.max_tokens}")
    log(f"budget: ${args.budget:.2f} for all runs in {out}; spent before this run: ${before:.4f}")

    if not args.live:
        top = sum(ceiling(len(system) + len(staged[c.id].existing) + sum(map(len, staged[c.id].files.values()))
                          + (len(urls_of(staged[c.id].files)) * SOURCE_CHARS if args.condition == "fetched" else 0),
                          args.model, prices, web, args.max_uses, args.max_tokens) for c in to_ai)
        log(f"most this run could cost: ${top:.2f} (a ceiling, not an estimate)")
        dry_dir = None
        if args.condition == "fetched":
            stored = None
            if getattr(args, "sources", None):
                stored = StoredFetcher(json.loads(Path(args.sources).read_text(encoding="utf-8")))
                log(f"sources: {args.sources} ({len(stored.cache)} URLs, fetched by an earlier step)")
            else:
                log("No --sources: the gate reads no source texts, so TK2 and TE1 go to the referee.")
            replay = {}
            if getattr(args, "replay", None):
                replay = {r.id: r for r in load_results(Path(args.replay))}
                log(f"replay: the answers of {args.replay}, with the gate in front")
            rows = [gate_row(c, staged[c.id], stored, replay.get(c.id)) for c in cases]
            stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
            dry_dir = out / f"{stamp}-dry-{args.condition}"
            dry_dir.mkdir(parents=True, exist_ok=False)
            text = gate_summary(rows, dry_dir.name, args.sources if stored else "", getattr(args, "replay", "") or "")
            (dry_dir / "summary.md").write_text(text, encoding="utf-8")
            log(text)
        log("Dry run: nothing was sent. Add --live to call the API.")
        return [], dry_dir
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("A live run needs ANTHROPIC_API_KEY")
        client = AnthropicClient()
    if fetcher is None:
        if getattr(args, "sources", None):
            fetcher = StoredFetcher(json.loads(Path(args.sources).read_text(encoding="utf-8")))
            log(f"sources: {args.sources} ({len(fetcher.cache)} URLs, fetched by an earlier step)")
        else:
            fetcher = Fetcher(log=log)

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    # A batch run carries its effort in its name: the measurement compares stands of one model.
    run_dir = out / (f"{stamp}-{args.condition}-{args.model}" + (f"-{args.effort}" if batch else ""))
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "run.json").write_text(json.dumps({
        "condition": args.condition, "model": args.model, "effort": args.effort, "max_tokens": args.max_tokens,
        "max_uses": args.max_uses, "budget": args.budget, "spent_before": round(before, 6),
        "cases": [c.id for c in cases], "testset": str(args.testset), "prompt": str(prompt), "started": stamp,
        "batch": batch,
    }, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
            gh.write(f"run_dir={run_dir}\nrun_name={run_dir.name}\n")

    api = api_schema(schema)
    budget = Budget(args.budget, before, args.model, prices, args.max_tokens, batch=batch)
    largest_web = largest_before(out, args.model, args.condition) if web else 0.0
    results, stopped, fetched = [], "", {}
    waiting = []  # batch: (result, case, sources, decided, message), sent together after the loop
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as jsonl:
        for case in cases:
            st = staged[case.id]
            res = Result(id=case.id, type=case.type, expect=case.expect, outcome="", by="", rules=case.rules,
                         alias_of=case.alias_of, lint=st.lint_seen)
            if not st.lint_ok:
                res.by, res.error = "invalid", f"the lint gives {st.lint_seen}, the case says {case.lint}"
            elif st.lint_seen.startswith("error"):
                res.by, res.outcome, res.rule = "code", "reject", st.lint_seen.split()[1]
            elif use_gate and gate.ai_chat(st.product, st.publisher):
                # A source on the domain of an AI chat: rejected on TE1 by code, and nothing is fetched.
                chat = gate.ai_chat(st.product, st.publisher)
                res.by, res.outcome, res.rule, res.error, res.gate = "code", "reject", "TE1", chat["reason"], {"TE1": "no"}
            else:
                sources = gather_sources(fetcher, st) if args.condition == "fetched" else []
                for s in sources:
                    fetched[source_key(s.url, s.product) + (f" | for {s.instead_of}" if s.instead_of else "")] = asdict(s)
                res.sources = [[s.url, s.status] for s in sources]
                gone = product_gone(st.files, sources)
                wrong = second_source_fault(st, sources if args.condition == "fetched" else None)
                swap = replacement_fault(st, fetcher if args.condition == "fetched" else None) if st.old else ""
                if gone or wrong or swap:
                    # The page that should show the product does not exist (TP1), a second source that
                    # is given does not pass (TV4), or a change replaces (TV5): rejected by code.
                    rule = "TP1" if gone else "TV4" if wrong else "TV5"
                    res.by, res.outcome, res.rule, res.error = "code", "reject", rule, gone or wrong or swap
                    res.right = res.outcome in case.expect
                    log(f"{case.id}: reject (code, {gone or wrong or swap}), $0.0000; spent ${budget.spent:.4f} of ${args.budget:.2f}")
                    results.append(res)
                    jsonl.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
                    jsonl.flush()
                    continue
                decided = gate.decide(st.product, st.publisher, sources, not st.known_publisher) if use_gate else None
                res.gate = {r: j["verdict"] for r, j in (decided or {}).items()}
                res.ai_rules = [r for r in RULES if r not in res.gate]
                res.held = hold(st, sources if args.condition == "fetched" else None)
                if batch:
                    waiting.append((res, case, sources, decided, build_message(st, args.condition, sources, decided, footer)))
                    continue
                spent_at_start = budget.spent
                try:
                    text, stop, usage = review(client, budget, model=args.model, system=system,
                                               message=build_message(st, args.condition, sources, decided, footer), web=web,
                                               effort=args.effort, max_uses=args.max_uses,
                                               schema=schema_for(api, res.ai_rules), largest_web=largest_web)
                except BudgetExhausted as exc:
                    res.cost = budget.spent - spent_at_start  # a resumed request may have run before the stop
                    if res.cost:
                        res.by, res.outcome, res.error = "error", "parked", f"stopped by the budget: {exc}"
                        res.right = res.outcome in case.expect
                        results.append(res)
                        jsonl.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
                    stopped = f"Stopped before {case.id}: {exc}. {len(cases) - len(results)} cases did not run."
                    log(stopped)
                    break
                except Exception as exc:  # noqa: BLE001 - any failure is recorded, the run goes on
                    res.by, res.outcome, res.error = "error", "parked", f"{type(exc).__name__}: {exc}"
                    res.cost = budget.spent - spent_at_start
                else:
                    res.usage, res.cost = usage, budget.spent - spent_at_start
                    if web:
                        largest_web = max(largest_web, res.cost)
                    settle_reply(res, text, stop, schema, sources, decided, args.condition, versions_url(st.files), st.files, footer,
                                 publisher_names(st.publisher))
            res.right = res.outcome in case.expect
            log(f"{case.id}: {res.outcome or '-'} ({res.by}){'' if res.right else '  WRONG'}, ${res.cost:.4f}; "
                f"spent ${budget.spent:.4f} of ${args.budget:.2f}")
            results.append(res)
            jsonl.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
            jsonl.flush()

        if waiting:
            # One batch (or as many rounds as the budget asks) for every case that reached the referee.
            requests = [(f"c{n}", dict(
                model=args.model,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": message}],
                output_config={"effort": args.effort, "format": {"type": "json_schema", "schema": schema_for(api, res.ai_rules)}},
            )) for n, (res, _, _, _, message) in enumerate(waiting)]
            answers, never = batch_rounds(client, budget, requests, log=log, wait=getattr(args, "wait", BATCH_WAIT),
                                          sleep=getattr(args, "sleep", None))
            for n, (res, case, sources, decided, _) in enumerate(waiting):
                answer = answers.get(f"c{n}")
                if answer is None:
                    continue  # never fitted the budget: did not run
                res.usage, res.cost = answer["usage"], answer["cost"]
                if answer["error"]:
                    res.by, res.outcome, res.error = "error", "parked", answer["error"]
                else:
                    settle_reply(res, answer["text"], answer["stop"], schema, sources, decided, args.condition,
                                 versions_url(staged[case.id].files), staged[case.id].files, footer,
                                 publisher_names(staged[case.id].publisher))
                res.right = res.outcome in case.expect
                log(f"{case.id}: {res.outcome or '-'} ({res.by}){'' if res.right else '  WRONG'}, ${res.cost:.4f}")
                results.append(res)
                jsonl.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
            if never:
                stopped = (f"Stopped by the budget: {len(never)} cases did not fit "
                           f"(${budget.spent:.4f} of ${args.budget:.2f} spent).")
                log(stopped)
            order = {c.id: n for n, c in enumerate(cases)}
            results.sort(key=lambda r: order[r.id])

    (run_dir / "sources.json").write_text(json.dumps(fetched, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if hasattr(fetcher, "close"):
        fetcher.close()
    summary = summarise(results, run_dir.name, args.condition, args.budget, before, stopped)
    (run_dir / "summary.md").write_text(summary, encoding="utf-8")
    log(summary)
    return results, run_dir


def settle_reply(res: "Result", text: str, stop: str, schema: dict, sources: list, decided: dict, condition: str,
                 versions: str = "", files: dict | None = None, footer: bool = False, names=()) -> None:
    """The outcome of one reply: parsed against the schema of the rules asked, quotes checked, and
    the rules the gate settled added as the gate settled them."""
    verdict, why = (parse_reply(text, schema_for(schema, res.ai_rules)) if stop != "refusal"
                    else (None, "the model refused"))
    if verdict is None:
        res.by, res.outcome, res.error = "error", "parked", why
        return
    res.by, res.verdict, res.duplicate_of = "ai", verdict, verdict.get("duplicate_of")
    # The quote check is the code part of TE1; TE4 switches it off in memory and web.
    checked, res.quote_notes = (check_quotes(verdict, sources, res.ai_rules, versions, files, footer, names) if condition == "fetched"
                                else (verdict, []))
    checked = {**checked, **(decided or {})}  # the rules the gate settled, as the gate settled them
    res.checked = {r: checked[r]["verdict"] for r in RULES}
    res.outcome, res.rule = held_outcome(*outcome(checked), res.held)


# --------------------------------------------------------------------------- scoring (README § Scoring, r13-research.md)

def metrics(results: list[Result]) -> dict:
    ran = [r for r in results if r.outcome]
    good = [r for r in ran if r.id.startswith("G")]
    bad = [r for r in ran if r.id.startswith("B")]
    called = [r for r in results if r.usage]
    right_rule = {}  # on a bad case: did one of its rules say no (or the lint rule stop it)?
    for r in bad:
        for rule in r.rules:
            hit = (r.by == "code" and r.rule == rule) or r.checked.get(rule) == "no" or rule in r.held
            right_rule.setdefault(rule, [0, 0])
            right_rule[rule][0] += 1
            right_rule[rule][1] += hit
    per_type = {}
    for r in ran:
        t = per_type.setdefault(r.type, [0, 0])
        t[0] += 1
        t[1] += r.right
    return {
        "cases": len(results),
        "ran": len(ran),
        "right": sum(r.right for r in ran),
        "let_through": [r.id for r in bad if r.outcome == "accept"],
        "good_rejected": [r.id for r in good if r.outcome in ("reject", "alias")],
        "parked": [r.id for r in ran if r.outcome == "parked"],
        "parked_closed": [r.id for r in ran if r.outcome == "parked" and source_closed(r)],
        "parked_doubt": [r.id for r in ran if r.outcome == "parked" and not source_closed(r)],
        "wrong": [r.id for r in ran if not r.right],
        "invalid": [r.id for r in results if r.by == "invalid"],
        "errors": [r.id for r in results if r.by == "error"],
        "by_code": sum(r.by == "code" for r in results),
        "calls": len(called),
        "cost_total": sum(r.cost for r in results),
        "cost_per_call": (sum(r.cost for r in called) / len(called)) if called else 0.0,
        "cost_per_call_max": max((r.cost for r in called), default=0.0),
        "tokens": {k: sum(r.usage.get(k, 0) for r in results) for k in empty_usage()},
        "right_rule": right_rule,
        "per_type": per_type,
        "te4": te4_metrics(results),
    }


def source_closed(r: Result) -> bool:
    """A source of the case was closed: refused, disallowed, an error such as a timeout, or not fetched."""
    return any(status in CLOSED or status.startswith("error") or status == "not fetched" for _, status in r.sources)


def te4_metrics(results: list[Result]) -> dict:
    """Per rule: right, judged and doubt; and the confident-and-wrong answers (r13-research.md)."""
    judged = [r for r in results if r.by == "ai"]
    per_rule, confident_wrong = {}, []
    for rule in TE4_RULES:
        right = wrong = doubt = 0
        for r in judged:
            v = r.checked.get(rule)
            if r.id.startswith("G"):
                truth = "yes"
            elif r.id.startswith("B") and rule in r.rules:
                truth = "no"
            else:
                continue
            if v == "doubt":
                doubt += 1
            elif v == truth:
                right += 1
            else:
                wrong += 1
                confident_wrong.append(f"{r.id} {rule}")
        per_rule[rule] = {"right": right, "wrong": wrong, "doubt": doubt}
    return {"per_rule": per_rule, "confident_wrong": confident_wrong}


def summarise(results: list[Result], name: str, condition: str, budget: float, before: float, stopped: str = "") -> str:
    m = metrics(results)
    ids = lambda xs: ", ".join(xs) or "none"  # noqa: E731
    lines = [
        f"# Referee run {name}",
        "",
        f"Condition **{condition}**. {m['cases']} cases: {m['by_code']} stopped by code (lint, 404 or the gate), {m['calls']} sent to the referee.",
        "",
        *([f"**{stopped}**", ""] if stopped else []),
        "| measure | value |",
        "| --- | --- |",
        f"| bad cases let through (target 0) | {len(m['let_through'])} |",
        f"| good cases rejected | {len(m['good_rejected'])} |",
        f"| parked (cases for a person or a better proposal) | {len(m['parked'])} |",
        f"| parked because a source is closed (refused, disallowed, error) | {len(m['parked_closed'])} |",
        f"| parked because the referee doubts | {len(m['parked_doubt'])} |",
        f"| outcome in `expect` | {m['right']} of {m['ran']} |",
        f"| failed calls or unusable replies | {len(m['errors'])} |",
        f"| cases whose lint does not match | {len(m['invalid'])} |",
        f"| cost per case sent to the referee | ${m['cost_per_call']:.4f} (max ${m['cost_per_call_max']:.4f}) |",
        f"| cost of this run | ${m['cost_total']:.4f} |",
        f"| spent on all runs, budget | ${before + m['cost_total']:.4f} of ${budget:.2f} |",
        "",
        f"Let through: {ids(m['let_through'])}  ",
        f"Good rejected: {ids(m['good_rejected'])}  ",
        f"Parked, a source closed: {ids(m['parked_closed'])}  ",
        f"Parked, the referee doubts: {ids(m['parked_doubt'])}  ",
        f"Not in `expect`: {ids(m['wrong'])}  ",
        f"Errors: {ids(m['errors'])}  ",
        f"Lint does not match: {ids(m['invalid'])}",
        "",
        "## On a bad case, did the right rule say no?",
        "",
        "| rule | bad cases with this rule | said no |",
        "| --- | --- | --- |",
        *[f"| {rule} | {n} | {hit} |" for rule, (n, hit) in sorted(m["right_rule"].items())],
        "",
        "## Per type",
        "",
        "| type | cases | outcome in `expect` |",
        "| --- | --- | --- |",
        *[f"| {t} | {n} | {ok} |" for t, (n, ok) in sorted(m["per_type"].items())],
        "",
        "## Per rule (TE4)",
        "",
        "Right: a good case answered yes, or a bad case answered no on one of its rules.",
        "",
        "| rule | right | wrong | doubt |",
        "| --- | --- | --- | --- |",
        *[f"| {rule} | {v['right']} | {v['wrong']} | {v['doubt']} |" for rule, v in m["te4"]["per_rule"].items()],
        "",
        f"Confident and wrong: {ids(m['te4']['confident_wrong'])}",
        "",
    ]
    if any(r.gate or r.ai_rules for r in results):
        lines += gate_table([GateRow(id=r.id, expect=r.expect, gate=r.gate, ai_rules=r.ai_rules,
                                     settled="stopped" if r.by == "invalid" or (r.by == "code" and not r.gate) else "")
                             for r in results])
    t = m["tokens"]
    lines += [f"Tokens: {t['input_tokens']} input, {t['cache_creation_input_tokens']} cache write, "
              f"{t['cache_read_input_tokens']} cache read, {t['output_tokens']} output; {t['web_search_requests']} web searches, "
              f"{t.get('web_fetch_requests', 0)} web fetches.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- the gate in a dry run (tools/gate.py)

@dataclass
class GateRow:
    id: str
    expect: list
    settled: str = ""  # what stopped the case before the gate: a lint error, a lint mismatch or a 404
    gate: dict = field(default_factory=dict)  # rule -> verdict, for the rules code settled
    ai_rules: list = field(default_factory=list)  # the rules left for the referee
    held: list = field(default_factory=list)  # the rules code holds the case on for a person (TV3, TV5)
    before: str = ""  # replay: the outcome in the earlier run
    after: str = ""  # replay: the outcome with the gate in front of the same answers


def gate_row(case: Case, st: Staged, stored, replayed: Result | None = None) -> GateRow:
    """What the gate settles for one case, without calling anything. `stored` gives the source
    texts of an earlier fetch, or is None. `replayed` is the result of the case in an earlier run:
    its answers are combined with the gate, to see what the gate would have changed."""
    row = GateRow(id=case.id, expect=case.expect)
    if replayed is not None:
        row.before = row.after = replayed.outcome
    if not st.lint_ok:
        row.settled = "lint does not match"
        return row
    if st.lint_seen.startswith("error"):
        row.settled = f"lint {st.lint_seen.split()[1]}"
        return row
    sources = gather_sources(stored, st) if stored is not None else None
    if sources is not None and product_gone(st.files, sources):
        row.settled = "TP1 404"
        return row
    if second_source_fault(st, sources):
        row.settled = "TV4 second source"
        return row
    decided = gate.decide(st.product, st.publisher, sources, not st.known_publisher)
    row.gate = {r: j["verdict"] for r, j in decided.items()}
    held = hold(st, sources)
    row.held = list(held)
    no = [r for r, v in row.gate.items() if v == "no"]
    row.ai_rules = [] if no else [r for r in RULES if r not in decided]
    if replayed is not None:
        if no:
            row.after = "reject"
        elif replayed.by == "ai" and replayed.checked:
            merged = {r: {"verdict": replayed.checked.get(r, "yes")} for r in RULES}  # an older run has no TV1
            merged.update({r: {"verdict": v} for r, v in row.gate.items()})
            row.after = held_outcome(*outcome({**merged, "duplicate_of": replayed.duplicate_of}), held)[0]
        else:
            row.after = replayed.outcome
    return row


def gate_counts(rows: list) -> dict:
    """For the cases that reach the gate: of their TP5, TK2 and TE1 judgments, how many code settled,
    how many were not needed because code rejected the case, and how many go to the referee."""
    at = [r for r in rows if not r.settled]
    per_rule = {rule: {"yes": 0, "no": 0, "ai": 0, "moot": 0} for rule in gate.GATE_RULES}
    for r in at:
        rejected = "no" in r.gate.values()
        for rule in gate.GATE_RULES:
            if rule in r.gate:
                per_rule[rule][r.gate[rule]] += 1
            elif rejected:
                per_rule[rule]["moot"] += 1
            else:
                per_rule[rule]["ai"] += 1
    total = len(at) * len(gate.GATE_RULES)
    code = sum(v["yes"] + v["no"] for v in per_rule.values())
    moot = sum(v["moot"] for v in per_rule.values())
    return {"cases": len(rows), "at_gate": len(at), "judgments": total, "code": code, "moot": moot,
            "ai": total - code - moot, "per_rule": per_rule}


def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:.0f}%" if total else "-"


def gate_table(rows: list) -> list[str]:
    c = gate_counts(rows)
    lines = [
        "## The gate: TP5, TK2 and TE1 by code, before the referee",
        "",
        "| measure | value |",
        "| --- | --- |",
        f"| cases | {c['cases']} |",
        f"| stopped before the gate (lint error, lint mismatch, product source 404) | {c['cases'] - c['at_gate']} |",
        f"| cases at the gate | {c['at_gate']} |",
        f"| TP5, TK2 and TE1 judgments of those cases | {c['judgments']} |",
        f"| settled by code | {c['code']} ({_pct(c['code'], c['judgments'])}) |",
        f"| not needed, because code rejected the case | {c['moot']} |",
        f"| left for the referee | {c['ai']} ({_pct(c['ai'], c['judgments'])}) |",
        "",
        "| rule | code: yes | code: no | not needed | referee |",
        "| --- | --- | --- | --- | --- |",
        *[f"| {rule} | {v['yes']} | {v['no']} | {v['moot']} | {v['ai']} |" for rule, v in c["per_rule"].items()],
        "",
    ]
    return lines


def gate_summary(rows: list[GateRow], name: str, sources: str = "", replay: str = "") -> str:
    lines = [f"# Dry run {name}", "",
             "Nothing was sent to the API. The gate (`tools/gate.py`) settles what code can; the rest is what a live run "
             "would ask the referee.", "",
             f"Source texts: {sources or 'none, so TK2 and TE1 (apart from an AI chat) are left for the referee'}.", ""]
    lines += gate_table(rows)
    held = [f"{r.id} ({', '.join(r.held)})" for r in rows if r.held]
    lines += [f"Held for a person by code (TV3–TV5), whatever the referee answers: {len(held)} cases. "
              f"{', '.join(held) or 'none'}", ""]
    replayed = [r for r in rows if r.before]
    head = "| case | stopped before | TP5 | TK2 | TE1 | referee judges | held for a person |"
    sep = "| --- | --- | --- | --- | --- | --- | --- |"
    if replay:
        head += " outcome in the replayed run | with the gate |"
        sep += " --- | --- |"
    lines += ["## Per case", "", head, sep]
    for r in rows:
        cells = [r.id, r.settled or "-", *[r.gate.get(rule, "referee" if not r.settled and rule in r.ai_rules else "-")
                                          for rule in gate.GATE_RULES], ", ".join(r.ai_rules) or "-", ", ".join(r.held) or "-"]
        if replay:
            cells += [r.before or "-", r.after or "-"]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    if replay:
        let = lambda key: [r.id for r in replayed if r.id.startswith("B") and getattr(r, key) == "accept"]  # noqa: E731
        changed = [f"{r.id} {r.before} → {r.after}" for r in replayed if r.before != r.after]
        lines += [f"## Replay of {replay}", "",
                  "The answers of that run, with the verdicts of the gate in place of the referee's for the rules code settled.", "",
                  "| measure | the run | with the gate |", "| --- | --- | --- |",
                  f"| bad cases let through | {len(let('before'))} | {len(let('after'))} |",
                  f"| outcome in `expect` | {sum(r.before in r.expect for r in replayed)} of {len(replayed)} | "
                  f"{sum(r.after in r.expect for r in replayed)} of {len(replayed)} |", "",
                  f"Let through with the gate: {', '.join(let('after')) or 'none'}  ",
                  f"Outcome changed: {', '.join(changed) or 'none'}", ""]
    return "\n".join(lines)


def load_results(run_dir: Path) -> list[Result]:
    lines = (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    return [Result(**json.loads(line)) for line in lines if line.strip()]


def compare(dirs: list, show: list[str] | None = None) -> str:
    """TE4: the conditions side by side, on the cases they share. An entry may be a list of run
    directories that together make one condition (a run split by the stop rule). `show` lists cases
    to set out one by one."""
    runs = {}
    for d in dirs:
        parts = d if isinstance(d, (list, tuple)) else [d]
        merged = {}
        for part in parts:
            merged.update({r.id: r for r in load_results(Path(part))})
        runs[" + ".join(Path(p).name for p in parts)] = merged
    shared = sorted(set.intersection(*(set(r) for r in runs.values())))
    lines = [f"# Comparison on {len(shared)} shared cases", "",
             "| run | wrongly accepted | wrongly rejected | doubt | " + " | ".join(f"{r} right/wrong/doubt" for r in TE4_RULES)
             + " | confident and wrong | cost per case | searches | fetches |",
             "| --- " * (8 + len(TE4_RULES)) + "|"]
    for name, by_id in runs.items():
        rs = [by_id[i] for i in shared]
        m = metrics(rs)
        per = m["te4"]["per_rule"]
        lines.append(f"| {name} | {len(m['let_through'])} | {len(m['good_rejected'])} | {len(m['parked'])} | "
                     + " | ".join(f"{per[r]['right']}/{per[r]['wrong']}/{per[r]['doubt']}" for r in TE4_RULES)
                     + f" | {len(m['te4']['confident_wrong'])} | ${m['cost_per_call']:.4f} | {m['tokens']['web_search_requests']}"
                     + f" | {m['tokens'].get('web_fetch_requests', 0)} |")
    differ = [i for i in shared if len({runs[n][i].outcome for n in runs}) > 1]
    lines += ["", "Different outcomes: " + (", ".join(differ) or "none"),
              "", "With 29 cases a difference of one or two is noise (r13-research.md).", ""]
    if show:
        names = list(runs)
        lines += ["## Cases set out one by one", "", "| case | " + " | ".join(names) + " |", "| --- " * (len(names) + 1) + "|"]
        for cid in show:
            cells = []
            for n in names:
                r = runs[n].get(cid)
                cells.append("-" if r is None else f"{r.outcome} ({', '.join(f'{k} {v}' for k, v in r.checked.items() if v != 'yes') or 'all yes'})")
            lines.append(f"| {cid} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def check_urls(cases: list[Case], fetcher) -> str:
    """README § Before the run: fetch the `verify` URLs with the fetcher of the run."""
    lines = ["| case | url | status | method | characters of text |", "| --- | --- | --- | --- | --- |"]
    for c in cases:
        for url in c.verify:
            s = fetcher.fetch(url)
            lines.append(f"| {c.id} | {url} | {s.status} | {s.method or '-'} | {s.chars} |")
    return "\n".join(lines) + "\n"


def fetch_sources(args, fetcher=None, data_root: Path = REPO, log=print) -> dict:
    """Fetch the sources of the cases that reach the referee, and write them to args.output.
    No key is needed or read: this step runs where the key is not."""
    cases = select(load_cases(Path(args.testset)), args.cases, "fetched")
    if args.limit:
        cases = cases[: args.limit]
    stager = Stager(data_root)
    try:
        staged = [stager.stage(c) for c in cases]
    finally:
        stager.close()
    fetcher = fetcher or Fetcher(use_browser=not args.no_browser, log=log)
    if getattr(args, "base", None):
        # The sources of an earlier run (A2): kept as they were read then; only what is missing is fetched.
        base = json.loads(Path(args.base).read_text(encoding="utf-8"))
        fetcher.cache.update({key: Source(**src) for key, src in base.items()})
        log(f"base: {args.base} ({len(base)} sources); only what it lacks is fetched")
    data = {}
    if getattr(args, "search", None):
        # The second pass: read the URLs that step 4 found for refused sources, down steps 1-3.
        data = json.loads(Path(args.sources).read_text(encoding="utf-8"))
        found = json.loads(Path(args.search).read_text(encoding="utf-8"))
        try:
            for refused, hit in found.items():
                for url in hit["urls"]:
                    if url == refused or not on_domains(url, hit.get("domains")):
                        continue  # TAX-49: a page of someone else never stands in for the publisher's
                    src = fetcher.fetch(url, hit["product"])
                    if src.status.isdigit() and int(src.status) < 300:
                        alt = Source(**{**asdict(src), "instead_of": refused, "method": f"search+{src.method}"})
                        data[f"{source_key(url, src.product)} | for {refused}"] = asdict(alt)
        finally:
            if hasattr(fetcher, "close"):
                fetcher.close()
    else:
        alternatives = {}
        try:
            for st in staged:
                if st.lint_ok and not st.lint_seen.startswith("error"):
                    name = product_name(st)
                    for url in case_urls(st):
                        if fetcher.fetch(url, name).status in CLOSED:
                            # Step 0 on a closed source: the publisher's own sources for machines.
                            for ms in st.machine_sources:
                                src = fetcher.fetch(ms, name)
                                if src.method == "machine" and src.status.isdigit() and int(src.status) < 300:
                                    alt = Source(**{**asdict(src), "instead_of": url})
                                    alternatives[f"{source_key(ms, name)} | for {url}"] = asdict(alt)
        finally:
            if hasattr(fetcher, "close"):
                fetcher.close()
        data = {key: asdict(src) for key, src in fetcher.cache.items()}
        data.update(alternatives)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    log(sources_table(data))
    return data


def sources_table(data: dict) -> str:
    """How many sources each step read, and what did not come through."""
    counts: dict[tuple, int] = {}
    for src in data.values():
        key = (src.get("method") or "-", src["status"] if not src["status"].isdigit() or int(src["status"]) >= 300 else "read")
        counts[key] = counts.get(key, 0) + 1
    lines = [f"{len(data)} sources", "", "| method | status | sources |", "| --- | --- | --- |"]
    lines += [f"| {m} | {s} | {n} |" for (m, s), n in sorted(counts.items())]
    lines += ["", "| status | method | characters | url |", "| --- | --- | --- | --- |"]
    lines += [f"| {src['status']} | {src.get('method') or '-'} | {src.get('chars', 0)} | {url} |"
              for url, src in sorted(data.items()) if not src["status"].isdigit() or int(src["status"]) >= 300]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- step 4: search for another open source

SEARCH_PROMPT = (
    "Run exactly one web search with this query: {query}\n"
    "Do not answer anything and do not summarise the results; reply with the single word: done."
)
SEARCH_RESULTS = 5  # URLs kept per search
CLOSED = ("refused", "disallowed")  # a source step 4 searches another open source for
# The model of step 4, apart from the referee's (TAX-49): it only returns URLs and judges nothing.
# The round and the test set both take it from here. Agreed on the measurement of run 44776351618:
# 15 of 15 products read in each of three runs, as Opus 5.5; referee/README.md § Step 4.
SEARCH_MODEL = "claude-haiku-5-5"
# The models that take the newest web search tool (dynamic filtering); every other model takes the
# basic tool. Claude API docs, Server tools (read 2026-11-20).
NEWEST_SEARCH_TOOL = "web_search_20260209"
BASIC_SEARCH_TOOL = "web_search_20250305"
NEWEST_SEARCH_MODELS = ("claude-fable-", "claude-mythos-", "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8",
                        "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6")
# What one search puts in and out of the model, measured in run 44764232264 (15 searches): the
# results come in as about 12,000 input tokens (11,959 to 12,005), whatever the product; 85 out.
SEARCH_INPUT_TOKENS = 12_000
SEARCH_OUTPUT_TOKENS = 100


def search_tool(model: str) -> str:
    """The version of the web search tool that this model takes."""
    newest = any(model == m or (m.endswith("-") and model.startswith(m)) for m in NEWEST_SEARCH_MODELS)
    return NEWEST_SEARCH_TOOL if newest else BASIC_SEARCH_TOOL


def search_cost(model: str, prices: dict) -> float:
    """What one search is expected to cost: the fee, and the tokens of its results."""
    if model not in prices["models"]:
        raise SystemExit(f"No price for model {model!r} in referee/prices.yaml")
    p = prices["models"][model]
    return ((SEARCH_INPUT_TOKENS * p["input"] + SEARCH_OUTPUT_TOKENS * p["output"]) / 1_000_000
            + prices["web_search_per_1000"] / 1000)


registrable = gate.registrable


def on_domains(url: str, domains) -> bool:
    """Whether a URL that a search found is on one of the publisher's domains (TAX-49). The one check
    for the round and the test set: the domain limit of the API is not tight. No domains: never."""
    return bool(domains) and gate.registrable(gate.host_of(url)) in set(domains)


MAX_DOMAINS = 30


def search_domains(refused: str, staged: "Staged") -> list[str]:
    """The publisher's domains (run A2 review §1, point 6): of the closed URL, of the product's
    sources, and of every URL in the publisher's file and its other products in the data. Never
    a search without domains: sites of others do not count under TP5."""
    hosts = [refused] + urls_of(staged.files) + list(staged.publisher_urls)
    out = []
    for h in hosts:
        if isinstance(h, str) and h.startswith(("http://", "https://")):
            d = registrable(urllib.parse.urlsplit(h).netloc)
            if d not in out:
                out.append(d)
    return out[:MAX_DOMAINS]


def search_urls(content, limit: int | None = SEARCH_RESULTS) -> list[str]:
    """The URLs in the web search results of a response. The snippets are not kept: a fragment is
    never evidence (TE6)."""
    urls = []
    for block in content:
        if getattr(block, "type", "") == "web_search_tool_result" and isinstance(getattr(block, "content", None), list):
            for r in block.content:
                url = getattr(r, "url", "")
                if url and url not in urls:
                    urls.append(url)
    return urls[:limit]


def run_searches(wanted: dict, model: str, budget_cap: float, prices_path: Path, out: Path, client=None, now=None,
                 log=print, cap: float | None = None, records: list | None = None) -> tuple[dict, float]:
    """Step 4, live: one web search per distinct (query, domains) of `wanted` (closed URL -> product,
    publisher, domains), on the publisher's domains only. Only the URLs of the results are kept, never
    a snippet (TE6). Returns (closed URL -> what was found, the cost). The run counts against the
    budget of `out` like any other. Used by the test set (`search`), by a round (tools/importer.py) and by
    one search on its own (`search-one`). `records` gets the line of results.jsonl of each search."""
    found: dict[str, dict] = {}
    prices = load_prices(prices_path)
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("A live search needs ANTHROPIC_API_KEY")
        client = AnthropicClient()
    before = spent_before(out)
    # `cap`: dollars for these searches (a round's cap), within the budget for every run together.
    budget = Budget(budget_cap if cap is None else min(budget_cap, before + cap), before, model, prices, 2000)
    groups: dict[tuple, list[str]] = {}
    for url, want in wanted.items():
        query = want.get("query") or f'"{want["product"]}" {want["publisher"]}'.strip()  # a measurement repeats a query
        groups.setdefault((query, tuple(want["domains"])), []).append(url)
    log(f"{len(groups)} distinct searches for {len(wanted)} sources, with {model} and {search_tool(model)}; "
        f"about ${len(groups) * search_cost(model, prices):.2f}")
    log(f"budget: ${budget_cap:.2f} for all runs in {out}; spent before: ${before:.4f}")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    run_dir = out / f"{stamp}-search-{model}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "run.json").write_text(json.dumps({"condition": "search", "model": model, "budget": budget_cap,
                                                  "spent_before": round(before, 6), "started": stamp}, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
            gh.write(f"run_dir={run_dir}\nrun_name={run_dir.name}\n")
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as jsonl:
        for (query, domains), closed in groups.items():
            want = wanted[closed[0]]
            request = dict(
                model=model,
                messages=[{"role": "user", "content": SEARCH_PROMPT.format(query=query)}],
                output_config={"effort": "low"},
                tools=[{"type": search_tool(model), "name": "web_search", "max_uses": 1, "allowed_domains": want["domains"]}],
            )
            spent_at_start = budget.spent
            try:
                request["max_tokens"] = budget.fit(client.count(request), web_allowance(model, prices, 1))
                response = client.create(request)
            except BudgetExhausted as exc:
                log(f"Stopped before searching {query}: {exc}")
                break
            except Exception as exc:  # noqa: BLE001 - a failed search leaves the source refused
                log(f"search {query} failed: {type(exc).__name__}: {str(exc)[:300]}")
                continue
            budget.add(response.usage)
            every = search_urls(response.content, None)
            # Only URLs on the publisher's domains are kept; the rest is noted, and never read.
            elsewhere = [u for u in every if not on_domains(u, want["domains"])]
            urls = [u for u in every if u not in elsewhere and u not in closed][:SEARCH_RESULTS]
            hit = {"product": want["product"], "query": query, "domains": want["domains"], "urls": urls}
            for url in closed:
                found[url] = hit
            cost_now = budget.spent - spent_at_start
            record = {"id": "search", "for": closed, **hit, "elsewhere": elsewhere, "tool": search_tool(model),
                      "usage": response.usage, "cost": cost_now}
            jsonl.write(json.dumps(record) + "\n")
            if records is not None:
                records.append(record)
            log(f"{query}: {len(urls)} URLs on {', '.join(want['domains'])} for {len(closed)} closed sources"
                + (f" ({len(elsewhere)} elsewhere, not kept)" if elsewhere else "")
                + f", {response.usage.get('input_tokens', 0)} input tokens, ${cost_now:.4f}; spent ${budget.spent:.4f} of ${budget_cap:.2f}")
    return found, budget.spent - before


def search_refused(args, client=None, data_root: Path = REPO, now=None, log=print) -> dict:
    """Step 4: for each refused or disallowed source, one web search on the publisher's domains. Costs money
    (`search_cost`: the fee and the tokens of the results) and counts against the budget like a run."""
    stored = StoredFetcher(json.loads(Path(args.sources).read_text(encoding="utf-8")))
    cases = select(load_cases(Path(args.testset)), args.cases, "fetched")
    if args.limit:
        cases = cases[: args.limit]
    stager = Stager(data_root)
    try:
        staged = [stager.stage(c) for c in cases]
    finally:
        stager.close()
    wanted: dict[str, dict] = {}
    for st in staged:
        if not st.lint_ok or st.lint_seen.startswith("error"):
            continue
        name = product_name(st)
        _, prod = product_file(st.files)
        for url in case_urls(st):
            # Step 4 only when step 0 found nothing in the publisher's machine_sources.
            if stored.fetch(url, name).status in CLOSED and not stored.alternatives(url) and url not in wanted:
                wanted[url] = {"product": name, "publisher": prod.get("publisher", ""), "domains": search_domains(url, st)}
    log(f"{len(wanted)} refused or disallowed sources to search for")
    out = Path(args.out)
    found: dict[str, dict] = {}
    if wanted and not args.live:
        log(f"Dry run: nothing was searched. Add --live to search (about ${search_cost(args.model, load_prices(Path(args.prices))):.3f} "
            f"a search on {args.model}, its results as input tokens included).")
    if wanted and args.live:
        found, _ = run_searches(wanted, args.model, args.budget, Path(args.prices), out, client, now, log)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(found, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return found


def search_one(args, client=None, now=None, log=print) -> dict:
    """One search on its own (TAX-49): the query of step 4 for one product, on the given domains,
    with the given model. No page is read and nothing is judged. Shows the URLs found, the tokens
    and the cost. Without --live it shows the estimate and sends nothing."""
    model = args.model or SEARCH_MODEL
    domains = [d for part in args.domains for d in part.replace(",", " ").split() if d]
    if not domains:
        raise SystemExit("search-one needs at least one domain of the publisher")
    prices = load_prices(Path(args.prices))
    wanted = {f"search-one: {args.product}": {"product": args.product, "publisher": args.publisher, "domains": domains}}
    log(f"one search for {args.product!r} of {args.publisher!r} on {', '.join(domains)}, with {model} and "
        f"{search_tool(model)}; about ${search_cost(model, prices):.3f}")
    if not args.live:
        log("Dry run: nothing was searched. Add --live to search.")
        return {}
    records: list = []
    run_searches(wanted, model, args.budget, Path(args.prices), Path(args.out), client, now, log, records=records)
    if not records:
        log("No result: the search failed or the budget stopped it (see above).")
        return {}
    r = records[0]
    u = r["usage"]
    lines = ["", "| model | tool | input tokens | output tokens | searches | cost |", "| --- | --- | --- | --- | --- | --- |",
             f"| {model} | {r['tool']} | {u.get('input_tokens', 0)} | {u.get('output_tokens', 0)} | "
             f"{u.get('web_search_requests', 0)} | ${r['cost']:.4f} |", "",
             f"On the publisher's domains, kept ({len(r['urls'])}):"]
    lines += [f"- {x}" for x in r["urls"]] or ["- none"]
    lines += ["", f"Elsewhere, never read ({len(r['elsewhere'])}):"]
    lines += [f"- {x}" for x in r["elsewhere"]] or ["- none"]
    log("\n".join(lines))
    return r


# --------------------------------------------------------------------------- the model measurement (TAX-18)

A2 = REFEREE / "runs/20261113-163823-fetched-claude-opus-5-5"  # Opus 5.5, effort medium, not a batch
PROBE = ("G01", "G02", "G03")  # three cases that reach the referee: measured first, then extrapolated
# Output tokens per effort, against those of A2 (effort medium). Low: Sonnet 5.5 wrote about 560 per
# case in the measurement of 16 November (workflow run 43890744244), A2 about 1,100. High: an assumption.
OUTPUT_FACTOR = {"low": 0.5, "medium": 1.0, "high": 2.0}


def a2_tokens(a2: Path = A2) -> dict:
    """Per case of run A2: (input tokens not cached, read from the cache, output tokens)."""
    out = {}
    for r in load_results(a2):
        if r.usage:
            u = r.usage
            out[r.id] = (u["input_tokens"] + u["cache_creation_input_tokens"], u["cache_read_input_tokens"], u["output_tokens"])
    return out


def estimate(model: str, prices: dict, cases: list[str], repeats: int, max_tokens: int, counted: dict | None = None,
             a2: Path = A2, effort: str = "medium") -> dict:
    """What `repeats` batch runs of these cases may cost with this model at this effort.
    Input: the tokens counted (token counting), else those of run A2; the part A2 read from the cache
    (the system prompt) at the cache price, the rest at the input price. Output: that of run A2
    (effort medium), times OUTPUT_FACTOR of the effort. A case A2 did not have counts as the mean
    case of A2 (`new` says how many).
    Ceiling: no cache, and every request its full max_tokens."""
    seen = a2_tokens(a2)
    mean = tuple(round(sum(v[i] for v in seen.values()) / len(seen)) for i in range(3)) if seen else (0, 0, 0)
    pm = prices["models"][model]
    d = discount(prices, True)
    p_out = output_price(model, prices, True)
    cached = plain = outp = new = 0
    for c in cases:
        new += c not in seen
        fresh, read, o = seen.get(c, mean)
        total = (counted or {}).get(c, fresh + read)
        cached += min(read, total)
        plain += total - min(read, total)
        outp += round(o * OUTPUT_FACTOR[effort])
    expected = ((plain * pm["input"] + cached * pm["cache_read"]) / 1_000_000 * d + outp * p_out) * repeats
    ceiling = ((plain + cached) * input_price(model, prices, True) + len(cases) * max_tokens * p_out) * repeats
    return {"model": model, "effort": effort, "cases": len(cases), "new": new, "input": plain + cached,
            "output": outp, "expected": expected, "ceiling": ceiling, "counted": bool(counted)}


def stands(args) -> list[tuple[str, str]]:
    """(model, effort) for each stand the measurement compares: every model at every effort."""
    return [(m, e) for m in args.models for e in args.efforts]


def stand_name(model: str, effort: str) -> str:
    return f"{model} {effort}"


def measure(args, client=None, fetcher=None, data_root: Path = REPO, now=None, log=print, sleep=None) -> dict:
    """TAX-18: per stand (a model at an effort), `repeats` batch runs of the test set, within the budget.
    First the probe cases of every stand, one small batch each; their real cost per case is
    extrapolated to the whole measurement, and if that would pass the budget nothing more is sent.
    The probe is the start of run 1: compare joins the two directories with +.
    Without --live it estimates: with --count from counted tokens (free, needs the key), else from
    the tokens of run A2."""
    out = Path(args.out)
    prices = load_prices(Path(args.prices))
    cases = select(load_cases(Path(args.testset)), None, "fetched")
    stager = Stager(data_root)
    try:
        to_ai = [c.id for c in cases if not stager.stage(c).lint_seen.startswith("error")]
    finally:
        stager.close()
    before = spent_before(out)
    log(f"{len(cases)} cases, {len(to_ai)} past the lint; budget ${args.budget:.2f} for all runs in {out}, "
        f"spent ${before:.4f}, left ${args.budget - before:.4f}")

    def run_args(model, ids, live, effort="low", **kw):
        return argparse.Namespace(condition="fetched", cases=ids, limit=None, model=model, effort=effort,
                                  max_tokens=args.max_tokens, max_uses=0, budget=args.budget, live=live, batch=True,
                                  testset=args.testset, prompt=args.prompt, schema=args.schema, prices=args.prices,
                                  out=str(out), sources=args.sources, replay=None, wait=args.wait, sleep=sleep, **kw)

    if not args.live:
        counted = {}
        if args.count:
            # Token counting does not depend on the effort: one count per model.
            counted = count_cases(run_args(args.models[0], None, False), client, fetcher, data_root, log)
        rows = [estimate(m, prices, to_ai, args.repeats, args.max_tokens, counted.get(m) if counted else None, effort=e)
                for m, e in stands(args)]
        if args.count:
            for row in rows:
                row["counted"] = True
        total = sum(r["expected"] for r in rows)
        lines = ["| model | effort | cases | input tokens | output tokens (A2 × effort) | expected, %d runs | ceiling |"
                 % args.repeats, "| --- | --- | --- | --- | --- | --- | --- |"]

        def basis(r):
            if r["counted"]:
                return ""
            return f" (A2; {r['new']} new cases as its mean case)" if r["new"] else " (A2)"

        lines += [f"| {r['model']} | {r['effort']} | {r['cases']} | {r['input']}{basis(r)} | "
                  f"{r['output']} | ${r['expected']:.2f} | ${r['ceiling']:.2f} |" for r in rows]
        lines += ["", f"Expected in all: ${total:.2f}; left in the budget: ${args.budget - before:.2f}. "
                  f"{'Fits.' if before + total <= args.budget else 'Does NOT fit: the probe would stop the measurement.'}",
                  "Dry run: nothing was sent. Add --live to measure."]
        log("\n".join(lines))
        return {"rows": rows, "total": total, "left": args.budget - before}

    stamp = lambda: (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")  # noqa: E731
    pairs = stands(args)
    runs: dict[str, list] = {stand_name(m, e): [] for m, e in pairs}
    probe = [c for c in PROBE if c in to_ai]
    per_case = {}
    for n, (model, effort) in enumerate(pairs):
        name = stand_name(model, effort)
        results, run_dir = run(run_args(model, probe, True, effort), client=client, fetcher=fetcher, data_root=data_root,
                               now=now and now.replace(minute=n), log=log)
        called = [r for r in results if r.usage]
        per_case[name] = sum(r.cost for r in called) / len(called) if called else 0.0
        runs[name].append([run_dir])
    rest = [c.id for c in cases if c.id not in probe]
    rest_ai = [c for c in rest if c in to_ai]
    projected = sum(per_case[s] * (len(rest_ai) + len(to_ai) * (args.repeats - 1)) for s in runs)
    spent = spent_before(out)
    log("probe: " + ", ".join(f"{s} ${per_case[s]:.4f} per case" for s in runs)
        + f"; the rest would cost about ${projected:.2f}; spent ${spent:.4f} of ${args.budget:.2f}")
    report = {"runs": runs, "per_case": per_case, "projected": projected, "stopped": ""}
    unmeasured = [s for s in runs if not per_case[s]]
    if unmeasured:
        report["stopped"] = f"Stopped after the probe: no answer to measure for {', '.join(unmeasured)} (the budget, or errors)"
        log(report["stopped"])
        return report
    if spent + projected > args.budget:
        report["stopped"] = (f"Stopped after the probe: ${spent:.4f} spent and about ${projected:.2f} to go "
                             f"passes the budget of ${args.budget:.2f}")
        log(report["stopped"])
        return report
    for n, (model, effort) in enumerate(pairs):
        name = stand_name(model, effort)
        for k in range(args.repeats):
            ids = rest if k == 0 else [c.id for c in cases]
            _, run_dir = run(run_args(model, ids, True, effort), client=client, fetcher=fetcher, data_root=data_root,
                             now=now and now.replace(hour=1 + n, minute=k), log=log)
            if k == 0:
                runs[name][0].append(run_dir)
            else:
                runs[name].append([run_dir])
    text = measurement_report(runs, Path(args.compare_with) if args.compare_with else None)
    path = out / f"model-measurement-{stamp()}.md"
    path.write_text(text, encoding="utf-8")
    log(text)
    report["report"] = path
    return report


def count_cases(args, client=None, fetcher=None, data_root: Path = REPO, log=print) -> dict:
    """model -> case -> the input tokens of its request, counted by the API (free; needs the key).
    Builds the same requests as a run, and sends none of them."""
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("Token counting needs ANTHROPIC_API_KEY (it is free, but it is an API call)")
        client = AnthropicClient()
    footer = footer_counts()  # TAX-43
    system = load_system_prompt(Path(args.prompt), footer)
    api = api_schema(load_schema(Path(args.schema)))
    stored = StoredFetcher(json.loads(Path(args.sources).read_text(encoding="utf-8"))) if args.sources else None
    fetcher = fetcher or stored or Fetcher(log=log)
    stager = Stager(data_root)
    out = {}
    try:
        for case in select(load_cases(Path(args.testset)), None, "fetched"):
            st = stager.stage(case)
            if st.lint_seen.startswith("error") or gate.ai_chat(st.product, st.publisher):
                continue
            sources = gather_sources(fetcher, st)
            if product_gone(st.files, sources):
                continue
            decided = gate.decide(st.product, st.publisher, sources, not st.known_publisher)
            rules = [r for r in RULES if r not in decided]
            for model in getattr(args, "models", None) or [args.model]:
                request = dict(model=model, system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                               messages=[{"role": "user", "content": build_message(st, "fetched", sources, decided, footer)}],
                               output_config={"effort": args.effort, "format": {"type": "json_schema", "schema": schema_for(api, rules)}})
                out.setdefault(model, {})[case.id] = client.count(request)
    finally:
        stager.close()
    log("counted: " + ", ".join(f"{m} {sum(v.values())} input tokens for {len(v)} cases" for m, v in out.items()))
    return out


def measurement_report(runs: dict, a2: Path | None = None) -> str:
    """TAX-18: per stand (a model at an effort) over its runs the numbers of the story, and the
    comparison with A2. Besides the outcomes: the quotes code did not find (TP3 without a version
    among them), the output tokens per proposal, and per rule how often it left a case to a person."""
    lines = ["# Model measurement (TAX-18)", "",
             "Per stand (a model at an effort), as a batch, after the gate (TAX-15). A stand that lets one bad case "
             "through in any run drops out.", "",
             "| stand | runs | bad let through (per run) | good rejected (per run) | parked (per run) | "
             "cases whose outcome differs between runs | quotes not found (per run) | output tokens per proposal | "
             "cost per proposal | cost of all runs |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for stand, groups in runs.items():
        per_run = []
        for group in groups:
            merged = {}
            for d in group:
                merged.update({r.id: r for r in load_results(Path(d))})
            per_run.append(merged)
        ms = [metrics(list(r.values())) for r in per_run]
        shared = set.intersection(*(set(r) for r in per_run)) if per_run else set()
        differ = sorted(i for i in shared if len({r[i].outcome for r in per_run}) > 1)
        called = [r for run_ in per_run for r in run_.values() if r.usage]
        cost_all = sum(r.cost for run_ in per_run for r in run_.values())
        notes = [sum(len(r.quote_notes) for r in run_.values()) for run_ in per_run]
        output = sum(r.usage.get("output_tokens", 0) for r in called) / len(called) if called else 0
        lines.append(f"| {stand} | {len(per_run)} | {' / '.join(str(len(m['let_through'])) for m in ms)} | "
                     f"{' / '.join(str(len(m['good_rejected'])) for m in ms)} | {' / '.join(str(len(m['parked'])) for m in ms)} | "
                     f"{len(differ)}{': ' + ', '.join(differ) if differ else ''} | {' / '.join(map(str, notes))} | "
                     f"{output:.0f} | ${(sum(r.cost for r in called) / len(called)) if called else 0:.4f} | ${cost_all:.4f} |")
        let = sorted({i for m in ms for i in m["let_through"]})
        doubt = {}
        for run_ in per_run:
            for r in run_.values():
                if r.outcome == "parked":
                    for rule in (x for x in RULES if r.checked.get(x) == "doubt"):
                        doubt[rule] = doubt.get(rule, 0) + 1
        tr4 = sum(n.startswith("TP3: yes without a version") or n.startswith("TP3: version not found")
                 for run_ in per_run for r in run_.values() for n in r.quote_notes)
        lines.append(f"|  | let through: {', '.join(let) or 'none'} | good rejected: "
                     f"{', '.join(sorted({i for m in ms for i in m['good_rejected']})) or 'none'} | "
                     f"parked by rule: {', '.join(f'{k} {v}' for k, v in sorted(doubt.items())) or 'none'} |  | "
                     f"TP3 without a version found: {tr4} |  |  |  |")
    lines += [""]
    if a2 is not None:
        dirs = [[a2]] + [group for groups in runs.values() for group in groups]
        lines += ["## Compared with A2 (Opus 5.5, effort medium, not a batch)", "", compare(dirs)]
    return "\n".join(lines)


# --------------------------------------------------------------------------- command line

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run cases past the referee")
    r.add_argument("--condition", choices=CONDITIONS, default="fetched")
    r.add_argument("--cases", nargs="*", help="case ids, or 'all'; default: all for fetched, the 29 TE4 cases otherwise")
    r.add_argument("--limit", type=int, help="only the first N of those cases")
    r.add_argument("--model", default=MODEL)
    r.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), default=EFFORT)
    r.add_argument("--max-tokens", type=int, default=MAX_TOKENS, help="per request; the budget may lower it")
    r.add_argument("--max-uses", type=int, default=3, help="web: most searches per case")
    r.add_argument("--budget", type=float, default=BUDGET, help="dollars, for all runs in --out together")
    r.add_argument("--live", action="store_true", help="call the API; without it nothing is sent")
    r.add_argument("--batch", action="store_true", help="all requests as one batch, at batch_discount of the price (not web)")
    for name, default in (("testset", REFEREE / "testset/cases.yaml"), ("prompt", REFEREE / "prompt.md"),
                          ("schema", REFEREE / "verdict.schema.json"), ("prices", REFEREE / "prices.yaml"),
                          ("out", REFEREE / "runs")):
        r.add_argument(f"--{name}", default=str(default))
    r.add_argument("--sources", help="a sources.json made by `fetch`; the run then fetches nothing itself")
    r.add_argument("--replay", help="dry run: a run directory whose answers are combined with the gate")
    u = sub.add_parser("check-urls", help="fetch the verify URLs of the test set (free)")
    u.add_argument("--testset", default=str(REFEREE / "testset/cases.yaml"))
    u.add_argument("--no-browser", action="store_true", help="skip the browser step")
    f = sub.add_parser("fetch", help="fetch the sources of the cases into a sources.json (free, no key)")
    f.add_argument("--testset", default=str(REFEREE / "testset/cases.yaml"))
    f.add_argument("--cases", nargs="*", help="case ids, or 'all' (default: all)")
    f.add_argument("--limit", type=int)
    f.add_argument("--output", default="sources.json")
    f.add_argument("--no-browser", action="store_true", help="skip the browser step")
    f.add_argument("--sources", help="with --search: the sources.json of the first pass, to add to")
    f.add_argument("--search", help="a search.json made by `search`: read the URLs it found (steps 1-3)")
    f.add_argument("--base", help="a sources.json of an earlier run: kept, and only the sources it lacks are fetched")
    s = sub.add_parser("search", help="step 4: search for another open source for each refused source (costs money)")
    s.add_argument("--sources", required=True, help="the sources.json made by `fetch`")
    s.add_argument("--output", default="search.json")
    s.add_argument("--testset", default=str(REFEREE / "testset/cases.yaml"))
    s.add_argument("--cases", nargs="*")
    s.add_argument("--limit", type=int)
    s.add_argument("--model", default=SEARCH_MODEL, help="the model of step 4, apart from the referee's")
    s.add_argument("--budget", type=float, default=BUDGET)
    s.add_argument("--prices", default=str(REFEREE / "prices.yaml"))
    s.add_argument("--out", default=str(REFEREE / "runs"))
    s.add_argument("--live", action="store_true", help="search; without it nothing is sent")
    o = sub.add_parser("search-one", help="one search on its own, no page read, nothing judged (costs about $0.01)")
    o.add_argument("--product", required=True, help="the name of the product, as the query of step 4 has it")
    o.add_argument("--publisher", default="", help="the name of the publisher, as the query of step 4 has it")
    o.add_argument("--domains", nargs="+", required=True, help="the publisher's domains, separated by spaces or commas")
    o.add_argument("--model", default="", help=f"empty: the model of step 4 ({SEARCH_MODEL})")
    o.add_argument("--budget", type=float, default=BUDGET, help="dollars, for all runs in --out together")
    o.add_argument("--prices", default=str(REFEREE / "prices.yaml"))
    o.add_argument("--out", default=str(REFEREE / "runs"))
    o.add_argument("--live", action="store_true", help="search; without it nothing is sent")
    m = sub.add_parser("measure", help="TAX-18: repeated batch runs per model, within the budget")
    m.add_argument("--models", nargs="+", default=[MODEL])
    m.add_argument("--repeats", type=int, default=3)
    m.add_argument("--efforts", nargs="+", choices=tuple(OUTPUT_FACTOR), default=[EFFORT],
                   help="one stand per model and effort: each gets --repeats runs")
    m.add_argument("--max-tokens", type=int, default=4000, help="per request; prompt.md asks for about 2,000")
    m.add_argument("--budget", type=float, default=BUDGET, help="dollars, for all runs in --out together (hard)")
    m.add_argument("--sources", help="a sources.json made by `fetch` (run A2: artifact referee-sources-final-43462875320)")
    m.add_argument("--compare-with", default=str(A2), help="the run to compare with")
    m.add_argument("--wait", type=float, default=BATCH_WAIT, help="seconds to wait for each batch")
    m.add_argument("--live", action="store_true", help="measure (costs money); without it: an estimate")
    m.add_argument("--count", action="store_true", help="dry run: count the input tokens with the API (free, needs the key)")
    for name, default in (("testset", REFEREE / "testset/cases.yaml"), ("prompt", REFEREE / "prompt.md"),
                          ("schema", REFEREE / "verdict.schema.json"), ("prices", REFEREE / "prices.yaml"),
                          ("out", REFEREE / "runs")):
        m.add_argument(f"--{name}", default=str(default))
    c = sub.add_parser("compare", help="compare runs on the cases they share (TE4)")
    c.add_argument("runs", nargs="+", help="run directories; join directories of one condition with + (a run split by the stop rule)")
    c.add_argument("--show", nargs="*", help="cases to set out one by one")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "compare":
        if len(args.runs) < 2:
            raise SystemExit("compare needs at least two run directories")
        print(compare([r.split("+") for r in args.runs], args.show))
    elif args.command == "check-urls":
        fetcher = Fetcher(use_browser=not args.no_browser)
        try:
            print(check_urls(load_cases(Path(args.testset)), fetcher))
        finally:
            if hasattr(fetcher, "close"):
                fetcher.close()
    elif args.command == "fetch":
        fetch_sources(args)
    elif args.command == "search":
        search_refused(args)
    elif args.command == "search-one":
        search_one(args)
    elif args.command == "measure":
        report = measure(args)
        if os.environ.get("GITHUB_OUTPUT") and args.live:
            # One artifact per run directory, as for any run, so that the budget counts each once.
            dirs = [d for groups in report["runs"].values() for group in groups for d in group]
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
                for n, d in enumerate(dirs, 1):
                    gh.write(f"dir_{n}={d}\nname_{n}={Path(d).name}\n")
                if report.get("report"):
                    gh.write(f"report={report['report']}\n")
    else:
        run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
