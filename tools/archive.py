#!/usr/bin/env python3
"""Archiving a product, and taking it up again, on a proposal for it (TP10, TAX-47).

A product is archived when its publisher has ended it: it gets no more updates. TAXNMY never does
that by itself, after a term or a count; a product may go years without an update and still be
the one a vulnerability hits. Only a change that proposes it sets the field, with a page of the
publisher that shows the end. The same holds the other way: a change that takes an archived
product up again gives a page of the publisher that shows it gets updates again.

    archive   a product file in TAXNMY gets `archived: {source: ...}` (a `resumed` it had goes)
    resume    an archived product gets `resumed: {source: ...}` in place of `archived`

Such a change is judged on that alone, in three steps:

    1  the place      the source is on the domain of the publisher (TE3), as TAXNMY holds it: the
                      owner of the publisher's own `source` or of a source for machines. Elsewhere:
                      reject. TAXNMY holds no domain of the publisher: parked, a better proposal
                      gives the publisher its own source first
    2  the text       the source is read. Not read: parked, the referee tries again
    3  the end        the AI answers one question on the text, with referee/prompt-archive.md: does
                      it show the end (or the updates again)? Code accepts a yes only with a quote
                      it finds in the text, naming the product (TV6). Anything else: parked

The product keeps its key and its aliases (TK3), and stays readable. A change that touches
`archived` or `resumed` and anything else, or a new product that brings either field, is an error
of the lint (TP10): the end of a product is proposed on its own.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from pathlib import Path

import yaml

import gate
import guard
import lint

REFEREE = Path(__file__).resolve().parent.parent / "referee"
PROMPT = REFEREE / "prompt-archive.md"
SCHEMA = REFEREE / "archive.schema.json"
FIELDS = ("archived", "resumed")
RULE = "TP10"
ASK = {"archive": "has the publisher ended this product, so that it gets no more updates?",
       "resume": "does this product get updates again?"}
NOT_JUDGED = ("TP3", "TP5", "TK2", "TP1", "TE1")
WHY_NOT = "judged when the product was taken in; a change that only archives a product or takes it up again does not judge them again (TAX-47)"


def _load(text: str):
    try:
        data = yaml.safe_load(text or "")
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _comments(text: str) -> Counter:
    return Counter(line.strip() for line in text.splitlines() if "#" in line)


def _rest(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in FIELDS}


def change(files: dict, old: dict) -> tuple[str, str] | None:
    """(direction, source) when the proposal is one change of a product in TAXNMY that archives it
    or takes it up again and does nothing else; None for any other proposal."""
    if len(files) != 1:
        return None
    (rel, text), = files.items()
    if not rel.startswith("products/") or rel not in old:
        return None
    before, after = _load(old[rel]), _load(text)
    if before is None or after is None or _rest(before) != _rest(after) or _comments(text) - _comments(old[rel]):
        return None
    if "archived" in after and after.get("archived") != before.get("archived") and "resumed" not in after:
        direction, entry = "archive", after["archived"]
    elif "archived" in before and "resumed" in after and "archived" not in after:
        direction, entry = "resume", after["resumed"]
    else:
        return None
    url = entry.get("source") if isinstance(entry, dict) else None
    return (direction, url) if isinstance(url, str) else None


def faults(old: dict, files: dict) -> list:
    """TP10 for the lint: a lifecycle field that is not one change of its own. A new product that
    brings `archived` or `resumed`, a change that touches either and something else, and a change
    that takes either out without the other in its place."""
    out = []
    for rel, text in sorted(files.items()):
        after = _load(text)
        if not rel.startswith("products/") or after is None:
            continue
        before = _load(old[rel]) if rel in old else None
        if before is None:
            if any(f in after for f in FIELDS):
                out.append(lint.Finding(rel, 1, RULE, "a new product brings archived or resumed; a product is "
                                                      "proposed first, and archived by a change of its own"))
            continue
        if all(after.get(f) == before.get(f) for f in FIELDS):
            continue
        if _rest(before) != _rest(after):
            out.append(lint.Finding(rel, 1, RULE, "a change archives the product or takes it up again, and changes "
                                                  "more; propose the archiving on its own"))
        elif change({rel: text}, {rel: old[rel]}) is None:
            out.append(lint.Finding(rel, 1, RULE, "archived or resumed is taken out or changed in another way; a "
                                                  "product is taken up again with `resumed` and a source of the publisher"))
    return out


def domains(publisher: dict) -> set:
    """The domains TAXNMY holds as the publisher's own: of its `source` and its sources for machines."""
    urls = [publisher.get("source")]
    urls += [e.get("url") for e in publisher.get("machine_sources") or [] if isinstance(e, dict)]
    return {o for u in urls if isinstance(u, str) and (o := guard.owner(u))}


