# The referee — fase 2

This folder holds what is needed to build and measure the AI referee for rules S4–S8. Rules S1–S3
are checked by `tools/lint.py`. Nothing in this folder calls an API. The runs are done by the
builder, within the budget below.

| file | what it is |
| --- | --- |
| `prompt.md` | the system prompt and user message for S4–S8, and how the outcome is computed |
| `verdict.schema.json` | the JSON the referee answers with (structured output) |
| `testset/cases.yaml` | 60 proposals with the expected outcome: 28 good, 30 bad, 2 for a person |
| `r13-research.md` | the research into whether an AI can be a source (R13) |

## The testset

### What is in it

| group | cases | kinds |
| --- | --- | --- |
| good | G01–G28 | 16 existing products proposed again (devices, an OS, software, managed services); 3 where the publisher is the project itself; the 4 S4 signals of fase 1 (GraalVM, Ansible); 2 related publishers (`alibaba`, `alibaba-cloud`); 3 new ones that look wrong but are right |
| for a person | H01–H02 | the AI models of Anthropic and OpenAI |
| bad, caught by code | B01–B06 | a missing field, a specification (T8), two keys against the writing rule, a purl duplicate, a source that is an AI chat |
| bad, for the AI | B07–B30 | 5 duplicates in another word order, 4 variants, 2 descriptions, 2 translations, 3 invented products, 2 values that the source does not confirm, 3 wrong publishers, 1 repackaged product, 2 SaaS without versions |

The good cases are mostly **hold-outs**: an existing product is taken out of the dataset and
proposed again. Their sources are real, and fase 1 has already checked them. Most bad cases are
built from the same products, so that their sources are real too and only the error differs.

### Staging a case

For each case, build a dataset as follows:

1. Copy `products/` and `publishers/` from `main`.
2. Remove the files in `holdout` and `hide`.
3. Add the files in `context`.
4. Add the proposal: the files in `holdout` (unchanged from `main`) and the files in `proposal`.
5. Run `tools/lint.py` on it. The findings on the proposal must match the field `lint`.
   - With `error`, the case stops there: the outcome is *reject* on that rule, and no AI is
     called.
   - With `pass` or `signal`, the case goes to the referee, with the signals.

All 60 cases have been staged this way against `main` (d511371). The lint result of each matches
its field `lint`.

### Before the run: check the URLs

The URLs in `verify` were written for this testset. On 13 November each was fetched once, with a
plain HTTP request. All load, except these:
- B20 and B21 give 404, as intended;
- B22 redirects to the general product list, which is also fine;
- H02 and B29 give 403, because the site blocks plain requests;
- the product URL of B25 redirects to another doorbell.

The cases say what that means for the expected outcome. Before the run, fetch them again with the
fetcher of the run, and check:

- **Good cases** (G26–G28, H01, H02) and the real pages in bad cases (B25, B26, B28–B30): the
  page must load and show what the case says. If it does not, replace the URL with one that does,
  or drop the case and say so in the result.
- **Invented products** (B20–B22): the page must *not* show the product. An error is fine. So is a
  general page.

The `chatgpt.com/share` link in B06 is meant to look like an AI chat; it does not need to load.

### Scoring

An outcome is right when it is in `expect`. Report:

| measure | target |
| --- | --- |
| bad cases let through (outcome *accept* on B01–B30) | **0** |
| good cases rejected (outcome *reject* or *alias* on G01–G28) | as few as possible; report each one |
| cases sent to a person (outcome *human*) | report the number; each costs Fred time |
| on a bad case: did the right rule say *no*? | report per rule, from `rules` |
| cost per proposal | input and output tokens, and dollars |

Report per `type` as well. That shows which kind of error the referee misses.

A *human* outcome on a bad case counts as caught, because a person will look at it. On a good case
it is not an error, but it is a cost.

## Budget

The budget for fase 2 is **at most $5** from the API credit. Prices are from the Claude API price
list of November 2026. Check them before the run.

| | per million input tokens | per million output tokens |
| --- | --- | --- |
| Claude Sonnet 5.5 | $2 | $10 |
| Claude Opus 5.5 | $4 | $20 |

These are estimates, not measurements. One proposal is about 10,000 input tokens (system prompt,
proposal, three source texts) and 1,500 output tokens.

| run | proposals | Sonnet 5.5 | Opus 5.5 |
| --- | --- | --- | --- |
| A: base run, cases that reach the AI (G, H, B07–B30) | 54 | ≈ $1.90; ≈ $0.95 as a batch | ≈ $3.80; ≈ $1.90 as a batch |
| R13 M: memory | 29 | ≈ $0.55; ≈ $0.28 as a batch | ≈ $1.10; ≈ $0.55 as a batch |
| R13 W: web search, ≤ 3 searches | 29 | ≈ $2.90, including about $0.30 for searches | ≈ $5.50 |
| **total** | | **≈ $4.10** | **over budget** |

**Advice: Sonnet 5.5 for all three conditions.** That fits the budget with room for one rerun of
the base run. Opus 5.5 fits only for the base run. R13 needs the same model in all three
conditions.

- **Batches** halve the price of A and M. W can also go in a batch if web search is supported
  there; check that first.
- **Measure first.** Run the first three proposals of each condition one at a time, read the real
  token counts, and extrapolate. Stop when the total would pass $5.
- The system prompt is the same for every proposal. Cache it.

## What the lint does not check yet

Two proposals for `tools/lint.py`, from building this testset:

1. **S8, by code:** reject a `source` on a domain of an AI chat (`chatgpt.com`, `chat.openai.com`,
   `claude.ai`, `gemini.google.com`, `perplexity.ai`, `copilot.microsoft.com`). B06 tests this. The
   lint passes it today, so the AI must catch it.
2. **Publishers, like S3 for products:** a signal when a new publisher key shares its first word
   with an existing one (`alibaba` and `alibaba-cloud`). G24 and G25 test this.

A finding from fase 1 that is not part of this testset: the source of several publishers is an
endoflife.date page (`alibaba`, `caddy`, `elastic`). That page shows the product, not who publishes
it. S5 needs a page of the publisher itself. The base run will show how often the referee doubts on
that.
