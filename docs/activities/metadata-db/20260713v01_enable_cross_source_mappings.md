---
name: 20260713v01_enable_cross_source_mappings
goal: Rework `column_mappings` so a source column can carry multiple, independently-named mappings into a target system, and so mappings are supported *within* a single system (between data sources/schemas), not only across systems. Add a `mapping_name` PK discriminator and a nullable `use_when` selection-guidance column (both mirroring `table_relationships`), drop the redundant `source_system` column and the `source_system <> target_system` CHECK, and enforce (not store) that each mapping row's `source_column_id` matches the folder it lives in. Schema change lands by editing `0001` and rebuilding the local Postgres; loader/tests/docs are updated against the new schema and verified end-to-end.
created: 2026-07-13 00:00:00
updated: 2026-07-13 00:00:00
---

## Implementation Plan

> Ordering rule: the schema edit (Task 1) defines the target shape; loader code + unit tests (Phase B) are mocked and land against it without a live DB; the corpus/example YAML (Phase C) are updated to carry `mapping_name`; then the DB is rebuilt and the corpus reloaded (Phase D, the synchronization point, mirroring `design-hardening` Task 6) before the integration test runs live; docs and review close it out. Suggested PR split: PR-A = Task 1 (schema, applied at Task 15); PR-B = Tasks 2–19 (code, YAML, tests, docs).

### Phase A — Schema

