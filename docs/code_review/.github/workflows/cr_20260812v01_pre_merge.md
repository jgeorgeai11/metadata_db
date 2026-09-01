---
name: cr_20260812v01_pre_merge
goal: Address code quality issues identified in .github/workflows/pre_merge.yml to align with the comments skill and GitHub Actions hardening practice, reviewed together with .github/workflows/post_merge.yml and .github/CODEOWNERS.
created: 2026-08-12 13:56:26
updated: 2026-08-12 15:03:29
---

## Implementation Plan

1. [completed] Close the GITHUB_TOKEN exposure the file's own trust boundary assumes away - `.github/workflows/pre_merge.yml`
   - 1.1. [major] Line 59-63: No `permissions:` block, so every job inherits the repository/organization default `GITHUB_TOKEN` scope (historically read-write). The header (lines 32-53) states the design goal that "unreviewed branch code never holds a write credential" and splits the Postgres role to achieve it, but the workflow token — which `pytest`, the loader dry-run and every other step in these jobs can read — is left at the default. `post_merge.yml` has the same omission (see `cr_20260812v01_post_merge.md`), so setting it in both keeps the two files consistent.
        - Current: `on:\n  pull_request:\n\njobs:`
        - Expected: `on:\n  pull_request:\n\npermissions:\n  contents: read\n\njobs:`
        - Resolution: Implemented as specified — added a workflow-level `permissions: contents: read` (the scope `actions/checkout` needs and nothing more), with a comment recording that no job here uses the workflow token and that the declaration is what makes the header's trust-boundary claim true of the token, not just the Postgres role. `post_merge.yml` finding 3.1 got the same block in the same pass.
   - 1.2. [minor] Lines 88, 115, 154-156, 264-266: `actions/checkout@v4` defaults to `persist-credentials: true`, writing an `http.extraheader` bearing the `GITHUB_TOKEN` into `.git/config` in a workspace that then executes unreviewed branch code. None of the four jobs push anything, so the credential is pure exposure. `code/revert_merge/git_ops.py` documents the opposite discipline for its own token ("Nothing token-bearing is ever persisted in `.git/config`"); the checkouts should follow it.
        - Current: `      - uses: actions/checkout@v4`
        - Expected: `      - uses: actions/checkout@v4\n        with:\n          persist-credentials: false`
        - Resolution: Implemented as specified on all four checkouts (the two that already carried `fetch-depth: 0` keep it). The rationale — including the `git_ops.py` discipline it matches — is written out once, on the first checkout in `unit_tests`, and the other three carry the bare key rather than repeating the paragraph. Same change applied to all three checkouts in `post_merge.yml` (finding 3.2).

