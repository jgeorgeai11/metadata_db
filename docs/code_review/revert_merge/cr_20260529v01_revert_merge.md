---
name: cr_20260529v01_revert_merge
goal: Address code quality issues identified across the revert_merge modules to align with python-development and sql-development skills.
created: 2026-05-29 00:00:00
updated: 2026-05-29 00:00:00
---

## Implementation Plan

1. [completed] Ensure token never leaks via run_git's argv-logging seam - `code/revert_merge/git_ops.py`
   - 1.1. [critical] `run_git` logged the literal `args` list it received and embedded that same list in any `CalledProcessError` it raised. When `set_authenticated_remote` passed the composed `https://oauth2:<TOKEN>@...` URL through `run_git`, the token ended up in both the DEBUG entry log ("Running git ...") and the `cmd` field of any raised exception — a `caplog`-asserted regression on the very first run of the test suite (`test_set_authenticated_remote_does_not_log_url_or_token`). Resolved: `run_git` now accepts an optional `log_args` parameter; `set_authenticated_remote` passes `["remote", "set-url", "origin", "<redacted>"]` while the real URL is sent to git. The DEBUG entry, the ERROR exit log, and the `CalledProcessError.cmd` field all use the redacted form. Covered by `test_set_authenticated_remote_does_not_log_url_or_token`, `test_set_authenticated_remote_does_not_log_url_on_failure`, and `test_run_git_log_args_overrides_log_only_not_argv`.

## Skills with No Issues

1. Type Hints: All functions across `git_ops.py`, `preconditions.py`, and `revert_merge.py` carry full parameter and return annotations using modern syntax (`list[str]`, `dict[str, Any]`, `subprocess.CompletedProcess[str]`, `Path`). The `log_args: list[str] | None = None` parameter added in item 1.1 follows the same style.
2. Docstrings: Google-style throughout. Module docstrings explain role and design rationale (e.g., the refusal contract in `revert_merge.py`'s module docstring, the "single subprocess seam" rationale in `git_ops.py`). Function docstrings carry Args / Returns / Raises sections.
3. Comments: Explain "why" — e.g., the "deferred log-file creation" rationale next to `setup_logging` in `revert_merge.main`, the "deliberately do NOT log url" comment in `set_authenticated_remote`, the "refuse before doing anything destructive" comment in `revert_merge.run`. No "what" comments.
4. Logging: Uses `logconfig.get_logger`/`setup_logging`; f-strings throughout; `"=" * 60` separators bracket the entry-point's main run; no `print()`; log_dir mirrors script location (`logs/revert_merge`); no "Entering/Exiting" noise. The token-redaction guarantee is asserted across the suite via `caplog` (item 1.1 plus `test_main_token_never_in_logs_across_happy_path`).
5. Exception Handling: Specific exceptions caught (no bare `except`); the entry point has six distinct arms — `PreconditionError`, `KeyError`, `RuntimeError`, `subprocess.CalledProcessError`, `OSError`, plus the inline `(OSError, tomllib.TOMLDecodeError)` arm for config-file reads — each with a distinct log message. `PreconditionError` is its own type (not reused `ValueError`) so the "refusing to push" log message lands in a dedicated arm. No generic `Exception` wrapping. `from e` is not needed here because no exception is being wrapped into a new type.
6. Executable Scripts: `main()` with `if __name__ == "__main__"` guard, single `--config` TOML argument (plus the required `--commit-sha`), config in `code/revert_merge/config/`, logging deferred until after argparse so `--help` doesn't create a log directory.
7. Data Validation: N/A — this is a CI-side cleanup script, not a `data_val_*` output-validation script.
8. Unit Tests: pytest fixtures via `unit_tests/conftest.py` mirror the Phase 1/2 pattern with a new `fake_subprocess_run` fixture that records argv and lets each test control return code / stdout / stderr. `parametrize` used for parent-count negative cases (0, 1, 3). `pytest.raises(match=...)` used to verify refusal messages. `caplog` used across the suite to enforce token-redaction. 100% line coverage on every revert_merge module; full suite is 38 passing.
9. SQL (best-practices): N/A — no SQL in this module.
