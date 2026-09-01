---
name: cr_20260812v01_test_git_ops
goal: Re-review code/revert_merge/unit_tests/test_git_ops.py against python-development skills since cr_20260713v01, reviewed together with git_ops.py for cross-file consistency.
created: 2026-08-12 13:50:41
updated: 2026-08-12 14:51:55
---

## Implementation Plan

1. [completed] Stop exercising `run_git` with an argv the package forbids - `code/revert_merge/unit_tests/test_git_ops.py`
   - 1.1. [minor] Lines 90-94: `test_run_git_log_args_overrides_log_only_not_argv` demonstrates `log_args` with `["remote", "set-url", "origin", "https://x-access-token:T@x/y.git"]` — the exact operation the package deliberately no longer performs, and which `test_fetch_and_push_never_touch_git_config` (lines 220-222) asserts can never appear in any argv. The file therefore both forbids and models `remote set-url`, and the sample keeps alive the same removed `set_authenticated_remote` design that `git_ops.py` line 57 still names in its docstring (see `cr_20260812v01_git_ops.md` finding 1.1). Redaction is orthogonal to the subcommand, so a fetch-shaped argv proves the same behavior without contradicting the hygiene contract.
        - Current:
          ```python
          git_ops.run_git(
              ["remote", "set-url", "origin", "https://x-access-token:T@x/y.git"],
              cwd=tmp_path,
              log_args=["remote", "set-url", "origin", "<redacted>"],
          )
          ```
        - Expected:
          ```python
          git_ops.run_git(
              ["fetch", "https://x-access-token:T@x/y.git", "refs/heads/main"],
              cwd=tmp_path,
              log_args=["fetch", "<redacted>", "refs/heads/main"],
          )
          ```
        - Resolution: Implemented with an added accommodation — swapped the argv to the fetch-shaped sample as specified, and also updated the follow-on assertion, which was `assert args[-1] == "https://x-access-token:T@x/y.git"`. The URL is no longer the last token under the new argv (the refspec is), so that assertion would have failed; it now compares the whole forwarded argv (`["git", "fetch", "<url>", "refs/heads/main"]`), which pins the URL's position as well as its presence. The redaction assertions on the log text are unchanged, and the file no longer both forbids and models `remote set-url` (paired with `cr_20260812v01_git_ops.md` finding 1.1).

2. [completed] Assert the WARNING-not-ERROR level on the `check=False` path - `code/revert_merge/unit_tests/test_git_ops.py`
   - 2.1. [minor] Lines 57-64: `test_run_git_nonzero_returns_when_check_false` asserts only the returned code, so the deliberate log-level split in `git_ops.run_git` (ERROR when raising, WARNING when the caller opted into `check=False`, lines 86-101, with an inline comment explaining the choice) has no assertion behind it on the non-raising side — the `check=True` counterpart is covered by `test_run_git_nonzero_logs_error` (lines 67-79). A regression that logged an expected non-zero exit at ERROR would leave all 19 tests green while filling the incident log with false failures.
        - Current:
          ```python
          result = git_ops.run_git(["fetch"], cwd=tmp_path, check=False)

          assert result.returncode == 2
          ```
        - Expected:
          ```python
          # add `caplog: pytest.LogCaptureFixture` to the signature
          caplog.set_level(logging.DEBUG, logger="git_ops")

          result = git_ops.run_git(["fetch"], cwd=tmp_path, check=False)

          assert result.returncode == 2
          assert [r.levelname for r in caplog.records if "exited 2" in r.message] == [
              "WARNING"
          ]
          ```
        - Resolution: Implemented as specified — added the `caplog` fixture to the signature, set the level to DEBUG on the `git_ops` logger, and asserted that the sole record mentioning `exited 2` is a WARNING. Added a one-line comment stating why (an expected non-zero exit is not an ERROR). The assertion is non-vacuous: it matches exactly one captured record, so a regression that logged this path at ERROR — or logged it twice — now fails.

