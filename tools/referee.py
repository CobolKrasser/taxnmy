#!/usr/bin/env python3
"""Test harness for the AI referee (rules S4-S8).

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
$20.00) is a hard limit for all runs in the output directory together: before
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
import shutil
import sys
import tempfile
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint  # noqa: E402
from fetch import (  # noqa: E402,F401 - Source and the text helpers are part of the harness API
    MIN_CHARS, SOURCE_CHARS, USER_AGENT, Fetcher, Source, StoredFetcher, html_to_text, page_text, pdf_to_text,
    source_key,
)

REPO = Path(__file__).resolve().parent.parent
REFEREE = REPO / "referee"
RULES = ("S4", "S5", "S6", "S7", "S8")
OUTCOMES = ("accept", "reject", "alias", "human")
CONDITIONS = ("fetched", "memory", "web")
MAX_TOKENS = 8000  # per request; the budget may lower it
MIN_OUTPUT = 2000  # a request with less room for output than this is not sent
WEB_ALLOWANCE = 20_000  # web: input tokens reserved per search (not a hard bound)
WEB_TOOL_OVERHEAD = 2_000  # web: input tokens of the tool definition, which token counting cannot see

# The 29 cases of the R13 research (referee/r13-research.md, "Which cases").
R13_CASES = (
    "G02", "G06", "G08", "G17", "G18", "G19", "G20", "G21", "G22", "G23", "G24", "G25", "G27", "G28",
    "B06", "B12", "B14", "B15", "B20", "B21", "B22", "B23", "B24", "B25", "B26", "B27", "B28", "B29", "B30",
)
R13_RULES = ("S4", "S5", "S7", "S8")

# The system prompt of conditions memory and web (R13), written by the analyst.
R13_PROMPT = REFEREE / "prompt-r13.md"

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
        "rules": {"type": "array", "items": {"type": "string", "pattern": "^S[1-9]$"}},
        "alias_of": {"type": "string"},
        "lint": {"type": "string", "pattern": "^(pass|signal|error S[1-3])$"},
        "holdout": {"type": "array", "items": {"type": "string"}},
        "hide": {"type": "array", "items": {"type": "string"}},
        "context": {"type": "object", "additionalProperties": {"type": "string"}},
        "proposal": {"type": "object", "additionalProperties": {"type": "string"}},
        "verify": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
    },
    "required": ["id", "type", "expect", "lint"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- the test set

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
        if not raw.get("holdout") and not raw.get("proposal"):
            problems.append(f"case {raw['id']} proposes nothing (no holdout, no proposal)")
        cases.append(Case(**raw))
    if problems:
        raise SystemExit("The test set has problems:\n  " + "\n  ".join(problems))
    if not cases:
        raise SystemExit(f"No cases in {path}")
    return cases


def select(cases: list[Case], ids: list[str] | None, condition: str) -> list[Case]:
    """The cases of a run: the ids given, or all cases for fetched and the R13 cases otherwise."""
    if ids == ["all"]:
        return cases
    wanted = ids or (None if condition == "fetched" else list(R13_CASES))
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


class Stager:
    """A copy of products/ and publishers/; each case is staged on it and undone after."""

    def __init__(self, data_root: Path = REPO):
        self.source = data_root
        self.root = Path(tempfile.mkdtemp(prefix="taxnmy-referee-"))
        for part in ("products", "publishers"):
            shutil.copytree(data_root / part, self.root / part)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def stage(self, case: Case) -> Staged:
        saved: dict[Path, bytes | None] = {}

        def put(rel: str, text: str | None) -> None:
            path = self.root / rel
            if path not in saved:
                saved[path] = path.read_bytes() if path.exists() else None
            if text is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")

        files = {}
        for rel in case.holdout:
            src = self.source / rel
            if not src.is_file():
                raise SystemExit(f"case {case.id}: hold-out {rel} is not in the data")
            files[rel] = src.read_text(encoding="utf-8")
        files.update(case.proposal)
        try:
            for rel in case.holdout + case.hide:
                put(rel, None)
            for rel, text in case.context.items():
                put(rel, text)
            for rel, text in files.items():
                put(rel, text)
            findings = [f for f in lint.lint(self.root, REPO / "schema") if f.file in files]
            seen = lint_summary(findings)
            for rel in files:  # the proposal is not part of what exists
                put(rel, None)
            existing = self._existing(case, files, findings)
            publisher_source = self._publisher_source(files)
            machine_sources = self._machine_sources(files)
            publisher_urls = self._publisher_urls(files)
        finally:
            for path, content in saved.items():
                if content is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
        return Staged(files=files, findings=findings, lint_ok=lint_matches(case.lint, findings), lint_seen=seen,
                      existing=existing, publisher_source=publisher_source, machine_sources=machine_sources,
                      publisher_urls=publisher_urls)

    def _publisher_file(self, files: dict) -> tuple[str, str]:
        """The existing publisher file of the proposed product, unless the proposal brings it."""
        _, data = product_file(files)
        pub = data.get("publisher")
        if not isinstance(pub, str) or not re.fullmatch(r"[a-z0-9-]+", pub):
            return "", ""
        rel = f"publishers/{pub}.yaml"
        path = self.root / rel
        if rel in files or not path.is_file():
            return "", ""
        return rel, path.read_text(encoding="utf-8")

    def _publisher_urls(self, files: dict) -> list:
        """The URLs in the publisher's file (proposed or existing) and in its other products in the data."""
        _, data = product_file(files)
        pub = data.get("publisher")
        if not isinstance(pub, str) or not re.fullmatch(r"[a-z0-9-]+", pub):
            return []
        texts = [files[f"publishers/{pub}.yaml"]] if f"publishers/{pub}.yaml" in files else []
        if not texts and (self.root / f"publishers/{pub}.yaml").is_file():
            texts.append((self.root / f"publishers/{pub}.yaml").read_text(encoding="utf-8"))
        texts += [p.read_text(encoding="utf-8") for p in sorted((self.root / "products" / pub).glob("*.yaml"))]
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
        elif re.fullmatch(r"publishers/[a-z0-9-]+\.yaml", rel) and (self.root / rel).is_file():
            text = (self.root / rel).read_text(encoding="utf-8")
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

    def _existing(self, case: Case, files: dict, findings: list) -> str:
        """For each signal the existing product it points to; the products of the same publisher;
        the publishers that share a word with the proposed publisher key."""
        parts, shown = [], set()
        rel, text = self._publisher_file(files)
        if rel:
            parts.append(f"# {rel}\n{text.rstrip()}")
        for f in findings:
            if f.level != "signal":
                continue
            for rel in re.findall(r"products/[a-z0-9-]+/[a-z0-9-]+\.yaml", f.message):
                path = self.root / rel
                if rel not in shown and rel not in files and path.is_file():
                    shown.add(rel)
                    parts.append(f"# {rel}\n{path.read_text(encoding='utf-8').rstrip()}")
        publishers = sorted({_publisher_of(rel, text) for rel, text in files.items() if rel.startswith("products/")} - {""})
        for pub in publishers:
            lines = []
            for path in sorted((self.root / "products" / pub).glob("*.yaml")):
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                lines.append(f"{data['key']}: {data['name']}")
            parts.append(f"# products of publisher {pub}\n" + ("\n".join(lines) if lines else "none"))
            words = set(pub.split("-"))
            near = sorted(p.stem for p in (self.root / "publishers").glob("*.yaml") if p.stem != pub and words & set(p.stem.split("-")))
            parts.append(f"# publishers that share a word with {pub}\n" + ("\n".join(near) if near else "none"))
        return "\n\n".join(parts)


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

