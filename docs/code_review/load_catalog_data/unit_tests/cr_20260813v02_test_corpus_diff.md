---
name: cr_20260813v02_test_corpus_diff
goal: Re-review code/load_catalog_data/unit_tests/test_corpus_diff.py against python-development skills since cr_20260813v01, reviewed as a group with corpus_validation.py and the sibling test_corpus_validation.py, adding a helper type-hint inaccuracy and the group's fixture-convention divergences.
created: 2026-08-13 11:48:48
updated: 2026-08-13 12:21:32
---

## Implementation Plan

1. [completed] Helper annotation contradicts the row contract - `code/load_catalog_data/unit_tests/test_corpus_diff.py`
   - 1.1. [minor] Line 21: `_system`'s `description: str | None = "x"` declares the field optional, but `SystemRow.description` is `str` — required, and documented as such ("Freeform; required (an undescribed venue tells a consumer nothing)", `data_model.py` lines 184-191). No call site passes `None`, so the looser annotation buys nothing and invites a future test to construct a row the loader's own model forbids; the type-hints skill requires annotations to be specific and to stay current with the types they describe. The sibling `test_corpus_validation.py` builds its `SystemRow`s with a plain `str` description throughout.
        - Current: `def _system(name: str, description: str | None = "x", reason: str | None = None) -> SystemRow:`
        - Expected: `def _system(name: str, description: str = "x", reason: str | None = None) -> SystemRow:`
        - Resolution: Implemented as specified — `description` is now annotated `str`, matching `SystemRow.description`. No call site passed `None`, so no test changed. Deviation: the line-width wrap floated by suggestion 2.2 was not folded in — 2.2 stays deferred, and the tightened annotation leaves the signature shorter than before.

2. [completed] Helper docstrings and line width - `code/load_catalog_data/unit_tests/test_corpus_diff.py`
   - 2.1. [suggestion] Lines 21-22, 28-40: `_system` and `_concept` carry no docstring, while every other builder in this file (`_deployment`, `_rel`, `_mapping`, `_state_with_systems`, `_keep_in_corpus`) and every builder in the sibling `test_corpus_validation.py` does — two conventions for one concern inside one file. Carried from cr_20260813v01 (1.1).
        - Current: `def _system(name: str, description: str | None = "x", reason: str | None = None) -> SystemRow:` followed directly by the return statement
        - Expected: a one-line docstring each, e.g. `"""Build a SystemRow with the given name, description, and update_reason."""`
        - Resolution: Deferred — both are private single-expression builders whose fully annotated signatures are self-describing, and the docstrings skill mandates docstrings for public functions only; alignment with the file's other helpers is an optional consistency polish.
   - 2.2. [suggestion] Lines 21, 22, 148: the file's only three lines over ~79 columns (94, 92, and 86) are the `_system` signature, its return, and one call; every other line in this module — and every line in `corpus_validation.py` — stays inside the wrap.
        - Current: `def _system(name: str, description: str | None = "x", reason: str | None = None) -> SystemRow:`
        - Expected: wrap the signature and the `SystemRow(...)` call across lines, as the neighbouring `_concept` / `_deployment` builders do.
        - Resolution: Deferred — cosmetic only, no linter enforces a width in `pyproject.toml`, and the three lines are readable as written; worth folding in if finding 1.1 is implemented, since that edit already touches the signature.

3. [completed] Cross-file consistency with the sibling test module - `code/load_catalog_data/unit_tests/test_corpus_diff.py`
   - 3.1. [suggestion] Lines 21-100 vs `test_corpus_validation.py` lines 38-54, 649-702: the two sibling test modules answer the same question — "how do we build a row fixture?" — two different ways. The same concepts carry different names and shapes (`_deployment(physical_table_name=...)` here vs `_dep(table_id, system, ds_id, db, schema, table)` there; `_mapping(target_expression, reason)` here vs `_cm(source, name, expr, use_when)` there; `_rel(cardinality, reason)` here — closing over a fixed `_REL_KEY` — vs `_rel(a, b, name, cond, ...)` there), and `_REL_KEY = ("ocs.general.bene", "ocs.general.claim", "default")` is duplicated verbatim in both. `unit_tests/conftest.py` already exists as the shared-fixture home the unit-tests skill (3.1) points at. Recorded in both reviews — see cr_20260813v02_test_corpus_validation.md finding 3.1.
        - Current: two private builder sets with overlapping intent and clashing signatures for the same row types
        - Expected: promote the shared row builders and key constants into `unit_tests/conftest.py` (or a `_row_builders.py` helper module) with one signature per row type, and import them in both test modules
        - Resolution: Deferred — each module's builders are tuned to its own needs (this file varies one content field on a fixed PK; the validation file varies endpoints and expressions per test), so a merged signature would be wider than either caller wants; the duplication is small, local, and does not risk drift in assertions.
   - 3.2. [suggestion] Line 25: `_CLAIM_ID = "sandbox_ocs.concept.claim"` anchors to a `sandbox_ocs` data source that no other fixture in this file mentions — `_DEP_KEY`, `_REL_KEY`, and `_MAP_KEY` (lines 43, 61, 83) all use `ocs.general.*` — and the sibling defines the same constant name with the `ocs` value (`test_corpus_validation.py` line 1180). One constant name, two id namespaces across the group.
        - Current: `_CLAIM_ID = "sandbox_ocs.concept.claim"`
        - Expected: `_CLAIM_ID = "ocs.concept.claim"`, matching this file's other fixture ids and the sibling's value
        - Resolution: Deferred — `corpus_diff` compares rows by PK and content without resolving concept anchors, so the prefix is inert here and no assertion depends on it; renaming is a cosmetic consistency change only.

