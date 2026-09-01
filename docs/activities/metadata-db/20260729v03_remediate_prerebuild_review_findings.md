---
name: 20260729v03_remediate_prerebuild_review_findings
goal: Remediate every finding from the 2026-07-29 pre-rebuild review of the ref-tables implementation and repo-wide rename — the deferrable-FK phase-ordering defect on `columns.ref_table_id`, the untracked `logconfig` CI activation blocker, ref-pipeline gaps (future-table grants, one-directional drift detection, new-table MR deadlock, hash provenance, BOM handling, audit-table shape), CODEOWNERS and CI trigger gaps, and documentation stragglers — then perform the full local rebuild (catalog + ref schemas, grants, ref load, first catalog load). DDL fixes ride the rebuild's bootstrap exception, so they cost no migration ceremony.
created: 2026-07-29 19:06:36
updated: 2026-07-29 19:41:03
---

## Implementation Plan

### Phase 1 — DDL and loader fixes that must precede the rebuild

1. [completed] Make `ref_table_id` deferrable and name its constraint - `code/apply_ddl/ddl_catalog/0001_initial_schema.sql`
   - 1.1. `columns.ref_table_id` FK becomes a NAMED constraint (`columns_ref_table_id_fkey`) declared `deferrable initially immediate`, with a comment explaining the phase-ordering rationale (an in-place UPDATE may point at a `tables` row inserted later in the same load transaction; the constraint still enforces at COMMIT) — mirroring the deployment physical-address precedent

2. [completed] Defer the new FK in the load transaction - `code/load_catalog_data/db_io.py`
   - 2.1. A named-constant for `columns_ref_table_id_fkey` (with the must-match-DDL note) and its `SET CONSTRAINTS ... DEFERRED` issued in `apply_diff` step 0 alongside the deployment-address deferral

3. [completed] Create and run tests for the deferral - `code/load_catalog_data/unit_tests/test_db_io.py`
   - 3.1. The constraint-name constant is bound to the DDL text (mirroring the existing deployment-constraint binding test); `apply_diff` issues both SET CONSTRAINTS statements; a link-to-new-table diff (columns UPDATE + tables INSERT) is exercised at the statement-sequence level
   - 3.2. Run with `uv run pytest code/load_catalog_data/unit_tests/test_db_io.py -v`

4. [completed] Give `ref_load_audit` a primary key - `code/apply_ddl/ddl_ref/0001_ref_initial.sql`
   - 4.1. Add a `bigint generated always as identity` PK to `ref_load_audit` so audit rows are individually addressable and the latest-row query has a deterministic tiebreaker (`order by loaded_ts desc, audit_id desc`)

5. [completed] Create and run tests for the DDL changes - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 5.1. The hstry no-constraints invariant also asserts no `references` in mirror bodies (closes the FK-absence gap); static pins for the named deferrable `columns_ref_table_id_fkey` and the `ref_load_audit` identity PK
   - 5.2. Run with `uv run pytest code/apply_ddl/unit_tests/test_apply_ddl.py -v`

### Phase 2 — Vendor logconfig (CI activation blocker + the old cwd-relative shim)

6. [completed] Vendor the logging module and replace every entry-point shim - `code/lib/logconfig/` plus the sys.path shim lines across `code/load_catalog_data/`, `code/load_ref_data/`, `code/apply_ddl/`, `code/revert_merge/`, `code/bootstrap/`
   - 6.1. Copy the `logconfig` package from `.claude/skills/python-development/scripts/logconfig/` into `code/lib/logconfig/` (tracked); the `.claude` copy remains for the skill's own use but the repo's runtime never touches it again
   - 6.2. Replace every `sys.path.insert(0, ".claude/skills/python-development/scripts")` across entry points and modules (load_catalog_data, load_ref_data, apply_ddl, revert_merge incl. git_ops/preconditions, bootstrap generators and their data_validation scripts) with a repo-root-derived insertion (`Path(__file__).resolve().parents[N] / "code" / "lib"`) — killing both the untracked-dependency CI blocker and the run-from-repo-root-only fragility in one change
   - 6.3. Full test suite green from the repo root AND from a different cwd (spot-check one entry point per module)

