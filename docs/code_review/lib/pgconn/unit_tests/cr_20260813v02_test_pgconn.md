---
name: "cr_20260813v02_test_pgconn"
goal: Address code quality issues identified in code/lib/pgconn/unit_tests/test_pgconn.py to align with python-development skills; re-review since cr_20260813v01_test_pgconn.md (both prior findings verified fixed), reviewed as a group with pgconn.py, __init__.py, and conftest.py.
created: 2026-08-13 11:42:48
updated: 2026-08-13 11:42:48
---

## Implementation Plan

1. [completed] Pin the contracts the suite currently leaves unguarded - `code/lib/pgconn/unit_tests/test_pgconn.py`
   - 1.1. [minor] Line 52: `assert stub_dotenv.called` only proves `load_dotenv` was invoked, not how. The `interpolate=False` argument added to `pgconn.py` line 79 is the entire subject of the previous review's finding 1.1 (`docs/code_review/lib/pgconn/cr_20260813v01_pgconn.md`) and is what makes the module docstring's "literal values — no shell expansion" claim true; nothing in the suite asserts it. Reverting `pgconn.py` line 79 to a bare `load_dotenv()` leaves all 23 tests green, so the fix has no regression guard. The module docstring here (lines 3-5) already frames the stub as "a MagicMock, so calls remain assertable" — this is the assertion that use was meant to enable, and it is a one-line change to the existing test.
        - Current: `assert stub_dotenv.called`
        - Expected: `stub_dotenv.assert_called_once_with(interpolate=False)`
        - Resolution: Implemented as specified, with a two-line "why" comment above it recording what the argument buys (literal `.env` values, so a secret containing `${` is not rewritten by python-dotenv's default expansion). Verified as a real regression guard by mutation: reverting `pgconn.py` to a bare `load_dotenv()` now fails `test_connection_kwargs_happy_path_builds_six_key_mapping` (1 failed, 23 passed), where it previously left the suite green; the source was restored and the full suite passes.
   - 1.2. [minor] Lines 127-129: the valid-schema parametrization covers `catalog` and `_private` only, so the digit-accepting half of `SCHEMA_NAME_RE`'s second character class (`[a-z0-9_]*`) is never exercised — narrowing the pattern to `[a-z_]+` would keep the whole suite green while breaking any real schema named e.g. `catalog_v2`. The invalid list already pins the leading-digit boundary (`1prod`, line 107); the accepting side of the same boundary is the gap the unit-tests skill's cover-all-paths guideline (7.1) calls for, and it costs one `pytest.param`.
        - Current: `"schema", [pytest.param("catalog"), pytest.param("_private")]`
        - Expected: `"schema", [pytest.param("catalog"), pytest.param("_private"), pytest.param("catalog_v2")]`
        - Resolution: Implemented with an added accommodation — added `pytest.param("catalog_v2", id="trailing_digit")` and, while touching the list, gave the two existing params explicit ids (`lowercase`, `leading_underscore`) to match the invalid-schema parametrization's convention, plus a comment noting the new case pins the accepting side of the leading-digit boundary that `1prod` pins on the rejecting side. Suite is now 24 tests, all passing, with `pgconn.py` at 100% coverage.

2. [completed] Optional enhancements - `code/lib/pgconn/unit_tests/test_pgconn.py`
   - 2.1. [suggestion] Lines 16-34: the `stub_dotenv` and `postgres_env` fixtures live in the test module while `conftest.py` holds only `sys.path` setup, which inverts the arrangement of every sibling suite (`apply_ddl`, `load_catalog_data`, `load_ref_data`, and `revert_merge` conftests all carry the shared fixtures) and the unit-tests skill's 3.1 "share common test data via conftest.py"; noted symmetrically in `docs/code_review/lib/pgconn/unit_tests/cr_20260813v01_conftest.md` finding 1.1.
        - Resolution: Deferred — this is the only test module in the suite, so both fixtures have exactly one consumer and keeping them beside their tests is the clearer arrangement; conftest.py exists here purely for the path insert that must run before collection. Move them if a second test module in this directory needs them.
   - 2.2. [suggestion] Lines 8 and 23-24: the suite mocks with `MagicMock` + `monkeypatch.setattr` rather than the unit-tests skill's `mocker.patch()` (guideline 4).
        - Resolution: Deferred — carried forward from `cr_20260813v01_test_pgconn.md` finding 2.2 and re-verified against `pyproject.toml`: `pytest-mock` is still not in the dev dependency group, `monkeypatch` is the repo-wide convention and is sanctioned in the skill's built-in fixtures table, and the patch is applied where the name is looked up (`pgconn.pgconn`), satisfying guideline 4's patch-where-used rule.
   - 2.3. [suggestion] Lines 55-60 and 130-135: `test_connection_kwargs_sets_search_path_option` and `test_connection_kwargs_valid_lowercase_schema_passes` re-assert the `options` string the happy-path test already pins at line 50.
        - Resolution: Deferred — carried forward from `cr_20260813v01_test_pgconn.md` finding 2.1; the redundancy is harmless and each test documents a distinct intent (search_path wiring vs. the validator accepting lowercase/underscore names), so collapsing them saves little.

## Skills with No Issues

1. Type Hints: No issues found - every test, fixture, and the local `fake_load_dotenv` carries parameter and `-> None` annotations
2. Docstrings: No issues found - module and fixture docstrings state purpose and the non-obvious patch target; test intent is carried by descriptive names plus "why" comments (lines 47-49, 78-79, 119-122, 141-143)
3. Comments: No issues found
4. Exception Handling: No issues found - expected errors are asserted via `pytest.raises(..., match=...)` with the specific type
5. Logging: N/A - test module; pytest captures output and no logging setup is needed
6. Executable Scripts: N/A - test module, not an entry-point script
7. Data Validation: N/A - no data pipeline inputs or outputs to validate
