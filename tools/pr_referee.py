#!/usr/bin/env python3
"""The referee on a pull request, with a parking list (TAX-17).

Every product file a pull request adds or changes gets an outcome:

    accept   it may be merged
    reject   on a rule (TE1, TK2, TK3, TP1, TP3, TP5, TV1, TV4), with the reason
    alias    it is an existing product written in another way (TP3)
    parked   not taken in, on a rule, with the reason: the referee doubts, a source is closed, the
             budget or a fork leaves it unjudged, or code holds it for a person (TV3, TV5, tools/guard.py),
             whatever the referee answers. It goes to the parking list (referee/parked.yaml)
             and can be judged again later with one command.

First the lint (TE1, TK2 and TK3) and the gate (TP5, TK2 and TE1 by code, tools/gate.py), for free; then the AI
referee, only for what code left, as one batch. Doubt means: not taken in, parked (14 November 2026).

The steps, each in its own job:

    collect   pull_request, no key: the changed data files into proposal.json, kept as an artifact.
              It runs the code of the pull request, so nothing downstream trusts what it writes.
    gate      workflow_run on main, no key: reads only that artifact, checks it strictly, stages the
              files on the data of main, lints them, reads their sources and runs the gate.
    judge     workflow_run on main, environment referee, the key: one batch for what is left, within
              a ceiling per pull request, per day and in all.
    apply     locally, on the branch of the pull request: parked files move to the parking list,
              rejected ones are taken out.
    parked    judge the parking list again (one command; weekly by workflow referee-parked): what
              is past its term lapses, the rest is judged again as far as its reason allows.
    decide    a person decides on a publisher that code held (TV3, TV5); kept in referee/decisions.yaml.
    settle    a person settles a collision or an audit finding on a product (`kind`); kept there too.
    remove    a maintainer takes a product out (TK3, TG13); the decision is logged there, with what it was.
    waiting   what waits for a decision of a person, per publisher, as JSON.
    testset   the 104 cases of referee/testset/cases.yaml, run as if they were one pull request.

No step imports or runs code from a pull request; they read YAML and JSON only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alias  # noqa: E402
import archive  # noqa: E402
import gate  # noqa: E402
import intake  # noqa: E402
import referee  # noqa: E402
from fetch import Fetcher, Source, footer_counts, moved  # noqa: E402

REPO = referee.REPO
PARKED = REPO / "referee/parked.yaml"
TERMS = "referee/parking.yaml"  # the terms of the parking list, relative to a data root
DECISIONS = "referee/decisions.yaml"  # the decisions of a person per publisher
# What a parked proposal waits for (TR10, decided 18 November 2026), and how the comment says it.
WAITS_FOR = {
    "event": "a source could not be read, or a ceiling or a fork left it unjudged. The referee tries again "
             "weekly; nothing to do for you",
    "better-proposal": "the sources were read and do not show what they must. Improve the source or the "
                       "proposal and propose it again",
    "decision": "code held it for a person (TV3, TV5), who decides per publisher",
}
DATA_PATH = re.compile(r"(products/[a-z0-9-]+/[a-z0-9-]+|publishers/[a-z0-9-]+)\.yaml")
MAX_FILES = 2000  # per pull request; a larger one is split
MAX_BYTES = 64 * 1024  # per file
PR_CAP = 2.00  # dollars per pull request, all its runs together
DAY_CAP = 5.00  # dollars per day (UTC), all pull requests together
PR_MAX_TOKENS = 4000  # per request; prompt.md asks for about 2,000
OUTCOMES = ("accept", "reject", "alias", "parked")


# --------------------------------------------------------------------------- collect (no key; untrusted)

def collect(base: str, head: str, pr: int, root: Path = REPO) -> dict:
    """The data files the pull request adds or changes, with their text at head, and the data files it
    deletes (TK3). Without rename detection a renamed file is a deleted one and an added one."""
    lines = subprocess.run(["git", "diff", "--name-status", "--no-renames", f"{base}...{head}"], cwd=root,
                           check=True, capture_output=True, text=True).stdout.splitlines()
    status = [(ln.split("\t", 1)[0][:1], ln.split("\t", 1)[1]) for ln in lines if "\t" in ln]
    files = {n: (root / n).read_text(encoding="utf-8") for st, n in status if st in "AM" and DATA_PATH.fullmatch(n)}
    deleted = sorted(n for st, n in status if st == "D" and DATA_PATH.fullmatch(n))
    return {"version": 1, "pr": pr, "head_sha": head, "files": files, "deleted": deleted}


def deleted_list(path: Path) -> list[str]:
    """The data files a pull request deletes or renames, one path per line, as GitHub lists them.
    The gate takes these from GitHub, not from the artifact: the collect step runs code of the pull request."""
    names = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return sorted({n for n in names if DATA_PATH.fullmatch(n)})


# --------------------------------------------------------------------------- the artifact (checked strictly)

def load_proposal(path: Path) -> dict:
    """proposal.json as the collect step wrote it, or SystemExit. It comes from code of the pull
    request, so every field is checked: a number, a commit, data paths, plain text of limited size."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{path}: not readable JSON ({type(exc).__name__})")
    if not isinstance(data, dict) or set(data) - {"deleted"} != {"version", "pr", "head_sha", "files"} \
            or data["version"] != 1:
        raise SystemExit(f"{path}: not a proposal of version 1")
    deleted = data.setdefault("deleted", [])
    if not isinstance(deleted, list) or len(deleted) > MAX_FILES or \
            not all(isinstance(d, str) and DATA_PATH.fullmatch(d) for d in deleted):
        raise SystemExit(f"{path}: deleted is not a list of data files")
    if not isinstance(data["pr"], int) or isinstance(data["pr"], bool) or data["pr"] <= 0:
        raise SystemExit(f"{path}: pr is not a number")
    if not isinstance(data["head_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", data["head_sha"]):
        raise SystemExit(f"{path}: head_sha is not a commit")
    files = data["files"]
    if not isinstance(files, dict):
        raise SystemExit(f"{path}: files is not a mapping")
    if len(files) > MAX_FILES:
        raise SystemExit(f"{path}: {len(files)} files; at most {MAX_FILES} per pull request, split it")
    for rel, text in files.items():
        if not isinstance(rel, str) or not DATA_PATH.fullmatch(rel):
            raise SystemExit(f"{path}: {str(rel)[:80]!r} is not a data file (products/<publisher>/<product>.yaml, publishers/<key>.yaml)")
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
            raise SystemExit(f"{path}: {rel} is not text of at most {MAX_BYTES} bytes")
    return data


def group(files: dict, deleted=()) -> list[dict]:
    """One proposal per product file, with its publisher file when the pull request brings that too.
    A publisher file that no product of the pull request uses is a proposal of its own, and so is
    every data file the pull request deletes or renames (`deleted`, TK3)."""
    proposals, used = [], set()
    for rel in sorted(f for f in files if f.startswith("products/")):
        pub = f"publishers/{rel.split('/')[1]}.yaml"
        own = {rel: files[rel]}
        try:
            data = yaml.safe_load(files[rel])
            named = data.get("publisher") if isinstance(data, dict) else None
        except yaml.YAMLError:
            named = None
        if isinstance(named, str) and re.fullmatch(r"[a-z0-9-]+", named):
            pub = f"publishers/{named}.yaml"
        if pub in files:
            own[pub] = files[pub]
            used.add(pub)
        proposals.append({"id": rel, "files": own})
    for rel in sorted(f for f in files if f.startswith("publishers/") and f not in used):
        proposals.append({"id": rel, "files": {rel: files[rel]}})
    for rel in sorted(set(deleted) - set(files)):
        proposals.append({"id": rel, "files": {}, "deleted": [rel]})
    return proposals


# --------------------------------------------------------------------------- gate (no key)

def key_of(files: dict) -> str:
    rel, data = referee.product_file(files)
    if rel:
        key = data.get("key")
        return key if isinstance(key, str) else rel
    for rel, text in files.items():
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("key"), str):
            return data["key"]
        return rel
    return ""


