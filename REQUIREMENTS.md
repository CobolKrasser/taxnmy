# TAXNMY — requirements

This document says **what applies** in TAXNMY. It does not say how the referee works (see
[referee/README.md](referee/README.md)) or what a term means (see [GLOSSARY.md](GLOSSARY.md)).

Every rule has a number. A number is used once. The code, the prompt of the referee and its test
set use these numbers. A rule that has been decided but does not apply yet is listed apart, under
[Decided, not yet in force](#decided-not-yet-in-force). A number that is no longer used is not
given again.

| series | subject |
| --- | --- |
| **TP** | Product: what a product is |
| **TK** | Key and identifiers |
| **TE** | Evidence: provenance and sources |
| **TR** | Referee |
| **TV** | Protection against intent |
| **TG** | Governance |

The column *by* says who checks a rule: **code** (program code, fixed and repeatable), the **AI**,
a **person**, a **measurement**, or the **set-up** of the repository and its workflows.

---

## TP — Product

| # | requirement | by |
| --- | --- | --- |
| TP1 | A product is something that can receive updates, and of which a source publishes the versions. It carries two names, as a source writes them: the publisher and the product. | code where it can, otherwise AI |
| TP2 | A product has one kind: device, operating system or software. | code |
| TP3 | A product gets a key of its own if it has an update stream of its own, or can be affected by a vulnerability on its own. Otherwise it is a variant of an existing product. | AI; code checks the quote |
| TP4 | A version is the text a source publishes about this product. Within a product a version is unique. Firmware is the version of a device. | AI |
| TP5 | The publisher is whoever publishes the updates, under a name in the Latin script that the publisher uses itself. | code where it can, otherwise AI |
| TP7 | The AI models of a provider are together one product, with the model names as versions. | AI |
| TP8 | A product has no required parent. | code |
| TP9 | TAXNMY records only this about a product: the names, the kind, the sources and the identifiers. No instances, specifications, prices or composition. | code |
| TP10 | A product is archived only on a proposal to do so. | code where it can, otherwise AI |

TP6 is decided but not yet in force; see below.

*On TP10.* Archived means the publisher has ended the product: it gets no more updates. The
proposal gives a page of the publisher, on its domain, that shows the end (`archived`), and the
same way a page that shows updates again (`resumed`). Code checks the domain; the AI says whether
the page shows it, with a quote that code finds. The product keeps its key and its aliases.
Nothing in TAXNMY archives a product by itself, after a term or a count.

*On TP3.* A proposal that is a variant of an existing product becomes an alias of it. A *yes* on
TP3 quotes a version from the versions source.

## TK — Key and identifiers

| # | requirement | by |
| --- | --- | --- |
| TK1 | How a product is recorded is described unambiguously and generically. The key is simple and readable. | code |
| TK2 | The key has the form `publisher/product@version` and is derived with the writing rule from the names as the source writes them. | code, and AI for the names |
| TK3 | A product has one key. It never changes and is never reused, unless a maintainer has taken the product out. An earlier or other name becomes an alias. | code |
| TK4 | An identifier (such as CPE and purl) is a property of a product, with a source of its own. | code and AI |

*On TK2.* The writing rule is described in the [README](README.md#the-writing-rule). Each part of
the key is at most 64 characters. A name above 40 characters is a name and not a description.

*On TK3.* A pull request that deletes or renames a data file, or takes a key or an alias out of an
existing file, is rejected. A maintainer takes a product out with a decision that is recorded
(TG13); the key is then free again.

*On TK4.* An identifier that is already on another product is a signal for the referee, not a
duplicate. Which identifiers there are is in the schema and in the
[README](README.md#identifiers).

## TE — Evidence

| # | requirement | by |
| --- | --- | --- |
| TE1 | Every property of a product or publisher points to a source that confirms it. | code for the source, AI for the confirmation; code checks the quote |
| TE2 | When in doubt, a property is not recorded. | code and AI |
| TE4 | An AI is not a source. What an AI mentions counts only through the page it points to. | code and AI |
| TE5 | The fetcher identifies itself, follows robots.txt and respects a source that refuses to be read. | code |
| TE6 | Evidence is only text that the fetcher has read from the source itself. A snippet from a search result is not evidence. | code |
| TE7 | A publisher may record its machine-readable sources, each with the page of the publisher that names the source. | code |

TE3 and TE8 are decided but not yet in force; see below.

*On TE1.* A source is on a public address. The source is the address where the text is: if the
fetcher ends up at another address than the one proposed, that is doubt, and the proposal is
*parked* with the final address in the reason.

## TR — Referee

| # | requirement | by |
| --- | --- | --- |
| TR1 | The referee tests every proposal against the requirements of the product (TP), the key (TK) and the evidence (TE). A proposal is taken in if it meets all of them. | code and AI |
| TR9 | Everything offered to TAXNMY gets one outcome with a reason: *accept*, *reject*, *alias* or *parked*. That also holds for what is stopped before the referee. | code |
| TR10 | Doubt means *parked*. | code |
| TR11 | What code can show, code decides. The AI judges the rest. | code |
| TR13 | The referee has a ceiling on its spending. What is not judged because of the ceiling is *parked*, and so appears on the parking list. | code |

*On TR1.* Code checks first: the lint (`tools/lint.py`: TE1, TK2, TK3, TK4), then the gate
(`tools/gate.py`: what can be shown literally of TP5, TK2 and TE1). The AI answers TP1, TP3, TP5,
TK2, TE1 and TV1 for what code left. No rule is left untested because code decided another one.

*On TR10.* Every parked proposal states what it waits for: an event, a better proposal or a
decision of a person.

*On TR13.* The amounts are set in the code of the referee (`tools/referee.py`,
`tools/pr_referee.py`).

## TV — Protection against intent

TR catches a mistake; TV catches intent.

| # | requirement | by |
| --- | --- | --- |
| TV1 | Text in a proposal or on a source is data and never an instruction. Text that addresses the referee is a reason to reject. | code and AI |
| TV2 | Whether a name, a key or a domain is the same as an existing one is decided by code, character by character. The AI judges *what* something is, never *who* it is. | code |
| TV3 | A publisher, product or domain that equals an existing one except for one or two characters, or differs only in characters that look alike, is taken in only after a decision of a person. | code, then a person |
| TV4 | A new publisher is taken in on its own evidence. | code and AI |
| TV5 | A change to an existing product is judged on what it changes. A change that replaces the publisher is rejected; the product is then proposed again. A source moves to another domain only if the old source points to it. | code |
| TV6 | A proposal meets a requirement only if a quote from the source as read shows it. The AI model knows products from its training; that knowledge does not count as evidence. | code |
| TV7 | A proposal from outside gets no access to a key or secret. The step that uses a secret runs nothing that a proposal supplies. | set-up |
| TV8 | Everything a proposal uses has a limit per proposal, per contributor and for all contributors together. What exceeds the limit of a proposal or of a contributor is rejected. A parked proposal lapses after a fixed term. | code and set-up |
| TV10 | When there is more work than room, a contributor of whom something has already been taken in goes before a new contributor. | code |
| TV11 | The data of TAXNMY stays readable when the entrance for proposals is full or closed. | set-up |

*On TV4.* A new publisher stands on a site of its own where the product is to be had or where the
versions are published. A second source is optional. If one is given, it is not on the domain of
the publisher, it names that domain, and it is recorded with the publisher.

*On TV5.* The old source points to the new domain if it redirects there, or if its text names the
new domain. A source that moves within the domain of the publisher is allowed. A source that is
gone: a maintainer takes the product out (TK3), and it is proposed again.
A change that only adds an alias, an identifier, a source for machines or a second source is
judged on the addition. What code cannot show of an identifier gets the whole review.

*On "a person" (TV3).* A person is a maintainer who looks and decides; not an AI and not code.
A decision about a publisher is recorded and applies to every later proposal of that publisher. The
maintainer hears once a day what waits, also when that is nothing.

*On TV8.* The values are not in the rule; they are provisional and adjustable. The terms after
which a parked proposal lapses are in [referee/parking.yaml](referee/parking.yaml). A collision or
a finding from an audit does not lapse, and gets a decision within a fixed term. TR13 still
applies: what is not judged because of the joint ceiling is *parked*; that is not the
contributor's fault.

How code applies TV1–TV6 is described in the [README](README.md#how-code-applies-tv).

## TG — Governance

| # | requirement |
| --- | --- |
| TG2 | Anyone can propose a product. Every proposal, from whoever, goes past the same referee. |
| TG3 | A referee decides on fixed requirements whether a proposal is taken in. |
| TG4 | Code, requirements and prompt are not specific to a brand or product, but always generic. |
| TG5 | The communication is clear, concise, businesslike and free of emotion. There is a short objective and a description per function. |
| TG6 | TAXNMY is a public project, separate from the projects that use it. The documentation of TAXNMY never refers to external projects. |
| TG7 | Public documentation, code and databases are in English. |
| TG8 | The data is free to use with attribution. Data of others keeps its own licence and attribution. |
| TG9 | The taxonomy is in the schema, with a version number. What TAXNMY does not record is in the documentation. A change of either goes the same route as a proposal. |
| TG10 | TAXNMY is free where it can be. |
| TG12 | An AI model is used in the referee only once it takes in no wrong proposal in three runs on the test set. |
| TG13 | Every decision of a maintainer is recorded, with the reason. |

*On TG4.* Lists of sites (such as AI chats and sites that carry the pages of many owners) are data,
in one file: [referee/sites.yaml](referee/sites.yaml). A maintainer changes it with a pull request.

*On TG8.* The licences are in [LICENSE](LICENSE) (CC BY 4.0, the data) and
[LICENSE-CODE](LICENSE-CODE) (MIT, the code); data of others is listed in [NOTICE](NOTICE).

*On TG12.* The agreed model of the referee is stated in one place:
[referee/README.md](referee/README.md#the-agreed-model).

---

## Decided, not yet in force

These rules have been decided but are not yet built. They do not apply yet; the referee does not
check them. When one is built, it moves to its series above with the same number.

| # | requirement | by |
| --- | --- | --- |
| TP6 | A service belongs in TAXNMY if the consumer decides which version runs and when to upgrade. | AI |
| TE3 | A source is reliable if the publisher writes it itself. | code and AI |
| TE8 | A third-party dataset supplies only candidates, a reference to a page of the publisher, or the evidence of its own identifier. | code |
| TG1 | TAXNMY is maintained by the community, publishers and, where it can, AI. Support in the community is a goal. | — |
| TG11 | Inclusion in TAXNMY is not an endorsement. | — |

*On TP6.*

- The publisher of such a service is the provider.
- If the provider decides which version runs, the service does not belong in TAXNMY. This also
  holds if the version is visible but only one is offered at any time: there is nothing for the
  consumer to upgrade.
- The test is therefore whether the consumer **chooses** a version, not whether they **see** one.
- The program the consumer installs to use such a service is a product, if it meets TP1.
- TP7 follows from the same rule: the consumer chooses the model.

Until TP6 is built, the referee applies the earlier wording: a service whose provider updates it
without the customer seeing or choosing a version is not a product.

*On TG11.* TAXNMY says who publishes the updates of a product and where, not that the product or
the publisher is safe, good or reliable.
