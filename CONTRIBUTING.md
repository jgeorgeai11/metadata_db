# Contributing to the metadata_db catalog

This guide is for anyone **expanding or refining the catalog** — adding a data source, documenting more tables and columns, recording a join or a cross-dataset mapping, writing a concept, or correcting something that's wrong.

You do all of it by editing YAML in `data_catalog/` and opening a pull request. That folder is the only thing you touch: never the database (no row is ever edited by hand), and not the rest of the repo either — the loader code, migrations, and CI workflows are maintainer territory (see [MAINTAINING.md](MAINTAINING.md)). The one exception is the curated reference code-set values in `data_ref/` — see [What belongs in the catalog](#what-belongs-in-the-catalog).

If you're maintaining the *system* rather than the *catalog* — migrations, database roles, CI, rebuilds — see [MAINTAINING.md](MAINTAINING.md) instead.

---

## Contents

- [The one rule that explains the rest](#the-one-rule-that-explains-the-rest)
- [Before you start](#before-you-start)
- [Where everything lives](#where-everything-lives)
  - [Shard folders](#shard-folders)
- [Naming and identity](#naming-and-identity)
- [What belongs in the catalog](#what-belongs-in-the-catalog)
- [The workflow](#the-workflow)
  - [Catalog changes](#catalog-changes) · [Reference-value changes](#reference-value-changes)
- [Recipes](#recipes)
  - [Add a data source](#add-a-data-source) · [Add a schema](#add-a-schema) · [Add tables](#add-tables) · [Add columns](#add-columns)
  - [Add or edit deployments](#add-or-edit-deployments) · [Add a system](#add-a-system)
  - [Add a join relationship](#add-a-join-relationship) · [Add a column mapping](#add-a-column-mapping) · [Add a concept](#add-a-concept)
  - [Modify a row](#modify-a-row) · [Remove something](#remove-something)
- [Choosing the right construct](#choosing-the-right-construct)
- [Field reference](#field-reference)
- [What gets validated](#what-gets-validated)
- [Troubleshooting](#troubleshooting)

---

## The one rule that explains the rest

**Git is the source of truth; the database is derived.**

Every row in Postgres came from a YAML file in this repo, loaded by a script after a merge to `main`. Nothing is inserted by hand, and nothing edited in the database survives — the next load computes its diff from the YAML and deletes anything the corpus doesn't describe.

So: to change the catalog, change the YAML. Everything below is a consequence of that.

---

## Before you start

You need a clone of this repo, permission to push a branch, and [uv](https://docs.astral.sh/uv/). Nothing else: the first `uv run` materializes the project's locked environment automatically — the pinned packages, and Python itself if the machine lacks it. There is nothing to install by hand.

**You do not need database access.** Nobody outside the maintainers has it, and you don't need it to contribute — almost every rule the loader enforces is a property of the YAML alone, and your pre-push check ([Catalog changes](#catalog-changes), step 3) runs entirely offline.

---

## Where everything lives

```
data_catalog/
├── systems.yaml                        # the venue registry (maintainer-owned)
└── sources/
    └── {data_source}/                  # one folder per data source; folder name = catalog label
        ├── data_source.yaml            # owner + description
        ├── deployments.yaml            # where this data is materialized
        ├── concepts.yaml               # (optional) source-level glossary
        └── {schema}/                   # one folder per schema; folder name = schema name
            ├── schema.yaml
            ├── tables.yaml
            ├── columns.yaml
            ├── table_relationships.yaml # (optional)
            ├── concepts.yaml           # (optional) schema-level glossary
            └── mappings/
                └── {target}.yaml       # (optional) mappings from this schema's columns
```

**Location is meaningful.** A file's path supplies part of the identity of every row in it — `data_catalog/sources/nppes/general/tables.yaml` means every row there is a table in `nppes.general`. You never write the data source or schema name in the body of those files; the folder says it.

### Shard folders

For a large schema (hundreds of tables, thousands of columns), any row-list file can be a folder of shards instead:

```
columns.yaml   ->   columns/
                   ├── bene.yaml
                   └── clm.yaml
```

Available for `tables/`, `columns/`, `table_relationships/`, and `concepts/` at a schema folder, plus `concepts/` at a data-source folder.

- **One form per type and scope** — a schema with both `columns.yaml` and `columns/` is rejected.
- Shard filenames are **grouping labels**, by convention the subject area. They're charset-validated but never decoded into identity, so which rows live in which shard is a convention enforced by review, not by the loader.
- Re-sharding is **identity-neutral**: moving rows between shards produces an empty loader diff.

`mappings/` is the pattern these folder forms generalize. Mappings are *always* a folder of files — there is no single-file form — with the same rules: stems are grouping labels (by convention the target dataset), never decoded, and moving rows between files changes nothing, since a mapping's identity comes from its body.

---

## Naming and identity

**Every identifier segment is lowercase** — `[a-z0-9_-]` only. That covers the data source label, schema name, table name, column name, relationship name, mapping name, concept name, system label, and every physical name in a `deployments.yaml`.

This is enforced, and the reason matters: hosting systems resolve unquoted identifiers case-insensitively (Postgres folds down, Snowflake folds up, SAS ignores case), while catalog ids are case-sensitive. Two spellings of one physical object would mint two catalog identities. Lowercase is the canonical spelling — Snowflake's `MUP_PHY` is cataloged as `mup_phy`.

**Ids are composed, not written.** You author the leaf name; the loader composes the id from the file's path:

| Object | Id shape | Example |
|---|---|---|
| data source | `{database}` | `nppes` |
| schema | `{database}.{schema}` | `nppes.general` |
| table | `{database}.{schema}.{table}` | `nppes.general.npi` |
| column | `{database}.{schema}.{table}.{column}` | `nppes.general.npi.entity_type_code` |
| concept | `{anchor}.concept.{leaf}` | `nppes.concept.provider_identity` |

References *between* objects (a relationship's endpoints, a mapping's source column, a concept's links) are written as full dotted ids.

**The catalog label is identity, not an address.** A data source's label defaults to the physical database name but doesn't have to equal it — physical names live in `deployments.yaml`. That's what resolves collisions: if the warehouse and lake platforms each have a database physically named `staging` holding unrelated data, they become two data sources labeled `warehouse_staging` and `lake_staging`, each with a deployment row recording `staging` as its physical name. Labels must also be distinct from every system name.

Renaming a label is an identity change — every id beneath it changes, which the loader sees as delete + insert. Choose carefully.

---

## What belongs in the catalog

**Shared, canonical data only.** The catalog documents data whose definition does *not* live in runnable code we control: native system sources (OCS, the EDW views — defined upstream) and externally published reference data.

**Team-produced datasets stay out** — derived tables, transfers between systems, working extracts — no matter how stable. Their authoritative definition is the pipeline code that builds them, which a code-generating agent reads directly. Documenting them here would only drift from that code.

> **Promotion valve:** when a team dataset becomes cross-team infrastructure, it has stopped being team data. It enters the catalog at that point, deliberately, with docs at catalog quality and a steward in `data_sources.owner`.

**No transformation logic.** The catalog records *structure* and *relationships*, never the SQL that produces a derived table. That stays with the tool that runs it.

**Copies of data need a decision.** When the same data is materialized in more than one place, two questions decide the treatment:

| | Faithful (same tables and columns) | Reshaped (pruned, renamed, regrained, filtered) |
|---|---|---|
| **Stable** (refreshed, reused) | A **deployment** — one dataset, extra rows in `deployments.yaml`. Every documented join becomes valid in the new venue automatically. | **Out of scope** as team data — or, if promoted to shared infrastructure, its **own data source**, with `column_mappings` recording lineage. |
| **Ad-hoc** (pulled for one analysis) | Not cataloged — analysis working data. Its columns are already documented on the source. | Not cataloged. |

A deployment asserts **table-level fidelity**: every deployed table carries every documented column under the documented name. If columns were renamed or dropped, it isn't a deployment — it's a different dataset.

Row-scope caveats ("2020 forward only", "nightly mirror, one day of lag") are real and worth recording, but `deployments.yaml` holds only physical facts. Put the caveat in a concept anchored under the source, or in the data source's `notes`.

**Curated reference code sets are a special case** — the one place a contribution reaches outside `data_catalog/`. Their *values* live in this repo too, as CSVs under `data_ref/`, loaded as typed tables into the catalog's own Postgres. How to change them — editing values versus adding or retiring a set — is covered in [Reference-value changes](#reference-value-changes); the catalog-side authoring (the `ref.codes.*` docs, and pointing a consuming column at a code set via `ref_table`) works exactly as described in this guide.

---

## The workflow

Every contribution moves through the same outer loop; only step 2 depends on what you're changing.

1. **Branch off `main`.**

   ```bash
   git checkout main && git pull
   git checkout -b docs/add-my-data-source
   ```

2. **Make and self-check your change** — follow the track for what you're editing:
   - [Catalog changes](#catalog-changes) (`data_catalog/` — the normal case)
   - [Reference-value changes](#reference-value-changes) (`data_ref/` — the one exception)

3. **Commit and push**, then open a PR against `main`.

4. **The change is validated before merge** — a dry-run against the live database, run by a maintainer today and by the pre-merge jobs automatically once CI is active. If it fails, fix and push again. For catalog changes, a clean offline check leaves only the two diff-time rules to fail here — see [What gets validated](#what-gets-validated); reference values have no offline check, so this dry-run is their first automated pass.

5. **Get review, then a maintainer merges.** CODEOWNERS auto-requests the right reviewers from the paths you touched; once the review is satisfied and the checks pass, a maintainer merges. (The routing rules and merge-method settings behind this are maintainer territory — see MAINTAINING.md.)

After merge, your change is loaded into Postgres in a single transaction — by a maintainer running the loader today, by the post-merge job once CI is active (reference values are the exception: their load is always manual). A failed load never strands the repo: the merge is reverted (by hand today, automatically under CI) so `main` and the database stay in sync — you then push fixes to your branch and open a new PR.

### Catalog changes

Step 2 for the normal case — three parts, whatever the recipe:

1. **Edit YAML under `data_catalog/`.** Every [recipe](#recipes) below is an instance of this step — each tells you which files to touch for one kind of change.

2. **Set `update_reason` on every row you changed.** A row whose content differs from what's in the database must carry a non-blank `update_reason` explaining the change, and brand-new rows get `update_reason: null` explicitly. Two exemptions: deployments (those rows carry no `update_reason` at all) and deletions (the rationale lives in your commit message — see [Remove something](#remove-something)).

3. **Run the offline corpus check** and fix anything it reports:

   ```bash
   uv run code/load_catalog_data/check_corpus.py
   ```

   It runs the loader's real discovery, assembly, and validation over `data_catalog/` with no database connection, and reports issues exactly as the loader does. Exit 0 means clean. While CI is dormant this is the only automated check your change gets before review — so run it. What it can and can't cover: [What gets validated](#what-gets-validated).

4. **Walk the conventions.** Every recipe closes with a numbered **Conventions** list, and [Recipes](#recipes) opens with the prose conventions that apply to every freeform field. These are the judgment calls the loader cannot check, which review otherwise has to catch. Go through the prose conventions plus each used recipe's list and fix what doesn't conform before pushing — an agent making the change should check off every item explicitly.

### Reference-value changes

Step 2 for the `data_ref/` exception ([why it exists](#what-belongs-in-the-catalog)). What you can do yourself turns on one question: **does the set's shape change?**

**Values only — do it directly.** Adding, correcting, or removing rows of an existing code set, with the CSV header exactly as documented:

1. **Edit the CSV** (`data_ref/codes/<table>.csv`). There is no `update_reason` here: PR review is the gate, and git history is the audit trail.

2. **Check it by eye** against the table's documented columns (`data_catalog/sources/ref/codes/columns/`). There is no offline checker for CSVs — `check_corpus.py` doesn't read them, and the real validation (the ref loader's dry-run) needs database access: a maintainer runs it today, the `validate_ref_data` job once CI is active.

**Anything touching shape or existence — involve a maintainer.** A new code set; a column added, renamed, retyped, or dropped; a set retired or renamed. All of these need a DDL migration landed in the same PR as the CSV and the catalog docs. Together is not just convention: ref validation holds docs == CSV == DDL in both directions, so a one-sided change — docs for a set with no values, values for an undocumented set — fails rather than merging quietly, and the corpus checks alone won't catch it (they validate YAML, never a table's physical existence). Two ways to start: open an issue describing the set (what it enumerates, its columns, where the values come from), or author the CSV and catalog docs on your branch and ask a maintainer to add the migration leg. The runbooks are in MAINTAINING.md, *The ref reference schema*.

**Either way, loading is manual.** Even once CI is active, a merged `data_ref/` change triggers no automatic load; a maintainer applies it deliberately. Expect a lag between your merge and the values appearing in the `reference` schema.

---

## Recipes

**The best reference is the corpus itself.** Every file under `data_catalog/` is a working, loader-valid example. When in doubt about a shape, open the nearest equivalent: `data_catalog/sources/ref/` is small enough to read end to end, and `data_catalog/sources/nppes/` and `data_catalog/sources/mup_phy/` show the other shapes — relationships, cross-source mappings, and concepts.

Each recipe closes with a numbered **Conventions** list — the review-held practices the loader can't enforce, written to be walked as a checklist ([Catalog changes](#catalog-changes), step 4). Everything the loader *does* enforce lives in [What gets validated](#what-gets-validated).

**Prose conventions — every freeform field (`description`, `notes`, `use_when`, `label`, `definition`):** the likeliest reader is an agent or RAG pipeline that retrieves your text as an isolated snippet, without the file around it. Write for that reader:

1. **Every field stands alone.** Use full dotted ids (`pagila.general.film.rental_rate`) — never "this column," "see above," or a bare leaf name that only resolves in file context.
2. **Be specific enough to answer a question.** "Payment amount is denormalized onto the rental row" retrieves and answers; "some fields are denormalized" retrieves and doesn't.
3. **Record durable facts about the data**, not operational status — "refresh is currently delayed" doesn't belong in the catalog.
4. **Expand abbreviations at first use within the field** ("NPI (National Provider Identifier)") — the retrieved snippet won't carry your team's context.

### Add a data source

The headline case. A new data source needs, **in one PR**: the folder, `data_source.yaml`, `deployments.yaml`, and at least one schema containing at least one documented table.

You create the folder yourself — it's just a path in your branch. Two pieces of this recipe are **maintainer-owned content**, though: whoever reviews your source's docs, these need a maintainer's sign-off.

- **The venue must already be registered.** Your `deployments.yaml` references a system by name, and that name has to resolve to a row in `data_catalog/systems.yaml`, which is maintainer-owned. If your data lives somewhere not yet registered, add the registry entry yourself, in the same PR ([Add a system](#add-a-system)) — validation resolves your reference against the branch, not the database, so nothing has to land first.
- **Review routing** — your source will eventually carry its own CODEOWNERS entry naming its steward team, and `.github/` is maintainer-owned, so that entry is a maintainer's edit. (Today a single catch-all routes everything, so there is nothing to add yet.)

**1. Create the folder and `data_source.yaml`:**

```yaml
# data_catalog/sources/pagila/data_source.yaml
owner: data-ops
description: Pagila — the PostgreSQL port of MySQL's Sakila sample database, modeling a fictional DVD-rental store.
notes: Standard open-source sample schema (https://github.com/devrimgunduz/pagila).
update_reason: null
```

**2. Declare where it lives** — every data source must deploy somewhere:

```yaml
# data_catalog/sources/pagila/deployments.yaml
- system: sandbox                    # bare entry: all schemas and tables, original names
- system: warehouse
  database_name: pg_mirror           # physical database name differs from the label
  schemas:
    general:
      name: pagila_general           # physical schema name differs
      tables:
        film: films                  # physical table name differs
```

See [Add or edit deployments](#add-or-edit-deployments) for the defaulting rules — they matter.

**3. Add at least one schema, with at least one table in it.** See the next three recipes. For a data source with no schema concept, use the literal `general` as the schema name.

A schema on its own is not enough. A venue entry expands against the *documented* inventory, so a source with no tables produces zero deployment rows and is rejected:

```
deployment entry for system 'sandbox' … expands to zero deployment rows —
the data source has no documented tables to deploy; document schemas/tables first
```

Columns aren't strictly required to pass validation, but a table with none documents nothing — add them in the same PR.

**Conventions:**

1. Choose the label for the long term. It defaults to the physical database name but need not equal it, and renaming later is an identity change to every id beneath it.
2. `owner` is the team accountable for *these docs* — queryable stewardship ("which sources does team X maintain?"). Who publishes the data (CMS, etc.) is different information and belongs in `description`.
3. `description` says what the data source is — its scope and provenance.
4. `notes` captures durable caveats about the source as a whole: provenance quirks, migration history, known divergences.

### Add a schema

```yaml
# data_catalog/sources/pagila/general/schema.yaml
description: The core rental-store schema — film catalog and rental transactions.
notes: null
update_reason: null
```

The folder name is the schema name. If the source's `deployments.yaml` uses exhaustive `schemas:` maps, a new schema is deployed **only** where you add it to those maps.

**Conventions:**

1. `description` says what the schema is and what it holds — its scope, not a restatement of its name.

### Add tables

```yaml
# data_catalog/sources/pagila/general/tables.yaml
- table_name: film
  description: One row per film title in the catalog.
  notes: null
  update_reason: null

- table_name: rental
  description: One row per rental transaction — a customer renting one film copy.
  notes: Payment amount is denormalized onto the rental row.
  update_reason: null
```

**Conventions:**

1. `description` says what a *row* is ("one row per rental transaction") — grain first, never a restatement of the table name.
2. `notes` is for structural surprises a consumer would trip on — denormalized amounts, wide repeated columns, segment layouts.

### Add columns

```yaml
# data_catalog/sources/pagila/general/columns.yaml
- table_name: film
  column_name: film_id
  data_type: integer
  is_nullable: false
  is_primary_key: true
  description: Surrogate key for the film.
  notes: null
  update_reason: null

- table_name: film
  column_name: rental_rate
  data_type: numeric(4,2)
  is_nullable: false
  description: List rental price per rental period.
  notes: The mart carries a realized average of pagila.general.film.rental_rate rather than the list price — see the stored_average mapping.
  update_reason: null
```

- `table_name` says which table in this schema the column belongs to.
- `is_primary_key` records the table's **grain** — consumer knowledge, not an enforced constraint; it's what lets someone derive the right grouping for an aggregate mapping.
- `ref_table` (optional) points at a documented table that **enumerates this column's value domain**, e.g. `ref_table: ref.codes.entity_type_cd`. It's for context retrieval only — no join path, no co-deployment requirement.

**Conventions:**

1. `data_type` is the native type exactly as the source system writes it (`numeric(4,2)`, `char(2)`). The one exception is the ref source, whose documented types are a machine contract with a fixed vocabulary — see MAINTAINING.md, *The ref reference schema*.
2. Flag **every** part of a composite key with `is_primary_key: true` — the loader doesn't verify grain completeness, and a half-flagged key misleads every consumer that derives a grouping from it.
3. Set `ref_table` on **every** column that carries a code set's values, not just the "main" one — context lookup should work from whichever column a consumer lands on. Never self-link a ref table's own key column.
4. `notes` records value quirks and encoding surprises. When it grows into teaching prose, that's a column-anchored concept, not a longer note.

### Add or edit deployments

One entry per venue the data is materialized in:

```yaml
- system: sandbox          # bare: every documented schema and table, original names
```

```yaml
- system: warehouse
  database_name: pg_mirror
  schemas:
    analytics: mart_analytics        # string form: rename the schema, all tables
    general:
      name: pagila_general           # map form: rename the schema…
      tables:
        film: films                  # …and rename or omit tables
```

**Maps are exhaustive.** If you write a `schemas:` map, a schema not listed is *not deployed* there. Same for `tables:`. That mirrors how replication actually behaves: a full-schema copy job picks up newly documented tables automatically; an explicit list doesn't, and neither does the map.

Rules the loader enforces: each venue appears at most once per file; physical names are explicit and lowercase; map keys must name documented schemas/tables of this source; every source must deploy somewhere; and no two catalog tables may claim the same physical address in a venue. A `tables: {}` empty map is rejected outright — it expands to zero rows.

Venue entries carry **only** residency facts — there is no `notes` or `update_reason` key.

**Conventions:**

1. Prefer the sparsest form that's true: a bare entry when the venue carries everything under original names; write a map only to rename or omit.
2. When you narrow a map, check what references the tables you drop. Because maps are exhaustive, a table omitted from *every* venue's map deploys nowhere — and that invalidates anything documented against it: relationships with that endpoint and mappings referencing it fail as runnable or computable nowhere, with errors surfacing on rows far from the file you edited.

### Add a system

Venues are maintainer-owned. Add to `data_catalog/systems.yaml`:

```yaml
- system: warehouse
  description: The analytics warehouse — curated, query-optimized copies for reporting.
  notes: Addressed as database.schema.table over SQL; each source's physical names live in its deployments.yaml map.
  update_reason: null
```

A system holds no folder and no data of its own; data sources reference it from their `deployments.yaml`.

**A system is a query context, not infrastructure.** It's defined by how data is addressed and queried there — so one platform can host more than one (Warehouse's SAS datasets are `warehouse`; a parquet store on the same platform would register as its own system, e.g. `warehouse_lake`), and "is this a new system?" turns on whether generated code would address the data differently, not on whether the bytes moved.

**Conventions:**

1. Name the query context, not the machine. Suffix the name with the context only when the platform actually hosts more than one; until then a platform keeps its bare name (`warehouse`, `metadata_db`). When a second context arrives, it registers suffixed (`warehouse_lake`) — and renaming the original then is cheap, since system names appear in no catalog id.
2. Use `notes` to record how physical addresses dereference in this venue (path layout, engine, libname convention) — the registry entry is where a consumer looks first.

### Add a join relationship

Authored in the schema folder of **`table_a`** — its `table_a_id` must be a table in that schema. `table_b_id` may be any documented table, in any source.

```yaml
# data_catalog/sources/pagila/general/table_relationships.yaml
- table_a_id: pagila.general.rental
  table_b_id: pagila.general.film
  relationship_name: default
  join_condition: pagila.general.rental.film_id = pagila.general.film.film_id
  cardinality: many_to_one
  use_when: null
  notes: Every rental is for exactly one film.
  validated: true
  update_reason: null
```

- `join_condition` is a boolean predicate — an `ON`-style expression using fully-qualified `database.schema.table.column` references. It may touch **only the two endpoints**, and when the endpoints differ it must reference both.
- It must be **deterministic** (no `now()`, `random()`, session context; an explicit `AT TIME ZONE '<zone>'` is fine) and **navigation-free** (no `SELECT`/`FROM`/`JOIN`/subquery/CTE/set-op). The relationship *is* the join.
- `cardinality` is the a->b row correspondence — `many_to_one` means many `table_a` rows match one `table_b` row.
- There is no `join_type`. Inner vs. outer is a per-query analytical choice.
- The two endpoints must be **co-deployed in at least one venue** — a join runnable nowhere is rejected.

**Conventions:**

1. Leave `cardinality` null until verified against real data — never guess; there's no default for a reason.
2. Set `validated: true` only after confirming the join against real data in a hosting venue.
3. Model a multi-hop path as several pairwise relationships — one row per hop, each with its own condition.
4. When a pair carries more than one relationship, write each `use_when` so a consumer can actually choose between them — the loader checks that it's present, not that it helps.
5. `notes` says why the join holds and what a consumer should know before trusting it ("Every rental is for exactly one film").

### Add a column mapping

A mapping says: *given this column, what expression computes the equivalent value from another dataset's columns?* Authored in the **source** schema's `mappings/` folder; the filename is a grouping convention, usually the target's label.

```yaml
# data_catalog/sources/pagila/general/mappings/mart.yaml
- source_column_id: pagila.general.film.rental_rate
  mapping_name: stored_average
  target_expression: mart.analytics.film_performance.avg_rental_rate
  use_when: Use the mart's precomputed average realized rate.
  notes: pagila.general.film.rental_rate is the list price; the mart's avg_rental_rate is the realized average — close but not identical; discounts diverge.
  validated: false
  update_reason: null

- source_column_id: pagila.general.film.length
  mapping_name: default
  target_expression: null            # intentional drop
  use_when: null
  notes: "No equivalent: the mart does not carry running time."
  validated: false
  update_reason: null
```

- `source_column_id`'s `{db}.{schema}` prefix must match the folder you're in. `mapping_name` is the only discriminator; when a source column carries more than one mapping, each needs `use_when`.
- `target_expression` is **portable Postgres**, a single value-producing expression over the target's own columns.
  - **Allowed:** column references, literals, operators, casts, scalar built-ins (`coalesce`, `split_part`, `date_trunc`…), conditionals (`CASE`, `NULLIF`), **aggregates** and conditional aggregation (`FILTER`, `SUM(CASE …)`) for cross-grain equivalence, and **window functions**.
  - **Forbidden:** navigation of any kind (`SELECT`/`FROM`/`JOIN`/subquery/CTE/set-op, any DML/DDL), volatile or context-dependent functions (`now()`, `random()`, `current_user`…), and any reference back to the source column's own table.
  - **Co-deployment applies to the expression's own tables, not to source-vs-target.** A single-table expression is fine as long as that table deploys *somewhere*; the source column's table and the target may sit in entirely different venues and the mapping is still valid (that's the point — it's a translation between datasets, not a join). A multi-table expression additionally needs its referenced tables **linkable** via documented relationships and **co-deployed with each other** in at least one venue.

**Conventions:**

1. Name the mapping for what it's *toward* (`nppes_npi`, `stored_average`) — `default` can't distinguish two mappings to different targets.
2. Map to the **canonical** column when a value is denormalized across the target — the referenced/"one" side of the FK graph — and let the copies be reached through `table_relationships`, whose join conditions already assert the equality. Nothing is lost: a consumer at the canonical column finds every copy by reading the joins that equate it.
3. **No equivalent? Say so.** `target_expression: null`, with `notes` opening with the literal prefix `No equivalent:` and then the rationale — why the value has no counterpart, and the route a consumer takes instead where one exists. Every existing drop in the corpus follows this shape. It's the right answer when the target replaced the value (natural key -> surrogate), doesn't carry it, or would need an as-of / date-band / non-key join — those are out of scope, and a clean-looking approximation is worse than an honest drop.
4. Never map a dataset to its own deployed copy — that's deployment resolution, not equivalence.
5. Set `validated: true` only after confirming the expression against real data, and write each `use_when` so a consumer can actually choose.
6. `notes` records how source and target semantics diverge ("list price vs. realized average; discounts diverge") — the caveat a consumer needs before substituting one for the other.

### Add a concept

A concept is a **freeform definition** for a human or a tool to look up — never parsed, never executed. Use it for knowledge that is genuinely definitional: what a thing means, how each dataset identifies it, an encoding lesson, a usage discipline.

Concepts are **path-anchored**. The body's `name` is the id *relative to the file's folder*, and you write the reserved `concept` segment yourself:

```yaml
# data_catalog/sources/pagila/general/concepts.yaml   (schema-level file)

- name: concept.film_identity                       # -> pagila.general.concept.film_identity
  label: Film identity
  definition: >-
    A film is identified by the surrogate pagila.general.film.film_id everywhere in
    pagila and in every venue copy; titles are display values and are not unique.
  notes: null
  related_object_ids:
    - pagila.general.film.film_id
    - pagila.general.film.title
  update_reason: null

- name: film.rental_rate.concept.rental_rate_pricing  # -> pagila.general.film.rental_rate.concept.…
  label: Rental rate pricing
  definition: >-
    pagila.general.film.rental_rate is the per-rental list price in effect today, not a historical charge…
  notes: null
  related_object_ids:
    - pagila.general.rental.payment_amount
  update_reason: null
```

- The anchor depths: source-wide -> `concept.{leaf}` in the source-level file; schema-wide -> `concept.{leaf}` in the schema file; one table -> `{table}.concept.{leaf}`; one column -> `{table}.{column}.concept.{leaf}`. Anchor segments before `concept` are legal **only in a schema-level file**, and the anchor must name a documented table/column — the loader checks it.
- `definition` is required. `related_object_ids` is optional; every entry must resolve to a real data source / schema / table / column / concept (systems are not linkable).
- There's no `validated` flag — a definition is reviewed via the PR, not asserted as runnable.

**Conventions:**

1. Anchor at the **narrowest** object whose scope covers what the concept teaches.
2. Not a second `description`: the description says what the column *is* and travels with it everywhere; a concept is teaching prose retrieved on its own. If a sentence belongs in every rendering of the column, it's a description.
3. The definition names its objects inline — `related_object_ids` are retrieval anchors that complement the prose, never replace it.
4. Don't repeat the concept's own anchor in `related_object_ids` — retrieval unions the anchor lookup with the links, so the anchor is already found.
5. For a schema holding many granular concepts, shard into a `concepts/` folder, by convention one shard per table.

### Modify a row

Edit the row in place and set `update_reason` ([Catalog changes](#catalog-changes), step 2).

Renaming is not modifying: a new leaf name is a new identity — the loader sees delete + insert, exactly as for a data source label — and every reference to the old id must move in the same PR.

**Conventions:**

1. `update_reason` says *why*, not what — the diff already shows what changed. The loader checks presence, not quality; the authoritative rationale is your commit message, joinable to the row via `load_audit`.

### Remove something

Delete the row, or the whole file/folder for a larger removal. The loader moves affected rows to their `_hstry` mirror, stamped with an `end_ts`. No `update_reason` is needed — the rationale lives in the commit that removed the YAML.

Deleting a lot at once trips the **mass-delete guard** (see [Troubleshooting](#troubleshooting)). That's deliberate: an intended decommission is rare enough to warrant a maintainer's sign-off.

---

## Choosing the right construct

The most common authoring question. **Structured first** — anything expressible as a mapping or a relationship must go there, where it's validated and queryable. Concepts are for what genuinely can't be structured.

| You want to record | Use | Because |
|---|---|---|
| These two tables join on equal keys | **table_relationship** | It's a runnable key-equality join. |
| This column's value can be computed from another dataset's columns | **column_mapping** | It's a deterministic value equivalence. |
| The same table exists in another venue under a different name | **deployment** | That's addressing, not equivalence. Never a mapping. |
| Two datasets identify the same thing differently, with no equal keys and no clean expression | **concept** | Not equal values (so not a mapping) and no join to run (so not a relationship). |
| What a code value means, or a filter discipline like "final action only" | **concept** | Definitional; nothing to execute. |
| A row-level crosswalk of which record here matches which record there | **none of these** | That's instance data, owned by ETL. The catalog documents structure and rules, never rows. |
| How a derived table is built | **none of these** | Transformation logic stays with the pipeline that runs it. |

---

## Field reference

This is the *authoring* contract — the keys as you write them in YAML. What they become in Postgres (composed ids, expanded deployment rows, loader-managed columns, constraints and indexes) is [SCHEMA.md](SCHEMA.md)'s side of the story.

Fields marked **required** must be present and non-blank. Optional freeform fields (`notes`, `use_when`, `update_reason`, `label`) must be either `null` or non-whitespace — a `""` or `"   "` is rejected, so stored rows never carry two spellings of "absent".

### `systems.yaml` (corpus root)
| field | required | notes |
|---|---|---|
| `system` | yes | the venue label, single lowercase segment |
| `description` | yes | what this system is |
| `notes` | | how addresses dereference in this venue |
| `update_reason` | | null on insert; required on change |

### `data_source.yaml`
| field | required | notes |
|---|---|---|
| `owner` | yes | the steward team accountable for these docs |
| `description` | yes | what the data source is, its scope and provenance |
| `notes` | | caveats, migration history, known divergences |
| `update_reason` | | |

### `deployments.yaml` — list of venue entries
| field | required | notes |
|---|---|---|
| `system` | yes | must exist in `systems.yaml`; at most once per file |
| `database_name` | | physical database name if it differs from the label |
| `schemas` | | exhaustive map; omit for "all schemas, original names" |

No `notes`, no `update_reason` — residency facts only.

### `schema.yaml`
| field | required | notes |
|---|---|---|
| `description` | yes | what this schema is |
| `notes` / `update_reason` | | |

### `tables.yaml`
| field | required | notes |
|---|---|---|
| `table_name` | yes | leaf name; the folder supplies the rest of the id |
| `description` | yes | ideally says what one row is |
| `notes` / `update_reason` | | |

### `columns.yaml`
| field | required | notes |
|---|---|---|
| `table_name` | yes | which table in this schema |
| `column_name` | yes | |
| `data_type` | yes | native type from the source system |
| `is_nullable` | yes | |
| `is_primary_key` | | default `false`; flag every part of a composite key |
| `ref_table` | | 3-segment id of the table enumerating this column's value domain |
| `description` | yes | |
| `notes` / `update_reason` | | |

### `table_relationships.yaml`
| field | required | notes |
|---|---|---|
| `table_a_id` | yes | must be in this schema folder |
| `table_b_id` | yes | any documented table |
| `relationship_name` | yes | part of the identity |
| `join_condition` | yes | boolean predicate, fully-qualified refs |
| `cardinality` | | `one_to_one` / `one_to_many` / `many_to_one` / `many_to_many`, or null |
| `use_when` | conditional | required when the pair has more than one relationship |
| `validated` | | `true` once confirmed against real data |
| `notes` / `update_reason` | | |

### `mappings/*.yaml`
| field | required | notes |
|---|---|---|
| `source_column_id` | yes | prefix must match this folder |
| `mapping_name` | yes | name it for what it maps *toward* |
| `target_expression` | yes, or null | null = intentional drop, then `notes` is required (convention: open with `No equivalent:`) |
| `use_when` | conditional | required when the source column has more than one mapping |
| `validated` | | `true` once confirmed against real data |
| `notes` / `update_reason` | | |

### `concepts.yaml`
| field | required | notes |
|---|---|---|
| `name` | yes | id relative to the folder; `concept` segment second-to-last |
| `definition` | yes | freeform prose — the lookup/RAG target |
| `label` | | human-readable title |
| `related_object_ids` | | list of ids that must resolve; systems not linkable |
| `notes` / `update_reason` | | |

Loader-managed fields you never author: `insert_ts`, `update_ts`, `validated_ts`, `target_tables_referenced`, and every id composed from a path.

---

## What gets validated

These are the **loader's** rules, not CI's. The same code enforces them wherever it runs — `check_corpus.py` on your machine, the pre-merge job once CI is switched on, and the post-merge load. CI adds no checks of its own; it just runs the loader against the live database.

Validation runs in three waves; a later wave only runs once the earlier one passes, so one bad row never cascades into phantom errors. Nearly everything **accumulates** — you get every violation in the corpus in one report, not one at a time.

Waves 1 and 2 — rules 1–19, where authoring mistakes actually land — need only the YAML, so `check_corpus.py` checks them in full locally. Wave 3's two diff-time rules compare your corpus against live rows, so they run only with database access — the maintainer's dry-run today, the pre-merge job once CI is active. A clean local check means the dry-run can only fail on those two.

**Wave 1 — file shape**

1. **Recognized locations and extensions** — Files sit at recognized locations; a `tables/`, `columns/`, or `table_relationships/` folder directly under a data source is misplaced (only `concepts/` is valid there). Corpus files carry the lowercase `.yaml` extension: a recognized file spelled `.yml` or `.YAML` is an error, never a silent skip — a skipped file would read as delete-by-absence and remove its previously loaded rows.
2. **Lowercase identifiers** — Every id segment is lowercase `[a-z0-9_-]` and ≤ 255 characters.
3. **Well-formed YAML** — YAML is readable and well-formed; duplicate keys are rejected, never last-value-wins.
4. **File shape and recognized keys** — Each file type has the right shape and only recognized keys — a misspelled key is rejected by name. Required fields present and non-blank; optional freeform fields null-or-non-whitespace.
5. **Reserved segments** — nothing may be named `concept`; concept names carry `concept` exactly once, second-to-last; `mappings` is valid only as a schema's subfolder; the four shard-folder names are reserved as schema names.
6. **Folder anchoring** — a relationship's `table_a_id` and a mapping's `source_column_id` must carry the authoring folder's prefix.
7. **Deployment file rules** — one entry per venue, explicit lowercase physical names, exhaustive maps naming documented objects, at least one venue per source.
8. **Primary-key and label uniqueness** — no duplicate PKs within or across files; data source labels globally unique and disjoint from system names.

**Wave 2 — corpus validation**

9. **Every reference resolves** — relationship endpoints, mapping source columns, `ref_table`, deployment systems, `related_object_ids`, and every concept's anchor prefix. Unresolved references get a "did you mean…? (case mismatch)" hint where one exists.
10. **Identifier-segment syntax** — re-validated over `table_name`, `column_name`, `relationship_name`, `mapping_name`, and every dotted segment of the derived `concept_id`. (Physical names are wave-1 only: they're text values, not ltree segments.)
11. **Runnable somewhere** — a relationship's endpoints must be co-deployed in at least one venue.
12. **Physical-address uniqueness** — across the whole catalog.
13. **Cardinality vocabulary** — `cardinality` is one of the four values (case-sensitive) when present.
14. **Endpoint-pair uniqueness** — The unordered endpoint pair is unique per `relationship_name`; multi-relationship pairs all set `use_when`.
15. **Multi-mapping use_when** — Multi-mapping source columns all set `use_when`.
16. **Intentional drops carry notes** — An intentional drop (`target_expression: null`) carries `notes`.
17. **SQL parses and resolves** — Every `join_condition` and `target_expression` parses as Postgres, and every column reference is four dotted segments resolving to a documented column.
18. **Join-condition shape** — boolean predicate, no navigation, deterministic, at least one column, endpoints only, both endpoints referenced.
19. **Target-expression shape** — single value-producing expression, no navigation, deterministic, at least one column, not referencing the source's own table. The referenced tables must deploy somewhere (a target excluded from every venue map is "computable nowhere"), and a multi-table expression's tables must additionally be linkable via relationships and co-deployed **with each other**. Source and target need not share a venue.

**Wave 3 — diff-time**

20. **update_reason discipline** — `update_reason` non-blank on every changed row, null on inserts. Applies to the eight authored tables; deployments are exempt.
21. **Mass-delete guard** — the run refuses when the diff would delete more than a quarter of current rows once at least 20 are being deleted. Overridable only by a maintainer.

The database re-enforces a subset independently — foreign keys, NOT NULLs, the cardinality enum, lowercase ids, and more — as a backstop against any non-loader writer. See [the schema reference](SCHEMA.md) for that layer.

---

## Troubleshooting

**"Classification issue" / file not recognized.** The file is in a location the loader doesn't recognize, or a shard folder sits where it isn't valid. Check it against [Where everything lives](#where-everything-lives).

**"expected lowercase"** — an identifier has uppercase. The error names the lowercased form to use.

**"unresolved reference"** — an id you wrote doesn't exist in the corpus. Note it validates against *the corpus*, not the database, so a reference to something added in the same PR is fine. Check for a case mismatch; the error hints when one is likely.

**"runnable nowhere"** — a relationship's two endpoint tables share no venue. Either a deployment is missing, or the two tables genuinely never co-exist — in which case the correspondence is a concept, not a join.

**"update_reason required"** — you changed a row's content without saying why. Add the reason; use `null` only on brand-new rows.

**Mass-delete guard tripped** — your diff deletes a large fraction of the catalog. Usually a wrong path, a half-finished rename, or a bad merge. If the deletion really is intended, a maintainer runs the load with the override.

**Target expression rejected** — most often navigation (a `JOIN` or subquery that belongs in a relationship), a volatile function, or a reference back to the source column's own table.

If `check_corpus.py` passes but the loader dry-run fails — the maintainer's today, CI's once active — it will be one of the two diff-time rules — `update_reason` or the mass-delete guard — since those are the only checks the local run can't perform. Both are described above.
