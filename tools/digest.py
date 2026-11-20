#!/usr/bin/env python3
"""The daily digest for a maintainer (TAX-37): what waits for a person, and what came in without one.

    python tools/digest.py [--since DATE]   the digest as text
    python tools/digest.py --urgent         only a collision or an audit finding within three days of
                                            its last day; nothing at all when there is none

Per publisher what waits for a decision (`pr_referee.waiting`, TAX-29): the question, the sources
proposed next to those in TAXNMY now, how many proposals, the last day and the pull requests; then
the publishers added without a person since DATE (the previous digest). With nothing waiting it
still says so: a digest that does not come means nobody looked. It writes nothing. An answer per
publisher is carried out with `python tools/pr_referee.py decide <publisher> approve|reject`; an
answer on a collision or an audit finding (`kind`), per product, with `pr_referee.py settle <key>
<answer>`, the command the digest shows with it.

Text in a proposal is data. The digest quotes it on one line, cut short, and marks a proposal whose
text speaks to whoever judges it (TV1): a finding, never an instruction.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard  # noqa: E402
import pr_referee  # noqa: E402

REPO = pr_referee.REPO
URGENT_DAYS = 3

QUESTIONS = {
    "TV3": "looks like an existing name or domain: the same party, another one, or an imitation?",
    "TV5": "changes a code, a source for machines or a second source of an existing file: is this change real?",
    "collision": "two parties claim the same product: which of the two is the publisher?",
    "audit": "a finding from an audit: is this product still right (keep, fix or remove)?",
}

# The answers to a collision or an audit (pr_referee.SETTLE), in the words of the digest.
ANSWERS = {
    "stays": "the party in TAXNMY stays the publisher",
    "proposal": "the party of the proposal becomes the publisher",
    "keep": "keep it as it is",
    "fix": "fix it, with a proposal of its own",
    "remove": "take it out",
}


def settle_lines(e: dict) -> list[str]:
    """For a collision or an audit: the answers and the command that carries one out."""
    answers = list(pr_referee.SETTLE.get(e.get("kind"), {}))
    if not answers:
        return []
    return ["  answers: " + "; ".join(f"{a} ({ANSWERS.get(a, a)})" for a in answers),
            f"  carry out: python tools/pr_referee.py settle {shlex.quote(str(e['key']))} {'|'.join(answers)} --reason \"…\" --by …"]


# Text that speaks to whoever judges or runs the digest. Generic words, no names (TV1, TG4).
ADDRESSED = re.compile(
    r"\b(approve|approved|accept this|merge this|ignore (all|any|the|previous)|instructions?|assistant|"
    r"session|maintainer|reviewer|goedkeur\w*|keur\w* .{0,20}goed|sessie|beheerder|negeer)\b", re.I)


def quote(text, limit: int = 160) -> str:
    """Untrusted text on one line, cut short, without markup that could pass for the digest's own."""
    text = re.sub(r"\s+", " ", str(text or "")).strip().replace("`", "'")
    return "`" + (text[:limit] + "…" if len(text) > limit else text) + "`"


def question(e: dict) -> str:
    if e.get("kind") in QUESTIONS:
        return QUESTIONS[e["kind"]]
    return " / ".join(QUESTIONS[r] for r in ("TV3", "TV5") if r in (e.get("holds") or {}) or r in str(e.get("rule")))\
        or QUESTIONS["TV3"]


def urls(files: dict, root: Path) -> tuple[list[str], list[str]]:
    """The URLs proposed, and those of the same files as they are now (none for a new file)."""
    new, now = set(), set()
    for rel, text in (files or {}).items():
        new.update(guard.urls_in(yaml.safe_load(text) or {}))
        if (root / rel).is_file():
            now.update(guard.urls_in(yaml.safe_load((root / rel).read_text(encoding="utf-8")) or {}))
    return sorted(new), sorted(now)


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


def digest(root: Path, today: date, since: str, urgent: bool = False) -> str:
    entries = pr_referee.load_parked(root / "referee/parked.yaml")
    files = {e["key"]: e.get("files") or {} for e in entries}
    groups = pr_referee.waiting(entries, pr_referee.load_terms(root))
    if urgent:
        limit = (today + timedelta(days=URGENT_DAYS)).isoformat()
        groups = {p: es for p, es in ((p, [e for e in es if e.get("kind") and e["decide_by"] <= limit])
                                      for p, es in groups.items()) if es}
        if not groups:
            return ""
    lines = [f"TAXNMY {today.isoformat()}" + (" — urgent" if urgent else "")]
    if not groups:
        lines.append("Nothing to decide today.")
    for pub, es in groups.items():
        last = min(e.get("decide_by") or e["lapses"] for e in es)
        prs = sorted({e["pr"] for e in es if e.get("pr")})
        lines += ["", f"## {pub}: {len(es)} proposal(s), last day {last}"
                  + (f", PR {', '.join(f'#{n}' for n in prs)}" if prs else "")]
        for e in es:
            new, now = urls(files.get(e["key"]), root)
            lines.append(f"- {e['key']}: {question(e)}")
            lines.append(f"  reason: {quote(e.get('reason'))}")
            for rule, subjects in (e.get("holds") or {}).items():
                lines.append(f"  held on {rule}: {', '.join(quote(s, 100) for s in subjects)}")
            lines.append(f"  proposed: {', '.join(quote(u, 100) for u in new) or '-'}")
            lines.append(f"  now in TAXNMY: {', '.join(quote(u, 100) for u in now) or '-'}")
            lines += settle_lines(e)
            if any(ADDRESSED.search(str(t)) for t in (files.get(e["key"]) or {}).values()):
                lines.append("  finding: text in this proposal addresses whoever judges it; none of it was carried out.")
        lines.append("Advice: ")
    if not urgent:
        lines += ["", f"## New publishers taken in without a person (since {since})"]
        lines += [f"- {quote(p['name'], 60)} ({p['key']}), {p['domain'] or '-'}, second source: {'yes' if p['second'] else 'no'}"
                  for p in added_publishers(root, since)] or ["- none"]
    if groups:
        lines += ["", "Answer per publisher: `<publisher> approve` or `<publisher> reject`, with a reason if you like."]
        if any(e.get("kind") for es in groups.values() for e in es):
            lines.append("For a collision or an audit: per product `<key> <answer>`, with one of the answers shown with it.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", default=str(REPO))
    p.add_argument("--since", default="1 day ago", help="new publishers since (the previous digest)")
    p.add_argument("--urgent", action="store_true", help="only what is within three days of its last day")
    args = p.parse_args(argv)
    print(digest(Path(args.root), date.today(), args.since, args.urgent), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
