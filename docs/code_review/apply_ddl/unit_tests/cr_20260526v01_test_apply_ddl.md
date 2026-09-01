---
name: cr_20260526v01_test_apply_ddl
goal: Address code quality issues identified in code/apply_ddl/unit_tests/test_apply_ddl.py (and its conftest.py) to align with the python-development unit-tests, type-hints, docstrings, comments, and exception-handling skills.
created: 2026-05-26 00:00:00
updated: 2026-05-26 14:55:00
---

## Implementation Plan

1. [completed] Strengthen assertions so tests cannot pass for the wrong reason - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 1.1. [major] `test_run_applies_only_pending` proved `apply_one` was called exactly once but not for the pending version `0002`. Resolved: now also asserts `patched_run["apply_one"].call_args[0][1] == "0002"`.
   - 1.2. [major] The five `main()` error-path tests asserted only `exc.value.code == 1`; since every `except` arm exits 1, a test could pass via the wrong arm. Resolved: each error test now takes `caplog`, sets the level to ERROR, and asserts its arm-specific message ("Config file not found", "Failed to read config file", "Missing required config field", the bare RuntimeError text, "Database error").

2. [completed] Cover untested behavior / paths - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 2.1. [major] No test proved multiple pending migrations apply in numeric order. Resolved: added `test_run_applies_pending_in_numeric_order` (asserts the ordered `apply_one` sequence via `run`) and `test_list_repo_migrations_unpadded_prefixes_returns_numeric_order` (asserts numeric ordering at the source).
   - 2.2. [completed] [suggestion] `test_run_check_pending_exits_nonzero` now also asserts `apply_one.assert_not_called()`, locking in the read-only contract of `--check`.

3. [completed] Test commenting / clarity - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 3.1. [suggestion] The `# Only 0002 is pending.` comment is now accurate because the version-level assertion from 1.1 verifies it.

## Skills with No Issues

1. unit-tests skill: Issues found - see 1.1, 1.2, 2.1, 2.2. Naming (`test_<function>_<scenario>_<expected>`), pytest (not unittest) for the test functions, conftest fixtures, `tmp_path`/`monkeypatch`/`pytest.raises(match=...)` usage, and per-test isolation are all correct. Note: use of stdlib `unittest.mock.MagicMock` + `monkeypatch` instead of `mocker.patch()` is an accepted project deviation (pytest-mock is not on the approved-packages list) and is NOT counted as a finding.
2. type-hints skill: No issues found. All test functions, fixtures, and helpers carry parameter and return annotations using modern syntax (`list[tuple[str, Path]]`, `dict[str, MagicMock]`, `-> None`); the `*a: object, **k: object` stubs are appropriately specific for throwaway callables.
3. docstrings skill: No issues found. Module docstrings are present in both files; helper/fixture docstrings (`_migrations`, `patched_run`, `fake_cursor`, `fake_conn`) explain the "why". Individual test functions are conventionally self-documenting via their names, consistent with the unit-tests skill.
4. comments skill: One minor alignment note - see 3.1. Other inline comments (e.g. "First execute runs the migration body; second records the version.", "CREATE DATABASE cannot run inside a transaction block.") correctly explain the "why".
5. exception-handling skill: No issues found. Tests use `pytest.raises(..., match=...)` for expected errors and the error-injection stubs raise specific types (`RuntimeError`, `KeyError`, `psycopg2.Error`); no bare excepts in test code.
6. logging skill: N/A - test/fixture code; `setup_logging` is stubbed out. (Becomes relevant only if `caplog` is adopted per 1.2.)
7. executable-scripts skill: N/A - these are test modules, not CLI entry points.
8. data-validation skill: N/A - no data-output validation logic in tests.
9. SQL best-practices skill: N/A - the only SQL appears as literal assertion substrings; no queries authored here.
