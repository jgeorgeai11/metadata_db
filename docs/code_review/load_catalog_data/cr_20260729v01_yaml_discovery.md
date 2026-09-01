---
name: cr_20260729v01_yaml_discovery
goal: Address code quality issues identified in code/load_catalog_data/yaml_discovery.py to align with python-development skills.
created: 2026-07-29 14:36:13
updated: 2026-07-29 14:36:13
---

## Implementation Plan

1. [completed] Reduce duplication of the ltree-legal charset - `code/load_catalog_data/yaml_discovery.py`
   - 1.1. [suggestion] Line 229: The legal-character class `[a-z0-9_-]` is spelled inline here, duplicating the definition already compiled into `_LABEL_RE` at line 94. If the canonical charset ever changes, both sites must be updated in lockstep or the "offending character(s)" message drifts from the real rule.
        - Current: `bad = sorted({c for c in value if not re.fullmatch(r"[a-z0-9_-]", c)})`
        - Expected: derive the offending set from a single source of truth, e.g. a module-level `_LABEL_CHARSET = frozenset(string.ascii_lowercase + string.digits + "_-")` used both to build `_LABEL_RE` and as `bad = sorted(set(value) - _LABEL_CHARSET)`
        - Resolution: Deferred — this branch runs only on the invalid-input error path (never in the hot path), and the duplicated class is a 9-character literal whose drift would affect only an error message's wording, not validation correctness. The current form is clear and self-contained; fold in only if the charset is revised.

## Skills with No Issues

1. Type Hints: No issues found — every function and the `PathIdentity` dataclass carry complete, modern-syntax annotations (`str | None`, `tuple[str, ...]`, `dict[str, FileType]`, `list[PathIdentity]`), and `FileType` is a precise `Literal` rather than `str`.
2. Docstrings: No issues found — module, public functions, private helpers (`_decode_parts`, `_validate_schema_segment`), and the dataclass all have Google-style docstrings with Args/Returns/Raises/Attributes as applicable, documenting the "why" behind the reserved-segment and shard-folder grammar.
3. Comments: No issues found — comments consistently explain rationale (ltree charset, wrong-depth rejections, EAFP wrong-extension guard, sorted-walk determinism) rather than restating the code, and stay current with the venue-free model.
4. Logging: No issues found — uses `get_logger` from `logconfig`, f-strings throughout, DEBUG for per-file diagnostics and INFO for the run summary; no `print`, no entering/exiting noise.
5. Exception Handling: No issues found — raises `ValueError` with path context, chains with `from e` in `decode_path`, and the aggregate-not-fail-fast pattern in `discover_yaml_files` is deliberate and documented; the `except ValueError: continue` in the wrong-extension guard is intentional EAFP classification control flow, commented in place.
6. Executable Scripts: N/A - library module with no `main()`/`__main__` entry point.
7. Data Validation: N/A - not a `data_val_` data-validation script.
8. Unit Tests: N/A - this is the source module under review; its tests live in `code/load_catalog_data/unit_tests/test_yaml_discovery.py`.
