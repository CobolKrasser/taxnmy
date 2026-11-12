# TAXNMY

**TAXNMY gives every product that can receive updates — a device, an operating system or
software — one open, readable key, and links that key to the codes others already use for it.**

TAXNMY does not replace CPE, purl or GTIN. It links them.

```
synology/ds224-plus
the-php-group/php@8.3.14
```

## How it works

### The key

A key has the form `publisher/product@version`.

- `publisher/product` identifies a product. It is the key stored in the data.
- `@version` is optional. You write it after the key to refer to one version, as with purl.
- A key never changes and is never reused. When a product is renamed, it gets a new key and the
  old name becomes an alias.
- Each part is at most 64 characters.

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

A product is something that can receive updates. It is one of three kinds:

| kind | meaning |
| --- | --- |
| `device` | a physical product that carries firmware (a device, in other words hardware) |
| `operating-system` | an operating system, including replaceable firmware that runs on devices of others |
| `software` | software |

Something is a product of its own if it has **its own update stream**, or can be **affected by a
vulnerability on its own**. Otherwise it is a variant or a property of an existing product. A
router sold in an EU and a US edition with the same firmware is one product with two GTINs.

Firmware is not a kind. It is the version axis of a device.

Software does not need a parent product, such as the operating system it runs on.

### Publishers

The publisher is whoever publishes the updates, under a Latin name the publisher uses itself.

### Versions

A version is the text a source publishes about this product. A product file names the source of
its versions.

### Identifiers

Existing codes are properties of a product, each with its source:

| code | unique to one product? | when a proposal matches an existing product |
| --- | --- | --- |
| purl (without version and qualifiers) | yes, except type `generic` | duplicate |
| CPE (vendor and product) | no | signal for review |
| GTIN | no (bundles share one) | signal for review |

### Sources

Every value points to a source. When in doubt, a value is left out.

### Aliases

An alias is an earlier or other key of the same product. A proposal that matches a key or an alias
is a duplicate.

### The referee

Every proposal arrives as a pull request and is checked against nine rules. S1–S3 are checked by
code on every pull request; S4–S8 will be judged by an AI referee with the source at hand.

| rule | checks | by |
| --- | --- | --- |
| S1 | All required fields are filled, with a source URL for each value. | code |
| S2 | The key follows the writing rule; each part is at most 64 characters. | code |
| S3 | No match on key or alias. A match on a purl is a duplicate; a match on CPE or GTIN is a signal for S4. | code |
| S4 | It is a product of its own: its own update stream or its own vulnerabilities. | AI, with the source |
| S5 | The publisher is whoever publishes the updates. | AI, with the source |
| S6 | The names are written as in the source. Above 40 characters: is it a name or a description? | AI, with the source |
| S7 | The product demonstrably exists. | AI, with the source |
| S8 | A value that only an AI mentions, without a confirming source, is not accepted. | code and AI |
| S9 | The rules themselves name no brand or product. | review |

## Scope

TAXNMY records products, their publishers, where their versions are published, and the codes
others use for them. It does not record:

- **instances**: a single unit of a product, its serial number or where it is installed;
- **technical specifications**, such as capacity, power use or dimensions. If they are added
  later, it will only be from a fixed vocabulary, with a unit and a source for every value;
- **prices**;
- **composition**: the parts a product is built from, and their firmware;
- **names in other scripts** than the Latin script.

The schema accepts no fields other than the ones it defines, so a proposal with any of these values
is rejected on S1. A change of the scope goes through a pull request, like any other change.

## Repository layout

```
publishers/<publisher>.yaml          one file per publisher
products/<publisher>/<product>.yaml  one file per product
schema/                              JSON schemas for both files
tools/                               the lint (writing rule, S1–S3)
tests/                               tests for the lint
```

How to propose a product: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Stack

Stack: Python 3.12+ with two libraries, PyYAML and jsonschema; data in YAML, checked against JSON
Schema; GitHub Actions runs the lint on every pull request.

## Licence

The data (`products/`, `publishers/`) is licensed under [CC BY 4.0](LICENSE). Data from
endoflife.date is used under its MIT licence, with attribution; see [NOTICE](NOTICE).

The code (`schema/`, `tools/`, `tests/`) is licensed under the [MIT licence](LICENSE-CODE).
