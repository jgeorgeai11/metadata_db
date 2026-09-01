---
name: cr_20260803v02_test_integration
goal: Re-review of code/load_catalog_data/unit_tests/test_integration.py against python-development skills after the cr_20260803v01 findings were implemented.
created: 2026-08-03 11:32:28
updated: 2026-08-03 11:32:28
---

## Implementation Plan

1. [completed] Optional refinements - `code/load_catalog_data/unit_tests/test_integration.py`
   - 1.1. [suggestion] Line 783: the lock-holder computes the second advisory-lock key via the private helper `lmd._schema_lock_key(TEST_DB, TEST_SCHEMA)` (unit-tests guideline 6: don't rely on private internals); a public constant/helper on the loader would remove the private coupling. Carried forward from cr_20260803v01 (4.1), unchanged.
        - Current: `(lmd.LOADER_LOCK_KEY, lmd._schema_lock_key(TEST_DB, TEST_SCHEMA))`
        - Expected: a public loader-exposed helper (e.g., `lmd.schema_lock_key`) used by both the loader and the test
        - Resolution: Deferred — the adjacent comment documents the coupling deliberately ("Compute the second key via the loader's own helper so the test cannot drift from the loader again"): a hardcoded key already caused real drift once, and promoting the helper to public API is a loader-module change out of scope for this test file.
   - 1.2. [suggestion] Lines 237-435: `test_pk_agreement_and_ltree_types` packs ~25 distinct DDL-conformance probes (PK agreement, dropped columns, NOT NULLs, constraint deferrability, indexes, ltree types) into one 200-line test, so the first failing assert masks the rest (unit-tests guidelines 3 and 5.1); most probes share a "query -> expected scalar" shape that could be parametrized. Carried forward from cr_20260803v01 (4.2), unchanged.
        - Current: one monolithic test body of sequential `assert _scalar(conn, ...) == expected` blocks
        - Expected: a parametrized `(query, expected, id)` table over a module-scoped connection fixture, keeping only the irregular probes inline
        - Resolution: Deferred — the probes jointly pin one behavior ("the applied 0001 DDL matches the design"), each assert carries an identifying message or self-describing query, and the shared connection against the module-scoped DB keeps the gated suite fast; splitting adds ceremony without changing what a failure means.
   - 1.3. [suggestion] Lines 1166 and 1186: the two direct-insert tests use a bare `cur = conn.cursor()` while every other cursor in the file is opened via `with conn.cursor() as cur:` (e.g., `_count`, `_scalar`, `_truncate_all`, the fixture, the lock test); a context manager would make the resource handling uniform.
        - Current: `cur = conn.cursor()` / ... / `finally:` / `conn.rollback()` / `conn.close()`
        - Expected: `with conn.cursor() as cur:` wrapping the setup/violating/select statements
        - Resolution: Deferred — optional consistency polish, not a skill requirement: the `finally` block's `conn.rollback()` + `conn.close()` already releases the cursor and discards the transaction on every path, and keeping the cursor flat avoids a fourth indentation level around the parametrized statement loop and `pytest.raises` block.

## Skills with No Issues

1. Unit Tests skill — naming: No issues found. `test_integration.py` deliberately names the cross-module gated suite rather than mirroring a single source module (the module docstring and the documented invocation command both anchor on this name), and test function names describe scenario and expectation.
2. Unit Tests skill — independence: No issues found. cr_20260803v01 finding 1.1 is implemented — `test_loader_lifecycle` now calls `_truncate_all(conn)` (line 447) before its first load, so every DB-content test resets state first; `test_loader_rejects_design_doc_violations` needs no DB at all (verified: `load_catalog_data.run` calls `validate_corpus` at line 242, before `psycopg2.connect` at line 255, so the expected `ValidationError` raises pre-connection); the direct-insert tests roll back everything in an isolated `zt` namespace. Suggestion 1.1 (private `_schema_lock_key` coupling) remains deferred.
3. Unit Tests skill — pytest usage and exception precision: No issues found. cr_20260803v01 finding 1.2 is implemented — `_BACKSTOP_VIOLATIONS` now carries `type[psycopg2.Error]` per case (`CheckViolation`/`UniqueViolation`, both verified reachable via the bare `import psycopg2`) and the test asserts `pytest.raises(expected_exc)`; the lock tests use `pytest.raises(..., match="already in progress")`; the backstop matrix uses `pytest.param(..., id=cid)`; the module-scoped `integration_db` fixture covers the expensive DB setup. Suggestion 1.2 (monolithic schema-conformance test) remains deferred.
4. Unit Tests skill — mocking: No issues found. As the one end-to-end suite this file intentionally mocks nothing; the only patching is `sys.argv` (and one env var) via `monkeypatch` to drive the real `main()`/`run()`.
5. Type Hints skill: No issues found. cr_20260803v01 finding 2.1 is implemented — the unused `monkeypatch` parameter is gone from `test_advisory_lock_excludes_concurrent_runs` (lines 755-757). Every helper, fixture, and test is annotated with modern syntax (`Iterator[None]`, `dict[str, Any]`, `list[tuple[str, list[str], str, type[psycopg2.Error]]]`); `_scalar`'s `Any` return is genuinely variable across probes.
6. Docstrings skill: No issues found. cr_20260803v01 finding 3.1 is implemented — all three backstop tests now open with docstrings (lines 1157-1163, 1180-1185, 1208-1212), so every function in the file carries one; the module docstring is current (venue-free 9-table schema, gating env var, maintainer-credential requirement).
7. Comments skill: No issues found. Comments explain the "why" and were verified accurate: the f-string-interpolation safety note (lines 239-246) matches the trusted-constant-only interpolation, the two-key advisory-lock comment (lines 774-780) matches `load_catalog_data._schema_lock_key`, the `conn.rollback()` ACCESS SHARE/ACCESS EXCLUSIVE deadlock explanation (lines 923-931) matches the non-autocommit verification connection, and the load_audit arithmetic comment (lines 503-504) sums correctly to 19.
8. Logging skill: N/A — test module with no logging responsibilities.
9. Exception Handling skill: N/A — no exception handling beyond `try/finally` connection cleanup; expected errors are asserted via `pytest.raises`.
10. Executable Scripts skill: N/A — not an executable script.
11. Data Validation skill: N/A — test module, not a data-pipeline output.