### Phase 3 — Ref-pipeline gaps

7. [completed] Fix drift direction, hash provenance, BOM, check messages, and the audit-PK tiebreaker query - `code/load_ref_data/load_ref_data.py`
   - 7.1. Bidirectional drift: `validate_all` and `--check` report a loud issue for every ref-schema table (infra tables excluded) with no matching `data_ref/*.csv`, and for every documented ref-source table with no CSV — restoring the docs == CSV == DDL guarantee in both directions
   - 7.2. Audit-hash provenance: hash the exact bytes read for validation/loading (single read), eliminating the second-read TOCTOU; align hash normalization with load semantics (hash the raw bytes, not universal-newline text)
   - 7.3. Read CSVs as `utf-8-sig` so Excel-saved BOMs don't produce cryptic header mismatches
   - 7.4. The freshness query adopts the audit PK tiebreaker (`order by loaded_ts desc, audit_id desc`)
   - 7.5. `--check`'s never-loaded message names the actual remedy (apply migration if the table is missing, else run the loader); rollback-failure paths preserve the original exception as the raised one
   - 7.6. New-table MR escape: `--dry-run` gains a repeatable `--allow-missing-table <name>` flag that downgrades that table's missing-from-DB error to a warning (validating everything else about its CSV), for MRs whose ref migration is applied post-merge

8. [completed] Create and run tests for the ref-loader fixes - `code/load_ref_data/unit_tests/test_load_ref_data.py`
   - 8.1. DB-table-without-CSV and documented-table-without-CSV both flagged in load and `--check` paths; hash equals hash-of-loaded-bytes (mutate file between hypothetical reads via mock to prove single-read); BOM CSV parses; `--allow-missing-table` downgrades only the named table; multi-CSV accumulation and multi-table single-commit paths covered
   - 8.2. Run with `uv run pytest code/load_ref_data/unit_tests/ -v`

9. [completed] Cover future tables in the ref grant script - `code/apply_ddl/grant_ref_ro.sql`
   - 9.1. Replace the per-table grant list with `GRANT SELECT ON ALL TABLES IN SCHEMA` plus `ALTER DEFAULT PRIVILEGES IN SCHEMA ref ... GRANT SELECT ON TABLES` for the three read roles, so new ref tables are readable without editing the script; header updated accordingly

### Phase 4 — CI and CODEOWNERS

10. [completed] Extend the ref-stream CI wiring - `.gitlab-ci.yml`
   - 10.1. `validate_ref_data` triggers also on `data_catalog/sources/ref/**` and `code/apply_ddl/ddl_ref/**` (the other legs of the consistency gate)
   - 10.2. The job computes MR-added CSVs (`git diff --diff-filter=A` against the MR base over `data_ref/`) and passes each stem via `--allow-missing-table`, unblocking new-table MRs
   - 10.3. Activation-checklist comment gains the logconfig note removal (no longer a blocker once Phase 2 lands) and the default-privileges grant behavior

11. [completed] Close the CODEOWNERS gaps - `.gitlab/CODEOWNERS`
    - 11.1. Per-source rules for `data_catalog/sources/ocs/` and `data_catalog/sources/ref/`, and a rule for `data_ref/` (values route to the ref steward with the docs)
    - 11.2. Dual-owner rules for the six `ocs/*/mappings/edwc_prd.yaml` files (source + target steward), replacing the stale "no mapping files currently in the corpus" note

### Phase 5 — Documentation and housekeeping

