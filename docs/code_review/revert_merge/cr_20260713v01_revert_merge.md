---
name: cr_20260713v01_revert_merge
goal: Address code quality issues identified in code/revert_merge/revert_merge.py to align with python-development skills.
created: 2026-07-13 22:33:37
updated: 2026-07-13 22:33:37
---

## Implementation Plan

1. [completed] Docstring accuracy - `code/revert_merge/revert_merge.py`
   - 1.1. [minor] Lines 47-48: The `run` docstring calls the flow a "6-step git sequence" but the parenthetical lists only 5 git operations (`set-url → fetch → checkout → revert → push`) and omits the two `verify_*` precondition checks. The inline comments number the steps 1, 2–3, 4, 5. The count and the enumeration disagree.
        - Current: `The 6-step git sequence (set-url → fetch → checkout → revert → push)`
        - Expected: `The git sequence (set-url → fetch → checkout → verify → revert → push)` (or restate as "5-step" and keep the 5-item list consistent with the numbered comments)
        - Resolution: Applied the expected fix — replaced "The 6-step git sequence (set-url → fetch → checkout → revert → push)" with "The git sequence (set-url → fetch → checkout → verify → revert → push)", dropping the incorrect count and adding the omitted verify step so the docstring matches the numbered inline comments.
   - 1.2. [minor] Lines 61-66: The `run` `Raises:` section omits `OSError`. `run_git` calls `subprocess.run(["git", ...])` without catching `FileNotFoundError` (a subclass of `OSError`), so a missing/unavailable `git` binary propagates out of `run`. `main()` explicitly handles this at line 159, which confirms `run` can raise it; the docstring should list it so the contract matches the handler.
        - Current: Raises lists `RuntimeError`, `KeyError`, `PreconditionError`, `subprocess.CalledProcessError`
        - Expected: Add `OSError: If the git executable cannot be found or invoked.`
        - Resolution: Added `OSError: If the git executable cannot be found or invoked.` to the `run` docstring `Raises:` section, so the documented contract matches the `except OSError` handler in `main()` (line 159).

2. [completed] Exception handling clarity - `code/revert_merge/revert_merge.py`
   - 2.1. [suggestion] Lines 147-150: The `KeyError` arm always logs "Missing required config field". A `KeyError` can also arise from `set_authenticated_remote` when `remote_url_template` lacks a `{token}` placeholder (documented in `git_ops.set_authenticated_remote`), in which case the operator sees a misleading "Missing required config field: 'token'" message. Consider validating the template contains `{token}` before formatting, or wording the message to cover both origins.
        - Resolution: Deferred — this is a `[suggestion]`, left unimplemented per the code-implementation policy. The reviewer promotes it to `[minor]` if the fix is wanted.

3. [completed] Executable script convention - `code/revert_merge/revert_merge.py`
   - 3.1. [suggestion] Lines 112-117: The script adds a second CLI argument, `--commit-sha`, alongside `--config`. The executable-scripts skill prescribes a single `--config` argument. The value is genuine runtime CI data ($CI_COMMIT_SHA), so a deviation is defensible, but it could instead be read from the `CI_COMMIT_SHA` environment variable (mirroring the `CLEANUP_BOT_TOKEN` pattern already used) to restore the single-argument convention. If the explicit flag is retained for testability, a one-line note in the module docstring acknowledging the intentional deviation would help.
        - Resolution: Deferred — this is a `[suggestion]`, left unimplemented per the code-implementation policy. The reviewer promotes it to `[minor]` if the fix is wanted.

## Skills with No Issues

1. Type Hints: No issues found — `run(config: dict[str, Any], commit_sha: str, cwd: Path) -> None` and `main() -> None` carry full parameter and return annotations using modern syntax.
2. Docstrings: Issues found — see items 1.1 and 1.2. Structure is otherwise strong (module docstring documents the refusal contract; `run` has Args/Raises).
3. Comments: No issues found — comments explain "why" (deferred log setup so `--help` doesn't create a log dir; "refuse before doing anything destructive"; token never logged).
4. Logging: No issues found — uses `logconfig.get_logger`/`setup_logging`, f-strings throughout, `"=" * 60` separators bracket the run, `log_dir="logs/revert_merge"` mirrors script location, no `print()`, no Entering/Exiting noise, token deliberately never logged.
5. Exception Handling: One suggestion (item 2.1). Otherwise sound — specific exceptions caught (no bare `except`), six distinct arms with distinct messages, `PreconditionError` is its own type, no generic `Exception` wrapping.
6. Executable Scripts: One suggestion (item 3.1). Otherwise conformant — `main()` with `if __name__ == "__main__"` guard, config lives in `code/revert_merge/config/`, logging deferred until after argparse, config-existence check before load.
7. Data Validation: N/A — this is a CI-side cleanup script, not a `data_val_*` output-validation script.
8. Unit Tests: N/A for this file — tests live in `code/revert_merge/unit_tests/test_revert_merge.py`, outside the scope of this single-file review.
