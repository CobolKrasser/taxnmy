# TAXNMY

**TAXNMY gives every product that can receive updates — a device, an operating system or
software — one open, readable key, and links that key to the codes others already use for it.**

TAXNMY does not replace CPE, purl or GTIN. It links them.

```
synology/ds224-plus
the-php-group/php@8.3.14
```

## The rules and the terms

- [REQUIREMENTS.md](REQUIREMENTS.md): every rule of TAXNMY, numbered by series (TP product, TK key
  and identifiers, TE evidence, TR referee, TV protection against intent, TG governance).
- [GLOSSARY.md](GLOSSARY.md): what each term means, with the name the schema and the code use.

This README describes how TAXNMY is built up. Where it names a rule, the rule itself is in
REQUIREMENTS.md.

## How it works

### The key

A key has the form `publisher/product@version` (TK2). `publisher/product` identifies a product and
is the key stored in the data; `@version` is optional and points to one version, as with purl. A
key never changes (TK3); an earlier or other name becomes an alias.

### The writing rule

Every part of a key is derived from the name as the source writes it:

1. Accented letters become plain letters (`Brötje` becomes `brotje`), and everything becomes
   lower case.
2. Four characters that carry meaning become a word: `+` becomes `plus`, `#` becomes `sharp`, `&`
   becomes `and`, and `@` becomes `at`.
3. Every other character, including a space, becomes `-`. A run of dashes becomes one dash, and a
   dash at the start or end is dropped.
4. **Publisher only:** a legal form at the end is dropped (`Inc.`, `GmbH`, `B.V.`, `& Cie. KG`, …).
   `Synology Inc.` and `Synology` are one publisher. The legal form is dropped before step 2.
5. **Version only:** spaces become `-`; `.`, `+` and `_` stay. Step 2 does not apply.

Examples:

| publisher, product, version | key |
| --- | --- |
| Synology Inc., DS224+ | `synology/ds224-plus` |
| AVM GmbH, FRITZ!Box 7590 AX | `avm/fritz-box-7590-ax` |
| Microsoft, C# | `microsoft/c-sharp` |
| Miele & Cie. KG, Dialogoven DO 7860 | `miele/dialogoven-do-7860` |
| Synology, DS224+, 7.2.1-69057 Update 5 | `synology/ds224-plus@7.2.1-69057-update-5` |

Names use the Latin script. Names in other scripts are not accepted at this stage.

### Products

A product (TP1) carries two names, as a source writes them: the publisher and the product. It is
one of three kinds (TP2):

| kind | meaning |
| --- | --- |
| `device` | a physical product that carries firmware |
| `operating-system` | an operating system, including replaceable firmware that runs on devices of others |
| `software` | software |

When something gets a key of its own is TP3; a router sold in an EU and a US edition with the same
firmware is one product with two GTINs.

### Publishers

The publisher (TP5) has a file of its own. A publisher file may list its machine-readable sources
(`machine_sources`, TE7): an update catalog, AppStream metadata or CSAF provider metadata, each
with the page of the publisher that names it. A second source, when there is one, is recorded in
`second_source` (TV4).

### Versions

A product file names the source of its versions (`versions.source`). How a version is built up is
observed per product, with its source (TP4).

### Identifiers

Existing codes are identifiers of a product, each with a source of its own (TK4): purl (without
version and qualifiers), winget package identifier, Homebrew token, CPE (vendor and product) and
GTIN. An identifier that is already on another product is a signal: the proposal goes to the
referee, which checks with TP3 whether it is a product of its own.

### Aliases

An alias is an earlier or other key of the same product. A proposal that matches a key or an alias
is a duplicate (TK3).

### Archived products

A product is archived when its publisher has ended it: it gets no more updates (TP10). Only a
change that proposes it sets `archived`, with a page of the publisher that shows the end; a change
that takes the product up again replaces it with `resumed` and a page that shows new updates.
Nothing in TAXNMY archives a product by itself, after a term or a count. An archived product keeps
its key and its aliases, and its file stays where it is.

