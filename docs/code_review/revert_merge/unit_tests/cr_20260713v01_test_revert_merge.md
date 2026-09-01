---
name: cr_20260713v01_test_revert_merge
goal: Address code quality issues identified in code/revert_merge/unit_tests/test_revert_merge.py to align with python-development skills.
created: 2026-07-13 22:35:05
updated: 2026-07-14 08:05:00
---

## Implementation Plan

1. [completed] Fix inaccurate comments - `code/revert_merge/unit_tests/test_revert_merge.py`
   - 1.1. [minor] Line 122: Comment says "5 git commands" but only 4 are asserted (`fetch`, `checkout`, `revert`, `push`); `set_authenticated_remote` is asserted separately on lines 118-120 and is not a `run_git` call. An inaccurate count comment misleads the next reader about the expected sequence.
        - Current: `# Then 5 git commands in this exact order:`
        - Expected: `# Then 4 git commands in this exact order:`
        - Resolution: Implemented as specified — changed the comment to `# Then 4 git commands in this exact order:` to match the 4 asserted `run_git` calls.
   - 1.2. [suggestion] Line 10: The module docstring references "the expected 6-step sequence" for the happy path, but the happy-path test verifies 1 `set_authenticated_remote` call plus 4 `run_git` calls. The "6-step" wording is carried over from the source docstring and is ambiguous relative to what this test actually asserts; consider restating in terms of the concrete calls this test checks (e.g., "set-url + fetch + checkout + revert + push").
        - Current: `the expected 6-step sequence ran for the happy path`
        - Expected: `the expected set-url + fetch + checkout + revert + push sequence ran for the happy path`
        - Resolution: Deferred — suggestion left unimplemented per the code-implementation workflow.

2. [completed] Tighten type hints - `code/revert_merge/unit_tests/test_revert_merge.py`
   - 2.1. [minor] Line 62: `fake_run_git` is annotated `-> Any`. The type-hints skill asks for specific types over `Any`. The stub returns a `MagicMock` standing in for `run_git`'s real `subprocess.CompletedProcess[str]`; annotate it accordingly so the stub's contract is explicit.
        - Current: `def fake_run_git(args: list[str], cwd: Path, **kwargs: Any) -> Any:`
        - Expected: `def fake_run_git(args: list[str], cwd: Path, **kwargs: Any) -> subprocess.CompletedProcess[str] | MagicMock:`
        - Resolution: Implemented as specified — annotated the return type `subprocess.CompletedProcess[str] | MagicMock` (matching `run_git`'s real return in git_ops.py). `subprocess` and `MagicMock` were already imported; the signature was wrapped across lines to stay within the line width.

3. [completed] Add docstrings to test functions - `code/revert_merge/unit_tests/test_revert_merge.py`
   - 3.1. [suggestion] Lines 106, 139, 155, 171, 185, 203, 222, 248, 276, 298, 322, 350, 385, 417, 449, 483: The docstrings skill states all public functions need docstrings; only `test_main_token_never_in_logs_across_happy_path` (line 520) has one. The test names are descriptive and self-documenting, so this is optional, but a one-line docstring per test (or per section) would document the intent (e.g., the "why" behind each refusal path) consistently with the fixtures and helpers, which are all documented.
        - Resolution: Deferred — suggestion left unimplemented per the code-implementation workflow.

4. [completed] Reduce boilerplate in main() error-dispatch tests - `code/revert_merge/unit_tests/test_revert_merge.py`
   - 4.1. [suggestion] Lines 298-512: The eight `test_main_*` tests exercising `main()`'s exception arms share nearly identical scaffolding (stub `setup_logging`, write config, patch `sys.argv`, patch `revert_merge.run` to raise, assert `exc.value.code == 1`, assert a substring in `caplog.text`). Per the unit-tests skill's parametrize guidance, these could collapse into one `@pytest.mark.parametrize`-driven test over `(raised_exception, expected_log_substring)`, cutting duplication while preserving coverage. The config-not-found and bad-TOML cases (lines 298, 322) differ enough to keep separate.
        - Resolution: Deferred — suggestion left unimplemented per the code-implementation workflow.

## Skills with No Issues

1. Type Hints: Issues found (see item 2) — all functions are otherwise fully annotated with modern syntax (`dict[str, Any]`, `list[str]`, `str | None`-style usage).
2. Docstrings: Issues found (see item 3) — fixtures and private helpers are all documented; test functions are not.
3. Comments: Issues found (see item 1).
4. Logging: No issues found — tests correctly use the `caplog` built-in fixture to assert on log content and verify the token never leaks (lines 131, 545).
5. Exception Handling: No issues found — `pytest.raises(..., match=...)` is used correctly throughout to assert error types and messages.
6. Unit Tests: Mostly conformant — pytest is used (not unittest), naming follows `test_<function>_<scenario>_<expected>`, fixtures live in the file/`conftest.py`, mocking is at the correct external boundaries (`run_git`, `head_sha`, `parent_shas`, `set_authenticated_remote`) and patched where used, `@pytest.mark.parametrize` is applied (line 154), and refusal/failure/config paths are all covered. See item 4 for an optional DRY improvement. Note: the skill's guideline 4 names `mocker.patch()`, but `monkeypatch` (used here) is an approved built-in fixture per the skill's own reference table and is the idiomatic tool for `setenv`/`delenv`/`setattr(sys, "argv", ...)`; no change needed.
7. Data Validation: N/A - test module, no data outputs to validate.
8. Executable Scripts: N/A - test module, not a CLI entrypoint.
