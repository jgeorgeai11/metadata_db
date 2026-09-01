---
name: cr_20260812v01_post_merge
goal: Address code quality issues identified in .github/workflows/post_merge.yml to align with the comments skill and GitHub Actions hardening practice, reviewed together with .github/workflows/pre_merge.yml and .github/CODEOWNERS.
created: 2026-08-12 13:56:26
updated: 2026-08-12 15:03:29
---

## Implementation Plan

1. [completed] Make the path gates fail closed instead of open - `.github/workflows/post_merge.yml`
   - 1.1. [major] Lines 90, 130-131, 182-183: All three change-detection steps take the exit status of `grep` at the end of a pipeline, so a failure of the `git diff` on the left of the pipe is indistinguishable from "nothing matched" and the step writes `run=false`. For `load_catalog_data` that means the load is silently skipped, the run stays green, and `main` moves ahead of Postgres with no signal — the exact invariant the workflow exists to protect — and the mirror gate in `revert_failed_load` then declines to revert as well. A missing `HEAD^` under `fetch-depth: 2` (root commit, or an unusual push shape) reaches this path. Capturing the diff into a variable first lets the step's default `bash -e` shell abort on a git failure; `pre_merge.yml` carries the same idiom in `validate_ref_data` (see `cr_20260812v01_pre_merge.md`, finding 2.3) and should be changed with it.
        - Current: `          if git diff --name-only HEAD^ HEAD -- data_catalog/ \\\n              | grep -q '\.yaml$'; then`
        - Expected: `          CHANGED="$(git diff --name-only HEAD^ HEAD -- data_catalog/)"\n          if printf '%s\n' "$CHANGED" | grep -q '\.yaml$'; then`
        - Resolution: Implemented as specified on all three gates, folded together with finding 1.2 so each gate was rewritten once (the base is `github.event.before`, not `HEAD^`). `unit_tests_main`'s gate applies no extension filter, so it tests `[ -n "$CHANGED" ]` rather than `printf | grep`; the two corpus-YAML gates keep `printf '%s\n' "$CHANGED" | grep -q '\.yaml$'` and are byte-identical to each other, which was verified by parsing the YAML and comparing the two step objects. Verified the fail-closed behaviour in a scratch repo: a bad base object now exits 128 with no `run=` written, where the old pipeline wrote `run=false`. `pre_merge.yml`'s `validate_ref_data` gate was changed the same way in the same pass (finding 2.3 there).
   - 1.2. [minor] Lines 84-94, 127-135, 179-187: `HEAD^ HEAD` covers only the tip commit of a push, so the gates are correct only while the "merge-commit-only, no direct pushes" repository settings hold — items 5 and 6 of the activation checklist in `readme/metadata-db-maintenance.md`, neither of which is applied yet. A multi-commit push to `main` (an emergency or admin push) would compare against the wrong base and skip the load silently. The push event supplies the true previous tip as `github.event.before`, which is exact regardless of push shape; using it needs the full history the revert job already fetches.
        - Current: `          if git diff --name-only HEAD^ HEAD -- data_catalog/ \`
        - Expected: `          if git diff --name-only ${{ github.event.before }} "$GITHUB_SHA" -- data_catalog/ \`
        - Resolution: Implemented as specified, quoted (`"${{ github.event.before }}" "$GITHUB_SHA"`) and combined with 1.1's capture form. As the finding notes this needs full history, `fetch-depth` went from 2 to 0 on `unit_tests_main` and `load_catalog_data` (`revert_failed_load` already had 0), so all three checkouts now match and the depth-2 comment was replaced. Confirmed the behaviour change in a scratch repo: for a 2-commit push whose YAML edit is in the *first* commit, the old `HEAD^ HEAD` gate wrote `run=false` and the new base writes `run=true`. Left the expression interpolated inline rather than passed via `env:` — the value is a GitHub-generated SHA, not user text, so there is no injection surface; the step comment now says so explicitly. Also noted there that an unreachable `before` (e.g. after a force push) fails the step, which is the intended fail-closed direction.

2. [completed] Correct the concurrency comment's safety claim - `.github/workflows/post_merge.yml`
   - 2.1. [minor] Lines 44-49: The comment justifies GitHub dropping a superseded pending run with "that is safe here because the loader is full-state". That argument covers `load_catalog_data` only. A superseded run also drops `unit_tests_main` for its commit, and because each run's gate diffs only its own `HEAD^ HEAD`, a `code/` change merged between two rapid merges is then never unit-tested on `main` — the precise gap the job's own comment (lines 57-62) says it exists to close. The comment should scope its claim to the load rather than the whole run.
        - Current: `# group (an intermediate queued run is superseded); that is safe here\n# because the loader is full-state — the newest run loads the complete\n# corpus at its own commit, covering any superseded merge's changes.`
        - Expected: `# group (an intermediate queued run is superseded). That is safe for\n# the LOAD — the loader is full-state, so the newest run loads the\n# complete corpus at its own commit, covering any superseded merge's\n# changes — but a superseded run also skips unit_tests_main for its\n# commit, so a code/ change merged between two rapid merges goes\n# untested on main until the next code/ merge.`
        - Resolution: Implemented as specified, and the closing sentence narrowed with it — the advisory lock is "the correctness backstop for the load", not for the run as a whole. The same over-broad claim appears in `readme/metadata-db-maintenance.md` ("Merge serialization", the `concurrency` bullet), so that sentence was corrected to match rather than left contradicting the file it documents.

