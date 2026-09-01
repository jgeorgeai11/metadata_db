---
name: cr_20260813v01_test_db_io
goal: Re-review of code/load_catalog_data/unit_tests/test_db_io.py against python-development skills after the vendored-lib refactor (the connection_kwargs tests moved to code/lib/pgconn/unit_tests, dropping the suite from 86 to 75 tests); no new pending findings, standing suggestions carried from cr_20260812v01 plus two new optional refinements.
created: 2026-08-13 11:05:25
updated: 2026-08-13 11:05:25
---

## Implementation Plan

1. [completed] Carried optional refinements from cr_20260812v01 - `code/load_catalog_data/unit_tests/test_db_io.py`
   - 1.1. [suggestion] Lines 30 and 49: `_SHA = "testsha0000"` and the autouse `_stub_commit_sha` fixture's `monkeypatch.setenv("GITHUB_SHA", "testsha0000")` hardcode the same literal in two places. They serve different roles (the value handed to `apply_diff` vs. the ambient CI default), but the shared literal invites a reader to assume one derives from the other.
        - Current: `_SHA = "testsha0000"` (line 30) and `monkeypatch.setenv("GITHUB_SHA", "testsha0000")` (line 49)
        - Expected: `monkeypatch.setenv("GITHUB_SHA", _SHA)`, or a distinct fixture literal such as `"ambientsha0000"`
        - Resolution: Deferred — cosmetic; both sites carry comments explaining their separate roles, and `test_apply_diff_uses_passed_sha_and_does_not_resolve_internally` (line 626) and `test_apply_diff_writes_load_audit_row_with_counts` (line 1270) both pass explicit non-`_SHA` values, so the independence of the two paths is pinned by assertion rather than by naming. Matches cr_20260812v01 finding 2.1.
   - 1.2. [suggestion] Line 1144: `test_resolve_commit_sha_prefers_ci_env` pins a platform-specific variable name (`GITHUB_SHA`, line 1147) rather than a generic "CI env"; a name stating the variable would make that contract visible in the failure output (unit-tests guideline 2.2).
        - Current: `def test_resolve_commit_sha_prefers_ci_env(`
        - Expected: `def test_resolve_commit_sha_prefers_github_sha_env(`
        - Resolution: Deferred — the current name is not inaccurate (`GITHUB_SHA` is the CI-provided variable) and the platform-neutral wording survives a future CI migration unchanged; the body names the variable one line down. Matches cr_20260812v01 finding 2.2.
   - 1.3. [suggestion] Line 652: `row_factory: Any` is the one bare `Any` on a hint-able parameter; `Callable[[], Any]` states the zero-argument-callable contract more precisely (type-hints guideline 3).
        - Current: `table: str, row_factory: Any, reason_index: int`
        - Expected: `table: str, row_factory: Callable[[], Any], reason_index: int` (import `Callable`)
        - Resolution: Deferred — carried tradeoff; the return type genuinely varies across the eight row dataclasses and the call site (`row_factory()` at line 657) makes the contract obvious. Low value. Matches cr_20260812v01 finding 2.4.
   - 1.4. [suggestion] Lines 947-984 and 1113-1118, plus broad use of `_insert_params`/`_update_params`/`_pk_params` and the `_SELECT_*`/`_INSERT_*`/`_UPDATE_*`/`_HSTRY_INSERT_*`/`_HSTRY_TABLES`/`_FK_ORDER` privates: dispatch-dict identity assertions and private-surface reliance couple tests to internal wiring (unit-tests guideline 6).
        - Current: `assert db_io._INSERT_SQL["deployment_tables"] is db_io._INSERT_DEPLOYMENT_TABLES` (and the `concepts` equivalents)
        - Expected: rely on the public-path smoke tests (`test_apply_diff_runs_all_nine_table_inserts`, `test_apply_diff_runs_all_nine_table_updates_and_deletes`) that already assert the emitted SQL through `apply_diff`
        - Resolution: Deferred — accepted tradeoff reaffirmed across five prior reviews; the private helpers encode the param-order and validated_ts-stamping contracts most precisely pinned at this level, and the same behaviors are also exercised through `apply_diff`. Re-verified this pass: every asserted index still matches `db_io.py` (INSERT is_nullable/is_primary_key at 4/5, UPDATE at 3/4, column_mappings and table_relationships UPDATE stamp_now/else_value at 6/7, all eight `reason_index` values, `_SELECT_COLUMNS` trailing `ref_table_id`). Matches cr_20260812v01 finding 2.5.
   - 1.5. [suggestion] Lines 313-323 vs 1298-1311 and 295-306 vs 1314-1328: factory pairs `_cm_row`/`_cm` and `_rel_row`/`_rel` duplicate row construction; the second member exposes `validated`/`validated_ts` for the transition-stamping tests but carries no docstring distinguishing it from its sibling.
        - Current: two near-identical factories per table with no note on why both exist
        - Expected: consolidate the pair, or add a one-line docstring on `_cm`/`_rel` clarifying the validated_ts-scenario purpose
        - Resolution: Deferred — accepted tradeoff from prior reviews; the split keeps the validated_ts tests readable, and section comments plus local usage make intent clear. Matches cr_20260812v01 finding 2.6.
   - 1.6. [suggestion] Lines 1331-1380: the validated_ts transition matrix remains separate test functions rather than one parametrized case (unit-tests guideline 5.1).
        - Resolution: Deferred — accepted tradeoff from prior reviews; the explicit per-transition names document the state machine and the count is small. Matches cr_20260812v01 finding 2.7.

