---
name: cr_20260813v02_check_corpus
goal: Address code quality issues identified in code/load_catalog_data/check_corpus.py to align with python-development skills; re-review since cr_20260813v01 (whose findings are all applied), done as a group with test_check_corpus.py.
created: 2026-08-13 11:33:32
updated: 2026-08-13 11:33:32
---

## Implementation Plan

1. [completed] Import-block conventions diverge from the loader (carryover) - `code/load_catalog_data/check_corpus.py`
   - 1.1. [suggestion] Lines 35-42: this file marks its post-`sys.path.insert` imports with `# noqa: E402` and additionally inserts its own directory (line 36), while the sibling loader does neither — two conventions for one concern; unchanged carryover of cr_20260813v01 finding 5.1.
        - Current: `sys.path.insert(0, str(Path(__file__).resolve().parent))` / `from logconfig import get_logger, setup_logging  # noqa: E402`
        - Expected: one convention across both siblings (markers on both or neither; the self-dir insert kept or dropped consistently)
        - Resolution: Deferred — same grounds as cr_20260813v01: no linter is configured in `pyproject.toml`, so the `noqa` markers are inert either way, and the self-dir insert is harmless when run as a script (the script dir is auto-prepended) while keeping the module importable from elsewhere. Align when a linter is adopted.

## Skills with No Issues

1. Type Hints skill: No issues found. Both functions are annotated (`-> None`), and argparse's `type=Path` matches the `Path` consumption downstream (`args.config.exists()`, `open(args.config, "rb")`).
2. Docstrings skill: No issues found. The module docstring documents purpose, usage (including the zero-argument run and the `--config` default's rationale), exit-status contract, and both output destinations; `_mirror_log_to_stderr`'s docstring documents the root-logger attachment and stderr-vs-stdout choices; `main` carries a behavioral docstring. All cr_20260813v01 path references (`logs/load_catalog_data/check_corpus.jsonl`) are now accurate.
3. Comments skill: No issues found. The cp1252 comment now shows the `→` escape form (cr_20260813v01 finding 3.1 applied), the config-sourcing comment (lines 109-110) matches the code reading only `data_root`, and the `ValidationError`-subclass comment (lines 136-139) matches `corpus_assembly`'s class hierarchy and the per-issue logging below it.
4. Logging skill: No issues found. `log_dir="logs/load_catalog_data"` follows the module-folder convention (cr_20260813v01 finding 3.2 applied); `logconfig` with no `print()`, f-strings in every log call, `setup_logging` after argparse, `"=" * 60` separators on the start, success, and all four error arms; the extra stderr handler remains a justified, documented addition for this script's interactive audience.
5. Exception Handling skill: No issues found. The single `except ValidationError` arm covers both stages and logs `e.summary` plus one record per issue, mirroring the loader (cr_20260813v01 findings 2.1 and 2.2 applied); config arms catch specific types (`OSError`/`TOMLDecodeError`, `KeyError`) with contextful messages; nothing is swallowed.
6. Executable Scripts skill: No issues found. Single `--config` argument reading `data_root` from the loader's TOML (cr_20260813v01 finding 4.1 applied; the script-relative default is the documented, deliberate deviation preserving the advertised zero-argument run), `main()` + `if __name__ == "__main__":` guard with `# pragma: no cover`, config in `config/` alongside the script, logging deferred past argparse.
7. Unit Tests skill: No issues found. `test_check_corpus.py` now exists (cr_20260813v01 finding 1.1 applied) and pins the exit-code contract, all four config-error arms, both corpus-failure arms, the stderr mirror, and the zero-argument default; see cr_20260813v01_test_check_corpus.md for that file's own review.
8. Data Validation skill: N/A — corpus validation is this script's business logic, not a `data_val_` output-validation script.
9. SQL skill: N/A — the script is deliberately database-free and contains no SQL.
