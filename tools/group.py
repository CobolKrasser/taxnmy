#!/usr/bin/env python3
"""Grouping names per publisher (TAX-26): one judgment sorts every name a contributor gives for one
publisher, so that one product gets one key and the other writings become aliases.

A dataset of others writes a product per record: one product gets several names, an edition or a
series stands as a product. Judged one proposal at a time, every name looks right on its own. Here
code collects, per publisher, every name given and the products TAX already has of it, and one
request sorts each name into a class (referee/prompt-group.md):

    product   a product of its own: a candidate, judged later by the referee like any proposal
    alias     another writing of a product named in the request: an existing one, or a name classed product
    edition   an edition or a version of a product named in the request: not a product of its own (TP3)
    series    several products at once, a family, or a part without an update stream of its own
    doubt     the names do not settle it (TR10)

Code decides what it can show literally (TV2): names that give the same key are one name, and a
name that gives the key or an alias of an existing product of the publisher is that product. Code
checks the answer: every name answered once, and every alias or edition points to an existing key
or to a name classed product; otherwise the name becomes doubt.

Usage:

    python tools/group.py collect --kev KEV.json --cve DIR [--since DATE] [--publishers MAP] --output names.json
    python tools/group.py collect --candidates candidates.yaml --output names.json
    python tools/group.py run                                # dry run of the test set: nothing is sent
    python tools/group.py run --live --repeats 3             # the measurement, as one batch

The test set is referee/testset/groups.yaml: publishers from the KEV data, each name with the class
a person gave it. A live run refuses a test set whose publishers the tester has not checked. The
budget is that of tools/referee.py: a hard limit for every run in referee/runs together.

Nothing is sent without --live. The key is read from ANTHROPIC_API_KEY and never written anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keyrule  # noqa: E402
import referee  # noqa: E402

REPO = referee.REPO
REFEREE = referee.REFEREE
CLASSES = ("product", "alias", "edition", "series", "doubt")
POINTS = ("alias", "edition")  # classes that name the product they belong to
MODEL = "claude-opus-5-5"  # the referee of TAX-18 (Opus 5.5 at effort low, since 17 November 2026)
EFFORT = "low"
MAX_TOKENS = 8000
MAX_VERSIONS = 4  # per name, in the message
MAX_RECORDS = 5  # per name, in the message
NONE_NAMES = {"n/a", "na", "none", "unknown", "-", ""}  # what a record writes when it names no product
CHARS_PER_TOKEN = 3.5  # a dry run's estimate of the input tokens
OUTPUT_PER_NAME = 70  # a dry run's estimate of the output tokens per name, at effort low
OUTPUT_FIXED = 300


# --------------------------------------------------------------------------- collecting

@dataclass
class Name:
    id: str
    name: str  # the first spelling, as given
    spellings: list = field(default_factory=list)  # other spellings that give the same key
    seen: dict = field(default_factory=dict)  # origin -> number of records
    records: list = field(default_factory=list)
    versions: list = field(default_factory=list)
    together: list = field(default_factory=list)  # names given as separate entries in one record


@dataclass
class Group:
    publisher: str  # the key of the publisher in TAX, or derived from its name
    publisher_name: str
    known: bool  # the publisher is already in TAX
    names: list  # [Name]
    existing: list = field(default_factory=list)  # [{"key", "name", "aliases"}]


def part(name: str) -> str:
    """The key part of a product name by the writing rule, or the name folded when it has none."""
    try:
        return keyrule.product_part(name)
    except keyrule.KeyRuleError:
        return re.sub(r"\s+", " ", name).strip().casefold()


def publisher_index(data_root: Path) -> dict:
    """Publisher key, and the key of its name and of each alias, -> the key in TAX."""
    index = {}
    for path in sorted((data_root / "publishers").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        key = data.get("key")
        if not key:
            continue
        for value in [key, *data.get("aliases", [])]:
            index.setdefault(value, key)
        try:
            index.setdefault(keyrule.publisher_part(data.get("name", "")), key)
        except keyrule.KeyRuleError:
            pass
    return index


def publisher_key(name: str, index: dict, mapping: dict) -> tuple[str, bool]:
    """(the key, already in TAX). A mapping given by the contributor comes first; then the key of the
    name, compared character by character with the publishers in TAX (TV2)."""
    if name in mapping:
        key = mapping[name]
        return key, key in index.values()
    try:
        derived = keyrule.publisher_part(name)
    except keyrule.KeyRuleError:
        derived = part(name)
    if derived in index:
        return index[derived], True
    return derived, False


def existing_products(data_root: Path, publisher: str, only: list | None = None) -> list:
    """The products TAX has of this publisher. With `only`, just those keys (a test set freezes what
    it shows); a key in `only` that TAX does not have is an error."""
    out = []
    folder = data_root / "products" / publisher
    for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if only is None or data.get("key") in only:
            out.append({"key": data["key"], "name": data.get("name", ""), "aliases": list(data.get("aliases", []))})
    if only is not None:
        missing = sorted(set(only) - {p["key"] for p in out})
        if missing:
            raise SystemExit(f"{publisher}: the test set shows products TAX does not have: {', '.join(missing)}")
    return out


def collect(candidates: list, data_root: Path = REPO, mapping: dict | None = None) -> list[Group]:
    """Candidates [{publisher, name, record?, origin?, versions?}] -> one Group per publisher.
    Names that give the same key are one Name; the record of each candidate says which names were
    given together."""
    index, mapping = publisher_index(data_root), mapping or {}
    groups: dict = {}
    by_record: dict = {}
    seen_pairs: dict = {}
    for c in candidates:
        name = str(c.get("name", "")).strip()
        if name.casefold() in NONE_NAMES:
            continue
        key, known = publisher_key(str(c["publisher"]).strip(), index, mapping)
        g = groups.setdefault(key, {"name": str(c["publisher"]).strip(), "known": known, "names": {}})
        p = part(name)
        n = g["names"].get(p)
        if n is None:
            n = g["names"][p] = Name(id="", name=name)
        elif name != n.name and name not in n.spellings:
            n.spellings.append(name)
        origin = c.get("origin", "given")
        record = c.get("record", "")
        if record and record not in n.records:
            n.records.append(record)
        pairs = seen_pairs.setdefault((key, p), set())
        if not record or (origin, record) not in pairs:
            pairs.add((origin, record))
            n.seen[origin] = n.seen.get(origin, 0) + 1
        for v in c.get("versions", []) or []:
            if v and v not in n.versions:
                n.versions.append(str(v))
        if record:
            by_record.setdefault((key, record), []).append((origin, p))
    out = []
    for key in sorted(groups):
        g = groups[key]
        names = sorted(g["names"].items(), key=lambda kv: kv[1].name.casefold())
        for i, (_, n) in enumerate(names, 1):
            n.id = f"n{i}"
        ids = {p: n.id for p, n in names}
        for (pub, _), entries in by_record.items():
            if pub != key:
                continue
            # Together: separate entries of one record from the same origin (a record that lists two
            # affected products), never a name against the one another dataset gave for the record.
            by_origin: dict = {}
            for origin, p in entries:
                by_origin.setdefault(origin, set()).add(p)
            for parts in by_origin.values():
                for p in parts:
                    for q in parts - {p}:
                        n = g["names"][p]
                        if ids[q] not in n.together:
                            n.together.append(ids[q])
        for _, n in names:
            n.together.sort(key=lambda i: int(i[1:]))
        existing = existing_products(data_root, key) if g["known"] else []
        out.append(Group(publisher=key, publisher_name=g["name"], known=g["known"], names=[n for _, n in names],
                         existing=existing))
    return out


def from_kev(kev: dict, cve_dir: Path, since: str = "") -> list:
    """Candidates from the KEV list of CISA and the CVE records (CVE List v5) of its entries: the
    product KEV writes, and each product the CNA of the record lists as affected, with its versions.
    All go to the vendor KEV names for the entry. Names from ADP containers are left out: they are
    CPE parts or another party's products, not a name the record's author wrote."""
    out = []
    for entry in kev.get("vulnerabilities", []):
        if since and entry.get("dateAdded", "") < since:
            continue
        cve, vendor = entry["cveID"], entry["vendorProject"]
        out.append({"publisher": vendor, "name": entry["product"], "record": cve, "origin": "kev"})
        path = cve_dir / f"{cve}.json"
        if not path.is_file():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        for affected in record.get("containers", {}).get("cna", {}).get("affected", []):
            versions = []
            for v in affected.get("versions", []):
                for k in ("version", "lessThan", "lessThanOrEqual"):
                    if v.get(k) and v[k] not in ("0", "*", "n/a"):
                        versions.append(v[k])
            out.append({"publisher": vendor, "name": affected.get("product", ""), "record": cve, "origin": "cna",
                        "versions": versions})
    return out


def groups_json(groups: list[Group]) -> dict:
    return {"groups": [asdict(g) for g in groups]}


# --------------------------------------------------------------------------- the test set

@dataclass
class Expect:
    classes: list  # the classes a person accepts for this name; the first is the one meant
    of: list  # per class: the product it belongs to (an existing key or a name), or None


@dataclass
class Case:
    id: str
    group: Group
    expect: dict  # name id -> Expect
    status: str  # proposed, or checked once the tester confirmed every name
    note: str = ""


def load_testset(path: Path, data_root: Path = REPO) -> list[Case]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = []
    for p in data["publishers"]:
        pub = p["publisher"]
        known = (data_root / "publishers" / f"{pub}.yaml").is_file()
        existing = existing_products(data_root, pub, p.get("existing", [])) if known else []
        if not known and p.get("existing"):
            raise SystemExit(f"{p['id']}: {pub} is not in TAX, but the case shows its products")
        names, expect, by_name = [], {}, {}
        for i, n in enumerate(p["names"], 1):
            name = Name(id=f"n{i}", name=n["name"], spellings=list(n.get("spellings", [])), seen=dict(n.get("seen", {})),
                        records=list(n.get("records", [])), versions=list(n.get("versions", [])))
            names.append(name)
            by_name[n["name"]] = name.id
        for i, n in enumerate(p["names"], 1):
            names[i - 1].together = [by_name[t] for t in n.get("together", [])]
        keys = {e["key"] for e in existing}
        for i, n in enumerate(p["names"], 1):
            classes = n["expect"] if isinstance(n["expect"], list) else [n["expect"]]
            ofs = n.get("of")
            ofs = ofs if isinstance(ofs, list) else [ofs] * len(classes)
            if len(ofs) != len(classes):
                raise SystemExit(f"{p['id']} {n['name']}: one 'of' per class in expect")
            resolved = []
            for c, o in zip(classes, ofs):
                if c not in CLASSES:
                    raise SystemExit(f"{p['id']} {n['name']}: unknown class {c!r}")
                if c in POINTS and not o:
                    raise SystemExit(f"{p['id']} {n['name']}: {c} needs 'of'")
                if o and o not in keys and o not in by_name:
                    raise SystemExit(f"{p['id']} {n['name']}: 'of' {o!r} is neither a product shown nor a name of the case")
                resolved.append(o if (not o or o in keys) else by_name[o])
            expect[f"n{i}"] = Expect(classes=classes, of=resolved)
        group = Group(publisher=pub, publisher_name=p["name"], known=known, names=names, existing=existing)
        cases.append(Case(id=p["id"], group=group, expect=expect, status=p.get("status", "proposed"),
                          note=p.get("note", "")))
    return cases


# --------------------------------------------------------------------------- what code decides

def literal(group: Group) -> dict:
    """TV2: a name whose key part is that of an existing product of the publisher, of one of its
    aliases, or of its name, is that product. name id -> {"class": "alias", "of": key, "reason"}."""
    parts = {}
    for e in group.existing:
        for value in [e["key"], *e["aliases"]]:
            parts.setdefault(value.split("/", 1)[-1], e["key"])
        parts.setdefault(part(e["name"]), e["key"])
    out = {}
    for n in group.names:
        for spelling in [n.name, *n.spellings]:
            key = parts.get(part(spelling))
            if key:
                out[n.id] = {"class": "alias", "of": key,
                             "reason": f"code: {spelling!r} gives the key of {key}, or of its name or an alias"}
                break
    return out


# --------------------------------------------------------------------------- the message

MARKERS = re.compile(r"<(?=\s*/?\s*(?:publisher|existing|decided|names)\b)", re.I)


def inert(text: str) -> str:
    """TV1: a name cannot write the markers of the message."""
    return MARKERS.sub("&lt;", str(text))


def build_message(group: Group, decided: dict) -> str:
    pub = (f"key: {group.publisher}\nname: {inert(group.publisher_name)}\n"
           f"in TAX: {'yes' if group.known else 'no'}")
    existing = "\n".join(
        f"- {e['key']}: {inert(e['name'])}" + (f" (aliases: {', '.join(e['aliases'])})" if e["aliases"] else "")
        for e in group.existing) or "none"
    lines = []
    for n in group.names:
        lines.append(f"{n.id}: {inert(n.name)}")
        if n.spellings:
            lines.append("  also written: " + "; ".join(inert(s) for s in n.spellings))
        if n.seen:
            lines.append("  seen: " + ", ".join(f"{o} {c}" for o, c in sorted(n.seen.items())))
        if n.versions:
            lines.append("  versions: " + "; ".join(inert(v) for v in n.versions[:MAX_VERSIONS]))
        if n.together:
            lines.append("  together with: " + ", ".join(n.together))
    dec = "\n".join(f"{i}: {d['class']} of {d['of']} ({d['reason']})" for i, d in sorted(decided.items(), key=_order))
    asked = [n.id for n in group.names if n.id not in decided]
    return (f"<publisher>\n{pub}\n</publisher>\n\n<existing>\n{existing}\n</existing>\n\n"
            f"<decided>\n{dec or 'none'}\nAnswer: {', '.join(asked) or 'none'}\n</decided>\n\n"
            f"<names>\n" + "\n".join(lines) + "\n</names>\n")


def _order(item) -> int:
    return int(item[0][1:])


def load_schema(path: Path = REFEREE / "group.schema.json") -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def request(group: Group, decided: dict, system: str, schema: dict, model: str, effort: str) -> dict:
    return dict(
        model=model,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": build_message(group, decided)}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": referee.api_schema(schema)}},
    )


# --------------------------------------------------------------------------- the answer

def check_answer(group: Group, text: str, schema: dict, decided: dict) -> tuple[dict, list]:
    """The classes per name id, after code checked them, and what code changed. A name that is not
    answered, answered twice, or that points to something that is not a product of the request,
    becomes doubt."""
    notes = []
    try:
        answer = json.loads(text)
        jsonschema.validate(answer, schema)
    except (json.JSONDecodeError, jsonschema.ValidationError) as e:
        notes.append(f"the answer is unusable: {str(e).splitlines()[0][:200]}")
        answer = {"names": []}
    ids = [n.id for n in group.names]
    keys = {e["key"] for e in group.existing}
    out = {i: dict(d) for i, d in decided.items()}
    given: dict = {}
    for item in answer["names"]:
        i = item.get("id")
        if i not in ids:
            notes.append(f"{i}: not a name of the request")
            continue
        if i in decided:
            notes.append(f"{i}: decided by code; the answer is not used")
            continue
        if i in given:
            notes.append(f"{i}: answered twice; doubt")
            given[i] = {"class": "doubt", "of": None, "reason": "answered twice"}
            continue
        given[i] = {"class": item["class"], "of": item.get("of"), "reason": item.get("reason", "")}
    for i in ids:
        if i not in out and i not in given:
            notes.append(f"{i}: not answered; doubt")
            given[i] = {"class": "doubt", "of": None, "reason": "not answered"}
    products = {i for i, d in given.items() if d["class"] == "product"}
    for i, d in given.items():
        if d["class"] in POINTS:
            if d["of"] not in keys and d["of"] not in products:
                notes.append(f"{i}: {d['class']} of {d['of']!r}, which is neither an existing key nor a name classed product; doubt")
                d = {"class": "doubt", "of": None, "reason": d["reason"]}
        elif d["class"] != "series":
            d = {**d, "of": None}
        elif d["of"] is not None and d["of"] not in keys and d["of"] not in products:
            d = {**d, "of": None}
        out[i] = d
    return out, notes


def outcome(cls: str) -> str:
    """What follows from a class (TAX-27): a product is a candidate for the referee; an alias is
    added to the product it names; the rest is parked with the reason."""
    return {"product": "candidate", "alias": "alias"}.get(cls, "parked")


# --------------------------------------------------------------------------- the comparison with the hand

def identity(i: str, cls: str, of: str | None) -> str | None:
    """The product a name stands for: itself when it is a product, the product it names when it is an
    alias or an edition (an edition gets no key of its own), none when it is a series or a doubt."""
    if cls == "product":
        return i
    if cls in POINTS:
        return of
    return None


def _pick(options: list, cls: str, keys: set) -> tuple[tuple, bool]:
    """The option of the hand the answer is compared with, and whether the class counts as right.
    First the same class; then, for an answer that points to a product, an option of the hand that
    points too; then product and alias as one, for a product the hand gives no existing key (which
    name of a group the answer makes the product, and which the aliases, is free); else the first."""
    for opt in options:
        if opt[0] == cls:
            return opt, True
    if cls in POINTS:
        for opt in options:
            if opt[0] in POINTS:
                return opt, False
    if cls in ("product", "alias"):
        for opt in options:
            if opt[0] in ("product", "alias") and opt[1] not in keys:
                return opt, True
    return options[0], False


def compare(case: Case, classes: dict) -> dict:
    """The answer against the hand, as a partition of the names into products.
    A merge: one product of the answer holds names, or an existing product, that the hand gives to
    two different products. A split: one product of the hand is spread over two of the answer.
    Which name of a group is the product and which the aliases is free. Per name, an error is a
    class the hand does not accept ('hand -> answer'), or a place in a merge or a split."""
    keys = {e["key"] for e in case.group.existing}
    hand_id, model_id, class_ok, picked = {}, {}, {}, {}
    for n in case.group.names:
        e, got = case.expect[n.id], classes[n.id]
        options = [(c, identity(n.id, c, o)) for c, o in zip(e.classes, e.of)]
        opt, ok = _pick(options, got["class"], keys)
        hand_id[n.id], model_id[n.id] = opt[1], identity(n.id, got["class"], got.get("of"))
        class_ok[n.id], picked[n.id] = ok, opt
    for k in keys:
        hand_id[k] = model_id[k] = k
    merges, splits, in_merge, in_split = [], [], set(), set()
    by_model: dict = {}
    for x, m in model_id.items():
        if m is not None and hand_id[x] is not None:
            by_model.setdefault(m, {}).setdefault(hand_id[x], []).append(x)
    for m, hands in sorted(by_model.items()):
        if len(hands) > 1:
            merges.append({"answer": m, "hand": sorted(hands)})
            in_merge.update(x for xs in hands.values() for x in xs)
    by_hand: dict = {}
    for x, h in hand_id.items():
        if h is not None and model_id[x] is not None:
            by_hand.setdefault(h, {}).setdefault(model_id[x], []).append(x)
    for h, models in sorted(by_hand.items()):
        if len(models) > 1:
            splits.append({"hand": h, "answer": sorted(models)})
            in_split.update(x for xs in models.values() for x in xs)
    wrong, kinds = [], Counter()
    for n in case.group.names:
        got, opt = classes[n.id], picked[n.id]
        if not class_ok[n.id]:
            kind = f"{opt[0]} -> {got['class']}"
        elif n.id in in_merge:
            kind = "merged"
        elif n.id in in_split:
            kind = "split"
        else:
            continue
        kinds[kind] += 1
        wrong.append({"id": n.id, "name": n.name, "kind": kind,
                      "hand": opt[0] + (f" of {opt[1]}" if opt[0] in POINTS else ""),
                      "answer": got["class"] + (f" of {got.get('of')}" if got["class"] in POINTS else ""),
                      "reason": got.get("reason", "")})
    return {"names": len(case.group.names), "wrong": wrong, "kinds": dict(kinds), "merges": merges, "splits": splits,
            "doubts": sum(1 for d in classes.values() if d["class"] == "doubt")}


# --------------------------------------------------------------------------- a run

def estimate(cases: list[Case], system: str, model: str, prices: dict, repeats: int, max_tokens: int) -> dict:
    p_in, p_out = referee.input_price(model, prices, True), referee.output_price(model, prices, True)
    pm = prices["models"][model]
    d = referee.discount(prices, True)
    sys_tokens = len(system) / CHARS_PER_TOKEN
    total_in = total_out = 0.0
    expected = 0.0
    for c in cases:
        msg = len(build_message(c.group, literal(c.group))) / CHARS_PER_TOKEN
        out = OUTPUT_FIXED + OUTPUT_PER_NAME * len(c.group.names)
        total_in += sys_tokens + msg
        total_out += out
        # The system prompt is read from the cache after the first request of a batch, at best.
        expected += (msg * pm["input"] + sys_tokens * pm["cache_read"]) / 1_000_000 * d + out * p_out
    ceiling = (total_in * p_in + len(cases) * max_tokens * p_out)
    return {"publishers": len(cases), "names": sum(len(c.group.names) for c in cases), "input": round(total_in),
            "output": round(total_out), "expected": expected * repeats, "ceiling": ceiling * repeats,
            "per_publisher": expected / max(len(cases), 1)}


def run(args, client=None, data_root: Path = REPO, now=None, log=print, sleep=None) -> dict:
    cases = load_testset(Path(args.testset), data_root)
    if args.cases:
        cases = [c for c in cases if c.id in args.cases]
    system = referee.load_system_prompt(Path(args.prompt))
    schema = load_schema(Path(args.schema))
    prices = referee.load_prices(Path(args.prices))
    out = Path(args.out)
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    est = estimate(cases, system, args.model, prices, args.repeats, args.max_tokens)
    log(f"{est['publishers']} publishers, {est['names']} names; {args.repeats} runs as a batch on {args.model} "
        f"at {args.effort}: about ${est['expected']:.2f}, at most ${est['ceiling']:.2f}")
    if not args.live:
        run_dir = out / f"{stamp}-group-dry"
        run_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"# Grouping, dry run {stamp}", "", "Nothing was sent.", "",
                 f"- publishers: {est['publishers']}; names: {est['names']}",
                 f"- estimate for {args.repeats} runs as a batch on {args.model} at {args.effort}: "
                 f"about ${est['expected']:.2f} (${est['per_publisher']:.4f} per publisher per run), "
                 f"at most ${est['ceiling']:.2f}", "",
                 "| case | publisher | names | decided by code | not checked by the tester |", "| --- | --- | --- | --- | --- |"]
        for c in cases:
            lines.append(f"| {c.id} | {c.group.publisher} | {len(c.group.names)} | {len(literal(c.group))} | "
                         f"{'yes' if c.status != 'checked' else ''} |")
        (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if args.show:
            for c in cases:
                if c.id in args.show:
                    log(build_message(c.group, literal(c.group)))
        return {"dir": run_dir, "estimate": est}
    unchecked = [c.id for c in cases if c.status != "checked"]
    if unchecked and not args.unchecked:
        raise SystemExit("The tester has not checked the hand of " + ", ".join(unchecked)
                         + "; a measurement against it would measure the builder. Set status: checked first.")
    client = client or referee.AnthropicClient()
    spent = referee.spent_before(out)
    budget = referee.Budget(args.budget, spent, args.model, prices, max_tokens=args.max_tokens, batch=True)
    log(f"budget ${args.budget:.2f}; spent before ${spent:.4f}")
    requests, decided = [], {}
    for r in range(1, args.repeats + 1):
        for c in cases:
            decided[c.id] = literal(c.group)
            requests.append((f"r{r}-{c.id}", request(c.group, decided[c.id], system, schema, args.model, args.effort)))
    answers, never = referee.batch_rounds(client, budget, requests, log=log, wait=args.wait, sleep=sleep)
    runs = {}
    for r in range(1, args.repeats + 1):
        run_dir = out / f"{stamp}-group-{args.model}-{args.effort}-run{r}"
        run_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for c in cases:
            cid = f"r{r}-{c.id}"
            a = answers.get(cid)
            if a is None or a["error"]:
                why = a["error"] if a else ("no room in the budget" if cid in never else "no answer")
                classes, notes = check_answer(c.group, "", schema, decided[c.id])
                notes.insert(0, why)
                a = a or {"usage": referee.empty_usage(), "cost": 0.0, "text": "", "stop": ""}
            else:
                classes, notes = check_answer(c.group, a["text"], schema, decided[c.id])
            cmp = compare(c, classes)
            rows.append({"id": c.id, "publisher": c.group.publisher, "classes": classes, "notes": notes,
                         "outcomes": {i: outcome(d["class"]) for i, d in classes.items()}, "compare": cmp,
                         "text": a["text"], "stop": a["stop"], "usage": a["usage"], "cost": a["cost"]})
        (run_dir / "results.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows),
                                               encoding="utf-8")
        (run_dir / "run.json").write_text(json.dumps({
            "kind": "group", "model": args.model, "effort": args.effort, "batch": True, "run": r,
            "repeats": args.repeats, "testset": args.testset, "prompt": args.prompt, "budget": args.budget,
            "spent_before": spent, "unchecked": unchecked, "time": stamp}, indent=2) + "\n", encoding="utf-8")
        (run_dir / "summary.md").write_text(summary(rows, run_dir.name, cases), encoding="utf-8")
        runs[r] = rows
        log(f"run {r}: {sum(len(x['compare']['merges']) for x in rows)} merges, "
            f"{sum(len(x['compare']['wrong']) for x in rows)} names classed otherwise than the hand, "
            f"${sum(x['cost'] for x in rows):.4f}")
    report = out / f"group-measurement-{stamp}.md"
    report.write_text(measurement_report(runs, cases, args), encoding="utf-8")
    log(f"report: {report}")
    if os.environ.get("GITHUB_OUTPUT"):
        # One artifact per run directory, as for any run, so that the budget counts each once.
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
            for r in runs:
                d = out / f"{stamp}-group-{args.model}-{args.effort}-run{r}"
                gh.write(f"dir_{r}={d}\nname_{r}={d.name}\n")
            gh.write(f"report={report}\n")
    return {"runs": runs, "report": report}


def totals(rows: list) -> dict:
    kinds = Counter()
    for x in rows:
        kinds.update(x["compare"]["kinds"])
    return {"merges": sum(len(x["compare"]["merges"]) for x in rows),
            "splits": sum(len(x["compare"]["splits"]) for x in rows),
            "wrong": sum(len(x["compare"]["wrong"]) for x in rows),
            "doubts": sum(x["compare"]["doubts"] for x in rows),
            "names": sum(x["compare"]["names"] for x in rows), "kinds": kinds,
            "cost": sum(x["cost"] for x in rows), "output": sum(x["usage"].get("output_tokens", 0) for x in rows)}


def summary(rows: list, name: str, cases: list[Case]) -> str:
    t = totals(rows)
    lines = [f"# Grouping {name}", "",
             f"- names classed otherwise than the hand: **{t['wrong']}** of {t['names']}",
             f"- merges (two products of the hand in one product of the answer): **{t['merges']}**",
             f"- splits (one product of the hand in two of the answer): {t['splits']}",
             f"- doubts (parked): {t['doubts']}",
             f"- cost: ${t['cost']:.4f}, ${t['cost'] / max(len(rows), 1):.4f} per publisher; {t['output']} output tokens", "",
             "## Per kind of error (hand -> answer)", ""]
    lines += [f"- {k}: {v}" for k, v in sorted(t["kinds"].items())] or ["- none"]
    lines += ["", "## Per publisher", "", "| case | publisher | names | otherwise | merges | splits | doubts | cost |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for x in rows:
        c = x["compare"]
        lines.append(f"| {x['id']} | {x['publisher']} | {c['names']} | {len(c['wrong'])} | {len(c['merges'])} | "
                     f"{len(c['splits'])} | {c['doubts']} | ${x['cost']:.4f} |")
    names = {c.id: {n.id: n.name for n in c.group.names} for c in cases}
    lines += ["", "## Merges and names classed otherwise", ""]
    for x in rows:
        label = names[x["id"]]
        for m in x["compare"]["merges"]:
            lines.append(f"- **merge** {x['id']}: {', '.join(label.get(h, h) for h in m['hand'])} "
                         f"in one product ({label.get(m['answer'], m['answer'])})")
        for w in x["compare"]["wrong"]:
            lines.append(f"- {x['id']} {w['id']} {w['name']!r}: hand {_named(w['hand'], label)}, "
                         f"answer {_named(w['answer'], label)}. {w['reason']}")
        for note in x["notes"]:
            lines.append(f"- {x['id']} code: {note}")
    return "\n".join(lines) + "\n"


def _named(text: str, label: dict) -> str:
    return re.sub(r"\bn\d+\b", lambda m: repr(label.get(m.group(0), m.group(0))), text)


def measurement_report(runs: dict, cases: list[Case], args) -> str:
    lines = [f"# Grouping per publisher (TAX-26): {len(runs)} runs", "",
             f"Model {args.model} at effort {args.effort}, as a batch; test set {args.testset}.", "",
             "| run | merges | splits | otherwise | doubts | cost | per publisher |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r, rows in runs.items():
        t = totals(rows)
        lines.append(f"| {r} | {t['merges']} | {t['splits']} | {t['wrong']} of {t['names']} | {t['doubts']} | "
                     f"${t['cost']:.4f} | ${t['cost'] / max(len(rows), 1):.4f} |")
    lines += ["", "## Per kind of error (hand -> answer), per run", ""]
    kinds = sorted({k for rows in runs.values() for k in totals(rows)["kinds"]})
    lines += ["| kind | " + " | ".join(f"run {r}" for r in runs) + " |", "| --- |" + " --- |" * len(runs)]
    for k in kinds:
        lines.append(f"| {k} | " + " | ".join(str(totals(rows)["kinds"].get(k, 0)) for rows in runs.values()) + " |")
    lines += ["", "## Cost per publisher", "", "| case | publisher | names | " + " | ".join(f"run {r}" for r in runs) + " |",
              "| --- | --- | --- |" + " --- |" * len(runs)]
    for c in cases:
        costs = [next((x["cost"] for x in rows if x["id"] == c.id), 0.0) for rows in runs.values()]
        lines.append(f"| {c.id} | {c.group.publisher} | {len(c.group.names)} | " + " | ".join(f"${v:.4f}" for v in costs) + " |")
    norm = all(totals(rows)["merges"] == 0 for rows in runs.values())
    lines += ["", f"**Norm (no run merges two different products): {'met' if norm else 'NOT met'}.**", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- the command line

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="names per publisher, with the products TAX has of it (free)")
    c.add_argument("--candidates", help="a YAML or JSON list of {publisher, name, record?, origin?, versions?}")
    c.add_argument("--kev", help="the KEV list of CISA (known_exploited_vulnerabilities.json)")
    c.add_argument("--cve", help="a folder of CVE records (CVE List v5), one CVE-YYYY-N.json per entry")
    c.add_argument("--since", default="", help="only KEV entries added on or after this date (YYYY-MM-DD)")
    c.add_argument("--vendors", nargs="*", help="only these vendors, as KEV writes them")
    c.add_argument("--publishers", help="a YAML map from a publisher as the candidates write it to its key in TAX")
    c.add_argument("--output", required=True)
    r = sub.add_parser("run", help="the test set past the grouping: a dry run, or with --live the measurement")
    r.add_argument("--testset", default=str(REFEREE / "testset/groups.yaml"))
    r.add_argument("--prompt", default=str(REFEREE / "prompt-group.md"))
    r.add_argument("--schema", default=str(REFEREE / "group.schema.json"))
    r.add_argument("--prices", default=str(REFEREE / "prices.yaml"))
    r.add_argument("--out", default=str(REFEREE / "runs"))
    r.add_argument("--model", default=MODEL)
    r.add_argument("--effort", default=EFFORT)
    r.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    r.add_argument("--budget", type=float, default=referee.BUDGET)
    r.add_argument("--repeats", type=int, default=3)
    r.add_argument("--cases", nargs="*")
    r.add_argument("--show", nargs="*", default=[], help="dry run: print the message of these cases")
    r.add_argument("--wait", type=float, default=referee.BATCH_WAIT)
    r.add_argument("--live", action="store_true", help="send the requests (costs money)")
    r.add_argument("--unchecked", action="store_true", help="measure also against a hand the tester has not checked")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "collect":
        if args.kev:
            if not args.cve:
                raise SystemExit("--kev needs --cve")
            candidates = from_kev(json.loads(Path(args.kev).read_text(encoding="utf-8")), Path(args.cve), args.since)
        elif args.candidates:
            candidates = yaml.safe_load(Path(args.candidates).read_text(encoding="utf-8"))
        else:
            raise SystemExit("give --candidates, or --kev with --cve")
        if args.vendors:
            candidates = [c for c in candidates if c["publisher"] in args.vendors]
        mapping = yaml.safe_load(Path(args.publishers).read_text(encoding="utf-8")) if args.publishers else {}
        groups = collect(candidates, mapping=mapping)
        Path(args.output).write_text(json.dumps(groups_json(groups), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{len(groups)} publishers, {sum(len(g.names) for g in groups)} names, "
              f"{sum(len(literal(g)) for g in groups)} decided by code -> {args.output}")
        return 0
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
