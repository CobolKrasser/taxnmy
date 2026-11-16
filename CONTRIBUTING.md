# Contributing to TAXNMY

You propose a product by opening a pull request that adds or changes data files. Every pull request
is checked by the lint (rules S1–S3), and then by the referee: code first, and an AI for what code
cannot settle (rules S4–S8). The rules are described in the [README](README.md#the-referee).

The referee leaves a comment on the pull request with one line per file: *accept*, *reject* (with
the rule), *alias* (an existing product written in another way) or *parked* (with the rule and the
reason). Parked means not taken in now, because the referee doubts or a source cannot be read; it
goes to the parking list `referee/parked.yaml` and is judged again later. A pull request from a fork
is judged by code only; what code cannot settle is parked. How it works:
[referee/README.md](referee/README.md#the-referee-on-a-pull-request).

## Structure

- **One file per publisher:** `publishers/<publisher>.yaml`
- **One file per product:** `products/<publisher>/<product>.yaml`

The file name is the key. `products/synology/ds224-plus.yaml` holds the product
`synology/ds224-plus`, and `publishers/synology.yaml` holds the publisher `synology`.

Both files are checked against the JSON schemas in `schema/`. Field names are in English.

### A publisher file

```yaml
key: synology
name: Synology Inc.                  # the name as the publisher writes it
source: https://www.synology.com/    # where the publisher uses this name
aliases: []
```

### A product file

```yaml
key: synology/ds224-plus
publisher: synology                  # the key of a file in publishers/
name: DS224+                         # the name as the source writes it
kind: device                         # device | operating-system | software
source: https://www.synology.com/en-global/products/DS224+
versions:
  source: https://www.synology.com/en-global/releaseNote/DSM
identifiers:                         # optional; each code with its own source
  - cpe: cpe:2.3:h:example:example
    source: https://...
  - purl: pkg:generic/example
    source: https://...
  - gtin: "0123456789012"
    source: https://...
  - winget: Example.App
    source: https://github.com/microsoft/winget-pkgs/tree/master/manifests/e/Example/App
  - homebrew: example
    source: https://formulae.brew.sh/cask/example
aliases: []
```

- `source` is the page where the name and kind of the product can be read.
- `versions.source` is the page where the versions of the product are published.
- Each identifier holds exactly one code (`cpe`, `purl`, `gtin`, `winget` or `homebrew`) and a `source`.
- A GTIN is written in quotes, so leading zeros are kept.
- A product has no parent field. Software does not need to name the operating system it runs on.

## How to propose a product

1. **Check that it does not exist yet.** Search `products/` for the key, the aliases and the codes.
2. **Check that it is a product of its own:** it has its own update stream, or it can be affected
   by a vulnerability on its own. A colour, region or packaging variant is not a product of its
   own.
3. **Find the publisher:** whoever publishes the updates. Add `publishers/<publisher>.yaml` if the
   file does not exist yet.
4. **Write the key** with the writing rule from the README, from the names as the source writes
   them. Do not shorten, translate or invent a name. If a name is longer than 40 characters, check
   that it is a name and not a description.
5. **Add the product file**, with a source URL for every value. Leave out any value you cannot
   source.
6. **Run the lint:**

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/python tools/lint.py
   ```

7. **Open a pull request.** Describe the product in one sentence and link the sources.

## What the lint reports

Each finding names the file, the line and the rule:

```
products/example/bad.yaml:5: S1: 'kind' is a required property
```

- An **error** rejects the pull request.
- A **signal** (a CPE or GTIN that another product also has, or a key with the same words as another key or alias of the same publisher in another order) does not reject it. The referee
  decides with S4 whether it is a product of its own.

## Changing the schema

The schemas in `schema/` are the taxonomy of TAXNMY, and they carry a version (the field `version`).
A pull request that changes a schema also raises that version in both schemas, and adds an entry to
[CHANGELOG.md](CHANGELOG.md):

- **major** when existing files or readers can become invalid;
- **minor** for an addition that existing files and readers can ignore;
- **patch** for a change that does not change which files are valid.

The tests check that both schemas carry the same version, and that the newest CHANGELOG entry
matches it.

## Renaming

A key never changes and is never reused. When a product is renamed, add a new product file and move
the old key to `aliases` of the new one.

## Licence

By contributing data you agree that it is published under [CC BY 4.0](LICENSE).
By contributing code you agree that it is published under the [MIT licence](LICENSE-CODE).
