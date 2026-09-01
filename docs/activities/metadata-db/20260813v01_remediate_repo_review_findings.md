---
name: 20260813v01_remediate_repo_review_findings
goal: Close the findings from the 2026-08-13 whole-repo review with behavior-preserving cleanups — deduplicate the triplicated `connection_kwargs` / schema-name validation into a shared `code/lib` package, replace fragile numbered CONTRIBUTING.md rule citations in code comments with stable named references, drop the unused `corpus` parameter from `validate_update_reason`, and replace activity-task-number comments with self-contained rationale. No schema, corpus, or DB change; a fifth review finding (splitting `corpus_assembly.py`) was examined and deliberately dropped (Decision 4).
created: 2026-08-13 10:22:13
updated: 2026-08-13 11:25:04
---

## Implementation Plan

### Phase 1 — Shared Postgres connection helper

1. [completed] Create the shared connection package - `code/lib/pgconn/pgconn.py`
   - 1.1. New vendored-style package beside `logconfig` (`code/lib/pgconn/` with an `__init__.py` re-exporting the public names), resolvable through the `sys.path.insert(... / "code" / "lib")` preamble all three consumers already carry
   - 1.2. Move in the three duplicated definitions: `ENV_VARS` (the four `POSTGRES_*` names), `SCHEMA_NAME_RE` (`[a-z_][a-z0-9_]*`), and `connection_kwargs(database, schema)` including its `load_dotenv()` call and `options=-c search_path=<schema>` construction
   - 1.3. Keep the exception contract all three copies share: `ValueError` on a schema failing `SCHEMA_NAME_RE`, `RuntimeError` naming every missing env var; adopt `db_io.py`'s error text (the richest — it explains the search_path interpolation and lowercase folding) as the single message
   - 1.4. Docstring records why the schema restriction exists (option-injection surface + unquoted search_path case folding), merging the rationale currently split across the three copies; full type hints and Google-style docstrings per the `python-development` core skills

2. [completed] Create and run tests for pgconn - `code/lib/pgconn/unit_tests/test_pgconn.py`
   - 2.1. Cover: happy path builds the six-key kwargs mapping from env; each missing env var is named in the `RuntimeError`; invalid schemas (`uppercase`, leading digit, embedded space/quote, empty) raise `ValueError`; `.env` values are read via `load_dotenv`
   - 2.2. Add a `conftest.py` with the `sys.path` shim if pytest's rootdir handling needs it (mirror the pattern in the other `unit_tests/` folders); pytest patterns per the `python-development` unit-tests core skill
   - 2.3. Run `uv run --locked pytest code/lib/ -v --cov=pgconn --cov-report=term-missing`; bar is 100% on the new module

3. [completed] Route apply_ddl through pgconn - `code/apply_ddl/apply_ddl.py`
   - 3.1. Delete the local `ENV_VARS`, `SCHEMA_NAME_RE`, and `connection_kwargs`; import from `pgconn`
   - 3.2. `create_database_if_absent` and every other caller keep their exact call shape; no other line of the module changes

4. [completed] Update and run apply_ddl tests - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 4.1. Repoint `connection_kwargs` tests/patches at the `pgconn` import (patch where it is looked up, not where it is defined — module-level `from pgconn import connection_kwargs` keeps existing `apply_ddl.connection_kwargs` patch targets working); drop cases that now duplicate Task 2's direct coverage, keep any asserting apply_ddl-specific behavior
   - 4.2. Update any assertion pinned to apply_ddl's old short error message (`Invalid schema name: ...`) to the unified message
   - 4.3. Run `uv run --locked pytest code/apply_ddl/ -v --cov --cov-report=term-missing`

5. [completed] Route the loader through pgconn - `code/load_catalog_data/db_io.py`
   - 5.1. Delete the local `_SCHEMA_RE` and `connection_kwargs` and the `ENV_VARS` tuple; import from `pgconn`
   - 5.2. `resolve_commit_sha`, `read_db_state`, `apply_diff`, and the module's SQL constants are untouched

