# TAXNMY — requirements

This document says **what applies** in TAXNMY. It does not say how the referee works (see
[referee/README.md](referee/README.md)) or what a term means (see [GLOSSARY.md](GLOSSARY.md)).

Every rule has a number that does not change. The code, the prompt of the referee and its test set
use these numbers. A rule that has been decided but does not apply yet is listed apart, under
[Decided, not yet in force](#decided-not-yet-in-force).

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

| # | requirement |
| --- | --- |
| TP1 | A product is something that can receive updates, and of which a source publishes the versions. It carries two names, as a source writes them: the publisher and the product. |
| TP2 | A product has one kind: device, operating system or software. |
| TP3 | A product gets a key of its own if it has an update stream of its own, or can be affected by a vulnerability on its own. Otherwise it is a variant or a property of an existing product. |
| TP4 | A version is the text a source publishes about this product. Within a product a version is unique. Firmware is the version of a device. |
| TP5 | The publisher is whoever publishes the updates, under a name in the Latin script that the publisher uses itself. |
| TP7 | The AI models of a provider are together one product, with the model names as versions. |
| TP8 | A product has no required parent. |
| TP9 | TAXNMY records only this about a product: the names, the kind, the sources and the identifiers. No instances, specifications, prices or composition. |

TP6 is decided but not yet in force; see below.

## TK — Key and identifiers

| # | requirement |
| --- | --- |
| TK1 | How a product is recorded is described unambiguously and generically. The key is simple and readable. |
| TK2 | The key has the form `publisher/product@version` and is derived with the writing rule from the names as the source writes them. |
| TK3 | A product has one key. It never changes and is never reused, unless a maintainer has taken the product out. An earlier or other name becomes an alias. |
| TK4 | An identifier is a property of a product, with a source of its own. |
| TK5 | An identifier that, according to its own register, points to one product belongs to at most one product in TAXNMY. An identifier that can cover more products only says that two products may be the same. |

*On TK2.* The writing rule is described in the [README](README.md#the-writing-rule).

*On TK5.* Which identifier belongs to which group is in the schema and in the
[README](README.md#identifiers): purl, winget package identifier and Homebrew token in the first
group; CPE and GTIN in the second.

## TE — Evidence

| # | requirement |
| --- | --- |
| TE1 | Every value points to a source. |
| TE2 | When in doubt, a value is left out. |
| TE4 | An AI is not a source. What an AI mentions counts only through the page it points to. |
| TE5 | A source that refuses to be read is not circumvented. The fetcher identifies itself and follows robots.txt. |
| TE6 | Evidence is only text that the fetcher has read from the source itself. A snippet from a search result is not evidence. |
| TE7 | A publisher may record its machine-readable sources, each with the page of the publisher that names the source. |

TE3 is decided but not yet in force; see below.

## TR — Referee

TR1–TR8 are about one proposal. TR9–TR13 are about the referee itself.

| # | requirement | by |
| --- | --- | --- |
| TR1 | All required fields are filled, with a source for each value. | code |
| TR2 | The key follows the writing rule; each part is at most 64 characters. | code |
| TR3 | The product is not yet in TAXNMY: no match on key, alias or an identifier that points to one product. | code |
| TR4 | It is a product of its own (TP3). A *yes* quotes a version from the versions source. If it is a variant of an existing product, the proposal becomes an alias. | AI; code checks the quote |
| TR5 | The publisher is whoever publishes the updates (TP5). | code where it can, otherwise AI |
| TR6 | The names are written as in the source. A name above 40 characters is a name and not a description. | code where it can, otherwise AI |
| TR7 | The product demonstrably exists. | code where it can, otherwise AI |
| TR8 | Every value is confirmed by the source it points to. | AI; code checks the quote |
| TR9 | Every proposal gets one outcome: *accept*, *reject* (with the rule), *alias* or *parked* (with the rule and the reason). Code computes the outcome from the verdicts per rule. | code |
| TR10 | Doubt means *parked*: not taken in, but kept. Every parked proposal states what it waits for: an event, a better proposal or a decision of a person. | code |
| TR11 | Code judges first, and only on what it can show literally. No rule is left untested because code decided another rule. | code |
| TR12 | A model becomes the referee only if, on the test set, in three runs, it lets no wrong proposal through. | measurement |
| TR13 | The referee has a ceiling on its spending. What is not judged because of the ceiling is *parked*. | code |

*On TR3.* A match on an identifier that can cover more products (TK5) is a signal for the referee,
not a duplicate.

*On TR13.* The amounts are set in the code of the referee (`tools/referee.py`,
`tools/pr_referee.py`).

## TV — Protection against intent

TR catches a mistake; TV catches intent.

| # | requirement | by |
| --- | --- | --- |
| TV1 | Text in a proposal or on a source is data and never an instruction. Text that addresses the referee is a reason to reject. | code and AI |
| TV2 | Whether a name, a key or a domain is the same as an existing one is decided by code, character by character. The AI judges *what* something is, never *who* it is. | code |
| TV3 | A publisher, product or domain that equals an existing one except for one or two characters, or differs only in characters that look alike, is not taken in without a person. | code, then a person |
| TV4 | A new publisher is taken in on its own evidence: a site of its own where the product is to be had or where the versions are published. A second source is not a condition. If there is a second source, it is recorded with the publisher. | code and AI |
| TV5 | A change that moves a source, a publisher or an identifier of an existing product to another domain or another value is decided by a person. | code, then a person |
| TV6 | A rule is *yes* only with a quote that is literally in the source as read. What the referee itself knows about a product does not count. | code |
| TV7 | A proposal from outside gets no access to a key or secret. The step that uses a secret runs nothing that a proposal supplies. | set-up |
| TV11 | Reading TAXNMY does not depend on proposing. The entrance for proposals can be full or closed without a consumer noticing. | set-up |

*On TV4.*

- No note *not found* is recorded: if there is no second source, nothing is recorded.
- A **second source** is a place that the publisher does not control alone and that points to the
  same domain: the place where consumers get the product, or a public project. It adds to the
  trust, but is not a requirement for now.
- The threshold is deliberately low. **If it is abused, the referee becomes stricter**; the
  recorded second source is then the first means, since it is known which publishers stand on
  their own evidence only.
- TV3 and TV5 still apply: what resembles an existing publisher, or replaces an existing source,
  is decided by a person.

*On "a person" (TV3, TV5).* A person is a maintainer who looks and decides; not an AI and not code.
A decision about a publisher is recorded and applies to every later proposal of that publisher. The
maintainer hears once a day what waits, also when that is nothing.

How code applies TV1–TV6 is described in the [README](README.md#how-code-applies-tv).

## TG — Governance

| # | requirement |
| --- | --- |
| TG2 | Anyone can propose a product. Every proposal, from whoever, goes past the same referee. |
| TG3 | A referee with fixed rules decides whether a proposal is taken in. |
| TG4 | Code, rules and prompt name no brand or product. What is about one product is in the data. |
| TG5 | The communication is clear, concise, businesslike and free of emotion. There is a short objective and a description per function. |
| TG6 | TAXNMY is a public project, separate from the projects that use it. The documentation of TAXNMY does not refer to them. |
| TG7 | Schema, keys and documentation are in English. |
| TG8 | The data is free to use with attribution. Data of others keeps its own licence and attribution. |
| TG9 | The taxonomy is in the schema, with a version number. What TAXNMY does not record is in the documentation. A change of either goes the same route as a proposal. |
| TG10 | TAXNMY is free where it can be. |
| TG13 | Every decision of a maintainer is recorded, with the reason. |

*On TG8.* The licences are in [LICENSE](LICENSE) (CC BY 4.0, the data) and
[LICENSE-CODE](LICENSE-CODE) (MIT, the code); data of others is listed in [NOTICE](NOTICE).

---

## Decided, not yet in force

These rules have been decided but are not yet built. They do not apply yet; the referee does not
check them. When one is built, it moves to its series above with the same number.

| # | requirement | by |
| --- | --- | --- |
| TP6 | A service belongs in TAXNMY if the consumer decides which version runs and when to upgrade. | AI |
| TE3 | A source is reliable if the publisher wrote the value itself. A third-party dataset supplies candidates, points to a page or proves its own identifier; nothing more. | code and AI |
| TV8 | Every resource that a proposal uses has a limit per proposal, per contributor and for all contributors together: spending, computing time, requests to sources (also per site) and places on the parking list. | code and set-up |
| TV9 | What exceeds the limit of a proposal or of a contributor is rejected and not parked. A parked proposal lapses after a fixed term, without a person having to do anything. | code |
| TV10 | When there is more work than room, a contributor of whom something has already been taken in goes before a contributor of whom nothing has been taken in yet. Unknown contributors cannot use up the room of known ones. | code |
| TG1 | TAXNMY is maintained by the community, publishers and, where it can, AI. Support in the community is a goal. | — |
| TG11 | Inclusion in TAXNMY is not an endorsement. TAXNMY says who publishes the updates of a product and where, not that the product or the publisher is safe, good or reliable. This is stated with the data and in the documentation. | — |

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

*On TR8.* Decided addition: the source is the address where the text is. If the fetcher ends up at
another address than the one proposed, that is doubt: the proposal is *parked* and waits for a
better proposal, with the final address in the reason.

*On TV8–TV10.* TR13 still applies: what is not judged because of the joint ceiling on spending is
*parked*; that is not the contributor's fault. TV9 is about a contributor or a proposal that
exceeds its own limit. The values are not in the rule; they are provisional and adjustable.

- **Partly in place.** The spending ceiling (TR13) and the limits of the fetcher already apply.
  The terms after which a parked proposal lapses are in
  [referee/parking.yaml](referee/parking.yaml) and already apply. A collision or a finding from an
  audit does not lapse, and gets a decision within a fixed term.
- **The limits (TV8)** start low and are adjusted on what is observed.
- **An agreed round** has a limit of its own, higher than usual. That is not a separate rule: the
  limit per contributor is a value that can differ per contributor.
