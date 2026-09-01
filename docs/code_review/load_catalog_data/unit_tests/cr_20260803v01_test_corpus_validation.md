---
name: cr_20260803v01_test_corpus_validation
goal: Re-review of code/load_catalog_data/unit_tests/test_corpus_validation.py since cr_20260729v01 (concept-anchor tests and naming refactor landed) to align with python-development skills.
created: 2026-08-03 10:14:44
updated: 2026-08-03 10:14:44
---

## Implementation Plan

1. [completed] Test data sharing convention - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 1.1. [suggestion] Line 57: `_happy_corpus()` is a module-level builder function rather than a `@pytest.fixture`; the unit-tests skill (3.1) prefers sharing common test data via fixtures. Carried forward from cr_20260729v01, where it was deferred.
        - Current: `def _happy_corpus() -> Corpus:` called directly in each test (e.g. `corpus = _happy_corpus()`)
        - Expected: a function-scoped `@pytest.fixture` (e.g. `def happy_corpus() -> Corpus: return _happy_corpus()`) injected as a parameter
        - Resolution: Deferred — the builder pattern is deliberate and ergonomic here: nearly every test mutates the returned `Corpus` (and often adds tables/rows) before acting, which reads more directly from a plain call than from an injected fixture. The helper is used only within this file, so keeping it local rather than in conftest.py is reasonable, and each call returns a fresh, independent object, so the isolation goal of fixtures is already met. Same rationale as the cr_20260729v01 deferral.

2. [completed] Test redundancy - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 2.1. [suggestion] Lines 941-942, 972-973, 1173-1174: three tests (`test_validate_corpus_single_relationship_null_use_when_accepted`, `test_validate_corpus_m1_scalar_accepted`, `test_validate_corpus_m9_single_table_accepted`) have the identical one-line body and re-run the exact scenario already covered by `test_validate_corpus_happy_path` (line 164), so they exercise no additional code path.
        - Current: `v.validate_corpus(_happy_corpus())` repeated as the entire body of three separately named tests
        - Expected: rely on `test_validate_corpus_happy_path` alone, or keep one named acceptance test per rule only where the input differs from the baseline
        - Resolution: Deferred — the duplicate executions are cheap, and each named test documents a specific rule's acceptance baseline (single-relationship use_when, M1 scalar, M9 single-table) inside its own rule section; deleting them would trade a few milliseconds for weaker per-rule regression naming.
   - 2.2. [suggestion] Lines 777-787 and 837-847: `test_validate_corpus_b_self_join_single_name_accepted` and `test_validate_corpus_d_self_join_condition_accepted` build near-identical corpora (same self-relationship on `ocs.general.bene` with the same join condition; only the relationship name differs) and assert the same outcome.
        - Current: two structurally identical accepted-self-join tests under the rule-B and rule-D sections
        - Expected: a single self-join acceptance test, or a parametrized case shared by both rule sections
        - Resolution: Deferred — the two tests intentionally guard two different validation rules (B: orientation-duplicate detection; D: endpoint coverage) against independent regressions, and keeping each rule section self-contained is a readability choice consistent with the file's section-per-rule layout.

3. [completed] Docstring formatting - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 3.1. [suggestion] Lines 1025-1026: `_add_two_edw_tables` has a multi-line docstring whose summary wraps onto a second line with no blank line before the continuation; Google style prefers a one-line summary, a blank line, then the body.
        - Current: `"""Add edw tables t1, t2 (one column each). Deploy t1 in edw; t2 in edw\n    when `both_in_edw`, else warehouse only (so they share no venue)."""`
        - Expected: `"""Add edw tables t1 and t2 (one column each).\n\n    Deploy t1 in edw; t2 in edw when `both_in_edw`, else warehouse only (so they share no venue).\n    """`
        - Resolution: Deferred — a formatting nit on a private helper whose content is complete and accurate; the compact form matches the terse one-liner style used by the file's other helpers (`_dep`, `_rel`, `_cm`).

## Skills with No Issues

1. Type Hints: No issues found — every test function is annotated `-> None`, parametrized arguments are typed (`cardinality: str | None`, `expr: str`, `concept_id: str, fragment: str`), and all helpers (`_dep`, `_happy_corpus`, `_rel`, `_cm`, `_edw_column`, `_add_two_edw_tables`, `_link_t1_t2`, `_concept_with_links`, `_bare_concept`) carry full parameter and return annotations using modern syntax.
2. Docstrings: One formatting suggestion (finding 3.1); otherwise the module has a docstring, every helper has one, and test functions convey intent via descriptive names plus rationale comments per project convention.
3. Comments: No issues found — comments consistently explain the "why" (e.g. why a cross-source `ref_table_id` resolves without co-deployment, why whitespace-only notes/use_when are treated as missing, what hole each Task 8.x regression test closes, why `deployment_tables` rows are exempt from update_reason).
4. Unit Tests: Suggestions only (findings 1.1, 2.1, 2.2) — pytest is used throughout; file naming matches `test_<module>.py`; function names follow `test_<function>_<scenario>_<expected>`; tests are AAA-structured and independent (each builds a fresh corpus, no shared mutable state); `pytest.raises(..., match=...)`, `@pytest.mark.parametrize`, and `pytest.param(..., id=...)` are used appropriately; assertions target the public `ValidationError.issues` API rather than private internals.
5. Logging: N/A - test module performs no logging.
6. Exception Handling: N/A - tests assert on raised exceptions rather than handling them; no production error-handling code.
7. Executable Scripts: N/A - not an executable script.
8. Data Validation: N/A - exercises the validation module under test rather than validating pipeline I/O.
9. PySpark / Ibis / SAS Conversion: N/A - no Spark, Ibis, or SAS code present.
