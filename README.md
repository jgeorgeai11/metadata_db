# metadata_db

A queryable catalog of the datasets we do research on: their schemas, tables, and columns; how their tables join; how columns in one dataset translate to another; where each dataset is physically deployed; and a glossary of the business concepts the data represents.

It is meant to be the source of truth for anyone working with our data — a researcher orienting in an unfamiliar dataset, or a code-generating agent that needs to resolve a table to a real physical address before it can emit code.

## Why it exists

What we know about our data is fragmented — spread across data dictionaries, buried inside data-ops and project-team code, and held in individual people's heads. For an organization whose work is built on data, there is no consolidated, authoritative place to look. That is the gap this aims to fill.

A single authoritative source also buys consistency: when every project resolves the same joins, mappings, and definitions from the same place, work across teams stays in agreement instead of quietly diverging as each re-derives the logic on its own.

## The model in one idea

The catalog separates two kinds of facts:

- **Venue-independent** — what a dataset *is*: its schemas, tables, columns, joins, cross-dataset mappings, and concepts. True no matter where the data is hosted, and documented exactly once.
- **Venue-dependent** — *where* a dataset lives and *what it is physically called there*. This lives in exactly one table: `deployment_tables`.

So identifiers never contain a system name. A column is `{database}.{schema}.{table}.{column}` — for example `nppes.general.npi.entity_type_code` — and that id is stable no matter which, or how many, systems host the data.

That split is what makes code generation work. Generated code never contains catalog ids verbatim: a tool targeting a system resolves each table through its deployment row to that venue's physical names, and fails fast when the venue lacks the object. Translating a query between systems is the same operation against a different venue — mechanical renames come from deployments, genuine vocabulary differences come from column mappings, and the joins valid in that venue come from relationships.

## What it catalogs

| Object | What it holds |
|---|---|
| **Systems** | The queryable platforms data can live in (e.g. `warehouse`, `metadata_db`). A venue is a query context — defined by how data is addressed and queried, not by where the bytes sit, so one infrastructure can host more than one. A registry — not a level of the id hierarchy. |
| **Data sources** | The datasets we document, each under a globally unique catalog label (e.g. `nppes`). |
| **Schemas / Tables / Columns** | The structure of each data source, with descriptions, types, nullability, primary-key flags, and an optional pointer (`ref_table`) to the code set enumerating a column's values. |
| **Deployments** | Which system(s) each table is materialized in, and its physical database/schema/table names there. The single home of venue-dependent truth. |
| **Join relationships** | How tables join, with the join condition, a->b cardinality, and rationale. Where a join can *run* is derived from deployments. |
| **Column mappings** | For a column in one dataset, a SQL expression producing the equivalent value from another dataset's columns. |
| **Concepts** | A business glossary — what a thing *means* and how each dataset defines or identifies it. Looked up, never executed. |

## Reference code sets

One kind of data lives in this repo directly: small curated code sets (claim type codes, and similar) whose values are authored as CSVs under `data_ref/` and loaded as real typed tables into the `reference` schema of the same Postgres instance. They're documented in the catalog as an ordinary data source (`ref`), and any documented column can point at the set enumerating its values via `ref_table` — context retrieval for a human or agent resolving what a code means, never a runtime join. Because the values, their catalog docs, and their DDL all live here, a consistency gate holds all three equal — the one source where docs-vs-reality drift is mechanically preventable. See [MAINTAINING.md](MAINTAINING.md), *The ref reference schema*.

## How it works

The catalog is **authored as YAML in this repo** and loaded into Postgres. Git is the source of truth; the database is derived.

Every change goes through a pull request, and two things are guaranteed: nothing merges without passing the loader's validation — a broken reference, an unparsable SQL expression, or a missing description fails *before* merge — and every merge is loaded in a single transaction, or reverted, so `main` and the database never drift apart.

Today those guarantees are enforced by hand: authors run `check_corpus.py` before pushing, and a maintainer dry-runs, merges, and loads. Once a CI runner is registered, the committed workflows take over the same contract — pre-merge dry-run, post-merge load, automatic revert on a failed load. See [MAINTAINING.md](MAINTAINING.md) for the activation checklist.

The database is never edited by hand. Anything you want changed in the catalog, you change in YAML.

## Repo layout

```
data_catalog/          the catalog corpus — the YAML you edit
data_ref/              curated reference code-set values (CSV)
code/                  the loader, the DDL/migrations, the CI-side scripts
docs/                  activity plans, code reviews, data reviews
.github/workflows/     pre-merge checks and the post-merge load
```

## Querying it

The catalog is queried through the **`metadata_db` MCP server**, registered for MCP-capable agents in this repo's [`.mcp.json`](.mcp.json). It provides read-only access to everything in the catalog — what's documented in a data source, where a table physically lives, which concepts are about a column — backed by the `mcp_ro_metadata` role, which also sees the `_hstry` mirrors, `load_audit`, and the `reference` code-set schema (see [MAINTAINING.md](MAINTAINING.md)).

For humans there is also a **pgweb browser UI** (`code/pgweb/`) — the same read-only view of both schemas (via the `pgweb_ro` role), with table browsing, a SQL pane, and CSV/JSON export; connections are per-viewer and bookmark-only.

## Contributing

Adding a data source, documenting more of an existing one, correcting a description, recording a join or a mapping — all of it is a pull request against `data_catalog/`. **[CONTRIBUTING.md](CONTRIBUTING.md)** is the full guide: the authoring workflow, a walkthrough for adding a new data source, the field-by-field reference for every file type, and the validation rules your PR will be checked against.

## Documentation map

| Doc | For |
|---|---|
| **[CONTRIBUTING.md](CONTRIBUTING.md)** | Anyone expanding or refining the catalog — workflow, file formats, validation rules |
| **[MAINTAINING.md](MAINTAINING.md)** | Maintainers — database roles, migrations, CI, the loader, rebuilds, activation |
| **[SCHEMA.md](SCHEMA.md)** | The physical schema: design rationale, history tables, constraints, indexes |
