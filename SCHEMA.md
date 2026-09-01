# metadata_db schema reference

The physical schema: what each table holds, why it's shaped that way, and the constraints and indexes the database enforces independently of the loader.

For *authoring* — which YAML fields to write and what the loader validates — see [CONTRIBUTING.md](CONTRIBUTING.md). For operations, see [MAINTAINING.md](MAINTAINING.md).

## Where the objects live

Every metadata_db object — the 9 main tables, their 9 `_hstry` mirrors, `load_audit`, `ddl_versions`, and the [`ltree`](https://www.postgresql.org/docs/current/ltree.html) extension — lives in a dedicated Postgres schema named **`catalog`**. The server must be **PostgreSQL 16 or newer**: the identifier charset permits hyphens in ltree labels, which ltree supports only from 16 — `0001_initial_schema.sql` asserts the version up front so a too-old server fails at apply time with a named requirement.

Objects are reached via the connection's `search_path`, so all SQL is written **schema-unqualified**: `select * from columns` resolves against `catalog` once `search_path` is set (`set search_path = catalog;`, or the `options=-c search_path=catalog` that the loader and `apply_ddl.py` apply automatically). The schema name is a config knob — the `schema` key alongside `database` in both `code/apply_ddl/config/apply_ddl_catalog.toml` and `code/load_catalog_data/config/load_catalog_data.toml` — so the SQL never hardcodes it, and `0001_initial_schema.sql` is schema-agnostic, building into whatever schema the config names.

## Two identity rules

**Identifier segments are lowercase-only.** Every segment composing an ltree id — the system label, the data source label, `schema_name`, `table_name`, `column_name`, `relationship_name`, `mapping_name`, a concept's `name` — must match `[a-z0-9_-]`, loader-enforced.

The reason is that hosting systems resolve *unquoted* identifiers case-insensitively (Postgres folds to lowercase, Snowflake to uppercase, SAS names are case-insensitive) while ltree ids are case-sensitive — so case variation would mint spurious distinct catalog ids for the same physical object.

The `physical_*_name` columns in `deployment_tables` are plain text rather than ltree segments, but follow the same rule: the lowercase spelling addresses the physical object regardless of how the venue displays it (the database a Snowflake console shows as `MUP_PHY` is authored as `mup_phy`), and one canonical spelling keeps the physical-address uniqueness check plain equality.

> **Documented assumption:** no cataloged object was created with a *quoted* mixed-case identifier — the one thing a lowercase spelling cannot address. None exists in any cataloged or anticipated system. If one appears, record its exact spelling in the owning data source's `notes`, quote it at emission, and revisit this rule.

**Catalog labels are identity, not addresses.** The data source label is globally unique (loader-enforced), chosen at authoring time; it *defaults* to the physical database name but need not equal it, and may not equal any `systems` name (also enforced — keeping the two single-segment namespaces disjoint keeps every single-segment id unambiguous).

That freedom resolves physical collisions. If the warehouse and lake platforms each have a database physically named `staging` holding unrelated data, the catalog holds two data sources under distinct labels — `warehouse_staging`, `lake_staging` — each with a deployment row recording `staging` as its physical name. Generated SQL is unaffected, since emission always resolves through the deployment row. Renaming a label is still an identity change (every id beneath it changes), so labels deserve care; what the design removes is any *venue-driven* reason to rename.

## The tables

Every authored main table carries the same three trailing columns: `update_reason` (freeform, why the row last changed — NULL exactly when `insert_ts = update_ts`), `insert_ts`, and `update_ts`. They're omitted from the tables below except where the behavior differs.

### `systems`
The registry of queryable platforms. Referenced only by `deployment_tables`; no other id or row contains a system name. A system is a query context — defined by how data is addressed and queried, not by where the bytes sit — so one infrastructure can host more than one (`warehouse` is the SAS datasets on the Warehouse platform today; a parquet store there would register as its own system).

| col | type | notes |
|---|---|---|
| system | ltree PK | e.g. `warehouse`, `metadata_db` |
| description | text NOT NULL | required — an undescribed venue tells a consumer nothing |
| notes | text | |

### `data_sources`
| col | type | notes |
|---|---|---|
| data_source_id | ltree PK | the catalog label, single segment, globally unique |
| owner | text NOT NULL | the team accountable for the documentation — queryable stewardship. Should agree with the source folder's CODEOWNERS entry once per-source routing lands (MAINTAINING.md, *Ownership routing*). Publisher provenance stays in `description` — who publishes and who stewards are different facts. |
| description | text NOT NULL | |
| notes | text | |

### `schemas`
| col | type | notes |
|---|---|---|
| schema_id | ltree PK | `{database}.{schema_name}` |
| data_source_id | ltree FK | -> `data_sources` |
| schema_name | text NOT NULL | `general` is the sentinel for schema-less systems |
| description | text NOT NULL | |
| notes | text | |

### `tables`
| col | type | notes |
|---|---|---|
| table_id | ltree PK | always 3 segments |
| schema_id | ltree FK | -> `schemas` |
| table_name | text NOT NULL | |
| description | text NOT NULL | |
| notes | text | |

### `columns`
| col | type | notes |
|---|---|---|
| column_id | ltree PK | always 4 segments |
| table_id | ltree FK | -> `tables` |
| column_name | text NOT NULL | |
| data_type | text NOT NULL | native type from the source system |
| is_nullable | bool NOT NULL | |
| is_primary_key | bool | default false. Records the table's **grain** as data — consumer knowledge, not an enforced constraint. |
| ref_table_id | ltree FK, nullable | -> `tables`. Names the table enumerating this column's value domain. **Context retrieval only:** no join path, no co-deployment semantics — cross-source pointers are expected. A reviewed assertion: the loader verifies the target exists, never that the column's values fall inside the enumerated codes. The catalog never touches instance data. Declared deferrable; see the backstops. |
| description | text NOT NULL | |
| notes | text | |

### `deployment_tables`
One row per (documented table, system). The single home of venue-dependent truth. Absence of a row **is** the "not deployed" fact — there are no tombstones.

A deployment asserts **table-level fidelity**: every documented column exists in the venue's copy under the documented name. Column names are the vocabulary of the data and never rename per venue; a copy with renamed or missing columns is a different dataset.

| col | type | notes |
|---|---|---|
| table_id | ltree | composite PK; -> `tables` |
| system | ltree | composite PK; -> `systems` |
| data_source_id | ltree NOT NULL FK | redundant with `table_id`'s leading segment (CHECK-enforced), stored for query convenience — venue-inventory queries read it directly instead of parsing the prefix |
| physical_database_name | text NOT NULL | lowercase |
| physical_schema_name | text NOT NULL | lowercase |
| physical_table_name | text NOT NULL | lowercase |
| insert_ts / update_ts | timestamptz | |

**This is a pure-facts table** — every column NOT NULL, no freeform columns at all. That's deliberate, and worth recording because the columns *used* to exist.

The primary reason they were dropped is a structural tension between the YAML and the table. `deployments.yaml` is authored sparse at *entry* grain (one entry per venue), while the table stores *table*-grain rows — so freeform columns could only be filled by inheritance, copying one entry-level string onto every expanded row. The two grains never agreed on what the columns meant. `notes` stored an entry-grain fact in a row-level column that silently promised a precision ("this is about *this table's* copy") the authoring grammar couldn't express. `update_reason` was contradictory outright — an inherited value colliding with per-row insert-null/update-non-null discipline — and keeping loads legal and idempotent required deployment-only special cases in the diff engine that every future maintainer would have to rediscover.

What made dropping them *safe*, secondarily, is that nothing of value was lost. `update_reason` was redundant with better records: deployment changes are the most mechanical, most diff-visible changes in the catalog, and their rationale is the git commit, joinable via `load_audit` — the same stance history takes on deletes. `notes` had a prose home already (a concept anchored under the source, or the data source's `notes`), and at the time of the drop no deployment note had ever been authored.

**If venue-copy caveats later prove important**, the recorded escalation path is, in order — each rung additive, each waiting on a demonstrated need the rung below can't meet:

1. **Concepts (available now).** A prose caveat anchored under the source, linking affected tables via `related_object_ids`. Zero schema changes; the venue is named in prose only, so caveats are readable but not venue-queryable.
2. **A table-grain `table_notes` sidecar.** A map on the venue entry (`{schema}.{table}` -> note) so a bare entry stays bare, plus a nullable `notes` column returning at honest table grain — for when caveats need to be structured and queryable.
3. **Coarser-grain siblings.** `deployment_dbs` / `deployment_schemas` as the authored-grain homes for entry- and schema-level facts — for when entry-grain facts themselves need to be data.

The table is named for its grain, leaving those sibling names free.

**Authored sparse, stored expanded.** The loader expands `deployments.yaml` defaults into fully explicit table-grain rows, so consumers query facts and never re-implement the defaulting rules. The defaulting semantics mirror how replication behaves: full-schema deployments automatically include newly documented tables (a whole-schema copy job copies them); exhaustive lists don't (the copy job must be updated, and so must the list).

### `table_relationships`
| col | type | notes |
|---|---|---|
| table_a_id | ltree FK | composite PK; the "left" side. **Anchors the file** — its `{database}.{schema}` prefix must equal the authoring folder. |
| table_b_id | ltree FK | composite PK; may be any documented table |
| relationship_name | text | composite PK. The **unordered** pair is also unique per name — a join documented in both orientations under one name is a conflict. |
| join_condition | text NOT NULL | portable SQL `ON` expression, fully-qualified refs |
| cardinality | text | nullable enum, read a->b. NULL means *not yet recorded* — never guessed. Because the unordered pair is unique per name, each pair has one stored orientation and the reverse reading is derivable by swapping the enum's sides. |
| use_when | text | required when a pair carries more than one relationship |
| validated | bool | default false — set when confirmed against real data in a hosting system. Verification runs against one venue; table-level deployment fidelity is what licenses carrying it to others. |
| validated_ts | timestamptz | loader-managed. Stamped when `validated` flips false->true, NULLed on true->false, preserved otherwise; also stamped at insert when a row arrives `validated: true`. A PK change is delete+insert, so the timestamp is **not** carried across an identity change. Non-null exactly when `validated` (CHECK). |
| notes | text | |

There is no `system` column: a relationship records join *logic*, and the venues where it runs are derived — the intersection of the endpoints' deployment sets. There is no `join_type`: inner vs. outer is a per-query analytical choice.

### `column_mappings`
| col | type | notes |
|---|---|---|
| source_column_id | ltree FK | composite PK; -> `columns` |
| mapping_name | text | composite PK. The only discriminator — names should say what the mapping is *toward*. |
| target_tables_referenced | ltree[] NOT NULL | loader-derived from the parse. Never NULL — an intentional drop stores the empty array. GiST-indexed (`gist__ltree_ops`), serving the lineage lookup `target_tables_referenced <@ 'x'::ltree`. The generic anyarray forms (`@> '{…}'::ltree[]`, `&&`) are correct but served by no GiST opclass, so they scan at catalog scale — prefer the ltree forms. |
| target_expression | text | portable **Postgres**. Postgres is the canonical dialect — the loader enforces it parses; runnability in a non-Postgres venue is not enforced. NULL marks an intentional drop and requires `notes` (CHECK). |
| use_when | text | required when a source column carries more than one mapping |
| validated / validated_ts | bool / timestamptz | same semantics as `table_relationships` |
| notes | text | |

There is no `target_system` column: the expression's own references identify the target dataset, and where the equivalent is computable is derived from those tables' deployments.

**Grain convention.** A `target_expression` is evaluated **per source row**, over the target rows corresponding to it — so the equivalent is produced at the source column's grain. The join and grouping that combine referenced tables are *not* in the expression: they come from the target's `table_relationships` plus the grain recorded by `columns.is_primary_key`, composed by the consumer. A scalar stays at the same row; an aggregate rolls finer target rows up to the source grain; a window returns one value per target row and fits only when the source is already at that grain. The loader does not verify grain alignment — that correctness stays with the consumer and the `validated` flag, exactly as `join_condition` correctness does.

### `concepts`
A lightweight business glossary — one row per concept, captured as freeform prose for a reader or tool to look up or RAG, **never parsed or executed**.

| col | type | notes |
|---|---|---|
| concept_id | ltree PK | path-derived: the file's path prefix + `.` + the body `name`, byte for byte. The reserved `concept` segment sits second-to-last, keeping the id self-describing and enabling subtree lookup (`{database}.*.concept.*`). |
| label | text | optional human title |
| definition | text NOT NULL | the lookup/RAG target; never parsed |
| notes | text | caveats *about the definition itself* — provenance, contested points |
| related_object_ids | ltree[] NOT NULL | authored links; empty array when none. Each must resolve to a `data_sources` / `schemas` / `tables` / `columns` / `concepts` id. `systems` rows are not linkable — concepts are about data; the venue registry is infrastructure. Order preserved; duplicates and self-references rejected. GiST-indexed for `related_object_ids <@ 'x'::ltree`. |

**The retrieval contract is a two-lookup union.** A concept does not repeat its own anchor in `related_object_ids`, so "which concepts are about object X?" is the union of two indexed lookups:

```sql
select concept_id from concepts where concept_id <@ '<object_id>'::ltree
union
select concept_id from concepts where related_object_ids <@ '<object_id>'::ltree;
```

The first leg returns concepts anchored at X (and anything under X); the second returns concepts linking to X from elsewhere.

Structured object links were originally left out on a drift objection — hand-maintained tags drift from the prose. That need arrived (deterministic retrieval anchors for RAG consumers), and the objection is answered by making the loader *verify* every link: an entry that no longer resolves fails the pre-merge dry-run like any broken reference. There is no `validated` flag — a definition is reviewed via PR, not asserted as a verified equivalence.

### `load_audit`
One row per successful **real** loader run (not dry-run), written inside the load transaction.

| col | type | notes |
|---|---|---|
| load_id | bigint PK | `GENERATED ALWAYS AS IDENTITY` |
| commit_sha | text NOT NULL | the git commit the corpus reflects (`$GITHUB_SHA` in CI, else `git rev-parse HEAD`) |
| inserts / updates / deletes | int NOT NULL | all 0 on an idempotent no-op run — the row is still written as a heartbeat |
| reset_hstry | bool | NOT NULL, default false |
| loaded_ts | timestamptz | `now()` — the transaction time |

**Lineage.** Because the whole load is one transaction, `loaded_ts` equals the `insert_ts`/`update_ts` stamped on every row that run wrote. So the commit that last touched a row is a timestamp join — no per-row commit column needed:

```sql
select c.column_id, la.commit_sha
from columns c join load_audit la on la.loaded_ts = c.update_ts;
```

**Drift detection.** Compare `git rev-parse origin/main` to `select commit_sha from load_audit order by loaded_ts desc limit 1` to see whether `main` is ahead of the database.

`load_audit` is not written from YAML and has no `_hstry` mirror. The schema itself is versioned separately by `ddl_versions` (`version`, `checksum`, `applied_ts`), created and maintained by `apply_ddl.py` — not part of the catalog.

### History tables (one per main table)

Each of the 9 main tables has a `_hstry` mirror holding **only superseded records**. Current state lives in the main table; prior states accumulate in history. Each mirrors its main table's shape and adds one column:

| col | type | notes |
|---|---|---|
| end_ts | timestamptz | when this version stopped being current |

PK is the main table's PK plus `end_ts`. Rows are never updated in history — only inserted.

The reason a version *ended* is not stored separately: it equals the `update_reason` of the *next* version of the same row. Walk forward by matching this row's `end_ts` to the next row's `update_ts` (or `insert_ts` for the original). For deletes there is no next version, and the rationale lives in the git commit that removed the row from YAML. `deployment_tables_hstry` relies on git the same way for *every* change — its rows carry no `update_reason` at all.

## Database-level backstops

The database re-enforces a subset of the loader's rules independently, guarding against any non-loader writer. The loader checks remain the pre-merge gate; these are the last line, not the authoring experience.

**Declared on every relevant table:** FKs on every parent reference — including the nullable `columns.ref_table_id`, which gets its own btree index and is declared `deferrable initially immediate` (like the physical-address key below) so a load can point a column at a `tables` row the same transaction inserts later; NOT NULL on every required field — all five `description`s, `definition`, `data_sources.owner`, both `ltree[]` columns, every column of `deployment_tables`, and every identity/structural field (PKs, FKs, leaf names, `data_type`, `is_nullable`, `is_primary_key`, `join_condition`, both `validated` flags, all timestamps, every `_hstry` mirror of those, each `end_ts`, and all of `load_audit`).

**CHECK constraints:**

- **Hierarchy consistency** (`schemas`/`tables`/`columns`) — the stored parent FK equals the id's leading labels: `data_source_id = subltree(schema_id, 0, 1)`, `schema_id = subltree(table_id, 0, 2)`, `table_id = subltree(column_id, 0, 3)`. Extends the principle already on `deployment_tables`.
- **Leaf-name redundancy** — `schema_name`/`table_name`/`column_name` equals the id's last label, so the stored name and the id cannot drift.
- **Lowercase identity** — every ltree id and every `physical_*_name` equals its own `lower()`. A case-variant manual insert would otherwise break plain-equality joins and the physical-address uniqueness key.
- **`update_reason` pairing** — `(update_reason is null) = (insert_ts = update_ts)`: a reason present exactly on updates.
- **`validated` pairing** — `validated = (validated_ts is not null)`.
- **`concept_id` shape** — `nlevel between 3 and 6` with the reserved `concept` segment second-to-last. The anchor's *existence* stays a loader check: a variable-depth prefix is not expressible as a CHECK.
- **cardinality enum**, **`target_expression`-or-`notes`**, and **`deployment_tables.data_source_id = subltree(table_id, 0, 1)`**.

These are main-table only — the `_hstry` mirrors deliberately gain none of them, since history legitimately holds superseded values.

**Uniqueness:** the `deployment_tables` physical-address constraint (`deployment_tables_physical_address_key`, declared `deferrable initially immediate` — statement-time for every writer, except that the loader defers it within its single transaction so a validated address swap between updated rows settles at commit); a UNIQUE index on `load_audit (loaded_ts)`, since both the lineage join and the `_hstry` correlation assume exactly one audit row per timestamp — the constraint turns that assumption into an enforced error rather than a silent join fan-out; and a UNIQUE expression index on `table_relationships (LEAST(a, b), GREATEST(a, b), relationship_name)` so both orientations of one pair under one name cannot coexist.

**Indexes:** btree FK indexes on `schemas.data_source_id`, `tables.schema_id`, `columns.table_id`, `columns.ref_table_id`, and `deployment_tables.data_source_id` (the composite `(system, data_source_id)` index does not lead with it); composite indexes on `deployment_tables (system, data_source_id)` and `table_relationships (table_b_id, table_a_id)`; GiST indexes on every hierarchical id column and the two `ltree[]` columns.

All of these fail the writing transaction at their check time — statement time for everything except the two constraints the loader defers inside its run (the physical-address UNIQUE and the `ref_table_id` FK, both re-checked at commit) — and the loader's single-transaction design rolls the whole run back either way.
