---
name: cr_20260803v02_corpus_assembly
goal: Address code quality issues identified in code/load_catalog_data/corpus_assembly.py to align with python-development skills; re-review since cr_20260803v01, verifying its identifier-context minor is implemented (all six call sites now wrap with file/row context) and carrying its two deferred suggestions forward.
created: 2026-08-03 11:31:48
updated: 2026-08-03 11:31:48
---

## Implementation Plan

1. [completed] Optional enhancements - `code/load_catalog_data/corpus_assembly.py`
   - 1.1. [suggestion] Line 304: `_UniqueKeyLoader.construct_mapping` raises `yaml.constructor.ConstructorError` on merge and duplicate keys but its one-line docstring has no Google-style `Raises:` section. Carried forward from cr_20260803v01 finding 2.1.
        - Current: `"""Build the mapping, raising on any duplicated or merge key."""`
        - Expected: add a `Raises: yaml.constructor.ConstructorError: On a merge key or a duplicated key.` section
        - Resolution: Deferred — the one-line summary already names the raising behavior, and the class docstring documents both raise conditions and the why in full; a formal section would restate it. The method is also an override of a PyYAML hook, where the local docstring is conventionally brief.
   - 1.2. [suggestion] Line 1394: `_expand_schema_entry` takes `ctx: dict[str, Any]` — a heterogeneous string-keyed bag (`system`, `ds_id`, `phys_db`, `path`, `rejections`, `suppressed_ds`) built in `_assemble_deployment_entry` (line 1670) and read via string subscripts. Per the type-hints skill ("be specific, not `Any`"), a small frozen dataclass would give each field a precise type and catch key typos statically. Carried forward from cr_20260729v01 task 2.1 and cr_20260803v01 finding 2.2.
        - Current: `ctx: dict[str, Any]` with `ctx["system"]`, `ctx["ds_id"]`, `ctx["phys_db"]`, `ctx["path"]`, `ctx["rejections"]`, `ctx["suppressed_ds"]`
        - Expected: a `@dataclass` context object with typed fields, passed in place of the dict
        - Resolution: Deferred — optional structural refactor, already deferred twice for reasons that still hold: the dict is internal to the deployments pass, its keys are set in one place and read in two adjacent functions, and the field roles are spelled out in the `_expand_schema_entry` docstring, so the change carries churn without a correctness gain.

## Skills with No Issues

1. Type Hints: No issues found — every function, method, and nested closure (`_row`, `_record`, `_rejections_for`, `_record_file_failure`) carries parameter and return annotations in modern syntax (`str | None`, `list[...]`, `frozenset[str]`, `tuple[str, ...]`); no `Optional`/`List` legacy forms; the only `Any` uses are genuinely-untyped parsed-YAML documents. (The `ctx: dict[str, Any]` specificity note remains a deferred suggestion, finding 1.2.)
2. Docstrings: No issues found — all assemblers, validators, and the deployment/shard machinery carry Google-style docstrings with Args/Returns/Raises where applicable, and the `Raises` claims of `_assemble_system_row`, `_assemble_table_row`, `_assemble_column_row`, `_assemble_table_relationship_row`, `_assemble_column_mapping_row`, and `_assemble_deployment_entry` are now accurate: the cr_20260803v01 finding 1.1 wraps mean every identifier-segment failure names the file and the row. (The `construct_mapping` Raises section is a deferred suggestion, finding 1.1.)
3. Comments: No issues found — comments consistently explain the "why" (reserved-segment collisions, wave-1 cascade suppression, YAML retyping hazards, sort-by-repr rationale, validate-physical-name-first ordering, deterministic sorted-path iteration) and are current with the two-pass deployment-expansion and shard-folder model.
4. Logging: No issues found — `get_logger(__name__)` from the vendored logconfig, no `print`, no lazy `%s` formatting, and a single condensed INFO summary with f-string interpolation; issue reporting is intentionally routed through `AssemblyError` to the orchestrator's logger rather than duplicated here.
5. Exception Handling: No issues found — the cr_20260803v01 minor is implemented: all twelve `validate_identifier_segment` call sites now either wrap with `raise ValueError(f"{e} (in {ident.path}: {raw!r})") from e` (lines 429-432, 566-569, 654-658, 797-802, 914-917, 1642-1645, plus the pre-existing ref_table/concept-name/physical-database wraps) or append a deployment-entry-contextualized issue (lines 1452-1458, 1504-1513, 1570-1576). No bare excepts; `load_yaml` catches `(OSError, yaml.YAMLError)` and chains via `from e`; per-row/entry handlers catch specific `ValueError`s to aggregate rather than swallow.
6. Executable Scripts: N/A - library module with no `main()` / CLI entry point.
7. Data Validation: N/A - this module is corpus-assembly/authoring-shape validation itself, not a `data_val_` output-quality script.
8. Unit Tests: N/A - this file is the module under review, not its test suite.
