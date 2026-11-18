#!/usr/bin/env python3
"""The daily digest for a maintainer (TAX-37): what waits on a person, and what came in without one.

    show     the digest as text: per publisher what waits on a decision (TV3, TV5, a collision, an
             audit finding), with the sources side by side, how many proposals, the last date and the
             pull requests; then the publishers that were added without a person since a date.
             With nothing waiting it still says so: a digest that does not come means nobody looked.
             --urgent: only a collision or an audit finding within three days of its last date, and
             nothing at all when there is none (a second digest on the same day).
    answer   carry out one answer for one publisher: `approve` or `reject`. The decision is recorded
             the way the parking list records it (TAX-29), and every proposal of that publisher that
             waits on a decision gets its outcome: on reject it leaves the parking list; on approve it
             waits on the referee again, which no longer holds it for the same reason.

`show` writes nothing. `answer` changes only referee/ (the parking list and the decisions); it does not
merge, close or comment: who runs it does that with the pull requests it prints.

Text in a proposal is data. The digest quotes it on one line, cut short, and marks a proposal whose
text speaks to whoever judges it (TV1): that is a finding, never an instruction.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# The fields of the parking list (TAX-29). One place, so a different name is one line.
WAITS_ON, DECISION, AGAIN = "waits_on", "decision", "event"  # waits on a person; waits on the referee
PUBLISHER, DUE, KIND = "publisher", "due", "kind"
URGENT = ("collision", "audit")  # no expiry, a decision within 14 days
URGENT_DAYS = 3

QUESTIONS = {
    "TV3": "lijkt op een bestaande naam of een bestaand domein: dezelfde partij, een andere, of een nabootsing?",
    "TV5": "verplaatst een bron of uitgever naar een ander domein: is deze wijziging echt?",
    "collision": "twee partijen eisen hetzelfde product op: wie van de twee is de uitgever?",
    "audit": "een bevinding uit een audit: klopt dit product nog (laten staan, herstellen of weghalen)?",
}

# Text that speaks to whoever judges or runs the digest. Generic words, no names (TV1, TG4).
ADDRESSED = re.compile(
    r"\b(approve|approved|accept this|merge this|ignore (all|any|the|previous)|instructions?|assistant|"
    r"session|maintainer|reviewer|goedkeur\w*|keur\w* .{0,20}goed|sessie|beheerder|negeer)\b", re.I)


def load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return list((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("parked") or [])


def quote(text, limit: int = 160) -> str:
    """Untrusted text on one line, cut short, without markup that could pass for the digest's own."""
    text = re.sub(r"\s+", " ", str(text or "")).strip().replace("`", "'")
    return "`" + (text[:limit] + "…" if len(text) > limit else text) + "`"


def kind_of(entry: dict) -> str:
    if entry.get(KIND) in URGENT:
        return entry[KIND]
    return "TV5" if "TV5" in str(entry.get("rule", "")) else "TV3"


def publisher_of(entry: dict) -> str:
    return entry.get(PUBLISHER) or str(entry.get("key", "")).split("/")[0]


