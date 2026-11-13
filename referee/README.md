# The referee — fase 2

This folder holds what is needed to build and measure the AI referee for rules S4–S8. Rules S1–S3
are checked by `tools/lint.py`. Nothing in this folder calls an API. The runs are done by the
harness `tools/referee.py`, within the budget below.

| file | what it is |
| --- | --- |
| `prompt.md` | the system prompt and user message for S4–S8, and how the outcome is computed |
| `verdict.schema.json` | the JSON the referee answers with (structured output) |
| `testset/cases.yaml` | 60 proposals with the expected outcome: 28 good, 30 bad, 2 for a person |
| `r13-research.md` | the research into whether an AI can be a source (R13) |
| `prices.yaml` | the prices per model that the harness counts with, with their source |
| `runs/<run>/` | the results of a measurement, one directory per run |

The harness is `tools/referee.py`; see [The harness](#the-harness) below.

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

## The harness

`tools/referee.py` runs the cases as this README and `prompt.md` describe. It reads `cases.yaml`,
`prompt.md` (the text block under *System prompt*) and `verdict.schema.json` as they are.

```
pip install -r requirements.txt -r requirements-referee.txt
python tools/referee.py run                          # dry run of the base run: nothing is sent
python tools/referee.py run --condition memory       # dry run of R13 M, on the 29 cases
python tools/referee.py run --live                   # the base run: one call per case
python tools/referee.py check-urls                   # fetch the verify URLs (free)
python tools/referee.py compare referee/runs/<a> referee/runs/<b> referee/runs/<c>
```

For each case it:

1. stages the case on a copy of `products/` and `publishers/` and runs the lint. A case whose lint
   result does not match its field `lint` does not run; the summary lists it. A case with an error
   stops there, with outcome *reject*;
2. fetches the distinct `source` URLs of the proposal with plain HTTP (condition *fetched* only),
   and turns each page into text: without scripts, styles and navigation, at most about 6,000
   tokens. A PDF or another type that is not text gets `status="unreadable"`;
3. fills in the user message of `prompt.md`. `<existing>` holds the existing product of each
   signal, the products of the same publisher, and the publishers that share a word with the
   proposed publisher key;
4. makes one call, with structured output against `verdict.schema.json`;
5. checks the quotes (condition *fetched* only): a *yes* whose quote is not found in the fetched
   text of its URL, ignoring whitespace and case, becomes *doubt*. A *yes* without any quote also
   becomes *doubt*;
6. computes the outcome as in `prompt.md` § Outcome, and scores it: right when it is in `expect`.

The conditions of R13: `--condition fetched` (A, the default, all 60 cases), `memory` (M) and `web`
(W, web search with at most 3 searches). M and W run the 29 cases of `r13-research.md` unless
`--cases` says otherwise (`--cases all` for all 60); the quote check is off in both. `compare` puts
runs side by side on the cases they share.

Each live run writes `runs/<time>-<condition>-<model>/`: `run.json` (the settings and the budget),
`results.jsonl` (one line per case, with the answer, the checked verdicts, the outcome and the
cost), `sources.json` (the fetched texts, so that the quote check can be redone) and
`summary.md` (the measures of § Scoring, per rule and per type, and the R13 measures).

Model and effort are `--model` (default `claude-opus-5-5`) and `--effort` (default `medium`).
`max_tokens` is 8,000 per request unless the budget lowers it. Thinking counts toward it.

### The hard budget

The key may belong to another project, so the harness does not rely on a limit in the Console.
`--budget` (default $5.00) is a **hard limit** for **all runs in `runs/` together**:

- at the start the harness adds up the cost in every `results.jsonl` in `runs/`, and logs the limit
  and what is spent; `run.json` records both;
- before each request it counts the input tokens (token counting is free), adds 5% and 100 tokens
  of margin, and sets `max_tokens` so that the request cannot cost more than what is left. When that
  leaves room for fewer than 2,000 output tokens, it does not send the request and the run stops;
  the summary says so;
- after each request it adds the cost of the usage the API reports, from `prices.yaml`, and logs it
  with the total.

**Web search is bounded less tightly.** Token counting does not take server tools, and search
results enter the input during the call. For each request the harness reserves 2,000 tokens for the
tool and 20,000 input tokens per search, plus the search fee. If twice the largest web case so far
is more, it reserves that. On Opus 5.5 with 3 searches that is $0.34 per request.

The key is read from `ANTHROPIC_API_KEY` in the environment only. The harness never writes it to a
file or a log; a test checks this. Nothing is sent without `--live`.

### Running it as an Action

The key is a secret of the repository, so a paid measurement runs as the workflow **referee**
(`.github/workflows/referee.yml`). Start it by hand from `main` (*Actions → referee → Run
workflow*), with the condition, the cases, the model, the effort, the budget and *live*.

- Without *live* it is a dry run: the key is not passed to it.
- It runs only from `main`, one at a time. It reads only data files and runs no code from a pull
  request (hosting plan §8).
- The run directory is kept as an artifact `referee-run-<run>` for 90 days, and its summary is shown
  on the run page.
- **The budget counts the artifacts of earlier runs and the runs committed in `runs/`.** Commit the
  run directories in a pull request after a measurement, so the count survives the 90 days. If an
  artifact has expired and its run is not committed, the workflow stops.
- `task: check-urls` fetches the `verify` URLs with the fetcher of the run, as *Before the run*
  asks. It is free and needs no key.

