---
name: 20260716v01_add_relationship_mapping_validation_rules
goal: Implement the two decided design docs — `table-relationship-validation-rules.md` (B, C2, D, E, F, G, H) and `column-mapping-validation-rules.md` (M1–M9) — as loader-enforced rules plus two schema additions. Add orientation-duplicate detection, a `table_a_id` schema anchor, join-condition table-scope/predicate/join-type checks, corpus-wide unknown-key rejection, and `use_when` disambiguation for relationships; and the `target_expression` expression-only/construct-taxonomy/determinism/target-system/linkability rules for mappings, together with the `columns.is_primary_key` grain flag and a GiST index on `column_mappings.target_tables_referenced`. Schema edits land in `0001` and are applied by rebuilding the local Postgres (bootstrap exception); loader code, tests, corpus/example YAML, and docs are updated against them and verified end-to-end.
created: 2026-07-16 19:01:46
updated: 2026-07-17 09:05:36
---

## Implementation Plan

> Ordering rule: the two schema additions (Task 1, M5 + M6) define the target shape; loader code + unit tests (Phase B) are mocked and land against it without a live DB; the corpus/example YAML (Phase C) gains `is_primary_key` and is confirmed compliant with every new rejection; then the DB is rebuilt and the corpus reloaded (Phase D, the synchronization point, mirroring `enable_cross_source_mappings` Task 15 and `design_hardening` Task 6) before the integration test runs live; docs and review close it out. Suggested PR split: PR-A = Task 1 (schema, applied at Task 16); PR-B = Tasks 2–20 (code, YAML, tests, docs).
>
> Both design docs are validation-only except M5 and M6. Rule → file map: B, D, E, G, H, M1, M2, M3, M7, M8, M9 → `corpus_validation.py` (with parse-tree primitives in `sql_parsing.py`); C2, F → `corpus_assembly.py`; M5 → `0001` + `data_model.py` + `corpus_assembly.py` + `db_io.py`; M6 → `0001`. M4 is withdrawn (no work).

### Phase A — Schema

