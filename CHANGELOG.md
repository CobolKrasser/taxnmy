# Taxonomy changelog

Changes to the TAXNMY taxonomy: the schemas in `schema/` for products and publishers. Both schemas
carry the same version, in the field `version`.

The version follows [Semantic Versioning](https://semver.org/):

- **major**: a change that can make existing files or existing readers invalid, such as a field that
  is removed, renamed or becomes required, or a kind that is removed;
- **minor**: an addition that existing files and readers can ignore, such as a new optional field or
  a new kind;
- **patch**: a change that does not change which files are valid, such as a description.

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
  is a duplicate (TR3).

## 1.0.0 — 2026-11-12

First version.

- Product: `key`, `publisher`, `name`, `kind`, `source`, `versions.source`, `identifiers` and
  `aliases`.
- Kinds: `device`, `operating-system` and `software`.
- Identifiers: `cpe`, `purl` or `gtin`, one per entry, each with its own `source`.
- Publisher: `key`, `name`, `source` and `aliases`.
- No fields other than these are accepted.
