---
name: 20260723v01_migrate_loader_to_venue_free_model
goal: Migrate the load_metadata_db loader (all seven modules plus the CLI orchestrator and every unit test) from the system-rooted catalog model to the venue-free identity + deployments model. The spec (readme/metadata-db-overview.md, commit c53407c), the corpus (data/ restructure, commit d01ebb9), and the DDL (commit 9a93775, applied to the rebuilt prod schema) have already migrated — the loader is the last mover, so this activity ends with the restructured corpus loading into the rebuilt database and an idempotent 0/0/0 dry-run.
created: 2026-07-23 22:12:15
updated: 2026-07-23 23:59:00
---

## Implementation Plan

> Phases: identity & discovery (1), assembly (2), validation (3),
> diff / DB IO / orchestrator (4), tests (5), load & verify (6).
> Module tasks follow dependency order; the suite cannot go green
> mid-stream (fixtures encode the old model), so tests are migrated as
> one phase after the modules rather than interleaved.
>
> Out of scope, by explicit user decision: **code review** (run
> separately afterward) and the docs/examples/generator follow-ups
> (readme/metadata-db-maintenance.md, readme/metadata-db-example-yamls/,
> scripts/Convert-MetadataWorkbook.ps1) — see decision 10.
>
> Target model in one paragraph: ids are venue-free
> (`database.schema.table.column`; data_source_id is a single globally
> unique label). `data/systems.yaml` is a registry of venues;
> `data/sources/{label}/` is a flat tree; each data source carries a
> `deployments.yaml` stating which venues host it under what physical
> names. Relationships have no `system` column (validity = endpoints
> co-deployed somewhere); mappings have no `target_system` (PK is
> `(source_column_id, mapping_name)`). A new `deployments` table (PK
> `(table_id, system)`) holds the expanded table-grain residency facts.

### Phase 1 — Identity and discovery

1. [completed] Reshape the row dataclasses and corpus container - `code/load_metadata_db/data_model.py`
   - 1.1. `DataSourceRow`: PK `data_source_id` (single-label ltree), new required `owner`; drop `system` and `database_name`
   - 1.2. New `DeploymentRow`: PK `(table_id, system)`, plus `data_source_id`, `physical_database_name`, `physical_schema_name`, `physical_table_name`, `notes`, `update_reason` (loader-managed timestamps excluded, as on every row type)
   - 1.3. `TableRelationshipRow`: drop `system`
   - 1.4. `ColumnMappingRow`: drop `target_system`; PK becomes `(source_column_id, mapping_name)`
   - 1.5. `Corpus`: add `deployments` keyed by the composite PK; update the table registry/ordering (deployments after columns, before table_relationships) so diff/IO iterate parent-before-child
   - 1.6. `pk()` reflects every changed identity; docstrings updated to the 3-/4-segment id shapes

2. [completed] New path grammar and file types - `code/load_metadata_db/yaml_discovery.py`
   - 2.1. Recognize `data/systems.yaml` at the corpus root as the venue-registry file type (replaces per-system `system.yaml`)
   - 2.2. Sources tree is `data/sources/{label}/...` — one level shallower; `PathIdentity` drops `system` and `target_system` and carries the data-source label
   - 2.3. New data-source-level file type `deployments.yaml` (sibling of `data_source.yaml`)
   - 2.4. `mappings/{name}.yaml` filenames become grouping labels: the stem is charset-validated like any segment but is no longer decoded into a target system
   - 2.5. Path depths for schema-level files shift accordingly (`data/sources/{label}/{schema}/...`); classification issues still aggregate corpus-wide
   - 2.6. `validate_identifier_segment` is unchanged; docstrings and the module header describe the new grammar

3. [completed] 4-segment SQL references - `code/load_metadata_db/sql_parsing.py`
   - 3.1. `extract_column_refs` requires exactly 4 dotted segments (`database.schema.table.column`); the ambiguity rationale in the docstring updates from "system can't be determined" to "data source can't be determined"
   - 3.2. Segment-recovery internals and error messages updated to the new shape; case-preservation behavior unchanged
   - 3.3. `compute_target_tables_referenced` drops its target-system filter — it returns every table the expression references (there is no target_system to filter by; the DDL comment already describes the unfiltered semantics)

### Phase 2 — Assembly

