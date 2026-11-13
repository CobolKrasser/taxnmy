# Referee run 20261113-174534-memory-claude-opus-5-5

Condition **memory**. 29 cases: 0 stopped by the lint, 29 sent to the referee.

| measure | value |
| --- | --- |
| bad cases let through (target 0) | 0 |
| good cases rejected | 0 |
| cases for a person (human) | 28 |
| human because a source is closed (refused, disallowed, error) | 0 |
| human because the referee doubts | 28 |
| outcome in `expect` | 14 of 29 |
| failed calls or unusable replies | 0 |
| cases whose lint does not match | 0 |
| cost per case sent to the referee | $0.0113 (max $0.0249) |
| cost of this run | $0.3280 |
| spent on all runs, budget | $7.0939 of $20.00 |

Let through: none  
Good rejected: none  
Human, a source closed: none  
Human, the referee doubts: G02, G06, G08, G17, G18, G19, G20, G21, G22, G23, G24, G25, G27, G28, B06, B14, B15, B20, B21, B22, B23, B24, B25, B26, B27, B28, B29, B30  
Not in `expect`: G02, G06, G08, G17, G18, G19, G20, G21, G22, B14, B15, B25, B26, B27, B28  
Errors: none  
Lint does not match: none

## On a bad case, did the right rule say no?

| rule | bad cases with this rule | said no |
| --- | --- | --- |
| S4 | 6 | 1 |
| S5 | 4 | 0 |
| S7 | 4 | 0 |
| S8 | 6 | 0 |

## Per type

| type | cases | outcome in `expect` |
| --- | --- | --- |
| ai-source | 1 | 1 |
| distribution-package | 1 | 1 |
| hallucinated | 3 | 3 |
| holdout-device | 3 | 0 |
| own-firmware | 1 | 1 |
| project-publisher | 3 | 0 |
| related-publisher | 2 | 2 |
| repackaged | 1 | 0 |
| s4-signal | 4 | 1 |
| saas | 2 | 2 |
| unconfirmed-value | 2 | 2 |
| variant | 3 | 1 |
| wrong-publisher | 3 | 0 |

## Per rule (R13)

Right: a good case answered yes, or a bad case answered no on one of its rules.

| rule | right | wrong | doubt |
| --- | --- | --- | --- |
| S4 | 1 | 0 | 19 |
| S5 | 0 | 0 | 18 |
| S7 | 0 | 0 | 18 |
| S8 | 0 | 0 | 20 |

Confident and wrong: none

Tokens: 12922 input, 2831 cache write, 79268 cache read, 12314 output; 0 web searches.
