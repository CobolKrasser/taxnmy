# Notes on run M (memory)

R13 condition M, Claude Opus 5.5, effort medium, on the 29 cases of `r13-research.md`.
[Run 43475816329](https://github.com/CobolKrasser/taxnmy/actions/runs/43475816329). Cost: $0.3280, which is $0.0113 per case.

- Outcome: 0 bad cases let through and 0 good cases rejected. 28 of the 29 cases went to *human*:
  the referee doubted. No answer was confident and wrong.
- Why: the system prompt in `prompt.md` says *"You judge five rules, using only the source texts you
  are given. You do not use your own memory as evidence."* The line in `<sources>` that M adds,
  *"Answer from what you know"*, does not override that. The model therefore declines to judge from
  memory.
- **So M does not measure what R13 asks.** To measure that, M (and W) would need a system prompt
  without the rule on memory. That is a design question for the analyst. A new run of M costs about
  $0.35.