4. [completed] Assemble the new shapes; expand deployments - `code/load_metadata_db/corpus_assembly.py`
   - 4.1. Venue registry: assemble `data/systems.yaml` (a list of system entries) into `SystemRow`s; duplicate system labels are aggregated issues
   - 4.2. `data_source.yaml`: `owner` is required (missing/blank is an issue); recognized-keys check updated for the new body
   - 4.3. Id builders lose the system segment throughout (`data_source_id` = label; `schema_id` = `{db}.{schema}`; etc.); the relationship anchor check compares `table_a_id`'s `{db}.{schema}` prefix to the authoring folder
   - 4.4. `deployments.yaml` assembly: parse venue entries — a bare entry means all schemas/tables under original names; an exhaustive `schemas:` map (schema → physical name, or a mapping with `name:` and an exhaustive `tables:` map of table → physical name) subsets and renames; explicit physical names required (null/`~` values rejected); each system at most once per file
   - 4.5. Expand sparse entries into explicit table-grain `DeploymentRow`s against the documented tables of the source, after tables assemble; physical names default to documented names at each level; unknown schema/table keys are aggregated issues
   - 4.6. Every data source must deploy somewhere: a missing `deployments.yaml`, or one that yields zero venues, is an aggregated issue (documentation of data that exists nowhere)
   - 4.7. Corpus-wide label rules: `data_source_id` globally unique and disjoint from `systems` names — both aggregated issues
   - 4.8. Mapping rows assemble without `target_system`; duplicate `(source_column_id, mapping_name)` within and across files is an aggregated issue; the path-agreement rule carries forward (each row's `source_column_id` must begin with the authoring file's `{db}.{schema}` prefix)
   - 4.9. Recognized-keys checks extend to the new bodies: registry entries (`system`, `description`, `notes`, `update_reason` — with the system label charset-validated, since it is body-derived rather than path-derived here) and deployments entries (`system`, `database_name`, `schemas`, `tables`/`name` within a schema mapping, `notes`, `update_reason`); the old optional body-`system` path-agreement check (`_check_body_system`) is retired with the field
   - 4.10. Reserved `concept` segment rules carry forward unchanged (table_name, schema-level shadowing, concept `name` itself), re-verified under the shifted id shapes ({database}[.{schema}].concept.{name})

### Phase 3 — Validation

5. [completed] Replace system-scoped validation with co-deployment rules - `code/load_metadata_db/corpus_validation.py`
   - 5.1. Relationships: drop the same-system within-row checks; require a non-empty venue intersection of the two endpoint tables' deployments ("runnable somewhere"); message names both endpoints and their venue sets
   - 5.2. Mapping expressions: referenced tables must be co-deployed in at least one venue (alongside the unchanged linkability floor); the target-system-membership check is retired, replaced by rejecting any expression reference back to the source column's own table (per the spec's "one coherent target" rule — a value trivially equivalent to itself is not a translation)
   - 5.3. Deployments referential rules: `system` values resolve against the registry; physical addresses (`system` + three physical names) unique across the catalog; physical names lowercase (same charset check as identifier segments, applied to physical name values)
   - 5.4. `use_when` multiplicity discipline moves to per-`source_column_id` grain (was per source column + target system)
   - 5.5. Concepts: `related_object_ids` entries resolve against data_sources / schemas / tables / columns / concepts only — `systems` is no longer a linkable id space
   - 5.6. SQL reference resolution and `_case_hint` operate on 4-segment refs; hint text updated
   - 5.7. `update_reason` discipline extends to deployments rows unchanged (insert null, update non-null)

### Phase 4 — Diff, DB IO, orchestrator

6. [completed] Diff the deployments table; changed PKs - `code/load_metadata_db/corpus_diff.py`
   - 6.1. Deployments participate in the diff keyed `(table_id, system)`; insert/update/delete semantics and `_hstry` handoff identical to every other table
   - 6.2. Changed PKs flow through: mappings keyed `(source_column_id, mapping_name)`; relationships unchanged key, smaller column set
   - 6.3. The mass-delete guard counts deployments rows like any other table

7. [completed] Read/write the new shapes - `code/load_metadata_db/db_io.py`
   - 7.1. `read_db_state` covers `deployments`; column lists and PK tuples updated for `data_sources` (owner, no system/database_name), `table_relationships` (no system), `column_mappings` (no target_system)
   - 7.2. `apply_diff` writes `deployments` and `deployments_hstry` in the registry order; bound-parameter maps updated; `validated_ts` handling untouched
   - 7.3. Log-line table counts include deployments (9 main tables)

8. [completed] Orchestrator currency - `code/load_metadata_db/load_metadata_db.py`
   - 8.1. No flow changes expected: verify the run sequence, docstrings, and log summaries reflect 9 tables and the new module contracts; `--reset-hstry` truncation list picks up `deployments_hstry` (confirm it derives from the registry rather than a literal list)

