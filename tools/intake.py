"""The limits of the intake (TAX-32): what one proposal and one submitter may use (TV8), what goes over
them is rejected and never parked (TV9), known submitters go first (TV10), and the intake can be
closed without touching how the data is read (TV11).

The values are in referee/intake.yaml, in one place. A submitter is the account of a pull request;
whether it is known (a pull request of it was taken in) and how many proposals it has in earlier
open pull requests are facts the workflow asks GitHub for, on main, without code of the pull
request. An agreed round has no code of its own: it is a submitter with values of its own.

No network, no AI, no keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
INTAKE = "referee/intake.yaml"
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")
URL_FIELDS = {"source", "url", "second_source"}
OVERRIDES = ("open", "day_cap", "pr_cap")


@dataclass(frozen=True)
class Submitter:
    login: str
    known: bool
    open_before: int = 0  # proposals in earlier open pull requests of this submitter

    def __post_init__(self):
        if not LOGIN.fullmatch(self.login):
            raise ValueError(f"{self.login[:60]!r} is not an account name")
        if self.open_before < 0:
            raise ValueError("open_before is not a count")


def load(root: Path = REPO) -> dict:
    """The limits: referee/intake.yaml in `root`, else those of the repository. Checked, so a typing
    error in the file stops the run instead of opening the intake."""
    path = root / INTAKE if (root / INTAKE).is_file() else REPO / INTAKE
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if cfg.get("version") != 1 or not isinstance(cfg.get("open"), bool):
        raise SystemExit(f"{path}: not a file of limits of version 1")
    for key in ("sources_per_proposal", "parking_list"):
        if not isinstance(cfg.get(key), int) or cfg[key] < 0:
            raise SystemExit(f"{path}: {key} is not a count")
    per = cfg.get("open_per_submitter") or {}
    if not all(isinstance(per.get(k), int) and per[k] >= 0 for k in ("unknown", "known")):
        raise SystemExit(f"{path}: open_per_submitter needs a count for unknown and known")
    share = cfg.get("unknown_share_of_day")
    if not isinstance(share, (int, float)) or not 0 <= share <= 1:
        raise SystemExit(f"{path}: unknown_share_of_day is not a part between 0 and 1")
    for login, values in (cfg.get("submitters") or {}).items():
        if not isinstance(values, dict) or set(values) - set(OVERRIDES):
            raise SystemExit(f"{path}: submitter {login}: only {', '.join(OVERRIDES)}")
    return cfg


def own(cfg: dict, login: str) -> dict:
    """The values of its own of a submitter (an agreed round), or {}."""
    return dict((cfg.get("submitters") or {}).get(login) or {})


def open_limit(cfg: dict, sub: Submitter) -> int:
    mine = own(cfg, sub.login)
    if "open" in mine:
        return int(mine["open"])
    return int(cfg["open_per_submitter"]["known" if sub.known else "unknown"])


def urls(files: dict) -> set:
    """The distinct URLs in the URL fields of the files of a proposal (text that is not YAML has none)."""
    found: set = set()

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key in URL_FIELDS:
            found.add(node.strip())

    for text in files.values():
        try:
            walk(yaml.safe_load(text))
        except yaml.YAMLError:
            pass
    return found


def refusals(proposals: list[dict], sub: Submitter | None, cfg: dict, parked: list[dict]) -> dict:
    """proposal id -> (rule, reason) for each proposal that goes over a limit (TV8, TV9) or finds the
    intake closed (TV11); the others are admitted. Without a submitter (the test set, an import run
    by a maintainer) only the limit per proposal applies. The proposals are taken in their order;
    one that is refused for itself does not use the room of the submitter."""
    out: dict = {}
    if sub is not None and not cfg["open"]:
        return {p["id"]: ("TV11", "the intake for proposals is closed; nothing new is taken in for now")
                for p in proposals}
    most = cfg["sources_per_proposal"]
    for p in proposals:
        n = len(urls(p["files"]))
        if n > most:
            out[p["id"]] = ("TV9", f"{n} sources in one proposal; the limit is {most} (TV8)")
    if sub is None:
        return out
    limit = open_limit(cfg, sub)
    waiting = sum(1 for e in parked if e.get("submitter") == sub.login)
    room = limit - waiting - sub.open_before
    kind = "with values of its own" if "open" in own(cfg, sub.login) else ("known" if sub.known else "unknown")
    for p in proposals:
        if p["id"] in out:
            continue
        if room > 0:
            room -= 1
            continue
        out[p["id"]] = ("TV9", f"the limit of {limit} open proposals per submitter ({kind}) is reached: "
                               f"{waiting} on the parking list, {sub.open_before} in earlier open pull requests (TV8)")
    return out


def cap_parking(items: list[dict], on_list: int, cfg: dict) -> list[dict]:
    """TV9: a new proposal that would be parked while the parking list is full is rejected instead.
    Known submitters first, so the room left goes to them. Returns the items it rejected."""
    most = cfg["parking_list"]
    rejected = []
    for item in sorted((i for i in items if i["outcome"] == "parked"), key=lambda i: not i.get("known")):
        if on_list < most:
            on_list += 1
            continue
        item.update(outcome="reject", rule="TV9", by="code", waits_for="",
                    reason=f"the parking list is full ({most} proposals); a proposal that would be parked is "
                           f"rejected instead. You may propose it again later")
        rejected.append(item)
    return rejected


def first_known(items: list[dict]) -> list[dict]:
    """TV10: the order in which the referee judges: known submitters first, otherwise as they came."""
    return sorted(items, key=lambda i: not i.get("known"))
