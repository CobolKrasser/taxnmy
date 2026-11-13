# Notes on run W, first 3 cases (stop rule)

R13 condition W, Claude Opus 5.5, effort medium, on the first 3 of the 29 cases, following the stop
rule of `tax-fase2-run-a2-beoordeling.md` §3. [Run 43476499234](https://github.com/CobolKrasser/taxnmy/actions/runs/43476499234).

- Cost: $0.2645, which is $0.0882 per case (max $0.1277). Each case used 3 web searches.
- Extrapolated: 26 more cases cost about $2.30, so all runs together would come to about $9.70. That
  is under the stop line of $17.
- Outcome: all 3 cases went to *human*.
  - G02 and G06 got *doubt* on every rule.
  - G08 got *yes* on S4–S7, from a third-party page (9to5google), and *doubt* on S8.

**Why the run stopped after 3 cases.** The harness gives W only `web_search`, as `r13-research.md`
describes it. It has no `web_fetch`. The model finds pages with a search, but it cannot read the URLs
of the proposal itself, so S8 is *doubt* every time. The review of run A2 (§3) assumed that W uses
the web fetch of the API. Running the other 26 cases in this form would mostly measure that missing
tool. The choice is with the regie: add `web_fetch` to W and run it again, or go on as it is.