### Phase 5 — Tests

9. [completed] Registry, tree, and deployments discovery coverage - `code/load_metadata_db/unit_tests/test_yaml_discovery.py`
   - 9.1. New-grammar cases: `data/systems.yaml` classification; flat `data/sources/{label}/` decoding at both data-source and schema depths; `deployments.yaml` recognized; mapping filename stems accepted as labels (and rejected on charset violations); misplaced files (e.g. `deployments.yaml` at schema level, files under `data/systems/`) are classification issues
   - 9.2. Fixture sweep to the new paths/ids

10. [completed] Assembly and expansion coverage - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
    - 10.1. New behavior: registry assembly (incl. body-derived system-label charset and duplicate labels); owner required; bare-entry expansion (all schemas/tables, original names); exhaustive `schemas:`/`tables:` subset + rename expansion; null physical name rejected; duplicate system per file rejected; unknown schema/table keys and unrecognized entry keys aggregate; label uniqueness and label-vs-system disjointness; duplicate mapping identity; mapping path-agreement re-anchored to the `{db}.{schema}` prefix
    - 10.2. Fixture tree (`_build_corpus_tree`) rebuilt to the new layout, mirroring the shipped corpus; expected-count helpers re-derived
    - 10.3. All negative-shape parametrizations re-pointed at the new bodies

11. [completed] Validation coverage - `code/load_metadata_db/unit_tests/test_corpus_validation.py`
    - 11.1. New behavior: runnable-nowhere relationship rejected, co-deployed relationship accepted (incl. cross-source pairs sharing a venue only via deployments); mapping expression co-deployment; physical-address collision; uppercase physical name; concepts link to a system id rejected; per-source-column use_when multiplicity
    - 11.2. `_happy_corpus()` and all fixtures re-shaped (venue-free ids, deployments present so existing scenarios stay runnable-somewhere)

12. [completed] Diff coverage - `code/load_metadata_db/unit_tests/test_corpus_diff.py`
    - 12.1. Deployment insert/update/delete cases keyed `(table_id, system)`; mass-delete guard counts them; mapping composite-key cases re-keyed without target_system
    - 12.2. Fixture sweep

13. [completed] DB IO coverage - `code/load_metadata_db/unit_tests/test_db_io.py`
    - 13.1. Read/write/hstry cases for deployments; bound-parameter index constants re-verified against the new column sets; fixture sweep

14. [completed] Row-shape coverage - `code/load_metadata_db/unit_tests/test_data_model.py`
    - 14.1. `pk()` cases for the new identities (DeploymentRow composite, mapping two-part key); registry-order assertion includes deployments; fixture sweep

15. [completed] Parsing coverage - `code/load_metadata_db/unit_tests/test_sql_parsing.py`
    - 15.1. 4-segment acceptance, 3-and-5-segment rejection; fixture sweep (case-preservation and TokenError cases keep their intent)

16. [completed] Orchestrator coverage - `code/load_metadata_db/unit_tests/test_load_metadata_db.py`
    - 16.1. Staged-corpus fixtures re-shaped; reset-hstry table list assertion covers `deployments_hstry`; exit-code scenarios unchanged in intent

17. [completed] Integration + shared fixtures - `code/load_metadata_db/unit_tests/test_integration.py`, `code/load_metadata_db/unit_tests/conftest.py`
    - 17.1. Staged end-to-end corpus and DDL-apply flow updated to the new layout and 9-table schema; env-gated as today
    - 17.2. Full suite green at the established 100%-coverage bar

### Phase 6 — Load and verify (out-of-band, run against the already-rebuilt DB)

18. [completed] Load the restructured corpus and verify idempotence - loader run, no repo file
    - 18.1. `--dry-run` over `data/` reports all-inserts with zero issues (the corpus was authored to the spec; any surviving mismatch is a loader bug or a corpus typo — fix accordingly)
    - 18.2. Real run commits into the `prod` schema of the `metadata_db` database (per `code/load_metadata_db/config/load_metadata_db.toml`); row counts match the corpus (11 data_sources; deployments = documented tables × their venues, e.g. each PUF table appearing under both warehouse and edw)
    - 18.3. Post-load `--dry-run` reports 0 insert(s) / 0 update(s) / 0 delete(s)
    - 18.4. Spot checks as the CI role: venue-inventory query (distinct data_source_id by system), one name-resolution lookup, and the deployments UNIQUE constraint present

