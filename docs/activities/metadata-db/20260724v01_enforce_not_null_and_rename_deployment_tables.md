---
name: 20260724v01_enforce_not_null_and_rename_deployment_tables
goal: Implement the two constraint decisions already specified docs-first in readme/metadata-db-overview.md — required documentation prose (description on systems/data_sources/schemas/tables/columns, definition on concepts) enforced by the loader and NOT NULL in the DDL alongside the two ltree[] columns. Reshape deployments into a pure-facts table — renamed deployment_tables (with its history mirror, constraint, and indexes), notes and update_reason dropped from the table, the deployments.yaml entry grammar, and the diff engine's two special cases. Ends with one schema rebuild from the edited 0001, a corpus reload, and an idempotent 0/0/0 dry-run.
created: 2026-07-24 21:19:57
updated: 2026-07-24 21:23:24
---

## Implementation Plan

### Phase 1 — Data model and identity

1. [completed] Reshape DeploymentRow and rename the deployments registry entry - `code/load_metadata_db/data_model.py`
   - 1.1. `DeploymentRow` drops `notes` and `update_reason` — its fields become exactly the identity, `data_source_id`, and the three physical names
   - 1.2. The table registry (`TABLE_ORDER`, `CONTENT_COLUMNS`, `PRIMARY_KEY_COLUMNS`, and the `Corpus`/`DbState` attribute) renames `deployments` → `deployment_tables`, keeping its position in the parent-before-child ordering
   - 1.3. `CONTENT_COLUMNS["deployment_tables"]` covers only the physical-name/data_source_id content (no `update_reason` to exclude — the column is gone)
   - 1.4. Docstrings describe the pure-facts shape and point to the overview's `deployment_tables` section for the rationale

### Phase 2 — Loader behavior

2. [completed] Require documentation prose; strip the deployments entry grammar - `code/load_metadata_db/corpus_assembly.py`
   - 2.1. Required non-blank `description` (missing/blank string is an aggregated issue, same pattern as `data_sources.owner`): registry entries (`_assemble_system_row`), `schema.yaml` (`_assemble_schema`), `tables.yaml` rows (`_assemble_table_row`), `columns.yaml` rows (`_assemble_column_row`); required non-blank `definition` on concept rows (`_assemble_concept_row`). Multi-field files report all missing required fields in one issue (the `data_source.yaml` owner+description precedent)
   - 2.2. `_RECOGNIZED_KEYS["deployments"]` drops `notes` and `update_reason` — an entry still carrying them fails as an unrecognized key, pointing authors at the new rules
   - 2.3. Deployment expansion builds the slimmed `DeploymentRow` (no inherited values); the `Corpus` attribute rename from task 1 carries through the assembly wiring
   - 2.4. The `yaml_discovery` file-type label for `deployments.yaml` stays `deployments` (it names the file, which is unchanged) — only the assembled corpus attribute and DB table use the new name

3. [completed] Delete the deployment update_reason special cases - `code/load_metadata_db/corpus_diff.py`
   - 3.1. Remove the insert auto-null block in `compute_diff` and the `update_reason` exclusion in `_content_signature` — with the column gone from `DeploymentRow`, both are dead code, and the diff logic returns to being identical for all 9 tables
   - 3.2. Iteration and mass-delete counting pick up the renamed registry entry from task 1 with no behavior change

4. [completed] Exempt deployment_tables from update_reason discipline - `code/load_metadata_db/corpus_validation.py`
   - 4.1. `validate_update_reason` skips `deployment_tables` rows entirely (they carry no `update_reason` attribute), with the derived-rows/git-via-load_audit rationale in its docstring
   - 4.2. All deployments-related checks (referential, physical-address uniqueness, co-deployment, `data_source_id`/`table_id` agreement) operate on the renamed corpus attribute; message texts say `deployment_tables` where they name the table (file-path context still says `deployments.yaml`)

5. [completed] Rename the table in all SQL and the deferred constraint - `code/load_metadata_db/db_io.py`
   - 5.1. All read/insert/update/delete/history SQL targets `deployment_tables` / `deployment_tables_hstry`; column lists drop `notes` and `update_reason` (main and hstry)
   - 5.2. The deferred-constraint literal becomes `deployment_tables_physical_address_key` (shared literal with the DDL — keep them in agreement); the `SET CONSTRAINTS` statement and its test-pinned first-statement position are unchanged otherwise
   - 5.3. Read/write log lines and docstrings use the new name

6. [completed] Orchestrator currency - `code/load_metadata_db/load_metadata_db.py`
   - 6.1. No flow changes expected: verify log summaries, docstrings, and the `--reset-hstry` truncation list (derived from the registry) reflect `deployment_tables`

### Phase 3 — DDL and grants

