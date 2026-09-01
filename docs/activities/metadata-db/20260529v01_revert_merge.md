---
name: 20260529v01_revert_merge
goal: Build `code/revert_merge/revert_merge.py` — the cleanup-bot script invoked by the `revert_failed_load` CI job when a post-merge loader run fails. Authenticates as the cleanup bot, verifies preconditions (HEAD == commit, commit is a 2-parent merge), runs `git revert --no-edit -m 1 <sha>`, and pushes to `origin/main`. Refuses (exits non-zero, no push) if any precondition fails.
created: 2026-05-29 09:00:00
updated: 2026-05-29 12:00:00
---

## Implementation Plan

1. Create TOML config - `code/revert_merge/config/revert_merge.toml`
   - 1.1. Fields: `remote_url_template` (Python `str.format` template with a `{token}` placeholder; e.g., `https://oauth2:{token}@gitlab.example.com/<group>/metadata-db.git`), `main_branch` (default `"main"`)
   - 1.2. Header comment with the usage line: `uv run code/revert_merge/revert_merge.py --config code/revert_merge/config/revert_merge.toml --commit-sha "$CI_COMMIT_SHA"`

2. Create git subprocess helpers - `code/revert_merge/git_ops.py`
   - 2.1. Function `run_git(args, cwd, check=True)`: thin wrapper over `subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)`. Returns the `CompletedProcess`. Logs the command (token-redacted) on entry and any non-zero exit.
   - 2.2. Function `head_sha(cwd)`: return `git rev-parse HEAD` as a stripped string.
   - 2.3. Function `parent_shas(cwd, sha)`: return list of parent SHAs from `git rev-list --parents -n 1 <sha>` (skip the first token, which is `<sha>` itself).
   - 2.4. Function `set_authenticated_remote(cwd, remote_url_template, token)`: format the URL via `str.format(token=token)` and run `git remote set-url origin <url>`. The composed URL is never logged.

3. Create and run tests for git_ops module - `code/revert_merge/unit_tests/test_git_ops.py`
   - 3.1. Patch `subprocess.run` via a `monkeypatch` fixture; assert each helper invokes `git` with the expected argv
   - 3.2. Assert `head_sha` strips trailing newline
   - 3.3. Assert `parent_shas` returns `[]` for a root commit (one token), `[p]` for a normal commit (two tokens), `[p1, p2]` for a merge commit (three tokens)
   - 3.4. Assert `set_authenticated_remote` does not log the composed URL (use `caplog`); composed URL contains the token in the right slot
   - 3.5. Assert `run_git` raises `CalledProcessError` when `check=True` and exit code is non-zero
   - 3.6. Run with `uv run pytest code/revert_merge/unit_tests/test_git_ops.py -v`

4. Create precondition checks - `code/revert_merge/preconditions.py`
   - 4.1. Define `PreconditionError(Exception)` carrying a human-readable reason
   - 4.2. Function `verify_head_is(commit_sha, cwd)`: raise `PreconditionError` if `head_sha(cwd) != commit_sha`. Message names both SHAs for triage.
   - 4.3. Function `verify_is_merge_commit(commit_sha, cwd)`: raise `PreconditionError` if `parent_shas(cwd, commit_sha)` does not contain exactly 2 entries.

5. Create and run tests for preconditions module - `code/revert_merge/unit_tests/test_preconditions.py`
   - 5.1. Patch `head_sha` / `parent_shas` (not `subprocess.run`) — exercise just the precondition logic
   - 5.2. Happy path: HEAD matches, commit has 2 parents → no raise
   - 5.3. Negative: HEAD differs → `PreconditionError` with both SHAs in the message
   - 5.4. Negative: 0, 1, or 3 parents each → `PreconditionError`
   - 5.5. Run tests

6. Create entry point - `code/revert_merge/revert_merge.py`
   - 6.1. Argparse: required `--config`, required `--commit-sha`
   - 6.2. `setup_logging(log_dir="logs/revert_merge")` after argparse so `--help` doesn't create log files
   - 6.3. Function `run(config, commit_sha, cwd)` orchestrating:
       - 6.3.1. Read `CLEANUP_BOT_TOKEN` from env; raise `RuntimeError` if missing or empty
       - 6.3.2. `set_authenticated_remote(cwd, config["remote_url_template"], token)`
       - 6.3.3. `run_git(["fetch", "origin", config["main_branch"]], cwd)`
       - 6.3.4. `run_git(["checkout", config["main_branch"]], cwd)`
       - 6.3.5. `verify_head_is(commit_sha, cwd)`
       - 6.3.6. `verify_is_merge_commit(commit_sha, cwd)`
       - 6.3.7. `run_git(["revert", "--no-edit", "-m", "1", commit_sha], cwd)`
       - 6.3.8. `run_git(["push", "origin", config["main_branch"]], cwd)`
   - 6.4. Exception handling per the apply_ddl / load_metadata_db pattern: specific `except` arms per error type with `sys.exit(1)` and a distinct log message per arm — `PreconditionError`, `RuntimeError`, `KeyError`, `tomllib.TOMLDecodeError`, `subprocess.CalledProcessError`, `OSError`
   - 6.5. Refusal contract: on any `PreconditionError` or earlier failure, the script must exit *before* `git revert` or `git push` ever run. The error message must explicitly say "refusing to push" so the failed pipeline's job log is self-explanatory.

