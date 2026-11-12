"""The writing rule of a TAXNMY key: publisher/product@version.

1. Accented letters become plain letters, and everything becomes lower case.
2. "+" becomes "plus", "#" becomes "sharp", "&" becomes "and", "@" becomes "at".
3. Every other character becomes "-"; runs of dashes become one, and dashes
   at the start or end are dropped.
4. Publisher only: a legal form at the end is dropped, before step 2.
5. Version only: spaces become "-"; ".", "+" and "_" stay; step 2 does not apply.

Only the Latin script is accepted. The rule holds no knowledge of brands or
products (S9): the only list is one of legal forms.
"""

from __future__ import annotations

import re
import unicodedata

MAX_PART = 64

# Latin letters that Unicode does not decompose into a plain letter.
_LATIN_EXTRA = str.maketrans({
    "ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D",
    "ð": "d", "Ð": "D", "þ": "th", "Þ": "TH", "ı": "i",
})

_WORDS = {"+": " plus ", "#": " sharp ", "&": " and ", "@": " at "}

# Legal forms, written in lower case without accents. Dots are optional.
_LEGAL_FORMS = [
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "llc", "l l c", "plc", "lp", "llp", "l p",
    "gmbh", "ag", "kg", "kgaa", "ohg", "ug", "e v", "se",
    "sa", "s a", "sas", "s a s", "sarl", "s a r l", "srl", "s r l",
    "spa", "s p a", "sl", "s l", "ltda", "s r o", "a s", "kft", "zrt",
    "bv", "b v", "nv", "n v", "vof", "cv", "c v",
    "oy", "oyj", "ab", "as", "a/s", "aps", "asa",
    "kk", "k k", "pty", "pte", "pvt", "bhd", "sdn",
    "& co", "& cie", "and co", "und co",
]


def _form_regex(form: str) -> str:
    parts = []
    for ch in form:
        if ch == " ":
            parts.append(r"[\s.]*")
        elif ch == "&":
            parts.append(r"&\s*")
        else:
            parts.append(re.escape(ch) + r"\.?")
    return "".join(parts)


_LEGAL_FORM_AT_END = re.compile(
    r"[\s,]+(?:" + "|".join(_form_regex(f) for f in sorted(_LEGAL_FORMS, key=len, reverse=True)) + r")\.?[\s,.]*$"
)


class KeyRuleError(ValueError):
    """A name that cannot become a key part."""


def fold(text: str) -> str:
    """Step 1: plain Latin letters, lower case."""
    text = text.translate(_LATIN_EXTRA)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def non_latin_letters(text: str) -> list[str]:
    """Letters in a script other than Latin, after folding."""
    return [ch for ch in fold(text) if ch.isalpha() and not ch.isascii()]


def _check_latin(text: str) -> None:
    bad = non_latin_letters(text)
    if bad:
        raise KeyRuleError(f"only the Latin script is accepted; found {''.join(dict.fromkeys(bad))!r}")


def _dashes(text: str, keep: str = "") -> str:
    out = re.sub(r"[^a-z0-9" + re.escape(keep) + r"]", "-", text)
    return re.sub(r"-+", "-", out).strip("-")


def strip_legal_form(name: str) -> str:
    """Step 4: drop legal forms at the end, as long as something is left."""
    text = fold(name)
    while True:
        shorter = _LEGAL_FORM_AT_END.sub("", text)
        if shorter == text or not _dashes(shorter):
            return text
        text = shorter


def _part(text: str) -> str:
    for ch, word in _WORDS.items():
        text = text.replace(ch, word)
    return _dashes(text)


def publisher_part(name: str) -> str:
    _check_latin(name)
    part = _part(strip_legal_form(name))
    if not part:
        raise KeyRuleError("the name has no Latin letters or digits")
    return part


def product_part(name: str) -> str:
    _check_latin(name)
    part = _part(fold(name))
    if not part:
        raise KeyRuleError("the name has no Latin letters or digits")
    return part


def version_part(version: str) -> str:
    _check_latin(version)
    part = _dashes(fold(version), keep="._+")
    if not part:
        raise KeyRuleError("the version has no Latin letters or digits")
    return part


def make_key(publisher: str, product: str, version: str | None = None) -> str:
    key = f"{publisher_part(publisher)}/{product_part(product)}"
    if version is not None:
        key += "@" + version_part(version)
    return key


_PART = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^[a-z0-9._+]+(?:-[a-z0-9._+]+)*$")


def part_problems(part: str, what: str) -> list[str]:
    """Why a written key part does not follow the rule (empty if it does)."""
    problems = []
    if not _PART.match(part):
        problems.append(f"{what} {part!r} has characters other than a-z, 0-9 and single inner dashes")
    if len(part) > MAX_PART:
        problems.append(f"{what} {part!r} is {len(part)} characters; the maximum is {MAX_PART}")
    return problems


def key_problems(key: str) -> list[str]:
    """Why a written key does not have the form publisher/product[@version]."""
    key, _, version = key.partition("@")
    if key.count("/") != 1:
        return [f"key {key!r} must have the form publisher/product"]
    publisher, product = key.split("/")
    problems = part_problems(publisher, "publisher part") + part_problems(product, "product part")
    if version:
        if not _VERSION.match(version):
            problems.append(f"version {version!r} has characters other than a-z, 0-9, '.', '+', '_' and '-'")
        if len(version) > MAX_PART:
            problems.append(f"version {version!r} is {len(version)} characters; the maximum is {MAX_PART}")
    return problems
