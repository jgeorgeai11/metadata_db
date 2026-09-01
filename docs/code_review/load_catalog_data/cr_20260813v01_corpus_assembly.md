---
name: cr_20260813v01_corpus_assembly
goal: Address code quality issues identified in code/load_catalog_data/corpus_assembly.py to align with python-development skills; re-review since cr_20260803v02 covering the CONTRIBUTING.md rule-number-to-wave reference updates (verified accurate against CONTRIBUTING.md waves 1 and 3) and carrying forward its two deferred suggestions.
created: 2026-08-13 11:05:19
updated: 2026-08-13 11:05:19
---

## Implementation Plan

1. [completed] Optional enhancements - `code/load_catalog_data/corpus_assembly.py`
   - 1.1. [suggestion] Line 305: `_UniqueKeyLoader.construct_mapping` raises `yaml.constructor.ConstructorError` on merge and duplicate keys but its one-line docstring has no Google-style `Raises:` section. Carried forward from cr_20260803v01 finding 2.1 and cr_20260803v02 finding 1.1.
        - Current: `"""Build the mapping, raising on any duplicated or merge key."""`
        - Expected: add a `Raises: yaml.constructor.ConstructorError: On a merge key or a duplicated key.` section
        - Resolution: Deferred — the one-line summary already names the raising behavior, and the class docstring documents both raise conditions and the why in full; a formal section would restate it. The method is also an override of a PyYAML hook, where the local docstring is conventionally brief.
   - 1.2. [suggestion] Line 1395: `_expand_schema_entry` takes `ctx: dict[str, Any]` — a heterogeneous string-keyed bag (`system`, `ds_id`, `phys_db`, `path`, `rejections`, `suppressed_ds`) built in `_assemble_deployment_entry` (line 1675) and read via string subscripts. Per the type-hints skill ("be specific, not `Any`"), a small frozen dataclass would give each field a precise type and catch key typos statically. Carried forward from cr_20260729v01 task 2.1, cr_20260803v01 finding 2.2, and cr_20260803v02 finding 1.2.
        - Current: `ctx: dict[str, Any]` with `ctx["system"]`, `ctx["ds_id"]`, `ctx["phys_db"]`, `ctx["path"]`, `ctx["rejections"]`, `ctx["suppressed_ds"]`
        - Expected: a `@dataclass` context object with typed fields, passed in place of the dict
        - Resolution: Deferred — optional structural refactor, already deferred three times for reasons that still hold: the dict is internal to the deployments pass, its keys are set in one place and read in two adjacent functions, and the field roles are spelled out in the `_expand_schema_entry` docstring, so the change carries churn without a correctness gain.

## Skills with No Issues

1. Type Hints: No issues found — every function, method, and nested closure (`_row`, `_record`, `_rejections_for`, `_record_file_failure`) carries parameter and return annotations in modern syntax (`str | None`, `list[...]`, `frozenset[str]`, `tuple[str, ...]`, `dict[str | tuple[str, ...], Path]`); no `Optional`/`List` legacy forms; the only `Any` uses are genuinely-untyped parsed-YAML documents. (The `ctx: dict[str, Any]` specificity note remains a deferred suggestion, finding 1.2.)
2. Docstrings: No issues found — all assemblers, validators, and the deployment/shard machinery carry Google-style docstrings with Args/Returns/Raises where applicable, and the reworded references in `_check_optional_string_fields`, `_expand_schema_entry`, and `_assemble_deployment_entry` remain accurate: the update_reason discipline is CONTRIBUTING.md wave 3 (rule 20) and the physical-name charset is the deployment file rules in wave 1 (rule 7), as the docstrings now say. (The `construct_mapping` Raises section is a deferred suggestion, finding 1.1.)
3. Comments: No issues found — comments consistently explain the "why" (reserved-segment collisions, wave-1 cascade suppression, YAML retyping hazards, sort-by-repr rationale, validate-physical-name-first ordering, deterministic sorted-path iteration), and this pass's edits keep them current: the stale `CONTRIBUTING.md rule 7` citations at lines 1451-1454 and 1666-1668 now cite the deployment file rules under wave 1, matching the reorganized CONTRIBUTING.md.
4. Logging: No issues found — `get_logger(__name__)` from the vendored logconfig, no `print`, no lazy `%s` formatting, and a single condensed INFO summary with f-string interpolation; issue reporting is intentionally routed through `AssemblyError` to the orchestrator's logger rather than duplicated here.
5. Exception Handling: No issues found — no bare excepts; `load_yaml` catches `(OSError, yaml.YAMLError)` and chains via `from e`; every `validate_identifier_segment` call site either wraps with `raise ValueError(f"{e} (in {ident.path}: {raw!r})") from e` (lines 430-433, 567-570, 655-659, 798-803, 915-918, 1646-1649, 1669-1672, plus the ref_table and concept-name wraps) or appends a deployment-entry-contextualized issue (lines 1455-1461, 1507-1515, 1573-1579); per-row/entry handlers catch specific `ValueError`s to aggregate rather than swallow, and the domain type `AssemblyError` subclasses `ValidationError` rather than wrapping in generic `Exception`.
6. Executable Scripts: N/A - library module with no `main()` / CLI entry point.
7. Data Validation: N/A - this module is corpus-assembly/authoring-shape validation itself, not a `data_val_` output-quality script.
8. Unit Tests: N/A - this file is the module under review, not its test suite.
