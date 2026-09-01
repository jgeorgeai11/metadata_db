---
name: cr_20260803v01_test_integration
goal: Address code quality issues identified in code/load_catalog_data/unit_tests/test_integration.py to align with python-development skills (first review of this file).
created: 2026-08-03 10:08:02
updated: 2026-08-03 10:08:02
---

## Implementation Plan

1. [completed] Test independence and assertion precision - `code/load_catalog_data/unit_tests/test_integration.py`
   - 1.1. [minor] Line 447: `test_loader_lifecycle` is the only DB-content test that does not call `_truncate_all(conn)` before its first load, so its exact-count assertions (`_count(conn, "systems") == 2`, `_count(conn, "load_audit") == 1`, the zero `_hstry` counts at lines 517-521) depend on running first against the pristine module-scoped DB (unit-tests guideline 6: never depend on execution order or shared state). Every other content test (`test_deployment_address_swap_commits_in_one_run`, `test_clean_dry_run_is_pure`, `test_lineage_join_ties_rows_to_their_run`, `test_reset_hstry_truncates_inside_load_transaction`, `test_multiple_mappings_and_cross_source_mapping`, `test_loader_run_satisfies_new_backstops`) defensively truncates first — `_truncate_all`'s own docstring frames truncate-first as the pattern that keeps a test "independent of the module-scoped DB's leftover state".
        - Current: `try:` / `# --- 1. Full load ---` / `_load(cfg, monkeypatch)` (no reset before the first load)
        - Expected: `try:` / `_truncate_all(conn)` / `# --- 1. Full load ---` / `_load(cfg, monkeypatch)`
        - Resolution: Implemented as specified — added `_truncate_all(conn)` as the first statement in the `try` block, before the `# --- 1. Full load ---` comment, so the test's exact-count assertions no longer depend on running first against the pristine module-scoped DB.
   - 1.2. [minor] Line 1153: `test_direct_insert_violating_backstop_is_rejected` asserts only `pytest.raises(psycopg2.Error)`, so a violating statement that fails for the wrong reason (e.g., a typo producing `ProgrammingError` instead of tripping the backstop) still passes (unit-tests guideline 5.2: verify the expected error). Each `_BACKSTOP_VIOLATIONS` case has a known violation class — CHECK constraints raise `psycopg2.errors.CheckViolation`, the unique indexes raise `psycopg2.errors.UniqueViolation` — so the parametrized tuple can carry the expected class per case.
        - Current: `_BACKSTOP_VIOLATIONS: list[tuple[str, list[str], str]]` cases of `(id, setup, violating)` and `with pytest.raises(psycopg2.Error):`
        - Expected: extend cases to `(id, setup, violating, expected_exc)` (e.g., `psycopg2.errors.UniqueViolation` for `duplicate_loaded_ts`/`reverse_orientation_pair`, `psycopg2.errors.CheckViolation` for the rest) and assert `with pytest.raises(expected_exc):`
        - Resolution: Implemented as specified — widened the tuple type to `list[tuple[str, list[str], str, type[psycopg2.Error]]]`, added the expected error class to each case (`UniqueViolation` for `duplicate_loaded_ts`/`reverse_orientation_pair`, `CheckViolation` for the other six), added `expected_exc` to the `@pytest.mark.parametrize` field list and the test signature, and changed the assertion to `with pytest.raises(expected_exc):`. Confirmed `psycopg2.errors.CheckViolation`/`UniqueViolation` are reachable via the existing bare `import psycopg2`, so no new import was needed.

2. [completed] Signature hygiene - `code/load_catalog_data/unit_tests/test_integration.py`
   - 2.1. [minor] Lines 754-756: `test_advisory_lock_excludes_concurrent_runs` requests the `monkeypatch` fixture but never uses it — the test drives `lmd.run` directly and never calls `_load` (the only helper that needs `monkeypatch`). The dead parameter implies argv/env patching that does not happen.
        - Current: `def test_advisory_lock_excludes_concurrent_runs(\n    tmp_path: Path, integration_db: None, monkeypatch: pytest.MonkeyPatch\n) -> None:`
        - Expected: `def test_advisory_lock_excludes_concurrent_runs(\n    tmp_path: Path, integration_db: None\n) -> None:`
        - Resolution: Implemented as specified — dropped the unused `monkeypatch: pytest.MonkeyPatch` parameter; the test drives `lmd.run` directly and never calls `_load`, so no argv/env patching is implied.

