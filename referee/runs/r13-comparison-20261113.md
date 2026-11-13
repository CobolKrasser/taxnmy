# R13 — comparison of A2, M and W (13 November 2026)

The three conditions of `referee/r13-research.md`, all run with Claude Opus 5.5 at effort medium on
the 29 cases of that note:

- **A2:** *fetched*, the base run with the ladder: `20261113-163823-fetched-claude-opus-5-5`.
- **M:** *memory*, with `prompt-r13.md`: `20261113-180523-memory-claude-opus-5-5`
  ([run 43479029566](https://github.com/CobolKrasser/taxnmy/actions/runs/43479029566)).
- **W:** *web*, with web search and web fetch and `prompt-r13.md`. The stop rule split it into
  `20261113-181026-web-claude-opus-5-5` (the first 3 cases,
  [run 43479952967](https://github.com/CobolKrasser/taxnmy/actions/runs/43479952967)) and
  `20261113-181300-web-claude-opus-5-5` (the other 26,
  [run 43480307164](https://github.com/CobolKrasser/taxnmy/actions/runs/43480307164)).

The earlier runs `20261113-174534-memory-…` and `20261113-174911-web-…` used the system prompt of A.
They are not part of this comparison, but their cost counts in the budget.

## Overview

| | A2 | M | W |
| --- | --- | --- | --- |
| bad cases let through | 0 | 0 | 0 |
| good cases rejected | 0 | 0 | 0 |
| sent to a person (*human*) | 8 | 14 | 14 |
| outcome in `expect` | 24 of 29 | 22 of 29 | 22 of 29 |
| confident and wrong | 0 | 1 (B25 S7) | 0 |
| cost of the 29 cases | $1.5540 | $0.5568 | $2.7798 |
| cost per case | $0.0576 (in A2) | $0.0192 | $0.0959 |

## Output of `compare`

| run | wrongly accepted | wrongly rejected | doubt | S4 right/wrong/doubt | S5 right/wrong/doubt | S7 right/wrong/doubt | S8 right/wrong/doubt | confident and wrong | cost per case | searches | fetches |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20261113-163823-fetched-claude-opus-5-5 | 0 | 0 | 8 | 17/0/2 | 12/0/6 | 14/0/3 | 17/0/2 | 0 | $0.0576 | 0 | 0 |
| 20261113-180523-memory-claude-opus-5-5 | 0 | 0 | 14 | 18/0/2 | 17/0/1 | 15/1/2 | 8/0/12 | 1 | $0.0192 | 0 | 0 |
| 20261113-181026-web-claude-opus-5-5 + 20261113-181300-web-claude-opus-5-5 | 0 | 0 | 14 | 16/0/4 | 11/0/7 | 12/0/6 | 13/0/7 | 0 | $0.0959 | 9 | 78 |

Different outcomes: B06, B14, B15, B20, B21, B22, B24, B25, B30, G02, G06, G08, G19, G20, G27, G28

With 29 cases a difference of one or two is noise (r13-research.md).

## Cases set out one by one

| case | 20261113-163823-fetched-claude-opus-5-5 | 20261113-180523-memory-claude-opus-5-5 | 20261113-181026-web-claude-opus-5-5 + 20261113-181300-web-claude-opus-5-5 |
| --- | --- | --- | --- |
| G06 | human (S4 doubt, S5 doubt, S6 doubt, S7 doubt, S8 doubt) | accept (all yes) | accept (all yes) |
| G17 | human (S5 doubt, S6 doubt) | human (S5 doubt) | human (S5 doubt, S6 doubt) |
| G21 | human (S5 doubt) | human (S8 doubt) | human (S5 doubt, S6 doubt, S7 doubt, S8 doubt) |
| G22 | human (S5 doubt) | human (S8 doubt) | human (S5 doubt) |
| B15 | human (S4 doubt, S5 doubt, S6 doubt, S7 doubt, S8 doubt) | alias (S4 no, S8 doubt) | alias (S4 no, S6 doubt, S7 doubt, S8 no) |

## Reading, following `r13-research.md` § How to read the result

The table there gives three readings. This result fits the second: *"M is as right as W and A on
S4 and S5, but not on S7 and S8."*

- **S4 and S5:** M is as right as A2, or more so: S4 18 against 17, S5 17 against 12. It doubts
  less on S5 because it does not need a page of the publisher. That page is often missing in the
  data (the run A2 review, §1 point 3).
- **S7:** about the same (15 against 14). M is wrong once, B25. The ground truth expects S7 to be
  *no* there because the Ring URL redirected, but the Video Doorbell 4 does exist. That looks like an
  artefact of the test set, not a hallucination.
- **S8:** M is clearly weaker (8 right and 12 doubt, against 17 and 2). Memory cannot confirm a
  specific value such as a GTIN, a CPE or a versions URL. This is what S8 is for.
- **Bad cases:** none of the three conditions lets one through.
- **W** is not more often right than A2. It sends more cases to a person and costs the most. It does
  read what our ladder cannot: G06 (Sony) is *accept* in W and in M, against *human* in A2. W also
  found the alias for B15.
- **The difference of 2 cases in `expect`** (24 against 22) is noise, by the rule of that note.

**The five cases the regie asked about:**
- **G06:** the source is closed in A2. M and W accept it: the web fetch of the API reads sony.nl.
- **G17, G21 and G22:** *human* in all three conditions. The doubt is on S5 (or S8). The publisher
  sources in the data remain the cause, whatever the evidence.
- **B15:** *human* in A2, because Sony refused. In M and W it is *alias* of `sony/ilce-7m4`, as
  `expect` wants.

The decision on R13 is Fred's.

## Budget

| | |
| --- | --- |
| M | $0.5568 |
| W | $0.2271 + $2.5527 = $2.7798 |
| spent on all runs | **$10.6950 of $20.00** |
| left | $9.3050 |
