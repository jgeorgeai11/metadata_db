---
name: 20260529v01_ci_and_gitlab_setup
goal: Wire the Phase 1–3 scripts into GitLab CI so the metadata_db GitOps loop runs end-to-end. Adds `.gitlab/CODEOWNERS` (ownership routing) and `.gitlab-ci.yml` (4-job pipeline: pre-merge validate, pre-merge schema-sync check, post-merge load, post-merge revert-on-failure). Configures the cleanup-bot account, CI/CD variables, branch protection, and the Free/CE merge-serialization combo per the maintenance doc.
created: 2026-05-29 13:00:00
updated: 2026-06-01 15:00:00
---

## Implementation Plan

1. Create CODEOWNERS routing file - `.gitlab/CODEOWNERS`
   - 1.1. Catch-all rule: `* @metadata-db-maintainers` (anything not matched below)
   - 1.2. Repo-plumbing rules: `code/`, `.gitlab/`, `.gitlab-ci.yml`, `readme/` → `@metadata-db-maintainers`
   - 1.3. Per-system data ownership block under `data/systems/`: top-level `data/` to maintainers; `data/systems/{system}/` to the matching team; cross-system `mappings/*.yaml` listing both source-team and target-team owners (sketch handles from `readme/metadata-db-maintenance.md` — `@warehouse-team`, `@ocs-team`, `@edw-team`, `@cdr-team`)
   - 1.4. Header comment flagging that handles are sketch placeholders that the user must replace before the file goes live
   - 1.5. Free/CE behavior note in the header: per maintenance doc, Free/CE auto-requests reviewers but does NOT enforce approval; the "both teams must approve mapping changes" rule is documentation-only on this tier (Premium would enforce at-least-one; Ultimate via Sections would enforce both)