6. [completed] Update and run db_io tests - `code/load_catalog_data/unit_tests/test_db_io.py`
   - 6.1. Same repointing rule as Task 4.1; the unified message is already db_io's, so message assertions should survive unchanged
   - 6.2. Two sibling suites consume `connection_kwargs` through `db_io` and need no edits, but must be confirmed: `test_integration.py` imports it directly (`from db_io import connection_kwargs`, valid via the module-level re-export — and pytest collects the file even when its env gate skips the tests, so a broken import would fail collection) and `test_load_catalog_data.py`'s env fixture references it
   - 6.3. Run `uv run --locked pytest code/load_catalog_data/ -v --cov --cov-report=term-missing` — proves collection of both suites above alongside the db_io cases

7. [completed] Route the ref loader through pgconn - `code/load_ref_data/load_ref_data.py`
   - 7.1. Delete the local `ENV_VARS`, `SCHEMA_NAME_RE`, and `connection_kwargs`; import from `pgconn`; everything else untouched
   - 7.2. The deleted docstring's "kept local so the ref module stays self-contained" rationale is consciously overridden — see Decision 3; nothing else in the module changes

8. [completed] Update and run load_ref_data tests - `code/load_ref_data/unit_tests/test_load_ref_data.py`
   - 8.1. Same repointing rule as Task 4.1; update assertions pinned to the old middle-length error message
   - 8.2. Run `uv run --locked pytest code/load_ref_data/ -v --cov --cov-report=term-missing`

### Phase 2 — Reference and signature hygiene

9. [completed] Give every validation rule a stable name - `CONTRIBUTING.md`
   - 9.1. In the "What gets validated" section, lead each of the 21 numbered rules with a short bold name (e.g. `2. **Lowercase identifiers** — …`, `7. **Deployment file rules** — …`, `20. **update_reason discipline** — …`, `21. **Mass-delete guard** — …`); numbers stay for reading order, names become the cross-reference key
   - 9.2. Names must be unique and grep-able; where a rule is already effectively named (7, 20, 21), keep that wording as the official name

10. [completed] Repoint rule citations in the assembler - `code/load_catalog_data/corpus_assembly.py`
    - 10.1. Replace the ~4 `rule 7` / `rule 20` citations in comments and docstrings with the rule's name plus wave (e.g. `CONTRIBUTING.md rule 7` → `the deployment-file rules (CONTRIBUTING.md wave 1)`)
    - 10.2. Comment/docstring-only edits; no behavior change

11. [completed] Repoint rule citations and rewrite the task-number comment - `code/load_catalog_data/corpus_validation.py`
    - 11.1. Replace the ~3 `rule N` / `rules 14/15/16` citations with name-plus-wave references, same rule as Task 10.1
    - 11.2. Rewrite the `(Task 6.2)` comment (line ~972) to state the fact itself — assembly rejects authored whitespace-only freeform values, so this diff-time check guards rows arriving by other routes — with no activity-plan pointer

12. [completed] Repoint rule citations in the orchestrator - `code/load_catalog_data/load_catalog_data.py`
    - 12.1. Replace the ~3 citations (`rule 20`, `rule 21`, `Rule 20 before rule 21`) with named references (`update_reason discipline`, `the mass-delete guard`), same rule as Task 10.1

13. [completed] Repoint the rules-range reference in the offline checker - `code/load_catalog_data/check_corpus.py`
    - 13.1. Replace the docstring's `rules 1-19` with the wave-named equivalent (`waves 1 and 2 — every file-shape and corpus-validation rule`)

14. [completed] Sweep rule and task-number comments in the loader test suites - `code/load_catalog_data/unit_tests/`
    - 14.1. One mechanical rule applied across the sibling suites (grouped for that reason): in `test_corpus_assembly.py`, `test_corpus_validation.py`, `test_db_io.py`, `test_load_catalog_data.py`, `test_yaml_discovery.py`, and `test_sql_parsing.py`, replace `rule N` citations per Task 10.1 and each `(Task N.N)` section marker with the behavior it pins (e.g. `# Task 12.1` → `# ltree label-length cap fails wave 1 with a named file and segment`)
    - 14.2. Comment-only edits; run `uv run --locked pytest code/load_catalog_data/ -q` to confirm collection and the suite still pass

