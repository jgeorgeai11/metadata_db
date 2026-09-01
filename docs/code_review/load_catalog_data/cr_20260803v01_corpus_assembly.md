---
name: cr_20260803v01_corpus_assembly
goal: Address code quality issues identified in code/load_catalog_data/corpus_assembly.py to align with python-development skills; re-review since cr_20260729v01, whose two minors were implemented and whose ctx-dataclass suggestion remains deferred.
created: 2026-08-03 10:07:38
updated: 2026-08-03 10:07:38
---

## Implementation Plan

1. [completed] Add file/row context to identifier-segment validation errors - `code/load_catalog_data/corpus_assembly.py`
   - 1.1. [minor] Lines 429, 563, 648-649, 788, 900, 1588: Six `validate_identifier_segment` call sites let its `ValueError` propagate unwrapped, so a mis-charset `system`/`table_name`/`column_name`/`relationship_name`/`mapping_name` is recorded in the corpus-wide issues list as e.g. `Invalid table_name segment 'Foo': ...` with no file path or row — the only aggregated issues that do not locate their defect. This contradicts the exception-handling skill ("provide context") and the module's own pattern: sibling call sites already wrap for context (`ref_table` segments at lines 704-708, concept-name segments at lines 1030-1036, `database_name` at lines 1607-1610, and every physical name in `_expand_schema_entry`). It also makes the `Raises` docstrings of `_assemble_table_row`/`_assemble_column_row` inaccurate for this path — both promise "the message names the file and the row"; wrapping fixes the docstring claim too.
        - Current: `validate_identifier_segment(table_name, "table_name")` (line 563; same bare form at 429, 648, 649, 788, 900, 1588)
        - Expected: wrap each call like the `ref_table` site — `try: validate_identifier_segment(table_name, "table_name")` / `except ValueError as e: raise ValueError(f"{e} (in {ident.path}: {raw!r})") from e`
        - Resolution: Implemented as specified — wrapped all six bare call sites in `try/except ValueError` that re-raises `f"{e} (in {ident.path}: {raw!r})" from e`, matching the existing `ref_table` pattern: `system` in `_assemble_system_row`, `table_name` in `_assemble_table_row`, `table_name`+`column_name` in `_assemble_column_row` (folded into one `try` block since they share the same `raw`/`ident` context and the first failure short-circuits), `relationship_name` in `_assemble_table_relationship_row`, `mapping_name` in `_assemble_column_mapping_row`, and `system` in `_assemble_deployment_entry`. The `relationship_name` call was line-wrapped to stay within the line limit under the added indentation. Every wrap now names the file path and the offending row, restoring the "names the file and the row" claim in the `_assemble_table_row`/`_assemble_column_row` `Raises` docstrings; `python -m py_compile` passes.

2. [completed] Optional enhancements - `code/load_catalog_data/corpus_assembly.py`
   - 2.1. [suggestion] Line 304: `_UniqueKeyLoader.construct_mapping` raises `yaml.constructor.ConstructorError` on merge and duplicate keys but its one-line docstring has no Google-style `Raises:` section.
        - Current: `"""Build the mapping, raising on any duplicated or merge key."""`
        - Expected: add a `Raises: yaml.constructor.ConstructorError: On a merge key or a duplicated key.` section
        - Resolution: Deferred — the one-line summary already names the raising behavior, and the class docstring documents both raise conditions and the why in full; a formal section would restate it. The method is also an override of a PyYAML hook, where the local docstring is conventionally brief.
   - 2.2. [suggestion] Line 1340: `_expand_schema_entry` takes `ctx: dict[str, Any]` — a heterogeneous string-keyed bag (`system`, `ds_id`, `phys_db`, `path`, `rejections`, `suppressed_ds`) built in `_assemble_deployment_entry` (line 1613) and read via string subscripts. Per the type-hints skill ("be specific, not `Any`"), a small frozen dataclass would give each field a precise type and catch key typos statically. Carried forward from cr_20260729v01 task 2.1.
        - Current: `ctx: dict[str, Any]` with `ctx["system"]`, `ctx["ds_id"]`, `ctx["phys_db"]`, `ctx["path"]`, `ctx["rejections"]`, `ctx["suppressed_ds"]`
        - Expected: a `@dataclass` context object with typed fields, passed in place of the dict
        - Resolution: Deferred — optional structural refactor, already deferred in cr_20260729v01 for reasons that still hold: the dict is internal to the deployments pass, its keys are set in one place and read in two adjacent functions, and the field roles are spelled out in the `_expand_schema_entry` docstring, so the change carries churn without a correctness gain.

## Skills with No Issues

1. Type Hints: No issues found — every function, nested closure (`_row`, `_record`, `_rejections_for`, `_record_file_failure`), and module-level container is annotated with modern syntax (`str | None`, `list[...]`, `frozenset[str]`); the only `Any` uses are genuinely-untyped YAML documents. (The `ctx: dict[str, Any]` specificity note remains a deferred suggestion, finding 2.2.)
2. Docstrings: No issues found — the two minors from cr_20260729v01 (`_require_mapping`/`_require_list`) are implemented with full Args/Returns/Raises; the new deployment/shard/rejection machinery (`_WaveOneRejections`, `_expand_schema_entry`, `_assemble_deployment_entry`, `_shard_form_conflicts`) is thoroughly documented. (The `construct_mapping` Raises section is a deferred suggestion, finding 2.1; the "names the file and the row" inaccuracy is folded into finding 1.1, whose fix restores it.)
3. Comments: No issues found — comments consistently explain the "why" (reserved-segment collisions, wave-1 cascade suppression, YAML retyping hazards, sort-by-repr rationale, validate-physical-name-first ordering) and are current with the two-pass deployment-expansion and shard-folder model.
4. Logging: No issues found — `get_logger(__name__)`, no `print`, a single condensed INFO summary with f-string interpolation; issue reporting is intentionally routed through `AssemblyError` to the orchestrator's logger rather than duplicated here.
5. Exception Handling: Issues found — see task 1 (six identifier-segment validation errors surface without file/row context). Otherwise clean: no bare excepts, `load_yaml` catches `(OSError, yaml.YAMLError)` and chains via `from e`, per-row/entry handlers catch specific `ValueError`s to aggregate, and every wrap re-raises with `from e`.
6. Executable Scripts: N/A - library module with no `main()` / CLI entry point.
7. Data Validation: N/A - this module is corpus-assembly/authoring-shape validation itself, not a `data_val_` output-quality script.
8. Unit Tests: N/A - this file is the module under review, not its test suite.