7. [completed] NOT NULLs, the rename, and the column drops in the initial schema - `code/apply_ddl/ddl/0001_initial_schema.sql`
   - 7.1. `NOT NULL` on `description` (`systems`, `data_sources`, `schemas`, `tables`, `columns`), `definition` (`concepts`), `target_tables_referenced` (`column_mappings`), and `related_object_ids` (`concepts`) — on the main tables and their `_hstry` mirrors (the `owner` precedent: data-shape constraints carry to mirrors)
   - 7.2. Rename `deployments` → `deployment_tables` and `deployments_hstry` → `deployment_tables_hstry`; drop `notes` and `update_reason` from both, leaving every column NOT NULL
   - 7.3. Constraint and index renames: `deployments_physical_address_key` → `deployment_tables_physical_address_key` (still `deferrable initially immediate`); `idx_deployments_*` → `idx_deployment_tables_*`; the `subltree` CHECK and all FKs carry over unchanged
   - 7.4. `comment on table/column` statements updated; the file's header notes stay consistent with the overview's §5 backstops inventory

8. [completed] Grant script table lists - `code/apply_ddl/grant_metadata_db_ci.sql`
   - 8.1. DML grant list names `deployment_tables`; INSERT-only hstry list names `deployment_tables_hstry`; header prose updated

### Phase 4 — Tests

9. [completed] Row-shape and registry coverage - `code/load_metadata_db/unit_tests/test_data_model.py`
   - 9.1. `DeploymentRow` field set (no notes/update_reason), `pk()` unchanged, registry order and `CONTENT_COLUMNS` agreement under the new name; fixture sweep

10. [completed] Required-field and grammar coverage - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
    - 10.1. Missing/blank `description` rejected per file type (registry entry, schema, table row, column row) and missing/blank `definition` on a concept; multi-field single-issue reporting where applicable
    - 10.2. A deployments entry carrying `notes:` or `update_reason:` fails as an unrecognized key
    - 10.3. Fixture tree gains descriptions everywhere the new rules demand them; expansion assertions target the slimmed row shape

11. [completed] Diff coverage - `code/load_metadata_db/unit_tests/test_corpus_diff.py`
    - 11.1. Delete the auto-null and signature-exclusion tests (dead behavior); deployment insert/update/delete/no-op and mass-delete cases re-keyed to `deployment_tables` with the slimmed rows
    - 11.2. Keep a regression test that a `deployment_tables` diff round-trips idempotently (insert → re-diff empty) — now trivially true, pinned so it stays true

12. [completed] Validation coverage - `code/load_metadata_db/unit_tests/test_corpus_validation.py`
    - 12.1. `validate_update_reason` exemption: a changed `deployment_tables` row raises no update_reason issue while a changed authored-table row still does; fixture sweep to slimmed rows and renamed attribute

13. [completed] DB-IO coverage - `code/load_metadata_db/unit_tests/test_db_io.py`
    - 13.1. Column lists and bound-parameter indexes against the new shapes; renamed table/hstry/constraint literals; deferred-constraint-first-statement test updated to the new name

14. [completed] Orchestrator coverage - `code/load_metadata_db/unit_tests/test_load_metadata_db.py`
    - 14.1. Staged corpora gain required descriptions; reset-hstry list assertion covers `deployment_tables_hstry`

15. [completed] Discovery coverage - `code/load_metadata_db/unit_tests/test_yaml_discovery.py`
    - 15.1. Verify no behavior change (file grammar and paths unchanged); fixture-only updates if any assertions name the registry

16. [completed] Integration coverage - `code/load_metadata_db/unit_tests/test_integration.py`
    - 16.1. Staged corpus carries all required descriptions/definitions; deployment rename/restore/swap scenarios drop their `update_reason` entry keys and hstry-count assertions adjust to the no-reason model; table/constraint names updated
    - 16.2. Full unit suite green at the established 100%-coverage bar; run the gated integration suite (`METADATA_DB_INTEGRATION=1`, CREATEDB-capable role) — it is the only executor of the hand-written SQL against real ltree

### Phase 5 — Rebuild, reload, verify (out-of-band, after all tests green)

17. [completed] Schema-scoped rebuild and corpus reload - loader/DDL runs, no repo file
    - 17.1. As `metadata_db_maintainer`: `drop schema prod cascade`, then `uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl.toml`, then `psql -v schema=prod -v database=metadata_db -d metadata_db -U metadata_db_maintainer -f code/apply_ddl/grant_metadata_db_ci.sql`
    - 17.2. As `metadata_db_ci`: real loader run commits the corpus (289 rows expected — same counts as the current tree; deployment rows now 6 columns narrower)
    - 17.3. Post-load `--dry-run` reports 0 insert(s) / 0 update(s) / 0 delete(s)
    - 17.4. Spot checks: `\d prod.deployment_tables` shows every column NOT NULL, the renamed deferrable UNIQUE, the CHECK, and renamed indexes; `deployment_tables_hstry` exists with no notes/update_reason; the five description columns and concepts.definition/related_object_ids and column_mappings.target_tables_referenced show NOT NULL; the example corpus under readme/metadata-db-example-yamls/ still assembles and validates via the loader modules (its legends were pre-updated to this state)

