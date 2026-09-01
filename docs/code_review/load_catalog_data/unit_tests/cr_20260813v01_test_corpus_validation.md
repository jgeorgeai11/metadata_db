---
name: cr_20260813v01_test_corpus_validation
goal: Re-review code/load_catalog_data/unit_tests/test_corpus_validation.py against python-development skills since cr_20260803v01, after the `validate_update_reason` call sites dropped the `corpus` argument and the rule-reference comments were reworded to the CONTRIBUTING.md wave language.
created: 2026-08-13 11:06:15
updated: 2026-08-13 11:15:46
---

## Implementation Plan

1. [completed] Remove dead corpus setup left behind by the `validate_update_reason` signature change - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 1.1. [minor] Lines 1411, 1306/1315: Two `validate_update_reason` tests retain a `corpus` that is never read now that the function takes only `diff`. In `test_validate_update_reason_happy` (line 1411) `corpus = _happy_corpus()` is entirely unused; in `test_validate_update_reason_concept_insert_with_reason_raises` (lines 1306, 1315) the corpus is built and mutated (`corpus.concepts[_CLAIM_ID] = row`) but never read — the diff is constructed from `row` directly. Stale arrange code misstates what the test depends on (comments/keep-current principle and AAA clarity).
        - Current: `corpus = _happy_corpus()` (line 1411, unused) / `corpus.concepts[_CLAIM_ID] = row` (line 1315, corpus never read afterward)
        - Expected: delete the unused `corpus` assignment in `test_validate_update_reason_happy`; in the concept test, build `row` directly and drop the corpus construction and write-back.
        - Resolution: Implemented as specified — deleted the unused `corpus = _happy_corpus()` line in `test_validate_update_reason_happy`, and in `test_validate_update_reason_concept_insert_with_reason_raises` removed both the corpus construction and the `corpus.concepts[_CLAIM_ID] = row` write-back (the `row` was already built directly and feeds the diff unchanged).
   - 1.2. [minor] Lines 1387, 1400, 1525, 1538: Four systems-row gate tests keep a write-back `corpus.systems["warehouse"] = row` that was only meaningful when the corpus was passed to `validate_update_reason`; the corpus read on the preceding line (to derive `row` via `replace`) is still live, but the write-back is dead code in `test_validate_update_reason_insert_with_reason_raises`, `test_validate_update_reason_update_without_reason_raises`, `test_validate_update_reason_update_with_whitespace_reason_raises`, and `test_validate_update_reason_update_with_real_reason_passes`.
        - Current: `row = replace(corpus.systems["warehouse"], update_reason=...)` followed by `corpus.systems["warehouse"] = row` with `corpus` never read again
        - Expected: drop the four write-back lines (the diff is built from `row` directly).
        - Resolution: Implemented as specified — removed the dead `corpus.systems["warehouse"] = row` write-back from all four gate tests; the live `corpus = _happy_corpus()` read that derives `row` via `replace` was kept in each. Full module suite passes (110 tests).

2. [completed] Test data sharing convention - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 2.1. [suggestion] Line 57: `_happy_corpus()` is a module-level builder function rather than a `@pytest.fixture`; the unit-tests skill (3.1) prefers sharing common test data via fixtures. Carried from cr_20260803v01 (1.1).
        - Current: `def _happy_corpus() -> Corpus:` called directly in each test (e.g. `corpus = _happy_corpus()`)
        - Expected: a function-scoped `@pytest.fixture` (e.g. `def happy_corpus() -> Corpus: return _happy_corpus()`) injected as a parameter
        - Resolution: Deferred — the builder pattern is deliberate and ergonomic here: nearly every test mutates the returned `Corpus` before acting, which reads more directly from a plain call than from an injected fixture; each call returns a fresh, independent object, so the isolation goal of fixtures is already met. Same rationale as the cr_20260803v01 deferral. Note the sibling `test_corpus_diff.py` follows the same builder convention (see cr_20260813v01_test_corpus_diff.md, finding 2.1).
   - 2.2. [suggestion] Lines 1305-1544: With the `corpus` parameter gone from `validate_update_reason`, the gate tests only need individual rows, so most no longer need `_happy_corpus()` at all — a small `_system`-style row builder (as `test_corpus_diff.py` uses, lines 21-22 there) would make the arrange sections cheaper and more direct.
        - Current: each gate test builds a full nine-table `_happy_corpus()` just to pluck or derive one row.
        - Expected: derive rows from a minimal builder (or `replace` on a module-level constant row) in the `validate_update_reason` section.
        - Resolution: Deferred — building the happy corpus is cheap, keeps every section of the file on one shared baseline, and the live reads are correct; purely an optional economy once finding 1.x removes the dead writes.

