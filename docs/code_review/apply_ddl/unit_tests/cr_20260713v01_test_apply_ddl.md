---
name: cr_20260713v01_test_apply_ddl
goal: Address code quality issues identified in code/apply_ddl/unit_tests/test_apply_ddl.py to align with the python-development unit-tests, comments, and docstrings skills.
created: 2026-07-13 22:37:15
updated: 2026-07-13 22:37:15
---

## Implementation Plan

1. [completed] Lock in connection cleanup in the `run()` happy-path tests - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 1.1. [suggestion] Lines 369-405: The `run()` tests assert branching (which migrations get applied, when create/verify fire) but none asserts that the target connection is closed. `run()` closes it in a `finally` block (`apply_ddl.py:367-368`); if that cleanup were dropped, every `run` test would still pass. This is the same cleanup-assertion gap the prior review flagged and fixed for `create_database_if_absent` (see `cr_20260702v01` finding 2.2, now asserted at lines 279 & 299). Adding a single close assertion to one happy-path test (e.g. `test_run_applies_only_pending`) would restore that symmetry.
        - Current: `apply_ddl.run(_config(), check_only=False, create_db=False)` (no cleanup assertion)
        - Expected: add `patched_run["connect"].return_value.close.assert_called_once()` after the call
        - Resolution: Deferred — this is a `[suggestion]`, so it is left unimplemented per the code-implementation workflow. If promoted to `[minor]`, add `patched_run["connect"].return_value.close.assert_called_once()` to `test_run_applies_only_pending` to lock in the `finally`-block close at `apply_ddl.py:367-368`.

## Skills with No Issues

1. unit-tests skill: One suggestion - see 1.1. Otherwise strong. pytest (not unittest), `test_<function>_<scenario>_<expected>` naming, conftest fixtures (`fake_conn`/`fake_cursor`), and `tmp_path`/`monkeypatch`/`caplog` usage are all correct. `@pytest.mark.parametrize` (lines 115-118), `pytest.raises(..., match=...)`, and per-test isolation are used well. The prior review's findings are all resolved in this file: the section header now reads `applied_migrations` (line 132); the `create_database` tests use a `MagicMock` for `connect` and assert the maintenance `dbname`, the existence-check parameter, and `conn.close()` (lines 264-299); the numeric-order run test is renamed `test_run_applies_pending_preserving_list_order` with a delegation comment (lines 380-396); `test_main_success_does_not_exit` now asserts `mock_run.assert_called_once()` (line 527); and `_config()` has a docstring (line 342). Coverage was verified at 100% of `apply_ddl.py` (159/159 statements, 40 tests passing). The stdlib `unittest.mock` + `monkeypatch` combination (instead of `mocker.patch()`) remains an accepted project deviation; the pattern of mocking `run()`'s own leaf helpers is deliberately documented in the module docstring (lines 4-5) and is not counted as a finding.
2. type-hints skill: No issues found. All test functions, fixtures, and helpers carry parameter and return annotations using modern syntax (`list[tuple[str, Path]]`, `dict[str, MagicMock]`, `-> None`); the `*a: object, **k: object` stubs are appropriately typed for throwaway callables.
3. docstrings skill: No issues found. The module docstring and every helper/fixture docstring are present and explain the "why"; individual test functions are conventionally self-documenting via their names, consistent with the unit-tests skill.
4. comments skill: No issues found. Inline comments (e.g. lines 41-42, 78-79, 271-272, 375, 383-385) explain intent and are accurate against the current source; the previously-outdated section header has been corrected.
5. exception-handling skill: No issues found. Expected errors are asserted with `pytest.raises(..., match=...)`; error-injection stubs raise specific types (`RuntimeError`, `KeyError`, `psycopg2.Error`); no bare excepts in test code.
6. logging skill: N/A - test module; `setup_logging` is stubbed and `caplog` is used correctly to assert on emitted ERROR text in the `main()` tests.
7. executable-scripts skill: N/A - this is a test module, not a CLI entry point.
8. data-validation skill: N/A - no data-output validation logic in the tests.
9. SQL best-practices skill: N/A - SQL appears only as literal assertion substrings; no queries are authored in this file.
