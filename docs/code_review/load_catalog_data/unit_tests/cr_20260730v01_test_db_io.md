---
name: cr_20260730v01_test_db_io
goal: Re-review code/load_catalog_data/unit_tests/test_db_io.py against python-development skills after the columns.ref_table_id FK-deferral test additions (follow-up to cr_20260729v01, whose deferred suggestions are carried forward).
created: 2026-07-30 14:20:08
updated: 2026-07-30 14:37:51
---

## Implementation Plan

1. [completed] Naming accuracy and new-test duplication - `code/load_catalog_data/unit_tests/test_db_io.py`
   - 1.1. [minor] Line 110: `test_connection_kwargs_prod_schema_passes` does not exercise a "prod" schema — the body passes `"catalog"`, making the test a near-duplicate of `test_connection_kwargs_populates_from_env` (line 47) and its name inaccurate (unit-tests guideline 2.2: `test_<function>_<scenario>_<expected>`). The test exists as the passing counterpart to the invalid-schema parametrization (which rejects `Prod`/`PROD`), so the scenario should actually be a lowercase prod-style name.
        - Current: `kwargs = db_io.connection_kwargs("mydb", "catalog")` / `assert kwargs["options"] == "-c search_path=catalog"`
        - Expected: `kwargs = db_io.connection_kwargs("mydb", "prod")` / `assert kwargs["options"] == "-c search_path=prod"` (or rename the test to match what it actually asserts)
        - Resolution: Implemented as specified — the test body now passes `"prod"` and asserts `"-c search_path=prod"`, so the name matches the scenario and the test is no longer a near-duplicate of `test_connection_kwargs_populates_from_env`.
   - 1.2. [minor] Lines 1295-1340: `test_deployment_tables_constraint_name_matches_ddl` and the newly added `test_columns_ref_table_id_constraint_name_matches_ddl` are byte-for-byte identical except the constraint constant, including a copy-pasted 7-line `ddl_path` construction (unit-tests guideline 5.1: parametrize one test over multiple inputs). A future DDL-file move requires two edits; a single parametrized test with a module-level `_DDL_PATH` requires one.
        - Current: two separate tests, each rebuilding `ddl_path = Path(db_io.__file__).resolve().parents[2] / "code" / "apply_ddl" / "ddl_catalog" / "0001_initial_schema.sql"` and re-stating the regex/assert
        - Expected: `@pytest.mark.parametrize("constraint", [db_io.DEPLOYMENT_TABLES_PHYSICAL_ADDRESS_CONSTRAINT, db_io.COLUMNS_REF_TABLE_ID_FK_CONSTRAINT])` over one `test_deferred_constraint_names_match_ddl(constraint: str)` body, with the DDL path hoisted to a module-level constant
        - Resolution: Implemented as specified — the two tests are now one `test_deferred_constraint_names_match_ddl(constraint: str)` parametrized over both constants (with `pytest.param` ids `deployment_tables_physical_address` and `columns_ref_table_id_fk` for readable failure output), and the 7-line path construction is hoisted to a module-level `_DDL_PATH` constant with a short comment.

2. [completed] Optional refinements (new and carried from cr_20260729v01) - `code/load_catalog_data/unit_tests/test_db_io.py`
   - 2.1. [suggestion] Lines 47-51, 63-69, 102-105, 113-116: four `connection_kwargs` tests repeat the same four `monkeypatch.setenv("POSTGRES_*", ...)` lines; a small `_pg_env` fixture (conftest or module-local) would remove the repetition (unit-tests guideline 3.1).
        - Current: `monkeypatch.setenv("POSTGRES_HOST", "h")` ... repeated in each test
        - Expected: a shared fixture applying the four env vars, requested by the tests that need them
        - Resolution: Deferred — four short, fully explicit setup blocks in adjacent tests; the inline env values ("h", "5432", "u", "p") are asserted verbatim in the first test, so keeping them visible at the call site aids readability more than a fixture would save.
   - 2.2. [suggestion] Line 714: `row_factory: Any` is the one bare `Any` on a hint-able parameter; `Callable[[], Any]` states the zero-argument-callable contract more precisely (type-hints guideline 3). Carried from cr_20260729v01 item 1.1.
        - Current: `table: str, row_factory: Any, reason_index: int`
        - Expected: `table: str, row_factory: Callable[[], Any], reason_index: int` (import `Callable`)
        - Resolution: Deferred — carried tradeoff from cr_20260729v01; the return type genuinely varies across the eight row dataclasses and the call site (`row_factory()` at line 719) makes the contract obvious. Low value.
   - 2.3. [suggestion] Lines 1030-1045, 1175-1180, plus broad use of `_insert_params`/`_update_params`/`_pk_params` and the `_SELECT_*`/`_INSERT_*`/`_UPDATE_*`/`_HSTRY_INSERT_*`/`_HSTRY_TABLES`/`_FK_ORDER` privates: dispatch-dict identity assertions and private-surface reliance couple tests to internal wiring (unit-tests guideline 6). Carried from cr_20260717v01 item 3.2 via cr_20260729v01 item 2.1/2.2.
        - Current: `assert db_io._INSERT_SQL["deployment_tables"] is db_io._INSERT_DEPLOYMENT_TABLES` (and the `concepts` equivalents)
        - Expected: rely on the public-path smoke tests (`test_apply_diff_runs_all_nine_table_inserts`, `test_apply_diff_runs_all_nine_table_updates_and_deletes`) that already assert the emitted SQL through `apply_diff`
        - Resolution: Deferred — accepted tradeoff reaffirmed across three prior reviews; the private helpers encode param-order and validated_ts-stamping contracts most precisely pinned at this level, and the same behaviors are also exercised through `apply_diff`. Verified this pass: every asserted index/order still matches `db_io.py` (INSERT columns is_nullable/is_primary_key at 4/5, UPDATE at 3/4, column_mappings/table_relationships UPDATE stamp_now/else_value at 6/7, all eight `reason_index` values, `_SELECT_COLUMNS` trailing `ref_table_id`).
   - 2.4. [suggestion] Lines 375-385 vs 1383-1396 and 357-368 vs 1399-1413: factory pairs `_cm_row`/`_cm` and `_rel_row`/`_rel` duplicate row construction; the second member exposes `validated`/`validated_ts` for the transition-stamping tests but carries no docstring distinguishing it from its sibling. Carried from cr_20260717v01 item 5.1 via cr_20260729v01 item 2.3.
        - Current: two near-identical factories per table with no note on why both exist
        - Expected: consolidate the pair, or add a one-line docstring on `_cm`/`_rel` clarifying the validated_ts-scenario purpose
        - Resolution: Deferred — accepted tradeoff from prior reviews; the split keeps the validated_ts tests readable, and section comments plus local usage make intent clear.
   - 2.5. [suggestion] Lines 1416-1465: the validated_ts transition matrix remains separate test functions rather than one parametrized case (unit-tests guideline 5.1). Carried from cr_20260717v01 item 4.1 via cr_20260729v01 item 2.4.
        - Resolution: Deferred — accepted tradeoff from prior reviews; the explicit per-transition names document the state machine and the count is small.

