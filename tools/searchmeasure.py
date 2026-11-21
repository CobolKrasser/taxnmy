"""A measurement of the model of step 4 (TAX-49): the searches of an earlier run, repeated.

The searches of an earlier run (its search.json, closed URL -> product, query, domains and the
URLs found then) are sent again, `--repeats` times, on one model, within a cap. Nothing is judged.
Then, without the key, the URLs are read as a round reads them: per product, whether a page on
the publisher's domains is readable and shows the name. The report sets each run next to the
earlier run.

    python tools/searchmeasure.py search --base search.json --base-results results.jsonl \\
        --repeats 3 --model claude-haiku-5-5 --cap 0.75 --output measure.json [--live]   # the key
    python tools/searchmeasure.py read --measure measure.json --output report.md [--no-browser]  # no key

The searches count against the budget for every run together (tools/referee.py), one run
directory per repeat, as any search does.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import referee  # noqa: E402
from fetch import Fetcher, moved  # noqa: E402

MAX_REPEATS = 5


def load_base(path: Path) -> dict:
    """closed URL -> what to search for, from a search.json (step 4) or a closed.json (a round)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    wanted = {}
    for url, hit in data.items():
        if not hit.get("domains") or not hit.get("product"):
            continue  # never a search without domains
        wanted[url] = {"product": hit["product"], "publisher": hit.get("publisher", ""), "domains": hit["domains"]}
        if hit.get("query"):
            wanted[url]["query"] = hit["query"]
    return wanted


def load_records(path: Path | None) -> list:
    if not path or not Path(path).is_file():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def search(args, client=None, now=None, log=print) -> dict:
    """`repeats` runs of the searches of the base, on one model, within `cap` dollars together."""
    if not 1 <= args.repeats <= MAX_REPEATS:
        raise SystemExit(f"--repeats is 1 to {MAX_REPEATS}")
    model = args.model or referee.SEARCH_MODEL
    wanted = load_base(Path(args.base))
    prices = referee.load_prices(Path(args.prices))
    queries = len({(w.get("query") or w["product"], tuple(w["domains"])) for w in wanted.values()})
    expected = queries * args.repeats * referee.search_cost(model, prices)
    log(f"{queries} searches x {args.repeats} runs on {model} ({referee.search_tool(model)}): about ${expected:.2f}; "
        f"cap ${args.cap:.2f}")
    base = {"label": args.base_label, "found": json.loads(Path(args.base).read_text(encoding="utf-8")),
            "records": load_records(args.base_results)}
    result = {"model": model, "repeats": args.repeats, "expected": expected, "base": base, "runs": []}
    if expected > args.cap:
        result["stopped"] = f"About ${expected:.2f} passes the cap of ${args.cap:.2f}: nothing was sent"
        log(result["stopped"])
    elif not args.live:
        log("Dry run: nothing was searched. Add --live to search.")
    else:
        out = Path(args.out)
        start = now or datetime.now(timezone.utc)
        spent = 0.0
        for n in range(args.repeats):
            records: list = []
            # The cap holds for the measurement as a whole: each run gets what the earlier ones left.
            found, cost = referee.run_searches(wanted, model, args.budget, Path(args.prices), out, client,
                                               start + timedelta(seconds=n), log, cap=args.cap - spent, records=records)
            spent += cost
            run_dir = next(iter(sorted(out.glob(f"{(start + timedelta(seconds=n)).strftime('%Y%m%d-%H%M%S')}-search-{model}"))), None)
            result["runs"].append({"label": f"{model}, run {n + 1}", "found": found, "records": records,
                                   "dir": str(run_dir) if run_dir else ""})
            if len(records) < queries:
                result["stopped"] = f"Run {n + 1} did {len(records)} of {queries} searches (the cap, or errors): stopped"
                log(result["stopped"])
                break
        result["spent"] = spent
        log(f"spent ${spent:.4f} of the cap of ${args.cap:.2f}")
    out_file = Path(args.output)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
            for n, run in enumerate(result["runs"], 1):
                if run["dir"]:
                    gh.write(f"dir_{n}={run['dir']}\nname_{n}={Path(run['dir']).name}\n")
    return result


def product_read(closed: str, hit: dict, fetcher) -> tuple[list, list, str]:
    """(URLs on the publisher's domains, URLs elsewhere, the first page read that shows the name or '').
    Read as a round reads (tools/importer.py check_pages): only URLs on the domains, never the closed one."""
    urls = [u for u in hit.get("urls") or [] if u != closed]
    on = [u for u in urls if referee.on_domains(u, hit.get("domains"))][:referee.SEARCH_RESULTS]
    elsewhere = [u for u in urls if not referee.on_domains(u, hit.get("domains"))]
    for u in on:
        src = fetcher.fetch(u, "")
        if gate.read(src) and not moved(src) and gate.appears(hit["product"], src.text):
            return on, elsewhere, u
    return on, elsewhere, ""


