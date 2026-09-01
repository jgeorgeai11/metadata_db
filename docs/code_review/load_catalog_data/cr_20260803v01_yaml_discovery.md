---
name: cr_20260803v01_yaml_discovery
goal: Address code quality issues identified in code/load_catalog_data/yaml_discovery.py to align with python-development skills — re-review since cr_20260729v01 after the naming refactor and granular concept-anchor changes (commits ca45b27, 968ab41, 773d09e).
created: 2026-08-03 10:07:13
updated: 2026-08-03 10:07:13
---

## Implementation Plan

1. [completed] Reduce duplication of the ltree-legal charset - `code/load_catalog_data/yaml_discovery.py`
   - 1.1. [suggestion] Line 243: The legal-character class `[a-z0-9_-]` is spelled inline here, duplicating the definition already compiled into `_LABEL_RE` at line 105. If the canonical charset ever changes, both sites must be updated in lockstep or the "offending character(s)" message drifts from the real rule.
        - Current: `bad = sorted({c for c in value if not re.fullmatch(r"[a-z0-9_-]", c)})`
        - Expected: derive the offending set from a single source of truth, e.g. a module-level `_LABEL_CHARSET = frozenset(string.ascii_lowercase + string.digits + "_-")` used both to build `_LABEL_RE` and as `bad = sorted(set(value) - _LABEL_CHARSET)`
        - Resolution: Deferred — this branch runs only on the invalid-input error path (never in the hot path), and the duplicated class is a 9-character literal whose drift would affect only an error message's wording, not validation correctness. The current form is clear and self-contained; fold in only if the charset is revised.

## Skills with No Issues

1. Type Hints: No issues found — every function and the `PathIdentity` dataclass carry complete, modern-syntax annotations (`str | None`, `tuple[str, ...]`, `dict[str, FileType]`, `tuple[list[PathIdentity], list[str]]`), and `FileType` is a precise `Literal` rather than `str`.
2. Docstrings: No issues found — module, public functions, private helpers (`_decode_parts`, `_validate_schema_segment`), and the dataclass all have Google-style docstrings with Args/Returns/Raises/Attributes as applicable; the module docstring and `decode_path` grammar reflect the current four-depth concept anchoring and shard-folder forms, and `discover_yaml_files`' documented ordering guarantee (sorted identities, classification issues then wrong-extension issues) matches the implementation.
3. Comments: No issues found — comments consistently explain rationale (ltree charset and lowercase canonicalization, length-before-charset check order, wrong-depth rejections for `mappings/` and schema-only shard folders, the virtual-parts wrong-extension guard on case-insensitive filesystems, sorted-walk determinism) rather than restating the code; cross-references (`code/apply_ddl/ddl_catalog/0001_initial_schema.sql`, `readme/metadata-db-maintenance.md`, `corpus_assembly._assemble_concepts`/`_assemble_tables`/`_assemble_column_row`, `code/lib/logconfig`) all resolve, and the "9 file types" comment matches the 9-member `FileType` literal.
4. Logging: No issues found — uses `get_logger` from the vendored `logconfig` (path resolved from the module's own location, with the rationale commented), f-strings throughout, DEBUG for per-file diagnostics and INFO for the run summary; no `print`, no entering/exiting noise, no duplicate logging (per-issue detail logs at DEBUG while the caller reports the aggregate).
5. Exception Handling: No issues found — raises `ValueError` with path and relative-parts context, chains with `from e` in `decode_path`, and the aggregate-not-fail-fast pattern in `discover_yaml_files` is deliberate and documented (environment errors stay fail-fast as `FileNotFoundError`); the `except ValueError: continue` branches in the wrong-extension guard are intentional EAFP classification control flow, commented in place (one with a `pragma: no cover` explaining why it is unreachable in practice).
6. Executable Scripts: N/A - library module with no `main()`/`__main__` entry point.
7. Data Validation: N/A - not a `data_val_` data-validation script.
8. Unit Tests: N/A - this is the source module under review; its tests live in `code/load_catalog_data/unit_tests/test_yaml_discovery.py`.
