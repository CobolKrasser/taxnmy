# Referee run 20261113-163823-fetched-claude-opus-5-5

Condition **fetched**. 60 cases: 7 stopped by the lint, 53 sent to the referee.

| measure | value |
| --- | --- |
| bad cases let through (target 0) | 0 |
| good cases rejected | 1 |
| cases for a person (human) | 12 |
| outcome in `expect` | 50 of 60 |
| failed calls or unusable replies | 0 |
| cases whose lint does not match | 0 |
| cost per case sent to the referee | $0.0651 (max $0.1236) |
| cost of this run | $3.4521 |
| spent on all runs, budget | $6.7660 of $20.00 |

Let through: none  
Good rejected: G07  
Human: G03, G06, G09, G12, G17, G21, G22, G23, G24, H02, B06, B15  
Not in `expect`: G03, G06, G07, G09, G12, G17, G21, G22, H02, B15  
Errors: none  
Lint does not match: none

## On a bad case, did the right rule say no?

| rule | bad cases with this rule | said no |
| --- | --- | --- |
| S1 | 2 | 2 |
| S2 | 2 | 2 |
| S3 | 1 | 1 |
| S4 | 12 | 10 |
| S5 | 4 | 4 |
| S6 | 5 | 5 |
| S7 | 4 | 2 |
| S8 | 6 | 4 |

## Per type

| type | cases | outcome in `expect` |
| --- | --- | --- |
| ai-model | 2 | 1 |
| ai-source | 1 | 1 |
| code | 5 | 5 |
| description | 2 | 2 |
| distribution-package | 1 | 1 |
| hallucinated | 3 | 3 |
| holdout-device | 9 | 5 |
| holdout-os | 1 | 1 |
| holdout-service | 2 | 2 |
| holdout-software | 4 | 3 |
| own-firmware | 1 | 1 |
| project-publisher | 3 | 2 |
| related-publisher | 2 | 2 |
| repackaged | 1 | 1 |
| s4-signal | 4 | 2 |
| saas | 2 | 2 |
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
| S4 | 36 | 0 | 3 |
| S5 | 24 | 0 | 8 |
| S7 | 27 | 0 | 4 |
| S8 | 28 | 1 | 4 |

Confident and wrong: G07 S8

Tokens: 558896 input, 2831 cache write, 147212 cache read, 58646 output; 0 web searches.