### The referee

Every proposal arrives as a pull request and is judged against the rules of the referee (TR) and
the rules that protect against intent (TV). TE1, TK2 and TK3 are checked by code on every pull request
(the lint); TP1, TP3, TP5, TK2 and TE1 are judged by an AI with the source at hand, after code has settled what it can
(the gate). See [referee/README.md](referee/README.md).

#### How code applies TV

How code applies TV1–TV6 (`tools/guard.py`, the lint, and `referee/prompt.md`):

| rule | what code does | outcome |
| --- | --- | --- |
| TV1 | Every text from a proposal or a source reaches the referee as data, and cannot write the markers of the message. The referee answers TV1; a *no* quotes the text that addresses it, and code finds that quote. | *reject* |
| TV2 | The message to the referee holds an `<identity>` block: whether the publisher and each domain is already in TAX, compared character by character. The referee is told not to judge that. | — |
| TV3 | The lint gives a signal for a publisher key, a product key within its publisher, or a domain that is one or two characters from an existing one, or the same once characters that look alike are read as one (`0` and `o`, `rn` and `m`, …). A difference in digits only (another model number) does not count. | *parked*, with the existing item |
| TV4 | Nothing is required of a new publisher beyond its own site. A second source that a publisher file gives (`second_source`) must be a page that is not on the publisher's domain, that can be read, and that names the publisher's domain. The lint checks where it stands; the gate reads it. | *reject* when a given second source does not pass |
| TV5 | A change to an existing product is judged on what it changes. A change that replaces the publisher is rejected; the product may be proposed again. A source that moves to another domain passes only if the old source, read now, redirects there or names the new domain. A code that is changed or taken out, a new domain for machines, or another second source is held for a person. A change that only adds an alias, an identifier, a source for machines or a second source is judged on the addition; code accepts what it can show, and the rest gets the whole review. | *reject*, or *parked* for a code |
| TV6 | Every *yes* needs a quote that code finds in the source text. | *doubt*, so *parked* |

A proposal held for a person (TV3, or TV5 on a code) still goes to the referee, so that no rule is
left untested; a *reject* stays a *reject*, anything else is *parked* for a person.

## Scope

What TAXNMY records, and what not, is TP9. Services are covered by TP6, which is decided but not
yet in force. Names in other scripts than the Latin script are not accepted at this stage (TP5).
If technical specifications are added later, it will only be from a fixed vocabulary, with a unit
and a source for every value.

The schema accepts no fields other than the ones it defines, so a proposal with any other value is
rejected on TE1. A change of the scope goes through a pull request, like any other change (TG9).

## Repository layout

```
publishers/<publisher>.yaml          one file per publisher
products/<publisher>/<product>.yaml  one file per product
schema/                              JSON schemas for both files
tools/                               the lint (writing rule, TE1, TK2 and TK3), the referee harness, the count of candidate sources (`census.py`) and the finders of a source of the publisher itself, for a publisher (`ownsource.py`) and for a product (`ownversions.py`)
tests/                               tests for the lint and the harness
referee/                             prompt, test set and runs of the referee (TP1, TP3, TP5, TK2 and TE1)
```

How to propose a product: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Stack

Stack: Python 3.12+ with two libraries, PyYAML and jsonschema; data in YAML, checked against JSON
Schema; GitHub Actions runs the lint on every pull request. The referee harness also uses the
Anthropic Python SDK (`requirements-referee.txt`); see [referee/README.md](referee/README.md).

## Licence

The data (`products/`, `publishers/`) is licensed under [CC BY 4.0](LICENSE). Data from
endoflife.date is used under its MIT licence, with attribution; see [NOTICE](NOTICE).

The code (`schema/`, `tools/`, `tests/`) is licensed under the [MIT licence](LICENSE-CODE).
