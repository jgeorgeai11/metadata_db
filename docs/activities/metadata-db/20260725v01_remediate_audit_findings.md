---
name: 20260725v01_remediate_audit_findings
goal: Remediate the findings of the 2026-07-25 documentation-vs-code audit of metadata_db — validation-gate holes that let bad YAML past the pre-merge dry-run, phantom-error cascades, CI/CODEOWNERS/revert-script defects, DDL comment and index corrections, test coverage gaps, and documentation drift. The untracked `logconfig` module (and its cwd-relative `sys.path` shim) is explicitly out of scope and handled separately.
created: 2026-07-25 08:39:23
updated: 2026-07-25 13:17:49
---

## Implementation Plan

### Phase A — Loader validation gate (dry-run parity and error quality)

1. [completed] Harden wave-1 assembly: field typing, cascade suppression, lowercase placement, merge keys - `code/load_metadata_db/corpus_assembly.py`
   - 1.1. Type-check every optional/freeform field read via bare `raw.get(...)` — `notes`, `use_when`, `update_reason`, `label` (all row types) — rejecting any non-null, non-string value as an assembly issue naming the file, row identity, key, and the actual YAML type (e.g. `update_reason: 2024-01-01` parses as a date and currently passes all validation, failing only inside the post-merge write transaction)
   - 1.2. Suppress phantom deployment cascades: when a `tables.yaml`/`schema.yaml` row was already rejected in this wave, do not also emit "unknown table/schema" (or "expands to zero rows" / "deploys nowhere" where the reduced inventory is the sole cause) for `deployments.yaml` entries referencing it — one defect, one issue
   - 1.3. Move the physical-name lowercase check from wave 2 (`corpus_validation.py:289-299`) into wave-1 deployments assembly, alongside the existing explicit/non-null checks, so it surfaces in the same report as other authoring-shape issues (overview §5 rule 7 already documents it as wave 1)
   - 1.4. Reject YAML merge keys (`<<:`) in `_UniqueKeyLoader` with a clear message naming the file and stating merge keys are unsupported and fields must be spelled out (currently fails with a cryptic "could not determine a constructor for the tag 'tag:yaml.org,2002:merge'")

2. [completed] Create and run tests for wave-1 hardening - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
   - 2.1. Non-string `notes`/`use_when`/`update_reason`/`label` (int, bool, YAML date) rejected per row type; explicit `null` and proper strings still pass
   - 2.2. A single bad `tables.yaml` row produces exactly one issue — no companion deployment issues (cover the "unknown table", "expands to zero rows", and "deploys nowhere" variants)
   - 2.3. Uppercase physical name is now a wave-1 issue (and no longer double-reported from wave 2)
   - 2.4. A file using `<<:` fails with the new merge-key message
   - 2.5. Run with `uv run pytest code/load_metadata_db/unit_tests/test_corpus_assembly.py -v`

3. [completed] Fix wave-2 validation inconsistencies - `code/load_metadata_db/corpus_validation.py`
   - 3.1. Guard `_check_mapping_linkability` against expressions referencing undocumented tables (skip like `_check_mapping_codeployment` at line 612 already does) so a typo'd table yields only "unknown column", not an additional phantom "not all linkable" issue
   - 3.2. Correct `_check_mapping_codeployment`'s docstring (lines 602-604): a single-table expression is NOT trivially deployed — a documented table excluded by every exhaustive deployments map deploys nowhere and is rejected (behavior is correct and intentionally stricter than overview rule 19; keep it, document it in Task 25)
   - 3.3. Tighten the rule 14/15/16 "non-null" checks (`use_when`, intentional-drop `notes`) to stripped-string semantics: whitespace-only values are treated as missing (non-string values are now impossible per Task 1.1)
   - 3.4. Remove the wave-2 physical-name lowercase check moved to wave 1 by Task 1.3 (physical-address uniqueness stays in wave 2)

4. [completed] Create and run tests for wave-2 fixes - `code/load_metadata_db/unit_tests/test_corpus_validation.py`
   - 4.1. Typo'd table in a multi-table `target_expression` produces the unknown-column issue only — no linkability issue
   - 4.2. Single-table mapping whose target table is documented but deployed nowhere is rejected (pins the previously untested branch)
   - 4.3. Whitespace-only `use_when` and whitespace-only drop-`notes` are rejected
   - 4.4. Run with `uv run pytest code/load_metadata_db/unit_tests/test_corpus_validation.py -v`