4. [completed] Test data sharing convention - `code/load_catalog_data/unit_tests/test_corpus_diff.py`
   - 4.1. [suggestion] Lines 21-100: shared test data comes from module-level builder functions (`_system`, `_concept`, `_deployment`, `_rel`, `_mapping`) rather than `@pytest.fixture`s; the unit-tests skill (3.1) prefers fixtures for shared data. Carried from cr_20260813v01 (2.1).
        - Current: plain `_system("warehouse", description="NEW", reason="x")`-style calls inside each test
        - Expected: function-scoped fixtures injected as parameters
        - Resolution: Deferred — the builders are parameterized (each call varies description/reason/expression), which fixtures express awkwardly; every call returns a fresh, independent object, so the isolation goal of fixtures is already met, and the convention matches the sibling test file. Same rationale as the cr_20260813v01 deferral.

5. [completed] Coverage completeness for the non-empty diff branch - `code/load_catalog_data/unit_tests/test_corpus_diff.py`
   - 5.1. [minor] Lines 108-115: `Diff.is_empty()` and `Diff.summary()` are asserted directly only for the empty case (`is_empty() is True`, the all-zero summary string); the non-empty branch of both public methods is exercised only implicitly through `compute_diff` tests that assert list contents, never as `is_empty() is False` or a non-zero summary string. Carried from cr_20260813v01 (3.1). Re-triaged from [suggestion] to [minor] on 2026-08-13: "already executed, so 100% statement coverage" is exactly why the gap is invisible — execution without assertion is what let the counts in `summary()` go unpinned, and that string is the loader's dry-run headline (`load_catalog_data.py` logs `DRY RUN — {diff.summary()}`), the one line a pre-merge reviewer reads to decide whether a diff is sane. A transposed insert/delete count there would pass this suite today.
        - Current: `assert d.is_empty() is True` / `assert d.summary() == "Diff: 0 insert(s), 0 update(s), 0 delete(s)"` on an empty `Diff()` only
        - Expected: one companion assertion on a populated diff, e.g. `assert d.is_empty() is False` and `assert d.summary() == "Diff: 1 insert(s), 0 update(s), 0 delete(s)"` inside an existing insert test
        - Resolution: Implemented as specified — `test_compute_diff_insert_only` now closes with `assert d.is_empty() is False` and `assert d.summary() == "Diff: 1 insert(s), 0 update(s), 0 delete(s)"`, pinning the counts in the loader's dry-run headline, with a comment recording why the assertions live there. Kept inside the existing test rather than adding a new one, so the populated `Diff` is the one `compute_diff` actually produced.

## Skills with No Issues

1. Type Hints: One inaccuracy (1.1); otherwise no issues — every test function is annotated `-> None`, and the remaining builders carry full modern annotations (`str | None`, `tuple[str, ...]`, precise row-class returns), with `_state_with_systems(n: int) -> DbState` and `_keep_in_corpus(state: DbState, keep: range) -> Corpus` specific to the types they build.
2. Docstrings: One consistency suggestion (2.1); the module docstring states scope, and the non-obvious helpers (`_deployment`'s pure-facts note, `_rel`'s venue-free note, `_mapping`'s composite-key note) document the "why".
3. Comments: No issues found — comments explain rationale, not mechanics (why the deployment insert-then-rediff idempotency pin exists at lines 250-254, why insert normalization is gone at lines 272-275, why `deployment_tables` counts in the mass-delete denominator at lines 507-509), and the gate call at line 282 matches the current one-argument `validate_update_reason(d)` signature in `corpus_validation.py` (line 929).
4. Unit Tests: Suggestions only (3.1, 3.2, 4.1, 5.1) — pytest throughout; file name matches `test_<module>.py` for `corpus_diff.py`; function names follow `test_<function>_<scenario>_<expected>`; tests are AAA-structured and independent (fresh `empty_corpus()`/`empty_db_state()` per test, no shared mutable state); `pytest.raises(..., match=...)` verifies `ValidationError` and `MassDeleteError` messages; coverage spans insert/update/delete/no-op, PK-change delete-plus-insert, all three composite-key tables, tuple-shaped `RowChange.key` assertions, reason-only updates, mixed classifications, and the mass-delete guard's min-count, exceeding-fraction, below-fraction, and exact-boundary paths; the 32 tests pass and drive `corpus_diff.py` to 100% statement coverage.
5. Logging: N/A - test module performs no logging.
6. Exception Handling: N/A - tests assert on raised exceptions rather than handling them; no production error-handling code.
7. Executable Scripts: N/A - not an executable script.
8. Data Validation: N/A - exercises the diff module under test rather than validating pipeline I/O.
9. SAS Conversion / PySpark / Ibis: N/A - no SAS, Spark, or Ibis code present.
