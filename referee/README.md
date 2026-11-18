# The referee — fase 2

This folder holds what is needed to build and measure the AI referee for rules TR4–TR8. Rules TR1–TR3
are checked by `tools/lint.py`. Nothing in this folder calls an API. The runs are done by the
harness `tools/referee.py`, within the budget below.

| file | what it is |
| --- | --- |
| `prompt.md` | the system prompt and user message for TR4–TR8, and how the outcome is computed |
| `verdict.schema.json` | the JSON the referee answers with (structured output) |
| `testset/cases.yaml` | 79 proposals with the expected outcome: 31 good (G01–G29, H01–H02), 48 bad |
| `prompt-group.md`, `group.schema.json` | the prompt and the answer of the grouping per publisher (TAX-26) |
| `testset/groups.yaml` | 15 publishers of the KEV data, 204 names, each with the class a person gave it |
| `r13-research.md` | the research into whether an AI can be a source (TE4) |
| `prices.yaml` | the prices per model that the harness counts with, with their source |
| `runs/<run>/` | the results of a measurement, one directory per run |

The harness is `tools/referee.py`; see [The harness](#the-harness) below.

## The testset

### What is in it

| group | cases | kinds |
| --- | --- | --- |
| good | G01–G29 | 16 existing products proposed again (devices, an OS, software, managed services); 3 where the publisher is the project itself; the 4 TR4 signals of fase 1 (GraalVM, Ansible); 2 related publishers (`alibaba`, `alibaba-cloud`); 3 new ones that look wrong but are right; 1 small publisher with only its own site (G29) |
| good, AI models | H01–H02 | the AI models of Anthropic and OpenAI: once for a person, good since Fred decided on 13 November that AI models are in TAX |
| bad, caught by code | B01–B06 | a missing field, a specification (TP9), two keys against the writing rule, a purl duplicate, a source that is an AI chat |
| bad, for the AI | B07–B30 | 5 duplicates in another word order, 4 variants, 2 descriptions, 2 translations, 3 invented products, 2 values that the source does not confirm, 3 wrong publishers, 1 repackaged product, 2 SaaS without versions |
| bad, past the old gate | B31–B35 | 3 wrong publishers with the product and the versions on a domain of their own (download sites), 2 existing products with a wrong `kind` (TAX-24) |
| bad, protection against intent | B39–B48 | 6 names or domains one or two characters from an existing one (TV3), a name that addresses the referee (TV1), a second source on the publisher's own site (TV4), a versions source moved to someone else (TV5), a new publisher whose site has nothing to download and no versions |

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

All 65 cases have been staged this way against `main` (B31–B35 on 16 November). The lint result of each matches
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

- **Good cases** (G26–G29, H01, H02) and the real pages in bad cases (B25, B26, B28–B30): the
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
| good cases rejected (outcome *reject* or *alias* on G01–G29, H01–H02) | as few as possible; report each one |
| cases sent to a person (outcome *human*) | report the number; each costs Fred time |
| on a bad case: did the right rule say *no*? | report per rule, from `rules` |
| cost per proposal | input and output tokens, and dollars |

Report per `type` as well. That shows which kind of error the referee misses.

A *human* outcome on a bad case counts as caught, because a person will look at it. On a good case
it is not an error, but it is a cost.

## Budget

The budget for every run of the referee together is **at most $50** from the API credit (Fred,
17 November 2026; it was $20 for fase 2, and $5 before that). The ceilings per pull request and per
day stay as they are. Prices are from the Claude API price list of November 2026. Check them before
the run.

| | per million input tokens | per million output tokens |
| --- | --- | --- |
| Claude Sonnet 5.5 | $2 | $10 |
| Claude Opus 5.5 | $4 | $20 |

These are estimates, not measurements. One proposal is about 10,000 input tokens (system prompt,
proposal, three source texts) and 1,500 output tokens.

| run | proposals | Sonnet 5.5 | Opus 5.5 |
| --- | --- | --- | --- |
| A: base run, cases that reach the AI (G, H, B07–B30) | 54 | ≈ $1.90; ≈ $0.95 as a batch | ≈ $3.80; ≈ $1.90 as a batch |
| TE4 M: memory | 29 | ≈ $0.55; ≈ $0.28 as a batch | ≈ $1.10; ≈ $0.55 as a batch |
| TE4 W: web search, ≤ 3 searches | 29 | ≈ $2.90, including about $0.30 for searches | ≈ $5.50 |
| **total** | | **≈ $4.10** | **over budget** |

**Advice: Sonnet 5.5 for all three conditions.** That fits the budget with room for one rerun of
the base run. Opus 5.5 fits only for the base run. TE4 needs the same model in all three
conditions.

- **Batches** halve the price of A and M. W can also go in a batch if web search is supported
  there; check that first.
- **Measure first.** Run the first three proposals of each condition one at a time, read the real
  token counts, and extrapolate. Stop when the total would pass $20.
- The system prompt is the same for every proposal. Cache it.

## What the lint does not check yet

Two proposals for `tools/lint.py`, from building this testset:

1. ~~**TR8, by code:** reject a `source` on a domain of an AI chat.~~ Built in the gate (TAX-15),
   see [The gate](#the-gate-tr5-tr6-and-tr8-by-code). B06 tests it.
2. **Publishers, like TR3 for products:** a signal when a new publisher key shares its first word
   with an existing one (`alibaba` and `alibaba-cloud`). G24 and G25 test this.

3. ~~**Lookalikes (TV3):** a signal for a key or a domain one or two characters from an existing
   one.~~ Built in the lint (TAX-31, `tools/guard.py`). B39–B44 test it.

A finding from fase 1 that is not part of this testset: the source of several publishers is an
endoflife.date page (`alibaba`, `caddy`, `elastic`). That page shows the product, not who publishes
it. TR5 needs a page of the publisher itself. The base run will show how often the referee doubts on
that.

## The harness

`tools/referee.py` runs the cases as this README and `prompt.md` describe. It reads `cases.yaml`,
`prompt.md` (the text block under *System prompt*) and `verdict.schema.json` as they are.

```
pip install -r requirements.txt -r requirements-referee.txt -r requirements-fetch.txt
python -m playwright install chromium                # once, for the browser step
python tools/referee.py fetch --output sources.json  # read the sources (free, no key)
python tools/referee.py run --sources sources.json   # dry run with those sources
python tools/referee.py run                          # dry run of the base run: nothing is sent
python tools/referee.py run --condition memory       # dry run of TE4 M, on the 29 cases
python tools/referee.py run --live                   # the base run: one call per case
python tools/referee.py run --live --batch           # the same, as one batch, at half price
python tools/referee.py check-urls                   # fetch the verify URLs (free)
python tools/referee.py compare referee/runs/<a> referee/runs/<b> referee/runs/<c>
```

For each case it:

1. stages the case on the data, read once per run and changed in memory only, and runs the lint on
   the files of the case, compared with the whole dataset (TAX-16). A case whose lint
   result does not match its field `lint` does not run; the summary lists it. A case with an error
   stops there, with outcome *reject*. A case with a source on an AI chat stops there too: *reject*
   on TR8, by the gate, and nothing is fetched (condition *fetched* only);
2. reads the distinct `source` URLs of the proposal, and the source of the publisher file, with
   the ladder of [Reading sources](#reading-sources) (condition *fetched* only). At most about
   6,000 tokens per page. When the product's own `source` answers 404 or 410, the case stops
   there: outcome *reject* on TR7, by code;
3. runs the gate on the fetched texts (condition *fetched* only): code settles TR5, TR6 and TR8 where
   it can, see [The gate](#the-gate-tr5-tr6-and-tr8-by-code);
4. fills in the user message of `prompt.md`, with a `<decided>` block for what the gate settled. The publisher file always comes along: in
   `<proposal>` when it is new, otherwise first in `<existing>`. `<existing>` also holds the
   existing product of each signal, the products of the same publisher that resemble the proposal (a word of
   the key or name in common, or one that begins with the other; at most 20), and the publishers that
   share a word with the proposed publisher key;
5. makes one call, with structured output against `verdict.schema.json`, holding only the rules the
   gate left;
6. checks the quotes of the referee (condition *fetched* only): a *yes* whose quote is not found in the fetched
   text of its URL, ignoring whitespace and case, becomes *doubt*. A *yes* without any quote also
   becomes *doubt*;
7. computes the outcome as in `prompt.md` § Outcome, and scores it: right when it is in `expect`.

The conditions of TE4: `--condition fetched` (A, the default, all 79 cases), `memory` (M) and `web`
(W, web search and web fetch, at most 3 of each). M and W run the 29 cases of `r13-research.md` unless
`--cases` says otherwise (`--cases all` for all 79); the quote check is off in both. Both read their own system prompt, `referee/prompt-r13.md`
(the text block under *System prompt*), written by the analyst; condition *fetched* reads
`prompt.md`. `--prompt` overrides both. `compare` puts
runs side by side on the cases they share.

Each live run writes `runs/<time>-<condition>-<model>/`: `run.json` (the settings and the budget),
`results.jsonl` (one line per case, with the answer, the checked verdicts, the outcome and the
cost), `sources.json` (the fetched texts, so that the quote check can be redone) and
`summary.md` (the measures of § Scoring, per rule and per type, and the TE4 measures; *human* is counted by cause: a source closed, or the referee doubts).

Model and effort are `--model` (default `claude-opus-5-5`) and `--effort` (default `medium`).
`max_tokens` is 8,000 per request unless the budget lowers it. Thinking counts toward it.

### The gate: TR5, TR6 and TR8 by code

`tools/gate.py` settles what can be read off literally, before the referee is called (TAX-15;
`tax-analyse-fable-2026-11-14.md` §4 in keepitfresh). Code only says what it can show; the rest
goes to the referee, never a guess.

| rule | code says *yes* | code says *no* | otherwise |
| --- | --- | --- | --- |
| TR5 | the publisher is already in TAX (not in the proposal), `versions.source` was read, and its registrable domain is that of the publisher's `source` | – | referee |
| TR6 | the product name and the publisher name, each at most 40 characters, appear in a source text that was read, capitals and whitespace aside, not glued to other letters or digits | – | referee |
| TR8 | never | a source is on the domain of an AI chat | referee |

No rule stays untested because code settled another one (TAX-24). Where code checked part of a
rule that stays with the referee, the `<decided>` block says so in a line `TR8 in part: …` or
`TR5 in part: …`; that is a note, not a verdict.

- **TR8.** Code looks for the literal values: the product name, in the text of the product's
  `source`, and each code, in the text of its own `source`, with every source of the product read.
  A source found *instead of* another does not count. But `kind` is a value too, and a
  classification that no code reads off, so TR8 always goes to the referee; what code found goes
  along as a note. Before TAX-24 code said TR8 *yes* here, and a wrong `kind` reached no one (B34, B35).
- **TR5.** Two URLs on one domain show where the versions are, not who publishes them. For a
  publisher proposed together with the product, both URLs come from the proposal: a wrong
  publisher with a site of its own and the versions on that same site passes the comparison
  (B31–B33). So code says *yes* only for a publisher whose `source` TAX already accepted, and only
  when the versions source was read itself; a closed source shows nothing (G06).
- On sites where one domain carries many owners, a shared domain proves nothing: on code hosting
  (`github.com`, …) the owner is the first part of the path, on `*.github.io` and the like the
  label in front; catalogues, package registries and encyclopaedias (`endoflife.date`, `pypi.org`,
  …) never count as the publisher's site.
- The lists in `tools/gate.py` are classes of sites, never a brand or a product of the data (TG4).
- TR4 and TR7 stay with the referee. Conditions *memory* and *web* (TE4) have no gate: they measure
  the AI alone.

A **dry run** shows per case what the gate settles and what a live run would ask, and writes it to
`runs/<time>-dry-fetched/summary.md` (no `results.jsonl`, so it costs the budget nothing). Without
`--sources` the gate has no texts and settles only the AI chats. `--replay RUN_DIR` puts the
gate in front of the answers of an earlier run, to see what it would have changed:

```
python tools/referee.py run --sources sources.json --replay referee/runs/<run>
```

### The hard budget

The key may belong to another project, so the harness does not rely on a limit in the Console.
`--budget` (default $50.00) is a **hard limit** for **all runs in `runs/` together**:

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

### Reading sources

`tools/fetch.py` goes down a ladder for each URL until it has usable text
(`tax-fase2-pagina-s-lezen.md` §3):

| step | `method` | what it does |
| --- | --- | --- |
| 0 | `machine` | a source made for machines; one reader per format, never per brand (`tools/machine.py`): an update catalog (a `Manifest` of `SoftwareComponent`, as Dell publishes), AppStream metadata (as LVFS publishes), a CSAF advisory or provider metadata, the NVD CPE API. The reader takes out the part about the product, by its name; `sources.json` keeps it per product |
| 1 | `api` | a known API instead of a page: an NVD search page becomes the NVD API |
| 2 | `http` | a plain request; HTML without scripts, styles and navigation; JSON and plain text as they are; the text of a PDF (`pypdf`); a page with one frame or iframe is followed |
| 3 | `browser` | a headless Chromium (Playwright), when an HTML page gives fewer than 200 characters of text. It waits until the network is quiet, at most 15 seconds, and takes the visible text, frames included |
| 0 again | `machine` | for a source that is `refused` or `disallowed`: the publisher's `machine_sources` (schema 1.2.0), each read for the product. What they give is added `instead_of` the closed source |
| 4 | `search+…` | only for a source that is `refused` or `disallowed` and for which the publisher's `machine_sources` gave nothing: one web search of the API with Claude Sonnet 5.5, which only returns URLs and judges nothing. The search is limited to the publisher's domains: of the closed URL, of the product's sources, and of every URL in the publisher's file and its other products (at most 30). Each query runs once per run, also when several sources of one product are closed. The URLs that come back, at most 5, go down steps 1–3 and are added as sources `instead_of` the closed one. The snippets are never kept or given to the referee. It counts against the budget, like a run (`runs/<time>-search-<model>/`) |

The ladder stops at a refusal:

- **401 or 403**: `refused`. It is not tried again, not with a browser either.
- **429**: one more try after the wait in `Retry-After` (at most 60 seconds). A second 429 is
  `refused`.
- **A timeout of an API** (step 1, such as the NVD API, which limits requests without a key): one
  more try after 5 seconds.
- **robots.txt** forbids the URL: `disallowed`. The URL is not requested.
- **Not a public address** (TAX-34): an IP address, `localhost`, a name inside a network, a name
  that resolves to a private address, or a redirect to one: `error: blocked`. It is not requested;
  urllib connects only to an address it checked, and the browser aborts every other request
  (`tools/address.py`).
- **Fewer than 200 characters** of text in the end: `unreadable`.

For each source `sources.json` records `status`, `method`, `chars` (the length before the cut to
6,000 tokens) and `fetched_url`, when that differs from the URL in the proposal: an API, or a frame.
`python tools/referee.py fetch` prints how many sources each step read.

**Conduct** (§2 of the same note):
- an honest User-Agent, `TAXNMY-referee/1.0`, with a link to this repository;
- robots.txt is respected, following RFC 9309;
- at most one request per second per site;
- each URL once per run;
- no imitated browser headers, no proxies and no stealth;
- only public addresses: the lint refuses a source on an IP address, `localhost` or an internal
  name (TR1), and the reader checks again at every connection.

A site that refuses is not worked around. A person decides, or the product gets another source.

### The model measurement (TAX-18)

Which model may judge: Opus 5.5 was measured (A, A2), at effort *medium*. A stand that lets one
bad case through in any of its runs drops out (TE2). In the first measurement (16 November 2026,
effort *low*) Haiku 5.5 and Sonnet 5.5 both let B30 through and dropped out; Haiku is not measured
again. Sonnet 5.5 is measured again, at *low* and at *medium*, with the TR4 version (below).

`run --batch` sends every case that reaches the referee as one batch (half price, answered within
hours; not for condition *web*). The budget stays hard: requests go in rounds, each holding what
fits the budget at its worst, as in [the PR mode](#the-referee-on-a-pull-request).

`measure` runs the test set per **stand**, a model at an effort, `--repeats` times, as a batch.
`--models` and `--efforts` give the stands: every model at every effort. The default is Sonnet 5.5
at *low*; the second measurement of TAX-18 is Sonnet 5.5 at *low* and at *medium*:

```
python tools/referee.py measure --efforts low medium --sources sources.json            # estimate from the tokens of A2
python tools/referee.py measure --efforts low medium --sources sources.json --count    # with counted tokens (free, the key)
python tools/referee.py measure --efforts low medium --sources sources.json --live     # the measurement
```

1. **The probe:** cases G01–G03 per stand, one small batch each. Their real cost per case is
   extrapolated to the rest. When that would pass `--budget`, or a probe got no answer, nothing more
   is sent. The probe is the start of run 1 (`compare` joins the two directories with `+`).
2. **The runs:** per stand the rest of run 1, then runs 2 and 3, each a batch. A batch run carries
   its effort in its name: `runs/<time>-fetched-<model>-<effort>/`.
3. **The report** `runs/model-measurement-<time>.md`: per stand and per run the bad cases let
   through, the good cases rejected, the cases for a person (*human*; *parked* in the PR mode) and
   the rules that left them there, the cases whose outcome differs between the runs, the quotes code
   did not find (a TR4 without a version among them), the output tokens and the cost per proposal;
   and `compare` with A2.

**The estimate** without `--live`: the input tokens of A2 (or, with `--count`, those counted), and
the output tokens of A2 (effort *medium*) times a factor per effort: *low* 0.5 (Sonnet 5.5 wrote
about 560 tokens per case at *low* in the first measurement, A2 about 1,100), *medium* 1, *high* 2
(an assumption). A case A2 did not have counts as the mean case of A2. For Sonnet 5.5 at *low* and
*medium*, three runs each, on the 69 cases past the lint, that is about $2.80 and $3.40: **about
$6.20** together. About $13.15 of the $50 is spent.

**The TR4 version.** Since the second measurement, a *yes* on TR4 needs a version quoted from the
versions source (`prompt.md` § Outcome). The first measurement showed a hosted service passing TR4
on a page of news about it (B30). The test set now holds five cases of a service without a version
(B29, B30, B36–B38) and six of a name or domain one or two characters from an existing one
(B39–B44, rules TV2 and TV3). The outcomes of the first and the second measurement are therefore not
comparable one to one: the prompt differs.

**Protection against intent (TAX-31).** Since then the referee also answers TV1 (no text addresses
the referee), and the message holds an `<identity>` block (TV2). Code holds a case for a person on
TV3 or TV5, whatever the referee answers (`prompt.md` § Outcome): B39–B44 on TV3 and B47 on TV5.
A new publisher is taken in on its own evidence (TV4); a second source is optional, and one that is
given and does not pass is rejected by code: B46 gives one on its own site, which the lint rejects.
The good cases that propose a publisher as new (G17–G19, G24, G25, H01, H02) expect what they
expected before. G29 is a small publisher with only its own site and a download page (*accept*);
B48 a new publisher whose site has nothing to download and no versions (not *accept*). B45 has a
name that addresses the referee (TV1). The `<identity>` block says whether a new publisher gives a
second source, and judges nothing. The prompt changed again, so a new measurement is needed before the
referee judges with it.

As an Action: **referee-models** (*Actions → referee-models → Run workflow*). Its job `sources`
keeps the sources of A2 (artifact `referee-sources-final-43462875320`, kept until 11 February 2027)
and fetches with the ladder only what they lack, the sources of the cases added since; no key, so it
may run from any branch. Its job `measure` runs from `main` only, in environment `referee`. `live`
off and `count` on gives the estimate with counted tokens, for free. Each run directory is kept as
its own artifact `referee-run-<run>`, so the budget counts it; commit them in `runs/` afterwards.

### The referee on a pull request

`tools/pr_referee.py` judges the product files of a pull request (TAX-17). Each file gets one
outcome, in a comment on the pull request:

| outcome | when |
| --- | --- |
| *accept* | the lint passes, and every rule is *yes*, by code or by the referee |
| *reject* | a rule is *no*: the lint (TR1–TR3), a 404 (TR7), an AI chat as source (TR8), or the referee; with the rule and the reason |
| *alias* | the referee finds the same product under another key (TR4); the comment names it |
| *parked* | not taken in now: the referee doubts, a quote is not in its source, the answer is unusable, a ceiling leaves no room, or the pull request comes from a fork. With the rules and the reason |

*Doubt means: not taken in, but parked* (Fred, 14 November). Only what code holds (TV3, TV5) waits
for a person; a contributor who disagrees can say so on the pull request. The test set's *human* is
*parked* here.

**Two workflows**, so that no code of a pull request runs where the key is
(`hosting-taxnmy-publiek.md` §4 in keepitfresh):

- **`referee-pr-collect`** (`pull_request`, on `products/**` and `publishers/**`) runs the code of
  the pull request, so it has no key and a read-only token. It only keeps the data files the pull
  request adds or changes as an artifact, `proposal.json`.
- **`referee-pr`** (`workflow_run`, on `main`) reads only that artifact, and checks it before
  anything else: the pull request number, a commit, paths under `products/` and `publishers/`, at
  most 2,000 files of 64 KB. The pull request must have that commit as its head. Then:
  - **gate**, no key: stages each product file (with the publisher file it brings) on the data of
    `main`, lints it, reads its sources with the ladder of [Reading sources](#reading-sources) and runs
    [the gate](#the-gate-tr5-tr6-and-tr8-by-code). Free.
  - **judge**, in environment `referee`: the key, after Fred approves. One request per product that
    code did not settle, as **one batch** (half price, answered within hours). Model and effort:
    the repository variables `REFEREE_MODEL` (default `claude-opus-5-5`, until TAX-18 chooses) and
    `REFEREE_EFFORT` (default `low`). A pull request from a fork never gets here: code judges it, the
    rest is parked.
  - **report**: the comment, edited on the next run.
  - Only `workflow_run` starts it: that trigger can read the cache of `main` but not write it, so an
    artifact from outside cannot poison it (CodeQL, *cache poisoning*). Its jobs that read an artifact
    save no cache, and keep the artifact outside the checkout.

**Ceilings, in code.** Before the batch is sent, every request is counted (free) and gets the same
`max_tokens`, so that the whole batch, at its worst, fits the tightest of three:

| ceiling | default | variable |
| --- | --- | --- |
| per pull request, all its runs together | $2.00 | `REFEREE_PR_CAP` |
| per day (UTC), all pull requests together | $5.00 | `REFEREE_DAY_CAP` |
| everything in `runs/`, as [The hard budget](#the-hard-budget) | $50.00 | `REFEREE_BUDGET` |

Requests go in rounds: each batch holds what fits at its worst, each request with its full
`max_tokens` (4,000); once it ends, what it really cost frees room for the next round. What never
fits is parked, with the ceiling as reason. A batch that is not done after 5 hours is
cancelled; what it did not answer is parked. Each judge run writes `runs/<time>-pr<n>-<model>/`
(condition `pr`), kept as an artifact like a measurement, so the ceilings and the budget count it.

**The parking list** is `referee/parked.yaml` (TAX-29). A parked proposal waits for exactly one
thing; each has an owner and a term, in `referee/parking.yaml` (provisional; change the number there,
not in the code):

| `waits_for` | when | `owner` | term |
| --- | --- | --- | --- |
| `event` | a source could not be read, a ceiling or a fork left it unjudged, or the answer was unusable | `referee`: judged again weekly | lapses after 90 days |
| `better-proposal` | the referee doubted on sources that were all read | `submitter`: not judged again until the proposal changes | lapses after 90 days |
| `decision` | code held it for a person (TV3, TV5); or a `kind` that a person adds: `collision`, `audit` | `person`, per publisher | lapses after 30 days; a `kind` never lapses and has `decide_by`, 14 days |

The comment on the pull request names what a parked proposal waits for. Every entry has these
fields; a reader (such as the daily message, TAX-37) can rely on them:

| field | what it is |
| --- | --- |
| `key`, `path`, `publisher` | the product key, its file, and the publisher key |
| `waits_for`, `owner` | one of the three above, and who is at turn |
| `kind` | only for a decision that never lapses: `collision` or `audit` |
| `rule`, `reason` | the rules and the reason of the last judgement |
| `holds` | only for a decision: rule → what code holds it on, without the file (the subject a decision is about) |
| `since`, `checked` | the day it was first parked, and the day it was last judged |
| `lapses` or `decide_by` | the day it leaves the list, or the day a person decides by |
| `pr`, `fingerprint`, `files` | its pull request, a hash of its files, and the files, so it can be judged again without the pull request |

**Judged again.** The workflow **referee-parked** runs weekly (Mondays) and in environment `referee`
(Fred approves the key). First what is past its term lapses: it leaves the list, and its pull
request gets a comment with the reason; the submitter may propose it again. Then, by what an entry
waits for: an `event` is staged, read and gated again for free, and goes to the referee only when
nothing holds it and every source is read now; a `better-proposal` is not touched while its
`fingerprint` is the same; a `decision` is gated again and goes on only when a person has decided.
A run with nothing for the referee costs nothing. The change comes as a pull request to merge.

**A decision of a person** is kept per publisher in `referee/decisions.yaml`, one line per subject
of `holds`, with the date and the reason. It holds for every next proposal of that publisher held
for the same subject: *approve* takes the hold away (the referee judges it), *reject* rejects it on
that rule, `by: person`, with the decision as reason. Another subject still holds it.

```
python tools/pr_referee.py apply verdicts.json       # on the branch: parked to the list, rejected out
python tools/pr_referee.py parked                    # lapse, and judge again; dry: the rest stays parked
python tools/pr_referee.py parked --live             # with the referee, one batch
python tools/pr_referee.py waiting                   # what waits for a person, per publisher (JSON)
python tools/pr_referee.py decide <publisher> approve --reason "..." [--by name]
python tools/pr_referee.py decide <publisher> reject --reason "..."
python tools/pr_referee.py gate --testset            # the test set as a pull request, code only (free)
python tools/pr_referee.py judge --gated gated/gated.json --live   # and the rest, with the referee
```

`apply` takes the `verdicts.json` of the run (artifact `referee-pr-verdicts-<id>`) over on a
checkout of the pull request; a proposal parked again starts a new term. `parked` writes an
accepted entry to the data and takes it off the list, takes rejected ones off, and keeps the rest
with the new reason, its first day and its pull request. `waiting` groups the entries that wait
for a decision per publisher, the most urgent publisher first, each with its last day.

**The test set as a pull request.** Started by hand (*Actions → referee-pr-testset → Run workflow*,
from `main`), the workflow **referee-pr-testset** runs the 79 cases of the test set through the same gate and the same batch, each staged as
[Staging a case](#staging-a-case) says, and writes the count per outcome to the run page. Only the
test set may take files out of the data; the artifact of a pull request may not.

### Running it as an Action

The key is a secret of the environment `referee` (only `main`, Fred approves), so a paid measurement runs as the workflow **referee**
(`.github/workflows/referee.yml`). Start it by hand (*Actions → referee → Run workflow*), with the
condition, the cases, the model, the effort, the budget and *live*.

It has four jobs, so that the key never comes near the JavaScript of other sites:

- **fetch** reads the sources with steps 0–3, including the browser. It has no key. It first runs
  the tests of the ladder against a real headless Chromium, and keeps `sources.json` as an artifact
  `referee-sources-<id>`. It runs for condition *fetched* and for `task: check-urls`, from any
  branch. That is how the ladder is tested for free.
- **search** is step 4 for the refused and disallowed sources. It has the key and no browser, and runs only on a
  live run from `main`, in environment `referee`: it waits until Fred approves.
- **refetch** reads what search found, down steps 1–3, without a key, and keeps the result as
  `referee-sources-final-<id>`.
- **judge** reads the sources and calls the referee. It has the key, only on a live run and only
  from `main`, and runs no browser. It runs in environment `referee`, so it waits until Fred approves.

- Without *live* it is a dry run: the key is not passed to it.
- It runs one at a time. It reads only data files and runs no code from a pull request (hosting
  plan §8).
- The run directory is kept as an artifact `referee-run-<run>` for 90 days, and its summary is shown
  on the run page.
- **The budget counts the artifacts of earlier runs and the runs committed in `runs/`.** Commit the
  run directories in a pull request after a measurement, so the count survives the 90 days. If an
  artifact has expired and its run is not committed, the workflow stops.
- `task: check-urls` fetches the `verify` URLs with the fetcher of the run, as *Before the run*
  asks. It is free and needs no key.


## Grouping names per publisher (TAX-26)

A dataset of others writes one product per record: one product gets several names, and an
edition or a series stands as a product. Judged one proposal at a time, each name looks right on
its own. `tools/group.py` collects every name a contributor gives for one publisher, with the
products TAX already has of it, and one request sorts each name into a class:

| class | what follows (TAX-27) |
| --- | --- |
| *product* | a candidate: proposed with its own sources and judged by the referee |
| *alias* | an alias of a product named in the request, also one TAX already has |
| *edition* | parked: an edition or a version is not a product of its own (TP3) |
| *series* | parked: several products at once, a family, or a part without an update stream |
| *doubt* | parked (TR10) |

The prompt, what code decides before and after the request, and the settings are in
`prompt-group.md`. The grouping is not the referee and its answer is never a source (TE4): a
*product* still goes past the referee with its own sources.

```
python tools/group.py collect --kev known_exploited_vulnerabilities.json --cve cves/ --since 2023-11-16 --output names.json
python tools/group.py collect --candidates candidates.yaml --publishers map.yaml --output names.json
python tools/group.py run                       # dry run of the test set: the estimate, nothing sent
python tools/group.py run --live --repeats 3    # the measurement: three runs as one batch
```

- **collect** reads the KEV list of CISA and the CVE records (CVE List v5) of its entries, or any
  list of `{publisher, name, record, origin, versions}`. Names from ADP containers and `n/a` are left
  out. The publisher is matched to TAX character by character, on its key, its name or an alias;
  `--publishers` maps a publisher the contributor knows by another name.
- **The test set** `testset/groups.yaml`: 15 publishers of the KEV list (entries since 16 November
  2023), every name KEV and the CNA write for them, and the class a person gave each name. A list
  of classes means the hand accepts each. `existing` freezes the products of TAX the case shows.
- **The tester checks the hand.** Each case is `status: proposed` until the tester has confirmed or
  corrected every name and set `status: checked`. A live run refuses a case that is not checked
  (`--unchecked` overrides that, for a trial only).
- **Scoring** compares the answer with the hand as a partition of the names into products; which
  name of a group leads is free. A **merge** is one product of the answer that holds two products
  of the hand, an existing one included: the error this story must not make. A **split** is one
  product of the hand spread over two. Per name, an error is a class the hand does not accept
  (*hand → answer*), or a place in a merge or a split. A *doubt* is counted, not as a merge.
- Each run writes `runs/<time>-group-<model>-<effort>-run<n>/` (`results.jsonl`, `run.json`,
  `summary.md`); the three runs together `runs/group-measurement-<time>.md`, with the merges, the
  errors per kind and the cost per publisher per run. The budget is the hard limit of the
  referee: every run in `runs/` together.
- **Norm:** in three runs, no merge.
- **Result** (18 November 2026, `runs/group-measurement-20261118-122615.md`): the norm is met on
  Opus 5.5 at *low*: no merge in three runs, 0 or 1 split, 5 or 6 of the 204 names classed otherwise
  and 13 to 15 doubts, for $0.12 per run ($0.008 per publisher).
- **Estimate** (dry run, prices of `prices.yaml`): about $0.61 for three runs on Opus 5.5 at *low* as a
  batch, about $0.014 per publisher per run; at most $3.76 if every request used its full
  `max_tokens`.

As an Action: **referee-group** (*Actions → referee-group → Run workflow*). Its job `estimate` is
the dry run, from any branch and without a key. Its job `measure` runs only with *live*, from
`main`, in environment `referee`, so Fred approves it. Each run is kept as an artifact
`referee-run-<run>`, so the budget counts it; commit them in `runs/` afterwards.