5. [completed] Surface wrong-extension YAML files instead of silently ignoring them - `code/load_metadata_db/yaml_discovery.py`
   - 5.1. During discovery, any file at a recognized corpus location whose name matches a recognized stem but carries a `.yml` or case-variant (`.YAML`, `.Yml`, …) extension becomes a wave-1 issue naming the file and the required `.yaml` spelling — closing the silent data-loss channel where a mis-extensioned `concepts.yml` yields a green pipeline and delete-by-absence removes previously loaded rows
   - 5.2. Files that are not YAML at all (e.g. `.md`, `.txt`) under `data/` remain ignored as today

6. [completed] Create and run tests for extension handling - `code/load_metadata_db/unit_tests/test_yaml_discovery.py`
   - 6.1. `concepts.yml`, `tables.YAML`, and a mappings-file `.yml` each produce the wave-1 issue; `.md`/`.txt` stay ignored; update the existing `test_discover_yaml_files_ignores_stray_yml` to the new contract
   - 6.2. Run with `uv run pytest code/load_metadata_db/unit_tests/test_yaml_discovery.py -v`

7. [completed] Extend the volatile-function denylist - `code/load_metadata_db/sql_parsing.py`
   - 7.1. Add `current_database()`, `current_catalog`, `current_role`, `pg_sleep()`, and `setseed()` to the M3 denylist, handling functions sqlglot parses to dedicated node classes (e.g. `exp.CurrentDatabase`) that name-matching cannot catch
   - 7.2. Keep the denylist stance (anything not forbidden passes) and the explicit `AT TIME ZONE '<zone>'` allowance unchanged

8. [completed] Create and run tests for SQL-shape changes - `code/load_metadata_db/unit_tests/test_sql_parsing.py`
   - 8.1. Each newly denylisted function rejected in both `target_expression` and `join_condition` contexts; extend the volatile-canary test to pin the new node classes against sqlglot drift
   - 8.2. Add `>`, `>=`, `<=` to the boolean-predicate-root parametrize list (documented family, previously untested)
   - 8.3. Run with `uv run pytest code/load_metadata_db/unit_tests/test_sql_parsing.py -v`

### Phase B — Loader orchestration and write path

9. [completed] Fix orchestration-level gate and ordering defects - `code/load_metadata_db/load_metadata_db.py`
   - 9.1. Run the `update_reason` discipline check (rule 20) BEFORE the mass-delete guard (rule 21), matching overview §5 ordering, so a tripped guard no longer hides the accumulated update_reason report
   - 9.2. Enforce the `METADATA_DB_ALLOW_RESET_HSTRY=1` gate on `--reset-hstry` in dry-run as well as real runs (currently `if reset_hstry and not dry_run` at line 134 silently accepts the flag in dry-run), mirroring `--allow-mass-delete`'s both-modes behavior

10. [completed] Create and run tests for orchestration fixes - `code/load_metadata_db/unit_tests/test_load_metadata_db.py`
    - 10.1. When both conditions hold, the update_reason `ValidationError` surfaces (or at minimum is computed and reported) rather than being masked by `MassDeleteError`; pin the documented ordering
    - 10.2. `--dry-run --reset-hstry` without the env var exits with the refusal message
    - 10.3. Run with `uv run pytest code/load_metadata_db/unit_tests/test_load_metadata_db.py -v`

