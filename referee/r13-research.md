# TE4 research: can an AI be a source?

TE1 says that every value points to a source. Until this research is done, an AI is a *finder*: its
answer counts only through the page it points to (TR8). This research asks whether that has to
stay that way:

> **Does an AI answer the referee's questions from its memory as often correctly as an AI that
> searches the web?**

If it does, an AI could become a source of its own kind, with its own label. If it does not, TR8
stays as it is. **The decision is Fred's.** This research supplies the measurement, not the
decision.

## Three conditions, one set of questions

All three conditions use the same model, the same rules and the same output schema. Only the
evidence the model gets differs. M and W share a system prompt of their own (`prompt-r13.md`): it
differs from that of A (`prompt.md`) only in what counts as evidence, because A's says to use only
the source texts given, and M and W are given none.

| | condition | the `<sources>` block | tools |
| --- | --- | --- | --- |
| **A** | *fetched* (the base run of fase 2) | the texts of the URLs in the proposal, fetched by code | none |
| **M** | *memory* | empty, with the line *"No source texts are given. Answer from what you know; give no quotes."* | none |
| **W** | *web* | empty, with the line *"No source texts are given. Read the URLs in the proposal with web fetch, and find other pages with web search. Quote only from pages you read with web fetch."* | web fetch and web search, each at most 3 uses per proposal |

In condition M, every *yes* or *no* has no evidence. The quote check in `prompt.md` would turn each
*yes* into *doubt*. For this research the check is **switched off in M and W**, and the raw answer
counts. The question is whether the answer is right, not whether it can be checked.

## Which cases

Not all 60. Only the cases where the answer depends on facts about the world:

| rule | cases |
| --- | --- |
| TR5 (who publishes) | G17–G19, G23–G25, B25–B28 |
| TR7 (does it exist) | G02, G06, G08, G27, B20–B22 |
| TR8 (is the value right) | B06, B23, B24 |
| TR4 (own update stream) | G20–G22, G28, B12, B14, B15, B29, B30 |

These are **29 cases**: G02, G06, G08, G17–G25, G27, G28, B06, B12, B14, B15, B20–B30. The cases for
TR6 (name and translation) and the word-order cases are left out, because there the source text
itself is the question.

The ground truth is the `expect` and `rules` of each case in `testset/cases.yaml`. Score per rule:
right when the verdict on that rule matches the ground truth. For a good case every rule must be
*yes*. For a bad case at least one of its `rules` must be *no*. *Doubt* is counted separately.

## What to measure

Per condition, over the 29 cases:

1. **Wrongly accepted:** a bad case that gets only *yes*. This is the number that matters most
   (requirement TE2).
2. **Wrongly rejected:** a good case that gets a *no*.
3. **Doubt:** how often the model sends a case to a person.
4. **Right per rule:** TR4, TR5, TR7 and TR8 separately.
5. **Cost per proposal:** input and output tokens, and in W the number of searches and fetches.

For condition M one extra count: **confident and wrong**, a *yes* or *no* that the ground truth
contradicts. That is the hallucination rate.

## How to read the result

| outcome | meaning for TE4 |
| --- | --- |
| M lets a bad case through, or is confidently wrong more often than W | **AI stays a finder.** TR8 is unchanged. |
| M is as right as W and A on TR4 and TR5, but not on TR7 and TR8 | An AI may *suggest* a publisher or an update stream, but a value still needs a page. |
| M is as right as W and A on all four rules, with no bad case let through | Fred may consider a label *ai-memory* for low-risk values. Even then not for codes (GTIN, CPE) or for existence (TR7). |

With 29 cases the measurement shows large differences, not small ones. A difference of one or two
cases is noise. Report it as *no difference shown*.

## Order and budget

1. First run condition **A** on all 60 cases. That is the base run of fase 2.
2. Then run **M** and **W** on the 29 cases above.
3. Use the same model in all three conditions. Budget: see `README.md` § Budget.

W costs the most, because a search adds its fee and the pages it reads add input tokens. Measure
the cost of the first three proposals in W and extrapolate. Stop if the run will go over the
budget.
