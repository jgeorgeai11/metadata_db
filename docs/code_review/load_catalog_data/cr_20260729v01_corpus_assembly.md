---
name: cr_20260729v01_corpus_assembly
goal: Address code quality issues identified in code/load_catalog_data/corpus_assembly.py to align with python-development skills.
created: 2026-07-29 14:36:26
updated: 2026-07-29 14:36:26
---

## Implementation Plan

1. [completed] Complete docstring Raises/Returns sections on the shape-coercion helpers - `code/load_catalog_data/corpus_assembly.py`
   - 1.1. [minor] Line 352: `_require_mapping` raises `ValueError` on a non-mapping document but its one-line docstring omits the `Raises:` section that every other raising helper in this file carries (e.g. `_assemble_system_row`, `load_yaml`). Document it for consistency with the docstrings skill (Args/Returns/Raises sections).
        - Current: `"""Coerce \`doc\` to a mapping or raise with the offending path."""`
        - Expected: add a `Raises: ValueError: If \`doc\` is not a mapping.` section (and, optionally, `Args`/`Returns`).
        - Resolution: Implemented — expanded the docstring with Args (`doc`, `ident`), Returns (the document unchanged once confirmed a `dict`), and Raises (`ValueError` if `doc` is not a mapping) sections, matching the `load_yaml` style.
   - 1.2. [minor] Line 362: `_require_list` both returns a coerced `list` (with the `None` -> `[]` convenience path) and raises `ValueError` on a non-list document, yet its one-line docstring documents neither the `Returns:` nor the `Raises:` behavior. The `None`-to-empty coercion in particular is a non-obvious contract worth stating.
        - Current: `"""Coerce \`doc\` to a list or raise with the offending path."""`
        - Expected: add `Returns:` (noting `None` becomes `[]`) and `Raises: ValueError` sections.
        - Resolution: Implemented — expanded the docstring with Args (`doc`, `ident`), Returns (stating a `None` document is coerced to an empty list `[]`), and Raises (`ValueError` if `doc` is neither `None` nor a list) sections.

2. [completed] Type specificity of the deployment-expansion context - `code/load_catalog_data/corpus_assembly.py`
   - 2.1. [suggestion] Line 1192: `_expand_schema_entry` takes `ctx: dict[str, Any]` — a heterogeneous string-keyed bag (`system`, `ds_id`, `phys_db`, `path`, `rejections`, `suppressed_ds`) built at line 1465 and read via string subscripts (`ctx["system"]`, `ctx["ds_id"]`). Per the type-hints skill ("be specific, not `Any`"), a small frozen dataclass (e.g. `_DeploymentContext`) would give each field a precise type, catch key typos statically, and make the shared-field set self-documenting.
        - Current: `ctx: dict[str, Any]` with `ctx["system"]`, `ctx["ds_id"]`, `ctx["phys_db"]`, `ctx["path"]`, `ctx["rejections"]`, `ctx["suppressed_ds"]`
        - Expected: a `@dataclass` context object with typed fields, passed in place of the dict
        - Resolution: Deferred — optional structural refactor; the dict is internal to the deployments pass, its keys are set in one place and read in two adjacent functions, and the field roles are already spelled out in the `_expand_schema_entry` docstring, so current form is acceptable and the change carries churn without a correctness gain.

## Skills with No Issues

1. Type Hints: No issues found — every function, nested closure, and module-level container is annotated with modern syntax (`str | None`, `list[...]`, `dict[...]`, `frozenset[str]`); the only `Any` uses are the genuinely-untyped YAML documents. (The `ctx: dict[str, Any]` specificity note is a deferred suggestion, task 2.)
2. Docstrings: Issues found — see task 1 (missing Raises/Returns sections on two coercion helpers).
3. Comments: No issues found — comments consistently explain the "why" (reserved-segment collisions, wave-1 cascade suppression, YAML retyping hazards) rather than restating the code, and stay current with the venue-free / `deployment_tables` model.
4. Logging: No issues found — uses `get_logger(__name__)`, no `print`, a single condensed INFO summary with f-string interpolation; issue reporting is intentionally routed through `AssemblyError` rather than logs.
5. Exception Handling: No issues found — no bare excepts; `load_yaml` catches `(OSError, yaml.YAMLError)` and chains via `from e`; per-row/entry handlers catch specific `ValueError`s to aggregate issues; re-raises preserve context (`raise ... from e`).
6. Executable Scripts: N/A - library module with no `main()` / CLI entry point.
7. Data Validation: N/A - this is corpus-assembly/authoring-shape validation, not a `data_val_` output-quality script.
8. Unit Tests: N/A - this file is the module under review, not its test suite.