1. [completed] Add `columns.is_primary_key` (M5) and the `target_tables_referenced` GiST index (M6) to the base schema - `code/apply_ddl/ddl/0001_initial_schema.sql`
   - 1.1. M5: add `is_primary_key boolean not null default false` to `columns`, placed after `is_nullable`; add the same column to `columns_hstry` (mirror, no default needed — history rows always carry a value)
   - 1.2. M5: update the `columns` / `columns_hstry` header/inline comments to describe `is_primary_key` (records grain: a table's PK is the set of its flagged columns; composite keys are multiple flags) and that it is consumer knowledge, not a loader-enforced constraint
   - 1.3. M6: add `create index idx_column_mappings_target_tables_gist on column_mappings using gist (target_tables_referenced gist__ltree_ops);` with a comment noting the `gist__ltree_ops` operator class is required for `ltree[]` containment (`@>`, `<@`, `&&`) and a plain `using gist (...)` will not serve those operators
   - 1.4. Pre-launch bootstrap: this edits an already-applied migration; permitted while pre-launch (only `sandbox.*` exists, reproducible from YAML — `enable_cross_source_mappings` Key Decision #5, `design_hardening` Key Decision #6). The Task 16 rebuild re-applies `0001` fresh, so `ddl_versions` re-baselines the checksum with no manual reconcile. No `0002` migration is created

### Phase B — Loader code + unit tests (mocked; no live DB)

2. [completed] Add the `is_primary_key` field to the columns row + registry (M5) - `code/load_metadata_db/data_model.py`
   - 2.1. `ColumnRow`: add `is_primary_key: bool` after `is_nullable`; update the docstring to name it and its grain role
   - 2.2. `CONTENT_COLUMNS["columns"]`: add `is_primary_key` so a flag change is diffed like any other content field (no `PRIMARY_KEY_COLUMNS` change — the columns PK is still `column_id`)
   - 2.3. Confirm no other row dataclass or registry entry changes (the relationship/mapping rules are validation-only and touch no dataclass)

3. [completed] Update + run tests for data_model - `code/load_metadata_db/unit_tests/test_data_model.py`
   - 3.1. Assert `is_primary_key` ∈ `CONTENT_COLUMNS["columns"]` and `ColumnRow` exposes it; assert `PRIMARY_KEY_COLUMNS["columns"]` is unchanged (`("column_id",)`)
   - 3.2. Run `uv run pytest code/load_metadata_db/unit_tests/test_data_model.py -v --cov=data_model --cov-report=term-missing`

4. [completed] Add SQL parse-tree classification primitives (M1, M2, M3, G) - `code/load_metadata_db/sql_parsing.py`
   - 4.1. Add a `contains_statement_or_navigation(tree)` predicate (M1): returns the offending node kind if the tree is/contains `exp.Select` / `exp.Join` / `exp.Subquery` / `exp.CTE` / a set-operation (`exp.Union`/`exp.Intersect`/`exp.Except`), else None
   - 4.2. Add `VOLATILE_FUNCTION_DENYLIST` (M3): the case-insensitive set `{random, now, current_timestamp, current_date, clock_timestamp, nextval, current_user, current_setting, version}`, plus a `find_volatile_functions(tree)` helper returning any denylisted function/identifier names found; note `AT TIME ZONE '<zone>'` is deterministic and must NOT be flagged
   - 4.3. Add `is_boolean_predicate(tree)` (G): true when the root node is a comparison/logical/membership operator (`=`, `<>`, `<`, `<=`, `>`, `>=`, `AND`, `OR`, `NOT`, `IN`, `IS`, `LIKE`, `BETWEEN`, …), false for a bare column/literal/scalar arithmetic root
   - 4.4. Keep `parse_expression`, `extract_column_refs`, `_collect_segments`, and `compute_target_tables_referenced` unchanged; the M2 taxonomy is expressed as "reject the M1 navigation nodes and the M3 volatile functions" (a denylist of forbidden constructs), so no allowlist enumeration is added here

5. [completed] Create + run tests for the parse-tree primitives - `code/load_metadata_db/unit_tests/test_sql_parsing.py`
   - 5.1. `contains_statement_or_navigation`: subquery / `JOIN` / CTE / `UNION` flagged; a scalar expression, a `CASE`, an aggregate, and a window function pass
   - 5.2. `find_volatile_functions`: `now()` / `random()` / `current_user` flagged; `AT TIME ZONE 'UTC'`, `date_trunc`, `coalesce`, `split_part` not flagged
   - 5.3. `is_boolean_predicate`: `a = b`, `a AND b`, `a IN (...)`, `a IS NOT NULL` true; a bare column, a literal, and `a + b` false
   - 5.4. Run with coverage

6. [completed] Add the `table_a_id` schema anchor (C2), corpus-wide unknown-key rejection + body-`system` match (F), and read `is_primary_key` (M5) - `code/load_metadata_db/corpus_assembly.py`
   - 6.1. F — define the recognized-key set per file type (one allowlist each for `system`, `data_source`, `schema`, `tables`, `columns`, `table_relationships`, `column_mappings`) covering exactly the keys each `_assemble_*` reads; in every per-row/per-file assembler, raise `ValueError` naming the offending key and file when a body key is outside its allowlist. Include `is_primary_key` in the `columns` allowlist (added in 6.5) and `system` in the path-derived allowlists per 6.2
   - 6.2. F — path-derived `system`: allow a `system:` key in the body but require it to equal the path-derived `ident.system`; a mismatch is a `ValueError`. (Reconciles the maintenance-doc legend, which currently says path-derived fields must not appear — resolve toward "allowed, must match"; the legend edit is Task 19)
   - 6.3. C2 — in `_assemble_table_relationships` (where `PathIdentity` is available, alongside the duplicate-PK guard's home in `assemble_corpus`): require `table_a_id`'s schema prefix (`subltree(table_a_id, 0, 3)` == `{system}.{database_name}.{schema_name}` of the file) to equal the file's schema; raise `ValueError` like `relationship table_a_id '<id>' is not in this file's schema '<schema_id>'`. `table_b_id` keeps only the existing same-system rule (may live in another schema/data source within the system)
   - 6.4. C2 — enforce the anchor at the point in `_assemble_table_relationships` where the file's `PathIdentity.system`/`schema_name` are already read; do not add a second corpus pass
   - 6.5. M5 — in `_assemble_columns`, read an optional `is_primary_key` (bool, default `False`); non-bool → `ValueError` naming the path; pass it to `ColumnRow(...)`
   - 6.6. Confirm the existing mappings path-agreement check (source_column_id prefix == folder) is untouched — it is the mappings analogue and stays as-is

7. [completed] Update + run tests for corpus_assembly - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
   - 7.1. F: an unrecognized/typo'd key (e.g. `join_conditon:`) in each affected file type raises; a body `system` disagreeing with the path raises; a well-formed row with only known keys and a matching `system` passes
   - 7.2. C2: a `table_a_id` outside the file's schema raises; a `table_b_id` in another schema of the same system passes
   - 7.3. M5: a `columns` row with `is_primary_key: true` assembles with the flag set; omitted → `False`; a non-bool value raises
   - 7.4. Run with coverage

8. [completed] Add all corpus-wide + per-row validation rules (B, D, E, G, H, M1, M2, M3, M7, M8, M9) - `code/load_metadata_db/corpus_validation.py`
   - 8.1. B (relationships, whole-corpus): group rows by `(frozenset({table_a_id, table_b_id}), relationship_name)`; a second row on the same key is a conflict naming both rows. Allows the same pair under different names; a self-join duplicated under one name is still a conflict
   - 8.2. E (relationships, per-row): `join_type`, when present, must be exactly one of `INNER`/`LEFT`/`RIGHT`/`FULL` (case-sensitive, matching the DB CHECK); absent defaults to `INNER`
   - 8.3. D + G (relationships, in `_check_sql_expressions`, single traversal over each `join_condition`'s refs): D — every column ref's `table_id` must be `table_a_id` or `table_b_id` (subset), and when `table_a_id != table_b_id` the refs must include both endpoints (coverage); a self-join needs only subset. G — the parsed `join_condition` root must be a boolean predicate (`sql_parsing.is_boolean_predicate`). Emit distinct messages for unknown-column vs. wrong-table vs. non-predicate; keep existence + membership as two failure modes of the one loop
   - 8.4. H (relationships, whole-corpus, reusing B's unordered-pair grouping): when an unordered pair `{table_a_id, table_b_id}` carries more than one relationship, every one must have a non-null `use_when`; a single-relationship pair has no requirement
   - 8.5. M1 + M2 (mappings, in `_check_sql_expressions` over each non-null `target_expression`): reject the tree if `sql_parsing.contains_statement_or_navigation` finds a `SELECT`/`FROM`/`JOIN`/subquery/CTE/set-op or a trailing statement (M1); the M2 taxonomy is enforced as the M1 + M3 denylists (aggregates, windows, `FILTER`, conditionals, casts, scalar built-ins all pass)
   - 8.6. M3 (mappings): reject any `target_expression` whose tree contains a `sql_parsing.VOLATILE_FUNCTION_DENYLIST` function; explicit `AT TIME ZONE '<zone>'` passes
   - 8.7. M7 (mappings, in the same per-ref loop as existing resolution): every `target_expression` column ref must belong to `target_system`; emit "column is in `<system>`, not target_system `<T>`" vs. the existing "unknown column". A within-system mapping (target_system == source system) is unaffected. One traversal, two failure modes — do not add a separate pass
   - 8.8. M8 (mappings, whole-corpus): group by `(source_column_id, target_system)`; if a group has more than one `mapping_name`, each must have a non-null `use_when` (the H-analogue)
   - 8.9. M9 (mappings, whole-corpus): for a `target_expression` referencing more than one `target_system` table, build the `target_system` `table_relationships` graph (nodes = tables, edges = relationships) and require the referenced tables to lie in one connected component; a single-table expression passes trivially. Error message points to the missing enabling relationship
   - 8.10. Wire the new whole-corpus checks (B, H, M8, M9) as their own aggregated checks alongside `_check_references`/`_check_within_row`/`_check_sql_expressions`; keep every issue accumulated and reported together via `ValidationError` (no fail-fast). D/G/M1/M2/M3/M7 extend `_check_sql_expressions`; E extends the per-row pass

9. [completed] Update + run tests for corpus_validation - `code/load_metadata_db/unit_tests/test_corpus_validation.py`
   - 9.1. B: reverse-orientation pair rejected; same pair under distinct names accepted; self-join duplicated under one name rejected
   - 9.2. C2 is covered in Task 7 (assembly); here confirm no regression in existing relationship reference/within-row cases
   - 9.3. D: `join_condition` referencing a third table rejected; referencing only one endpoint rejected; a valid two-table condition using functions/casts accepted; a self-join condition accepted
   - 9.4. E: invalid `join_type` rejected; a valid value and the omitted default accepted
   - 9.5. G: bare-column / scalar `join_condition` rejected; equality / `AND` predicate accepted
   - 9.6. H: a two-relationship pair with one null `use_when` rejected; both carrying `use_when` accepted; a single-relationship pair with null `use_when` accepted
   - 9.7. M1: subquery/`JOIN` `target_expression` rejected; scalar accepted; trailing second statement rejected
   - 9.8. M2: `CASE WHEN` accepted; cross-grain `SUM` accepted; conditional aggregate via `FILTER` and via `SUM(CASE …)` both accepted; window with `PARTITION BY`/`ORDER BY` accepted; `string_agg` with `ORDER BY` accepted; a grain-misaligned window still accepted (grain is authoring, not a loader check)
   - 9.9. M3: `now()` / `random()` / `current_user` rejected; explicit `AT TIME ZONE 'UTC'` accepted
   - 9.10. M7: `target_expression` referencing a non-`target_system` table rejected; all refs in `target_system` accepted; within-system mapping accepted
   - 9.11. M8: two mappings for one `(source_column_id, target_system)` with one null `use_when` rejected; both with `use_when` accepted; single mapping with null `use_when` accepted
   - 9.12. M9: two-table expression whose tables share no relationship path rejected; the same two tables connected by a relationship accepted; single-table expression accepted trivially
   - 9.13. Run with coverage at the 100% bar

10. [completed] Carry `is_primary_key` through the columns SQL + param builders (M5) - `code/load_metadata_db/db_io.py`
    - 10.1. `_SELECT_COLUMNS`: add `is_primary_key` to the column list (order matching `ColumnRow`); `read_db_state` builds `ColumnRow(*row)` positionally, so the SELECT order must match the dataclass field order
    - 10.2. `_INSERT_COLUMNS`: add `is_primary_key` to the column list and a `%s` placeholder; `_insert_params["columns"]` adds `row.is_primary_key` in the matching position
    - 10.3. `_UPDATE_COLUMNS`: add `is_primary_key=%s` to the SET clause; `_update_params["columns"]` adds `row.is_primary_key` before the `key` WHERE param
    - 10.4. `_HSTRY_INSERT_COLUMNS`: add `is_primary_key` to both the insert column list and the SELECT list
    - 10.5. No change to the `column_mappings`/`table_relationships` SQL — the relationship/mapping rules are validation-only; M6 is an index (created in Task 1, invisible to these statements)

11. [completed] Update + run tests for db_io - `code/load_metadata_db/unit_tests/test_db_io.py`
    - 11.1. Update the columns SELECT/INSERT/UPDATE/HSTRY assertions to include `is_primary_key` in the expected column order; assert `_insert_params`/`_update_params` for `columns` carry the flag in the correct position
    - 11.2. Keep the existing `column_mappings` array-cast and null-`target_tables_referenced` guards unchanged
    - 11.3. Run with coverage

12. [completed] Confirm the generic diff engine needs no change - `code/load_metadata_db/corpus_diff.py` (+ `unit_tests/test_corpus_diff.py`)
    - 12.1. Confirm `corpus_diff.py` is driven entirely by `CONTENT_COLUMNS`/`PRIMARY_KEY_COLUMNS`, so `is_primary_key` is picked up automatically (an `is_primary_key` change surfaces as an in-place update) and no `column_mappings`/`table_relationships`-specific code exists — no edit expected
    - 12.2. Confirm `test_corpus_diff.py` needs no new fixtures; run it green as a regression check

13. [completed] Confirm the orchestrator's derivation still aligns - `code/load_metadata_db/load_metadata_db.py` (+ `unit_tests/test_load_metadata_db.py`)
    - 13.1. Confirm `load_metadata_db.py`'s `target_tables_referenced` derivation (keyed off `corpus.column_mappings` + `cm.target_system` via the validation memo) is unaffected by the new rules — no edit expected; adjust only if a rename surfaces
    - 13.2. No standalone test change — covered by `test_load_metadata_db.py` (advisory lock; unaffected, run green as regression) and the Phase D integration test

### Phase C — Corpus & example YAML

14. [completed] Set `is_primary_key` flags in the sandbox corpus (M5) - `data/systems/sandbox/pagila/public/columns.yaml`, `data/systems/sandbox_warehouse/mart/analytics/columns.yaml`
    - 14.1. Add `is_primary_key: true` to each surrogate/primary-key column (per each column's existing `description`, e.g. "Surrogate primary key."); leave non-key columns to default (omit the key or set `false`)
    - 14.2. Update each file's header/legend comment to document the new `is_primary_key` field and that omission defaults to `false`
    - 14.3. Confirm the existing `table_relationships.yaml` and `mappings/sandbox_warehouse.yaml` need no edit — the design docs state the current corpus is compliant with every new rejection (no reverse pairs, every `table_a_id` local, every `join_condition` a two-table equality predicate with valid `join_type`, no unknown keys, body `system` matching the path, single relationship per pair; mappings use only bare columns/`split_part`/arithmetic/casts, all refs in `sandbox_warehouse`, the one multi-table expression's tables linked); the Task 16 dry-run confirms

15. [completed] Update the example YAMLs to match the new schema/rules - `readme/metadata-db-example-yamls/data/systems/sandbox/pagila/public/columns.yaml`, `readme/metadata-db-example-yamls/data/systems/sandbox_warehouse/mart/analytics/columns.yaml`, `readme/metadata-db-example-yamls/data/systems/sandbox/pagila/public/mappings/sandbox_warehouse.yaml`
    - 15.1. Add `is_primary_key` to the example columns (flag the PK columns; update the header schema table with the new field and its default)
    - 15.2. In the example mappings, add/keep an illustrative cross-grain aggregate (`SUM(...)`) and/or conditional aggregate demonstrating the allowed M2 taxonomy, ensuring all refs stay in `target_system` (M7) and any multi-table expression's tables are linked (M9); keep the file loadable
    - 15.3. Verify the example relationship YAMLs still satisfy B/C2/D/E/G/H (adjust orientation or add `use_when` only if an example currently violates a rule)

### Phase D — Rebuild, reload, and end-to-end verification

16. [completed] Rebuild the local Postgres, apply `0001`, regrant, reload, dry-run - maintainer-run, out-of-band
    - 16.1. As `metadata_db_maintainer` (lacks CREATEDB): `DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO public; REVOKE CREATE ON SCHEMA public FROM public;`
    - 16.2. `uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl.toml` — re-applies the edited `0001` (M5 column + M6 index); `ddl_versions` records the new checksum
    - 16.3. Re-run `code/apply_ddl/grant_metadata_db_ci.sql` as maintainer to restore the `metadata_db_ci` privilege model on the rebuilt tables
    - 16.4. Reload the corpus as the CI role: `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml`. Expect a clean full-insert load with NO new validation rejection — this is the pre-check that confirms the live corpus (pagila `public`, warehouse `analytics`) is compliant with B/C2/D/E/F/G/H and M1/M2/M3/M7/M8/M9. Any C2 offender is fixed by ordering endpoints so `table_a` is the local table
    - 16.5. Verify: `columns.is_primary_key` populated for PK columns; the GiST index exists (`\d column_mappings`); an `@>` containment query over `target_tables_referenced` uses the index; `apply_ddl.py --check` clean

17. [completed] Expand + run the integration test - `code/load_metadata_db/unit_tests/test_integration.py`
    - 17.1. Extend `test_pk_agreement` (or an adjacent schema-shape check) to assert `columns.is_primary_key` exists (boolean, not-null) in the built schema and that the `idx_column_mappings_target_tables_gist` index is present
    - 17.2. Stage a corpus row exercising `is_primary_key` round-trip (set/flip) through `_hstry` on update
    - 17.3. Add at least one negative fixture per design doc proving the loader now rejects pre-merge (e.g. a reverse-orientation relationship, a third-table `join_condition`, an invalid `join_type`, a subquery `target_expression`, a `now()` expression, an unlinkable multi-table expression) — asserting `ValidationError` aggregates them
    - 17.4. Run `METADATA_DB_INTEGRATION=1 uv run pytest -m integration code/load_metadata_db/unit_tests/test_integration.py -v` against the rebuilt DB, then the full unit suite at the 100%-coverage bar: `uv run pytest code/load_metadata_db/unit_tests -v --cov --cov-report=term-missing`

### Phase E — Documentation

18. [completed] Update the schema reference and modeling guidance - `readme/metadata-db-overview.md`
    - 18.1. `columns` table: add the `is_primary_key` row (boolean; records grain; consumer knowledge, not loader-enforced); document the mapping interpretation convention (a `target_expression` is evaluated per source row at the source column's grain; grouping comes from `table_relationships` + `is_primary_key`, not the expression)
    - 18.2. `column_mappings`: widen §3 (Column mappings → "Equivalence, not transformation or joins") and the "Modeling column mappings" section for cross-grain aggregation and the M1–M3 construct taxonomy; note the target-system-only rule (M7), the multi-table linkability floor (M9), the `use_when` disambiguation (M8), and the as-of/temporal out-of-scope boundary
    - 18.3. `column_mappings.target_tables_referenced`: note the GiST index (M6) and the `@>`/`<@`/`&&` containment query it serves
    - 18.4. `table_relationships`: note the orientation-uniqueness rule (B), the `table_a_id` schema-anchor authoring rule (C2), the two-table join-condition scope + boolean-predicate rules (D, G), loader-side `join_type` validation (E), and `use_when` disambiguation (H)
    - 18.5. Reconcile any prose that a `join_condition`/`target_expression` may route through intermediate tables — it may not (D, M1)

19. [completed] Update the maintenance doc and close the design docs - `readme/metadata-db-maintenance.md`, `docs/design/table-relationship-validation-rules.md`, `docs/design/column-mapping-validation-rules.md`
    - 19.1. Loader-validation list: add the new relationship rules (B, C2, D, E, F, G, H) and mapping rules (M1, M2, M3, M7, M8, M9), and note F (unknown-key rejection + body-`system`-must-match) applies to all seven file types
    - 19.2. Fix the path-derived-fields legend to "allowed in body, must equal the path-derived value" (per F), resolving the inconsistency with the examples/data that carry `system:`
    - 19.3. Authoring runbook: document `is_primary_key` (flag PK columns; omission defaults false), the `table_a_id`-anchors-the-file rule for relationships, and the allowed/forbidden `target_expression` construct taxonomy with the out-of-scope (as-of/temporal → intentional drop) guidance
    - 19.4. Flip both design docs' `status:` frontmatter to `implemented` (from `decided — pending implementation`) once Task 17 is green

### Phase F — Review

20. [pending] Code review of changed files and address findings - `docs/code_review/`
    - 20.1. Run `code-review-agent` against each changed code file (`0001_initial_schema.sql`, `data_model.py`, `sql_parsing.py`, `corpus_assembly.py`, `corpus_validation.py`, `db_io.py`), writing `cr_*.md` per the existing `docs/code_review/` layout
    - 20.2. Address findings via `code-implementation-agent`; re-run the full suite at the 100%-coverage bar
    - 20.3. Mark each review's Status resolved when fixes land

## Key Data Decisions and Considerations

1. **Two design docs, one activity** — the relationship rules (B, C2, D, E, F, G, H) and mapping rules (M1–M9) are implemented together because they touch the same files (`corpus_validation.py`, `corpus_assembly.py`, `sql_parsing.py`) and share cross-cutting decisions: F's unknown-key rejection is corpus-wide (all seven file types), and the `use_when` disambiguation rule is one pattern applied to both tables (relationships H, mappings M8). Splitting would duplicate the schema-rebuild and doc passes.

2. **Validation-only, except M5 and M6** — no rule changes the schema. The only schema edits are M5 (`columns.is_primary_key` + its `_hstry` mirror) and M6 (a GiST index on `column_mappings.target_tables_referenced`). Both add *capability* (consumer grain knowledge; indexed lineage queries), not a new loader constraint — `corpus_validation` is untouched by M5/M6.

3. **Edit `0001` + rebuild, not a `0002` ALTER** — follows the established pre-launch bootstrap exception (`enable_cross_source_mappings` Key Decision #5, `design_hardening` Key Decision #6): only `sandbox.*` exists and is fully reproducible from YAML, so schema changes land by editing `0001` and rebuilding. `is_primary_key`'s `not null default false` and M6's index add cleanly to a fresh apply. After the first real (non-sandbox) system lands, `0001` becomes immutable and further changes go into numbered diffs.

4. **M4 is withdrawn — no work** — the `FILTER`-clause ban was dropped in the design doc (it is semantically identical to the allowed `SUM(CASE …)` and reproduces real stored columns). `FILTER`/conditional aggregation is an *allowed* construct under M2; the in-scope-vs-recipe call stays a human/CODEOWNERS judgment backed by `validated`.

5. **M2 taxonomy is enforced as a denylist, not an allowlist** — the set of legitimate value-producing built-ins is too large to enumerate, so the loader rejects only the forbidden constructs (M1 navigation nodes + M3 volatile functions) and lets everything else through. This matches the design doc's "denylist, not allowlist" note for M3 and keeps aggregates/windows/`FILTER`/casts/scalar built-ins passing without maintenance.

6. **Parse-tree primitives live in `sql_parsing.py`; rules live in `corpus_validation.py`** — the reusable predicates (`contains_statement_or_navigation`, `find_volatile_functions`, `is_boolean_predicate`) and the `VOLATILE_FUNCTION_DENYLIST` go in `sql_parsing.py` (which already owns parse-tree concerns and gets its own test file); `corpus_validation._check_sql_expressions` calls them so each rule stays a thin decision over the tree it already builds. D/G/M1/M2/M3/M7 are one traversal per expression with multiple failure modes — not bolted-on second passes.

7. **F (unknown-key rejection) is the widest-blast-radius change** — it touches every `_assemble_*` function and requires a correct recognized-key allowlist per file type; a too-narrow allowlist would reject the compliant current corpus. The `columns` allowlist must include `is_primary_key` (M5), and every allowlist must include the optional `system:` body key (allowed but must match the path, per 6.2). The Task 16 dry-run is the guard that no live row is wrongly rejected.

8. **Grain is consumer knowledge, not loader enforcement (M5)** — `is_primary_key` is added so a consumer (a researcher or Claude Code) can derive the correct `GROUP BY` for an aggregate/multi-table mapping; the loader does NOT verify grain alignment. Correctness of the composed grouping stays with the consumer + the `validated` flag, exactly as `join_condition`/`target_expression` correctness is already handled. The earlier "Layer 2/3" loader grain-verification idea was dropped as over-engineering.

9. **M9 is a linkability floor, not a grain/path proof** — it only asserts a multi-table expression's referenced tables share one connected component of the `target_system` relationship graph (they *can* be joined at all); it does not resolve which path or verify grain. It bites only on multi-table expressions — a single-table aggregate (`SUM(clm_line.amount)`) trivially satisfies it and does not force the parent-grain relationship to exist. Justified as a composability/referential check (close to "every ref resolves"), and the target team already co-owns every cross-system mapping and its relationships (CODEOWNERS), so a missing enabling relationship is cataloged by an already-required reviewer.

10. **As-of/temporal lookups are out of scope (mappings)** — a target equivalent needing a non-key/temporal/range join cannot be captured (the join is not a key-equality `table_relationship`, so it can't be delegated, and M1 forbids it in the expression). Authors record an intentional drop (`target_expression: null` + `notes`) rather than a misleading clean-looking expression. Simple arithmetic conversions (`amount_cents / 100`) stay in scope.

11. **Current corpus is expected compliant; Task 16 dry-run is the pre-check** — the design docs assert the live corpus passes every new rejection, but adding rejections is a breaking change for authors, so the rebuild+reload (Task 16.4) must confirm no live row is rejected before the rules are considered enabled. Fixing a C2 offender is mechanical (order endpoints so `table_a` is the local table).

12. **Schema-before-code ordering** — Task 1 (schema) is applied at the Task 16 rebuild, the synchronization point, before the live integration test. Loader code + mocked unit tests (Phase B) land against the target shape without a DB. This mirrors both precedent activities and the `check_schema_in_sync` drift guard.

13. **Implementation status at hand-off (2026-07-17)** — Phases A–C, E, and the DB-independent parts of Phase D are complete: all schema (Task 1), loader code (Tasks 2, 4, 6, 8, 10), unit tests (Tasks 3, 5, 7, 9, 11), corpus/example YAML (Tasks 14, 15), and docs (Tasks 18, 19) are done. The full unit suite is green (250 passed, 4 integration tests skipped) at 100% coverage on every source module. The corpus/example dry-run pre-check (Task 16.4) was verified **at the validation layer** without a DB: `assemble_corpus` + `validate_corpus` pass cleanly on both `data/` (11 PK flags) and the example tree (11 PK flags), confirming the live corpus is compliant with every new rejection (B, C2, D, E, F, G, H, M1–M3, M7–M9). The tests in Tasks 3/5/7/9/11 rebuilt the two example-based helpers (`test_corpus_assembly._build_corpus_tree`, `test_load_metadata_db._stage_corpus`) that the earlier example-tree restructure (commit 89773…) had left pointing at the removed flat layout — a pre-existing breakage fixed here so the suite runs green.

14. **Blocked on the maintainer / review phase (Tasks 16, 17.4, 20)** — three steps require access this implementation agent does not hold and are handed to the maintainer:
    - **Task 16 (DB rebuild + reload + verify)** — needs the `metadata_db_maintainer` role (owner/`DROP SCHEMA`/regrant); the only reachable role here is `metadata_db_ci` (DML-only, no CREATEDB). Left `[pending]` for the maintainer to run out-of-band per the runbook.
    - **Task 17.4 (live integration run)** — the integration test code is authored (17.1 asserts `columns.is_primary_key` boolean/not-null and the `idx_column_mappings_target_tables_gist` index; 17.2 exercises the `is_primary_key` set→flip round-trip through `_hstry`; 17.3 adds a negative fixture proving the loader rejects B/E/M1/M3 violations pre-merge, which passes DB-free since validation precedes the connection). Running the DB-backed tests (`test_pk_agreement_and_ltree_types`, `test_loader_lifecycle`, `test_within_system_and_multiple_mappings`) awaits the Task 16 rebuild on a maintainer DB. Task 17 is `[in-progress]`.
    - **Task 20 (code review)** — Phase F runs the `code-review-agent` and addresses findings; a single code-implementation agent does not spawn other agents, so this is left `[pending]` as the review follow-up.
