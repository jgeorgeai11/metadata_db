---
name: 20260727v01_remediate_system_review_findings
goal: Remediate the findings of the 2026-07-27 whole-system review of metadata_db — missing DB-level backstop constraints, a validation hole that loads concepts under phantom anchors, SQL-shape and freeform-field gaps, loader-runtime parity and guard defects, an inoperative auto-revert path (no git identity), and a CI credential/workflow model that would fail or over-privilege on activation. CI is not yet activated, so all `.gitlab-ci.yml` changes land dormant; DDL changes edit `0001_initial_schema.sql` directly under the pre-launch bootstrap exception (no `0002`). The cwd-relative `sys.path.insert` shim is explicitly out of scope.
created: 2026-07-27 12:05:42
updated: 2026-07-27 12:12:48
---

## Implementation Plan

### Phase 1 — DB-level backstops and apply_ddl hardening

1. [completed] Add the missing backstop constraints and platform guard to the initial schema - `code/apply_ddl/ddl/0001_initial_schema.sql`
   - 1.1. Hierarchy-consistency CHECKs matching the one that already exists on `deployment_tables`: `schemas` (`data_source_id = subltree(schema_id, 0, 1)`), `tables` (`schema_id = subltree(table_id, 0, 2)`), `columns` (`table_id = subltree(column_id, 0, 3)`) — closing the gap where the DDL's own stated principle ("a row assembled by any non-loader route cannot disagree") is enforced on one table but not the three it depends on
   - 1.2. Leaf-name redundancy CHECKs on the same tables: `schema_name`/`table_name`/`column_name` must equal the id's last label (`subpath(<id>, -1, 1)::text`)
   - 1.3. Lowercase-identity CHECKs: each main-table ltree id equals `lower(<id>::text)`, and the three `physical_*_name` text columns in `deployment_tables` are lowercase — the §4 identity invariant and the plain-equality physical-address uniqueness both silently break on a case-variant manual insert without this
   - 1.4. Replace `idx_load_audit_loaded_ts` with a UNIQUE index — the documented lineage join (`load_audit.loaded_ts = <row>.update_ts/insert_ts/end_ts`) and the hstry correlation assume one audit row per timestamp; make the assumption an enforced error instead of silent join fan-out
   - 1.5. Unordered-pair uniqueness on `table_relationships`: a unique expression index on `(LEAST(table_a_id, table_b_id), GREATEST(table_a_id, table_b_id), relationship_name)` so both orientations of the same pair+name cannot coexist (the invariant the reverse-cardinality reading depends on, currently loader-only)
   - 1.6. `concepts.concept_id` shape CHECK: `nlevel(concept_id) in (3, 4)` and the second-to-last label equals the reserved `concept` segment
   - 1.7. `update_reason` pairing CHECK on every authored main table: `(update_reason is null) = (insert_ts = update_ts)` — the same loader-managed pairing the DDL already backstops for `validated`/`validated_ts`
   - 1.8. A version-assert `DO` block at the top of the migration raising a clear error when `server_version_num` < 160000 — the corpus charset permits hyphens in ltree labels, which PostgreSQL supports only from 16; today this fails months later, mid-load, with a raw ltree syntax error
   - 1.9. All new CHECKs are main-table only; the `*_hstry` mirrors deliberately stay constraint-free per their existing in-place comments
   - 1.10. Update the DDL's DB-level-backstops commentary to enumerate the new constraints and the rationale for each

2. [completed] Harden migration handling and the sync-check contract - `code/apply_ddl/apply_ddl.py`
   - 2.1. Refuse to apply a migration file containing top-level transaction-control statements (`COMMIT`, `BEGIN`, `ROLLBACK`, `START TRANSACTION`) — an embedded `COMMIT;` currently splits the file's atomicity silently, leaving half a migration applied with no ledger row; strip SQL comments before scanning and document the string-literal false-positive limitation
   - 2.2. Extend discovery beyond the case-sensitive `glob("*.sql")`: a file in `ddl/` whose extension is a case-variant of `.sql` (`.SQL`, `.Sql`) is an error naming the required lowercase spelling, not a silent skip (mirrors the corpus wrong-extension guard)
   - 2.3. Add a repeatable `--allow-pending <filename>` flag honored by `--check`: listed migrations may be repo-present-but-unapplied without failing the check — this is what lets a migration MR's own pipeline pass (the CI job passes the MR's newly added migration filenames; see Task 21.4), resolving the deadlock where the MR introducing `0002` can never satisfy `check_schema_in_sync`
   - 2.4. Distinguish "ddl_versions absent" from "no privilege to see it" in `--check` (`to_regclass` returns NULL for both): probe schema USAGE separately and emit a permissions message instead of misreporting all migrations as pending

