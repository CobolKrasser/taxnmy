#!/usr/bin/env python3
"""A change that only adds an alias to a product in TAXNMY (TAX-40, decision 35 in keepitfresh).

Such a change is judged on the alias, not on the whole product again. An alias proves three things:

    1  it is nobody else's     not the key or alias of another product (the lint, TR3), under the
                               publisher of the product (TV2), and, when it looks like an existing
                               name of that publisher within one or two characters, a person
                               decides (TV3)
    2  the name exists         a source of the product that was read writes the name. Code decides
                               what it can show literally (TR11); otherwise the AI answers this one
                               question, and a yes quotes the name from a read source (TV6)
    3  nothing else changes    the file is what TAXNMY holds, but for the added aliases

TR4, TR5, TR7 and TR8 are not judged again: they were judged when the product was taken in. A
change that does anything more is not alias-only and gets the whole review. So does a change with
a new comment line, or an alias whose words speak to whoever judges it (TV1): those are judged as
a whole, where the referee answers TV1.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from pathlib import Path

import yaml

import gate
import guard

REFEREE = Path(__file__).resolve().parent.parent / "referee"
PROMPT = REFEREE / "prompt-alias.md"
SCHEMA = REFEREE / "alias.schema.json"
NOT_JUDGED = ("TR4", "TR5", "TR7", "TR8")
WHY_NOT = "judged when the product was taken in; a change that only adds an alias does not judge them again (TAX-40)"

# Words in an alias that speak to whoever judges it (TV1). An alias with one of them is judged as a whole.
ADDRESSED = re.compile(r"(?:^|[-/])(approve[ds]?|accept|merge|ignore|instructions?|referee|judge|verdict|assistant|"
                       r"reviewer|maintainer|system|prompt|override)(?=$|[-/])")


def _comments(text: str) -> Counter:
    return Counter(line.strip() for line in text.splitlines() if "#" in line)


def added(files: dict, old: dict) -> list[str] | None:
    """The aliases a change adds, when that is all it does; None for any other proposal. `files` are
    the proposed files, `old` the texts in TAXNMY of those that change an existing one."""
    if len(files) != 1:
        return None
    (rel, text), = files.items()
    if not rel.startswith("products/") or rel not in old:
        return None
    try:
        before, after = yaml.safe_load(old[rel]), yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    was, now = before.get("aliases") or [], after.get("aliases") or []
    if not isinstance(was, list) or not isinstance(now, list) or any(a not in now for a in was):
        return None
    new = [a for a in now if a not in was]
    rest = lambda d: {k: v for k, v in d.items() if k != "aliases"}  # noqa: E731
    if not new or rest(before) != rest(after) or _comments(text) - _comments(old[rel]):
        return None
    if any(not isinstance(a, str) or ADDRESSED.search(a.casefold()) for a in new):
        return None
    return new


def foreign(product: dict, aliases: list[str]) -> list[str]:
    """Point 1, by code (TV2): the aliases that are not under the publisher of the product."""
    pub = product.get("publisher")
    return [a for a in aliases if a.split("/")[0] != pub]


def lookalike(aliases: list[str], product: dict, others: dict) -> list[tuple[str, str]]:
    """Point 1, TV3: (rule, reason) per alias that looks like an existing key or alias of another
    product of the same publisher. `others` is path -> data of the products in TAXNMY."""
    pub, own = product.get("publisher"), product.get("key")
    names: dict[str, str] = {}
    for rel, data in others.items():
        if not isinstance(data, dict) or data.get("key") == own:
            continue
        for v in [data.get("key"), *(data.get("aliases") or [])]:
            if isinstance(v, str) and v.startswith(f"{pub}/"):
                names.setdefault(v, rel)
    out = []
    for a in aliases:
        for old, why in guard.lookalikes(a, list(names)):
            out.append(("TV3", f"alias {a!r} looks like {old!r} ({names[old]}): {why}; a person decides"))
    return out


def own_urls(product: dict) -> list[str]:
    """The sources of the product itself: its own, of its versions and of its codes. Not the
    publisher's, which writes many names and not one of them for this product."""
    return gate.sources_of(product, {})


