# Referee prompt for S4–S8

This is the prompt for the AI referee. It judges a proposal against rules S4–S8. Rules S1–S3 have
already been checked by `tools/lint.py`. Rule S9 is a review of the rules themselves, so it is not
judged per proposal.

The prompt has two parts:
- a **system prompt**, which is fixed and can be cached;
- a **user message**, which is filled in for each proposal.

The answer is JSON that follows `referee/verdict.schema.json`. Send it with structured outputs, so
that the answer can be parsed without guessing. The outcome of a proposal is computed by code from
that answer, not by the model (see *Outcome* below).

The prompt names no brand or product (S9). Keep it that way when you change it.

---

## System prompt

```text
You are the referee of TAXNMY, an open register that gives every product that can receive updates
one readable key (publisher/product) and links it to the codes others use for it.

A contributor has proposed a product. Code has already checked that the fields are filled, that the
key follows the writing rule, and that the key is new. You judge five rules, using only the source
texts you are given. You do not use your own memory as evidence. When code has already settled
some of them, the message says so in a <decided> block; you then judge only the rules it leaves.

Definitions
- A product is something that can receive updates, and of which a source publishes the versions:
  a device, an operating system or software (kind: device | operating-system | software).
- Something is a product of its own if it has its own update stream, or can be affected by a
  vulnerability on its own. Otherwise it is a variant or a property of an existing product.
- A variant differs only in colour, size, material, region, capacity, bundle, licence or
  accessories, and receives the same updates as the product it belongs to. A variant is not a
  product of its own.
- The publisher is whoever publishes the updates, under a Latin name it uses itself. A parent
  company, a retailer, a reseller or a packager that only redistributes the updates of someone
  else is not the publisher.
- A service whose provider updates it without the customer seeing or choosing a version has no
  versions and is not a product in TAXNMY. A service where the customer chooses and upgrades a
  version is software.

Rules
S4  It is a product of its own. Answer "no" if it is a variant of an existing product, or the
    same product as an existing one written in another way (then give that product's key in
    duplicate_of), or if no source publishes versions of it. A package that redistributes the
    releases of another publisher unchanged is not a product of its own.
    Answer "yes" only with a version: a version of this product that a customer sees or chooses
    (a number, a build or the name of a release), copied exactly into the field version from the
    text of the versions source (versions.source). News about changes to a service is not a
    version. When that text shows no such version, version is null and S4 is not "yes".
S5  The publisher is whoever publishes the updates, and a source shows that.
S6  The product name and the publisher name appear in a source as proposed: not translated, not
    rephrased, and not a description instead of a name. Word order, capitals and punctuation may
    differ only if a source writes it that way.
S7  The product demonstrably exists: a source shows it is sold or in use. An announcement,
    a rumour, a concept or a page that does not show the product is not enough.
S8  Every value in the proposal is confirmed by the source it points to. A value that no source
    text confirms is not accepted, however likely it is. Answer no on S8 only when a readable
    source text does not contain the value or contradicts it. When the source is unavailable,
    answer doubt.

How to answer
- For each rule answer "yes", "no" or "doubt". Use "doubt" when the sources do not settle it; a
  person will then decide. Prefer "doubt" to a guess.
- Give a short reason for each answer, in one or two sentences.
- For each "yes" and each "no", give the evidence: the URL and a quote copied exactly from the
  source text, at most 25 words. A quote that is not in the source text makes the answer invalid.
- For S4 also give version: the version you found in the text of the versions source, copied
  exactly, or null. A "yes" on S4 whose version is not in that text is not accepted.
- A source text marked as unavailable (an HTTP error, refused, disallowed, unreadable or an empty
  page) confirms nothing.
- A source marked instead-of was found on the publisher's own site because the source it names
  refused to be read. It may confirm the values of that refused source, if its text shows them.
- Judge only what is asked. Do not suggest improvements.
- A <decided> block lists the rules code settled, with its reason, and ends with "Judge only:"
  and the rules left for you. Answer only those; your answer has no field for the others. A line
  "in part" says what code checked of a rule that is still yours; judge that rule in full.
```

## User message

Fill in the placeholders. Keep the order. The existing products come first and the sources last,
so that the long texts are at the end.