3. [completed] Create and run tests for the DDL and apply_ddl changes - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 3.1. Static DDL invariants: each new CHECK/unique index from Task 1 is present in `0001_initial_schema.sql` (textual pins, mirroring the existing constraint-name binding test); the version-assert block exists; the hstry mirrors gained no constraints
   - 3.2. Transaction-control guard: a migration containing `COMMIT;` (bare, and inside a comment — the latter passes) is refused before execution
   - 3.3. `.SQL`/`.Sql` files in the ddl dir produce the wrong-extension error; `.sql` files are unaffected
   - 3.4. `--check` with `--allow-pending` exempts exactly the named files and still fails on other pending migrations; the permissions-vs-absent message split
   - 3.5. Run with `uv run pytest code/apply_ddl/unit_tests/test_apply_ddl.py -v`

4. [completed] Create the read-only CI role grant script - `code/apply_ddl/grant_metadata_db_ci_ro.sql`
   - 4.1. New maintainer-run psql script (companion to `grant_metadata_db_ci.sql`, same `-v schema`/`-v database` conventions) establishing the `metadata_db_ci_ro` role's privileges: CONNECT on the database, USAGE on the schema, SELECT on all main tables and `ddl_versions` — no DML, no `_hstry` access
   - 4.2. Header documents the role's purpose: the identity MR-pipeline jobs run as (`validate_metadata_db`, `check_schema_in_sync`), so unreviewed branch code never holds write credentials (see Task 21.1)

5. [pending] Rebuild the local database against the edited 0001, re-grant, reload (run) — maintainer-run, out-of-band
   - 5.1. Schema-scoped rebuild per the maintenance runbook: as `metadata_db_maintainer`, `DROP SCHEMA prod CASCADE`, then `uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl.toml`
   - 5.2. Re-run `grant_metadata_db_ci.sql` and the new `grant_metadata_db_ci_ro.sql`
   - 5.3. Reload the corpus with the loader; confirm `ddl_versions` holds the re-checksummed 0001 and every new constraint accepted the live corpus (any violation is a real data defect to fix in the same MR)

### Phase 2 — Validation gate (close the green-pipeline-wrong-data paths)

6. [completed] Tighten wave-1 assembly field rules and suppression ordering - `code/load_metadata_db/corpus_assembly.py`
   - 6.1. Require `columns.data_type` to be a non-blank string (strip-checked like `description`) — `data_type: ""` currently loads a column documented with no type
   - 6.2. Reject whitespace-only values for the optional freeform fields (`notes`, `use_when`, `update_reason`, `label`): a value must be null or carry non-whitespace content, eliminating the two-spellings-of-absent (`""` vs null) ambiguity in stored rows and closing the `update_reason: ""` hole against rule 20 at the source
   - 6.3. In deployment expansion, validate an entry's explicit physical name BEFORE the wave-1-cascade suppression path can `continue` past it — a rejected table row currently hides an independent invalid physical name on the same deployment line, costing an extra fix round-trip
   - 6.4. Iterate discovered files in sorted order (see Task 12.2) so duplicate-PK "first occurrence" attribution and issue ordering are deterministic across machines

7. [completed] Create and run tests for the assembly tightening - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
   - 7.1. Blank/whitespace `data_type` rejected; proper strings pass
   - 7.2. Whitespace-only `notes`/`use_when`/`update_reason`/`label` rejected per row type; explicit null and real content still pass
   - 7.3. A deployment entry whose table row was wave-1-rejected AND whose physical name is invalid reports the physical-name issue (suppressing only the phantom unknown-table issue)
   - 7.4. Run with `uv run pytest code/load_metadata_db/unit_tests/test_corpus_assembly.py -v`

