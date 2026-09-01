---
name: cr_20260702v01_conftest
goal: Address code quality issues identified in code/apply_ddl/unit_tests/conftest.py to align with python-development skills.
created: 2026-07-02
updated: 2026-07-02
---

## Implementation Plan

1. [completed] Fixture design - `code/apply_ddl/unit_tests/conftest.py`
   - 1.1. [suggestion] Lines 16-19: Constrain the cursor mock to the real psycopg2 cursor surface with `spec` so that typos or references to non-existent attributes/methods fail loudly instead of silently returning a new `MagicMock`. This tightens the "mock external boundaries" contract without changing any test.
        - Current:
          ```python
          @pytest.fixture
          def fake_cursor() -> MagicMock:
              """A mock psycopg2 cursor (the object yielded by `with conn.cursor()`)."""
              return MagicMock()
          ```
        - Expected (illustrative):
          ```python
          @pytest.fixture
          def fake_cursor() -> MagicMock:
              """A mock psycopg2 cursor (the object yielded by `with conn.cursor()`)."""
              return MagicMock(spec=psycopg2.extensions.cursor)
          ```
   - 1.2. [suggestion] Lines 22-29: The `fake_conn` docstring states the cursor context manager yields `fake_cursor` but does not note that `__exit__` is wired to return `False` so exceptions propagate (relied on by `test_apply_one_rolls_back_on_error`). Recording this "why" makes the fixture contract explicit for future test authors.
        - Current: `conn.cursor.return_value.__exit__.return_value = False`
        - Expected: unchanged code, plus a docstring/comment noting that `__exit__` returns `False` so exceptions raised inside the `with` block are not suppressed.

2. [completed] Docstring style - `code/apply_ddl/unit_tests/conftest.py`
   - 2.1. [minor] Lines 24-25: The `fake_conn` summary wraps across two physical lines. PEP 257 / the project docstring style prefer a concise single-line summary (with any elaboration in a following paragraph).
        - Current:
          ```python
          """A mock psycopg2 connection whose cursor() context manager yields
          `fake_cursor`."""
          ```
        - Expected:
          ```python
          """A mock psycopg2 connection whose cursor() context manager yields `fake_cursor`."""
          ```

## Skills with No Issues

1. Type Hints: No issues found — both fixtures annotate parameters and return types (`fake_cursor() -> MagicMock`, `fake_conn(fake_cursor: MagicMock) -> MagicMock`); `MagicMock` is the accurate, specific type for mock fixtures.
2. Comments: No issues found — the `sys.path` block comment explains the "why" (resolving `import apply_ddl` regardless of pytest's rootdir handling) rather than the "what".
3. Unit Tests (fixture design / conftest placement): No issues found beyond the suggestions above — fixtures live in `conftest.py` for auto-discovery, are function-scoped (correct for per-test mocks), mock only the psycopg2 boundary, and `fake_conn` correctly composes `fake_cursor`.
4. sys.path manipulation pattern: No issues found — uses `Path(__file__).resolve().parent.parent` with a guarded `if str(...) not in sys.path` insert, matching the established idiom in `load_metadata_db/unit_tests/conftest.py` and `revert_merge/unit_tests/conftest.py` (absolute, deduplicated).
5. Logging: N/A — shared test fixtures perform no logging.
6. Exception Handling: N/A — no exception handling logic in this file (the mock only wires context-manager return values).
7. Executable Scripts: N/A — this is a pytest conftest, not a CLI script.
8. Data Validation: N/A — no data inputs/outputs to validate.
9. SQL best-practices: N/A — no SQL in this file.