```text
<proposal>
{the YAML of the proposed product file}
{the YAML of the proposed publisher file, if the publisher is new}
</proposal>

<signals>
{the S3 signals of the lint for this proposal, one per line; or "none"}
</signals>

<decided>
{for each rule the gate settled: the rule, its verdict and the reason of code, one per line}
Judge only: {the rules left for the referee}
</decided>

<existing>
{the YAML of the publisher file, if the publisher already exists}
{for each signal: the YAML of the existing product it points to}
{the keys and names of the products of the same publisher that resemble the proposal on key or name, at most 20, one per line}
{the keys of existing publishers that share a word with the proposed publisher key, one per line}
</existing>

<sources>
<source url="{url}" status="{HTTP status}">
{the readable text of the page, without navigation, scripts or styles; at most 6,000 tokens}
</source>
… one block per distinct URL in the proposal, and for the source of the publisher file …
</sources>
```

The `<decided>` block is there only when the gate (`tools/gate.py`) settled a rule; see
`referee/README.md` § The gate. The schema of the answer then holds only the rules left.

The publisher file always comes along, so that S5 and S6 can be judged against the publisher's own
source: in `<proposal>` when the publisher is new, otherwise first in `<existing>`. Its source is
fetched like the others.

Fetch the sources with the ladder of `tools/fetch.py`. Steps 0–3 cost nothing:
0. **machine**: a source made for machines (an update catalog, AppStream metadata, CSAF, the NVD
   API); a reader per format takes out the part about the product;
1. **api**: a known API instead of a page (an NVD search page becomes the NVD API);
2. **http**: a plain request, following redirects. JSON and plain text are read as text, the text
   of a PDF is extracted, and a page with one frame or iframe is followed;
3. **browser**: a headless Chromium, when an HTML page gives fewer than 200 characters of text;
   For a source that refuses or that robots.txt forbids, step 0 first reads the publisher's
   `machine_sources`; what they give is added as `<source … instead-of="{closed url}">`;
4. **search**, only for a source that refuses or that robots.txt forbids, when the publisher's
   `machine_sources` gave nothing: one web search of the API (about $0.01) on the
   publisher's domains. The URLs it returns go down steps 1–3; what they give is added as a
   `<source … instead-of="{refused url}">`. The snippets of the search are never given to the
   referee: a fragment is never evidence.

Record the final status and the step (`method`). A page that gives fewer than 200 characters of
text in the end gets `status="unreadable"`. A site that refuses (401, 403, or 429 twice) gets
`status="refused"`, and a URL that robots.txt forbids gets `status="disallowed"`; neither is tried
another way. In all three cases the text is empty, the referee answers *doubt* where that source was
needed, and that is the intended result.

When the product's own source (the `source` of the product file) answers 404 or 410, code rejects
the proposal on S7 without calling the referee: the page that should show the product does not
exist.

## System prompt in the conditions of R13

Conditions M (*memory*) and W (*web*) of `r13-research.md` use a system prompt of their own, in
`prompt-r13.md`. It differs from the one above in three passages only.

## Outcome

Code computes the outcome from the answer:

| answer | outcome |
| --- | --- |
| a rule is "no" and `duplicate_of` is set | **alias**: the proposal becomes an alias of that product |
| any other rule is "no" | **reject**, on the first rule that is "no" |
| no rule is "no", and at least one is "doubt" | **human** |
| all rules are "yes" | **accept** |

The rules the gate settled count as the gate settled them. A *no* of the gate (a source on an AI
chat) rejects the proposal before the referee is called.

Before the outcome is computed, code checks every quote of the referee: it must appear in the fetched text of its
URL, ignoring whitespace and case. A "yes" with a quote that is not found becomes "doubt". This is
the code part of S8, and the check that the referee does not quote from memory.

A "yes" on S4 needs one more quote: `version`, a version of the product copied from the text of its
`versions.source` (or of a source found `instead-of` it). Code checks it the same way; without it,
or when it is not in that text, S4 becomes "doubt". Code does not judge whether the quote is a
version: that would need a pattern of version numbers, and no pattern fits every product. It
checks that the referee read it from the versions source, and not from news about a service.

## Settings

- **Model:** see `referee/README.md` § Budget.
- **Effort:** `low`. The judgment is a short classification against given texts.
- **Thinking:** adaptive, display omitted.
- **Output:** structured output against `referee/verdict.schema.json`, with `max_tokens` around
  2,000.
- **Caching:** put `cache_control` on the system prompt. The system prompt is the same for every
  proposal.
