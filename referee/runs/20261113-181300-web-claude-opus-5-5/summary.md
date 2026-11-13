# Referee run 20261113-181300-web-claude-opus-5-5

Condition **web**. 26 cases: 0 stopped by the lint, 26 sent to the referee.

| measure | value |
| --- | --- |
| bad cases let through (target 0) | 0 |
| good cases rejected | 0 |
| cases for a person (human) | 13 |
| human because a source is closed (refused, disallowed, error) | 0 |
| human because the referee doubts | 13 |
| outcome in `expect` | 20 of 26 |
| failed calls or unusable replies | 0 |
| cases whose lint does not match | 0 |
| cost per case sent to the referee | $0.0982 (max $0.2467) |
| cost of this run | $2.5527 |
| spent on all runs, budget | $10.6950 of $17.00 |

Let through: none  
Good rejected: none  
Human, a source closed: none  
Human, the referee doubts: G17, G19, G21, G22, G23, G24, G28, B06, B14, B20, B21, B25, B30  
Not in `expect`: G17, G19, G21, G22, B14, B25  
Errors: none  
Lint does not match: none

## On a bad case, did the right rule say no?

| rule | bad cases with this rule | said no |
| --- | --- | --- |
| S4 | 6 | 4 |
| S5 | 4 | 3 |
| S7 | 4 | 1 |
| S8 | 6 | 3 |

## Per type

| type | cases | outcome in `expect` |
| --- | --- | --- |
| ai-source | 1 | 1 |
| distribution-package | 1 | 1 |
| hallucinated | 3 | 3 |
| own-firmware | 1 | 1 |
| project-publisher | 3 | 1 |
| related-publisher | 2 | 2 |
| repackaged | 1 | 1 |
| s4-signal | 4 | 2 |
| saas | 2 | 2 |
| unconfirmed-value | 2 | 2 |
| variant | 3 | 2 |
| wrong-publisher | 3 | 2 |

## Per rule (R13)

Right: a good case answered yes, or a bad case answered no on one of its rules.

| rule | right | wrong | doubt |
| --- | --- | --- | --- |
| S4 | 14 | 0 | 3 |
| S5 | 9 | 0 | 6 |
| S7 | 10 | 0 | 5 |
| S8 | 11 | 0 | 6 |

Confident and wrong: none

Tokens: 14296 input, 233624 cache write, 1450141 cache read, 47367 output; 9 web searches, 69 web fetches.
