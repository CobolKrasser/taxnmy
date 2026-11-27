# Taxonomy changelog

Changes to the TAXNMY taxonomy: the schemas in `schema/` for products and publishers. Both schemas
carry the same version, in the field `version`.

The version follows [Semantic Versioning](https://semver.org/):

- **major**: a change that can make existing files or existing readers invalid, such as a field that
  is removed, renamed or becomes required, or a kind that is removed;
- **minor**: an addition that existing files and readers can ignore, such as a new optional field or
  a new kind;
- **patch**: a change that does not change which files are valid, such as a description.

## 3.1.0 — 2026-11-27

- Product: two new kinds of identifier (TAX-68). `lvfs`: a GUID that a device reports itself to
  fwupd, as LVFS names it at the device, in lower case. `endoflife`: the name of a page of
  endoflife.date that describes this one product, as the API of endoflife.date names it. Each has a
  `source`, like every identifier. The schema refuses a value that does not have the form of its kind.
- A page of endoflife.date that lists the models of a line of devices names no product: its name is
  not an identifier of any of them.
- Existing files stay valid; readers that do not know the kinds can ignore them.

## 3.0.0 — 2026-11-25

- Product: `versions` takes one of two forms (rule TP1, TAX-67). Either `source`, the page where the
  versions are published, as before; or `published: false` with `updates`, the page where the
  publisher says itself that the product gets updates, for a product of which the publisher
  publishes no versions. `published: false` marks such a product, so a reader sees it without
  opening a source. A file holds exactly one of the two forms; the lint refuses a product with
  neither.
- When the publisher later publishes the versions, an ordinary change replaces `published` and
  `updates` with `source`.
- How a product is updated (over the air, by the driver, at a dealer) is not recorded: there is no
  field for it (decision of the product owner, 25 November 2026).
- Major: every existing file stays valid and unchanged, but `versions.source` is no longer present in
  every file, so a reader that expects it must read `versions.published` first.

## 2.1.0 — 2026-11-20

- Product: two new optional fields, `archived` and `resumed`, each with the `source` of the
  publisher that shows the change (rule TP10). `archived`: the publisher has ended the product; it
  gets no more updates. `resumed`: the publisher took an archived product up again. A file holds at
  most one of the two.
- Only a change that proposes it sets either field; nothing in TAXNMY archives a product by
  itself. An archived product keeps its key and its aliases.
- Existing files stay valid; readers that do not know the fields can ignore them.

## 2.0.0 — 2026-11-18

- Every source (`source`, `versions.source`, the `source` of an identifier, `machine_sources`,
  `second_source`) is `https` only; `http` is no longer accepted. The lint also refuses a source
  whose host is an IP address, `localhost` or a name inside a network, such as `.local` or
  `.internal` (rule TE1).
- `name` of a product and of a publisher: at most 100 characters; `aliases`: at most 130 characters
  each. Neither may hold a control character or `<` or `>`: a value from TAXNMY is plain text.
- Major, because files that were valid can become invalid. No file in TAXNMY is: on 18 November
  no source used `http`, an IP address or an internal name, the longest name had 45 characters,
  and no name or alias held one of the characters now refused.

## 1.3.0 — 2026-11-17

- Publisher: a new optional field `second_source`, a page the publisher does not write itself that
  names its domain (rule TV4). A new publisher is taken in without one; when it is given, it is
  recorded with the publisher, and code checks that it is not on the publisher's domain and that
  it names that domain.
- Existing publisher files stay valid; readers that do not know the field can ignore it.

## 1.2.0 — 2026-11-13

- Publisher: a new optional field `machine_sources`, a list of the sources the publisher makes for
  machines (an update catalog, AppStream metadata or CSAF provider metadata). Each entry has a
  `url`, a `format` and a `source`: the page of the publisher that names it.
- Existing publisher files stay valid; readers that do not know the field can ignore it.

## 1.1.0 — 2026-11-12

- Identifiers: two new codes, `winget` (a winget package identifier) and `homebrew` (a Homebrew
  cask or formula token), each with its own `source`. Package URL has no type for either.
- Like a purl, each points to one package and so to one update stream: a match on another product
  is a duplicate (TK3).

## 1.0.0 — 2026-11-12

First version.

- Product: `key`, `publisher`, `name`, `kind`, `source`, `versions.source`, `identifiers` and
  `aliases`.
- Kinds: `device`, `operating-system` and `software`.
- Identifiers: `cpe`, `purl` or `gtin`, one per entry, each with its own `source`.
- Publisher: `key`, `name`, `source` and `aliases`.
- No fields other than these are accepted.