3. [completed] Test redundancy - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 3.1. [suggestion] Lines 941-942, 972-973, 1173-1174: three tests (`test_validate_corpus_single_relationship_null_use_when_accepted`, `test_validate_corpus_m1_scalar_accepted`, `test_validate_corpus_m9_single_table_accepted`) have the identical one-line body and re-run the exact scenario already covered by `test_validate_corpus_happy_path` (line 164). Carried from cr_20260803v01 (2.1).
        - Current: `v.validate_corpus(_happy_corpus())` repeated as the entire body of three separately named tests
        - Expected: rely on `test_validate_corpus_happy_path` alone, or keep one named acceptance test per rule only where the input differs from the baseline
        - Resolution: Deferred — the duplicate executions are cheap, and each named test documents a specific rule's acceptance baseline inside its own rule section; deleting them would trade a few milliseconds for weaker per-rule regression naming. Same rationale as the cr_20260803v01 deferral.
   - 3.2. [suggestion] Lines 777-787 and 837-847: `test_validate_corpus_b_self_join_single_name_accepted` and `test_validate_corpus_d_self_join_condition_accepted` build near-identical corpora (same self-relationship on `ocs.general.bene` with the same join condition; only the relationship name differs) and assert the same outcome. Carried from cr_20260803v01 (2.2).
        - Current: two structurally identical accepted-self-join tests under the rule-B and rule-D sections
        - Expected: a single self-join acceptance test, or a parametrized case shared by both rule sections
        - Resolution: Deferred — the two tests intentionally guard two different validation rules (B: orientation-duplicate detection; D: endpoint coverage) against independent regressions, and keeping each rule section self-contained is a readability choice consistent with the file's section-per-rule layout. Same rationale as the cr_20260803v01 deferral.

4. [completed] Docstring formatting - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 4.1. [suggestion] Lines 1024-1026: `_add_two_edw_tables` has a multi-line docstring whose summary wraps onto a second line with no blank line before the continuation; Google style prefers a one-line summary, a blank line, then the body. Carried from cr_20260803v01 (3.1).
        - Current: `"""Add edw tables t1, t2 (one column each). Deploy t1 in edw; t2 in edw\n    when `both_in_edw`, else warehouse only (so they share no venue)."""`
        - Expected: `"""Add edw tables t1 and t2 (one column each).\n\n    Deploy t1 in edw; t2 in edw when `both_in_edw`, else warehouse only (so they share no venue).\n    """`
        - Resolution: Deferred — a formatting nit on a private helper whose content is complete and accurate; the compact form matches the terse one-liner style used by the file's other helpers (`_dep`, `_rel`, `_cm`). Same rationale as the cr_20260803v01 deferral.

## Skills with No Issues

1. Type Hints: No issues found — every test function is annotated `-> None`, parametrized arguments are typed (`cardinality: str | None`, `expr: str`, `concept_id: str, fragment: str`), and all helpers carry full parameter and return annotations using modern syntax.
2. Docstrings: One formatting suggestion (finding 4.1); otherwise the module has a docstring, every helper has one, and test functions convey intent via descriptive names plus rationale comments per project convention.
3. Comments: No issues found beyond the dead-code findings in task 1 — the reworded rule references (deployment file rules / target-expression shape rule, lines 417-419, 1071-1074) now name the CONTRIBUTING.md wave concepts instead of retired numbered rules, and the de-tasked section headers (lines 1417, 1518, 1548) describe the rule rather than a stale plan task.
4. Unit Tests: Two minor dead-arrange findings (1.1, 1.2) plus suggestions (2.1, 2.2, 3.1, 3.2) — pytest is used throughout; file naming matches `test_<module>.py`; function names follow `test_<function>_<scenario>_<expected>`; all `validate_update_reason` call sites were updated to the one-argument signature consistently with `test_corpus_diff.py`; `pytest.raises(..., match=...)`, `@pytest.mark.parametrize`, and `pytest.param(..., id=...)` are used appropriately; assertions target the public `ValidationError.issues` API rather than private internals.
5. Logging: N/A - test module performs no logging.
6. Exception Handling: N/A - tests assert on raised exceptions rather than handling them; no production error-handling code.
7. Executable Scripts: N/A - not an executable script.
8. Data Validation: N/A - exercises the validation module under test rather than validating pipeline I/O.
9. PySpark / Ibis / SAS Conversion: N/A - no Spark, Ibis, or SAS code present.
