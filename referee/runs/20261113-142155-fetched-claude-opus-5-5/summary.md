# Referee run 20261113-142155-fetched-claude-opus-5-5

Condition **fetched**. 60 cases: 5 stopped by the lint, 55 sent to the referee.

| measure | value |
| --- | --- |
| bad cases let through (target 0) | 0 |
| good cases rejected | 6 |
| cases for a person (human) | 19 |
| outcome in `expect` | 39 of 60 |
| failed calls or unusable replies | 0 |
| cases whose lint does not match | 0 |
| cost per case sent to the referee | $0.0527 (max $0.1195) |
| cost of this run | $2.8969 |
| spent on all runs, budget | $2.8969 of $5.00 |

Let through: none  
Good rejected: G05, G08, G10, G19, G20, G27  
Human: G01, G03, G04, G06, G07, G09, G12, G14, G16, G17, G21, G22, G23, G24, G28, H02, B15, B20, B30  
Not in `expect`: G01, G03, G04, G05, G06, G07, G08, G09, G10, G12, G14, G16, G17, G19, G20, G21, G22, G27, H01, B15, B30  
Errors: none  
Lint does not match: none

## On a bad case, did the right rule say no?

| rule | bad cases with this rule | said no |
| --- | --- | --- |
| S1 | 2 | 2 |
| S2 | 2 | 2 |
| S3 | 1 | 1 |
| S4 | 12 | 9 |
| S5 | 4 | 4 |
| S6 | 5 | 5 |
| S7 | 4 | 1 |
| S8 | 6 | 5 |

## Per type

| type | cases | outcome in `expect` |
| --- | --- | --- |
| ai-model | 2 | 1 |
| ai-source | 1 | 1 |
| code | 5 | 5 |
| description | 2 | 2 |
| distribution-package | 1 | 1 |
| hallucinated | 3 | 3 |
| holdout-device | 9 | 1 |
| holdout-os | 1 | 0 |
| holdout-service | 2 | 1 |
| holdout-software | 4 | 2 |
| own-firmware | 1 | 0 |
| project-publisher | 3 | 1 |
| related-publisher | 2 | 2 |
| repackaged | 1 | 1 |
| s4-signal | 4 | 1 |
| saas | 2 | 1 |
| translated | 2 | 2 |
| unconfirmed-value | 2 | 2 |
| variant | 4 | 3 |
| word-order-different | 1 | 1 |
| word-order-duplicate | 5 | 5 |
| wrong-publisher | 3 | 3 |

## Per rule (R13)

Right: a good case answered yes, or a bad case answered no on one of its rules.

| rule | right | wrong | doubt |
| --- | --- | --- | --- |
| S4 | 29 | 1 | 10 |
| S5 | 20 | 0 | 12 |
| S7 | 24 | 0 | 8 |
| S8 | 18 | 6 | 10 |

Confident and wrong: B28 S4, G05 S8, G08 S8, G10 S8, G19 S8, G20 S8, G27 S8

Tokens: 429753 input, 2687 cache write, 145098 cache read, 56774 output; 0 web searches.
