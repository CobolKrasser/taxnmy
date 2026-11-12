# Taxonomy changelog

Changes to the TAXNMY taxonomy: the schemas in `schema/` for products and publishers. Both schemas
carry the same version, in the field `version`.

The version follows [Semantic Versioning](https://semver.org/):

- **major**: a change that can make existing files or existing readers invalid, such as a field that
  is removed, renamed or becomes required, or a kind that is removed;
- **minor**: an addition that existing files and readers can ignore, such as a new optional field or
  a new kind;
- **patch**: a change that does not change which files are valid, such as a description.

## 1.0.0 — 2026-11-12

First version.

- Product: `key`, `publisher`, `name`, `kind`, `source`, `versions.source`, `identifiers` and
  `aliases`.
- Kinds: `device`, `operating-system` and `software`.
- Identifiers: `cpe`, `purl` or `gtin`, one per entry, each with its own `source`.
- Publisher: `key`, `name`, `source` and `aliases`.
- No fields other than these are accepted.