## Skills with No Issues

1. Unit Tests skill — naming: Issues found — see task 1.1 (misnamed `prod_schema` test); all other test functions follow `test_<function>_<scenario>_<expected>` and the file correctly mirrors `db_io.py` as `test_db_io.py`.
2. Unit Tests skill — pytest usage: Issues found — see task 1.2 (unparametrized DDL-agreement pair); otherwise `pytest.raises(..., match=...)` throughout, `@pytest.mark.parametrize` for invalid schemas (line 82), null/empty `related_object_ids` (line 265), and the update_reason-NULL bind matrix (line 700), conftest `fake_conn`/`fake_cursor` fixtures, `monkeypatch`/`caplog` built-ins, and the autouse `_stub_commit_sha` guard.
3. Unit Tests skill — mock external boundaries only: No issues found. DB conn/cursor mocked via conftest `MagicMock` fixtures; `db_io.subprocess.run`, `db_io.resolve_commit_sha`, and `db_io.load_dotenv` are patched on the module that looks them up.
4. Unit Tests skill — coverage: No issues found. New code since cr_20260729v01 is well covered: both SET CONSTRAINTS deferrals asserted in order before any write (lines 429-469), the update-links-to-same-transaction-insert phase-ordering scenario the FK deferral exists for (line 472), `ref_table_id` presence across all four columns SQL statements plus trailing-position and NULL-bind checks (lines 923-966), and the columns FK constraint-name-vs-DDL agreement (line 1319; both constraint names verified declared in `0001_initial_schema.sql` lines 188/256). Pre-existing coverage (nine-SELECT read path, reverse-FK deletes, rollback-and-reraise, load_audit counts and heartbeat, resolve_commit_sha env/git/dirty/timeout/missing-binary matrix, validated_ts transitions, all-nine-table smoke counts) remains intact.
5. Type Hints skill: Issues found — see task 2.2 (single `row_factory: Any` refinement, deferred). Every other test, fixture, factory, and stub is fully and modernly annotated (`db_value: list[str] | None`, `tuple[str, ...]`, `-> None`).
6. Docstrings skill: No issues found. Module docstring present and current (venue-free, 9 tables); the autouse `_stub_commit_sha` fixture and `_one_insert_per_table_diff` helper are documented; per-test intent is conveyed by descriptive names plus inline comments, consistent with prior reviews.
7. Comments skill: No issues found. The bound-parameter index constants (lines 894-910) and the fetchall-stub shape comments were re-verified against current `db_io.py` and are accurate, including the new ref_table_id ordering notes (lines 135-136, 933-937) and the SET CONSTRAINTS rationale comments (lines 431-434, 448-450, 475-479); `repr(c.args[0])` in the TRUNCATE assertions remains required because `db_io` builds TRUNCATE as a `psycopg2.sql.Composed`, not a `str`.
8. Logging skill: N/A — test module; log output is asserted via `caplog`, no logging responsibilities of its own.
9. Exception Handling skill: N/A — exceptions are asserted via `pytest.raises`, not handled.
10. Executable Scripts skill: N/A — not an executable script.
11. Data Validation skill: N/A — test module, not a data-pipeline output.