12. [completed] Fix the maintenance doc stragglers - `readme/metadata-db-maintenance.md`
    - 12.1. Layout-section `ddl/` -> `ddl_catalog/` (both lines), add `ddl_ref/` + `apply_ddl_ref.toml` + grant scripts to the tree
    - 12.2. Ref runbook: state that ref tables' own key columns are NOT self-linked via `ref_table` (the column is the enumeration); note the default-privileges grant behavior (no per-table script edits); document the `--allow-missing-table` MR flow
    - 12.3. Record the logconfig vendoring (runtime dependency now `code/lib/logconfig/`, tracked)

13. [completed] Fix the stale design-doc path - `docs/design/table-relationship-validation-rules.md`
    - 13.1. `data/systems/{system}/{db}/{schema}/...` -> the current `data_catalog/sources/{source}/{schema}/...` layout

14. [completed] Fix the overview doc example ids - `readme/metadata-db-overview.md`
    - 14.1. Replace the nonexistent `sandbox_ocs.general.*` example ids and the sample deployment query with real `ocs` ids that resolve against the current corpus

15. [completed] Local housekeeping (run, not committed)
    - 15.1. Delete the untracked old-name shells: `code/load_metadata_db/`, `code/generate_corpus_from_infoschema/`, `code/generate_ocs_corpus/` (pycache remnants), `logs/load_metadata_db/`
    - 15.2. Remove the two dead permission entries in `.claude/settings.local.json` (stale paths) — local file, maintainer-owned

### Phase 6 — Full local rebuild and first load (run)

16. [completed] Rebuild and load the local instance end to end (run) — maintainer-run
    - 16.1. Drop the obsolete `prod` schema; apply `apply_ddl_catalog.toml` (creates `catalog` with the deferrable FK), run `grant_catalog_ci.sql` + `grant_catalog_ci_ro.sql`
    - 16.2. Apply `apply_ddl_ref.toml` (creates `ref` with the audited PK), run the ref loader (317 rows into `ref.clm_type_cd` + audit row), run `grant_ref_ro.sql`
    - 16.3. Run the catalog loader: first load into the empty `catalog` schema; verify counts match the remote first load (~26,000 inserts; ~1,000 tables, ~22,800 columns, ~1,300 mappings, 139 relationships, 18 concepts, 118 ref_table links) and `load_audit` records the current HEAD
    - 16.4. Full test suite + a post-load dry-run showing an empty diff

## Key Data Decisions and Considerations