3. [completed] Close the token exposure in the credential-bearing jobs - `.github/workflows/post_merge.yml`
   - 3.1. [minor] Lines 35-39: No `permissions:` block, so all three jobs inherit the repository/organization default `GITHUB_TOKEN` scope. The exposure is smaller than in `pre_merge.yml` (this workflow runs reviewed, on-main code), but none of these jobs uses the workflow token at all — the revert authenticates as the cleanup bot — so an explicit read-only declaration costs nothing and keeps the two workflows consistent (see `cr_20260812v01_pre_merge.md`, finding 1.1).
        - Current: `on:\n  push:\n    branches: [main]`
        - Expected: `on:\n  push:\n    branches: [main]\n\npermissions:\n  contents: read`
        - Resolution: Implemented as specified, with a comment recording that no job here uses the workflow token at all (the revert authenticates as the cleanup bot) and that `contents: read` is exactly what the checkouts need. Matches the block added to `pre_merge.yml` (finding 1.1 there); the sketch in `readme/metadata-db-maintenance.md` shows it too.
   - 3.2. [minor] Lines 176-178 (and 79-83, 124-126): `actions/checkout@v4` defaults to `persist-credentials: true`, so the `GITHUB_TOKEN` is written into `.git/config` of the very workspace where `revert_merge.py` runs. That directly undercuts the property `code/revert_merge/git_ops.py` documents and its tests pin — "Nothing token-bearing is ever persisted in `.git/config`, so on a reused runner workspace the credential cannot outlive the job" — and leaves a second credential in the repo the revert then pushes from.
        - Current: `      - uses: actions/checkout@v4\n        with:\n          fetch-depth: 0`
        - Expected: `      - uses: actions/checkout@v4\n        with:\n          fetch-depth: 0\n          persist-credentials: false`
        - Resolution: Implemented as specified on all three checkouts (all three carry `fetch-depth: 0` after finding 1.2). Confirmed against `code/revert_merge/git_ops.py` that the revert never relies on `origin`'s credentials — `fetch_branch` / `push_branch` pass an authenticated URL built from `CLEANUP_BOT_TOKEN` on each command line — so removing the persisted header cannot break the push; the comment on the revert job's checkout records that this keeps a second credential out of the workspace the revert pushes from.