15. [completed] Sweep task-number comments in the DDL test suite - `code/apply_ddl/unit_tests/test_apply_ddl.py`
    - 15.1. Replace the `(Task 1.N)` markers with the constraint family each pins (hierarchy-consistency CHECKs, leaf-name redundancy CHECKs, lowercase-identity CHECKs, …), same rewrite rule as Task 14.1
    - 15.2. Comment-only edits; run `uv run --locked pytest code/apply_ddl/ -q`

16. [completed] Drop the unused corpus parameter from validate_update_reason - `code/load_catalog_data/corpus_validation.py`
    - 16.1. Signature becomes `validate_update_reason(diff)`; delete the docstring's "retained for signature stability" rationale and its `corpus` Args entry

17. [completed] Update the validate_update_reason call site - `code/load_catalog_data/load_catalog_data.py`
    - 17.1. `run` step 6 calls `validate_update_reason(diff)`; update the function docstring's step list to match

18. [completed] Update and run the validation tests for the new signature - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
    - 18.1. Update every `validate_update_reason` case to the one-argument call
    - 18.2. Run `uv run --locked pytest code/load_catalog_data/unit_tests/test_corpus_validation.py -v --cov=corpus_validation --cov-report=term-missing`

19. [completed] Update and run the diff tests for the new signature - `code/load_catalog_data/unit_tests/test_corpus_diff.py`
    - 19.1. The suite exercises `validate_update_reason` against computed diffs (two-argument call at ~line 282, plus the import); update to the one-argument call
    - 19.2. Run `uv run --locked pytest code/load_catalog_data/unit_tests/test_corpus_diff.py -v --cov=corpus_diff --cov-report=term-missing`

20. [completed] Update and run the orchestrator tests for the new signature - `code/load_catalog_data/unit_tests/test_load_catalog_data.py`
    - 20.1. Update any mock or assertion that passes or expects the old two-argument call
    - 20.2. Run `uv run --locked pytest code/load_catalog_data/unit_tests/test_load_catalog_data.py -v --cov=load_catalog_data --cov-report=term-missing`

### Phase 3 — Verify and review

21. [completed] Whole-repo regression verification - `code/`
    - 21.1. Full suite at the coverage bar: `uv run --locked pytest code/ -v --cov --cov-report=term-missing` — expect ≥ the current 1017 passed / 19 skipped, all touched source files at their pre-change coverage (95–100%)
    - 21.2. Offline corpus check: `uv run --locked code/load_catalog_data/check_corpus.py` — expect exit 0 with the same object counts as before (3 data sources, 963 tables, 26,799 columns)
    - 21.3. Loader dry-run against the live DB: `uv run --locked code/load_catalog_data/load_catalog_data.py --config code/load_catalog_data/config/load_catalog_data.toml --dry-run` — expect a diff summary identical to the pre-change baseline recorded 2026-08-13 (`Diff: 0 insert(s), 0 update(s), 0 delete(s)`, DB reachable and in sync). If the DB has moved since the baseline, re-record the baseline on main first and compare against that — the check is before/after parity, not emptiness (see Decision 7)

22. [completed] Code review of changed files and address findings - `docs/code_review/`
    - 22.1. Run the `code-review` skill against the changed/new code files (`pgconn.py`, `apply_ddl.py`, `db_io.py`, `load_ref_data.py`, `corpus_assembly.py`, `corpus_validation.py`, `load_catalog_data.py`, `check_corpus.py`) and the new/logic-changed test files (`test_pgconn.py` plus the suites updated in Tasks 4, 6, 8, 18, 19, 20 — the comment-only sweeps of Tasks 14–15 need no review), writing `cr_20260813v01_*.md` per the existing layout
    - 22.2. Address findings via the `code-implementation` skill; re-run Task 21.1

