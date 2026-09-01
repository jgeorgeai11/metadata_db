---
name: "cr_20260813v01_test_pgconn"
goal: Address code quality issues identified in code/lib/pgconn/unit_tests/test_pgconn.py to align with python-development skills; reviewed as a group with pgconn.py (see cr_20260813v01_pgconn.md).
created: 2026-08-13 11:05:46
updated: 2026-08-13 11:17:17
---

## Implementation Plan

1. [completed] Keep the dotenv fake compatible with the interpolate fix and close a coverage gap - `code/lib/pgconn/unit_tests/test_pgconn.py`
   - 1.1. [minor] Line 137: `fake_load_dotenv` accepts no parameters, so it encodes the zero-argument call contract that the sibling review changes — once `pgconn.py` calls `load_dotenv(interpolate=False)` (finding 1.1 in `docs/code_review/lib/pgconn/cr_20260813v01_pgconn.md`), this test fails with `TypeError: fake_load_dotenv() got an unexpected keyword argument 'interpolate'`. Implement together with that finding.
        - Current: `def fake_load_dotenv() -> None:`
        - Expected: `def fake_load_dotenv(*args: object, **kwargs: object) -> None:`
        - Resolution: Implemented as specified, in the same changeset as the sibling `interpolate=False` fix — the fake now takes `*args: object, **kwargs: object` with a comment noting it is signature-agnostic on purpose.
   - 1.2. [minor] Lines 63-76: the missing-env-var tests only `delenv` the variables, so the deliberate set-but-empty branch of `pgconn.py` line 76 (`not os.environ.get(name)` treats `POSTGRES_PASSWORD=""` as unusable) is never exercised — a boundary value the unit-tests skill's coverage guideline (7.1) calls for.
        - Current: `monkeypatch.delenv(missing_var, raising=False)` is the only way a variable becomes "missing" in the suite
        - Expected: add an empty-string case, e.g. parametrize the existing test over `("delete", "empty")` or add `test_connection_kwargs_empty_env_var_treated_as_missing` that does `monkeypatch.setenv("POSTGRES_PASSWORD", "")` and expects `pytest.raises(RuntimeError, match="POSTGRES_PASSWORD")`
        - Resolution: Implemented via the first suggested option — stacked a second `@pytest.mark.parametrize("mode", ...)` (`deleted`/`empty`) on `test_connection_kwargs_missing_env_var_named_in_error`, so all four variables are exercised both deleted and set-but-empty (8 cases); suite passes 23/23 with 100% coverage of `pgconn.py`.

2. [completed] Optional enhancements - `code/lib/pgconn/unit_tests/test_pgconn.py`
   - 2.1. [suggestion] Lines 55-60 and 117-125: `test_connection_kwargs_sets_search_path_option` and `test_connection_kwargs_valid_lowercase_schema_passes` re-assert the `options` string the happy-path test (line 50) already pins down.
        - Resolution: Deferred — the redundancy is harmless and each test documents a distinct intent (search_path wiring vs. the validator accepting lowercase/underscore names); collapsing them saves little.
   - 2.2. [suggestion] Lines 8, 23-24: the suite mocks with `MagicMock` + `monkeypatch.setattr` rather than the unit-tests skill's `mocker.patch()` (guideline 4).
        - Resolution: Deferred — `pytest-mock` is not a project dependency and `monkeypatch` is the repo-wide convention (all nine test modules use it, none use `mocker`); `monkeypatch` is also sanctioned in the skill's built-in fixtures table, and the patch is correctly applied where the name is looked up (`pgconn.pgconn`, per guideline 4's patch-where-used rule).

## Skills with No Issues

1. Type Hints: No issues found
2. Docstrings: No issues found
3. Comments: No issues found
4. Exception Handling: No issues found - expected errors are asserted via `pytest.raises(..., match=...)`
5. Logging: N/A - test module; pytest captures output and no logging setup is needed
6. Executable Scripts: N/A - test module, not an entry-point script
7. Data Validation: N/A - no data pipeline inputs or outputs to validate
