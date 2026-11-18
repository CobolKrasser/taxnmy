# TAXNMY — glossary

Every term has one meaning. In TAXNMY, use the term in the first column and no other word for the
same thing. The term is the one the schema and the code use; where they use another word, the
glossary says so. A rule number refers to [REQUIREMENTS.md](REQUIREMENTS.md).

**Code** always means *program code*: what decides in a fixed and repeatable way, as opposed to
the AI. A code that another register gave a product is an **identifier**.

## 1. The register

| term | meaning |
| --- | --- |
| TAXNMY | the name of the product and of this repository |
| taxonomy | the classification in the schema: the kinds and the fields. Has a version number (TG9) |
| glossary | this list of terms. Is not the taxonomy |
| scope | what TAXNMY does and does not record (TP9) |
| consumer | whoever uses the data of TAXNMY. In TP6, the consumer of a service; the prompt of the referee calls them the *customer* |
| community | everyone who proposes or follows along without being a maintainer |
| maintainer | whoever may merge a proposal |

## 2. The product

| term | meaning |
| --- | --- |
| product | something that can receive updates, and of which a source publishes the versions (TP1) |
| publisher | whoever publishes the updates of a product (TP5). Not: the seller, the maker of the casing or the shop |
| kind | device, operating system or software (TP2); in the schema `device`, `operating-system`, `software` |
| device | a physical product that carries firmware. *Hardware* is not a term |
| operating system | also firmware that is replaceable and runs on devices of others |
| software | what is not a device and not an operating system |
| version | the text a source publishes about this product (TP4) |
| firmware | the version of a device. Is not a kind |
| update stream | the series of versions a publisher publishes for one product |
| product of its own | a product that meets TP3 |
| variant | an edition without an update stream of its own: colour, size, region, bundle |
| service | software that runs at a provider |
| managed service | a service in which the consumer chooses and upgrades the version (TP6) |
| software as a service | a service in which the provider decides the version. Does not belong in TAXNMY |
| provider | whoever offers a service or an AI model; in TAXNMY its publisher |
| parent | the product another product runs on. TAXNMY does not record it (TP8) |
| instance | one unit of a product, with a serial number. TAXNMY does not record it |

## 3. The key and the identifiers

| term | meaning |
| --- | --- |
| key | `publisher/product`, with `@version` after it to point to one version |
| writing rule | the fixed steps that turn a name into a part of the key ([README](README.md#the-writing-rule)) |
| alias | an earlier or other key of the same product |
| identifier | a code that another register gave the product: CPE, purl, GTIN, winget package identifier, Homebrew token |
| duplicate | a proposal of a product that is already in TAXNMY |
| signal | an indication that two products may be the same. Does not reject; the referee decides |

## 4. Provenance and sources

| term | meaning |
| --- | --- |
| source | a web address where a value can be read |
| provenance | the source with a value |
| product source | the page where the name and the kind of the product are; in the schema `source` |
| versions source | the page where the versions of the product are published; in the schema `versions.source` |
| publisher source | the page where the publisher uses its name; in the schema `source` |
| machine-readable source | a file the publisher publishes for programs and not for people, in a fixed format: an update catalog, AppStream metadata, CSAF; in the schema `machine_sources` |
| reliable source | a source of which the publisher wrote the value itself (TE3) |
| own evidence | what the publisher publishes itself: its site, its downloads, its release page |
| second source | a place the publisher does not control alone and that points to the same domain: where consumers get the product, or a public project. Adds to the trust; not a condition (TV4). In the schema `second_source` |
| third-party dataset | a collection about products that the publisher does not write itself: winget, Homebrew, the CPE dictionary, CVE, endoflife.date |
| candidate source | a third-party dataset that supplies names of possible products. Proves nothing |
| finder | a third-party dataset or an AI that points to a page of the publisher. The evidence is the page |
| identifier source | a third-party dataset that proves only its own identifier |
| mirror | a local copy of a third-party dataset |
| fetcher | the program that fetches a source and turns it into text (`tools/fetch.py`) |
| quote | a piece of text that is literally in the source as read |
| refusal | a source that does not admit the fetcher: 401, 403, or a ban in robots.txt |

A dataset proposes nothing: it is a **candidate source**. A person or a program proposes, with a
proposal (§5).

## 5. The referee

| term | meaning |
| --- | --- |
| proposal | a request to take in or change a product or publisher. Arrives as a pull request |
| contributor | whoever makes a proposal. The parking list names this owner `submitter` |
| referee | the whole that judges a proposal: the lint, the gate and the AI |
| lint | the code that checks TR1–TR3 (`tools/lint.py`) |
| gate | the code that decides, of TR5–TR8, what can be shown literally, before the AI (`tools/gate.py`) |
| harness | the code that runs the referee (`tools/referee.py`, `tools/pr_referee.py`) |
| verdict | the answer on one rule: *yes*, *no* or *doubt* |
| outcome | the decision on one proposal: *accept*, *reject*, *alias* or *parked* |
| parking list | the proposals with the outcome *parked* (`referee/parked.yaml`) |
| waiting for an event | *parked* because a source is closed or failing, or the ceiling was reached. The referee tries again (`event`) |
| waiting for a better proposal | *parked* because the source as read does not show what it must. The contributor is next (`better-proposal`) |
| waiting for a decision | *parked* because code held the proposal for a person (TV3, TV5) (`decision`) |
| lapse | a parked proposal that leaves the parking list after its term, without anyone doing anything (TV9) |
| test set | the fixed collection of good and bad proposals with which the referee is measured (`referee/testset/`) |
| model measurement | the test set three times through one model (TR12) |
| ceiling | the amount the referee spends at most (TR13) |

## 6. The initial fill

| term | meaning |
| --- | --- |
| initial fill | the one-off work to bring TAXNMY from empty to usable |
| round | one pass of a third-party dataset as a candidate source |
| finding round | a round in which an AI looks for the pages of the publisher per candidate; a person runs it |
| count | establishing how many candidates a dataset yields, before anything is built (`tools/census.py`) |

## 7. Protection

| term | meaning |
| --- | --- |
| vulnerability | a flaw in a **product** through which it can be misused (TP3). In the series TV: a place where **TAXNMY itself** can be misused |
| pitfall | a property of the AI or of the way of working through which a mistake goes unnoticed, without anyone attacking |
| intent | a proposal made to mislead |
| imitation | a name or domain that resembles an existing one |
| a person | a maintainer who looks and decides; not an AI and not code |
| endorsement | a judgement that a product or publisher is safe, good or reliable. TAXNMY does not give one (TG11) |

*Vulnerability* thus has two meanings. Where they could be confused, add *of a product* or *of
TAXNMY*.

## 8. Words not used

| not | but |
| --- | --- |
| machines, sources for machines | machine-readable source |
| code (for CPE, purl, …) | identifier |
| hardware | device |
| manufacturer | publisher |
| the AI referee | the referee (the whole), or the AI (the model) |