## Key Data Decisions and Considerations

1. **One activity, not four** — every finding is a behavior-preserving cleanup on the same codebase with a shared verification story (green suite + baseline-parity dry-run), and the repo's precedent bundles review remediation this way (`20260725v01_remediate_audit_findings`, `20260729v03_remediate_prerebuild_review_findings`). Phase order puts the structural refactor (Phase 1) before the comment sweep (Phase 2) so the sweep edits final file contents and never has to be redone after code moves.

2. **Behavior-preserving, so tests are the proof** — no assembled id, issue string, SQL statement, or exception type may change (the one deliberate exception: the unified `connection_kwargs` error text, Decision 3). The existing suites assert exact messages and exact ids, so a green run with only import/patch-target changes is the correctness guarantee, backed by Task 21.3's baseline-parity dry-run. No `0001` edit, no DB rebuild, no corpus reload.

3. **`pgconn` lives in `code/lib` beside `logconfig`, and the richest error message wins** — all three consumers already insert `code/lib` on `sys.path`, so the package is importable with zero new path plumbing, and the vendored-package pattern is established. The three copies are functionally identical (same regex, same env vars, same kwargs); they differ only in error-message wording, and `db_io.py`'s version is adopted because it explains *why* (search_path interpolation, unquoted-identifier case folding). Consequence: a few `apply_ddl`/`load_ref_data` test assertions pinned to the shorter messages must be updated — that is the visible extent of the behavior change, and it is message-text only. This consciously overrides `load_ref_data`'s recorded "kept local so the ref module stays self-contained" rationale: that comment guards against importing from another *tool's* module (`apply_ddl`), which would couple two peers; `code/lib` is the sanctioned shared layer, exactly as `logconfig` already demonstrates. Rejected alternative: a `code/common/` package importable as a real package — a larger layout change than this finding warrants, and it would break the flat-script import convention every module follows.

4. **The corpus_assembly split was examined and dropped (2026-08-13)** — the original review suggested extracting the deployment-expansion section (~lines 1282–1805) into its own module. Closer analysis reversed that: (a) the section is one half of a two-pass mechanism, not a free-standing concern — `_WaveOneRejections` is populated during `assemble_corpus`'s first pass (`_record_file_failure`, the tables branch) and consumed during the expansion pass, so a split separates the producer and consumer of the same suppression state; (b) it would fragment the single `_RECOGNIZED_KEYS` registry and the shared shape helpers (`_require_list`, `_check_recognized_keys`), forcing either relocation or an import-cycle workaround — a "clean split" that needs cycle management isn't clean; (c) the module's 2,157 lines are inflated by the repo's ~42% documentation density (roughly 1,100 code lines), it sits at 100% coverage, and deployment expansion is a finished feature — file growth comes from new file types, not this section. Navigability payoff small, churn (public-name promotion, byte-preserving moves inside the 2,815-line test file, blame noise) real. Do not relitigate without new evidence, e.g. the section actually resuming growth.

5. **Rule names, not renumbering** — CONTRIBUTING.md keeps its numbered list (readers navigate by it), but each rule gains a bold name and code comments cite the name + wave instead of the number. What names buy is insertion-safety: inserting a new rule renumbers everything below it silently, while breaking no name. The coupling is reduced, not eliminated — a *renamed* rule still drifts from its citations — so names should be treated as stable identifiers once assigned. The 18 existing `rule N` citations were verified accurate on 2026-08-13, so this is pure future-proofing; no citation is currently wrong.

6. **`validate_update_reason(corpus, diff)` → `(diff)`** — the `corpus` argument has never been read (the check deliberately reads rows off the diff so it sees exactly what will be written); "signature stability" protects no external caller because the function is internal to the loader with exactly one call site. Dropping it removes a standing invitation to use stale corpus state in future edits.