8. [completed] Add the concept-anchor rule and align wave-2/3 string semantics - `code/load_metadata_db/corpus_validation.py`
   - 8.1. New wave-2 check: every concept's anchor prefix (the labels before the reserved `concept` segment) must resolve to an existing `data_sources` id (1 label) or `schemas` id (2 labels) — today a `concepts.yaml` in a typo'd or phantom folder loads under a nonexistent anchor with a fully green pipeline, the one asymmetric hole in the referential net (every other file type in a phantom folder is caught by an FK check)
   - 8.2. `validate_update_reason` treats whitespace-only `update_reason` on an update as missing (stripped-string semantics, matching the rule 14/15/16 checks; assembly now rejects authored whitespace-only values per Task 6.2, so this guards rows arriving through any other path)
   - 8.3. Require every `join_condition` to reference at least one column (mirroring the existing `target_expression` minimum) — a self-relationship's `join_condition: "1 = 1"` currently passes because the endpoint-coverage check is gated on differing endpoints
   - 8.4. Number the new checks into the overview §5 rule sequence (doc side lands in Task 24)

9. [completed] Create and run tests for the validation additions - `code/load_metadata_db/unit_tests/test_corpus_validation.py`
   - 9.1. A concept anchored to a nonexistent data source, a nonexistent schema, and a system label each produce the anchor issue; data-source-level and schema-level concepts with valid anchors pass
   - 9.2. Whitespace-only `update_reason` on an update is flagged by rule 20; a real reason passes
   - 9.3. A zero-column-reference `join_condition` is rejected for both self- and distinct-endpoint relationships
   - 9.4. Run with `uv run pytest code/load_metadata_db/unit_tests/test_corpus_validation.py -v`