def load_system_prompt(path: Path) -> str:
    """The text block under '## System prompt' in referee/prompt.md."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^## System prompt\s*$.*?^```text\n(.*?)^```", text, re.S | re.M)
    if not m:
        raise SystemExit(f"No system prompt (a ```text block under '## System prompt') in {path}")
    return m.group(1).rstrip() + "\n"


def build_message(staged: Staged, condition: str, sources: list[Source]) -> str:
    """The user message, in the order of referee/prompt.md § User message."""
    proposal = "\n".join(f"# {rel}\n{text.rstrip()}" for rel, text in staged.files.items())
    signals = "\n".join(f"{f.rule}: {f.message}" for f in staged.findings if f.level == "signal") or "none"
    if condition == "fetched":
        blocks = [f'<source url="{s.url}" status="{s.status}"'
                  + (f' instead-of="{s.instead_of}"' if s.instead_of else "") + f'>\n{s.text}\n</source>' for s in sources]
        body = "\n".join(blocks) if blocks else "none"
    else:
        body = NO_SOURCES[condition]
    return (f"<proposal>\n{proposal}\n</proposal>\n\n<signals>\n{signals}\n</signals>\n\n"
            f"<existing>\n{staged.existing or 'none'}\n</existing>\n\n<sources>\n{body}\n</sources>\n")


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


# --------------------------------------------------------------------------- the outcome (referee/prompt.md § Outcome)

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def product_gone(files: dict, sources: list[Source]) -> str:
    """The product's own source answers 404 or 410: a reason to reject on S7, or ''."""
    _, data = product_file(files)
    url = data.get("source")
    for s in sources:
        if s.url == url and s.status in ("404", "410"):
            return f"the product source {url} answers {s.status}"
    return ""


