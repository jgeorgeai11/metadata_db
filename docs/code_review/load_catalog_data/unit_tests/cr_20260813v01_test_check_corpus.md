---
name: cr_20260813v01_test_check_corpus
goal: Address code quality issues identified in code/load_catalog_data/unit_tests/test_check_corpus.py to align with python-development (unit-tests, type-hints, comments, docstrings) skills; first review of this new test file, done as a group with check_corpus.py.
created: 2026-08-13 11:33:32
updated: 2026-08-13 11:33:32
---

## Implementation Plan

1. [pending] Share the staged-corpus helper via conftest.py instead of a cross-test-module private import - `code/load_catalog_data/unit_tests/test_check_corpus.py`
   - 1.1. [minor] Line 14: the file imports the private helper `_stage_corpus` from the sibling test module `test_load_catalog_data` — shared test data belongs in `conftest.py` (unit-tests guideline 3.1), which already exists in this suite and already hosts the shared `fake_conn`/`fake_cursor` fixtures. The import couples this file to a private name a sibling refactor may freely rename, and it executes `test_load_catalog_data.py`'s module level, which imports `load_catalog_data` and hence `psycopg2` — pulling database imports into the test run of a deliberately database-free script.
        - Current: `from test_load_catalog_data import _stage_corpus`
        - Expected: move `_stage_corpus` (with its `_write` dependency) from `test_load_catalog_data.py` into `conftest.py` as a shared helper, and import it from there in both test modules
        - Resolution: _pending_

2. [completed] Optional refinements - `code/load_catalog_data/unit_tests/test_check_corpus.py`
   - 2.1. [suggestion] Lines 69 and 91: the two clean-corpus tests unpack `data_root, cfg = staged_cfg` but never use `data_root`; `_, cfg = staged_cfg` would make the unused binding explicit. Same pattern as cr_20260813v01_test_load_catalog_data.md finding 2.2 (grouped-suite consistency).
        - Current: `data_root, cfg = staged_cfg` (with `data_root` unused in the test body)
        - Expected: `_, cfg = staged_cfg` in the two tests that only need the config path
        - Resolution: Deferred — same grounds as the sibling suite's deferred finding 2.2: the uniform named unpack documents the fixture's `(data_root, cfg)` shape and keeps these tests symmetric with the two failure tests that do mutate the staged corpus through `data_root`; no linter is configured to flag the unused binding.
   - 2.2. [suggestion] Lines 103-118: `test_mirror_log_to_stderr_attaches_plain_info_handler_to_root` unit-tests the private helper `check_corpus._mirror_log_to_stderr` directly and inspects the root logger's handler list (unit-tests guideline 6: don't assert on private internals); the public behavior — progress mirrored to stderr, stdout empty — is already pinned by `test_main_clean_corpus_mirrors_progress_to_stderr`.
        - Current: `check_corpus._mirror_log_to_stderr()` / `added = [h for h in root.handlers if h not in before]`
        - Expected: rely on the `main()`-level stderr test alone, or promote the helper if its handler shape becomes public API
        - Resolution: Deferred — the handler details this test pins (exactly one handler, INFO threshold, plain `%(message)s` format preserving `→`) are load-bearing invariants not observable through the `main()`-level capsys test, and pinning invariants at the private helper matches this suite's accepted pattern (cr_20260813v01_test_load_catalog_data.md finding 2.1's deferral).

## Skills with No Issues

1. Unit Tests skill — naming: No issues found. The file mirrors its module (`test_check_corpus.py` for `check_corpus.py`), and test names follow `test_<function>_<scenario>_<expected>` (e.g., `test_main_validation_failure_exits_1_and_logs_one_record_per_issue`, `test_main_zero_arguments_reads_data_root_from_loader_config`).
2. Unit Tests skill — coverage: No issues found. All of `main()`'s arms are pinned: clean corpus (return, summary counts, "Not checked here" callout, stderr mirror with empty stdout), assembly failure, validation failure (single-line summary record plus per-issue records), missing corpus root, missing config file, unparsable TOML, missing `data_root` field, and the zero-argument script-relative default — each failure asserting both `SystemExit(1)` and the intended `caplog` message so a run cannot pass for the wrong reason.
3. Unit Tests skill — fixtures, structure, and independence: Issues limited to task 1.1 (sharing mechanism) — otherwise the function-scoped `staged_cfg` fixture gives each test its own mutable corpus under `tmp_path`, the autouse `_restore_root_handlers` fixture detaches (and closes) the handlers each `main()` run adds so tests stay order-independent and do not leak handlers into other modules' tests, Arrange-Act-Assert structure is consistent, and `pytest.raises(SystemExit)` with `excinfo.value.code` pins the exit contract. Mocking stays at external boundaries (`sys.argv`, cwd via `monkeypatch.chdir`); nothing internal is patched.
4. Type Hints skill: No issues found. Every helper, fixture, and test is annotated with modern, specific syntax (`Iterator[None]`, `tuple[Path, Path]`, `pytest.CaptureFixture[str]`, `pytest.LogCaptureFixture`).
5. Docstrings skill: No issues found. The module, both helpers (`_stage_config`), and both fixtures (`_restore_root_handlers`, `staged_cfg` with Args/Returns) carry docstrings documenting the "why"; per this suite's standing convention (cr_20260803v01 under Docstrings), test functions rely on descriptive names plus intent comments.
6. Comments skill: No issues found. Comments explain the "why" and were verified accurate against current sources: the "2 data sources, 3 tables" count matches `_stage_corpus`'s staged corpus (ocs.bene, ocs.claim, edw_prd.bene), the single-line-summary comment matches the checker's per-issue `ValidationError` logging, and the zero-argument comment matches the shipped loader TOML's `data_root = "data_catalog"` and the script-relative `--config` default.
7. Logging skill: N/A — test module; log output is asserted via `caplog`/`capsys`, not produced.
8. Exception Handling skill: N/A — no production exception handling; expected exits are asserted via `pytest.raises(SystemExit)`.
9. Executable Scripts skill: N/A — not an executable script (it tests one).
10. Data Validation skill: N/A — this suite exercises the checker's validation rather than performing data validation itself.
