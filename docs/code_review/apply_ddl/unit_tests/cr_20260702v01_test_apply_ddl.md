---
name: cr_20260702v01_test_apply_ddl
goal: Address code quality issues identified in code/apply_ddl/unit_tests/test_apply_ddl.py to align with the python-development unit-tests, comments, and docstrings skills.
created: 2026-07-02 00:00:00
updated: 2026-07-02 00:00:00
---

## Implementation Plan

1. [completed] Correct an outdated section-header comment - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 1.1. [minor] Line 121: The section header names `applied_versions`, but the function under test (and the tests below it) is `applied_migrations` (`apply_ddl.py:200`). There is no `applied_versions` symbol in the module. Per the comments skill, an outdated comment is worse than none.
        - Current: `# ensure_ddl_versions / applied_versions`
        - Expected: `# ensure_ddl_versions / applied_migrations`

2. [completed] Strengthen `create_database_if_absent` assertions to pin source-specific behavior - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 2.1. [minor] Lines 253 & 268: Both tests stub the driver with `lambda **k: fake_conn`, which discards the connection kwargs. As a result the load-bearing behavior in `apply_ddl.py:112-116` — that the maintenance connection overrides `dbname` to `"postgres"` (CREATE DATABASE cannot run from within the target DB) — is never asserted. Both tests still pass if that override is deleted. Use a `MagicMock` for `connect` and assert the maintenance `dbname`.
        - Current: `monkeypatch.setattr(apply_ddl.psycopg2, "connect", lambda **k: fake_conn)`
        - Expected:
          ```python
          mock_connect = MagicMock(return_value=fake_conn)
          monkeypatch.setattr(apply_ddl.psycopg2, "connect", mock_connect)
          ...
          assert mock_connect.call_args.kwargs["dbname"] == "postgres"
          ```
   - 2.2. [suggestion] Lines 260 & 275: The tests assert `execute.call_count` but not *what* ran. Neither the existence-check parameter (`(target,)` passed to the `SELECT ... pg_database` query, `apply_ddl.py:121-123`) nor the fact that `conn.close()` runs in the `finally` block (`apply_ddl.py:131-132`) is verified. Consider adding `assert fake_cursor.execute.call_args_list[0][0][1] == ("mydb",)` and `fake_conn.close.assert_called_once()` to lock in the query shape and connection cleanup.

3. [completed] Clarify a test whose name overstates what it verifies - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 3.1. [suggestion] Lines 355-368: `test_run_applies_pending_in_numeric_order` feeds `list_repo_migrations` (mocked) input that is *already* in numeric order, so it only proves `run()` preserves the iteration order of its input — the actual numeric sort lives in `list_repo_migrations` (`apply_ddl.py:165`) and is covered separately by `test_list_repo_migrations_unpadded_prefixes_returns_numeric_order`. The name implies `run()` performs the ordering. Rename to reflect the real contract (e.g. `test_run_applies_pending_in_input_order`) or add a comment noting the sort is delegated to `list_repo_migrations`.

4. [completed] Minor consistency / assertion polish - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 4.1. [suggestion] Line 484-496: `test_main_success_does_not_exit` has no explicit assertion; it relies solely on the absence of `SystemExit`. Since `run` is already stubbed, consider asserting the stub was invoked (e.g. capture it as a `MagicMock` and `assert mock_run.called`) so the test also proves `main()` reached the dispatch, not merely that it returned.
   - 4.2. [suggestion] Line 317: The private helper `_config()` has no docstring, whereas its sibling helper `_migrations()` (line 18-20) does. Add a one-line docstring for consistency (e.g. `"""Return a minimal valid config dict for run() tests."""`).

## Skills with No Issues

1. unit-tests skill: Issues found - see 2.1, 2.2, 3.1, 4.1. Otherwise strong: pytest (not unittest), `test_<function>_<scenario>_<expected>` naming, conftest fixtures (`fake_conn`/`fake_cursor`), `tmp_path`/`monkeypatch`/`caplog` usage, `@pytest.mark.parametrize` (lines 104-107), `pytest.raises(..., match=...)`, and per-test isolation are all correct. Every public function in `apply_ddl.py` has coverage. The stdlib `unittest.mock` + `monkeypatch` combination (instead of `mocker.patch()`) is an accepted project deviation (pytest-mock not on the approved-packages list) and is not counted as a finding.
2. type-hints skill: No issues found. All test functions, fixtures, and helpers carry parameter and return annotations using modern syntax (`list[tuple[str, Path]]`, `dict[str, MagicMock]`, `-> None`); the `*a: object, **k: object` stubs are appropriately typed for throwaway callables.
3. docstrings skill: One suggestion - see 4.2 (`_config` missing docstring). Module docstring and the other helper/fixture docstrings are present and explain the "why"; individual test functions are conventionally self-documenting via their names, consistent with the unit-tests skill.
4. comments skill: One issue found - see 1.1 (outdated `applied_versions` header). Other inline comments (e.g. lines 169-170, 259, 340) correctly explain intent and are accurate against the current source.
5. exception-handling skill: No issues found. Expected errors are asserted with `pytest.raises(..., match=...)`; error-injection stubs raise specific types (`RuntimeError`, `KeyError`, `psycopg2.Error`); no bare excepts in test code.
6. logging skill: N/A - test module; `setup_logging` is stubbed and `caplog` is used correctly to assert on emitted ERROR text in the `main()` tests.
7. executable-scripts skill: N/A - this is a test module, not a CLI entry point.
8. data-validation skill: N/A - no data-output validation logic in the tests.
9. SQL best-practices skill: N/A - SQL appears only as literal assertion substrings; no queries are authored in this file.