11. [completed] Harden db_io: commit-SHA resolution, connection options, stale comment - `code/load_metadata_db/db_io.py`
    - 11.1. Pin `git rev-parse HEAD` (line 124-128) to the repository root (derive from the module's own location, not the process cwd) and add a subprocess `timeout` — a loader run from another directory currently records the wrong repo's HEAD in `load_audit.commit_sha`, corrupting drift detection and lineage
    - 11.2. Validate the `schema` config knob before interpolating it into `options=-c search_path={schema}` (line 103): restrict to a lowercase `[a-z0-9_]+` pattern and raise a clear config error otherwise (removes the option-injection surface and the mixed-case-schema failure mode)
    - 11.3. Fix the stale "Resolve before opening the transaction" comment (line ~881; the transaction is already open since the advisory-lock statement)

12. [completed] Create and run tests for db_io hardening - `code/load_metadata_db/unit_tests/test_db_io.py`
    - 12.1. `resolve_commit_sha` uses the pinned repo-root cwd (assert the subprocess call's `cwd`), handles whitespace-only `CI_COMMIT_SHA` via the strip-then-fallback branch, and honors the timeout
    - 12.2. Invalid `schema` values (uppercase, spaces, quotes, empty) raise the config error; `prod` passes
    - 12.3. Add a test binding `DEPLOYMENT_TABLES_PHYSICAL_ADDRESS_CONSTRAINT` to the constraint name in `code/apply_ddl/ddl/0001_initial_schema.sql` (read the DDL text; mirrors `test_pk_agreement`'s pattern) so a future rename fails a unit test, not every load
    - 12.4. Run with `uv run pytest code/load_metadata_db/unit_tests/test_db_io.py -v`

13. [completed] Fix stale comments in the loader config - `code/load_metadata_db/config/load_metadata_db.toml`
    - 13.1. The data-root comment describes the layout as "the `systems/` corpus tree"; correct to `systems.yaml` + `sources/`
    - 13.2. The mass-delete comment's example is "decommissioning a system"; correct to "decommissioning a data source"

14. [completed] Run the loader in dry-run mode against the live corpus and DB (run)
    - 14.1. `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml --dry-run` from the repo root, after Tasks 1–13 land
    - 14.2. Confirm the existing corpus still validates cleanly under the new rules (field typing, extension checks, whitespace semantics); if any live YAML rows are newly flagged, correct them in the same MR (with `update_reason` per the change-lifecycle discipline)

### Phase C — CI pipeline, CODEOWNERS, revert script

15. [completed] Rewrite CODEOWNERS for the current venue-free layout - `.gitlab/CODEOWNERS`
    - 15.1. Replace the stale pre-restructure rules (`data/systems/warehouse/OCS/…` — matches nothing on disk) with the documented sketch adapted to the real corpus: catch-all and plumbing rules (`code/`, `.gitlab/`, `.gitlab-ci.yml`, `readme/`, `data/systems.yaml`, plus the existing `docs/` rule) to `@metadata-db-maintainers`; a `data/` root rule; one rule per existing data-source folder (`data/sources/sandbox_ocs/`, `data/sources/sandbox_edw/`, `data/sources/pagila/`, each `puf_*` source) naming its steward team consistent with each `data_source.yaml` `owner`
    - 15.2. Dual-owner rules for each `mappings/*.yaml` (source-side + target-side teams), using the post-Task-27 filenames

16. [completed] Fix the CI pipeline - `.gitlab-ci.yml`
    - 16.1. Scope `revert_failed_load` with the same `changes: [data/**/*.yaml]` rule as `load_metadata_db` (today, any main-branch commit not touching corpus YAML makes pipeline creation fail: the revert job `needs:` a job that isn't in the pipeline — latent while `METADATA_DB_CI_ENABLED` gates the workflow, fatal the day CI activates)
    - 16.2. Add a `validate`-stage unit-test job running `uv run pytest code/` on every MR (integration tests stay excluded — they are env-gated by `METADATA_DB_INTEGRATION`)
    - 16.3. Align the job image with the project's `requires-python >=3.14` / `.python-version` (currently `python:3.12-slim`, forcing uv to download a managed 3.14 per job or fail on a restricted runner)
    - 16.4. Set `GIT_DEPTH: 0` (or an explicit unshallow) on `revert_failed_load` so `git rev-list --parents` and the revert never operate on a shallow history

17. [completed] Harden the revert script's git operations - `code/revert_merge/git_ops.py`
    - 17.1. Stop persisting the token in `.git/config`: replace `git remote set-url origin https://oauth2:<token>@…` with per-command credential injection (e.g. `-c http.extraHeader=` or a one-shot credential helper), or at minimum reset the remote URL in a `finally` — on a reused runner workspace the token currently outlives the job
    - 17.2. Make checkout robust on reused checkouts: `git checkout -B main origin/main` (or fetch + `reset --hard origin/main`) so a stale local `main` cannot make the precondition check refuse a legitimate revert
    - 17.3. Fix `set_authenticated_remote`'s docstring (lines 152-154): a template missing the `{token}` placeholder formats silently to an unauthenticated URL (no `KeyError`); only a wrong placeholder name raises
    - 17.4. Note in a comment that `run_git`'s verbatim stderr logging on failure depends on git's URL-credential anonymization; keep argv redaction as is

18. [completed] Add a remote-URL template sanity check - `code/revert_merge/revert_merge.py`
    - 18.1. Refuse with a clear message, before any git operation, if the configured remote URL still contains an unfilled placeholder such as `<group>` (the shipped `revert_merge.toml` holds one)

19. [completed] Create and run tests for revert-script hardening - `code/revert_merge/unit_tests/test_git_ops.py`
    - 19.1. Token never appears in any logged string or in the post-run remote URL (assert the cleanup/injection path); stale-local-`main` scenario resolves to `origin/main`'s tip; missing-placeholder template behavior matches the corrected docstring
    - 19.2. Update `test_revert_merge.py` for the Task 18 refusal (unfilled placeholder exits non-zero without touching git) and any signature changes; update `test_preconditions.py` only if signatures changed
    - 19.3. Run with `uv run pytest code/revert_merge/unit_tests/ -v`

### Phase D — DDL and apply_ddl

20. [completed] Pre-launch corrections to the initial schema (bootstrap exception — see Decisions #5) - `code/apply_ddl/ddl/0001_initial_schema.sql`
    - 20.1. Correct the misleading comment on the `target_tables_referenced` GiST index (lines 458-462): `gist__ltree_ops` serves ltree-vs-lquery operators, not generic anyarray `@>`/`<@`/`&&` — align its wording with the (correct) concepts-index comment at lines 472-474
    - 20.2. Drop `idx_column_mappings_source_column` (line 433) — a leading-prefix duplicate of the PK index, the exact redundancy the DDL's own comment at lines 478-482 argues against
    - 20.3. Remove the `comment on table ddl_versions` statement (lines 560-561) — `ddl_versions` is created by `apply_ddl.py`, not this migration, so the comment makes 0001 unapplyable through any other path; the comment text moves to `apply_ddl.py` in Task 21.2
    - 20.4. Add a btree index on `load_audit (loaded_ts)` — backs both documented query patterns (the lineage join `load_audit.loaded_ts = <row>.update_ts` and the drift check's `ORDER BY loaded_ts DESC LIMIT 1`)

21. [completed] Tighten apply_ddl schema validation - `code/apply_ddl/apply_ddl.py`
    - 21.1. Restrict `SCHEMA_NAME_RE` (line 50) to lowercase `[a-z0-9_]+` — a mixed-case value currently creates a quoted `"Prod"` schema while libpq's unquoted `search_path` folds to `prod`, failing later with "no schema has been selected"; match the loader-side rule from Task 11.2
    - 21.2. Carry the `ddl_versions` table comment moved from 0001 (Task 20.3) into the `CREATE TABLE IF NOT EXISTS` bootstrap

22. [completed] Create and run tests closing the apply_ddl coverage gaps - `code/apply_ddl/unit_tests/test_apply_ddl.py`
    - 22.1. Static DDL invariants: 0001 contains no schema-qualified name and no `prod` literal (the documented schema-agnostic contract); the grant script's main/`_hstry` table lists agree with the DDL's table set (the dependency its own header declares)
    - 22.2. `--check` mode refuses on an unknown DB version (append-only rule, pre-merge CI path); argparse→`run()` plumbing for `--check` and `--create-db`; `verify_checksums` multi-violation message; `SCHEMA_NAME_RE` rejection paths (uppercase now included); CR-only newline normalization; `create_database_if_absent` error path; `ensure_ddl_versions` DDL pins `applied_ts timestamptz … default now()`
    - 22.3. Run with `uv run pytest code/apply_ddl/unit_tests/test_apply_ddl.py -v`

23. [completed] Rebuild the local database against the edited 0001, re-grant, reload (run) — maintainer-run, out-of-band
    - 23.1. Schema-scoped rebuild per the maintenance runbook: as `metadata_db_maintainer`, `DROP SCHEMA prod CASCADE`, then `uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl.toml`
    - 23.2. Re-run `psql -v schema=prod -v database=metadata_db -d metadata_db -U metadata_db_maintainer -f code/apply_ddl/grant_metadata_db_ci.sql`
    - 23.3. Reload the corpus with the loader; confirm `ddl_versions` has the re-checksummed 0001 and `load_audit` a fresh row

### Phase E — Integration test expansion

24. [completed] Expand the integration suite to cover the unproven contracts - `code/load_metadata_db/unit_tests/test_integration.py`
    - 24.1. Two-connection advisory-lock test: a second loader connection fails fast with the in-progress message while the first holds the lock; also assert a dry-run's held lock excludes a real run (documenting reader-blocks-writer)
    - 24.2. Clean-dry-run purity: after a no-change dry-run against the live DB, every table count and `load_audit` are unchanged
    - 24.3. Lineage join: after a real run, `load_audit.loaded_ts` equals the `insert_ts`/`update_ts` of rows written that run and the `end_ts` of rows superseded that run (the docs' headline lineage query returns the run's commit SHA)
    - 24.4. `--reset-hstry` against real Postgres: `_hstry` tables truncate inside the load transaction under maintainer credentials (pins the transactional-truncate claim; the CI role's lack of TRUNCATE is documented in Task 26, not tested here)
    - 24.5. Run with `METADATA_DB_INTEGRATION=1 uv run pytest -m integration code/load_metadata_db/unit_tests/test_integration.py -v`

### Phase F — Documentation, examples, packaging

25. [completed] Update the overview doc - `readme/metadata-db-overview.md`
    - 25.1. Fix the `target_tables_referenced` index claim (§ `column_mappings`, line ~208): anyarray containment is not served by `gist__ltree_ops`; state which operator forms are index-served (matching the corrected DDL comments) and that containment lineage queries scan at catalog scale
    - 25.2. Extend the documented M3 volatile denylist with the Task 7 additions
    - 25.3. Rule 19: document that co-deployment applies to single-table expressions too (a documented-but-nowhere-deployed target is rejected)
    - 25.4. Rule 5: document `mappings` as a reserved path segment alongside `concept`
    - 25.5. `validated_ts`: document insert-time stamping when a row arrives with `validated: true`, including the rename (delete+insert) consequence — the original timestamp is not carried across an identity change
    - 25.6. DB-level backstops: note the additional NOT NULLs the DDL enforces beyond the listed subset, and the supporting FK/id indexes 0001 creates beyond the four documented ones; correct `load_audit.reset_hstry` to note its default
    - 25.7. §5 wave-3: confirm the rule 20 → 21 ordering now matches code (Task 9.1)

26. [completed] Update the maintenance doc - `readme/metadata-db-maintenance.md`
    - 26.1. `ddl_versions` snippet: `applied_ts` is `timestamptz` (matching code)
    - 26.2. Dry-run section: it opens a read-only transaction and holds the single-writer advisory lock for its duration (a long dry-run fail-fasts a concurrent real load, and vice versa); remove the literal "no transaction is opened" claim
    - 26.3. `--reset-hstry`: gate applies in dry-run too (Task 9.2), and the flag requires maintainer credentials — `metadata_db_ci`'s INSERT-only `_hstry` grant does not include TRUNCATE
    - 26.4. Repo layout: show the loader's real module split (`yaml_discovery.py`, `corpus_assembly.py`, `corpus_validation.py`, `corpus_diff.py`, `sql_parsing.py`, `db_io.py`, `data_model.py`), `revert_merge`'s `preconditions.py`/`git_ops.py`, the `unit_tests/` folders, and `pyproject.toml`/`uv.lock`
    - 26.5. CI section: document the `METADATA_DB_CI_ENABLED` workflow gate as the dormancy mechanism, the new unit-test job, the revert job's `changes:` scoping and `GIT_DEPTH`, and the corrected pipeline sketch (Task 16)
    - 26.6. Roles: the `check_schema_in_sync` account also needs CONNECT on the database and USAGE on the schema (not "only SELECT on ddl_versions"); note the grant script's `\if` defaults and that `-v` values should always be passed explicitly
    - 26.7. File-extension rule: corpus files must be `.yaml` (lowercase); wrong extensions now fail wave 1 (Task 5)

27. [completed] Fix the example corpus and live mapping filenames - `readme/metadata-db-example-yamls/`
    - 27.1. Correct both field legends claiming `validated … required in YAML? yes` (`table_relationships.yaml:30`, `mappings/mart.yaml:33`) — it is optional, defaulting `false`
    - 27.2. Soften the example README's "every recognized field" claim or add a non-null relationship `use_when` demonstration (choose whichever keeps the corpus loader-valid and minimal)
    - 27.3. Rename the two live mapping files whose grouping label names their own source rather than the target — `data/sources/sandbox_ocs/general/mappings/sandbox_ocs.yaml` and `data/sources/sandbox_edw/claims_vw/mappings/sandbox_edw.yaml` — to their target dataset's label per the documented convention; verify with a loader `--dry-run` that the rename produces an empty diff (filenames are not stored)
    - 27.4. Keep CODEOWNERS (Task 15.2) in agreement with the final filenames

28. [completed] Fix packaging metadata - `pyproject.toml`
    - 28.1. Remove the `readme = "README.md"` field or point it at an existing file (no root `README.md` exists; docs live under `readme/`)

## Key Data Decisions and Considerations

1. **Scope exclusion** — The untracked `logconfig` module and the cwd-relative `sys.path.insert(".claude/skills/…")` shim in every script are excluded per the maintainer's direction and handled separately. Until that lands, all scripts must still be invoked from the repo root.
2. **Fix direction (code vs. docs)** — Where the docs state design intent, code moves to the docs: wave-3 ordering (rule 20 before 21), the `--reset-hstry` env gate in both modes, physical-name lowercase in wave 1. Where the code is right and the docs drifted, docs move to the code: `ddl_versions.applied_ts` as `timestamptz`, the NOT-NULL superset, single-table co-deployment rejection, the `mappings` reserved segment.
3. **Dry-run parity is the organizing principle for Phase A** — The design promises "everything fails the pre-merge dry-run exactly as it fails a real run." The freeform-field typing hole (a YAML date in `update_reason` passing validation and aborting the post-merge transaction, triggering the auto-revert bot) is the one finding that violates this outright, which is why Phase A leads.
4. **YAML merge keys are rejected, not supported** — Supporting `<<:` would require flattening before duplicate-key detection and reasoning about merged-row provenance in error messages; the corpus convention is explicit fields. A clear rejection message costs one check.
5. **Editing `0001_initial_schema.sql` is a pre-launch bootstrap exception** — Permitted by the maintenance doc's bootstrap note because the only data (sandbox corpora and PUFs) is reproducible from YAML; requires the Task 23 rebuild since the checksum guard will refuse the edited file against an existing `ddl_versions` row. Once a production consumer exists, equivalent changes go into numbered migrations.
6. **Revert-job scoping mirrors the load job's `changes:` rule** — Chosen over `needs: [{job: load_metadata_db, optional: true}]` because the revert job is meaningless in pipelines without a load job; mirroring keeps the two jobs' existence conditions identical by construction.
7. **Wrong-extension files become errors, not silently ignored** — A mis-extensioned `concepts.yml` currently yields a green pipeline while delete-by-absence removes previously loaded rows (bounded only by the mass-delete guard). Loud failure is the design's stated philosophy ("never silently ignored").
8. **`validated_ts` rename-restamp is documented, not changed** — Carrying a verification timestamp across a PK change would require identity tracking through delete+insert pairs, which the diff model deliberately lacks. The edge is documented (Task 25.5); re-verification after a rename is reasonable practice anyway.
9. **Token hygiene approach is implementer's choice within a hard constraint** — Per-command credential injection or a `finally` URL reset both satisfy the requirement; what is non-negotiable is that the token must not persist in `.git/config` after the job ends (reused-runner exposure).
10. **`load_audit (loaded_ts)` index added opportunistically** — Cheap, and both documented query patterns (lineage join, drift check) currently have no index on either side; bundled into the 0001 edit while the rebuild is already required.
11. **Whitespace-only semantics** — Assembly enforces string-or-null typing (never silently coercing or trimming authored values); validation treats whitespace-only `use_when`/drop-`notes` as missing. Stored values remain exactly as authored.
12. **New validation rules may flag existing corpus content** — Task 14's dry-run against the live corpus is the gate: any live YAML newly rejected by the Phase A rules (non-string fields, wrong extensions, whitespace-only values) is corrected in the same MR so the loader never lands in a state where `main` cannot validate itself.
13. **Mapping-file renames are git-only** — The mappings filename is a grouping convention the loader validates but does not store, so Task 27.3 must produce an empty loader diff; the dry-run check confirms it. CODEOWNERS dual-owner rules are written against the corrected names to avoid a second churn.
14. **Ordering/dependencies** — Task 15.2 depends on Task 27.3 (final mapping filenames). Task 23 (rebuild) must follow Tasks 20–21 and precede Task 24 (integration runs against the corrected schema). Phase F can proceed in parallel with Phases A–C, but Tasks 25.7 and 26.3/26.5 describe post-fix behavior, so they land with or after their code tasks. Suggested MR split: MR-1 = Phases A–B (Tasks 1–14), MR-2 = Phase C (15–19), MR-3 = Phase D (20–23, maintainer-applied), MR-4 = Phases E–F (24–28).
15. **CI Python alignment direction** — The image moves up to the project's required 3.14 rather than relaxing `requires-python` down: the codebase is already developed and tested on 3.14 locally, and pinning the image is the smaller change.
16. **Task 23 completed by the maintainer (2026-07-25)** — Schema-scoped rebuild ran per the runbook: `DROP SCHEMA prod CASCADE` (as `metadata_db_maintainer`), `apply_ddl.py` re-applied the edited `0001` (new checksum `c5d68674…` in `ddl_versions`), the grant script restored `metadata_db_ci`'s privileges, and the corpus reloaded as the CI role — `load_audit` row 1 records commit `62445ba` with 289 inserts, matching the nine table counts exactly (also confirming the Task 11 repo-root-pinned SHA resolution).
17. **Task 24 completed (2026-07-25)** — The full integration suite (9 tests) passes against the rebuilt schema under `claudedb_user` credentials (the module fixture drops/creates a throwaway `metadata_db_integration` DB, which needs the CREATEDB role, not just maintainer). First run surfaced a test-only deadlock in `test_reset_hstry_truncates_inside_load_transaction`: the non-autocommit verification connection's pre-reset `_count` read left an idle-in-transaction ACCESS SHARE lock on `tables_hstry`, blocking the loader's ACCESS EXCLUSIVE `TRUNCATE` indefinitely. Fixed with a `conn.rollback()` before the reset run; loader code was correct and untouched. The other tests are unaffected (plain DML loads take row-level locks only).
18. **Task 27.3 rename interpretation (implementation note, 2026-07-25)** — Both mis-labeled files hold *within-dataset* mappings, so their target dataset is the file's own `{db}.{schema}`; the only single-segment label that identifies that dataset (stems cannot contain dots, and repeating the data-source label is what the audit flagged) is the schema's name. Renamed `sandbox_ocs.yaml` → `general.yaml` and `sandbox_edw.yaml` → `claims_vw.yaml`; a live dry-run confirmed an empty diff (git-only rename, Decision #13), and CODEOWNERS (Task 15.2) was written against these final names.
19. **`current_role` volatile matching (implementation note, 2026-07-25)** — sqlglot 30.8 parses bare `current_role` under the postgres dialect as an unqualified `Column`, not a dedicated node or `Anonymous` call, so `find_volatile_functions` also matches completely unqualified column names against a context-keyword set; a fully qualified `db.schema.table.current_role` column is never flagged.
20. **Live corpus unaffected by the new rules (Task 14, 2026-07-25)** — The dry-run against the live corpus and DB passed cleanly under the Phase A rules (field typing, extension checks, whitespace semantics, wave-1 physical-name case) with an empty diff; no live YAML corrections were needed.