def check_quotes(verdict: dict, sources: list[Source]) -> tuple[dict, list[str]]:
    """A yes whose quotes are not all found in the fetched text of their URL becomes doubt.
    A yes without any quote becomes doubt too. Returns the checked verdict and what changed."""
    texts = {s.url: _norm(s.text) for s in sources}
    checked, notes = copy.deepcopy(verdict), []
    for rule in RULES:
        j = checked[rule]
        if j["verdict"] != "yes":
            continue
        if not j["evidence"]:
            j["verdict"] = "doubt"
            notes.append(f"{rule}: yes without a quote")
            continue
        for ev in j["evidence"]:
            if _norm(ev["quote"]) not in texts.get(ev["url"], ""):
                j["verdict"] = "doubt"
                notes.append(f"{rule}: quote not found at {ev['url']}")
                break
    return checked, notes


def outcome(verdict: dict) -> tuple[str, str]:
    """(outcome, the first rule that is no)."""
    noes = [r for r in RULES if verdict[r]["verdict"] == "no"]
    if noes:
        return ("alias" if verdict.get("duplicate_of") else "reject"), noes[0]
    if any(verdict[r]["verdict"] == "doubt" for r in RULES):
        return "human", ""
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


def cost(usage: dict, model: str, prices: dict) -> float:
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
    total += usage.get("web_search_requests", 0) * prices["web_search_per_1000"] / 1000
    return total


def input_price(model: str, prices: dict) -> float:
    p = prices["models"][model]
    return max(p["input"], p.get("cache_write_5m", 0)) / 1_000_000


def output_price(model: str, prices: dict) -> float:
    return prices["models"][model]["output"] / 1_000_000


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

    def __init__(self, limit: float, spent: float, model: str, prices: dict, max_tokens: int = MAX_TOKENS):
        self.limit, self.spent, self.model, self.prices, self.max_tokens = limit, spent, model, prices, max_tokens

    @property
    def left(self) -> float:
        return self.limit - self.spent

    def fit(self, input_tokens: int, extra: float = 0.0) -> int:
        """max_tokens for a request with this many input tokens, or BudgetExhausted.
        input_tokens gets 5% and 100 tokens of margin, since a count is an estimate."""
        fixed = (input_tokens * 1.05 + 100) * input_price(self.model, self.prices) + extra
        room = int((self.left - fixed) / output_price(self.model, self.prices))
        if room < MIN_OUTPUT:
            raise BudgetExhausted(
                f"${self.spent:.4f} of ${self.limit:.2f} spent; a request with {input_tokens} input tokens "
                f"and {MIN_OUTPUT} output tokens could cost more than the ${self.left:.4f} left")
        return min(self.max_tokens, room)

    def add(self, usage: dict) -> float:
        c = cost(usage, self.model, self.prices)
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
        # Condition W of R13: web search, and web fetch to read the pages it finds and the URLs of the
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


# --------------------------------------------------------------------------- a run

@dataclass
class Result:
    id: str
    type: str
    expect: list
    outcome: str  # accept, reject, alias, human; "" if the case did not run
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


