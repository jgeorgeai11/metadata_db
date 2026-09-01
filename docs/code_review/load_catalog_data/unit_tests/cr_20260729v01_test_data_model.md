---
name: cr_20260729v01_test_data_model
goal: Re-review of code/load_catalog_data/unit_tests/test_data_model.py (the suite relocated from load_metadata_db/, last reviewed as cr_20260724v02) against python-development (unit-tests, docstrings) skills; the prior substantive gap (untested split_schema_id) is now closed, so only carried-forward optional refinements remain.
created: 2026-07-29 14:39:14
updated: 2026-07-29 14:39:14
---

## Implementation Plan

1. [completed] Reduce duplicated `SystemRow` construction via a fixture - `code/load_catalog_data/unit_tests/test_data_model.py`
   - 1.1. [suggestion] Lines 140-145, 189-191, and 458-464: `sch.SystemRow(system=..., description=..., notes=None, update_reason=None)` is built inline three times (in `test_pk_returns_bare_value_for_single_column_pk`, `test_pk_raises_key_error_for_unknown_table`, and `test_system_row_is_frozen_and_hashable`), differing only in `system`/`description` values. Fixtures already exist for the two composite-PK rows (`sample_relationship_row`, `sample_deployment_row`) but `SystemRow` remains duplicated; a small in-file fixture or factory would remove the repetition (unit-tests skill 3.1) and keep all three call sites resilient to a `SystemRow` signature change.
        - Current: `row = sch.SystemRow(system="sandbox", description="Sandbox venue.", notes=None, update_reason=None)` repeated (with minor value variations) at three sites.
        - Expected:
          ```python
          @pytest.fixture
          def sample_system_row() -> sch.SystemRow:
              """A minimal SystemRow for identity/frozen checks."""
              return sch.SystemRow(
                  system="sandbox",
                  description="Sandbox venue.",
                  notes=None,
                  update_reason=None,
              )
          ```
        - Resolution: Deferred — optional refinement carried from cr_20260724v01/v02; the duplication is small, each site is readable as-is, and all 38 tests pass. Two of the three sites also carry table-specific values (`"s"` / differing descriptions), so a shared fixture would not fully eliminate the local construction.

2. [completed] Parametrize repetitive single-assertion tests - `code/load_catalog_data/unit_tests/test_data_model.py`
   - 2.1. [suggestion] Lines 394-429: the id-builder / `schema_prefix` tests (`test_data_source_id_returns_label` through `test_schema_prefix_of_column_id_returns_first_two_segments`) are each a single input/output assertion over a pure builder; `@pytest.mark.parametrize` (unit-tests skill 5.1) would consolidate them into one or two data-driven tests.
        - Resolution: Deferred — carried from cr_20260724v02; the one-function-per-builder form is defensible since each name documents one invariant, and the inverse-property tests at 432-454 are genuinely distinct.
   - 2.2. [suggestion] Lines 137-193 and 303-313: the `pk()` cases (bare-value, three composite-PK tuples, unknown-table `KeyError`, and the concepts bare-value case) exercise the same function across key shapes; parametrizing over `(row, table, expected_key)` would tighten them into one table-driven test (unit-tests skill 5.1).
        - Resolution: Deferred — carried from cr_20260722v01/cr_20260724v02; two cases consume fixtures, which cannot flow through `parametrize` values, so the split-by-name form remains reasonable. Recorded for visibility only.

3. [completed] Make test documentation consistent - `code/load_catalog_data/unit_tests/test_data_model.py`
   - 3.1. [suggestion] Line 265 (`test_deployment_row_is_frozen_and_hashable`): unlike its sibling `test_system_row_is_frozen_and_hashable` (line 457, which carries an explanatory comment at line 461), this test has neither a docstring nor a comment. A one-line docstring would make documentation uniform across the frozen/hashable pair.
        - Current: `def test_deployment_row_is_frozen_and_hashable(` (line 265, no docstring/comment)
        - Expected:
          ```python
          def test_deployment_row_is_frozen_and_hashable(
              sample_deployment_row: sch.DeploymentRow,
          ) -> None:
              """frozen=True DeploymentRow is hashable; attribute assignment raises."""
          ```
        - Resolution: Deferred — minor documentation-consistency gap carried from cr_20260724v01/v02; per prior-review consensus, the descriptive `test_<function>_<scenario>_<expected>` name satisfies the docstring intent.

## Skills with No Issues

1. type-hints: No issues found — every test function and fixture is fully annotated (`-> None`; fixture return types `sch.TableRelationshipRow` / `sch.DeploymentRow`); no untyped parameters, and the two `# type: ignore[misc]` comments (lines 270, 464) correctly suppress the expected frozen-assignment errors.
2. comments: No issues found — comments explain the "why" (FK-respecting `TABLE_ORDER`, loader-managed `validated_ts`, venue-free identity, `deployment_tables` pure-facts shape, positional row-building constraints in `db_io`) and remain accurate against the current source (e.g., the `deployment_tables` rename and dropped `notes`/`update_reason` are reflected).
3. docstrings: Module docstring present; most tests carry docstrings or explanatory comments. Only the minor consistency gap in finding 3.1 remains, and per prior-review consensus descriptive test names satisfy the intent.
4. unit-tests: Uses pytest with correct file/function naming, behavior-focused tests, fixtures for composite-PK rows, and `pytest.raises` for error paths (`FrozenInstanceError` at lines 269/463, `KeyError` at line 192). `pytest test_data_model.py --cov=data_model --cov-report=term-missing` reports 38 passed and 100% coverage of `data_model.py` (the prior review's untested `split_schema_id()` is now covered by `test_split_schema_id_is_inverse_of_schema_id` / `test_split_schema_id_splits_on_first_dot_only`, lines 440-454).
5. logging: N/A — test module performs no logging.
6. exception-handling: No issues found — `pytest.raises(dataclasses.FrozenInstanceError)` and `pytest.raises(KeyError)` are used specifically; no try/except blocks and no broad catches.
7. data-validation: N/A — no external input parsing/validation in this test module.
8. executable-scripts: N/A — not an executable script (no `__main__` / CLI entry point).
</content>
</invoke>
