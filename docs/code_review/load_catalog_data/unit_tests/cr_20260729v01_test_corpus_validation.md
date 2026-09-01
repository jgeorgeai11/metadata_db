---
name: cr_20260729v01_test_corpus_validation
goal: Address code quality issues identified in code/load_catalog_data/unit_tests/test_corpus_validation.py to align with python-development skills.
created: 2026-07-29 14:36:15
updated: 2026-07-29 14:36:15
---

## Implementation Plan

1. [completed] Test data sharing convention - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 1.1. [suggestion] Line 57: `_happy_corpus()` is a module-level builder function rather than a `@pytest.fixture`; the unit-tests skill (3.1) prefers sharing common test data via fixtures.
        - Current: `def _happy_corpus() -> Corpus:` called directly in each test (e.g. `corpus = _happy_corpus()`)
        - Expected: a function-scoped `@pytest.fixture` (e.g. `def happy_corpus() -> Corpus: return _happy_corpus()`) injected as a parameter
        - Resolution: Deferred — the builder pattern is deliberate and ergonomic here: nearly every test mutates the returned `Corpus` (and often adds tables/rows) before acting, which reads more directly from a plain call than from an injected fixture. The helper is used only within this file, so keeping it local rather than in conftest.py is reasonable. Each call already returns a fresh, independent object, so the isolation goal of fixtures is met.

## Skills with No Issues

1. Type Hints: No issues found — every test function is annotated `-> None`, and all helper builders (`_dep`, `_happy_corpus`, `_rel`, `_cm`, `_edw_column`, `_concept_with_links`, `_bare_concept`, `_add_two_edw_tables`, `_link_t1_t2`) carry full parameter and return annotations using modern syntax (e.g. `str | None`).
2. Docstrings: No issues found — the module has a docstring, and every helper function has a Google-style-appropriate one-line docstring; test functions convey intent via descriptive names plus inline rationale comments per project convention.
3. Comments: No issues found — inline comments consistently explain the "why" (e.g. why a cross-source pointer is allowed, why whitespace notes are treated as missing, what hole a regression test closes).
4. Unit Tests: No issues found — pytest is used throughout; file/function naming follows `test_<module>` / `test_<function>_<scenario>_<expected>`; tests are AAA-structured and mutually independent (each builds its own corpus, no shared mutable state); `pytest.raises(..., match=...)` and `@pytest.mark.parametrize` are used appropriately; assertions target the public `ValidationError.issues` API, not private internals.
5. Logging: N/A - test module performs no logging.
6. Exception Handling: N/A - tests assert on raised exceptions rather than handling them; no production error-handling code.
7. Executable Scripts: N/A - not an executable script.
8. Data Validation: N/A - exercises the validation module under test rather than validating pipeline I/O.
9. PySpark / Ibis / SAS Conversion: N/A - no Spark, Ibis, or SAS code present.
