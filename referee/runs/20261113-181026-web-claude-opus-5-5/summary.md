# Referee run 20261113-181026-web-claude-opus-5-5

Condition **web**. 3 cases: 0 stopped by the lint, 3 sent to the referee.

| measure | value |
| --- | --- |
| bad cases let through (target 0) | 0 |
| good cases rejected | 0 |
| cases for a person (human) | 1 |
| human because a source is closed (refused, disallowed, error) | 0 |
| human because the referee doubts | 1 |
| outcome in `expect` | 2 of 3 |
| failed calls or unusable replies | 0 |
| cases whose lint does not match | 0 |
| cost per case sent to the referee | $0.0757 (max $0.1122) |
| cost of this run | $0.2271 |
| spent on all runs, budget | $8.1423 of $17.00 |

Let through: none  
Good rejected: none  
Human, a source closed: none  
Human, the referee doubts: G08  
Not in `expect`: G08  
Errors: none  
Lint does not match: none

## On a bad case, did the right rule say no?

| rule | bad cases with this rule | said no |
| --- | --- | --- |

## Per type

| type | cases | outcome in `expect` |
| --- | --- | --- |
| holdout-device | 3 | 2 |

## Per rule (R13)

Right: a good case answered yes, or a bad case answered no on one of its rules.

| rule | right | wrong | doubt |
| --- | --- | --- | --- |
| S4 | 2 | 0 | 1 |
| S5 | 2 | 0 | 1 |
| S7 | 2 | 0 | 1 |
| S8 | 2 | 0 | 1 |

Confident and wrong: none

Tokens: 1636 input, 18946 cache write, 130111 cache read, 4990 output; 0 web searches, 9 web fetches.
