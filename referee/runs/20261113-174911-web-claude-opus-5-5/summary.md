# Referee run 20261113-174911-web-claude-opus-5-5

Condition **web**. 3 cases: 0 stopped by the lint, 3 sent to the referee.

| measure | value |
| --- | --- |
| bad cases let through (target 0) | 0 |
| good cases rejected | 0 |
| cases for a person (human) | 3 |
| human because a source is closed (refused, disallowed, error) | 0 |
| human because the referee doubts | 3 |
| outcome in `expect` | 0 of 3 |
| failed calls or unusable replies | 0 |
| cases whose lint does not match | 0 |
| cost per case sent to the referee | $0.0882 (max $0.1277) |
| cost of this run | $0.2645 |
| spent on all runs, budget | $7.3585 of $17.00 |

Let through: none  
Good rejected: none  
Human, a source closed: none  
Human, the referee doubts: G02, G06, G08  
Not in `expect`: G02, G06, G08  
Errors: none  
Lint does not match: none

## On a bad case, did the right rule say no?

| rule | bad cases with this rule | said no |
| --- | --- | --- |

## Per type

| type | cases | outcome in `expect` |
| --- | --- | --- |
| holdout-device | 3 | 0 |

## Per rule (R13)

Right: a good case answered yes, or a bad case answered no on one of its rules.

| rule | right | wrong | doubt |
| --- | --- | --- | --- |
| S4 | 1 | 0 | 2 |
| S5 | 1 | 0 | 2 |
| S7 | 1 | 0 | 2 |
| S8 | 0 | 0 | 3 |

Confident and wrong: none

Tokens: 1556 input, 13914 cache write, 106410 cache read, 3873 output; 9 web searches.
