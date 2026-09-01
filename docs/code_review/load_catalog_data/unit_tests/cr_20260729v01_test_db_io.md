---
name: cr_20260729v01_test_db_io
goal: Re-review code/load_catalog_data/unit_tests/test_db_io.py against python-development skills (follow-up to cr_20260724v03; prior task 1 `description: str` fixes confirmed applied, so only optional items remain).
created: 2026-07-29 14:37:41
updated: 2026-07-29 14:37:41
---

## Implementation Plan

1. [completed] Tighten the parametrized-factory type hint - `code/load_catalog_data/unit_tests/test_db_io.py`
   - 1.1. [suggestion] Line 644: `row_factory: Any` is the one bare `Any` on a hint-able parameter; it is a zero-argument callable returning a row dataclass, which `Callable[[], Any]` states more precisely than `Any` (type-hints guideline 3: be specific). The `*args: Any, **kwargs: Any` passthroughs on the `_fake_run`/`_no_git`/`_capture` stubs are idiomatic and not in scope.
        - Current: `table: str, row_factory: Any, reason_index: int`
        - Expected: `table: str, row_factory: Callable[[], Any], reason_index: int` (add `Callable` to the `typing` import)
        - Resolution: Deferred — the return type genuinely varies across the eight row dataclasses so the return stays `Any` either way; the parameter is a local test fixture whose call site (`row_factory()` at line 649) makes the callable contract obvious, and prior reviews explicitly signed off the annotations as complete. Low value.

2. [completed] Private-surface and duplication tradeoffs carried from prior reviews - `code/load_catalog_data/unit_tests/test_db_io.py`
   - 2.1. [suggestion] Lines 960-975, 1106-1110: Dispatch-dict identity assertions (`db_io._INSERT_SQL["deployment_tables"] is db_io._INSERT_DEPLOYMENT_TABLES`, and the `concepts` equivalents) couple the tests to private module wiring rather than observable behavior (unit-tests guideline 6).
        - Current: `assert db_io._INSERT_SQL["deployment_tables"] is db_io._INSERT_DEPLOYMENT_TABLES`
        - Expected: rely on the public-path smoke tests (`test_apply_diff_runs_all_nine_table_inserts`, `test_apply_diff_runs_all_nine_table_updates_and_deletes`) that already assert the correct SQL is emitted through `apply_diff`.
        - Resolution: Deferred — accepted tradeoff from cr_20260717v01 item 3.2, re-affirmed through cr_20260724v03; the `is` checks are an inexpensive, sharply-targeted guard against dispatch-table misconfiguration, so removal offers little net benefit.
   - 2.2. [suggestion] Broad reliance on private helpers/constants (`_insert_params`/`_update_params`/`_pk_params`, the `_SELECT_*`/`_INSERT_*`/`_UPDATE_*`/`_HSTRY_INSERT_*` SQL constants, `_HSTRY_TABLES`, `_FK_ORDER`) as the direct unit under test (unit-tests guideline 6, "don't assert on internal state").
        - Resolution: Deferred — accepted tradeoff from cr_20260717v01 item 3.2; these private functions encode param-order and validated_ts-stamping contracts that are most precisely pinned at the unit level, and the same behaviors are additionally exercised through the public `apply_diff` path. Verified this pass: every asserted index/order still matches `db_io.py` (e.g. `_INSERT_COLUMNS` is_nullable/is_primary_key at 4/5, column_mappings/table_relationships UPDATE stamp_now/else_value at 6/7).
   - 2.3. [suggestion] Lines 375-385 vs 1289-1302 and 357-368 vs 1305-1319: Factory pairs `_cm_row`/`_cm` and `_rel_row`/`_rel` duplicate row construction; the second member of each pair exposes `validated`/`validated_ts` for the transition-stamping tests but carries no docstring distinguishing it from its sibling.
        - Current: two near-identical factories per table with no note on why both exist
        - Expected: consolidate the pair (or add a one-line docstring on `_cm`/`_rel` clarifying the validated_ts-scenario purpose)
        - Resolution: Deferred — accepted tradeoff from cr_20260717v01 item 5.1; the split keeps the validated_ts tests readable and consolidation would entangle unrelated fixtures. Section comments and descriptive local usage make intent clear.
   - 2.4. [suggestion] Lines 1322-1371: The validated_ts transition matrix is expressed as separate test functions rather than a single parametrized case (unit-tests guideline 5.1).
        - Resolution: Deferred — accepted tradeoff from cr_20260717v01 item 4.1; the explicit per-transition names document the state machine and the current count is small.

## Skills with No Issues

1. Unit Tests skill — naming: No issues found. File mirrors `db_io.py` as `test_db_io.py`; test functions follow `test_<function>_<scenario>_<expected>`; the `deployment_tables` naming is applied consistently across test names, factories, and assertions.
2. Unit Tests skill — pytest usage: No issues found. `pytest.raises(..., match=...)` throughout; `@pytest.mark.parametrize` for null/empty `related_object_ids` (line 265) and the update_reason-NULL bind matrix (line 630); conftest `fake_conn`/`fake_cursor` fixtures; `monkeypatch`/`caplog` built-ins; autouse `_stub_commit_sha` keeps `apply_diff` tests from shelling out to git.
3. Unit Tests skill — mock external boundaries only: No issues found. DB conn/cursor mocked via `MagicMock` from conftest; `db_io.subprocess.run`, `db_io.resolve_commit_sha`, and `db_io.load_dotenv` are patched on the module that looks them up, not where defined.
4. Unit Tests skill — coverage: No issues found. Nine-SELECT read path, NULL/empty ltree[] coercion for both `target_tables_referenced` and `related_object_ids`, the deferred `deployment_tables` physical-address constraint as the first statement, composite-PK WHERE completeness, reverse-FK delete ordering (incl. deployment_tables before tables), rollback-and-reraise, the passed-SHA-not-resolved-internally contract, load_audit counts plus empty-diff heartbeat, the DDL-vs-code constraint-name agreement, the resolve_commit_sha env/git/dirty-tree/timeout/missing-binary matrix, the validated_ts transition matrix, and all-nine-table insert/update/delete smoke counts.
5. Type Hints skill: Issues found — see task 1 (single `row_factory: Any` refinement, deferred). Every other test, fixture, factory, and local stub is fully and modernly annotated (`db_value: list[str] | None`, `-> None`, `-> str`, `list[str]`).
6. Docstrings skill: No issues found. Module docstring present; the autouse `_stub_commit_sha` fixture and `_one_insert_per_table_diff` helper are documented; per-test intent is conveyed by descriptive names plus inline comments, consistent with prior reviews.
7. Comments skill: No issues found. The bound-parameter index constants (lines 824-840) and the fetchall-stub shape comments were re-verified against current `db_io.py` and remain accurate; the `repr(c.args[0])` in the TRUNCATE assertions (lines 581, 587) is still required because `db_io` builds TRUNCATE via `sql.SQL(...).format(...)` (a `psycopg2.sql.Composed`, not a `str`).
8. Logging skill: N/A — test module, no logging responsibilities (log output is asserted via `caplog`).
9. Exception Handling skill: N/A — exceptions are asserted via `pytest.raises`, not handled.
10. Executable Scripts skill: N/A — not an executable script.
11. Data Validation skill: N/A — test module, not a data-pipeline output.