4. [completed] Add the runner and toolchain guards shared with the PR workflow - `.github/workflows/post_merge.yml`
   - 4.1. [minor] Lines 64-66, 110-112, 164-167: No job sets `timeout-minutes`. Because this workflow also holds a `concurrency` group with `cancel-in-progress: false` (lines 52-54), a load that hangs on an unreachable Postgres blocks every subsequent merge's run for the 360-minute default, and does so while holding the loader's DB advisory lock.
        - Current: `    if: vars.METADATA_DB_CI_ENABLED == 'true'\n    runs-on: self-hosted`
        - Expected: `    if: vars.METADATA_DB_CI_ENABLED == 'true'\n    runs-on: self-hosted\n    timeout-minutes: 30`
        - Resolution: Implemented as specified on all three jobs (same value as `pre_merge.yml`, finding 3.2 there). The rationale is written once, on the first job, and states the two consequences specific to this workflow: `cancel-in-progress: false` means a hung job blocks every subsequent merge's run, and a hung load does it while holding the loader's DB advisory lock.
   - 4.2. [minor] Lines 66, 112, 167: `runs-on: self-hosted` matches any registered self-hosted runner, though every job needs Docker (for `container:`) and the load and revert jobs additionally need a network path to Postgres and to the GitHub remote. A specific label set makes the requirement enforceable; `pre_merge.yml` has the identical issue (see `cr_20260812v01_pre_merge.md`, finding 3.3) and should use the same labels.
        - Current: `    runs-on: self-hosted`
        - Expected: `    runs-on: [self-hosted, linux, docker]   # labels applied at runner registration`
        - Resolution: Implemented as specified on all three jobs, using the identical label set as `pre_merge.yml` (finding 3.3 there), whose header now carries the shared rationale; this file's header was updated to point at it. Activation checklist item 1 in `readme/metadata-db-maintenance.md` now requires registering the runner with the `linux` and `docker` labels, since without them these jobs would queue forever rather than fail visibly.
   - 4.3. [minor] Lines 97, 137, 190: `pip install --no-cache-dir uv` is unpinned — the one unpinned dependency in an otherwise `uv.lock`-pinned project. A breaking uv release would break the post-merge load, and the revert job with it, at the moment they are most needed.
        - Current: `        run: pip install --no-cache-dir uv`
        - Expected: `        run: pip install --no-cache-dir "uv==<pinned version>"`
        - Resolution: Implemented at all three sites, resolving the placeholder to `uv==0.11.1` — the version maintainers run locally and the one that produced the committed `uv.lock` — identical to the pin in `pre_merge.yml` (finding 4.1 there), whose header states the bump policy: it is a reviewed one-line PR that must move both workflows together.
   - 4.4. [minor] Lines 100, 141-143, 193-196: `uv run` silently re-resolves and rewrites `uv.lock` if `pyproject.toml` has drifted from it, so the real load can run against an environment that is not the committed one. `uv run --locked` fails instead of drifting.
        - Current: `        run: uv run pytest code/`
        - Expected: `        run: uv run --locked pytest code/`
        - Resolution: Implemented as specified on all three `uv run` invocations (pytest, the loader, the revert), matching `pre_merge.yml` (finding 4.2 there). Verified `uv lock --check` passes against the committed `pyproject.toml` and that the suite runs clean under `uv run --locked`, so the flag will not fail the real load on adoption.

5. [completed] Optional refinements - `.github/workflows/post_merge.yml`
   - 5.1. [suggestion] Lines 110-112: `load_catalog_data` does not `needs: unit_tests_main`, so a commit that changes both `code/` and corpus YAML loads in parallel with the tests that would have condemned the loader change.
        - Resolution: Deferred — the current arrangement is the safer of the two. Adding `needs:` would make a test failure skip the load, which leaves `main` ahead of the DB with `revert_failed_load` disarmed (`needs.load_catalog_data.result` would be `skipped`, not `failure`); the auto-revert is the intended backstop for a bad loader change and it only works if the load is actually attempted.
   - 5.2. [suggestion] Lines 191-196: When `revert_merge.py` refuses on a precondition, the only signal is a red run — nobody is notified that `main` and the DB are out of sync and awaiting a human.
        - Resolution: Deferred — the design (see "The revert script — Refusal" in `readme/metadata-db-maintenance.md`) deliberately treats the failed run as the human signal; adding notification means choosing and configuring a channel, which belongs with the activation checklist rather than the workflow file.
   - 5.3. [suggestion] Lines 74-79, 121-126, 173-178: The three jobs repeat the same `Install git` / `actions/checkout` / `Install uv` sequence, and two repeat the same change-detection step verbatim. A local composite action would collapse the repetition.
        - Resolution: Deferred — the two change-detection steps are duplicated *deliberately* (lines 152-159 explain that the revert must mirror the load's gate exactly), and factoring them into a shared action would obscure the pairing this design depends on.

## Skills with No Issues

1. Docstrings skill: N/A - declarative YAML workflow, no Python callables
2. Type Hints skill: N/A - not Python
3. Logging skill: N/A - not Python; step output is the runner log
4. Exception Handling skill: N/A - not Python
5. Executable Scripts skill: N/A - CI configuration, not an executable script
6. Unit Tests skill: N/A - CI configuration is not unit-testable in this repo
7. Data Validation skill: N/A - performs no validation itself; it invokes the loader
8. SQL Development skill: N/A - contains no SQL