1. **Fix everything now, rebuild once** — Maintainer direction (2026-07-29). The DDL fixes (deferrable FK, audit PK) are free only while the rebuild is pending (bootstrap exception; re-checksummed by the rebuild); deferring the rest would just split one remediation into two MRs with no benefit.
2. **Deferrable FK over reordering phases** — `ref_table_id` is the catalog's first mutable non-PK FK, so the deletes->updates->inserts phase order can violate it mid-transaction on legal end-states (link columns to a same-MR ref table: UPDATE before the referenced INSERT). Deferring the named constraint to COMMIT (precedent: the deployment physical-address UNIQUE) is surgical; reordering phases would perturb invariants every other table type depends on. The failure it prevents is the worst kind: green pre-merge dry-run, post-merge load failure, auto-revert of an innocent MR once CI is live.
3. **Vendoring logconfig closes two issues at once** — The untracked `.claude/` runtime dependency (100% CI-job failure on activation) and the long-deferred cwd-relative `sys.path` fragility (excluded from activities v01/v02 per maintainer direction, now explicitly in scope). Repo-root-derived path insertion keeps invocation working from any cwd without packaging changes.
4. **Bidirectional drift restores the ref guarantee** — Decision #7 of the ref activity claimed docs == CSV == DDL is mechanically guaranteed; CSV-driven iteration made it one-directional. Reporting DB-without-CSV and docs-without-CSV closes the loop; the guarantee statement in the maintenance doc becomes true as written.
5. **`--allow-missing-table` mirrors `--allow-pending`** — Same shape as the schema-sync escape: CI computes the exemption list from the MR's own diff (added CSVs), so authors cannot exempt arbitrary tables, and the validation of everything else in the CSV still runs pre-merge.
6. **Default privileges over per-table grants** — `ALTER DEFAULT PRIVILEGES` makes new ref tables readable at creation with no script edit; the runbook step the review found missing becomes unnecessary rather than documented.
7. **`ref_load_audit` PK is an identity column** — Order-by tiebreaker and row addressability; the table stays append-only and thin. Not a `(table_name, loaded_ts)` composite because two runs in one transaction timestamp would collide — the identity column is immune.
8. **Self-linking policy stated, not enforced** — Ref tables' own key columns don't get `ref_table` self-loops (the column IS the enumeration); a validation rule against self-links would be more machinery than the one-sentence convention warrants. Revisit if authors actually do it.
9. **Deliberately not fixed** — The float/int parser-parity edges in the ref loader (late-but-loud failures, never corruption); the concurrent-ref-load `loaded_ts` residual (single-maintainer operation, documented as identical-inputs-safe); `validate_ref_data` remains RO-role and reads the live DDL (acceptable: the escape hatch covers the only false-failure case). Recorded here so the next review doesn't re-litigate them.
10. **Rebuild verification target** — The remote first load (activity 20260729v01 Task 23) recorded exact per-table counts; the local rebuild must reproduce them (same corpus commit), making count equality a real cross-instance verification rather than a formality.
11. **Ordering and MR split** — Tasks 1-5 (DDL + deferral + tests) and Task 6 (logconfig) are independent of each other; Tasks 7-9 depend on Task 4's audit PK; Task 10.2 depends on Task 7's `--allow-missing-table`; Task 16 (rebuild) requires everything in Phases 1-3 and is the gate that re-checksums both edited 0001 files. Suggested MR split: MR-1 = Phases 1-3 (code), MR-2 = Phases 4-5 (CI/docs); Tasks 15-16 are local maintainer runs, not committed.
12. **Implementation notes (2026-07-29, Tasks 1-14)** — (a) `grant_ref_ro.sql`'s default-privileges statement is explicitly scoped `for role metadata_db_maintainer` (default privileges attach to the creating role, and all ref DDL is maintainer-applied); (b) `compute_csv_sha256` now takes bytes and the old line-ending-stable hashing is deliberately gone per 7.2 — a CRLF re-save of an unchanged CSV will read as "stale" once, resolved by one reload; (c) `load_tables` lost its `csv_hashes` parameter (the hash rides inside `loadable` from the single `read_csv` read); (d) `check_freshness` now takes the live ref-table set so its never-loaded message can name the right remedy; (e) `--allow-missing-table` is rejected outside `--dry-run` (a real load or `--check` must never skip the missing-table error); (f) the `validate_ref_data` CI job gained `GIT_DEPTH: 0` for its diff-base computation; (g) the overview's sample ids/queries now use `ocs.carr.*` and `puf_clfs.general.clfs_2026`, which resolve against the current corpus and co-deploy in `warehouse`. Full suite green after Tasks 1-14: 1,052 passed, 17 skipped (env-gated integration).
13. **Tasks 15-16 executed locally (2026-07-29)** — Old-name shells and stale log dirs deleted; the two dead settings.local.json permission entries removed. Rebuild: `prod` dropped, `catalog` applied with the named deferrable `columns_ref_table_id_fkey`, both catalog grant scripts run; `ref` applied (audit PK), ref loader loaded 317 rows + 1 audit row, `grant_ref_ro.sql` set schema-wide + default privileges. First catalog load as the CI role: ~26,000 inserts at HEAD 53d6caf, per-table counts identical to the remote first load (~1,000 / ~22,800 / ~1,300 / 139 / 18 / ~1,000; 118 ref_table links); post-load dry-run diff empty; full suite 1,052 passed. The dirty-tree lineage warning fired as designed (remediation code uncommitted at load time; corpus content unchanged from HEAD, so the recorded SHA correctly describes the loaded data).