2. Create CI pipeline config - `.gitlab-ci.yml`
   - 2.1. Three stages declared in order: `validate`, `deploy`, `cleanup`
   - 2.2. Pre-merge job `validate_metadata_db` (stage `validate`): fires on `$CI_PIPELINE_SOURCE == "merge_request_event"`; runs `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml --dry-run`
   - 2.3. Pre-merge job `check_schema_in_sync` (stage `validate`): fires on `$CI_PIPELINE_SOURCE == "merge_request_event"`; runs `uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl.toml --check`
   - 2.4. Post-merge job `load_metadata_db` (stage `deploy`): fires on `$CI_COMMIT_BRANCH == "main"` with `changes:` rule filtering to `data/systems/**/*.yaml`; runs the loader with no `--dry-run`
   - 2.5. Post-merge fallback `revert_failed_load` (stage `cleanup`): fires on `$CI_COMMIT_BRANCH == "main"` with `when: on_failure` and `needs: [load_metadata_db]`; runs `uv run code/revert_merge/revert_merge.py --config code/revert_merge/config/revert_merge.toml --commit-sha "$CI_COMMIT_SHA"`; authenticates as the cleanup bot via `$CLEANUP_BOT_TOKEN`
   - 2.6. All three Postgres-using jobs (`validate_metadata_db`, `check_schema_in_sync`, `load_metadata_db`) authenticate as the single CI Postgres role (see Key Decision #4). Each declares an image with `uv` available (e.g., a python image with uv installed in a `before_script`) and propagates the needed env vars: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `METADATA_DB_DATABASE`. The cleanup job additionally needs `CLEANUP_BOT_TOKEN` (and no Postgres creds).
   - 2.7. Header comment with a job-by-job summary plus the explicit invariant: no DDL ever runs from CI — `apply_ddl.py` only runs manually by maintainers, and the CI Postgres role has no DDL privileges

3. Document the cleanup-bot URL placeholder for `code/revert_merge/config/revert_merge.toml` - `code/revert_merge/config/revert_merge.toml`
   - 3.1. Update `remote_url_template` to the real GitLab project URL (sketch: `https://oauth2:{token}@gitlab.example.com/<group>/metadata-db.git`); flag in the header comment that the host and group are sketch placeholders the user must replace before the cleanup bot can authenticate

4. GitLab platform setup (manual, one-time, performed by a project owner)
   - 4.1. **Create cleanup bot account.** Project Settings → Access Tokens → new token with role `Maintainer` and scope `write_repository`. Rename the auto-provisioned `project_<id>_bot_<random>` user (e.g., `metadata-db-cleanup-bot`) and avatar it.
   - 4.2. **Store the token as a CI/CD variable.** Project Settings → CI/CD → Variables → add `CLEANUP_BOT_TOKEN`. Mark **masked** and **protected** so it's only available on protected branches.
   - 4.3. **Store Postgres credentials as CI/CD variables.** Add `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD` for the single CI Postgres role (DML on main tables + INSERT on `*_hstry` + SELECT on `schema_versions`). Mark each **masked** and **protected**. One credential set is shared across all three Postgres-using jobs.
   - 4.4. **Set `main` branch protection — two distinct settings.** Project Settings → Repository → Protected Branches → edit `main`. These dropdowns do different jobs; do not conflate them:
       - 4.4.1. **Allowed to push** → the cleanup-bot user **only**. This is the bot's direct-push right for the auto-revert; no human (including maintainers) may push directly, so every human change goes through an MR.
       - 4.4.2. **Allowed to merge** → **Maintainers** only. Data experts are `Developer` (least-privilege — they author and open MRs but do not merge); a maintainer reviews and merges. This is a *merge* restriction only — *push* is separately bot-only (4.4.1); do not set merge to bot-only or no MR could ever land.
       - 4.4.3. **Allowed to force push** → off.
       - 4.4.4. **Decision (Free/CE): maintainer-as-gate.** Merge is restricted to Maintainers (4.4.2) because CODEOWNERS is advisory-only on Free/CE — it requests reviewers but cannot block merging (Key Decision #2). With self-merge disabled, the maintainer's review-and-merge *is* the enforced human review gate; a data expert cannot land an unreviewed MR. Revisit only if the project moves to Premium/Ultimate or adds the custom MR-pipeline approval-gate job — either would let **Allowed to merge** open to `Developer` *with* enforced CODEOWNERS approval, enabling author self-merge without losing the review gate.
   - 4.5. **Free/CE merge serialization** (three settings combined):
       - 4.5.1. Merge requests → require **"Pipelines must succeed"** before merging
       - 4.5.2. Merge requests → enable **"Merged results pipelines"** (pipeline runs against post-merge state of `main`, so a queued MR's pipeline serializes against any in-flight one)
       - 4.5.3. Branch protection on `main` (already set in 4.4) backs this up — only the bot may push directly
   - 4.6. **Disable "Delete source branch when merge request is accepted."** Project Settings → Merge Requests → uncheck the default. Per maintenance doc: a failed loader run reverts `main` but the work still needs to land; keeping the source branch lets the author push fixes onto it.
   - 4.7. **Add a 1-year calendar reminder to rotate `CLEANUP_BOT_TOKEN`.** GitLab project access tokens expire at 1 year max; rotation is "create new token, swap the CI variable, delete old token."

5. End-to-end pipeline verification (after Tasks 1–4 land and the prerequisite DB roles activity completes)
   - 5.1. Open a trivial MR (e.g., add a single inline comment to an existing YAML file). Verify both pre-merge jobs (`validate_metadata_db`, `check_schema_in_sync`) run and succeed.
   - 5.2. Open an MR that breaks validation (e.g., reference a non-existent `table_id` in a `column_mappings` row). Verify `validate_metadata_db` fails and the MR is blocked from merging.
   - 5.3. Open an MR that introduces a real change (add one row). Merge it. Verify `load_metadata_db` runs post-merge, succeeds, and lands the row in Postgres. Verify `revert_failed_load` does NOT run.
   - 5.4. Stage a controlled failure **in a sandbox clone of the project**, never against the live metadata_db project. Trigger the failure by temporarily setting the sandbox's `POSTGRES_PASSWORD` CI/CD variable to an invalid value (reversible by one string edit), then merge an MR that touches `data/systems/**/*.yaml`. Verify: `load_metadata_db` fails (auth error from psycopg2), `revert_failed_load` runs, and the merge commit is reverted on `main`. Restore the password afterwards. Do NOT exercise this scenario by manipulating DB privileges, dropping tables, or otherwise perturbing the database — the goal is to exercise the CI failure path, not to mutate state that's hard to roll back.
   - 5.5. Verify merge serialization: queue two MRs in quick succession. Verify the second cannot merge until the first MR's full post-merge pipeline (including any revert) has completed.

## Key Data Decisions and Considerations

1. **Free/CE serialization is best-effort, not strict** — the Pipelines-must-succeed + Merged-results pipelines combo disables the second MR's "Merge" button while the first MR's post-merge pipeline runs, but does not prevent a human from clicking through edge cases (e.g., a force-pushed protected branch override). For a low-traffic repo this friction is acceptable; the design assumes humans aren't trying to race the pipeline. If traffic grows, the upgrade path is GitLab Premium + Merge Trains.
2. **CODEOWNERS is documentation-only enforcement on Free/CE** — listed reviewers are auto-requested but no approval is required to merge. The "both source-team and target-team must approve mapping changes" rule is aspirational on this tier. The Ultimate-tier upgrade path (CODEOWNERS Sections) would enforce it; the Free/CE fallback path (custom MR-pipeline job that reads an owners file and queries the API) is intentionally NOT in scope here — defer until and unless the gap matters.
3. **DDL never runs from CI** — `apply_ddl.py` is a maintainer-run script. The post-merge pipeline only ever invokes the DML loader. This separation — maintainer-only DDL role, CI-only DML role — is the load-bearing privilege boundary in the design.
4. **One CI Postgres role, not three** — the original maintenance-doc sketch split CI into three accounts (loader DML, dry-run SELECT, schema-check SELECT). Collapsed here to a single CI role with: DML on main tables, INSERT on `*_hstry`, SELECT on `schema_versions`, SELECT on main tables. Reasons: (a) dry-run protection is already enforced in code (`if dry_run: return` in `load_metadata_db.run`), not auth — the SELECT-only validator role was belt-and-suspenders for a code path that already can't write. (b) The three-role split's safety depended entirely on CI variable scoping being correct, since the scripts read one set of `POSTGRES_*` env vars; getting the scoping wrong silently handed a job a more-privileged role than intended. (c) Three credential sets means three rotation cycles and three places to misconfigure. (d) Postgres grants would need to be updated in lockstep with every schema migration. The collapse preserves the only load-bearing boundary (CI vs. maintainer/DDL) and removes operational drag. Upgrade path: if a future need surfaces (e.g., a non-CI service that reads metadata_db needs its own read-only role), add it then.
5. **Cleanup-bot authority is bounded by three things, all required together** — (a) `revert_merge.py` only ever pushes a `git revert -m 1 <sha>` of the immediately preceding merge commit, (b) branch protection lets only the bot push to `main`, (c) merge serialization guarantees no other commit lands between the failed load and the revert. Any one of those failing leaves the others as defense in depth.
6. **`changes:` rule on the load job skips no-op runs** — commits to `main` that don't touch `data/systems/**/*.yaml` (docs, CI changes, loader edits) skip the load entirely. The pre-merge dry-run still runs on every MR regardless of which paths changed, so loader-code changes still get exercised before merge.
7. **Diff-preview job is out of scope** — the maintenance doc mentions an optional pre-merge "show what rows would be added/updated/deleted" job. Useful for high-impact changes but adds another ephemeral-DB dependency. Defer until a real need surfaces.