3. [completed] Narrow the failure-path leak test to the vector it actually covers - `code/revert_merge/unit_tests/test_git_ops.py`
   - 3.1. [minor] Lines 241-257: the docstring claims "A failing credentialed command must still not leak the token", but the fixture sets `stderr="rejected"` — a token-free string — so the `assert "SECRET123" not in caplog.text` on line 256 passes no matter what the module does with `stderr`, which `git_ops.run_git` line 84 logs verbatim. Only the argv/`log_args` and `CalledProcessError.cmd` vectors are genuinely exercised. Say so, so the test is not read as covering the stderr vector (deferred in `cr_20260812v01_git_ops.md` finding 2.1).
        - Current:
          ```python
          """A failing credentialed command must still not leak the token."""
          ```
        - Expected:
          ```python
          """A failing credentialed command must not leak the token
          through argv or the raised exception's cmd."""
          ```
        - Resolution: Implemented as specified — the docstring now names the two vectors the test genuinely exercises, so it is not read as covering the stderr vector deferred in `cr_20260812v01_git_ops.md` finding 2.1. Scope only: the fixture's token-free `stderr="rejected"` and the assertions are unchanged, since a token-bearing stderr would fail against the module's current behavior (it logs stderr verbatim, relying on git's own credential anonymization).

4. [completed] Optional test-hygiene improvements - `code/revert_merge/unit_tests/test_git_ops.py`
   - 4.1. [suggestion] Line 186: `_AUTH_URL` is a module-level constant declared two-thirds of the way down the file rather than with the imports, so a reader scanning the top for shared test data does not see it.
        - Current: `_AUTH_URL` defined at line 186, under the `fetch_branch / push_branch` banner
        - Expected: `_AUTH_URL` defined immediately after the `import git_ops` block (line 20)
        - Resolution: Deferred — optional placement change. The constant is used only by the four tests under the banner it sits beneath, so the current position keeps it next to its consumers; moving it trades local proximity for top-of-file discoverability with no behavioral gain.

5. [completed] Pin the `check=False` forwarding that gives `run_git` the raise - `code/revert_merge/unit_tests/test_git_ops.py`
   - 5.1. [minor] Lines 37-41: `test_run_git_invokes_git_with_prefixed_args` asserts `cwd`, `capture_output`, and `text` are forwarded to `subprocess.run` but not `check=False` — the choice that makes the wrapper, rather than `subprocess`, own the raise-and-redact path. Without it, a change to `check=True` would hand the raise to `subprocess`, which builds `CalledProcessError.cmd` from the real argv rather than the redacted `log_args`, and the token-hygiene contract this module exists to pin would be lost by an edit no assertion here objects to. One line, in the test that already inspects the forwarded kwargs.
        - Current: `    assert kwargs["text"] is True`
        - Expected:
          ```python
          assert kwargs["text"] is True
          # run_git owns the raise so it can log/redact before raising
          assert kwargs["check"] is False
          ```
        - Resolution: Implemented as specified — added the `check is False` assertion with its explanatory comment to `test_run_git_invokes_git_with_prefixed_args`, alongside the existing forwarded-kwarg assertions. A change that let `subprocess` own the raise (and so build `CalledProcessError.cmd` from the unredacted argv) now fails here.

## Skills with No Issues

1. Unit Tests: File and function names follow `test_<module_name>.py` / `test_<function>_<scenario>_<expected>` and the file sits in `unit_tests/` beside `conftest.py`; every public function in `git_ops.py` has tests, boundary mocking is correct (`subprocess.run` patched inside `git_ops` via the `fake_subprocess_run` conftest fixture, not at the definition site), each test covers one behavior in Arrange-Act-Assert order with no shared state, `pytest.raises` is used for error paths, and `@pytest.mark.parametrize` drives the two fetch/push leak tests; `pytest --cov=git_ops --cov-report=term-missing` reports 100% line coverage with 19 tests passing. See findings 1-3 and 5.1 for assertion-strength gaps.
2. Type Hints: No issues found — every test annotates all parameters (`dict[str, Any]`, `Path`, `pytest.LogCaptureFixture`, `str`) and returns `-> None`; modern built-in generics throughout.
3. Docstrings: No issues found — the module docstring (lines 1-11) explains the patching seam and the token-hygiene contract under test; the remaining tests carry self-documenting `test_<function>_<scenario>_<expected>` names per the unit-tests naming convention. See finding 3.1 for an accuracy fix to one existing docstring.
4. Comments: No issues found — inline comments explain "why" (the `rev-list --parents` root-commit output shape at line 128, the `str.format` unused-kwarg semantics at lines 165-168, the credential-hygiene rationale at lines 215-216, the argv-vs-log split at lines 96-99).
5. Logging: N/A — the module emits no application logging; it only asserts on captured output via `caplog`.
6. Exception Handling: N/A — no `try`/`except` of its own; expected errors are verified with `pytest.raises`.
7. Executable Scripts: N/A — not an executable script (no `main()`, argparse, or TOML config).
8. Data Validation: N/A — not a `data_val_*` script and no dataset is produced or checked.