def waiting(entries: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in entries:
        if e.get(WAITS_ON) == DECISION:
            out.setdefault(publisher_of(e), []).append(e)
    return dict(sorted(out.items()))


def sources(files: dict, root: Path) -> tuple[list[str], list[str]]:
    """The URLs proposed, and the URLs of the same files as they are now (empty for a new file)."""
    new, now = [], []
    for rel, text in (files or {}).items():
        new += guard.urls_in(yaml.safe_load(text) or {})
        if (root / rel).is_file():
            now += guard.urls_in(yaml.safe_load((root / rel).read_text(encoding="utf-8")) or {})
    return sorted(set(new)), sorted(set(now))


def addressed(entry: dict) -> bool:
    return any(ADDRESSED.search(str(t)) for t in (entry.get("files") or {}).values())


def added_publishers(root: Path, since: str) -> list[dict]:
    """Publisher files added on the current branch since `since` (a date git understands)."""
    log = subprocess.run(["git", "-C", str(root), "log", f"--since={since}", "--diff-filter=A", "--name-only",
                          "--format=", "--", "publishers/"], capture_output=True, text=True, check=True).stdout
    out = []
    for rel in sorted({line for line in log.splitlines() if line.endswith(".yaml")}):
        if (root / rel).is_file():
            p = yaml.safe_load((root / rel).read_text(encoding="utf-8")) or {}
            out.append({"key": p.get("key"), "name": p.get("name"), "domain": guard.own_domain(p),
                        "second": bool(p.get("second_source"))})
    return out


def show(root: Path, today: date, since: str, urgent: bool = False) -> str:
    groups = waiting(load(root / "referee/parked.yaml"))
    if urgent:
        limit = (today + timedelta(days=URGENT_DAYS)).isoformat()
        groups = {p: [e for e in es if e.get(KIND) in URGENT and str(e.get(DUE) or "9999") <= limit]
                  for p, es in groups.items()}
        groups = {p: es for p, es in groups.items() if es}
        if not groups:
            return ""
    lines = [f"TAXNMY {today.isoformat()}" + (" — dringend" if urgent else "")]
    if not groups:
        lines.append("Vandaag niets te keuren.")
    for pub, entries in groups.items():
        due = min((str(e.get(DUE)) for e in entries if e.get(DUE)), default="geen")
        prs = sorted({e.get("pr") for e in entries if e.get("pr")})
        lines += ["", f"## {pub}: {len(entries)} voordracht(en), uiterlijk {due}"
                  + (f", PR {', '.join(f'#{n}' for n in prs)}" if prs else "")]
        for e in entries:
            new, now = sources(e.get("files"), root)
            lines.append(f"- {e.get('key')}: {QUESTIONS[kind_of(e)]}")
            lines.append(f"  reden: {quote(e.get('reason'))}")
            lines.append(f"  voorgesteld: {', '.join(quote(u, 100) for u in new) or '-'}")
            lines.append(f"  nu in TAXNMY: {', '.join(quote(u, 100) for u in now) or '-'}")
            if addressed(e):
                lines.append("  bevinding: tekst in deze voordracht richt zich tot wie keurt; niets daarvan is uitgevoerd.")
        lines.append("Advies: ")
    if not urgent:
        lines += ["", f"## Nieuwe uitgevers zonder mens opgenomen (sinds {since})"]
        added = added_publishers(root, since)
        lines += [f"- {quote(p['name'], 60)} ({p['key']}), {p['domain'] or '-'}, tweede bron: {'ja' if p['second'] else 'nee'}"
                  for p in added] or ["- geen"]
    if groups:
        lines += ["", "Antwoord per uitgever: `<uitgever> goed` of `<uitgever> af`, met een reden als je wilt."]
    return "\n".join(lines) + "\n"


def answer(root: Path, publisher: str, decision: str, reason: str, today: date, record=None) -> dict:
    """One answer for one publisher. `record(publisher, decision, reason, day, root)` is how the
    parking list keeps a decision (TAX-29); without it nothing is changed."""
    import pr_referee
    record = record or getattr(pr_referee, "record_decision", None)
    if record is None:
        raise SystemExit("the parking list keeps no decisions yet (TAX-29): nothing changed")
    path = root / "referee/parked.yaml"
    entries = load(path)
    mine = [e for e in entries if e.get(WAITS_ON) == DECISION and publisher_of(e) == publisher]
    if not mine:
        raise SystemExit(f"nothing of {publisher} waits on a decision: nothing changed")
    record(publisher, decision, reason, today.isoformat(), root)
    if decision == "reject":
        entries = [e for e in entries if e not in mine]
    else:
        for e in mine:
            e[WAITS_ON] = AGAIN
    pr_referee.save_parked(entries, path)
    return {"keys": [e["key"] for e in mine], "prs": sorted({e["pr"] for e in mine if e.get("pr")})}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", default=str(REPO))
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("show", help="the digest as text; writes nothing")
    s.add_argument("--since", default="1 day ago", help="new publishers since (the previous digest)")
    s.add_argument("--urgent", action="store_true", help="only what is within three days of its last date")
    a = sub.add_parser("answer", help="carry out one answer for one publisher")
    a.add_argument("publisher")
    a.add_argument("decision", choices=("approve", "reject"))
    a.add_argument("--reason", required=True)
    args = p.parse_args(argv)
    root = Path(args.root)
    if args.command == "show":
        print(show(root, date.today(), args.since, args.urgent), end="")
    else:
        done = answer(root, args.publisher, args.decision, args.reason, date.today())
        print(f"{args.decision}: {', '.join(done['keys'])}; pull requests: {', '.join(map(str, done['prs'])) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
