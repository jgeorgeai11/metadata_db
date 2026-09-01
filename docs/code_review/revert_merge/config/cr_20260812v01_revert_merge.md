---
name: cr_20260812v01_revert_merge
goal: First review of code/revert_merge/config/revert_merge.toml against the python-development executable-scripts and comments standards after the GitLab-to-GitHub migration filled in the remote URL, reviewed alongside revert_merge.py and its unit tests.
created: 2026-08-12 13:53:03
updated: 2026-08-12 14:19:09
---

## Implementation Plan

1. [completed] Deferred config-documentation enhancements - `code/revert_merge/config/revert_merge.toml`
   - 1.1. [suggestion] Lines 12-18: The `remote_url_template` comment explains the runtime `{token}` substitution but not the two refusal rules `revert_merge.py` enforces on this exact value — the script refuses before touching git if the template still carries any `<...>` placeholder or lacks `{token}` (`revert_merge.py` lines 106-122). The `bot_name` / `bot_email` comment 15 lines below does document its own refusal rule, so the file is asymmetric about the one thing an operator editing it most needs to know.
        - Current: `# ... The token is NEVER stored here.`
        - Expected: `# ... The token is NEVER stored here. The script refuses to run (before # any git operation) if this value lacks \`{token}\` or still carries an # unfilled \`<...>\` placeholder.`
        - Resolution: Deferred — optional; the value ships correct and fully filled in, the refusal messages themselves name the exact defect and remediation, and the algorithm in readme/metadata-db-maintenance.md ("The revert script", step 1) documents both rules.
   - 1.2. [suggestion] Lines 19, 25, 34-35: Nothing tests this shipped artifact — `unit_tests/test_revert_merge.py` synthesizes a config in every test, so a typo'd key name or a `{token}`-less URL committed here would surface only during a live incident, when the revert job is the last line of defense. A test that `tomllib.load`s this file and asserts the four keys `run()` reads are present, and that `remote_url_template` contains `{token}`, would guard it.
        - Resolution: Deferred here only — no change belongs in this file; the guard is a test, and it has been promoted to `[minor]` and is being implemented as item 3.1 of `docs/code_review/revert_merge/unit_tests/cr_20260812v01_test_revert_merge.md`. Recorded here for cross-file traceability. (The original deferral — that no other module tests its shipped config TOML — was outweighed by this config being read only during an incident, so a defect in it has no earlier signal.)
   - 1.3. [suggestion] Lines 19, 25, 34-35: All four keys are required — `run()` reads each with `config[...]`, so omitting any one is a `KeyError` — but the file never says so. The executable-scripts skill's TOML example separates "Required fields" from "Optional fields (defaults shown in comments)"; this config has no such marking, unlike the sibling `load_catalog_data.toml`, which annotates its optional guard knobs with their defaults.
        - Current: four bare assignments under prose comments
        - Expected: a `# All four fields below are required; the script exits 1 on a missing key.` line under the header block
        - Resolution: Deferred — optional; with exactly four keys and no optional ones, there is no required/optional split to disambiguate, and each key's comment already reads as mandatory ("these are required" for the identity pair).
   - 1.4. [suggestion] Lines 6-10: The header describes what the script does on success ("verifies preconditions, and pushes a clean `git revert -m 1 <sha>` directly to `main`") but never states the refusal contract — that a failed precondition means exit non-zero with nothing pushed. An operator who reads only this file to understand the config would not learn that a misconfigured value here fails the pipeline loudly rather than doing something partial.
        - Current: `# ... verifies preconditions, and # pushes a clean \`git revert -m 1 <sha>\` directly to \`main\`.`
        - Expected: `# ... verifies preconditions, and # pushes a clean \`git revert -m 1 <sha>\` directly to \`main\` — or exits # non-zero without pushing anything if any precondition fails.`
        - Resolution: Deferred — optional; the header already points at both `.github/workflows/post_merge.yml` and readme/metadata-db-maintenance.md, where the refusal contract is stated in full, and the config file is not the canonical place for the script's behavioral contract.
   - 1.5. [suggestion] Line 19: This value is fully filled in (`https://x-access-token:{token}@github.example.com/Warehouse/metadata_db.git`), but `revert_merge.py` lines 48, 78, and 103 still describe the shipped config as holding a `<group>` placeholder in the URL — GitLab-era vocabulary and, since the migration, a factually stale claim about this file.
        - Resolution: Deferred — no change belongs in this file; line 19 is correct and matches readme/metadata-db-maintenance.md's worked example. The fix is tracked against the source at `docs/code_review/revert_merge/cr_20260812v01_revert_merge.md` items 1.1 and 1.2 (`revert_merge.py`). Recorded here only for cross-file traceability.

## Skills with No Issues

1. Executable Scripts: Issues found (see items 1.3, 1.4, all deferred) — otherwise conformant with the TOML-config convention: the file lives at `{script_dir}/config/{script_name}.toml`, opens with a `# Configuration for revert_merge.py` header and a `# Usage:` block whose command line matches both the module docstring and the `revert_failed_load` step in `.github/workflows/post_merge.yml` verbatim, and holds no secrets (the token is read from the environment).
2. Comments: Issues found (see items 1.1, 1.4, 1.5, all deferred) — otherwise every comment explains "why" rather than restating the key name (why `x-access-token` serves both a GitHub App token and a fine-grained PAT; why the bot is the only identity that may push to `main`; why `git revert` needs an explicit identity on a stock CI container) and every cross-reference resolves — `readme/metadata-db-maintenance.md` does carry "Cleanup bot account" and an activation checklist whose item 4 is filling `bot_name` / `bot_email` in this file.
3. Type Hints: N/A - TOML data file, no functions.
4. Docstrings: N/A - TOML data file, no functions.
5. Logging: N/A - TOML data file, emits no output.
6. Exception Handling: N/A - TOML data file; the consuming script's config error handling is reviewed in `docs/code_review/revert_merge/cr_20260812v01_revert_merge.md`.
7. Data Validation: N/A - configuration for a CI-side cleanup script, not a data pipeline output.
8. Unit Tests: Issues found (see item 1.2) - this artifact is not itself exercised by the suite; the guard is tracked as item 3.1 of `docs/code_review/revert_merge/unit_tests/cr_20260812v01_test_revert_merge.md`.
