---
name: cr_20260727v01_revert_merge
goal: Re-review of code/revert_merge/revert_merge.py against python-development skills after the added bot-identity validation and per-command git identity injection (supersedes cr_20260725v01).
created: 2026-07-27 13:58:48
updated: 2026-07-27 13:58:48
---

## Implementation Plan

1. [completed] Deferred enhancements - `code/revert_merge/revert_merge.py`
   - 1.1. [suggestion] Lines 236-237: The `KeyError` arm in `main()` always logs "Missing required config field: {e}". A `KeyError` raised by `build_authenticated_url` (a mis-named `{...}` placeholder that slips past the `<...>` sanity check) surfaces here as a misleading "Missing required config field: 'tokn'". Consider broadening the message to cover both origins, or validating the template names only `{token}` before formatting.
        - Current: `logger.error(f"Missing required config field: {e}")`
        - Expected: `logger.error(f"Missing required config field or malformed URL template placeholder: {e}")`
        - Resolution: Deferred — optional; this is an operator-configuration edge case (a hand-typed brace typo in the TOML) that fails fast and non-destructively before any git operation, and the raised `KeyError` names the offending key. Carried over from cr_20260725v01 item 2.1. Promote to `[minor]` if the clearer message is wanted.
   - 1.2. [suggestion] Lines 201-206: `main()` adds a second CLI argument, `--commit-sha`, alongside `--config`, deviating from the executable-scripts convention of a single `--config` argument. The value is genuine runtime CI data (`$CI_COMMIT_SHA`) and could instead be read from the `CI_COMMIT_SHA` environment variable (mirroring the `CLEANUP_BOT_TOKEN` pattern already used) to restore the single-argument convention.
        - Current: `parser.add_argument("--commit-sha", type=str, required=True, help=...)`
        - Expected: read `CI_COMMIT_SHA` from the environment, or keep the flag (the module-docstring Usage block already documents it) as an intentional deviation for testability.
        - Resolution: Deferred — optional; the explicit `--commit-sha` flag keeps `run` testable with an arbitrary SHA without mutating process env, and the module docstring's Usage block already shows the flag and notes it carries `$CI_COMMIT_SHA`. Carried over from cr_20260725v01 item 2.2. Promote to `[minor]` if the single-argument convention should be enforced.
   - 1.3. [suggestion] Lines 126-131: The bot-identity guard `if not isinstance(value, str) or not value.strip():` raises with the message "{key} is blank in the config". The `not isinstance(value, str)` branch fires when the TOML value is a non-string (e.g. `bot_name = 123`), where "is blank" is a slightly imprecise description of the cause even though the remediation ("set the cleanup bot's git identity") remains correct.
        - Current: `f"{key} is blank in the config — set the cleanup bot's git identity before running; refusing to touch git."`
        - Expected: `f"{key} must be a non-empty string in the config — set the cleanup bot's git identity before running; refusing to touch git."`
        - Resolution: Deferred — optional; a non-string identity requires a malformed hand-edited TOML, the `isinstance` guard already handles it gracefully (avoiding an `AttributeError` on `.strip()`) and fails fast before any git operation. The wording is only imprecise for that unlikely edge, not wrong.

## Skills with No Issues

1. Type Hints: No issues found — `run(config: dict[str, Any], commit_sha: str, cwd: Path) -> None` and `main() -> None` carry full parameter and return annotations using modern syntax; `dict[str, Any]` is appropriate for a parsed-TOML config.
2. Docstrings: No issues found — the module docstring documents the refusal contract with a source reference; `run`'s `Raises` was extended correctly to cover the new bot-identity `RuntimeError` cases (blank / unfilled `<...>` identity) and its Args list the added `bot_name` / `bot_email` keys. `main`'s brief docstring is adequate for an entry point.
3. Comments: No issues found — comments explain "why" (deferred log setup so `--help` won't create a log dir; `checkout -B` rationale for reused runner checkouts; `<...>` template sanity check; per-command `-c user.name/email` mirroring token hygiene; token never logged). All comments track the current per-command-injection implementation.
4. Logging: No issues found — uses `logconfig.get_logger`/`setup_logging`, f-strings throughout, `"=" * 60` separators bracket the run in every exit arm, `log_dir="logs/revert_merge"` mirrors the script location, no `print()`, no Entering/Exiting noise, token deliberately never logged.
5. Exception Handling: One suggestion (item 1.1). Otherwise sound — specific exceptions caught (no bare `except`), six distinct arms with distinct messages, `PreconditionError` has its own arm placed first, no generic `Exception` wrapping, all refusal `raise`s happen before any destructive git call.
6. Executable Scripts: One suggestion (item 1.2). Otherwise conformant — `main()` with `if __name__ == "__main__"` guard, config lives in `code/revert_merge/config/`, logging deferred until after argparse, config-existence check before `tomllib.load`.
7. Data Validation: N/A — this is a CI-side cleanup script, not a `data_val_*` output-validation script.
8. Unit Tests: N/A for this file — tests live in `code/revert_merge/unit_tests/`, outside the scope of this single-file review.
