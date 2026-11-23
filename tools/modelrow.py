#!/usr/bin/env python3
"""A row of models (TAX-62): a name that lists several models that share one stream of firmware, such as
"ThinkPad T14 Gen 2/P14s Gen 2" or "Latitude 5431, Precision 3470". The PO decided on 23 November 2026:
the first model is the name and the key, the other models are aliases; a row the rule cannot read as full
model names is not taken in.

`models(name, brands, series, additions)` gives (the models, "") or ([], the reason it cannot be read).
`brands` are the names of the publisher, dropped at the start of a model. `series` are the series names
of the publisher (the words before the model number, lowercase): a part that is only a series name is no
model, and a part that starts with an unknown word is not read. `additions` are regular expressions of
words that are no part of a model (data in referee/lvfs.yaml, TG4).

The rule is the analyst's, applied by hand in PR #89, here in code.
"""

from __future__ import annotations

import re

MAX_NAME = 100
DIGIT = re.compile(r"\d")
SPLIT = re.compile(r"(?<!\b[A-Za-z])(?<!\b[A-Za-z]{2})/(?![A-Za-z]{1,2}\b)|,")  # not in C/A, I/O or A/V


def is_row(name: str) -> bool:
    """A slash or a comma outside brackets, in a name with a number: a model has a number, so a name without
    one (such as "UEFI/ATF") names no row of models. A slash between two letters or pairs of letters
    ("USB-C/A", "I/O") belongs to the name."""
    return (bool(SPLIT.search(name)) and bool(DIGIT.search(name))
            and not re.search(r"\([^)]*[/,][^)]*\)", name))


def clean(part: str, brands=(), additions=()) -> str:
    p = re.sub(r"\s+", " ", part).strip()
    for pattern in additions:
        p = re.sub(pattern, "", p, flags=re.I)
    p = re.sub(r"^for\s+", "", p, flags=re.I)
    for b in brands:
        p = re.sub(r"^" + re.escape(b) + r"\s+", "", p, flags=re.I)
    return p.strip(" -")


def lead(tokens: list[str]) -> list[str]:
    """The words before the first word with a digit: the series name."""
    out = []
    for t in tokens:
        if DIGIT.search(t):
            break
        out.append(t)
    return out


def series_of(names, brands=(), additions=()) -> set[str]:
    """The series names in a list of product names of one publisher (the first model of a row)."""
    out = set()
    for name in names:
        part = SPLIT.split(name)[0] if is_row(name) else name
        t = clean(part, brands, additions).split()
        ld = lead(t)
        if ld and len(ld) < len(t):
            out.add(" ".join(ld).lower())
    return out


def models(name: str, brands=(), series=(), additions=()) -> tuple[list[str], str]:
    if len(name) > MAX_NAME:
        return [], f"a row of models: the name is longer than {MAX_NAME} characters"
    parts = [p for p in (clean(x, brands, additions) for x in SPLIT.split(name)) if p]
    firsts = {s.split()[0] for s in series}
    out: list[str] = []
    prev = first = None
    for p in parts:
        t = p.split()
        if not DIGIT.search(p):
            if p.lower() in series:
                continue  # a series name without a model number is no model
            return [], f"a row of models: a part is no model: {p!r}"
        if first is None:
            first = t
        if not [x for i, x in enumerate(t) if DIGIT.search(x) and not (i and t[i - 1].lower() == "gen")]:
            return [], f"a row of models: a part has no model number: {p!r}"
        ld = lead(t)
        if (ld and series and ld[0].lower() not in firsts and len(ld) < len(t)
                and (prev is None or lead(prev.split()) != ld)):
            return [], f"a row of models: a part starts with no known series name: {p!r}"
        if len(t) >= len(first) + 2:
            return [], f"a row of models: the last part carries an addition for the whole row: {p!r}"
        full = (not DIGIT.search(t[0])) or (len(t) == len(first) and len(t) >= 2 and bool(DIGIT.search(first[0]))) \
            or prev is None
        if not full:
            pt = prev.split()
            pl = lead(pt)
            rest = pt[len(pl):]
            if pl and len(rest) == len(t):
                p = " ".join(pl + t)  # ThinkPad T14 Gen 2 / P14s Gen 2
            elif (len(t) == 1 and len(pt) >= 2 and re.fullmatch(r"\d+", t[0]) and re.fullmatch(r"\d+", pt[-1])
                  and len(t[0]) == len(pt[-1])):
                p = " ".join(pt[:-1] + t)  # G15 5520/5521
            else:
                return [], f"a row of models: a part is shortened and cannot be completed without doubt: {p!r}"
        if p.lower() not in [x.lower() for x in out]:
            out.append(p)
        prev = p
    if not out:
        return [], "a row of models: no model in the name"
    return out, ""