1. [completed] Rework `column_mappings` + `column_mappings_hstry` in the base schema - `code/apply_ddl/ddl/0001_initial_schema.sql`
   - 1.1. In `column_mappings`: drop the `source_system` column and its FK to `systems`; add `mapping_name text not null` and a nullable `use_when text` (selection guidance among multiple mappings for the same source column, mirroring `table_relationships.use_when`); change the primary key to `(source_column_id, target_system, mapping_name)`
   - 1.2. Remove `check (source_system <> target_system)` (within-system mappings are now permitted); keep `check (target_expression is not null or notes is not null)` and `check (validated = (validated_ts is not null))`
   - 1.3. In `column_mappings_hstry`: drop `source_system`, add `mapping_name text not null` and `use_when text`, change PK to `(source_column_id, target_system, mapping_name, end_ts)`
   - 1.4. Change `idx_column_mappings_target` (currently `on column_mappings(target_system, source_system)`) to `on column_mappings(target_system)`; keep `idx_column_mappings_source_column` (btree on `source_column_id`) and `idx_column_mappings_source_col_gist` (GiST on `source_column_id`)
   - 1.5. Update the header/inline comments for `column_mappings` to describe the new columns, the new PK, that `mapping_name` distinguishes multiple mappings per source column, and that source and target systems may now be equal
   - 1.6. Pre-launch bootstrap: this edits an already-applied migration; permitted while pre-launch (Key Decision #5). It changes `0001`'s checksum — the Task 15 rebuild re-applies fresh, so `ddl_versions` re-baselines the checksum (no manual reconcile needed)

### Phase B — Loader code + unit tests (mocked; no live DB)

2. [completed] Update the table registry and row dataclass - `code/load_metadata_db/data_model.py`
   - 2.1. `PRIMARY_KEY_COLUMNS["column_mappings"]` → `("source_column_id", "target_system", "mapping_name")`
   - 2.2. `CONTENT_COLUMNS["column_mappings"]`: remove `source_system`, add `mapping_name` and `use_when`
   - 2.3. `ColumnMappingRow`: remove the `source_system` field, add `mapping_name: str` and `use_when: str | None`; keep `target_tables_referenced`, `target_expression`, `notes`, `validated`, `update_reason`, and loader-managed `validated_ts`; update the docstring's PK line
   - 2.4. Update the `ColumnMappingKey` comment to `(source_column_id, target_system, mapping_name)` (still a 3-tuple alias)

3. [completed] Update shared test fixtures ahead of the per-module tests - `code/load_metadata_db/unit_tests/conftest.py`
   - 3.1. No change needed — `conftest.py` holds only mock cursor/conn fixtures; there are no shared `ColumnMappingRow`/corpus fixtures (they are constructed locally in each test file, handled in the per-module test tasks)

4. [completed] Update + run tests for data_model - `code/load_metadata_db/unit_tests/test_data_model.py`
   - 4.1. Assert `PRIMARY_KEY_COLUMNS["column_mappings"]` equals the new tuple; `mapping_name` ∈ `CONTENT_COLUMNS["column_mappings"]`, `source_system` ∉ it
   - 4.2. Assert `ColumnMappingRow` exposes `mapping_name`, has no `source_system`, and `validated_ts` is excluded from `CONTENT_COLUMNS`
   - 4.3. Run `uv run pytest code/load_metadata_db/unit_tests/test_data_model.py -v --cov=data_model --cov-report=term-missing`

5. [completed] Assemble `mapping_name` and enforce path-agreement - `code/load_metadata_db/corpus_assembly.py`
   - 5.1. `_assemble_column_mappings`: stop populating `source_system`; read a required `mapping_name` from each row body (missing/non-string → ValueError naming the path) and run `validate_identifier_segment(mapping_name, "mapping_name")`
   - 5.2. Add the path-agreement check: the first three dotted segments of `source_column_id` must equal `{ident.system}.{ident.database_name}.{ident.schema_name}`; on mismatch raise ValueError naming the offending `source_column_id` and the expected prefix
   - 5.3. Read an optional `use_when` from the row body (like `notes`); construct `ColumnMappingRow(source_column_id=..., target_system=ident.target_system, mapping_name=..., target_tables_referenced=(), target_expression=..., use_when=..., notes=..., validated=..., update_reason=...)`

6. [completed] Update + run tests for corpus_assembly - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
   - 6.1. Positive: a mapping row assembles with `mapping_name` and no `source_system`; the corpus key is `(source_column_id, target_system, mapping_name)`
   - 6.2. Negative: missing/blank `mapping_name` raises; a `mapping_name` containing an illegal ltree char (`$`, space, …) raises
   - 6.3. Negative: a `source_column_id` whose `{system}.{db}.{schema}` prefix ≠ the file's folder path raises (path-agreement); Positive: a matching prefix passes
   - 6.4. Run with coverage

7. [completed] Drop `source_system` checks from validation - `code/load_metadata_db/corpus_validation.py`
   - 7.1. `_check_references`: remove the `cm.source_system not in corpus.systems` check (source-side existence is guaranteed transitively by `source_column_id`'s FK to `columns`); keep the `target_system` and `source_column_id` existence checks
   - 7.2. `_check_within_row`: remove the `source_system == target_system` and `_system_of(source_column_id) != source_system` checks; keep the `target_expression is null → notes required` rule
   - 7.3. Leave `_check_sql_expressions` unchanged (`target_tables_referenced` derivation and all column-ref resolution are unaffected)

8. [completed] Update + run tests for corpus_validation - `code/load_metadata_db/unit_tests/test_corpus_validation.py`
   - 8.1. Remove the now-obsolete cases: `test_validate_corpus_column_mapping_unknown_source_system`, `test_validate_corpus_column_mapping_same_system`, `test_validate_corpus_column_mapping_source_prefix_mismatch`
   - 8.2. Add a positive: a within-system mapping (`source_system == target_system`, different data sources) validates cleanly
   - 8.3. Keep and adjust: null-expr-requires-notes, unknown-target-column, unknown-source-column (via `source_column_id` FK), and the case-mismatch hint
   - 8.4. Run with coverage

9. [completed] Update `column_mappings` SQL and param builders - `code/load_metadata_db/db_io.py`
   - 9.1. `_SELECT_COLUMN_MAPPINGS`: `SELECT source_column_id, target_system, mapping_name, target_tables_referenced::text[], target_expression, use_when, notes, validated, update_reason, validated_ts FROM column_mappings` (drop `source_system`, add `use_when`; keep the `::text[]` cast)
   - 9.2. `read_db_state` column_mappings loop: unpack the new column order and build `ColumnMappingRow(..., mapping_name=...)` with no `source_system`
   - 9.3. `_INSERT_COLUMN_MAPPINGS`: columns `(source_column_id, target_system, mapping_name, target_tables_referenced, target_expression, use_when, notes, validated, update_reason, validated_ts, insert_ts, update_ts)`; keep the `%s::ltree[]` cast and the `CASE WHEN %s THEN now() ELSE NULL END` validated_ts guard
   - 9.4. `_UPDATE_COLUMN_MAPPINGS`, `_DELETE_COLUMN_MAPPINGS`, `_HSTRY_INSERT_COLUMN_MAPPINGS`: WHERE clause → `source_column_id=%s AND target_system=%s AND mapping_name=%s` (order matching `PRIMARY_KEY_COLUMNS`); the `_UPDATE` SET clause adds `use_when`; the hstry column list + SELECT list drop `source_system` and add `mapping_name` + `use_when`
   - 9.5. `_insert_params` / `_update_params` column_mappings branches: drop `source_system`; add `mapping_name` (insert only — it's a PK) and `use_when` (both insert and update); `_pk_params` is generic (`key[0..2]`) and follows the new key order automatically — confirm each WHERE column order matches the PK tuple order

10. [completed] Update + run tests for db_io - `code/load_metadata_db/unit_tests/test_db_io.py`
    - 10.1. Update the column_mappings SELECT/INSERT/UPDATE/DELETE/HSTRY assertions for `mapping_name` and the absence of `source_system`
    - 10.2. Keep the `test_column_mappings_sql_casts_array_to_ltree` and `test_read_db_state_handles_null_target_tables_referenced` guards; assert the delete/hstry WHERE order is `(source_column_id, target_system, mapping_name)`; keep the `validated_ts` insert/hstry-carry assertions
    - 10.3. Run with coverage

11. [completed] Verify the generic diff engine and its tests need no change - `code/load_metadata_db/corpus_diff.py` (+ `unit_tests/test_corpus_diff.py`)
    - 11.1. Confirm `corpus_diff.py` is driven entirely by `PRIMARY_KEY_COLUMNS`/`CONTENT_COLUMNS` (no `column_mappings`-specific code), so the new PK is picked up automatically and a `mapping_name` change correctly surfaces as delete+insert
    - 11.2. Confirm `test_corpus_diff.py` constructs no `column_mappings`/`ColumnMappingRow` fixtures (verified: 0 references) — no edits needed; run it green as a regression check

12. [completed] Verify the orchestrator's derivation still aligns - `code/load_metadata_db/load_metadata_db.py`
    - 12.1. Confirm the `target_tables_referenced` derivation loop keys off `corpus.column_mappings` keys (via `pk()`) and `cm.target_system`, both intact under the new PK — no change expected; adjust only if a `source_system` reference surfaces
    - 12.2. No standalone test task — covered by `test_load_metadata_db.py` (advisory lock; unaffected) and the Phase D integration test

### Phase C — Corpus & example YAML

13. [completed] Add `mapping_name` to the sandbox mappings corpus - `data/systems/sandbox/pagila/public/mappings/sandbox_warehouse.yaml`
    - 13.1. Add `mapping_name: default` and `use_when: null` to each of the 11 rows (one mapping per source column → the conventional `default`, no alternative to choose between)
    - 13.2. Update the header comment block: `source_system` is no longer stored; add the `mapping_name` field row; note the path-agreement rule and that same-system mappings are now allowed
    - (Verification of these rows is the reload + integration assertions in Phase D)

14. [completed] Update the example mappings YAML - `readme/metadata-db-example-yamls/mappings/edw.yaml`
    - 14.1. Add `mapping_name: default` (and `use_when: null`) to each example row; add one extra row for an existing `source_column_id` with a different `mapping_name` and a non-null `use_when` — demonstrating both the discriminator and the selection guidance for multiple mappings per source column into the same target system
    - 14.2. Update the header schema table: drop the `source_system` line, add `mapping_name` (yaml field, composite-PK part) and `use_when` (yaml field, optional) lines, and update the PK note to `(source_column_id, target_system, mapping_name)`

### Phase D — Rebuild, reload, and end-to-end verification

15. [completed] Rebuild the local Postgres, apply `0001`, regrant, reload - maintainer-run, out-of-band
    - 15.1. As `metadata_db_maintainer` (lacks CREATEDB, so no DROP/CREATE DATABASE): `DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO public; REVOKE CREATE ON SCHEMA public FROM public;` (keeps the CI role DDL-less)
    - 15.2. `uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl.toml` — re-applies the edited `0001`; `ddl_versions` records the new checksum
    - 15.3. Re-run `code/apply_ddl/grant_metadata_db_ci.sql` as maintainer to restore the `metadata_db_ci` privilege model on the rebuilt tables
    - 15.4. Reload the corpus as the CI role: `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml` — expect a clean full-insert load
    - 15.5. Verify: `column_mappings` has `mapping_name` populated and no `source_system`; a within-system spot-check; `apply_ddl.py --check` clean; re-grant confirmed via a CI-role probe

16. [completed] Expand + run the integration test - `code/load_metadata_db/unit_tests/test_integration.py`
    - 16.1. Update all staged `column_mappings` fixtures to include `mapping_name` and drop `source_system`
    - 16.2. Add a **within-system** mapping (`source_system == target_system`, different data sources) and assert it loads and round-trips through `_hstry` on update
    - 16.3. Add a **one-source-column → two-mappings** case (same `source_column_id` + `target_system`, two `mapping_name`s) and assert both persist (no PK collision) and each carries its own `target_tables_referenced`
    - 16.4. Extend `test_pk_agreement` to assert the built schema's `column_mappings` PK is `(source_column_id, target_system, mapping_name)` and `source_system` is absent
    - 16.5. Run `METADATA_DB_INTEGRATION=1 uv run pytest -m integration code/load_metadata_db/unit_tests/test_integration.py -v` against the rebuilt DB, then the full unit suite at the 100%-coverage bar: `uv run pytest code/load_metadata_db/unit_tests -v --cov --cov-report=term-missing`

### Phase E — Documentation

17. [completed] Update the schema reference - `readme/metadata-db-overview.md`
    - 17.1. `column_mappings` column table: drop the `source_system` row, add a `mapping_name` row (PK part; the discriminator) and a `use_when` row (freeform; when to prefer this mapping over others for the same source column, mirroring `table_relationships.use_when`), update the PK note to `(source_column_id, target_system, mapping_name)`, and remove the `CHECK (source_system <> target_system)` note from `target_system`
    - 17.2. **Rewrite** the "Modeling cross-system mappings" subsection: the "Strictly cross-system — no within-system mappings" paragraph is now incorrect — replace it with the new model: mappings may be same-system; `mapping_name` distinguishes multiple mappings per `(source column, target system)`; the target's reach (multi-data-source / single-source / single-schema) is carried by the expression, not the identity; independent same-platform sources no longer need to be split into sibling systems
    - 17.3. Reconcile §2's "Cross-system column mappings" bullet with the new "may be within-system" reality (reframe as cross-*catalog* / drop the strict "one-directional across systems" wording as needed); note the path-agreement rule (a mapping row must live in the folder matching its `source_column_id`)

18. [completed] Update the maintenance doc - `readme/metadata-db-maintenance.md`
    - 18.1. "YAML files" table: keep `mappings/{target_system}.yaml`; note each row now carries a `mapping_name`, that a file's `{target_system}` may equal the source system, and that `source_system` is no longer authored/stored
    - 18.2. "Adding cross-system mappings" runbook step: add authoring `mapping_name` (use `default` for a single mapping; distinct names for multiple, with `use_when` documenting when to prefer each), the path-agreement requirement, and that within-system mappings are supported
    - 18.3. Loader validation-rules list (the `column_mappings` bullets under the CI/loader section): drop the `source_system` prefix/equality rules, add the `mapping_name` requirement and the `source_column_id`-matches-folder-path rule

### Phase F — Review

19. [completed] Code review of changed files and address findings - `docs/code_review/`
    - 19.1. Run `code-review-agent` against each changed code file (`0001_initial_schema.sql`, `data_model.py`, `corpus_assembly.py`, `corpus_validation.py`, `db_io.py`), writing `cr_*.md` per the existing `docs/code_review/` layout
    - 19.2. Address findings via `code-implementation-agent`; re-run the full suite at the 100%-coverage bar
    - 19.3. Mark each review's Status resolved when fixes land

## Key Data Decisions and Considerations

1. **`mapping_name` as the identity discriminator (mirrors `relationship_name`)** — the target of a mapping is an expression whose *reach* varies (multiple data sources, one data source, or one schema), so identity cannot be derived from the target's structure. An explicit per-row name is exactly how `table_relationships` already distinguishes multiple relationships between the same table pair (`(table_a_id, table_b_id, relationship_name)`). Applying the same pattern keeps the model uniform and lets one source column map into the same target system any number of ways.
2. **Drop `source_system`; keep `target_system`** — `source_system` is fully redundant (the leading label of `source_column_id`), its integrity is already guaranteed by `source_column_id`'s FK chain to `systems`, and its only non-redundant use was the removed cross-system CHECK. `target_system` stays: it is *not* derivable when `target_expression` is null (an intentional drop has no refs to infer from), and it is the mapping-file discriminator (`mappings/{target_system}.yaml`).
3. **Relax `source_system <> target_system` → allow within-system mappings** — this unblocks independent same-platform sources (e.g. `warehouse.OCS ↔ warehouse.SS`) without distorting the taxonomy into sibling systems. Cost: the CHECK was the structural fence against within-schema/within-data-source *transformations* (out of scope, owned by the pipeline tool). With it gone, that boundary becomes a CODEOWNERS/review concern. A narrower "no literal self-map" guard is possible but deferred — it does not cleanly express against an expression target and review covers the common case.
4. **Enforce path-agreement; do not store redundant path columns (option 1)** — `source_column_id` already contains `{system}.{db}.{schema}`, so the row already mirrors its file location. Rather than adding a redundant `source_schema`/`source_system` column (which would need its own consistency CHECK), add a loader validation that a mapping row's `source_column_id` prefix equals its folder path. This makes the folder authoritative with zero denormalization.
5. **Edit `0001` + rebuild vs. a new ALTER migration — RESOLVED: edit `0001` + rebuild (confirmed).** Precedent (`design-hardening` Key Decision #6, Note #4) is that while pre-launch — only `sandbox.pagila` + `sandbox_warehouse` exist, both reproducible from YAML — schema changes are made by editing `0001` and rebuilding, because `0001` *is* the initial schema until a real system lands. This plan follows that. (The rejected alternative was a `0002_column_mappings_mapping_name.sql` ALTER migration.) After the first real (non-sandbox) system lands, `0001` becomes immutable and all further schema changes go into new numbered diffs.
6. **Add `use_when` alongside `mapping_name`.** Now that a source column can carry multiple mappings into the same target system, a consumer must be able to choose among them — the same need `table_relationships` meets with `use_when` next to `relationship_name`. `use_when` is freeform, nullable (zero burden for the common single-mapping case), and semantically distinct from `notes` (general rationale) — it documents the *condition under which this mapping is preferred over the others for the same source column → target system*. Adding it now keeps `column_mappings` symmetric with `table_relationships` (name-discriminator + use_when) rather than retrofitting later.
7. **`mapping_name` convention** — use `default` for the common single-mapping-per-source-column case (matches `relationship_name`'s `default`); use meaningful names (e.g. `ss`, `xx`, per-target-source) only when a source column has more than one mapping into the same target system.
8. **Supersedes the just-added overview prose** — the "Strictly cross-system — no within-system mappings" subsection and the `source_system <> target_system` CHECK documentation added to `metadata-db-overview.md` earlier are reversed by this change (Task 17). Those edits are currently uncommitted; they will be rewritten rather than committed as-is.