2. [completed] Make the diff computations and their comments agree - `.github/workflows/pre_merge.yml`
   - 2.1. [minor] Lines 177-178 and 186-187: The two `--diff-filter=A` diffs in `check_schema_in_sync` omit `--no-renames`, while every exemption diff in `validate_ref_data` (lines 284, 290, 298) sets it. The file itself documents the hazard at lines 236-240: with rename detection on (git's default), a renamed file is reported as `R` and is silently dropped by `--diff-filter=A`. A PR that renames a not-yet-applied migration file therefore computes no `--allow-pending` for it and can never pass its own check.
        - Current: `          for f in $(git diff --name-only --diff-filter=A \`
        - Expected: `          for f in $(git diff --name-only --no-renames --diff-filter=A \`
        - Resolution: Implemented as specified on both `check_schema_in_sync` diffs, so all six diffs in the file now set the flag. Added one accompanying change beyond the finding: the step's preceding comment documented only `--diff-filter=A`, so it now also states why `--no-renames` is there (a renamed, not-yet-applied migration would otherwise be reported as `R`, dropped by the filter, and left with no `--allow-pending`) — the hazard was documented only in `validate_ref_data`'s block before.
   - 2.2. [minor] Line 240: The comment "Every diff runs with `--no-renames`" sits in the `validate_ref_data` block but is not true of that job's own "Detect ref-gate changes" diff (line 270), which omits the flag. The omission is harmless there (the step only tests for non-empty output, and a rename still appears in it), but the blanket wording invites a reader to assume a guarantee the job does not make.
        - Current: `# Every diff runs with --no-renames: git's rename detection (default`
        - Expected: `# Every EXEMPTION diff runs with --no-renames (the change-detection\n# diff above needs none — it only tests for a non-empty result):\n# git's rename detection (default`
        - Resolution: Implemented with a wording deviation — "the change-detection diff above" became "the change-detection gate described above ... which a rename produces either way", because the step itself sits *below* this header comment (only its description is above), and the parenthetical now says explicitly why a rename cannot change that gate's answer. Content is otherwise as specified, and it stays true after finding 2.3 rewrote that gate (it still only tests its diff for a non-empty result).
   - 2.3. [minor] Lines 270-276: The change-detection step is fail-open. `git diff ... | grep -q .` yields the pipeline's exit status from `grep`, so a `git` failure (missing parent, corrupt or shallow-grafted checkout) is indistinguishable from "no ref path changed" and the step writes `run=false` — the ref validation silently does not run and the PR goes green. `post_merge.yml` shares this idiom on a higher-stakes gate (see `cr_20260812v01_post_merge.md`, finding 1.1); fixing both the same way keeps the pattern uniform. Capturing the diff into a variable first lets the step's default `bash -e` abort on a git failure.
        - Current: `          if git diff --name-only HEAD^1 HEAD -- \\\n              data_ref/ data_catalog/sources/ref/ code/apply_ddl/ddl_ref/ \\\n              | grep -q .; then`
        - Expected: `          CHANGED="$(git diff --name-only HEAD^1 HEAD -- \\\n              data_ref/ data_catalog/sources/ref/ code/apply_ddl/ddl_ref/)"\n          if [ -n "$CHANGED" ]; then`
        - Resolution: Implemented as specified, with a comment on the step recording that the capture is what makes the gate fail closed. Verified in a scratch repo that a bad base object now exits 128 with no `run=` written (previously the pipeline swallowed it and wrote `run=false`). The three gates in `post_merge.yml` were rewritten the same way in the same pass (finding 1.1 there); those filter on `.yaml$`, so they keep `printf '%s\n' "$CHANGED" | grep -q` where this one, testing only for non-empty, uses `[ -n ]`.

3. [completed] Add the runner-capacity guards a single self-hosted runner needs - `.github/workflows/pre_merge.yml`
   - 3.1. [minor] Lines 59-63: No `concurrency` group, so every push to a PR branch starts another four jobs while the previous run's four are still queued on the one self-hosted runner the header describes (lines 24-30). Superseded PR runs carry no information, so cancelling them is safe and is the standard PR-workflow pattern; `post_merge.yml` already serializes its runs this way (there with `cancel-in-progress: false`, correctly, because those runs are not disposable).
        - Current: `on:\n  pull_request:\n\njobs:`
        - Expected: `on:\n  pull_request:\n\nconcurrency:\n  group: pre_merge-${{ github.ref }}\n  cancel-in-progress: true\n\njobs:`
        - Resolution: Implemented as specified (sits after the new `permissions:` block from 1.1), with a comment stating why a superseded PR run is disposable here and why `post_merge.yml` deliberately sets the opposite `cancel-in-progress`. On a `pull_request` event `github.ref` is the PR's own `refs/pull/<n>/merge`, so the group is per-PR, not per-branch-name.
   - 3.2. [minor] Lines 70-72, 102-104, 141-143, 251-253: No job sets `timeout-minutes`, so a job that hangs on an unreachable Postgres (a real possibility for jobs whose only work is a DB round trip) holds the shared runner for the 360-minute default before GitHub kills it, blocking every other PR and every post-merge load in the meantime.
        - Current: `    if: vars.METADATA_DB_CI_ENABLED == 'true'\n    runs-on: self-hosted`
        - Expected: `    if: vars.METADATA_DB_CI_ENABLED == 'true'\n    runs-on: self-hosted\n    timeout-minutes: 30`
        - Resolution: Implemented as specified — `timeout-minutes: 30` on all four jobs (and on all three in `post_merge.yml`, finding 4.1 there). Rationale is written once, on the first job of each file, rather than repeated four times. 30 minutes is far above any real run here and an order of magnitude below the 360-minute default.
   - 3.3. [minor] Lines 72, 104, 143, 253: `runs-on: self-hosted` matches any registered self-hosted runner, but the header (lines 24-30) states each job requires Docker (for `container:`), a network path to Postgres, and access to the Debian/PyPI mirrors. If the instance ever registers a second runner without Docker, jobs land on it and fail at container creation with an error that does not name the cause. A specific label set makes the requirement enforceable rather than documentary.
        - Current: `    runs-on: self-hosted`
        - Expected: `    runs-on: [self-hosted, linux, docker]   # labels applied at runner registration`
        - Resolution: Implemented as specified on all four jobs, with the reasoning added to the header's RUNNERS section (`post_merge.yml` uses the same set and points at that section, finding 4.2 there). Two accompanying doc changes, since the labels turn a documented prerequisite into a hard scheduling requirement: item 1 of the activation checklist in `readme/metadata-db-maintenance.md` now says to register the runner **with** the `linux` and `docker` labels and warns that jobs queue indefinitely without them, and the workflow sketches in that section show the label set instead of bare `self-hosted`.

4. [completed] Pin the parts of the toolchain the lockfile does not cover - `.github/workflows/pre_merge.yml`
   - 4.1. [minor] Lines 90, 117, 158, 279: `pip install --no-cache-dir uv` resolves to whatever uv release exists on the day the job runs. It is the one unpinned dependency in an otherwise `uv.lock`-pinned project, and a breaking uv release takes down all four PR jobs (and all three post-merge jobs) at once with no repo change to point at.
        - Current: `        run: pip install --no-cache-dir uv`
        - Expected: `        run: pip install --no-cache-dir "uv==<pinned version>"`
        - Resolution: Implemented, resolving the placeholder to `uv==0.11.1` — the version maintainers run locally today (`uv --version` → 0.11.1), which is the version that produced the committed `uv.lock`. Applied at all four sites here and all three in `post_merge.yml` (finding 4.3 there); the literal is repeated at each site rather than hoisted into a workflow-level `env:`, matching this file's stated preference for jobs that read end to end without indirection (see 5.1). A new TOOLCHAIN PINS section in the header states why uv is the one dependency the lockfile cannot cover and that bumping the pin is a reviewed one-line PR that must move both workflows together.
   - 4.2. [minor] Lines 92, 119-122, 181-182, 190-191, 305-307: `uv run` re-resolves and rewrites `uv.lock` when `pyproject.toml` has drifted from it, so a PR that edits dependencies without refreshing the lockfile passes CI against an environment that is not the committed one, and nothing flags the stale lock. `uv run --locked` fails the job instead.
        - Current: `        run: uv run pytest code/`
        - Expected: `        run: uv run --locked pytest code/`
        - Resolution: Implemented as specified on all five `uv run` invocations here and all three in `post_merge.yml` (finding 4.4 there); the header's new TOOLCHAIN PINS section records the reason. Confirmed the flag is safe to adopt now: `uv lock --check` passes against the committed `pyproject.toml`, and the full suite runs clean under `uv run --locked pytest code/`. The maintainer-run commands in `readme/metadata-db-maintenance.md` are deliberately left unflagged — `--locked` is a CI guard, and a maintainer refreshing a lock locally should not be blocked by it.

5. [completed] Optional refinements - `.github/workflows/pre_merge.yml`
   - 5.1. [suggestion] Lines 86-90, 113-117, 152-158, 262-266: The four jobs repeat the same `Install git` / `actions/checkout` / `Install uv` sequence, and three repeat the same four-line RO `env:` block. A local composite action (`.github/actions/setup-uv/action.yml`) would collapse the repetition.
        - Resolution: Deferred — optional structural refactor. The duplication is three steps per job, and keeping each job readable end to end without following an indirection is worth more here than the DRY gain, especially while the workflows are dormant and being read as documentation.
   - 5.2. [suggestion] Lines 88, 115, 154, 264: `actions/checkout@v4` is referenced by a moving major tag rather than a pinned commit SHA, so the action's contents can change under the workflow.
        - Resolution: Deferred — `actions/checkout` is a first-party action served from the GHES instance's own mirror, so the supply-chain exposure a SHA pin addresses is largely bounded by the instance already; pinning adds a recurring upgrade chore for little marginal gain on an internal repo.
   - 5.3. [suggestion] Lines 155-156, 265-266: `fetch-depth: 0` clones the full history, but every diff in both jobs is `HEAD^1 HEAD`, which `fetch-depth: 2` already satisfies.
        - Resolution: Deferred — the repo is small enough that a full clone costs little, and depth 0 stays correct if a future check ever needs to reach further back; the comment at line 138 already explains the choice.
   - 5.4. [suggestion] Line 251: No job checks that each source's `data_source.yaml` `owner` still agrees with the `.github/CODEOWNERS` rule for that folder, though `readme/metadata-db-maintenance.md` (line 148) states they should agree. The drift is silent in both directions — see the matching finding in `cr_20260812v01_CODEOWNERS.md`.
        - Resolution: Deferred — enforcing the agreement needs a new script and its own tests; today the pairing is checked by the maintainer review that every `data_source.yaml` change already requires, and a missed CODEOWNERS entry degrades safely to maintainer routing.

## Skills with No Issues

1. Docstrings skill: N/A - declarative YAML workflow, no Python callables
2. Type Hints skill: N/A - not Python
3. Logging skill: N/A - not Python; step output is the runner log
4. Exception Handling skill: N/A - not Python
5. Executable Scripts skill: N/A - CI configuration, not an executable script
6. Unit Tests skill: N/A - CI configuration is not unit-testable in this repo
7. Data Validation skill: N/A - performs no validation itself; it invokes the validating scripts
8. SQL Development skill: N/A - contains no SQL