7. **Task 21.3's check is baseline parity, not emptiness** — "expect an empty diff" would silently assume the DB is loaded to head whenever the work happens to run. A baseline was recorded on 2026-08-13 (DB reachable, `Diff: 0 insert(s), 0 update(s), 0 delete(s)`), and the task compares against the recorded baseline; if the DB or corpus has moved since, re-record on main and compare — the refactor is proven by the diff being *unchanged*, not by it being zero. If Postgres is unreachable when the work runs, skip 21.3, note it here, and run it manually before the branch merges: a passing result is the loader exiting `SUCCESS` with a diff summary matching the main-branch baseline. Tasks 21.1–21.2 need no database and always run.

8. **Test-comment provenance is deliberately dropped, not relocated** — the `(Task N.N)` markers point into activity files that describe *when* a behavior was added, not *what* it is; the replacement comments state the pinned behavior itself, and history stays recoverable via `git blame` and `docs/activities/`. Tasks 14–15 are grouped by suite directory rather than one task per test file because they apply a single mechanical rewrite rule with no per-file logic (guideline: group when the concern is shared); no test logic changes — comment lines only.
9. **Task 21.1's test-count baseline shifted by design (2026-08-13)** — the "expect >= 1017 passed" figure predates Tasks 4.1/6.1/8.1 dropping the duplicated direct `connection_kwargs` cases: 31 parametrized items across `test_apply_ddl.py` (14), `test_db_io.py` (11), and `test_load_ref_data.py` (6) were superseded by the 19 direct pgconn cases, so the full run is 1005 passed / 19 skipped. Coverage parity holds on every touched source file: `apply_ddl.py` 99% (same two pre-existing gaps), `db_io.py` 99% (same except-branch gap), `corpus_validation.py` 99% (same single gap), `load_ref_data.py` 95% (same 18 lines), `corpus_assembly.py` / `load_catalog_data.py` 100%, `pgconn.py` 100%.

10. **Task 21.2/21.3 baseline parity confirmed (2026-08-13)** — `check_corpus.py` exited 0 with the baseline counts (3 data sources, 963 tables, 26,799 columns), and the live-DB dry-run exited SUCCESS with `Diff: 0 insert(s), 0 update(s), 0 delete(s)`, identical to the recorded 2026-08-13 baseline.

11. **Task 22 remains for the orchestrator (2026-08-13)** — the `code-review` skill runs as its own agent, which the implementation pass cannot spawn; Task 22 stays `[pending]` until the orchestrator runs the review over the changed files (`pgconn.py`, `apply_ddl.py`, `db_io.py`, `load_ref_data.py`, `corpus_assembly.py`, `corpus_validation.py`, `load_catalog_data.py`, `check_corpus.py`, `test_pgconn.py`, and the suites updated in Tasks 4/6/8/18/19/20) and routes findings back through `code-implementation`.

12. **Task 22 completed (2026-08-13)** — 15 files reviewed in 7 grouped `code-review` workers, `cr_20260813v01_*.md` written per the existing layout: 0 critical, 1 major, 17 minor pending (all other items deferred suggestions with recorded rationale). All pending findings were implemented via 7 `code-implementation` workers. Highlights: the major was `check_corpus.py` lacking any test suite — `test_check_corpus.py` now covers it at 100%, and its `--data-root` flag was replaced by `--config` reading `data_root` from the loader's TOML (removing the parallel-default drift hazard) while keeping the zero-argument run CONTRIBUTING.md advertises; the best catch was in `pgconn.py`, where `load_dotenv()`'s default `interpolate=True` expanded `${VAR}` inside `.env` values, contradicting the docstring's "secrets containing `$` survive intact" claim inherited from the pre-dedup copies — fixed with `interpolate=False` (verified empirically) plus a set-but-empty env-var boundary test; `test_load_ref_data.py` closed its three uncovered error paths (95% → 100%). Post-fix verification: full suite 1029 passed / 19 skipped (up from 1005 by the 24 new tests), `check_corpus.py` exit 0 at baseline counts, live-DB dry-run `Diff: 0 insert(s), 0 update(s), 0 delete(s)` — baseline parity holds.