def name_pattern(alias: str) -> re.Pattern:
    """The name of an alias as a text writes it: the words of its product part, in order, apart or
    joined by a space, a dash, a dot or an underscore, and no letter or digit glued to either end."""
    words = [w for w in re.split(r"[-_.]+", alias.split("/")[-1]) if w]
    return re.compile(r"(?<!\w)" + r"[\s\-_.]?".join(map(re.escape, words)) + r"(?!\w)")


def written(alias: str, sources: list, urls: list[str]) -> str:
    """Point 2 by code (TR11): the URL of a read source of the product that writes the name, or ''."""
    pattern = name_pattern(alias)
    for s in sources:
        if (s.url in urls or s.instead_of in urls) and gate.read(s) and pattern.search(gate.normalise(s.text)):
            return s.url
    return ""


def not_judged() -> dict:
    """What the outcome says about the rules an alias does not prove."""
    return {r: gate.judgment("not judged", WHY_NOT) for r in NOT_JUDGED}


# --------------------------------------------------------------------------- the one question for the AI

MARKERS = re.compile(r"<(?=\s*/?\s*(?:product|alias|sources|source)\b)", re.I)


def inert(text: str) -> str:
    return MARKERS.sub("&lt;", text)


def message(product_text: str, aliases: list[str], sources: list) -> str:
    """The user message of referee/prompt-alias.md: the product as it is in TAXNMY, the aliases to
    prove, and the texts of its sources that were read."""
    blocks = [f'<source url="{inert(s.url)}" status="{s.status}">\n{inert(s.text)}\n</source>' for s in sources if gate.read(s)]
    return (f"<product>\n{inert(product_text.rstrip())}\n</product>\n\n"
            + "".join(f"<alias>{inert(a)}</alias>\n" for a in aliases)
            + f"\n<sources>\n{chr(10).join(blocks) or 'none'}\n</sources>\n")


def schema_for(schema: dict, aliases: list[str]) -> dict:
    """The answer schema, with the aliases asked as the only values of `alias`. How many answers there
    are is not in the schema: structured outputs take no count of items (referee.format_problems).
    `check` counts an alias without an answer as not proved."""
    out = copy.deepcopy(schema)
    item = out["properties"]["answers"]["items"]
    item["properties"]["alias"]["enum"] = list(aliases)
    return out


def check(answer: dict, aliases: list[str], sources: list) -> tuple[list[str], list[str]]:
    """(the aliases proved, why each other one is not). A yes counts only with a quote found in the
    text of a read source (TV6), and the quote must hold a word of the alias; anything else is not
    proved: no, doubt, a yes without such a quote, or no answer at all."""
    texts = {s.url: gate.normalise(s.text) for s in sources if gate.read(s)}
    by = {a["alias"]: a for a in answer.get("answers") or [] if isinstance(a, dict)}
    proved, why = [], []
    for alias in aliases:
        a = by.get(alias)
        if a is None:
            why.append(f"alias {alias!r}: no answer")
            continue
        if a.get("verdict") != "yes":
            why.append(f"alias {alias!r}: {a.get('verdict')}: {a.get('reason', '')}")
            continue
        words = [w for w in re.split(r"[-_.]+", alias.split("/")[-1]) if len(w) >= 3]
        quotes = [e for e in a.get("evidence") or [] if gate.normalise(e.get("quote", "")) and
                  gate.normalise(e["quote"]) in texts.get(e.get("url"), "") and
                  (not words or any(w in gate.normalise(e["quote"]) for w in words))]
        if quotes:
            proved.append(alias)
        else:
            why.append(f"alias {alias!r}: a yes without a quote of the name from a source that was read (TV6)")
    return proved, why
