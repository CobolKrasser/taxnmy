"""The sites that code treats as a class (TG4), read from referee/sites.yaml.

The code in tools/ names no site, brand or register. What it needs to know about a site (an AI chat,
a site with many owners, an API) is data in one file, which a maintainer changes with a pull request.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parent.parent / "referee/sites.yaml"


@lru_cache(maxsize=1)
def load() -> dict:
    data = yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise SystemExit(f"{PATH}: not version 1")
    return data


def names(key: str) -> tuple:
    """A list of the file as a tuple of lower-case names."""
    return tuple(str(v).lower() for v in load().get(key) or [])


def value(key: str):
    return load()[key]