def spent_before(out: Path) -> float:
    total = 0.0
    for path in out.glob("*/results.jsonl"):
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
        # R13: memory and web judge without fetched texts, so they get their own system prompt.
        prompt = R13_PROMPT
    if not prompt.is_file():
        raise SystemExit(f"No prompt at {prompt}")
    system = load_system_prompt(prompt)
    log(f"prompt: {prompt}")
    schema = load_schema(Path(args.schema))
    prices = load_prices(Path(args.prices))
    if args.model not in prices["models"]:
        raise SystemExit(f"No price for model {args.model!r} in {args.prices}")
    web = args.condition == "web"
    out = Path(args.out)
    before = spent_before(out)

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
        log("Dry run: nothing was sent. Add --live to call the API.")
        return [], None
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
    run_dir = out / f"{stamp}-{args.condition}-{args.model}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "run.json").write_text(json.dumps({
        "condition": args.condition, "model": args.model, "effort": args.effort, "max_tokens": args.max_tokens,
        "max_uses": args.max_uses, "budget": args.budget, "spent_before": round(before, 6),
        "cases": [c.id for c in cases], "testset": str(args.testset), "prompt": str(prompt), "started": stamp,
    }, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
            gh.write(f"run_dir={run_dir}\nrun_name={run_dir.name}\n")

    api = api_schema(schema)
    budget = Budget(args.budget, before, args.model, prices, args.max_tokens)
    largest_web = largest_before(out, args.model, args.condition) if web else 0.0
    results, stopped, fetched = [], "", {}
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as jsonl:
        for case in cases:
            st = staged[case.id]
            res = Result(id=case.id, type=case.type, expect=case.expect, outcome="", by="", rules=case.rules,
                         alias_of=case.alias_of, lint=st.lint_seen)
            if not st.lint_ok:
                res.by, res.error = "invalid", f"the lint gives {st.lint_seen}, the case says {case.lint}"
            elif st.lint_seen.startswith("error"):
                res.by, res.outcome, res.rule = "code", "reject", st.lint_seen.split()[1]
            else:
                sources = gather_sources(fetcher, st) if args.condition == "fetched" else []
                for s in sources:
                    fetched[source_key(s.url, s.product) + (f" | for {s.instead_of}" if s.instead_of else "")] = asdict(s)
                res.sources = [[s.url, s.status] for s in sources]
                gone = product_gone(st.files, sources)
                if gone:
                    # The page that should show the product does not exist: rejected on S7 by code.
                    res.by, res.outcome, res.rule, res.error = "code", "reject", "S7", gone
                    res.right = res.outcome in case.expect
                    log(f"{case.id}: reject (code, {gone}), $0.0000; spent ${budget.spent:.4f} of ${args.budget:.2f}")
                    results.append(res)
                    jsonl.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
                    jsonl.flush()
                    continue
                spent_at_start = budget.spent
                try:
                    text, stop, usage = review(client, budget, model=args.model, system=system,
                                               message=build_message(st, args.condition, sources), web=web,
                                               effort=args.effort, max_uses=args.max_uses, schema=api, largest_web=largest_web)
                except BudgetExhausted as exc:
                    res.cost = budget.spent - spent_at_start  # a resumed request may have run before the stop
                    if res.cost:
                        res.by, res.outcome, res.error = "error", "human", f"stopped by the budget: {exc}"
                        res.right = res.outcome in case.expect
                        results.append(res)
                        jsonl.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
                    stopped = f"Stopped before {case.id}: {exc}. {len(cases) - len(results)} cases did not run."
                    log(stopped)
                    break
                except Exception as exc:  # noqa: BLE001 - any failure is recorded, the run goes on
                    res.by, res.outcome, res.error = "error", "human", f"{type(exc).__name__}: {exc}"
                    res.cost = budget.spent - spent_at_start
                else:
                    res.usage, res.cost = usage, budget.spent - spent_at_start
                    if web:
                        largest_web = max(largest_web, res.cost)
                    verdict, why = parse_reply(text, schema) if stop != "refusal" else (None, "the model refused")
                    if verdict is None:
                        res.by, res.outcome, res.error = "error", "human", why
                    else:
                        res.by, res.verdict, res.duplicate_of = "ai", verdict, verdict.get("duplicate_of")
                        # The quote check is the code part of S8; R13 switches it off in memory and web.
                        checked, res.quote_notes = check_quotes(verdict, sources) if args.condition == "fetched" else (verdict, [])
                        res.checked = {r: checked[r]["verdict"] for r in RULES}
                        res.outcome, res.rule = outcome(checked)
            res.right = res.outcome in case.expect
            log(f"{case.id}: {res.outcome or '-'} ({res.by}){'' if res.right else '  WRONG'}, ${res.cost:.4f}; "
                f"spent ${budget.spent:.4f} of ${args.budget:.2f}")
            results.append(res)
            jsonl.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
            jsonl.flush()

    (run_dir / "sources.json").write_text(json.dumps(fetched, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if hasattr(fetcher, "close"):
        fetcher.close()
    summary = summarise(results, run_dir.name, args.condition, args.budget, before, stopped)
    (run_dir / "summary.md").write_text(summary, encoding="utf-8")
    log(summary)
    return results, run_dir


# --------------------------------------------------------------------------- scoring (README § Scoring, r13-research.md)

def metrics(results: list[Result]) -> dict:
    ran = [r for r in results if r.outcome]
    good = [r for r in ran if r.id.startswith("G")]
    bad = [r for r in ran if r.id.startswith("B")]
    called = [r for r in results if r.usage]
    right_rule = {}  # on a bad case: did one of its rules say no (or the lint rule stop it)?
    for r in bad:
        for rule in r.rules:
            hit = (r.by == "code" and r.rule == rule) or r.checked.get(rule) == "no"
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
        "human": [r.id for r in ran if r.outcome == "human"],
        "human_closed": [r.id for r in ran if r.outcome == "human" and source_closed(r)],
        "human_doubt": [r.id for r in ran if r.outcome == "human" and not source_closed(r)],
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
        "r13": r13_metrics(results),
    }


def source_closed(r: Result) -> bool:
    """A source of the case was closed: refused, disallowed, an error such as a timeout, or not fetched."""
    return any(status in CLOSED or status.startswith("error") or status == "not fetched" for _, status in r.sources)


def r13_metrics(results: list[Result]) -> dict:
    """Per rule: right, judged and doubt; and the confident-and-wrong answers (r13-research.md)."""
    judged = [r for r in results if r.by == "ai"]
    per_rule, confident_wrong = {}, []
    for rule in R13_RULES:
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
        f"Condition **{condition}**. {m['cases']} cases: {m['by_code']} stopped by the lint, {m['calls']} sent to the referee.",
        "",
        *([f"**{stopped}**", ""] if stopped else []),
        "| measure | value |",
        "| --- | --- |",
        f"| bad cases let through (target 0) | {len(m['let_through'])} |",
        f"| good cases rejected | {len(m['good_rejected'])} |",
        f"| cases for a person (human) | {len(m['human'])} |",
        f"| human because a source is closed (refused, disallowed, error) | {len(m['human_closed'])} |",
        f"| human because the referee doubts | {len(m['human_doubt'])} |",
        f"| outcome in `expect` | {m['right']} of {m['ran']} |",
        f"| failed calls or unusable replies | {len(m['errors'])} |",
        f"| cases whose lint does not match | {len(m['invalid'])} |",
        f"| cost per case sent to the referee | ${m['cost_per_call']:.4f} (max ${m['cost_per_call_max']:.4f}) |",
        f"| cost of this run | ${m['cost_total']:.4f} |",
        f"| spent on all runs, budget | ${before + m['cost_total']:.4f} of ${budget:.2f} |",
        "",
        f"Let through: {ids(m['let_through'])}  ",
        f"Good rejected: {ids(m['good_rejected'])}  ",
        f"Human, a source closed: {ids(m['human_closed'])}  ",
        f"Human, the referee doubts: {ids(m['human_doubt'])}  ",
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
        "## Per rule (R13)",
        "",
        "Right: a good case answered yes, or a bad case answered no on one of its rules.",
        "",
        "| rule | right | wrong | doubt |",
        "| --- | --- | --- | --- |",
        *[f"| {rule} | {v['right']} | {v['wrong']} | {v['doubt']} |" for rule, v in m["r13"]["per_rule"].items()],
        "",
        f"Confident and wrong: {ids(m['r13']['confident_wrong'])}",
        "",
    ]
    t = m["tokens"]
    lines += [f"Tokens: {t['input_tokens']} input, {t['cache_creation_input_tokens']} cache write, "
              f"{t['cache_read_input_tokens']} cache read, {t['output_tokens']} output; {t['web_search_requests']} web searches, "
              f"{t.get('web_fetch_requests', 0)} web fetches.", ""]
    return "\n".join(lines)