10. [completed] Close the statement-shape and volatility gaps - `code/load_metadata_db/sql_parsing.py`
    - 10.1. Extend the statement/navigation denylist with the DML/DDL statement node types sqlglot can emit (`Update`, `Insert`, `Delete`, `Merge`, `Create`, `Drop`, `Alter`, `Grant`, `TruncateTable`, and the generic `Command` fallback; verify exact class names against the pinned sqlglot) — verified during review: `update t set a.b.c.d = e.f.g.h` currently clears every guard and stores verbatim as a `target_expression`
    - 10.2. Add `age` to the volatile-function denylist (single-argument `age(ts)` is `now()`-dependent; the name-based denylist also rejects the immutable two-argument form — accepted, see Decisions #7)

11. [completed] Create and run tests for the SQL-shape changes - `code/load_metadata_db/unit_tests/test_sql_parsing.py`
    - 11.1. Full `UPDATE`, `DELETE`, `INSERT`, `MERGE`, `DROP`, and `GRANT` statements are rejected by the navigation check even when they contain qualified column references; existing expression forms still pass
    - 11.2. `age(x.y.z.ts)` flagged volatile in both expression contexts
    - 11.3. Run with `uv run pytest code/load_metadata_db/unit_tests/test_sql_parsing.py -v`

12. [completed] Add the ltree label-length cap and deterministic discovery - `code/load_metadata_db/yaml_discovery.py`
    - 12.1. `validate_identifier_segment` also rejects segments longer than 255 characters (the ltree label limit) so an over-long name fails wave 1 with a named file instead of dying at INSERT
    - 12.2. `discover_yaml_files` returns identities in sorted path order (the docstring already anticipates callers may need ordering; make it intrinsic)

13. [completed] Create and run tests for discovery changes - `code/load_metadata_db/unit_tests/test_yaml_discovery.py`
    - 13.1. A 256-character segment is rejected with the length message; 255 passes
    - 13.2. Discovery order is sorted and stable regardless of filesystem creation order
    - 13.3. Run with `uv run pytest code/load_metadata_db/unit_tests/test_yaml_discovery.py -v`

14. [completed] Run the loader in dry-run mode against the live corpus and DB (run)
    - 14.1. `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml --dry-run` from the repo root, after Tasks 6–13 land
    - 14.2. Confirm the live corpus passes the new rules (concept anchors, data_type, whitespace policy, join_condition references, label lengths); correct any newly flagged YAML in the same MR with `update_reason` per the change-lifecycle discipline

### Phase 3 — Loader runtime parity and guards

15. [completed] Fix orchestration parity, guard validation, and lock scoping - `code/load_metadata_db/load_metadata_db.py`
    - 15.1. Resolve the commit SHA before the diff in BOTH modes and pass it into `apply_diff` — dry-run currently skips `resolve_commit_sha` entirely, so a run that would fail SHA resolution (git absent, non-checkout deploy) passes its dry-run, breaking the module's own parity contract
    - 15.2. Validate `mass_delete_fraction` (float in [0, 1]) and `mass_delete_min_count` (non-negative int) at config load with a clear config error — a TOML string currently escapes as an unhandled `TypeError` traceback, and out-of-range values silently disable or over-trigger the guard
    - 15.3. Scope the single-writer advisory lock to the target database+schema pair (two-key `pg_try_advisory_xact_lock` with a stable hash as the second key) so loads against different schemas in one database stop spuriously excluding each other
    - 15.4. Handle the new `ValueError` config paths in `main()`'s exception arms (clean exit-1, no raw traceback)

16. [completed] Harden SHA resolution and insert semantics - `code/load_metadata_db/db_io.py`
    - 16.1. `apply_diff` accepts the resolved `commit_sha` as a parameter (resolution moves to the orchestrator per Task 15.1)
    - 16.2. `resolve_commit_sha` logs a WARNING when the local fallback path finds a dirty working tree (`git status --porcelain` non-empty) — a manual run with uncommitted YAML edits records a SHA whose tree does not contain the loaded content; the warning makes the lineage caveat visible without blocking legitimate local runs
    - 16.3. Bind `update_reason` as NULL in `_insert_params` regardless of the row value — the documented insert semantics currently hold only because `validate_update_reason` runs first in the sole call path; make `apply_diff` self-defending against future callers

17. [completed] Create and run tests for the orchestration fixes - `code/load_metadata_db/unit_tests/test_load_metadata_db.py`
    - 17.1. Dry-run invokes SHA resolution (failure surfaces in dry-run); mistyped and out-of-range mass-delete knobs produce the config error and a clean exit through `main()`
    - 17.2. The advisory-lock call carries the schema-scoped second key; different-schema configs produce different keys, same-config runs the same key
    - 17.3. Run with `uv run pytest code/load_metadata_db/unit_tests/test_load_metadata_db.py -v`

18. [completed] Create and run tests for the db_io changes - `code/load_metadata_db/unit_tests/test_db_io.py`
    - 18.1. `apply_diff` uses the passed SHA (no internal resolution); dirty-tree warning fires on porcelain output and stays silent on a clean tree; inserts bind `update_reason` NULL even when the row carries a value
    - 18.2. Run with `uv run pytest code/load_metadata_db/unit_tests/test_db_io.py -v`

### Phase 4 — Revert script and dormant CI configuration

19. [completed] Make the revert commit possible: explicit git identity - `code/revert_merge/revert_merge.py`
    - 19.1. Read `bot_name` and `bot_email` from the TOML config (required keys) and pass them as `-c user.name=… -c user.email=…` on the `git revert` invocation — `git revert` creates a commit, and no identity is configured anywhere today, so on a stock CI container the very first real incident fails at step 5 with nothing pushed and `main` left ahead of the DB; the entire auto-revert mechanism is currently inoperative
    - 19.2. Missing/blank identity keys are refused in the step-0 config sanity block (before any git operation), alongside the existing placeholder checks

20. [completed] Add the bot identity to the shipped revert config - `code/revert_merge/config/revert_merge.toml`
    - 20.1. `bot_name` / `bot_email` keys with `<...>` placeholder values (caught by the existing unfilled-placeholder guard until an operator fills them), commented with their purpose

21. [completed] Create and run tests for the revert identity - `code/revert_merge/unit_tests/test_revert_merge.py`
    - 21.1. The revert command argv carries both `-c` identity flags with the configured values; missing or placeholder identity refuses before any git call
    - 21.2. Run with `uv run pytest code/revert_merge/unit_tests/ -v`

22. [completed] Fix the CI credential model and workflow gaps (dormant; activates with `METADATA_DB_CI_ENABLED`) - `.gitlab-ci.yml`
    - 22.1. Split DB credentials by privilege: MR-pipeline jobs (`validate_metadata_db`, `check_schema_in_sync`) map their `POSTGRES_*` env vars from new unprotected+masked `POSTGRES_RO_*` variables holding the `metadata_db_ci_ro` role (Task 4); the post-merge `load_metadata_db` job keeps the write-role variables, now correctly markable protected (main-only) — today a single write-credential set is exposed to unreviewed branch code in every MR, and the file's own "mark protected" instruction would instead break both MR jobs on activation (protected variables are not injected into MR pipelines)
    - 22.2. Correct the REQUIRED-VARIABLES header to the two-roles model, stating which set is protected and why the RO set cannot be
    - 22.3. Add a main-branch unit-test job triggered by `code/**` changes so a loader-code break surfaces at its own merge instead of detonating under (and auto-reverting) the next innocent data MR
    - 22.4. `check_schema_in_sync` computes the MR's own newly added migration files (diff against the merge-request diff base) and passes them via `--allow-pending` (Task 2.3) so migration MRs can pass their own pipeline
    - 22.5. Extend the activation-checklist comment: fill `revert_merge.toml` (URL template + bot identity), create the `metadata_db_ci_ro` role and run its grant script, set the project merge method to merge commits (squash/fast-forward silently disables the revert's 2-parent precondition), and set the two variable sets with their protection flags

### Phase 5 — Integration coverage for the new backstops

23. [completed] Extend the integration suite to prove the DB backstops - `code/load_metadata_db/unit_tests/test_integration.py`
    - 23.1. Direct (non-loader) INSERTs violating each new constraint class are rejected by Postgres: mismatched hierarchy prefix, wrong leaf name, uppercase id, duplicate `loaded_ts`, reverse-orientation relationship pair, malformed `concept_id`, `update_reason` set on a fresh insert
    - 23.2. A normal loader run against the rebuilt schema still passes every constraint (regression proof that the loader's write path satisfies its own backstops)
    - 23.3. Run with `METADATA_DB_INTEGRATION=1 uv run pytest -m integration code/load_metadata_db/unit_tests/test_integration.py -v`

### Phase 6 — Documentation

24. [completed] Update the overview doc - `readme/metadata-db-overview.md`
    - 24.1. §5 rule additions/changes: concept anchor existence, `data_type` non-blank, whitespace-only freeform values rejected, `join_condition` minimum column reference, DML/DDL statement rejection in M-rules, `age` on the volatile denylist, the 255-character label cap
    - 24.2. DB-level backstops inventory: the new CHECKs, the UNIQUE `loaded_ts`, and the unordered-pair index, each with its one-line rationale
    - 24.3. State the PostgreSQL 16+ minimum (hyphenated ltree labels) as a platform requirement
    - 24.4. Renumber/cross-reference so every rule keeps one enforcing function named in the text

25. [completed] Update the maintenance doc - `readme/metadata-db-maintenance.md`
    - 25.1. Correct the merge-serialization claim: "Pipelines must succeed" does not gate on another MR's post-merge pipeline, and merged-results pipelines are a Premium feature — on Free/CE a second MR can merge while a load runs; document the resulting safe-but-manual state (revert refuses on HEAD mismatch) and its recovery runbook
    - 25.2. Operational runbook additions: a cancelled (not failed) load leaves `main` ahead of the DB with no revert and no red pipeline — check for this after any manual cancellation; never click Retry on a failed load job once its revert has landed (a transient-failure retry would load a corpus `main` has since reverted) — re-run a fresh pipeline on `main` instead
    - 25.3. Project-settings requirements: merge method must be merge commits (the revert precondition requires 2-parent commits); fork MRs receive no pipeline (the activation variable does not exist in forks) — with "pipelines must succeed" enforced they are simply unmergeable, which is the intended posture for this internal repo
    - 25.4. Document the two-role CI credential model (Task 22.1), the `grant_metadata_db_ci_ro.sql` script, and the migration-MR `--allow-pending` flow
    - 25.5. Document the revert bot's config-supplied git identity and the extended activation checklist

## Key Data Decisions and Considerations

1. **Bootstrap exception, again** — All DDL changes edit `0001_initial_schema.sql` directly with a maintainer drop/recreate (Task 5), per the user's direction that the DB is still pre-launch WIP; the checksum guard makes the rebuild mandatory, exactly as in activity 20260725v01. Once a production consumer exists, equivalent changes become numbered migrations.
2. **Scope exclusions** — The cwd-relative `sys.path.insert` shim stays excluded (user direction; tracked separately, as in the prior activity). CI is dormant behind `METADATA_DB_CI_ENABLED`, so every `.gitlab-ci.yml` change here lands without activation risk and is exercised only by the extended activation checklist.
3. **Backstop philosophy line** — The review's organizing finding was that the DDL enforces its non-loader-writer principle on `deployment_tables` but nowhere else. Phase 1 draws the line at declarative, single-table constraints: hierarchy prefixes, leaf names, lowercase, pairings, shapes, uniqueness. Deliberately NOT added: cross-table anchor FKs for concepts (variable depth — wave 2 owns it, Task 8.1), the systems/data_sources label-disjointness rule (needs a trigger; loader-owned per existing docs), and `end_ts` indexes on the nine hstry tables (the "what did load X change" query stays a scan at current catalog scale; revisit if history auditing becomes routine).
4. **`update_reason` pairing CHECK (Task 1.7) is a judgment call** — The §5 backstop inventory omits it without rationale while backstopping the exactly analogous `validated` pairing; with the DB pre-launch and a rebuild already required, adding it is nearly free. If any legitimate loader path violates it (none known — inserts bind NULL per Task 16.3, updates require a reason per rule 20), the integration suite (Task 23.2) will surface that before it can bite.
5. **Concept anchor check is wave 2, not a DB FK** — The anchor is a variable-depth ltree prefix (data source or schema), which plain FKs cannot express; the declarative shape CHECK (Task 1.6) covers what the DB can say cheaply, and the new wave-2 rule covers existence with a proper authored-file error message.
6. **Whitespace/empty-string policy hardens to reject-at-source** — The prior activity normalized wave-2 consumers to strip semantics but left `""` loadable. This activity rejects whitespace-only freeform values in wave 1 (Task 6.2): stored data gains a single spelling of "absent" (NULL), and `update_reason: ""` can no longer satisfy rule 20. Authored non-blank values still load exactly as written (never trimmed).
7. **Name-based volatility over-rejection accepted** — Adding `age` rejects the immutable two-argument form along with the volatile single-argument form. The denylist has always been name-based (same trade-off as `version` vs a hypothetical column named `version` — resolved by qualification rules); documenting the constraint costs less than argument-arity analysis.
8. **Statement-shape fix is contract hygiene, not sandboxing** — Stored expressions are never executed by this codebase; rejecting `UPDATE`/`DELETE`/etc. (Task 10.1) protects the "column-level expression" contract and any downstream consumer that templates stored text into runnable SQL. Class names must be verified against the pinned sqlglot version at implementation time (the review verified `exp.Update` exists and currently passes).
9. **Dry-run parity via parameter injection** — Moving SHA resolution to the orchestrator (Tasks 15.1/16.1) makes dry-run exercise the same failure surface as a real run without writing anything, and simplifies `apply_diff`'s contract. The dirty-tree condition warns rather than fails: local manual runs against a scratch DB with uncommitted YAML are a legitimate workflow; the warning plus `load_audit`'s SHA make the caveat auditable.
10. **Advisory-lock scoping uses the two-key form** — `(LOADER_LOCK_KEY, hash(database, schema))` keeps a stable, documented first key for observability (`pg_locks` filtering) while separating schema targets. Over-serialization was safe but wrong; under-serialization is impossible since the key derives from the same config the connection does.
11. **CI credential model: read-only exposure is the accepted residual** — MR jobs must touch the DB (the dry-run's update_reason discipline reads current rows; the sync check reads `ddl_versions`), so unreviewed code inevitably runs holding *some* credential. The design reduces that credential to SELECT on catalog metadata (low sensitivity) via `metadata_db_ci_ro`, and confines the write role to protected-ref pipelines. This implements the maintenance doc's stated-but-unimplemented identity split.
12. **Migration-MR deadlock resolved in code, not process** — `--allow-pending` (Tasks 2.3/22.4) keeps `check_schema_in_sync` strict for every MR except the one introducing a migration, which may exempt only its own files (computed from the MR diff, not author-supplied). The alternative — applying DDL from unmerged branches — contradicts the review-then-apply ordering the docs mandate.
13. **Platform assumptions become enforced or documented** — PG 16+ is asserted in the DDL (fail at apply, not months later mid-load); the merge-commit requirement, Free/CE serialization reality, fork-MR posture, cancellation gap, and retry hazard are documentation/runbook items (Tasks 25.1–25.3) because GitLab Free offers no mechanical enforcement for them.
14. **Deliberately not fixed, with reasons** — Symlinked corpus files misclassify under their target (unsupported input; loud duplicate-identity failure, wrong attribution only); `CREATE INDEX CONCURRENTLY`-class non-transactional DDL has no escape hatch (irrelevant at current scale; revisit with a real migration workflow); git stderr redaction continues to rely on git's URL anonymization (pinned modern image; existing in-code comment records the risk); the deploys-nowhere double-issue on a broken `deployments.yaml` stays (both issues name the same file).
15. **New rules may flag live corpus content** — Task 14 is the gate, as in the prior activity: any live YAML newly rejected (concept anchors are the likely candidates given the finding) is corrected in the same MR so `main` never fails to validate itself. Task 5.3 plays the same role for the new DB constraints against reloaded data.
16. **Ordering/dependencies** — Task 5 (rebuild) requires Tasks 1–4 and precedes Task 23 (integration proves the new constraints). Task 22.4 depends on Task 2.3; Task 22.1 depends on Task 4. Task 8.2 depends on Task 6.2 only conceptually (each stands alone). Phase 6 lands with or after the code it describes. Suggested MR split: MR-1 = Phase 1 + Task 23 (maintainer-applied rebuild), MR-2 = Phase 2, MR-3 = Phase 3, MR-4 = Phase 4 + Phase 6.

17. **Run-task status (implementation note, 2026-07-27)** — Verified as far as privileges allow:
    - **Task 14 (live dry-run) — DONE.** `load_metadata_db.py --dry-run` against the live corpus + DB exits 0: corpus validation reports *no issues* over the 69-file corpus (3 systems, 11 data sources, 26 tables, 165 columns, 15 relationships, 12 mappings, 7 concepts), so the new rules (concept anchors, `data_type` non-blank, whitespace policy, `join_condition` minimum column refs, 255-char label cap) accept the live corpus unchanged — nothing to correct. The run also exercised the new dry-run SHA resolution (the dirty-tree WARNING fired, as expected with uncommitted changes) and the mass-delete config-knob validation.
    - **Connectivity + apply_ddl logic** — a read-only `apply_ddl.py --check` against the live server correctly reports the expected append-only/checksum violation on `0001` (the edit that mandates the rebuild), proving the new apply_ddl code end-to-end.
    - **Task 23 (integration) — code done, execution blocked.** The new tests collect cleanly (17 collected). Executing them (`METADATA_DB_INTEGRATION=1`) fails at the `integration_db` fixture with `must be owner of database metadata_db_integration`: the throwaway DB is owned by the maintainer role, so the drop/recreate needs those credentials. Environment/privilege block, not a code defect.
    - **Task 5 (prod rebuild) — blocked, maintainer-only.** Requires `metadata_db_maintainer` (`DROP SCHEMA prod CASCADE` + re-grant + reload), destructive and out-of-band per Decision #1/#16.
    - All *code, test, and documentation* substeps of Phases 1–6 are implemented; the 809-test non-integration suite passes. Run-only substeps left for the maintainer applying MR-1: 5.1–5.3 (prod rebuild + re-grant + reload) and 23.3 (integration execution with maintainer creds).