3. [completed] Docstrings on the backstop tests - `code/load_catalog_data/unit_tests/test_integration.py`
   - 3.1. [minor] Lines 1139-1147, 1160-1165, 1185-1191: the three backstop tests (`test_direct_insert_violating_backstop_is_rejected`, `test_direct_insert_table_and_column_anchor_concepts_accepted`, `test_loader_run_satisfies_new_backstops`) open with block comments where a docstring belongs, while every other function in the file — all ten other tests, all nine helpers, and the fixture — carries a docstring (docstrings guideline 2: all public functions need docstrings). The comment text already reads as docstring prose; converting it also surfaces the intent in pytest/IDE introspection.
        - Current: `def test_direct_insert_violating_backstop_is_rejected(...) -> None:` / `    # Every new declarative backstop must reject a non-loader INSERT that` / `    # violates it. ...`
        - Expected: `def test_direct_insert_violating_backstop_is_rejected(...) -> None:` / `    """Every new declarative backstop rejects a non-loader INSERT that violates it. ..."""` (same conversion for the other two tests)
        - Resolution: Implemented as specified — converted the leading block comment on all three backstop tests to a Google-style docstring (a one-line summary plus the retained rationale prose): `test_direct_insert_violating_backstop_is_rejected` (folded in with task 1.2's edit), `test_direct_insert_table_and_column_anchor_concepts_accepted`, and `test_loader_run_satisfies_new_backstops`. Every function in the file now carries a docstring.

4. [completed] Optional refinements - `code/load_catalog_data/unit_tests/test_integration.py`
   - 4.1. [suggestion] Line 782: the lock-holder computes the second advisory-lock key via the private helper `lmd._schema_lock_key(TEST_DB, TEST_SCHEMA)` (unit-tests guideline 6: don't rely on private internals); a public constant/helper on the loader would remove the private coupling.
        - Current: `(lmd.LOADER_LOCK_KEY, lmd._schema_lock_key(TEST_DB, TEST_SCHEMA))`
        - Expected: a public loader-exposed helper (e.g., `lmd.schema_lock_key`) used by both the loader and the test
        - Resolution: Deferred — the adjacent comment documents the coupling deliberately ("Compute the second key via the loader's own helper so the test cannot drift from the loader again"): a hardcoded key already caused real drift once, and promoting the helper to public API is a loader-module change out of scope for this test file.
   - 4.2. [suggestion] Lines 237-435: `test_pk_agreement_and_ltree_types` packs ~25 distinct DDL-conformance probes (PK agreement, dropped columns, NOT NULLs, constraint deferrability, indexes, ltree types) into one 200-line test, so the first failing assert masks the rest (unit-tests guidelines 3 and 5.1); most probes share a "query -> expected scalar" shape that could be parametrized.
        - Current: one monolithic test body of sequential `assert _scalar(conn, ...) == expected` blocks
        - Expected: a parametrized `(query, expected, id)` table over a module-scoped connection fixture, keeping only the irregular probes inline
        - Resolution: Deferred — the probes jointly pin one behavior ("the applied 0001 DDL matches the design"), each assert carries an identifying message or self-describing query, and the shared connection against the module-scoped DB keeps the gated suite fast; splitting adds ceremony without changing what a failure means.

## Skills with No Issues

1. Unit Tests skill — naming: No issues found. `test_integration.py` deliberately names the cross-module gated suite rather than mirroring a single source module (guideline 2.1 targets unit-test files; the module docstring and the documented invocation command both anchor on this name), and test function names describe scenario and expectation.
2. Unit Tests skill — independence/precision: Issues found — see tasks 1.1 (lifecycle test depends on pristine DB order), 1.2 (generic `psycopg2.Error` in the backstop matrix), and 4.1 (private `_schema_lock_key` coupling, deferred).
3. Unit Tests skill — pytest usage: Issues found — see task 4.2 (monolithic schema-conformance test, deferred); otherwise `@pytest.mark.parametrize` with `pytest.param(..., id=cid)` for the backstop matrix, `pytest.raises(..., match="already in progress")` for the lock tests, module-scoped `integration_db` fixture for the expensive DB setup, and `tmp_path`/`tmp_path_factory`/`monkeypatch` built-ins throughout.
4. Unit Tests skill — mocking: No issues found. As the one end-to-end suite this file intentionally mocks nothing; the only patching is `sys.argv` via `monkeypatch` to drive the real `main()`.
5. Type Hints skill: No issues found. Every helper, fixture, and test is annotated with modern syntax (`Iterator[None]`, `dict[str, Any]`, `list[tuple[str, list[str], str]]`); `_scalar`'s `Any` return is genuinely variable across probes.
6. Docstrings skill: Issues found — see task 3.1 (three backstop tests carry comments where docstrings belong); the module docstring and all other function docstrings are present and current (venue-free 9-table schema, gating env var, maintainer-credential requirement).
7. Comments skill: No issues found. Comments explain the "why" and were verified accurate: the f-string-interpolation safety note (lines 239-246) matches the code's trusted-constant-only interpolation, the two-key advisory-lock comment (lines 773-779) matches `load_catalog_data._schema_lock_key`, and the `conn.rollback()` ACCESS SHARE/ACCESS EXCLUSIVE deadlock explanation (lines 922-930) matches the non-autocommit verification connection.
8. Logging skill: N/A — test module with no logging responsibilities.
9. Exception Handling skill: N/A — no exception handling beyond `try/finally` connection cleanup; expected errors are asserted via `pytest.raises`.
10. Executable Scripts skill: N/A — not an executable script.
11. Data Validation skill: N/A — test module, not a data-pipeline output.
