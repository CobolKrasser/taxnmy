# Referee run 20261113-180523-memory-claude-opus-5-5

Condition **memory**. 29 cases: 0 stopped by the lint, 29 sent to the referee.

| measure | value |
| --- | --- |
| bad cases let through (target 0) | 0 |
| good cases rejected | 0 |
| cases for a person (human) | 14 |
| human because a source is closed (refused, disallowed, error) | 0 |
| human because the referee doubts | 14 |
| outcome in `expect` | 22 of 29 |
| failed calls or unusable replies | 0 |
| cases whose lint does not match | 0 |
| cost per case sent to the referee | $0.0192 (max $0.0339) |
| cost of this run | $0.5568 |
| spent on all runs, budget | $7.9152 of $20.00 |

Let through: none  
Good rejected: none  
Human, a source closed: none  
Human, the referee doubts: G02, G08, G17, G19, G20, G21, G22, G23, G24, G27, G28, B21, B22, B24  
Not in `expect`: G02, G08, G17, G19, G20, G21, G22  
Errors: none  
Lint does not match: none

## On a bad case, did the right rule say no?

| rule | bad cases with this rule | said no |
| --- | --- | --- |
| S4 | 6 | 6 |
| S5 | 4 | 4 |
| S7 | 4 | 1 |
| S8 | 6 | 2 |

## Per type

| type | cases | outcome in `expect` |
| --- | --- | --- |
| ai-source | 1 | 1 |
| distribution-package | 1 | 1 |
| hallucinated | 3 | 3 |
| holdout-device | 3 | 1 |
| own-firmware | 1 | 1 |
| project-publisher | 3 | 1 |
| related-publisher | 2 | 2 |
| repackaged | 1 | 1 |
| s4-signal | 4 | 1 |
| saas | 2 | 2 |
| unconfirmed-value | 2 | 2 |
| variant | 3 | 3 |
| wrong-publisher | 3 | 3 |

## Per rule (R13)

Right: a good case answered yes, or a bad case answered no on one of its rules.

| rule | right | wrong | doubt |
| --- | --- | --- | --- |
| S4 | 18 | 0 | 2 |
| S5 | 17 | 0 | 1 |
| S7 | 15 | 1 | 2 |
| S8 | 8 | 0 | 12 |

Confident and wrong: B25 S7

Tokens: 12922 input, 3177 cache write, 88956 cache read, 23570 output; 0 web searches, 0 web fetches.