def settled(item: dict, outcome: str, rule: str = "", reason: str = "", by: str = "code", waits: str = "event") -> dict:
    """Settle an item. A parked one waits for exactly one thing: a decision when code holds it,
    otherwise `waits` (an event, unless the caller knows the sources were read)."""
    item.update(outcome=outcome, rule=rule, reason=reason, by=by,
                waits_for=("decision" if item.get("held") else waits) if outcome == "parked" else "")
    return item


def unread(item: dict) -> list[str]:
    """The sources of an item that the reader could not read, and for which nothing was found instead."""
    sources = [Source(**s) for s in item.get("sources") or []]
    found = {s.instead_of for s in sources if s.instead_of and gate.read(s)}
    return [s.url for s in sources if not s.instead_of and not gate.read(s) and s.url not in found]


# --------------------------------------------------------------------------- decisions of a person per publisher

def load_decisions(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return list((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("decisions") or [])


def subject(text: str) -> str:
    """What a hold is about, without the file it was found in: the same for every proposal of the
    publisher that is held for the same reason."""
    text = re.sub(r"^[a-z0-9/._-]+\.yaml: ", "", text)
    text = re.sub(r" \([^()]*\.yaml[^()]*\)", "", text)
    return text.removesuffix("; a person decides")


def publisher_of(files: dict) -> str:
    """The publisher key of a proposal: the publisher of its product, or the key of its publisher file."""
    rel, data = referee.product_file(files)
    if rel:
        pub = data.get("publisher")
        return pub if isinstance(pub, str) else ""
    for rel, text in files.items():
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and isinstance(data.get("key"), str):
            return data["key"]
    return ""


def removal(decisions: list[dict], text: str) -> dict | None:
    """The decision of a maintainer that took the product of `text` out of TAX (TK3, TG13), or None."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    key = data.get("key") if isinstance(data, dict) else None
    found = [d for d in decisions if d.get("decision") == "remove" and d.get("subject") == key]
    return found[-1] if found else None


def decided_hold(st, publisher: str, decisions: list[dict], items=None) -> tuple[dict, dict, tuple | None]:
    """What code holds the staged proposal on, after the decisions of a person on its publisher:
    (held, holds, rejected). An approved reason no longer holds it; a rejected one rejects it
    (rejected = (rule, reason)). `holds` is rule -> the subjects still waiting for a person."""
    mine = {(d.get("rule"), d.get("subject")): d for d in decisions if d.get("publisher") == publisher}
    left, holds, rejected = [], {}, None
    for rule, text in (referee.held_items(st.files, st.findings, st.old) if items is None else items):
        d = mine.get((rule, subject(text)))
        if d is None:
            left.append((rule, text))
            if subject(text) not in holds.setdefault(rule, []):
                holds[rule].append(subject(text))
        elif d.get("decision") == "reject" and rejected is None:
            rejected = (rule, f"a person decided on {d.get('date')} about {publisher}: {subject(text)}: {d.get('reason', '')}")
    return referee.join_held(left), holds, rejected


def gate_proposals(proposals: list[dict], fetcher, data_root: Path = REPO, log=print, decisions=None,
                   submitter: "intake.Submitter | None" = None, limits: dict | None = None) -> list[dict]:
    """Lint, gate and sources for each proposal. Each item gets an outcome, or `pending` with the
    message for the referee and the rules it is asked. The decisions of a person (DECISIONS in
    `data_root`) take away or settle what code holds a proposal on. A proposal over a limit of the
    intake (TAX-32: of the proposal, or of its `submitter`) is rejected before its sources are read."""
    stager = referee.Stager(data_root)
    if decisions is None:
        decisions = load_decisions(data_root / DECISIONS)
    limits = limits or intake.load(data_root)
    refused = intake.refusals(proposals, submitter, limits, load_parked(data_root / "referee/parked.yaml"))
    items = []
    for p in proposals:
        mine = decisions if "decisions" not in p else p["decisions"]
        gone = {rel: stager.data.texts[rel] for rel in p.get("deleted", ()) if rel in stager.data.texts}
        by_maintainer = {rel: d for rel in gone if (d := removal(mine, gone[rel]))}
        st = stager.stage_files(p["files"], removed=p.get("removed", ()), context=p.get("context"),
                                deleted=[rel for rel in gone if rel not in by_maintainer])
        item = {"id": p["id"], "key": key_of(p["files"] or gone), "publisher": publisher_of(p["files"] or gone),
                "files": p["files"], "deleted": sorted(gone), "case": p.get("case"), "old": {**gone, **st.old},
                "lint": [str(f) for f in st.findings], "sources": [], "decided": {}, "ai_rules": [], "message": "", "held": {},
                "holds": {}, "outcome": "", "rule": "", "reason": "", "by": "", "waits_for": "", "cost": 0.0,
                "verdict": {}, "checked": {},
                "submitter": p.get("submitter", submitter.login if submitter else ""),
                "known": p.get("known", submitter.known if submitter else True)}
        errors = [f for f in st.findings if f.level == "error"]
        if item["id"] in refused:
            settled(item, "reject", *refused[item["id"]])
        elif by_maintainer and not errors and not p["files"]:
            d = next(iter(by_maintainer.values()))
            settled(item, "accept", "TK3", f"a maintainer took {item['key']} out on {d.get('date')}: {d.get('reason', '')}",
                    by="person")
            item["old"] = {}
        elif errors:
            settled(item, "reject", errors[0].rule, errors[0].message)
        elif not referee.product_file(p["files"])[0]:
            # A publisher file alone: nothing for the referee, but code may hold it (TV3, TV5), and
            # a second source it gives must pass (TV4).
            sources = [fetcher.fetch(u, "") for u in referee.case_urls(st)]
            item["sources"] = [asdict(s) for s in sources]
            item["held"], item["holds"], by_person = decided_hold(st, item["publisher"], mine)
            if wrong := referee.second_source_fault(st, sources):
                settled(item, "reject", "TV4", wrong)
            elif swap := referee.replacement_fault(st, fetcher):
                settled(item, "reject", "TV5", swap)
            elif by_person:
                settled(item, "reject", *by_person, by="person")
            elif moves := redirected(p["files"], sources):
                park_redirected(item, moves)
            elif item["held"]:
                settled(item, "parked", ", ".join(item["held"]), "; ".join(item["held"].values()))
            else:
                settled(item, "accept", reason="a publisher file that passes the lint and the checks of code")
        elif (life := archive.change(p["files"], st.old)) is not None:
            gate_archive(item, st, *life, fetcher)
        elif (new := alias.added(p["files"], st.old)) is not None:
            gate_alias(item, st, new, fetcher, mine, stager)
        elif chat := gate.ai_chat(st.product, st.publisher):
            settled(item, "reject", "TE1", chat["reason"])
            item["decided"] = {"TE1": chat}
        else:
            sources = referee.gather_sources(fetcher, st)
            item["sources"] = [asdict(s) for s in sources]
            gone = referee.product_gone(st.files, sources)
            decided = gate.decide(st.product, st.publisher, sources, not st.known_publisher)
            item["decided"] = decided
            wrong = referee.second_source_fault(st, sources)
            if gone:
                settled(item, "reject", "TP1", gone)
            elif wrong:
                settled(item, "reject", "TV4", wrong)
            elif swap := referee.replacement_fault(st, fetcher):
                settled(item, "reject", "TV5", swap)
            elif no := [r for r, j in decided.items() if j["verdict"] == "no"]:
                settled(item, "reject", no[0], decided[no[0]]["reason"])
            elif (decision := decided_hold(st, item["publisher"], mine))[2]:
                settled(item, "reject", *decision[2], by="person")
            elif moves := redirected(p["files"], sources):
                item["held"], item["holds"], _ = decision
                park_redirected(item, moves)
            else:
                item["held"], item["holds"], _ = decision
                item["ai_rules"] = [r for r in referee.RULES if r not in decided]
                item["footer"] = footer_counts()  # TAX-43: the referee is shown the page footer
                item["names"] = referee.publisher_names(st.publisher)  # what a footer quote for TP5 must name
                item["message"] = referee.build_message(st, "fetched", sources, decided, item["footer"])
                item["outcome"] = "pending"
        log(f"{item['id']}: {item['outcome']}" + (f" {item['rule']}" if item["rule"] else ""))
        items.append(item)
    stager.close()
    return items


def gate_alias(item: dict, st, new: list[str], fetcher, decisions: list[dict], stager) -> dict:
    """A change that only adds aliases (TAX-40, tools/alias.py): judged on the aliases alone.
    Point 1 by code (TV2; TV3 for a person), point 2 by code where a read source of the product
    writes the name (TR11), else one question for the AI. TP3, TP5, TP1 and TE1 are not judged again."""
    product = st.product
    item["alias"] = new
    item["decided"] = alias.not_judged()
    if wrong := alias.foreign(product, new):
        return settled(item, "reject", "TV2", f"an alias is not under the publisher {product.get('publisher')!r} of the "
                       f"product: {', '.join(wrong)}. An alias is another key of the same product, so of the same publisher")
    others = {rel: rec.data for rel, rec in stager.data.records.items() if rel.startswith("products/")}
    held, holds, refused = decided_hold(st, item["publisher"], decisions, alias.lookalike(new, product, others))
    if refused:
        return settled(item, "reject", *refused, by="person")
    item["held"], item["holds"] = held, holds
    sources = referee.gather_sources(fetcher, st)
    item["sources"] = [asdict(s) for s in sources]
    urls = alias.own_urls(product)
    found = {a: alias.written(a, sources, urls) for a in new}
    left = [a for a, url in found.items() if not url]
    item["decided"]["alias"] = gate.judgment(
        "yes" if not left else "open",
        "; ".join(f"{a!r} is written at {url}" for a, url in found.items() if url) or "no read source writes the name literally",
        [{"url": url, "quote": a} for a, url in found.items() if url])
    if not left:
        if held:
            return settled(item, "parked", ", ".join(held), "; ".join(held.values()))
        return settled(item, "accept", reason=f"an added alias, written at a read source of the product (TR11); "
                       f"{', '.join(alias.NOT_JUDGED)}: not judged, {alias.WHY_NOT}")
    if not any(gate.read(s) for s in sources if s.url in urls or s.instead_of in urls):
        return settled(item, "parked", "alias", "no source of the product could be read, so nothing shows the name "
                       + ", ".join(left))
    item["ask"], item["alias"] = "alias", left
    item["ai_rules"] = ["alias"]
    item["message"] = alias.message(st.old[next(iter(st.old))],
                                    left, [s for s in sources if s.url in urls or s.instead_of in urls])
    item["outcome"] = "pending"
    return item


def settle_alias(item: dict, answer: dict, schema: dict) -> dict:
    """The outcome of the one question on an added alias: accepted only when every alias asked is
    proved with a quote of a read source (TV6); otherwise parked, waiting for a better proposal."""
    item["cost"], item["usage"] = answer["cost"], answer["usage"]
    if answer["error"] or answer["stop"] == "refusal":
        return settled(item, "parked", "alias", answer["error"] or "the model refused", "ai")
    verdict, why = referee.parse_reply(answer["text"], alias.schema_for(schema, item["alias"]))
    if verdict is None:
        return settled(item, "parked", "alias", why, "ai")
    item["verdict"] = verdict
    proved, notes = alias.check(verdict, item["alias"], [Source(**s) for s in item["sources"]])
    item["checked"] = {"alias": "yes" if not notes else "doubt"}
    if notes:
        return settled(item, "parked", "alias", "; ".join(notes), "ai", "better-proposal")
    if item.get("held"):
        return settled(item, "parked", ", ".join(item["held"]), "; ".join(item["held"].values()), "code")
    return settled(item, "accept", reason=f"an added alias, quoted from a read source of the product (TV6); "
                   f"{', '.join(alias.NOT_JUDGED)}: not judged, {alias.WHY_NOT}", by="ai")


def gate_archive(item: dict, st, direction: str, url: str, fetcher) -> dict:
    """A change that archives a product or takes it up again (TP10, TAX-47, tools/archive.py): judged
    on that alone. The place of the source by code; the text, when it was read, by one question for
    the AI. The other rules are not judged again."""
    item["archive"] = direction
    item["decided"] = archive.not_judged()
    outcome, why = archive.place(url, st.publisher)
    if outcome:
        return settled(item, outcome, archive.RULE, why, waits="better-proposal")
    src = fetcher.fetch(url, referee.product_name(st))
    item["sources"] = [asdict(src)]
    if end := moved(src):
        return park_redirected(item, [(url, end)])
    if not gate.read(src):
        return settled(item, "parked", archive.RULE, f"{url} could not be read ({src.status}); the referee tries again")
    item["ask"], item["ai_rules"] = "archive", [archive.RULE]
    item["message"] = archive.message(st.old[next(iter(st.old))], direction, [src])
    item["outcome"] = "pending"
    return item


def settle_archive(item: dict, answer: dict, schema: dict) -> dict:
    """The outcome of the one question on archiving or taking up again: accepted only when the answer
    is yes with a quote of the read source that names the product (TV6); otherwise parked."""
    item["cost"], item["usage"] = answer["cost"], answer["usage"]
    if answer["error"] or answer["stop"] == "refusal":
        return settled(item, "parked", archive.RULE, answer["error"] or "the model refused", "ai")
    verdict, why = referee.parse_reply(answer["text"], archive.schema_for(schema, item["archive"]))
    if verdict is None:
        return settled(item, "parked", archive.RULE, why, "ai")
    item["verdict"] = verdict
    product = referee.product_file(item["files"])[1]
    why = archive.check(verdict, item["archive"], product, [Source(**s) for s in item["sources"]])
    item["checked"] = {archive.RULE: "yes" if not why else "doubt"}
    if why:
        return settled(item, "parked", archive.RULE, why, "ai", "better-proposal")
    done = "the publisher ended the product" if item["archive"] == "archive" else "the product gets updates again"
    return settled(item, "accept", archive.RULE, f"{done}, quoted from a source of the publisher (TV6); "
                   f"{', '.join(archive.NOT_JUDGED)}: not judged, {archive.WHY_NOT}", by="ai")


def redirected(files: dict, sources: list) -> list[tuple[str, str]]:
    """TAX-38: (the URL proposed, where it ended) for each source in the proposed files (of the
    product, its versions, its codes and its publisher) that ends at another address after a
    redirect. Sources in TAX that the proposal does not bring are not its doing."""
    mine = intake.urls(files)
    out = []
    for s in sources:
        if not s.instead_of and s.url in mine and (end := moved(s)) and (s.url, end) not in out:
            out.append((s.url, end))
    return out


def park_redirected(item: dict, moves: list[tuple[str, str]]) -> dict:
    """Doubt: the source is not the address where its text is. The submitter may propose that address."""
    where = "; ".join(f"{url} ends at {end}" for url, end in moves)
    return settled(item, "parked", "redirect",
                   f"a source redirects to another address: {where}. The text read is that of the address it ends "
                   f"at, so it does not show what the proposed address says. Propose the address it ends at, if "
                   f"that is the page meant", waits="better-proposal")


def park_pending(items: list[dict], reason: str) -> None:
    """What the referee did not judge is parked, with the rules it would have been asked."""
    for item in items:
        if item["outcome"] == "pending":
            held = item.get("held") or {}
            settled(item, "parked", ", ".join([*held, *item["ai_rules"]]), "; ".join([*held.values(), reason]))


# --------------------------------------------------------------------------- judge (the key)

def spent_on(out: Path, pr: int | None, day: str) -> tuple[float, float]:
    """(spent on this pull request in all its runs, spent on all pull requests today), from the runs in `out`."""
    on_pr = on_day = 0.0
    for meta in out.rglob("run.json"):
        info = json.loads(meta.read_text(encoding="utf-8"))
        if info.get("condition") != "pr":
            continue
        results = meta.parent / "results.jsonl"
        if not results.is_file():
            continue
        c = sum(json.loads(line).get("cost", 0.0) for line in results.read_text(encoding="utf-8").splitlines() if line.strip())
        if pr is not None and info.get("pr") == pr:
            on_pr += c
        if str(info.get("started", ""))[:8] == day:
            on_day += c
    return on_pr, on_day


def spent_today_by(out: Path, day: str) -> dict:
    """What the referee spent today on proposals of unknown submitters ("unknown") and per submitter
    (by login), from the results of the runs in `out` (TAX-32). A result without a submitter is
    counted as known: before TAX-32 only maintainers reached the referee."""
    by: dict = {"unknown": 0.0}
    for meta in out.rglob("run.json"):
        info = json.loads(meta.read_text(encoding="utf-8"))
        results = meta.parent / "results.jsonl"
        if info.get("condition") != "pr" or str(info.get("started", ""))[:8] != day or not results.is_file():
            continue
        for line in results.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            c = row.get("cost", 0.0)
            if row.get("known") is False:
                by["unknown"] += c
            if row.get("submitter"):
                by[row["submitter"]] = by.get(row["submitter"], 0.0) + c
    return by


def judge(items: list[dict], args, client=None, now=None, log=print, sleep=None, limits: dict | None = None) -> Path | None:
    """One batch for the pending items, within the ceilings. Writes a run directory and settles every item.
    TV10 (TAX-32): known submitters first; unknown submitters only within their part of the day
    ceiling, so they cannot use up the part of known ones. A submitter with values of its own (an
    agreed round) has its own day and pull-request ceiling instead of the shared ones."""
    pending = intake.first_known([i for i in items if i["outcome"] == "pending"])
    if not pending:
        return None
    if not args.live:
        park_pending(items, "dry run: the referee was not asked")
        return None
    now = now or datetime.now(timezone.utc)
    out = Path(args.out)
    prices = referee.load_prices(Path(args.prices))
    if args.model not in prices["models"]:
        raise SystemExit(f"No price for model {args.model!r} in {args.prices}")
    before = referee.spent_before(out)
    on_pr, on_day = spent_on(out, args.pr, now.strftime("%Y%m%d"))
    limits = limits or intake.load()
    by = spent_today_by(out, now.strftime("%Y%m%d"))
    logins = {i.get("submitter") for i in pending}
    mine = intake.own(limits, next(iter(logins))) if len(logins) == 1 and None not in logins else {}
    pr_cap = float(mine.get("pr_cap", args.pr_cap))
    if "day_cap" in mine:  # its own ceiling, apart from the shared one
        day_left = float(mine["day_cap"]) - by.get(next(iter(logins)), 0.0)
    else:
        day_left = args.day_cap - on_day
    caps = {"budget": args.budget - before, "pull request": pr_cap - on_pr, "day": day_left}
    tightest = min(caps, key=caps.get)
    log(f"budget ${args.budget:.2f} (spent ${before:.4f}); pull request ${args.pr_cap:.2f} (spent ${on_pr:.4f}); "
        f"day ${args.day_cap:.2f} (spent ${on_day:.4f}): at most ${max(caps[tightest], 0):.4f} now, set by the {tightest}")
    # A Budget whose limit is the tightest of the three: what is left of it is what this run may spend.
    budget = referee.Budget(before + max(caps[tightest], 0), before, args.model, prices, args.max_tokens, batch=True)
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("A live run needs ANTHROPIC_API_KEY")
        client = referee.AnthropicClient()
    # TAX-43: the messages were built in the gate, with or without the page footer; the system prompt
    # must say the same, and one batch has one system prompt.
    feet = {bool(i.get("footer")) for i in pending if not i.get("ask")}
    if len(feet) > 1:
        raise SystemExit("the pending items were gated with and without the page footer (TAX-43): gate them again")
    footer = feet == {True}
    system = referee.load_system_prompt(Path(args.prompt), footer)
    schema = referee.load_schema(Path(args.schema))
    api = referee.api_schema(schema)
    asks = {a: (referee.load_system_prompt(ASKS[a][0]), referee.load_schema(ASKS[a][1]))
            for a in {i["ask"] for i in pending if i.get("ask")}}
    requests = []
    for n, item in enumerate(pending):
        if ask := item.get("ask"):  # one question of its own: an added alias (TAX-40), archiving (TAX-47)
            text, form = asks[ask][0], ASKS[ask][2](referee.api_schema(asks[ask][1]), item)
        else:
            text, form = system, referee.schema_for(api, item["ai_rules"])
        if wrong := referee.format_problems(form):  # refused with a 400 otherwise, before anything is paid
            raise SystemExit(f"{item['id']}: the answer schema is not one structured outputs take: {'; '.join(wrong)}")
        requests.append((f"p{n}", dict(
            model=args.model,
            system=[{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": item["message"]}],
            output_config={"effort": args.effort, "format": {"type": "json_schema", "schema": form}},
        )))
    by_id = {f"p{n}": item for n, item in enumerate(pending)}
    stamp = now.strftime("%Y%m%d-%H%M%S")
    run_dir = out / f"{stamp}-pr{args.pr or 0}-{args.model}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "run.json").write_text(json.dumps({
        "condition": "pr", "pr": args.pr, "model": args.model, "effort": args.effort, "batch": True,
        "budget": args.budget, "pr_cap": args.pr_cap, "day_cap": args.day_cap, "spent_before": round(before, 6),
        "max_tokens": args.max_tokens, "started": stamp, "proposals": [i["id"] for i in pending], "footer": footer,
    }, indent=2) + "\n", encoding="utf-8")
    known = [r for r in requests if by_id[r[0]].get("known", True)]
    unknown = [r for r in requests if not by_id[r[0]].get("known", True)]
    answers, left_out = referee.batch_rounds(client, budget, known, log=log, sleep=sleep, wait=args.wait) if known else ({}, [])
    if unknown:
        share = limits["unknown_share_of_day"] * args.day_cap - by["unknown"]
        part = referee.Budget(budget.spent + max(min(budget.left, share), 0), budget.spent, args.model, prices,
                              args.max_tokens, batch=True)
        log(f"unknown submitters: at most ${max(min(budget.left, share), 0):.4f} "
            f"({limits['unknown_share_of_day']:.0%} of the day ceiling, ${by['unknown']:.4f} spent today)")
        more, out_too = referee.batch_rounds(client, part, unknown, log=log, sleep=sleep, wait=args.wait)
        answers.update(more)
        left_out += out_too
    for cid, answer in answers.items():
        if ask := by_id[cid].get("ask"):
            ASKS[ask][3](by_id[cid], answer, asks[ask][1])
        else:
            settle_answer(by_id[cid], answer, schema)
    for cid in left_out:
        item = by_id[cid]
        room = f"the {tightest} ceiling" if item.get("known", True) else "the part of the day ceiling for unknown submitters"
        settled(item, "parked", ", ".join(item["ai_rules"]), f"{room} leaves no room for the referee")
    log(f"{len(pending)} for the referee: {len(answers)} answered, {len(left_out)} parked for the ceiling")
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as jsonl:
        for item in pending:
            jsonl.write(json.dumps({k: item[k] for k in ("id", "key", "outcome", "rule", "reason", "by", "verdict",
                                                         "checked", "cost", "usage", "case", "submitter", "known")
                                    if k in item},
                                   ensure_ascii=False) + "\n")
    return run_dir


# The questions of their own: ask -> (prompt, answer schema, the schema for one item, settle).
ASKS = {"alias": (alias.PROMPT, alias.SCHEMA, lambda s, i: alias.schema_for(s, i["alias"]), settle_alias),
        "archive": (archive.PROMPT, archive.SCHEMA, lambda s, i: archive.schema_for(s, i["archive"]), settle_archive)}


def settle_answer(item: dict, answer: dict, schema: dict) -> None:
    """The outcome of one answer, as in prompt.md § Outcome; doubt and every failure mean parked."""
    item["cost"], item["usage"] = answer["cost"], answer["usage"]
    rules = ", ".join(item["ai_rules"])
    if answer["error"]:
        return settled(item, "parked", rules, answer["error"], "ai")
    if answer["stop"] == "refusal":
        return settled(item, "parked", rules, "the model refused", "ai")
    verdict, why = referee.parse_reply(answer["text"], referee.schema_for(schema, item["ai_rules"]))
    if verdict is None:
        return settled(item, "parked", rules, why + (" (cut off at max_tokens)" if answer["stop"] == "max_tokens" else ""), "ai")
    sources = [Source(**s) for s in item["sources"]]
    checked, notes = referee.check_quotes(verdict, sources, item["ai_rules"], referee.versions_url(item["files"]), item["files"],
                                          bool(item.get("footer")), item.get("names") or ())
    checked = {**checked, **item["decided"]}
    item["verdict"] = verdict
    item["checked"] = {r: checked[r]["verdict"] for r in referee.RULES}
    outcome, rule = referee.outcome({**checked, "duplicate_of": verdict.get("duplicate_of")})
    held = item.get("held") or {}
    if held and outcome in ("accept", "parked"):
        # Held by code (TV3, TV5): a person decides, whatever the referee answered.
        doubts = [r for r in referee.RULES if checked[r]["verdict"] == "doubt"]
        return settled(item, "parked", ", ".join([*held, *doubts]),
                       "; ".join([*held.values(), *notes, *(f"{r}: {checked[r]['reason']}" for r in doubts)]), "code")
    if outcome == "parked":
        doubts = [r for r in referee.RULES if checked[r]["verdict"] == "doubt"]
        reasons = [n for n in notes] + [f"{r}: {checked[r]['reason']}" for r in doubts if not any(n.startswith(r) for n in notes)]
        # Doubt on sources that were all read: the proposal must get better. Otherwise an event.
        return settled(item, "parked", ", ".join(doubts), "; ".join(reasons), "ai",
                       "event" if unread(item) else "better-proposal")
    if outcome == "alias":
        item["duplicate_of"] = verdict.get("duplicate_of")
        return settled(item, "alias", rule, f"the same product as {verdict.get('duplicate_of')}: {checked[rule]['reason']}", "ai")
    if outcome == "reject":
        return settled(item, "reject", rule, checked[rule]["reason"], "ai")
    return settled(item, "accept", by="ai")


# --------------------------------------------------------------------------- the report

def counts(items: list[dict]) -> dict:
    return {o: sum(i["outcome"] == o for i in items) for o in OUTCOMES}


def plain(value, limit: int = 300, cell: bool = True) -> str:
    """A value from a proposal, or a reason that may quote one, as plain text in Markdown (TAX-34, K5):
    a code span, so markup, HTML and a mention of an account show as they are written and do nothing.
    On one line, without control characters, cut to `limit`; in a table `cell` a | is escaped."""
    text = re.sub(r"\s+", " ", str(value or ""))
    text = "".join(ch for ch in text if ch.isprintable()).strip()[:limit]
    if not text:
        return "-"
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    span = f"{fence}{pad}{text}{pad}{fence}"
    return span.replace("|", "\\|") if cell else span


def comment(items: list[dict], pr: int | None, cost: float = 0.0, note: str = "") -> str:
    c = counts(items)
    lines = ["<!-- taxnmy-referee -->", f"## Referee{f' on #{pr}' if pr else ''}", "",
             f"{len(items)} proposals: **{c['accept']} accept**, {c['reject']} reject, {c['alias']} alias, "
             f"{c['parked']} parked. AI cost ${cost:.4f}.", ""]
    if note:
        lines += [note, ""]
    lines += ["| file | outcome | rule | by | reason |", "| --- | --- | --- | --- | --- |"]
    for i in items:
        out = f"parked, waits for {i.get('waits_for') or 'event'}" if i["outcome"] == "parked" else i["outcome"]
        lines.append(f"| {plain(i['id'])} | {out} | {plain(i['rule'])} | {i['by']} | {plain(i['reason'])} |")
    lines += ["", "*Parked* means not taken in now: it goes to `referee/parked.yaml` and waits for one thing:", ""]
    terms = load_terms()["waits_for"]
    lines += [f"- waits for *{w}*: {text}. It lapses after {terms[w]['lapses_after']} days." for w, text in WAITS_FOR.items()]
    lines += ["", "A proposal that lapses leaves the list; you may propose it again. The list is judged again "
              "with `python tools/pr_referee.py parked --live`. To take the outcome over on "
              "the branch: `python tools/pr_referee.py apply verdicts.json` with the artifact of this run.", ""]
    return "\n".join(lines)


def write_verdicts(items: list[dict], out_dir: Path, pr: int | None, head_sha: str, note: str = "",
                   run_dir: Path | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    cost = sum(i.get("cost", 0.0) for i in items)
    keep = ("id", "key", "publisher", "outcome", "rule", "reason", "by", "waits_for", "holds", "files", "deleted", "old", "case",
            "duplicate_of", "alias", "archive", "cost", "submitter", "known")
    data = {"version": 1, "pr": pr, "head_sha": head_sha, "run": run_dir.name if run_dir else "",
            "counts": counts(items), "cost": round(cost, 6), "verdicts": [{k: i.get(k) for k in keep} for i in items]}
    (out_dir / "verdicts.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "comment.md").write_text(comment(items, pr, cost, note), encoding="utf-8")
    return data


# --------------------------------------------------------------------------- the parking list

def load_terms(root: Path = REPO) -> dict:
    """The terms of the parking list (TERMS): in `root`, else those of the repository."""
    path = root / TERMS if (root / TERMS).is_file() else REPO / TERMS
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_parked(path: Path = PARKED) -> list[dict]:
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = list(data.get("parked") or [])
    for e in entries:  # version 1 had one date and no reason to wait
        e.setdefault("since", e.pop("date", None))
        e.setdefault("waits_for", "event")
    return entries


def fingerprint(files: dict) -> str:
    """The files of a proposal as one short hash: a proposal that changed has another."""
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _day(text) -> date:
    return text if isinstance(text, date) else date.fromisoformat(str(text))


def with_terms(e: dict, terms: dict) -> dict:
    """The entry with its owner and its last day: `lapses`, or `decide_by` for a kind that never lapses."""
    e = {**e, "waits_for": e.get("waits_for") or "event", "since": e.get("since") or e.get("date")}
    e.pop("date", None)
    waits = terms["waits_for"][e["waits_for"]]
    since = _day(e["since"])
    within = terms.get("decide_within", {}).get(e.get("kind"))
    out = {k: e[k] for k in ("key", "path", "publisher") if k in e}
    out.update(waits_for=e["waits_for"], owner=waits["owner"])
    if e.get("kind"):
        out["kind"] = e["kind"]
    out.update({k: e[k] for k in ("rule", "reason") if k in e})
    if e.get("holds"):
        out["holds"] = e["holds"]
    out["since"] = since.isoformat()
    out["checked"] = str(e.get("checked") or since.isoformat())
    if within is not None:
        out["decide_by"] = (since + timedelta(days=within)).isoformat()
    else:
        out["lapses"] = (since + timedelta(days=waits["lapses_after"])).isoformat()
    out["pr"] = e.get("pr")
    out.update({k: e[k] for k in ("submitter", "known", "fingerprint", "files") if k in e})
    return out


PARKED_HEAD = """\
# The parking list (TAX-29): proposals not taken in now. Each waits for exactly one thing:
#   event            a source could not be read, or a ceiling or a fork left it unjudged; owner: referee
#   better-proposal  the sources were read and do not show what they must; owner: submitter
#   decision         code held it for a person (TV3, TV5), or a collision or an audit (`kind`); owner: person
# `since` is the day it was first parked; it leaves the list on `lapses`, or gets a decision by
# `decide_by` (a kind that never lapses). The terms are in referee/parking.yaml.
# Judged again weekly (workflow referee-parked) or with: python tools/pr_referee.py parked --live
"""


def save_parked(entries: list[dict], path: Path = PARKED, terms: dict | None = None) -> None:
    def text_block(dumper, value):
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)

    class Dumper(yaml.SafeDumper):
        pass

    Dumper.add_representer(str, text_block)
    terms = terms or load_terms(path.parent.parent)
    body = yaml.dump({"version": 2, "parked": sorted((with_terms(e, terms) for e in entries), key=lambda e: e["key"])},
                     Dumper=Dumper, sort_keys=False, allow_unicode=True, width=120)
    path.write_text(PARKED_HEAD + body, encoding="utf-8")


def park_entry(v: dict, day: str, old: dict | None = None) -> dict:
    """An entry of the parking list for a parked verdict. `old` is the entry it replaces after a new
    judgement: its first day, its pull request and its kind stay."""
    old = old or {}
    e = {"key": v["key"], "path": v["id"], "publisher": v.get("publisher") or "", "waits_for": v.get("waits_for") or "event",
         "rule": v["rule"], "reason": v["reason"], "since": old.get("since") or day, "checked": day,
         "pr": old.get("pr", v.get("pr")), "fingerprint": fingerprint(v["files"]), "files": v["files"]}
    if v.get("submitter") or old.get("submitter"):  # TAX-32: whose it is, for the limit per submitter
        e["submitter"] = old.get("submitter") or v["submitter"]
        e["known"] = bool(old.get("known", v.get("known")))
    if e["waits_for"] == "decision" and v.get("holds"):
        e["holds"] = v["holds"]
    if old.get("kind"):
        e["kind"] = old["kind"]
    return e


def lapse(entries: list[dict], today: date, terms: dict) -> tuple[list[dict], list[dict]]:
    """(the entries that stay, those past their term). A kind that never lapses always stays."""
    keep, gone = [], []
    for e in entries:
        full = with_terms(e, terms)
        (gone if "lapses" in full and today > _day(full["lapses"]) else keep).append(e)
    return keep, gone


def lapsed_comments(gone: list[dict], terms: dict, out_dir: Path) -> list[Path]:
    """One comment per pull request for what lapsed: <out_dir>/lapsed/<pr>.md."""
    by_pr: dict[int, list[dict]] = {}
    for e in gone:
        if isinstance(e.get("pr"), int):
            by_pr.setdefault(e["pr"], []).append(e)
    paths = []
    for pr, es in sorted(by_pr.items()):
        lines = ["<!-- taxnmy-parked -->", "## Lapsed from the parking list", ""]
        for e in es:
            days = terms["waits_for"][e["waits_for"]]["lapses_after"]
            lines.append(f"- {plain(e['key'], cell=False)} waited for {e['waits_for']} since {e['since']}; its term of {days} days has "
                         f"passed, so it is not taken in. Last reason: {plain(e.get('reason'), cell=False)}")
        lines += ["", "Nothing stays against it: you may propose it again.", ""]
        path = out_dir / "lapsed" / f"{pr}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        paths.append(path)
    return paths


def waiting(entries: list[dict], terms: dict) -> dict:
    """What waits for a decision of a person, per publisher, the most urgent first."""
    out: dict[str, list[dict]] = {}
    for e in entries:
        if e["waits_for"] == "decision":
            full = with_terms(e, terms)
            out.setdefault(full.get("publisher") or "", []).append(
                {k: full[k] for k in ("key", "path", "kind", "rule", "reason", "holds", "since", "lapses", "decide_by", "pr")
                 if k in full})
    first = {p: min(x.get("decide_by") or x["lapses"] for x in xs) for p, xs in out.items()}
    return {p: out[p] for p in sorted(out, key=lambda p: (first[p], p))}


def save_decisions(decisions: list[dict], path: Path) -> None:
    """DECISIONS, with its comment head kept."""
    head = "".join(line + "\n" for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("#")) \
        if path.is_file() else ""
    path.write_text(head + yaml.safe_dump({"version": 1, "decisions": decisions}, sort_keys=False, allow_unicode=True,
                                          width=120), encoding="utf-8")


def decide(publisher: str, decision: str, reason: str, root: Path = REPO, by: str = "", now=None, log=print) -> list[dict]:
    """A person decides on what code held the proposals of `publisher` on (TV3, TV5). The decision is
    kept per reason in DECISIONS and holds for every next proposal of that publisher. approve: the
    entries wait for the referee again; reject: they leave the list. Returns the entries decided."""
    if decision not in ("approve", "reject"):
        raise SystemExit("decide: approve or reject")
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    path, dpath = root / "referee/parked.yaml", root / DECISIONS
    entries = load_parked(path)
    mine = [e for e in entries if e.get("publisher") == publisher and e["waits_for"] == "decision" and not e.get("kind")]
    subjects = sorted({(rule, s) for e in mine for rule, ss in (e.get("holds") or {}).items() for s in ss})
    if not subjects:
        raise SystemExit(f"Nothing of {publisher} waits for a decision on a reason code held it on.")
    decisions = load_decisions(dpath)  # every decision is kept; the last one on a subject counts (TG13)
    decisions += [{"publisher": publisher, "rule": r, "subject": s, "decision": decision, "date": day,
                   "reason": reason, "by": by} for r, s in subjects]
    save_decisions(decisions, dpath)
    if decision == "reject":
        entries = [e for e in entries if e not in mine]
    else:
        for e in mine:
            e.update(waits_for="event", checked=day, reason=f"a person approved on {day}: {reason}")
            e.pop("holds", None)
    save_parked(entries, path)
    log(f"{publisher}: {decision} on {len(subjects)} reasons; {len(mine)} entries "
        + ("taken off the list" if decision == "reject" else "wait for the referee again"))
    return mine


# The answers to a kind that never lapses (`kind`), per kind: what happens to the entry. A collision:
# two parties claim the same product, the first stands until the decision. An audit: a finding on a
# product already in TAX.
SETTLE = {
    "collision": {"stays": "the party in TAXNMY is the publisher: the proposal leaves the list",
                  "proposal": "the party of the proposal is the publisher: it waits for the referee again"},
    "audit": {"keep": "the product stays as it is: the finding leaves the list",
              "fix": "the product is to be fixed, by a proposal of its own: the finding leaves the list",
              "remove": "the product is taken out of TAXNMY: its file is deleted, the finding leaves the list"},
}


def settle(key: str, answer: str, reason: str, root: Path = REPO, by: str = "", now=None, log=print) -> list[dict]:
    """A person settles a collision or an audit finding (`kind`) on the product `key`, which never
    lapses and gets a decision within its term (`decide_by`). The decision is kept in DECISIONS
    (rule: the kind, subject: the key); the answers per kind are in SETTLE. Returns the entries settled."""
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    path, dpath = root / "referee/parked.yaml", root / DECISIONS
    entries = load_parked(path)
    mine = [e for e in entries if e.get("key") == key and e.get("kind")]
    if not mine:
        raise SystemExit(f"No collision or audit finding on {key} waits for a decision.")
    kinds = {e["kind"] for e in mine}
    kind = kinds.pop() if len(kinds) == 1 else ""
    if answer not in SETTLE.get(kind, {}):
        raise SystemExit(f"settle: {key} is a {kind or 'mix of kinds'}; it takes {' or '.join(SETTLE.get(kind, {})) or 'no answer here'}")
    rels = sorted({str(e.get("path") or "") for e in mine})
    if answer == "remove" and not all(r.startswith("products/") and DATA_PATH.fullmatch(r) for r in rels):
        raise SystemExit(f"settle: {key} has no product file to remove ({', '.join(rels) or '-'})")
    decisions = load_decisions(dpath)  # every decision is kept; the last one on a subject counts (TG13)
    entry = {"publisher": mine[0].get("publisher") or "", "rule": kind, "subject": key, "decision": answer,
             "date": day, "reason": reason, "by": by}
    if answer == "remove":
        entry.update(what_was(root, rels))
    decisions.append(entry)
    save_decisions(decisions, dpath)
    if answer == "proposal":
        for e in mine:
            e.update(waits_for="event", checked=day, reason=f"a person settled the collision on {day}: {reason}")
            e.pop("kind", None)
            e.pop("holds", None)
    else:
        entries = [x for x in entries if not any(x is e for e in mine)]
    if answer == "remove":
        for r in rels:
            (root / r).unlink(missing_ok=True)
    save_parked(entries, path)
    log(f"{key}: {kind}, {answer}: {SETTLE[kind][answer]}")
    return mine


def what_was(root: Path, rels: list[str]) -> dict:
    """What the log keeps of a product a maintainer takes out (TK3, TG13): its name, its publisher and
    its sources, so a reader of a key that is free again can see what it was."""
    data = {}
    for rel in rels:
        if (root / rel).is_file():
            data = yaml.safe_load((root / rel).read_text(encoding="utf-8")) or {}
            break
    sources = referee.urls_of({"x.yaml": yaml.safe_dump(data)}) if data else []
    return {"name": data.get("name", ""), "removed_publisher": data.get("publisher", ""), "path": rels[0] if rels else "",
            "sources": sources}


def remove(key: str, reason: str, root: Path = REPO, by: str = "", now=None, log=print) -> dict:
    """A maintainer takes the product `key` out of TAX (TK3, TG13). The decision goes to DECISIONS with
    the key, the name, the publisher, the sources, the day, the reason and who; then the file is
    deleted. The key is free again: a next proposal with it is judged as any new product."""
    if not reason.strip():
        raise SystemExit("remove: a reason is required")
    rels = [r.relative_to(root).as_posix() for r in sorted((root / "products").glob("*/*.yaml"))
            if (yaml.safe_load(r.read_text(encoding="utf-8")) or {}).get("key") == key]
    if not rels:
        raise SystemExit(f"remove: no product file has the key {key}")
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    entry = {"publisher": key.split("/")[0], "rule": "remove", "subject": key, "decision": "remove", "date": day,
             "reason": reason, "by": by, **what_was(root, rels)}
    dpath = root / DECISIONS
    save_decisions(load_decisions(dpath) + [entry], dpath)
    for r in rels:
        (root / r).unlink()
    log(f"{key}: taken out ({', '.join(rels)}); the decision is in {DECISIONS}")
    return entry


def apply(verdicts: dict, root: Path = REPO, now=None, log=print) -> dict:
    """Take the outcomes over on a checkout of the pull request: parked proposals move to the parking
    list, rejected and aliased ones are taken out, accepted ones stay. A file that changed an existing
    one gets the text of main back instead (`old`): taking it out would delete what exists. A
    publisher file goes only when no product that stays uses it."""
    day = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    parked_path = root / "referee/parked.yaml"
    entries = {e["key"]: e for e in load_parked(parked_path)}
    gone = []
    for v in verdicts["verdicts"]:
        if v["outcome"] == "accept":
            continue
        if v["outcome"] == "parked":
            entries[v["key"]] = park_entry({**v, "pr": verdicts.get("pr")}, day)  # a new proposal: a new term
        old = v.get("old") or {}
        for rel in [*v["files"], *(v.get("deleted") or [])]:
            if rel in old:
                (root / rel).write_text(old[rel], encoding="utf-8")
                gone.append(rel)
            elif rel.startswith("products/") and (root / rel).is_file():
                (root / rel).unlink()
                gone.append(rel)
    for v in verdicts["verdicts"]:
        for rel in v["files"]:
            if rel in (v.get("old") or {}):
                continue
            if rel.startswith("publishers/") and v["outcome"] != "accept" and (root / rel).is_file():
                pub = Path(rel).stem
                if not any((root / "products" / pub).glob("*.yaml")):
                    (root / rel).unlink()
                    gone.append(rel)
    save_parked(list(entries.values()), parked_path)
    log(f"taken out: {len(gone)} files; on the parking list: {len(entries)} entries")
    return {"removed": gone, "parked": len(entries)}


# --------------------------------------------------------------------------- the commands

def add_judge_args(p) -> None:
    p.add_argument("--model", default=referee.MODEL, help="the agreed model; see referee/README.md § The agreed model")
    p.add_argument("--effort", choices=("low", "medium", "high"), default=referee.EFFORT)
    p.add_argument("--max-tokens", type=int, default=PR_MAX_TOKENS)
    p.add_argument("--budget", type=float, default=referee.BUDGET, help="dollars, for all runs in --out together")
    p.add_argument("--pr-cap", type=float, default=PR_CAP, help="dollars per pull request, its runs together")
    p.add_argument("--day-cap", type=float, default=DAY_CAP, help="dollars per day (UTC), pull requests together")
    p.add_argument("--wait", type=float, default=referee.BATCH_WAIT, help="seconds to wait for the batch")
    p.add_argument("--live", action="store_true", help="ask the referee (costs money); without it the rest is parked")
    for name, default in (("prompt", referee.REFEREE / "prompt.md"), ("schema", referee.REFEREE / "verdict.schema.json"),
                          ("prices", referee.REFEREE / "prices.yaml"), ("out", referee.REFEREE / "runs")):
        p.add_argument(f"--{name}", default=str(default))


def testset_proposals(path: Path, data_root: Path = REPO) -> list[dict]:
    """The cases of the test set as proposals of one pull request: each staged as README § Staging a
    case says, with its hold-outs, hidden files and context. Only the test set may take files out."""
    texts = {p.relative_to(data_root).as_posix(): p.read_text(encoding="utf-8")
             for part in ("products", "publishers") for p in (data_root / part).glob("**/*.yaml")}
    out = []
    for case in referee.load_cases(path):
        files = {rel: texts[rel] for rel in case.holdout}
        files.update(case.proposal)
        extra = {"decisions": (yaml.safe_load(case.context[DECISIONS]) or {}).get("decisions") or []} \
            if DECISIONS in case.context else {}
        out.append({**extra, "id": case.id, "files": files, "removed": case.holdout + case.hide, "deleted": case.delete,
                    "context": {k: v for k, v in case.context.items() if k != DECISIONS},
                    "case": {"expect": case.expect, "lint": case.lint, "rules": case.rules}})
    return out


def score(items: list[dict]) -> str:
    """The test set as a pull request: per outcome, and the bad cases let through (target 0)."""
    c = counts(items)
    bad_accept = [i["id"] for i in items if i["id"].startswith("B") and i["outcome"] == "accept"]
    good_out = [i["id"] for i in items if i["id"][0] in "GH" and i["outcome"] in ("reject", "alias")]
    wrong = [i["id"] for i in items if i.get("case") and i["outcome"] not in i["case"]["expect"]]
    return "\n".join([
        "## The test set as a pull request", "",
        "| outcome | proposals |", "| --- | --- |", *[f"| {o} | {n} |" for o, n in c.items()], "",
        f"Bad cases accepted (target 0): {len(bad_accept)} {', '.join(bad_accept)}  ",
        f"Good cases rejected or aliased: {len(good_out)} {', '.join(good_out)}  ",
        f"Not in `expect`: {len(wrong)} {', '.join(wrong)}  ", ""])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="pull_request, no key: the changed data files into proposal.json")
    c.add_argument("--base", required=True)
    c.add_argument("--head", required=True)
    c.add_argument("--pr", type=int, required=True)
    c.add_argument("--output", default="proposal.json")
    n = sub.add_parser("pr-number", help="the number of the pull request in a proposal.json, checked")
    n.add_argument("proposal")
    g = sub.add_parser("gate", help="no key: lint, gate and sources of a proposal.json or of the test set")
    src = g.add_mutually_exclusive_group(required=True)
    src.add_argument("--proposal", help="the proposal.json of the collect step")
    src.add_argument("--testset", nargs="?", const=str(referee.REFEREE / "testset/cases.yaml"), help="the test set as a pull request")
    g.add_argument("--cases", nargs="+", help="with --testset: only these case ids (e.g. the alias cases of TAX-40)")
    g.add_argument("--fork", action="store_true", help="a pull request from a fork: judged by code only, the rest parked")
    g.add_argument("--no-browser", action="store_true")
    g.add_argument("--output", default="gated/gated.json")
    g.add_argument("--deleted", help="a file with the data files the pull request deletes or renames, as GitHub lists them")
    g.add_argument("--submitter", help="the account of the pull request (TAX-32), as GitHub gives it")
    g.add_argument("--known", choices=("true", "false"), default="false",
                   help="whether a pull request of the submitter was taken in before")
    g.add_argument("--open-before", type=int, default=0, help="its proposals in earlier open pull requests")
    j = sub.add_parser("judge", help="the key: one batch for what the gate left")
    j.add_argument("--gated", required=True)
    j.add_argument("--output", default="verdicts", help="directory for verdicts.json and comment.md")
    add_judge_args(j)
    a = sub.add_parser("apply", help="take verdicts.json over on a checkout of the pull request")
    a.add_argument("verdicts")
    a.add_argument("--root", default=str(REPO))
    k = sub.add_parser("parked", help="the parking list: lapse what is past its term, judge the rest again")
    k.add_argument("--no-browser", action="store_true")
    k.add_argument("--output", default="verdicts", help="verdicts.json, comment.md and lapsed/<pr>.md")
    add_judge_args(k)
    d = sub.add_parser("decide", help="a person decides on what code held the proposals of a publisher on")
    d.add_argument("publisher")
    d.add_argument("decision", choices=("approve", "reject"))
    d.add_argument("--reason", required=True)
    d.add_argument("--by", default="")
    t = sub.add_parser("settle", help="a person settles a collision or an audit finding on a product")
    t.add_argument("key", help="the key of the product, as in the digest")
    t.add_argument("answer", choices=sorted({a for k in SETTLE.values() for a in k}))
    t.add_argument("--reason", required=True)
    t.add_argument("--by", default="")
    r = sub.add_parser("remove", help="a maintainer takes a product out; the decision is logged (TK3, TG13)")
    r.add_argument("key", help="the key of the product")
    r.add_argument("--reason", required=True)
    r.add_argument("--by", default="")
    sub.add_parser("waiting", help="what waits for a decision of a person, per publisher, as JSON")
    args = p.parse_args(argv)

    if args.command == "collect":
        data = collect(args.base, args.head, args.pr)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{len(data['files'])} data files of pull request {args.pr}; {len(data['deleted'])} deleted")
    elif args.command == "pr-number":
        print(load_proposal(Path(args.proposal))["pr"])
    elif args.command == "gate":
        if args.proposal:
            data = load_proposal(Path(args.proposal))
            deleted = set(data["deleted"]) | set(deleted_list(Path(args.deleted)) if args.deleted else ())
            proposals, pr, head = group(data["files"], deleted), data["pr"], data["head_sha"]
        else:
            proposals, pr, head = testset_proposals(Path(args.testset)), None, ""
            if args.cases:
                unknown = set(args.cases) - {p["id"] for p in proposals}
                if unknown:
                    raise SystemExit(f"No such cases: {', '.join(sorted(unknown))}")
                proposals = [p for p in proposals if p["id"] in args.cases]
        submitter = intake.Submitter(args.submitter, args.known == "true", args.open_before) if args.submitter else None
        limits = intake.load()
        fetcher = Fetcher(use_browser=not args.no_browser, log=print)
        try:
            items = gate_proposals(proposals, fetcher, submitter=submitter, limits=limits)
        finally:
            fetcher.close()
        pending = sum(i["outcome"] == "pending" for i in items)
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"version": 1, "pr": pr, "head_sha": head, "fork": args.fork, "items": items},
                                  ensure_ascii=False) + "\n", encoding="utf-8")
        # What a person sees if the referee does not run: a fork, nothing left, or no approval.
        provisional = json.loads(json.dumps(items))
        note = ("A pull request from a fork gets no key: judged by code only, the rest is parked." if args.fork
                else "Provisional: what code settled. The rest waits for the referee.")
        park_pending(provisional, "a pull request from a fork is judged by code only" if args.fork
                     else "waits for the referee")
        intake.cap_parking(provisional, len(load_parked()), limits)
        write_verdicts(provisional, out.parent / "provisional", pr, head, note)
        print(f"{len(items)} proposals: {len(items) - pending} settled by code, {pending} for the referee")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
                gh.write(f"pending={'true' if pending and not args.fork else 'false'}\n")
    elif args.command == "judge":
        data = json.loads(Path(args.gated).read_text(encoding="utf-8"))
        items, args.pr = data["items"], data["pr"]
        run_dir = judge(items, args)
        intake.cap_parking(items, len(load_parked()), intake.load())
        out = Path(args.output)
        write_verdicts(items, out, data["pr"], data["head_sha"], run_dir=run_dir)
        if any(i.get("case") for i in items):
            text = score(items)
            (out / "score.md").write_text(text, encoding="utf-8")
            print(text)
        if os.environ.get("GITHUB_OUTPUT") and run_dir:
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
                gh.write(f"run_dir={run_dir}\nrun_name={run_dir.name}\n")
        print((out / "comment.md").read_text(encoding="utf-8"))
    elif args.command == "apply":
        apply(json.loads(Path(args.verdicts).read_text(encoding="utf-8")), Path(args.root))
    elif args.command == "parked":
        reprocess(args)
    elif args.command == "decide":
        decide(args.publisher, args.decision, args.reason, by=args.by)
    elif args.command == "settle":
        settle(args.key, args.answer, args.reason, by=args.by)
    elif args.command == "remove":
        remove(args.key, args.reason, by=args.by)
    elif args.command == "waiting":
        print(json.dumps(waiting(load_parked(), load_terms()), indent=1, ensure_ascii=False))
    return 0


def reprocess(args, fetcher=None, client=None, data_root: Path = REPO, now=None, log=print, sleep=None) -> dict:
    """The parking list, judged again on the data as it is now. First what is past its term lapses.
    Then, by what an entry waits for:

        event            gate and sources again (free); the referee only if every source is read now
        better-proposal  nothing, as long as the proposal did not change (its fingerprint)
        decision         gate again (free): still held, it stays; a person decided, it goes on
        kind             (a collision, an audit) nothing: a person settles it

    An accepted entry is written to the data and leaves the list, a rejected or aliased one leaves
    it, a parked one stays with its new reason and its first day."""
    path = data_root / "referee/parked.yaml"
    terms = load_terms(data_root)
    today = (now or datetime.now(timezone.utc)).date()
    day = today.isoformat()
    out = Path(getattr(args, "output", "verdicts"))
    entries, gone = lapse(load_parked(path), today, terms)
    for e in gone:
        log(f"{e['key']}: lapsed (waited for {e['waits_for']} since {e['since']})")
    lapsed_comments(gone, terms, out)
    todo = [e for e in entries if not e.get("kind")
            and not (e["waits_for"] == "better-proposal" and e.get("fingerprint") == fingerprint(e["files"]))]
    rest = [e for e in entries if e not in todo]
    items = []
    if todo:
        fetcher = fetcher or Fetcher(use_browser=not getattr(args, "no_browser", False), log=log)
        try:
            items = gate_proposals([{"id": e["path"], "files": e["files"], "submitter": e.get("submitter", ""),
                                     "known": e.get("known", False)} for e in todo], fetcher, data_root, log)
        finally:
            if hasattr(fetcher, "close"):
                fetcher.close()
    for item in items:  # the referee only for what nothing holds and whose sources are all read
        if item["outcome"] == "pending" and item["held"]:
            settled(item, "parked", ", ".join(item["held"]), "; ".join(item["held"].values()))
        elif item["outcome"] == "pending" and (closed := unread(item)):
            settled(item, "parked", ", ".join(item["ai_rules"]), "a source is still not read: " + ", ".join(closed))
    args.pr = None
    run_dir = judge(items, args, client=client, now=now, log=log, sleep=sleep, limits=intake.load(data_root))
    by_path = {e["path"]: e for e in todo}
    keep = list(rest)
    for item in items:
        if item["outcome"] == "accept":
            for rel, text in item["files"].items():
                (data_root / rel).parent.mkdir(parents=True, exist_ok=True)
                (data_root / rel).write_text(text, encoding="utf-8")
        elif item["outcome"] == "parked":
            keep.append(park_entry(item, day, by_path.get(item["id"])))
    save_parked(keep, path, terms)
    write_verdicts(items, out, None, "", run_dir=run_dir)
    c = counts(items)
    c["lapsed"], c["untouched"] = len(gone), len(rest)
    log(f"{len(gone)} lapsed; {len(rest)} not judged again (unchanged, or for a person); {len(items)} judged again: "
        f"{c['accept']} accepted and written to the data, {c['reject'] + c['alias']} rejected or aliased and taken "
        f"off the list, {c['parked']} still parked")
    return c


if __name__ == "__main__":
    sys.exit(main())