## Key Data Decisions and Considerations

1. **Docs are the spec; this activity is the last mover.** `readme/metadata-db-overview.md`, `readme/metadata-db-maintenance.md`, and `readme/metadata-db-example-yamls/` were updated to the target state on 2026-07-24 (docs-first, per the venue-free migration precedent). Implementation follows the docs; any divergence discovered mid-implementation is a doc bug to fix consciously, not a license to re-decide. The full design rationale (grain tension, escalation ladder, rung-3 fat-leaves preference) lives in the overview's `deployment_tables` section and tracked task #2 — not re-argued here.

2. **Why the columns drop (recorded in the overview).** The primary reason is the structural tension between entry-grain YAML authoring and table-grain storage and the code complication it forced — inheritance plus two deployment-only diff special cases. Dropping `notes`/`update_reason` resolves the tension at its source and deletes that code; tasks 3 and 4 are deletions, not rewrites. Secondarily, nothing is lost: rationale lives in git via `load_audit`, caveats via concepts, and the shipped corpus has zero deployment notes and all-null reasons (verified live 2026-07-24).

3. **Rename scope.** DB objects (`deployment_tables`, `deployment_tables_hstry`, `deployment_tables_physical_address_key`, `idx_deployment_tables_*`), the corpus/registry attribute, and all SQL/log/docstring references rename; the YAML filename `deployments.yaml` and the discovery file-type label do not — the file describes deployments generally and its grammar already spans the grains a future reshape would store separately. The table is named for its grain, reserving `deployment_dbs`/`deployment_schemas`.

4. **No corpus YAML edits.** All 289 shipped rows already satisfy every new constraint (0 null descriptions/definitions/arrays; no deployments entry uses `notes`/`update_reason`) — verified against the live DB and the YAML tree on 2026-07-24. If assembly surfaces a violation anyway, that is an authoring defect to fix in the same change, not a rule to relax.

5. **Edit `0001` + rebuild, no `0002`.** Still the bootstrap phase (per the venue-free precedent and the maintenance doc's bootstrap note): the data is reproducible from YAML, so the schema-scoped drop/rebuild path applies. This is the single rebuild for both decision sets — they share the DDL edit deliberately so `prod` is rebuilt once.

6. **NOT NULLs carry to `_hstry` mirrors** (the `owner` precedent): every main row satisfied the constraint while current, so its history copies do too. The arrays' empty-array-never-NULL semantics are the loader's existing write behavior — the DDL codifies, not changes, the contract.

7. **`validate_update_reason` exemption over column retention.** The discipline applies to authored rows; `deployment_tables` rows are derived, and their "why" is the git commit joinable via `load_audit` — the same stance the model already takes on deletes. The exemption plus the task-3 deletions net *less* special-case code than the status quo.

8. **Sequencing.** Phase 1 first (every later module compiles against the new row shape and registry name); phases 2–3 in any order after it; tests after their modules (the suite cannot go green mid-stream — fixtures encode the old shapes — so per-file test tasks land with or after their module, and task 16 is the gate); the rebuild is last and only runs once the full suite is green. Until phase 5 completes, the live `prod` schema stays on the current DDL and the docs describe a future state.

9. **Integration suite must actually run** (gate, not gesture): the 2026-07-24 review found a never-executed gated test hiding fixture defects. Task 16.2 runs it with the CREATEDB-capable role against the throwaway `metadata_db_integration` database before phase 5 touches `prod`.

10. **Implementation run notes (2026-07-24).** (a) The gated integration suite could not run as `metadata_db_maintainer` — that role lacks CONNECT on the bootstrap `postgres` database the fixture connects to for `DROP DATABASE ... WITH (FORCE)`; it was run (all 5 tests green) with the superuser pair from `.env`, the CREATEDB-capable route. (b) The loader's Python constant was renamed alongside the SQL literal: `db_io.DEPLOYMENTS_PHYSICAL_ADDRESS_CONSTRAINT` → `DEPLOYMENT_TABLES_PHYSICAL_ADDRESS_CONSTRAINT` (value `deployment_tables_physical_address_key`, matching the DDL). (c) Phase 5 verified live: rebuild + grants clean, real load committed exactly 289 inserts as `metadata_db_ci` (39 `deployment_tables` rows), post-load dry-run 0/0/0, every targeted column NOT NULL on mains and `_hstry` mirrors, `deployment_tables` all-NOT-NULL with the renamed deferrable UNIQUE / CHECK / indexes, and the example corpus under `readme/metadata-db-example-yamls/data` assembles and validates through the loader modules. (d) Unit suite: 538 passed, all loader modules and test files at 100% coverage.