7. Create and run orchestration tests - `code/revert_merge/unit_tests/test_revert_merge.py`
   - 7.1. Patch `git_ops.run_git`, `head_sha`, `parent_shas` so no real `git` is invoked
   - 7.2. Happy path: HEAD matches, commit is 2-parent merge → assert the full 6-command git sequence ran in order; exit code 0
   - 7.3. HEAD mismatch → `git revert` and `git push` are NOT called; exit code 1; log contains "refusing to push"
   - 7.4. Wrong parent count (0, 1, 3) → `git revert` and `git push` are NOT called; exit code 1; log contains "refusing to push"
   - 7.5. Missing `CLEANUP_BOT_TOKEN` → exit code 1; no git commands invoked; clear error message
   - 7.6. `CalledProcessError` raised by `git fetch` → exit code 1; later commands not invoked
   - 7.7. `CalledProcessError` raised by `git revert` (simulated conflict) → exit code 1; `git push` not invoked
   - 7.8. Config field missing → exit code 1
   - 7.9. Token never appears in any log line (use `caplog` across the suite)
   - 7.10. Run tests; coverage target 100%

8. End-to-end manual verification against a sandbox GitLab project (deferred — requires Phase 4 setup)
   - 8.1. Stand up a throwaway sandbox repo with a cleanup-bot token and one merge commit at HEAD
   - 8.2. Set `CLEANUP_BOT_TOKEN` and run: `uv run code/revert_merge/revert_merge.py --config code/revert_merge/config/revert_merge.toml --commit-sha $(git rev-parse HEAD)`
   - 8.3. Verify the revert commit lands on `main` in the sandbox
   - 8.4. Run again against a non-merge commit → verify refusal (exit non-zero, no push)
   - 8.5. Run again against a SHA that is not HEAD → verify refusal

9. Code review and address findings
   - 9.1. Run `code-review-agent` against each module under `code/revert_merge/`; mirror the Phase 1/2 review pattern under `docs/code_review/`
   - 9.2. Address findings via `code-implementation-agent`; re-run the suite at 100% coverage
   - 9.3. Mark each review's `Status & Next Steps` resolved when fixes land

## Key Data Decisions and Considerations

1. **`git` via `subprocess`, not `python-gitlab`** — the maintenance doc spec is written in terms of plain git commands; subprocess keeps the implementation legible and avoids a new dependency. The GitLab API would only buy us things we don't need here (PR comments, label edits, etc.).
2. **Token via env, never config** — `CLEANUP_BOT_TOKEN` is a secret and must never be checked in. The TOML config holds only the URL *template* (with a `{token}` placeholder); the token is read from env at runtime and substituted in memory.
3. **Token never logged** — `set_authenticated_remote` logs only that it set the URL, never the URL itself. The unit suite enforces this via `caplog` assertions across every code path.
4. **Refusal is hard, not retried** — if any precondition fails, the script exits non-zero without pushing anything. Per the maintenance doc, papering over an unexpected `main` state would silently widen the very `main`/DB drift this script exists to prevent.
5. **Preconditions are isolated from subprocess** — `preconditions.py` calls `head_sha` / `parent_shas`, not `subprocess.run` directly. Tests can exercise the refusal logic without mocking subprocess at all.
6. **Single ordered sequence, no retries** — six git commands run once each, in order. Any non-zero exit halts the script and propagates as `CalledProcessError`. A transient network hiccup means a human reviews the failed pipeline; the cost of retrying automatically (multiple revert commits, race with a subsequent merge) is worse than the cost of one human-triggered re-run.
7. **`cwd` is an explicit parameter to every git helper** — the CI job runs inside the repo checkout, but tests need to point the helpers at a temp dir. Passing `cwd` through (rather than relying on the process cwd) makes that trivial.
8. **No working-tree state restoration on failure** — if `git revert` fails partway (e.g., conflict in an auto-resolution), the working tree is left as `git revert` left it. CI runners are ephemeral, so the runner is discarded after the job exits regardless. Adding cleanup logic would be code that runs in the path that matters least.
9. **`PreconditionError` exists rather than reusing `ValueError`** — gives the entry point a clean `except` arm dedicated to "refusing to push" log messages, distinct from arbitrary subprocess or config failures.
