---
name: 20260630v01_design_hardening
goal: Apply the set of design-hardening improvements agreed for metadata_db (items #1–#11 of the design review) — concurrency safety, an audit/lineage table, validation-provenance, identifier hardening, ltree-typed IDs, migration-immutability enforcement, an expanded end-to-end test, and documentation. Schema changes land as migrations applied to the local Postgres; loader/apply_ddl code is updated against the new schema; the expanded integration test verifies the whole path end-to-end.
created: 2026-06-30 12:00:00
updated: 2026-07-01 00:00:00
---

## Implementation Plan

> Ordering rule: all schema changes (Tasks 1–6) land and are applied to the local Postgres **before** any loader code that depends on the new schema (Tasks 7+). This respects the same drift window the `check_schema_in_sync` job exists to guard. Suggested PR split: PR-A = Tasks 1–6 (migrations + apply_ddl + grants, applied first); PR-B = Tasks 7–23 (loader code, tests, docs).

### Phase A — Schema & migration tooling

1. Add migration-checksum immutability enforcement (#11) - `code/apply_ddl/apply_ddl.py`
   - 1.1. Add a `checksum text` column to the `schema_versions` table this script manages (extend the managed `CREATE TABLE IF NOT EXISTS`; for an existing table add the column if absent)
   - 1.2. On applying a new migration, store a content hash of the `.sql` file alongside its version row
   - 1.3. On every run (apply and `--check`), recompute each already-applied migration's hash and compare to the stored value; exit non-zero with a clear "migration NNNN was edited after being applied (append-only violation)" message on mismatch
   - 1.4. Preserve existing behavior: numeric-order apply, one transaction per migration, `--create-db`, `--check` read-only drift detection
   - 1.5. Do NOT adopt Alembic — rationale in Key Data Decisions

2. Create and run tests for checksum enforcement - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 2.1. Assert the checksum is stored when a migration is applied
   - 2.2. Assert a previously-applied migration whose file contents changed makes both a normal run and `--check` exit non-zero with the append-only-violation message
   - 2.3. Assert an unchanged, fully-applied set is a clean no-op
   - 2.4. Run with `uv run pytest code/apply_ddl/unit_tests/test_apply_ddl.py -v --cov=apply_ddl --cov-report=term-missing`

3. Adopt ltree-typed ID columns in the base schema (#10) - `code/apply_ddl/ddl/0001_initial_schema.sql`
   - 3.1. Enable the `ltree` extension at the top of the file
   - 3.2. Change every ID column to type `ltree` across the 7 main tables and their 7 `_hstry` mirrors: `systems.system`, `data_sources.(data_source_id, system)`, `schemas.(schema_id, data_source_id)`, `tables.(table_id, schema_id)`, `columns.(column_id, table_id)`, `table_relationships.(table_a_id, table_b_id, system)`, `column_mappings.(source_system, target_system, source_column_id)` — FK columns must match the referenced PK type
   - 3.3. Change `column_mappings.target_tables_referenced` (and its `_hstry` mirror) from `text[]` to `ltree[]`
   - 3.4. Keep the existing btree FK indexes (ltree supports `=`/ordering via btree, so joins stay fast) and ADD a GiST index per queryable ID column (`column_id`, `table_id`, `schema_id`, `data_source_id`, `system`, and the composite tables' id columns) to support `<@` / `@>` / `lquery` subtree queries
   - 3.5. This edits an already-applied migration; permitted here only because we are pre-production (see Key Data Decisions #6). Requires the DB rebuild in Task 6

4. Create the load_audit migration (#3) - `code/apply_ddl/ddl/0002_load_audit.sql`
   - 4.1. Create `load_audit` with: `load_id` (identity PK), `commit_sha` (text, not null), `inserts`/`updates`/`deletes` (integer, not null), `reset_hstry` (boolean, not null, default false), `loaded_ts` (timestamptz, not null, default `now()`)
   - 4.2. Use an identity PK (not `bigserial`) so the loader role needs only table-level `INSERT`, no separate sequence grant
   - 4.3. Header comment describing purpose and the `loaded_ts = row.update_ts` join for per-row lineage

5. Create the validated_ts migration (#5) - `code/apply_ddl/ddl/0003_validated_ts.sql`
   - 5.1. Add `validated_ts timestamptz` to `column_mappings`, `table_relationships`, and their two `_hstry` mirrors
   - 5.2. On the two main tables add a CHECK enforcing `validated_ts` is non-null exactly when `validated` is true; `_hstry` mirrors carry the column without the CHECK (history is append-only and may hold either state)

6. Rebuild the local Postgres, apply migrations, set grants, reload (run) — maintainer-run, out-of-band
   - 6.1. Drop and recreate the database (the only live data is `sandbox.pagila`, fully reproducible from YAML): `DROP DATABASE metadata_db` then `apply_ddl.py --config code/apply_ddl/config/apply_ddl.toml --create-db` (maintainer role)
   - 6.2. Confirm `0001` (ltree), `0002` (load_audit), `0003` (validated_ts) all apply and `schema_versions` records three checksummed rows
   - 6.3. Grant the loader role: `GRANT SELECT, INSERT ON load_audit TO metadata_db_ci;` (identity column needs no sequence grant). Re-confirm existing grants unchanged
   - 6.4. Reload `sandbox.pagila` via the loader (the `load_audit` write requires Task 11; until then a plain load repopulates the catalog)
   - 6.5. The grant + rebuild steps are recorded in the maintenance doc in Task 22

### Phase B — Loader code (against the migrated schema)

7. Add the fail-fast advisory lock (#1) - `code/load_metadata_db/load_metadata_db.py`
   - 7.1. Immediately after connecting and before `read_db_state`, take a fixed-key transaction-scoped advisory lock using the try/non-blocking variant
   - 7.2. If the lock is not acquired, fail with a clear "another metadata_db load is already in progress" error → exit 1 (do not block)
   - 7.3. Lock is transaction-scoped: auto-released on commit/rollback and in dry-run (connection close); no explicit unlock

8. Create and run tests for the advisory lock - `code/load_metadata_db/unit_tests/test_load_metadata_db.py`
   - 8.1. Assert the lock is requested before any `read_db_state` query
   - 8.2. Assert a failed acquisition exits 1 with the expected message and opens no write transaction
   - 8.3. Run with coverage

9. Add the validated_ts field to the row dataclasses (#5) - `code/load_metadata_db/schema.py`
   - 9.1. Add a `validated_ts` field to `ColumnMappingRow` and `TableRelationshipRow`
   - 9.2. Keep `validated_ts` OUT of `CONTENT_COLUMNS` so it never drives diff classification and is never read from YAML (loader-managed, like `insert_ts`/`update_ts`)

10. Create and run tests for the schema change - `code/load_metadata_db/unit_tests/test_schema.py`
    - 10.1. Assert `validated_ts` is present on both row dataclasses
    - 10.2. Assert `validated_ts` is excluded from `CONTENT_COLUMNS` for both tables (guards against future diff churn)
    - 10.3. Run with coverage

11. Add the load_audit write, commit-SHA resolution, and validated_ts stamping (#3, #5) - `code/load_metadata_db/db.py`
    - 11.1. Add a `resolve_commit_sha()` helper: return `$CI_COMMIT_SHA` if set, else `git rev-parse HEAD`; surface a clear error if neither is available on a real run
    - 11.2. In `apply_diff`, as the final statement inside the load transaction (real runs only), insert one `load_audit` row using the diff counts and the resolved SHA — written on **every** real run, including empty-diff no-ops; dry-run writes nothing
    - 11.3. Extend the two composite tables' `read_db_state` SELECTs to read `validated_ts` (needed to preserve it across non-validation updates)
    - 11.4. In the insert/update paths for the two tables, derive `validated_ts`: insert → set when `validated` else null; update → set on a `false→true` transition, null on `true→false`, otherwise carry the old row's `validated_ts` unchanged (uses `change.old`)

12. Create and run tests for the db.py changes - `code/load_metadata_db/unit_tests/test_db.py`
    - 12.1. Assert `resolve_commit_sha` prefers `CI_COMMIT_SHA`, then falls back to `git rev-parse HEAD` (mock subprocess/env via `monkeypatch`)
    - 12.2. Assert `apply_diff` issues the `load_audit` insert with correct counts on a real run and NOT in dry-run
    - 12.3. Assert `validated_ts` stamping for all four transitions (F→T, T→F, T→T preserves old, F→F stays null) on both composite tables
    - 12.4. Assert `read_db_state` selects `validated_ts` for the two tables
    - 12.5. Run with coverage

13. Tighten identifier syntax to the ltree charset (#7, #10) - `code/load_metadata_db/paths.py`
    - 13.1. `validate_identifier_segment`: replace the "forbid `.`/whitespace" rule with a whitelist accepting only ltree-legal labels (`[A-Za-z0-9_-]`), with a message naming the offending character

14. Create and run tests for the identifier whitelist - `code/load_metadata_db/unit_tests/test_paths.py`
    - 14.1. Negative cases: `$`, `@`, `%`, `#`, space
    - 14.2. Positive cases: hyphen, leading digit, underscore, mixed case
    - 14.3. Run with coverage

15. Add the case-mismatch resolution hint (#7) - `code/load_metadata_db/validation.py`
    - 15.1. In `_check_sql_expressions`, when a `ColumnRef.column_id` fails to resolve, scan `corpus.columns` case-insensitively and, on a hit, append a "did you mean '<X>'? (case mismatch)" hint to the issue message
    - 15.2. Keep matching case-sensitive (ltree does not fold case); the hint only improves the error

16. Create and run tests for the case-mismatch hint - `code/load_metadata_db/unit_tests/test_validation.py`
    - 16.1. Assert the hint fires on a mis-cased reference and NOT on a genuinely unknown column
    - 16.2. Run with coverage

17. Create and run the identifier/expression characterization corpus (#7) - `code/load_metadata_db/unit_tests/test_expressions.py`
    - 17.1. Parametrized refs covering mixed-case segments, a quoted reserved-word column (e.g. `"order"`), functions/`CASE`/`CAST` wrapping refs, and multiple refs in one expression
    - 17.2. Assert `extract_column_refs` / `_collect_segments` recover exactly five segments each — pins behavior against future sqlglot changes (expressions.py is not modified; this is a characterization test)
    - 17.3. Run with coverage

### Phase C — End-to-end verification

18. Expand the integration test to the fuller version (#2) - `code/load_metadata_db/unit_tests/test_integration.py`
    - 18.1. Stage a full corpus: real `columns.yaml`, `table_relationships.yaml`, and a `mappings/{target}.yaml` with at least one `column_mappings` row (exercises the `ltree[]` `target_tables_referenced` and composite-key paths)
    - 18.2. Load, then assert per-table `SELECT count(*)` and that `load_audit` has one row with the expected counts and a non-null `commit_sha`
    - 18.3. Re-run; assert an empty diff produces no catalog changes but a second `load_audit` heartbeat row
    - 18.4. Update a row's `description` (+ non-null `update_reason`); re-run; assert the main row changed AND the prior version landed in `_hstry` with the OLD `update_reason` and populated `end_ts`
    - 18.5. Flip a `column_mappings` row `validated` false→true; assert `validated_ts` is stamped; flip true→false; assert it returns to null
    - 18.6. Delete a row from the corpus; re-run; assert main has no row and `_hstry` holds the prior version
    - 18.7. Keep `test_pk_agreement`; extend it to confirm the ID columns are type `ltree` in the built schema

19. Run the expanded integration test locally against Postgres - `code/load_metadata_db/unit_tests/test_integration.py`
    - 19.1. `METADATA_DB_INTEGRATION=1 uv run pytest -m integration code/load_metadata_db/unit_tests/test_integration.py -v` against the rebuilt local `metadata_db`
    - 19.2. Fix any failures; this is the first real execution of the migrated schema + all Phase B code together
    - 19.3. CI automation with a Postgres `services:` container is explicitly out of scope (no runner) — parked as future work

### Phase D — Documentation

20. Update the schema reference for the new columns/tables (#3, #5, #10) - `readme/metadata-db-overview.md`
    - 20.1. Document the `load_audit` table and the `loaded_ts = update_ts` lineage join
    - 20.2. Document `validated_ts` (loader-managed; non-null iff `validated`; CHECK-enforced) on both tables
    - 20.3. Note ID columns are `ltree`; add example subtree/`lquery` queries end users can run

21. Update the maintenance doc — validation/reason/scaling/identifier notes (#5, #6, #7, #8) - `readme/metadata-db-maintenance.md`
    - 21.1. #5: `target_expression`/`join_condition` must be valid **Postgres** SQL (canonical dialect, enforced); researchers translate per target; Postgres validity is enforced, target runnability is not
    - 21.2. #6: `update_reason` is a best-effort human summary; the authoritative rationale is the git commit, linkable via `load_audit.commit_sha`; reason quality is a CODEOWNERS review concern
    - 21.3. #7: identifiers are case-sensitive and must match folder/YAML exactly; must be ltree-legal (`[A-Za-z0-9_-]`); reserved-word/quoted names must be quoted in expressions
    - 21.4. #8: the loader assumes a human-scale curated corpus (full-reload each run); if it ever bites, the path is the SQL-side staging-diff (never path-scoped reads) — with the reason

22. Update the maintenance doc — ops/runbook items (#9b, #9c, Task 6) - `readme/metadata-db-maintenance.md`
    - 22.1. #9b: add a "re-land after an auto-revert" runbook snippet with the exact git sequence
    - 22.2. #9c: cross-reference the advisory lock (#1) as the DB-level backstop that makes the Free/CE approximate merge-serialization safe
    - 22.3. Record the ltree rebuild + `GRANT ... ON load_audit` step from Task 6 in the migration/apply section

23. Code review and address findings - `docs/code_review/`
    - 23.1. Run `code-review-agent` against changed modules (`apply_ddl.py`, `db.py`, `load_metadata_db.py`, `paths.py`, `validation.py`, `schema.py`) mirroring the existing `docs/code_review/` layout
    - 23.2. Address findings via `code-implementation-agent`; re-run the full suite at the project's 100%-coverage bar
    - 23.3. Mark each review's `Status & Next Steps` resolved when fixes land

## Key Data Decisions and Considerations

1. **Fail-fast advisory lock, not blocking** — the try/non-blocking transaction-scoped advisory lock so a second concurrent load exits with a clear message instead of hanging a job; auto-released at transaction end. Matches Flyway's default on Postgres and the Postgres docs' guidance that advisory locks beat a lock-table flag. Defense-in-depth on top of merge serialization, which the manual-run workflow can otherwise bypass.
2. **`load_audit` write on every real run (heartbeat), inside the load transaction** — an empty-diff run still records "DB confirmed in sync with commit X at time T," which is what makes drift detection reliable. Slightly softens strict "no-op = zero writes" (one audit row grows), but catalog data and `_hstry` stay idempotent. Per-row lineage comes free from `loaded_ts = update_ts` (both are the transaction's `now()`), so no commit column is added to the 14 tables.
3. **`validated_ts` is loader-managed and excluded from `CONTENT_COLUMNS`** — authors set only the `validated` bool; the loader derives the timestamp from the transition. No auto-reset of `validated` on expression change — that would clobber the legitimate validate-first workflow (validate new logic, then commit expression + `validated:true` together). Staleness of an already-true mapping whose expression was edited is left visible for CODEOWNERS review, not machine-enforced.
4. **Postgres is the enforced canonical SQL dialect** — verified against heterogeneous targets: no reliable "generic SQL" gate exists (sqlglot is a parser/transpiler, not an engine validator; the target set is open-ended; multi-dialect parsing forces a lowest-common-denominator subset). So expressions must be valid Postgres; researchers adapt per target.
5. **ltree charset verified on PG 18.3 / ltree 1.3** — effective label set is `[A-Za-z0-9_-]` (hyphens allowed since PG16). All realistic identifiers pass (upper/lower, underscores, leading digits, hyphens, mixed case); only `$ @ % #` and whitespace fail. `validate_identifier_segment` is tightened to that whitelist so illegal identifiers fail at validation with a clear message rather than a cryptic cast error. Residual risk (accepted, documented): an identifier containing `$ @ % #` would be rejected and need a handling decision only if it ever appears.
6. **ltree adopted by editing `0001` + rebuilding, not by an ALTER migration** — confirmed by the user. A bootstrap-only exception, justified because the only live data (`sandbox.pagila`) is fully reproducible from YAML, and an ALTER-based type change across ~30 columns on 14 tables with FKs/indexes/PKs is materially riskier than a clean rebuild. This edit happens **before** checksum enforcement is baselined (Task 6.2), so it does not trip the #11 immutability guard. Alternative considered: a `0004_ids_to_ltree.sql` ALTER migration — rejected for execution risk during bootstrap. After the first real (non-sandbox) system lands, `0001` becomes immutable like any applied migration.
7. **Keep the hand-rolled migration runner; add checksums instead of adopting Alembic** — `apply_ddl.py` + numbered SQL + `schema_versions` is already Flyway's model. Alembic's headline features don't fit: autogen needs SQLAlchemy models (none exist; would create a second source of truth against the reviewable `.sql`), downgrades are explicitly rejected (append-only), branching is unneeded. The one genuine gap — enforcing that applied migrations are never edited — is closed cheaply with a stored per-migration hash.
8. **Schema-before-code ordering** — migrations (Tasks 1–6) are applied to the local Postgres before loader code depending on the new schema runs, mirroring the `check_schema_in_sync` drift guard. The rebuild in Task 6 is the synchronization point.