def place(url: str, publisher: dict) -> tuple[str, str]:
    """Step 1, by code: ('', '') when the source is on the publisher's domain; else (outcome, reason)."""
    own = domains(publisher)
    if not own:
        return "parked", (f"TAXNMY holds no domain of the publisher {publisher.get('key')!r}: its source is a page of "
                          "many owners. Propose the publisher's own source first, then the archiving")
    if guard.owner(url) not in own:
        return "reject", (f"{url} is not on the publisher's domain ({', '.join(sorted(own))}); only the publisher "
                          "can say that it ended a product")
    return "", ""


def not_judged() -> dict:
    return {r: gate.judgment("not judged", WHY_NOT) for r in NOT_JUDGED}


# --------------------------------------------------------------------------- the one question for the AI

MARKERS = re.compile(r"<(?=\s*/?\s*(?:product|change|sources|source)\b)", re.I)


def inert(text: str) -> str:
    return MARKERS.sub("&lt;", text)


def message(product_text: str, direction: str, sources: list) -> str:
    """The user message of referee/prompt-archive.md: the product as it is in TAXNMY, the change,
    and the text of the source that was read."""
    blocks = [f'<source url="{inert(s.url)}" status="{s.status}">\n{inert(s.text)}\n</source>' for s in sources if gate.read(s)]
    return (f"<product>\n{inert(product_text.rstrip())}\n</product>\n\n"
            f"<change>{direction}: {ASK[direction]}</change>\n\n"
            f"<sources>\n{chr(10).join(blocks) or 'none'}\n</sources>\n")


def schema_for(schema: dict, direction: str) -> dict:
    """The answer schema, with the change asked as the only value of `change`."""
    out = copy.deepcopy(schema)
    out["properties"]["change"]["enum"] = [direction]
    return out


def names(product: dict) -> list[str]:
    """The words a quote must hold one of: of the product's name and of its keys and aliases."""
    texts = [product.get("name")] + [k.split("/")[-1] for k in [product.get("key"), *(product.get("aliases") or [])]
                                     if isinstance(k, str)]
    return sorted({w for t in texts if isinstance(t, str) for w in re.split(r"[\s\-_.]+", gate.normalise(t)) if len(w) >= 3})


def check(answer: dict, direction: str, product: dict, sources: list, quotes: bool = True) -> str:
    """'' when the answer shows the change; otherwise why not. A yes counts only with a quote that
    code finds in the text of the source that was read, and that names the product (TV6). `quotes`
    False switches that check off: only for the deliberate break of TAX-47."""
    if answer.get("change") != direction:
        return f"the answer is on {answer.get('change')!r}, not on {direction!r}"
    if answer.get("verdict") != "yes":
        return f"{answer.get('verdict')}: {answer.get('reason', '')}"
    if not quotes:
        return ""
    texts = {s.url: gate.normalise(s.text) for s in sources if gate.read(s)}
    words = names(product)
    for e in answer.get("evidence") or []:
        q = gate.normalise(e.get("quote", "")) if isinstance(e, dict) else ""
        if q and q in texts.get(e.get("url"), "") and (not words or any(w in q for w in words)):
            return ""
    return "a yes without a quote from the source that was read and that names the product (TV6)"