2. [completed] New optional refinements - `code/load_catalog_data/unit_tests/test_db_io.py`
   - 2.1. [suggestion] Lines 586-590 and 594-596: `test_apply_diff_reset_hstry_truncates_all_nine_tables` matches `"TRUNCATE TABLE" in repr(c.args[0])` — the `repr()` is load-bearing (db_io builds TRUNCATE as a `psycopg2.sql.Composed`, not a `str`, so a plain `in` test would raise `TypeError`) but nothing in the test says so; every neighboring assertion works on plain strings, so the deviation looks arbitrary (comments guideline 1: explain the why).
        - Current: `if "TRUNCATE TABLE" in repr(c.args[0])`
        - Expected: a one-line comment above the list comprehension, e.g. `# TRUNCATE is built as psycopg2 sql.Composed (not str) — match on repr().`
        - Resolution: Deferred — optional clarity polish; the assertion is correct, the technique is confined to this one test, and a reader who tries a plain-string match gets an immediate `TypeError` pointing at the cause.
   - 2.2. [suggestion] Lines 661-705: the `_warn_if_dirty_working_tree` coverage exercises the dirty and clean outcomes but not the swallow branch (`db_io.py` lines 123-124: `git status` raising `FileNotFoundError`/`TimeoutExpired` must neither warn nor fail the resolve) or the nonzero-returncode branch (line 125: a failed `git status` must not warn). Unit-tests guideline 7.1 asks for all paths; a third scenario whose `_fake_run` raises for the `status` command only would pin the never-fails contract.
        - Current: `_fake_run` variants covering only rev-parse-plus-dirty and rev-parse-plus-clean
        - Expected: a companion test where the `git status` leg raises (or returns nonzero) and `resolve_commit_sha()` still returns the SHA with no WARNING record
        - Resolution: Deferred — optional coverage of a two-line diagnostic-only branch in a private helper; the branch is near-unreachable in practice (it runs only immediately after `git rev-parse` succeeded with the same binary, cwd, and timeout), and the cmd-discriminating stub adds complexity disproportionate to the risk.

## Skills with No Issues

1. Unit Tests skill — naming: No issues found beyond the optional rename in task 1.2. Every test follows `test_<function>_<scenario>_<expected>`, and the file correctly mirrors `db_io.py` as `test_db_io.py`.
2. Unit Tests skill — pytest usage: No issues found. `pytest.raises(..., match=...)` throughout; `@pytest.mark.parametrize` for null/empty `related_object_ids` (line 203), the update_reason-NULL bind matrix (line 638), and the DDL constraint-name pair (line 1233); conftest `fake_conn`/`fake_cursor` fixtures; `monkeypatch`/`caplog` built-ins; and the autouse `_stub_commit_sha` guard against the ambient GitHub Actions `GITHUB_SHA`.
3. Unit Tests skill — mock external boundaries only: No issues found. DB conn/cursor mocked via the conftest `MagicMock` fixtures; `db_io.subprocess.run` and `db_io.resolve_commit_sha` are patched on the module that looks them up.
4. Unit Tests skill — coverage: Issues found — see task 2.2 (optional swallow-branch scenario, deferred). Otherwise no issues; the vendored-lib refactor is correctly mirrored: the four `connection_kwargs` tests moved to `code/lib/pgconn/unit_tests/test_pgconn.py` (which exists and covers the happy path, search_path option, missing-env errors, schema validation, and dotenv), and the comment at lines 52-56 accurately records that db_io only re-exports the helper — verified that `load_catalog_data.py` line 47 and `test_integration.py` line 35 still import `connection_kwargs` from db_io, so collection of those files proves the re-export. Pre-existing coverage is intact: nine-SELECT read path, both SET CONSTRAINTS deferrals in order before any write, the update-links-to-same-transaction-insert phase-ordering scenario, reverse-FK deletes, rollback-and-reraise, load_audit counts and heartbeat, the resolve_commit_sha env/git/dirty/clean/timeout/missing-binary/whitespace matrix, validated_ts transitions, unknown-table error paths for all three param builders, and the all-nine-table smoke counts. Full file: 75 passed.
5. Type Hints skill: Issues found — see task 1.3 (single `row_factory: Any` refinement, deferred). Every other test, fixture, factory, and stub is fully and modernly annotated (`db_value: list[str] | None`, `tuple[str, ...]`, `-> None`, `-> MagicMock`, `dict[str, object]`).
6. Docstrings skill: No issues found. Module docstring present and current (venue-free, 9 tables); the autouse `_stub_commit_sha` fixture docstring accurately describes both its role and the apply_diff-takes-the-SHA-as-an-argument arrangement; `_one_insert_per_table_diff` is documented; per-test intent is conveyed by descriptive names plus inline comments, consistent with prior reviews.
7. Comments skill: Issues found — see task 2.1 (missing why-comment on the `repr()` match, deferred). All other comments re-verified accurate against the current `db_io.py`: the `_SHA` note (lines 27-29), the `_DDL_PATH` note (lines 33-34), the fetchall-stub shape comments including the empty-list run counts (line 182 covers systems..table_relationships = 7; line 208 covers through column_mappings = 8), the SET CONSTRAINTS rationale comments (lines 367-370, 385-389, 411-417), the columns bound-parameter order block (lines 832-848) — the cr_20260812v01 finding 1.1 fix held, both lists now name the trailing `ref_table_id`, agreeing with `db_io.py` lines 216-218 and 638-640 — and the GITHUB_SHA local-fallback and stamp_now index notes.
8. Logging skill: N/A - test module; log output is asserted via `caplog`, no logging responsibilities of its own.
9. Exception Handling skill: N/A - exceptions are asserted via `pytest.raises`, not handled.
10. Executable Scripts skill: N/A - not an executable script.
11. Data Validation skill: N/A - test module, not a data-pipeline output.
