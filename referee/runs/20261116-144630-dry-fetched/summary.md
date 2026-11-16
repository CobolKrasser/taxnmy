# Dry run 20261116-144630-dry-fetched

Nothing was sent to the API. The gate (`tools/gate.py`) settles what code can; the rest is what a live run would ask the referee.

Source texts: `sources.json` from the artifact `referee-sources-final-43462875320` of run A2, and for B31–B35 a `fetch --no-browser` of 16 November (not committed: third-party texts).

TAX-24: before this change the same 65 cases gave 100 of 174 judgments by code (S5 27, S6 38, S8 34 yes and 1 no); on the 60 cases of A2, 85 of 159. Now 60 of 174, and 53 of 159 on those 60. The old gate settled the rule that catches the error as *yes* for all five new cases (S5 for B31–B33, S8 for B34–B35): the referee was never asked.

## The gate: S5, S6 and S8 by code, before the referee

| measure | value |
| --- | --- |
| cases | 65 |
| stopped before the gate (lint error, lint mismatch, product source 404) | 7 |
| cases at the gate | 58 |
| S5, S6 and S8 judgments of those cases | 174 |
| settled by code | 60 (34%) |
| not needed, because code rejected the case | 2 |
| left for the referee | 112 (64%) |

| rule | code: yes | code: no | not needed | referee |
| --- | --- | --- | --- | --- |
| S5 | 21 | 0 | 1 | 36 |
| S6 | 38 | 0 | 1 | 19 |
| S8 | 0 | 1 | 0 | 57 |

## Per case

| case | stopped before | S5 | S6 | S8 | referee judges | outcome in the replayed run | with the gate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G01 | - | yes | yes | referee | S4, S7, S8 | accept | accept |
| G02 | - | yes | yes | referee | S4, S7, S8 | accept | accept |
| G03 | - | yes | yes | referee | S4, S7, S8 | human | human |
| G04 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| G05 | - | yes | yes | referee | S4, S7, S8 | accept | accept |
| G06 | - | referee | referee | referee | S4, S5, S6, S7, S8 | human | human |
| G07 | - | referee | yes | referee | S4, S5, S7, S8 | reject | reject |
| G08 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| G09 | - | referee | yes | referee | S4, S5, S7, S8 | human | human |
| G10 | - | yes | yes | referee | S4, S7, S8 | accept | accept |
| G11 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| G12 | - | referee | referee | referee | S4, S5, S6, S7, S8 | human | human |
| G13 | - | yes | yes | referee | S4, S7, S8 | accept | accept |
| G14 | - | referee | referee | referee | S4, S5, S6, S7, S8 | accept | accept |
| G15 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| G16 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| G17 | - | referee | yes | referee | S4, S5, S7, S8 | human | human |
| G18 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| G19 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| G20 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| G21 | - | referee | yes | referee | S4, S5, S7, S8 | human | human |
| G22 | - | referee | yes | referee | S4, S5, S7, S8 | human | human |
| G23 | - | referee | yes | referee | S4, S5, S7, S8 | human | human |
| G24 | - | referee | yes | referee | S4, S5, S7, S8 | human | human |
| G25 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| G26 | - | yes | yes | referee | S4, S7, S8 | accept | accept |
| G27 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| G28 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| H01 | - | referee | yes | referee | S4, S5, S7, S8 | accept | accept |
| H02 | - | referee | yes | referee | S4, S5, S7, S8 | human | human |
| B01 | lint S1 | - | - | - | - | reject | reject |
| B02 | lint S1 | - | - | - | - | reject | reject |
| B03 | lint S2 | - | - | - | - | reject | reject |
| B04 | lint S2 | - | - | - | - | reject | reject |
| B05 | lint S3 | - | - | - | - | reject | reject |
| B06 | - | - | - | no | - | human | reject |
| B07 | - | yes | referee | referee | S4, S6, S7, S8 | alias | alias |
| B08 | - | yes | referee | referee | S4, S6, S7, S8 | alias | alias |
| B09 | - | yes | referee | referee | S4, S6, S7, S8 | alias | alias |
| B10 | - | referee | referee | referee | S4, S5, S6, S7, S8 | alias | alias |
| B11 | - | referee | referee | referee | S4, S5, S6, S7, S8 | alias | alias |
| B12 | - | yes | referee | referee | S4, S6, S7, S8 | alias | alias |
| B13 | - | yes | referee | referee | S4, S6, S7, S8 | alias | alias |
| B14 | - | yes | referee | referee | S4, S6, S7, S8 | alias | alias |
| B15 | - | referee | referee | referee | S4, S5, S6, S7, S8 | human | human |
| B16 | - | yes | referee | referee | S4, S6, S7, S8 | reject | reject |
| B17 | - | referee | referee | referee | S4, S5, S6, S7, S8 | reject | reject |
| B18 | - | yes | referee | referee | S4, S6, S7, S8 | reject | reject |
| B19 | - | yes | referee | referee | S4, S6, S7, S8 | reject | reject |
| B20 | S7 404 | - | - | - | - | reject | reject |
| B21 | - | yes | referee | referee | S4, S6, S7, S8 | reject | reject |
| B22 | - | yes | referee | referee | S4, S6, S7, S8 | reject | reject |
| B23 | - | yes | yes | referee | S4, S7, S8 | reject | reject |
| B24 | - | referee | yes | referee | S4, S5, S7, S8 | reject | reject |
| B25 | - | referee | referee | referee | S4, S5, S6, S7, S8 | reject | reject |
| B26 | - | referee | yes | referee | S4, S5, S7, S8 | reject | reject |
| B27 | - | referee | yes | referee | S4, S5, S7, S8 | reject | reject |
| B28 | - | referee | yes | referee | S4, S5, S7, S8 | reject | reject |
| B29 | S7 404 | - | - | - | - | reject | reject |
| B30 | - | referee | yes | referee | S4, S5, S7, S8 | reject | reject |
| B31 | - | referee | yes | referee | S4, S5, S7, S8 | - | - |
| B32 | - | referee | yes | referee | S4, S5, S7, S8 | - | - |
| B33 | - | referee | yes | referee | S4, S5, S7, S8 | - | - |
| B34 | - | yes | yes | referee | S4, S7, S8 | - | - |
| B35 | - | yes | yes | referee | S4, S7, S8 | - | - |

## Replay of referee/runs/20261113-163823-fetched-claude-opus-5-5

The answers of that run, with the verdicts of the gate in place of the referee's for the rules code settled.

| measure | the run | with the gate |
| --- | --- | --- |
| bad cases let through | 0 | 0 |
| outcome in `expect` | 51 of 60 | 51 of 60 |

Let through with the gate: none  
Outcome changed: B06 human → reject