## Key Data Decisions and Considerations

1. **The loader is the last mover.** Spec (c53407c), corpus (d01ebb9), and DDL (9a93775, applied — the prod schema is rebuilt and empty) migrated first, so nothing runnable exists mid-activity: the suite and the dry-run both go green only when the loader lands whole. This is why tests are one phase rather than interleaved, and why phase 6 doubles as the activity's end-to-end validation.

2. **Deployments are authored sparse, stored expanded.** The YAML file states venue entries with defaults (bare = everything, exhaustive maps to subset/rename); the loader expands to explicit table-grain rows so the DB — and every consumer — holds facts, never defaulting rules. Expansion happens after tables assemble (it needs the documented table inventory); a new documented table automatically joins full deployments and deliberately does not join exhaustive-list deployments, mirroring how whole-schema vs. per-table copy jobs behave.

3. **Expanded rows inherit the venue entry's `notes`/`update_reason`.** A deployments file entry is the authoring grain; its expanded table-grain rows carry its values, and `update_reason` discipline applies to the expanded rows exactly as to any row (all-null on this first load, since every row inserts).

4. **Physical names are lowercase, validated as values.** Per the spec's case rules: same charset as identifier segments, but validated as field values (they are text, not ltree segments and not part of any id). The physical-address uniqueness rule is therefore plain equality; the DDL's UNIQUE constraint is the backstop, the loader check is the pre-merge gate.

5. **Single-segment namespaces are kept disjoint.** `data_source_id` and `systems.system` are the same shape, and `related_object_ids` resolution unions id spaces — so labels must not collide with system names (loader-enforced at assembly), and `systems` is removed from the concept-linkable spaces (venue registry is infrastructure, not data).

6. **Mapping identity loses its target dimension knowingly.** PK `(source_column_id, mapping_name)` means names carry the "toward what" meaning; the shipped corpus was already de-conflicted during the data restructure (d01ebb9: `ocs_line_rollup` rename + `use_when` added), so assembly's duplicate-identity check should pass it unchanged. `use_when` multiplicity discipline moves to the same grain.

7. **Relationship validity is derived, not stored.** No `system` column anywhere in the flow; the "runnable somewhere" check (non-empty venue intersection at table grain) is the load-time guarantee, and per-venue filtering is the consumer's code-generation-time concern — out of loader scope.

8. **`extract_column_refs` stays strict (exactly 4 segments).** Fewer segments are ambiguous (data source unknown); more means a stray system prefix survived the migration — both reject with the existing aggregated-issue machinery, which is also the cheapest detector of un-migrated references anywhere in corpus or fixtures.

9. **No `0002` migration; the DB was rebuilt.** The prod schema was dropped and re-created from the reworked `0001` before this activity (bootstrap-phase precedent, as in the lowercase migration). `ddl_versions` and grants are already in place; phase 5 only loads and verifies.

10. **Follow-ups deliberately excluded.** Code review runs separately after this activity (explicit user decision — no review task here). The maintenance readme, the example YAML corpus under `readme/metadata-db-example-yamls/` (plus its `sandbox_warehouse` sample), and `scripts/Convert-MetadataWorkbook.ps1` (which still emits the old layout and now must also emit `owner` and a `deployments.yaml`) migrate in a separate docs/examples activity — none of them gate the loader.

11. **Integration tests stay env-gated.** `test_integration.py` keeps its `METADATA_DB_INTEGRATION=1` + maintainer-credential gate; phase 6's manual verification against the real DB is the substitute the activity relies on, exactly as prior activities did.

12. **One corpus typo fixed during phase 6 (implementation note).** The dry-run surfaced a single pre-existing authoring defect the old loader would also have rejected: `data/sources/puf_hh_pps_lupa/general/table_relationships.yaml` carried a prose `join_condition` ("conceptual: when a period's visit count < …") between `lupa_threshold` and `per_visit_rate` — two tables that share no join key. Per the spec's structured-first boundary (§3.6), a `table_relationships` row must be a runnable boolean key-equality join, so this conceptual correspondence was moved to the concepts layer: the relationship entry was removed and a new data-source-level `data/sources/puf_hh_pps_lupa/concepts.yaml` (`lupa_payment` concept) now captures the LUPA payment rule with `related_object_ids` linking both tables and the columns the rule names. Final corpus totals: 3 systems, 11 data_sources, 26 tables, 165 columns, 39 deployments, 15 relationships, 12 mappings, 7 concepts (289 rows). Post-load dry-run is 0/0/0.