def load_results(run_dir: Path) -> list[Result]:
    lines = (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    return [Result(**json.loads(line)) for line in lines if line.strip()]


def compare(dirs: list, show: list[str] | None = None) -> str:
    """R13: the conditions side by side, on the cases they share. An entry may be a list of run
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
             "| run | wrongly accepted | wrongly rejected | doubt | " + " | ".join(f"{r} right/wrong/doubt" for r in R13_RULES)
             + " | confident and wrong | cost per case | searches | fetches |",
             "| --- " * (8 + len(R13_RULES)) + "|"]
    for name, by_id in runs.items():
        rs = [by_id[i] for i in shared]
        m = metrics(rs)
        per = m["r13"]["per_rule"]
        lines.append(f"| {name} | {len(m['let_through'])} | {len(m['good_rejected'])} | {len(m['human'])} | "
                     + " | ".join(f"{per[r]['right']}/{per[r]['wrong']}/{per[r]['doubt']}" for r in R13_RULES)
                     + f" | {len(m['r13']['confident_wrong'])} | ${m['cost_per_call']:.4f} | {m['tokens']['web_search_requests']}"
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
    No key is needed or read: this step runs where the key is not (hosting plan §8)."""
    cases = select(load_cases(Path(args.testset)), args.cases, "fetched")
    if args.limit:
        cases = cases[: args.limit]
    stager = Stager(data_root)
    try:
        staged = [stager.stage(c) for c in cases]
    finally:
        stager.close()
    fetcher = fetcher or Fetcher(use_browser=not args.no_browser, log=log)
    data = {}
    if getattr(args, "search", None):
        # The second pass: read the URLs that step 4 found for refused sources, down steps 1-3.
        data = json.loads(Path(args.sources).read_text(encoding="utf-8"))
        found = json.loads(Path(args.search).read_text(encoding="utf-8"))
        try:
            for refused, hit in found.items():
                for url in hit["urls"]:
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


def registrable(host: str) -> str:
    """example.com from www.example.com; example.co.jp from support.example.co.jp."""
    labels = host.lower().strip(".").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in {"co", "com", "org", "net", "ac", "gov", "edu", "ne", "or"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


MAX_DOMAINS = 30


def search_domains(refused: str, staged: "Staged") -> list[str]:
    """The publisher's domains (run A2 review §1, point 6): of the closed URL, of the product's
    sources, and of every URL in the publisher's file and its other products in the data. Never
    a search without domains: sites of others do not count under S5."""
    hosts = [refused] + urls_of(staged.files) + list(staged.publisher_urls)
    out = []
    for h in hosts:
        if isinstance(h, str) and h.startswith(("http://", "https://")):
            d = registrable(urllib.parse.urlsplit(h).netloc)
            if d not in out:
                out.append(d)
    return out[:MAX_DOMAINS]


def search_urls(content) -> list[str]:
    """The URLs in the web search results of a response. The snippets are not kept: a fragment is
    never evidence (tax-weigerende-sites.md §3)."""
    urls = []
    for block in content:
        if getattr(block, "type", "") == "web_search_tool_result" and isinstance(getattr(block, "content", None), list):
            for r in block.content:
                url = getattr(r, "url", "")
                if url and url not in urls:
                    urls.append(url)
    return urls[:SEARCH_RESULTS]


def search_refused(args, client=None, data_root: Path = REPO, now=None, log=print) -> dict:
    """Step 4: for each refused or disallowed source, one web search on the publisher's domains. Costs money
    (about $0.01 a search plus tokens) and counts against the budget like a run."""
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
        log("Dry run: nothing was searched. Add --live to search (about $0.01 a search plus tokens).")
    if wanted and args.live:
        prices = load_prices(Path(args.prices))
        if client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise SystemExit("A live search needs ANTHROPIC_API_KEY")
            client = AnthropicClient()
        before = spent_before(out)
        budget = Budget(args.budget, before, args.model, prices, 2000)
        groups: dict[tuple, list[str]] = {}
        for url, want in wanted.items():
            query = f'"{want["product"]}" {want["publisher"]}'.strip()
            groups.setdefault((query, tuple(want["domains"])), []).append(url)
        log(f"{len(groups)} distinct searches for {len(wanted)} sources, with {args.model}")
        log(f"budget: ${args.budget:.2f} for all runs in {out}; spent before: ${before:.4f}")
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
        run_dir = out / f"{stamp}-search-{args.model}"
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "run.json").write_text(json.dumps({"condition": "search", "model": args.model, "budget": args.budget,
                                                      "spent_before": round(before, 6), "started": stamp}, indent=2) + "\n", encoding="utf-8")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
                gh.write(f"run_dir={run_dir}\nrun_name={run_dir.name}\n")
        with (run_dir / "results.jsonl").open("w", encoding="utf-8") as jsonl:
            for (query, domains), closed in groups.items():
                want = wanted[closed[0]]
                request = dict(
                    model=args.model,
                    messages=[{"role": "user", "content": SEARCH_PROMPT.format(query=query)}],
                    output_config={"effort": "low"},
                    tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 1, "allowed_domains": want["domains"]}],
                )
                spent_at_start = budget.spent
                try:
                    request["max_tokens"] = budget.fit(client.count(request), web_allowance(args.model, prices, 1))
                    response = client.create(request)
                except BudgetExhausted as exc:
                    log(f"Stopped before searching {query}: {exc}")
                    break
                except Exception as exc:  # noqa: BLE001 - a failed search leaves the source refused
                    log(f"search {query} failed: {type(exc).__name__}")
                    continue
                budget.add(response.usage)
                urls = [u for u in search_urls(response.content) if u not in closed]
                hit = {"product": want["product"], "query": query, "domains": want["domains"], "urls": urls}
                for url in closed:
                    found[url] = hit
                cost_now = budget.spent - spent_at_start
                jsonl.write(json.dumps({"id": "search", "for": closed, **hit, "usage": response.usage, "cost": cost_now}) + "\n")
                log(f"{query}: {len(urls)} URLs on {', '.join(want['domains'])} for {len(closed)} closed sources, "
                    f"${cost_now:.4f}; spent ${budget.spent:.4f} of ${args.budget:.2f}")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(found, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return found


# --------------------------------------------------------------------------- command line

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run cases past the referee")
    r.add_argument("--condition", choices=CONDITIONS, default="fetched")
    r.add_argument("--cases", nargs="*", help="case ids, or 'all'; default: all for fetched, the 29 R13 cases otherwise")
    r.add_argument("--limit", type=int, help="only the first N of those cases")
    r.add_argument("--model", default="claude-opus-5-5")
    r.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), default="medium")
    r.add_argument("--max-tokens", type=int, default=MAX_TOKENS, help="per request; the budget may lower it")
    r.add_argument("--max-uses", type=int, default=3, help="web: most searches per case")
    r.add_argument("--budget", type=float, default=20.00, help="dollars, for all runs in --out together")
    r.add_argument("--live", action="store_true", help="call the API; without it nothing is sent")
    for name, default in (("testset", REFEREE / "testset/cases.yaml"), ("prompt", REFEREE / "prompt.md"),
                          ("schema", REFEREE / "verdict.schema.json"), ("prices", REFEREE / "prices.yaml"),
                          ("out", REFEREE / "runs")):
        r.add_argument(f"--{name}", default=str(default))
    r.add_argument("--sources", help="a sources.json made by `fetch`; the run then fetches nothing itself")
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
    s = sub.add_parser("search", help="step 4: search for another open source for each refused source (costs money)")
    s.add_argument("--sources", required=True, help="the sources.json made by `fetch`")
    s.add_argument("--output", default="search.json")
    s.add_argument("--testset", default=str(REFEREE / "testset/cases.yaml"))
    s.add_argument("--cases", nargs="*")
    s.add_argument("--limit", type=int)
    s.add_argument("--model", default="claude-sonnet-5-5", help="it only returns URLs and judges nothing (run A2 review §1)")
    s.add_argument("--budget", type=float, default=20.00)
    s.add_argument("--prices", default=str(REFEREE / "prices.yaml"))
    s.add_argument("--out", default=str(REFEREE / "runs"))
    s.add_argument("--live", action="store_true", help="search; without it nothing is sent")
    c = sub.add_parser("compare", help="compare runs on the cases they share (R13)")
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
    else:
        run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
