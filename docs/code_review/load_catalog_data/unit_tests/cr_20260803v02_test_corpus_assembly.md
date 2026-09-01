---
name: cr_20260803v02_test_corpus_assembly
goal: Address code quality issues identified in code/load_catalog_data/unit_tests/test_corpus_assembly.py to align with python-development (unit-tests, type-hints, comments, docstrings) skills; re-review since cr_20260803v01, whose minor encoding finding is verified implemented and whose deferred suggestions remain in place.
created: 2026-08-03 11:31:33
updated: 2026-08-03 11:31:33
---

## Implementation Plan

1. [completed] Tighten loose substring assertions (carried over from cr_20260729v01) - `code/load_catalog_data/unit_tests/test_corpus_assembly.py`
   - 1.1. [suggestion] Line 530: `test_data_source_missing_owner_rejected` asserts the bare substring `"owner"`, which can match incidental text in any aggregated issue rather than the specific missing-field message. The companion `test_data_source_missing_owner_and_description_reports_both` (line 563) already uses the precise backticked form.
        - Current: `assert any("owner" in i for i in issues)`
        - Expected: `assert any("`owner`" in i for i in issues)`
        - Resolution: Deferred — optional precision improvement; the assertion still verifies the intended behavior and the exact message form is pinned by the neighbouring both-missing test, so tightening here adds little coverage.
   - 1.2. [suggestion] Line 540: `test_data_source_missing_description_rejected` asserts `"description" in i.lower()`, an even broader match (the word appears in many unrelated messages). A backticked / message-specific fragment would be more precise.
        - Current: `assert any("description" in i.lower() for i in issues)`
        - Expected: `assert any("`description`" in i for i in issues)`
        - Resolution: Deferred — optional; the loose form is intentionally lenient and the exact wording is asserted by the both-missing test, so the risk of a false pass is low.

2. [completed] Define shared helpers before first use (carried over from cr_20260729v01) - `code/load_catalog_data/unit_tests/test_corpus_assembly.py`
   - 2.1. [suggestion] Lines 703 / 725: `test_tables_missing_or_blank_description_rejected` and `test_columns_missing_or_blank_description_rejected` call `_path_id(...)`, but that helper is defined later at line 851 (under the "Document-shape guards" banner). It works because pytest resolves the name at run time, but forward references make the file harder to follow top-to-bottom.
        - Current: `_path_id` first used at line 703, defined at line 851
        - Expected: move `_path_id` above its earliest use, or relocate the two description tests to sit after the helper's definition
        - Resolution: Deferred — cosmetic ordering only; the helper resolves correctly at collection/run time and the current grouping keeps `_path_id` beside the shape-guard tests it primarily serves.

## Skills with No Issues

1. unit-tests: No issues found — file/function naming follow `test_<module>` / `test_<function>_<scenario>_<expected>`; pytest is used throughout with `@pytest.mark.parametrize`, `pytest.raises`, `tmp_path`, and a function-scoped `example_corpus` fixture; tests are independent (each builds its own tree under `tmp_path`, and input-order independence of the loader itself is asserted in `test_assemble_corpus_issue_order_independent_of_input_order`); assertions target the public `AssemblyError.issues` contract, not private state.
2. type-hints: No issues found — every helper, fixture, test, nested function (`_run`), and module-level constant carries modern annotations (`list[str]`, `str | None`, `dict[str, Any]`, `tuple[...]`). The single `# type: ignore[arg-type]` in `_path_id` is a justified narrowing to the `file_type` Literal. The cr_20260803v01 minor (missing `encoding="utf-8"` on four `write_text` calls at lines 161, 167, 180, 190) is verified implemented; all `write_text` calls in the file now pass the encoding explicitly.
3. comments: No issues found — inline comments consistently explain the "why" (the rule or regression under test), and the section banners keep the 2,800-line suite navigable.
4. docstrings: No issues found — the module and all shared helpers/fixtures have docstrings; per standard pytest practice the test functions rely on descriptive names plus intent comments rather than docstrings.
5. exception-handling: N/A — test code asserts on raised exceptions via `pytest.raises`; it does not implement production exception handling.
6. logging: N/A - no logging in test code.
7. data-validation: N/A — this is the test suite exercising the loader's validation, not a module performing validation itself.
8. executable-scripts: N/A - not an executable script.
