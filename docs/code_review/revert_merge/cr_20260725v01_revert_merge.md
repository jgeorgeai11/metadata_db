---
name: cr_20260725v01_revert_merge
goal: Re-review of code/revert_merge/revert_merge.py against python-development skills since the rewrite to per-command URL injection (supersedes cr_20260713v01).
created: 2026-07-25 13:53:30
updated: 2026-07-25 13:53:30
---

## Implementation Plan

1. [completed] Docstring `Raises` accuracy for `run` - `code/revert_merge/revert_merge.py`
   - 1.1. [minor] Lines 74-75: The `run` `Raises:` section documents `KeyError` only as "If a required config field is missing." But `run` also calls `build_authenticated_url(remote_url_template, token)` at line 107, which is documented (in `git_ops.build_authenticated_url`) to raise `KeyError` when the template names a placeholder other than `{token}` (e.g. a `{tokn}` typo). The `<...>` sanity check at lines 97-103 only catches angle-bracket placeholders, so a mis-named brace placeholder reaches `build_authenticated_url` and raises `KeyError` out of `run`. The documented contract should reflect that second origin.
        - Current: `KeyError: If a required config field is missing.`
        - Expected: `KeyError: If a required config field is missing, or if the remote URL template names a placeholder other than {token}.`
        - Resolution: Applied — the `run` `Raises:` section now documents both origins of `KeyError`.

2. [completed] Deferred enhancements - `code/revert_merge/revert_merge.py`
   - 2.1. [suggestion] Lines 182-185: The `KeyError` arm in `main()` always logs "Missing required config field: {e}". A `KeyError` raised by `build_authenticated_url` (mis-named `{...}` placeholder that slips past the `<...>` sanity check) would surface here as a misleading "Missing required config field: 'tokn'". Consider either validating the template contains `{token}` before formatting, or broadening the message to cover both origins.
        - Current: `logger.error(f"Missing required config field: {e}")`
        - Expected: `logger.error(f"Missing required config field or malformed URL template placeholder: {e}")`
        - Resolution: Deferred — optional; this is an operator-configuration edge case (a hand-typed brace typo in the TOML) that fails fast and non-destructively before any git operation, and the raised `KeyError` names the offending key. Carried over from cr_20260713v01 item 2.1, still a suggestion. Promote to `[minor]` if the clearer message is wanted.
   - 2.2. [suggestion] Lines 147-152: `main()` adds a second CLI argument, `--commit-sha`, alongside `--config`, deviating from the executable-scripts convention of a single `--config` argument. The value is genuine runtime CI data (`$CI_COMMIT_SHA`) and could instead be read from the `CI_COMMIT_SHA` environment variable (mirroring the `CLEANUP_BOT_TOKEN` pattern already used) to restore the single-argument convention.
        - Current: `parser.add_argument("--commit-sha", type=str, required=True, help=...)`
        - Expected: read `CI_COMMIT_SHA` from the environment, or add a one-line module-docstring note marking the extra flag an intentional deviation for testability.
        - Resolution: Deferred — optional; the explicit `--commit-sha` flag keeps `run` testable with an arbitrary SHA without mutating process env, and the module docstring's Usage block already shows the flag and notes it carries `$CI_COMMIT_SHA`. Carried over from cr_20260713v01 item 3.1. Promote to `[minor]` if the single-argument convention should be enforced.

## Skills with No Issues

1. Type Hints: No issues found — `run(config: dict[str, Any], commit_sha: str, cwd: Path) -> None` and `main() -> None` carry full parameter and return annotations using modern syntax; `dict[str, Any]` is appropriate for a parsed-TOML config.
2. Docstrings: Issues found — see item 1.1. Otherwise strong: module docstring documents the refusal contract with a source reference; `run` has Args/Raises and the `Raises` set now correctly includes `OSError` and `subprocess.CalledProcessError` (both prior-review findings addressed).
3. Comments: No issues found — comments explain "why" (deferred log setup so `--help` won't create a log dir; `checkout -B` rationale for reused runner checkouts; token never logged; the `<...>` template sanity check rationale). All comments track the current per-command-injection implementation.
4. Logging: No issues found — uses `logconfig.get_logger`/`setup_logging`, f-strings throughout, `"=" * 60` separators bracket the run in every exit arm, `log_dir="logs/revert_merge"` mirrors the script location, no `print()`, no Entering/Exiting noise, token deliberately never logged.
5. Exception Handling: One suggestion (item 2.1). Otherwise sound — specific exceptions caught (no bare `except`), six distinct arms with distinct messages, `PreconditionError` has its own arm placed first, no generic `Exception` wrapping, refusal raises happen before any destructive git call.
6. Executable Scripts: One suggestion (item 2.2). Otherwise conformant — `main()` with `if __name__ == "__main__"` guard, config lives in `code/revert_merge/config/`, logging deferred until after argparse, config-existence check before `tomllib.load`.
7. Data Validation: N/A — this is a CI-side cleanup script, not a `data_val_*` output-validation script.
8. Unit Tests: N/A for this file — tests live in `code/revert_merge/unit_tests/`, outside the scope of this single-file review.