def elsewhere_of(closed: str, hit: dict, records: list) -> list:
    """The URLs off the domains: kept in results.jsonl by the search now, in the URLs of an older run."""
    for r in records:
        if closed in r.get("for", []) and "elsewhere" in r:
            return list(r["elsewhere"])
    return [u for u in hit.get("urls") or [] if not referee.on_domains(u, hit.get("domains"))]


def cost_of(closed: str, records: list):
    for r in records:
        if closed in r.get("for", []):
            return r.get("cost")
    return None


def read(args, fetcher=None, log=print) -> str:
    data = json.loads(Path(args.measure).read_text(encoding="utf-8"))
    fetcher = fetcher or Fetcher(use_browser=not args.no_browser, log=log)
    runs = [data["base"]] + data["runs"]
    products = list(data["base"]["found"])
    rows: dict[str, list] = {}
    totals = []
    try:
        for run in runs:
            read_n = on_n = else_n = 0
            cost = 0.0
            for closed in products:
                hit = run["found"].get(closed)
                if hit is None:
                    rows.setdefault(closed, []).append(None)
                    continue
                on, _, page = product_read(closed, hit, fetcher)
                elsewhere = elsewhere_of(closed, hit, run["records"])
                c = cost_of(closed, run["records"])
                rows.setdefault(closed, []).append((len(on), page, elsewhere, c))
                read_n += bool(page)
                on_n += len(on)
                else_n += len(elsewhere)
                cost += c or 0.0
            totals.append((run["label"], read_n, on_n, else_n, cost, sum(1 for p in products if p in run["found"])))
    finally:
        if hasattr(fetcher, "close"):
            fetcher.close()
    lines = ["# Measurement of the model of step 4 (TAX-49)", "",
             f"{len(products)} searches of {data['base']['label']}, {len(data['runs'])} runs on {data['model']}. "
             "Nothing was judged. A product counts as read when a URL on the publisher's domains is readable and "
             "shows the name, as a round reads it. URLs elsewhere are never read.", ""]
    if data.get("stopped"):
        lines += [f"**Stopped:** {data['stopped']}", ""]
    lines += ["| run | searched | products read | URLs on the domains | URLs elsewhere (never read) | cost |",
              "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {label} | {n} | {r} of {n} | {on} | {el} | ${c:.4f} |" for label, r, on, el, c, n in totals]
    lines += ["", "Per product: URLs on the domains / read / elsewhere / cost, per run.", "",
              "| product | " + " | ".join(t[0] for t in totals) + " |", "| --- |" + " --- |" * len(totals)]
    for closed in products:
        name = data["base"]["found"][closed]["product"]
        cells = []
        for cell in rows.get(closed, []):
            if cell is None:
                cells.append("not searched")
                continue
            on, page, elsewhere, c = cell
            cells.append(f"{on} / {'yes' if page else '**no**'} / {len(elsewhere)}"
                         + (f" / ${c:.4f}" if c is not None else ""))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "## URLs elsewhere", ""]
    for closed in products:
        for (label, *_), cell in zip(totals, rows.get(closed, [])):
            if cell and cell[2]:
                lines.append(f"- {data['base']['found'][closed]['product']}, {label}: " + ", ".join(cell[2]))
    lines += ["", "The earlier run kept its first 5 URLs before the domain check; a search now keeps the first 5 "
              "on the domains. The earlier run is one run: the bar, not a measured mean.", ""]
    text = "\n".join(lines)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    log(text)
    return text


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("search", help="repeat the searches of an earlier run (the key; costs money)")
    s.add_argument("--base", required=True, help="search.json of an earlier run, or a closed.json")
    s.add_argument("--base-results", help="results.jsonl of that run: its cost per search")
    s.add_argument("--base-label", default="the earlier run", help="the name of that run in the report")
    s.add_argument("--repeats", type=int, default=3)
    s.add_argument("--model", default="", help=f"empty: the model of step 4 ({referee.SEARCH_MODEL})")
    s.add_argument("--cap", type=float, required=True, help="dollars, for this measurement (hard)")
    s.add_argument("--budget", type=float, default=referee.BUDGET, help="dollars, for every run together")
    s.add_argument("--prices", default=str(referee.REFEREE / "prices.yaml"))
    s.add_argument("--out", default=str(referee.REFEREE / "runs"))
    s.add_argument("--output", required=True, help="measure.json, for `read`")
    s.add_argument("--live", action="store_true", help="search; without it nothing is sent")
    r = sub.add_parser("read", help="read what the searches found and write the report (no key, free)")
    r.add_argument("--measure", required=True)
    r.add_argument("--output", required=True)
    r.add_argument("--no-browser", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "search":
        search(args)
    else:
        read(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
