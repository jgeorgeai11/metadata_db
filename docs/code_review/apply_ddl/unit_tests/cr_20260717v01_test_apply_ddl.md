---
name: cr_20260717v01_test_apply_ddl
goal: Address code quality issues identified in code/apply_ddl/unit_tests/test_apply_ddl.py to align with the python-development unit-tests, comments, and docstrings skills.
created: 2026-07-17 12:37:54
updated: 2026-07-17 13:05:00
---

## Implementation Plan

1. [completed] Correct the section-divider header to match its tests - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 1.1. [minor] Lines 147-149: The section divider reads `ensure_ddl_versions / applied_migrations`, but the block beneath it also contains the tests for `ensure_schema` (line 164) and `ddl_versions_exists` (lines 178, 188). Section dividers are the file's only structural map, and the prior review chain explicitly treats them as comments that must stay current (see `cr_20260702v01`, which corrected an outdated header). The header should name every function grouped under it so the map stays accurate.
        - Current: `# ensure_ddl_versions / applied_migrations`
        - Expected: `# ensure_ddl_versions / ensure_schema / ddl_versions_exists / applied_migrations`
        - Resolution: Fixed as documented. Updated the section-divider comment (line 148) to `# ensure_ddl_versions / ensure_schema / ddl_versions_exists / applied_migrations`, so the header now names every function grouped beneath it (`ensure_ddl_versions`, `ensure_schema`, `ddl_versions_exists`, `applied_migrations`).

2. [completed] Lock in connection cleanup in the `run()` happy-path tests - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 2.1. [suggestion] Lines 402-609: The `run()` tests assert branching (which migrations apply, when create/verify/schema fire) but none asserts that the target connection is closed. `run()` closes it in a `finally` block (`apply_ddl.py:436-437`); if that cleanup were dropped, every `run` test would still pass. This is the same cleanup-assertion gap already locked in for `create_database_if_absent` (asserted at lines 329 & 349) and carried forward unresolved from `cr_20260713v01` (finding 1.1). Adding a single close assertion to one happy-path test (e.g. `test_run_applies_only_pending`) would restore that symmetry.
        - Current: `apply_ddl.run(_config(), check_only=False, create_db=False)` (no cleanup assertion)
        - Expected: add `patched_run["connect"].return_value.close.assert_called_once()` after the call
        - Resolution: Not implemented (deferred). Carried-over suggestion from cr_20260713v01 (finding 1.1), deliberated and deferred there; not new to the catalog-schema change. Optional cleanup-assertion symmetry, not a defect — left for a future test-cleanup pass.

## Skills with No Issues

1. unit-tests skill: One minor and one suggestion - see 1.1 and 2.1. Otherwise strong. pytest (not unittest), `test_<function>_<scenario>_<expected>` naming, conftest fixtures (`fake_conn`/`fake_cursor`), and `tmp_path`/`monkeypatch`/`caplog` usage are all correct. `@pytest.mark.parametrize` (lines 131-134), `pytest.raises(..., match=...)`, per-test isolation, and the call-order pattern in `test_run_apply_mode_creates_schema_before_ddl_versions` (lines 468-488) are used well. Coverage was verified by running the file: 51 tests pass. Every public function in `apply_ddl.py` has tests, including all branches of `run()` (check vs. apply, absent tracking table, pending/no-pending, unknown DB version, checksum violation) and all `main()` error arms. The stdlib `unittest.mock` + `monkeypatch` combination (instead of `mocker.patch()`) remains an accepted project deviation; mocking `run()`'s own leaf helpers is deliberately documented in the module docstring (lines 4-5) and is not counted as a finding.
2. type-hints skill: No issues found. All test functions, fixtures, and helpers carry parameter and return annotations using modern syntax (`list[tuple[str, Path]]`, `dict[str, MagicMock]`, `-> None`); the `*a: object, **k: object` stubs are appropriately typed for throwaway callables.
3. docstrings skill: No issues found. The module docstring and the `_migrations`/`patched_run`/`_config` helper docstrings are present and explain intent; individual test functions are conventionally self-documenting via their names, consistent with the unit-tests skill.
4. comments skill: One minor - see 1.1. All other inline comments (e.g. lines 38-42, 94-96, 111-113, 170-172, 321-323, 421, 428-429) explain the "why" and are accurate against the current source.
5. exception-handling skill: No issues found. Expected errors are asserted with `pytest.raises(..., match=...)`; error-injection stubs raise specific types (`RuntimeError`, `KeyError`, `SystemExit`, `psycopg2.Error`); no bare excepts in test code.
6. logging skill: N/A - test module; `setup_logging` is stubbed and `caplog` is used correctly to assert on emitted ERROR/INFO text in the `main()` tests.
7. executable-scripts skill: N/A - this is a test module, not a CLI entry point.
8. data-validation skill: N/A - no data-output validation logic in the tests.
9. SQL best-practices skill: N/A - SQL appears only as literal assertion substrings; no queries are authored in this file.
