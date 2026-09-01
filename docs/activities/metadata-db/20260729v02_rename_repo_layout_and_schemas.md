---
name: 20260729v02_rename_repo_layout_and_schemas
goal: Repo-wide naming refactor executed before the pending ref-tables implementation commits, so everything is born with its final names. Loaders are renamed by target schema (`load_catalog`, `load_ref_data`), all DDL consolidates under `apply_ddl` (`ddl_catalog/` + `ddl_ref/`), the Postgres catalog schema renames `prod` → `catalog` (environment name → content name), and the data roots become an adjacent family (`data/`, `data_ref/`, `data_samples/`). Historical docs keep old names as dated records; the live database is renamed in place via `ALTER SCHEMA`.
created: 2026-07-29 13:26:14
updated: 2026-07-29 14:02:27
---

## Implementation Plan

1. [completed] Rename the corpus loader module - `code/load_catalog/`
   - 1.1. `git mv code/load_metadata_db code/load_catalog`; inside it `load_metadata_db.py` → `load_catalog.py`, `config/load_metadata_db.toml` → `config/load_catalog.toml`, `unit_tests/test_load_metadata_db.py` → `unit_tests/test_load_catalog.py`
   - 1.2. Update internal references: test imports of the renamed module, the `log_dir` string (`logs/load_metadata_db` → `logs/load_catalog`), module/docstring usage examples, and every `code/load_metadata_db/...` path string in code and configs

2. [completed] Rename the ref loader module - `code/load_ref_data/`
   - 2.1. `git mv code/ref_tables code/load_ref_data` (restores the folder == script convention; the module keeps `load_ref_data.py`, `config/`, `unit_tests/` — its `ddl/` moves out in Task 3); update its `log_dir` and internal path references

3. [completed] Consolidate all DDL under the applier - `code/apply_ddl/`
   - 3.1. `git mv code/apply_ddl/ddl code/apply_ddl/ddl_catalog` and `git mv` the ref migrations to `code/apply_ddl/ddl_ref/`
   - 3.2. Configs go symmetric: `config/apply_ddl.toml` → `config/apply_ddl_catalog.toml` with `ddl_dir = "code/apply_ddl/ddl_catalog"`; `config/apply_ddl_ref.toml` gets `ddl_dir = "code/apply_ddl/ddl_ref"`
   - 3.3. Update every `--config .../apply_ddl.toml` and `ddl/` path reference in code, tests (including the static DDL-invariant and grant-agreement tests that read the migration files), configs, and live docs

4. [completed] Rename the catalog schema in configs, code, and tests - `code/load_catalog/config/load_catalog.toml` and `code/apply_ddl/config/apply_ddl_catalog.toml`
   - 4.1. `schema = "prod"` → `schema = "catalog"` in both configs; documented grant invocations become `-v schema=catalog`
   - 4.2. Sweep remaining `prod` schema references token-wise (word-boundary, reviewed hit-by-hit — `production`/`reproducible` and the `metadata_db` database/role names must not change; `METADATA_DB_*` env-var names unchanged)
   - 4.3. The static "0001 contains no schema literal" test keeps its invariant but its guard token follows the configured schema name (it must now prove no `catalog` literal appears in the DDL); confirm the integration suite's throwaway schema name remains distinct or is deliberately shared with rationale recorded in a comment

5. [completed] Rename the data roots - `data_ref/` and `data_samples/`
   - 5.1. `git mv ref_data data_ref` and `git mv sample_data data_samples`
   - 5.2. Update path references: `code/load_ref_data/config/load_ref_data.toml` (csv dir), both generators' configs (input paths under what was `sample_data/`), provenance header comments in generated corpus YAML under `data/sources/` (comments only — row content must not change), the active worklists in `docs/data_review/` (enrichment worklist, ocs worklists, description-conflicts), and `readme/` docs
   - 5.3. Sweep hazard: `load_ref_data` contains the substring `ref_data` — the `ref_data` → `data_ref` replacement must be anchored (word-boundary with a negative lookbehind for `load_`), and `ref_table`/`ref_table_id` must be untouched by any `ref_tables` path replacement

6. [completed] Update the CI config - `.gitlab-ci.yml`
   - 6.1. Job renames: `validate_metadata_db` → `validate_catalog`, `load_metadata_db` → `load_catalog` (including the revert job's `needs:`); `changes:` rules and env-var names unchanged
   - 6.2. All path/config/schema references from Tasks 1–5 (loader paths, both apply_ddl configs, `data_ref/` in the `validate_ref_data` job, activation-checklist text)

7. [completed] Update the maintenance doc - `readme/metadata-db-maintenance.md`
   - 7.1. Repo-layout tree, runbooks, grant invocations, schema-knob prose, the ref section, and CODEOWNERS sketch — all to the final names (`catalog` schema, `load_catalog`, `load_ref_data`, `ddl_catalog`/`ddl_ref`, `data_ref/`, `data_samples/`)

8. [completed] Update the overview doc - `readme/metadata-db-overview.md`
   - 8.1. §4's schema section (the dedicated Postgres schema is `catalog`; `search_path` examples), §3.7's ref note, and any loader/module path mentions

9. [completed] Verify the refactor (run)
   - 9.1. Full unit suite green (`uv run pytest code/ -q`; expected baseline 1023 passed / 17 skipped)
   - 9.2. Grep sweeps proving no stale references outside the excluded dirs (`docs/activities/`, `docs/code_review/`, past `docs/data_review/dr_*` files, `.env`, `logs/`): `load_metadata_db`, `ref_tables`, anchored `ref_data`, `sample_data`, `apply_ddl.toml`, `apply_ddl/ddl/`, and schema-`prod` tokens
   - 9.3. `.gitlab/CODEOWNERS` checked for path references needing the same updates

10. [completed] Rename the live schema and re-verify (run) — maintainer-run, out-of-band (2026-07-29: the interim `ALTER SCHEMA prod RENAME TO catalog` was superseded by the maintainer's full rebuild — both schemas dropped and rebuilt under the final names/configs; held first load = ~26,000 inserts / 0 / 0; ~1,000 tables / ~22,800 columns / 118 ref links / 317 ref codes verified; catalog --check, ref --check, and the ref freshness --check all pass)
    - 10.1. `ALTER SCHEMA prod RENAME TO catalog;` as `metadata_db_maintainer` (non-destructive: all ~26,000 loaded rows, `ddl_versions`, `load_audit`, grants, and the ltree extension move with the schema)
    - 10.2. `apply_ddl.py --check` passes for both configs (checksums unchanged; `search_path=catalog` resolves the moved `ddl_versions`)
    - 10.3. Corpus loader dry-run under the new config: expect exactly 0 inserts / 0 updates / 0 deletes — the proof that Task 5.2's provenance-comment edits are content-neutral
    - 10.4. `load_ref_data.py --check`: current (the ref schema was never renamed; its audit hashes still match)

11. [completed] Round 2 — complete the data/loader bijection - `data_catalog/` and `code/load_catalog_data/` (maintainer direction, 2026-07-29)
    - 11.1. `git mv data data_catalog` (and the example corpus's internal root, `readme/metadata-db-example-yamls/data` → `data_catalog`, so the illustration matches); every `data_X/` root now has a `load_X_data` loader filling the `X` schema
    - 11.2. `git mv code/load_catalog code/load_catalog_data` (script/config/test files renamed to match); CI jobs follow (`validate_catalog_data`, `load_catalog_data`); `data_root` config values and all path references updated

12. [completed] Round 2 — rename the grant scripts by schema - `code/apply_ddl/`
    - 12.1. `grant_metadata_db_ci.sql` → `grant_catalog_ci.sql`, `grant_metadata_db_ci_ro.sql` → `grant_catalog_ci_ro.sql`, `grant_ref_schema.sql` → `grant_ref_ro.sql` — the old names mixed role-first and schema-first conventions; now uniformly `grant_<schema>_<who/what>`

13. [completed] Round 2 — move the spent one-shot generators under `code/bootstrap/`
    - 13.1. `code/generate_corpus_from_infoschema/` and `code/generate_ocs_corpus/` → `code/bootstrap/...`, separating scaffolders (kept for provenance, never re-run over hand edits) from live pipeline modules; repo-root `parents[...]` hops and segment-wise test paths corrected for the extra nesting level

## Key Data Decisions and Considerations

1. **Loaders are named by target schema** (maintainer decision, 2026-07-29) — two loaders now write into the `metadata_db` database, so `load_metadata_db` became overbroad; `load_catalog` + `load_ref_data` name what each fills. `load_ref_data` also restores the folder == script convention that `ref_tables` broke (the repo's `code/` pattern is one folder per executable, folder named for its script).
2. **The applier owns all DDL** — catalog DDL lived with `apply_ddl` while ref DDL lived with the ref loader; the maintainer flagged the inconsistency. Resolution consolidates both streams under `apply_ddl` (`ddl_catalog/` + `ddl_ref/`, `apply_ddl_catalog.toml` + `apply_ddl_ref.toml`): the module that runs DDL owns it, and loaders own none — truthful, since neither loader ever creates schema.
3. **Schema `prod` → `catalog`** — `prod` is an environment name in a content slot; it becomes actively confusing the day a dev/staging instance exists (schema `prod` inside a dev database). `catalog` names the content; `metadata_db.catalog` + `metadata_db.ref` describe the system exactly. `ref` stays (already content-named). The rename is the schema config knob doing its designed job — the DDL is schema-agnostic and needs zero changes.
4. **Data roots become an adjacent, self-describing family** — `data/` (the corpus: metadata about the world, loader-owned), `data_ref/` (reference values the repo itself hosts, ref-loader-owned), `data_samples/` (source material: dictionaries, layouts, exports, human-owned). Motivated by the maintainer's sort-adjacency request; `data_ref` keeps the "ref" thread consistent with the schema, source label, and `data/sources/ref/`.
5. **Timing: executed pre-commit, deliberately** — the entire ref-tables implementation (activity 20260729v01) is still uncommitted, so these renames fold into the same commit and the new modules' history is born with final names; the DB was rebuilt today, so the schema rename is an in-place `ALTER SCHEMA` with grants and data surviving. This is the cheapest moment this refactor will ever have.
6. **Historical docs keep old names** — dated records (`docs/activities/*` prior to this one, `docs/code_review/*`, past data-review files) correctly describe the repo as it was; renaming inside them would falsify history. Live docs (overview, maintenance, CI, active worklists) are updated. `.env` is user-owned and untouched.
7. **Sweep hazards, enumerated** — `ref_table`/`ref_table_id` (the new columns pointer) must survive `ref_tables` path replacement; `load_ref_data` contains the substring `ref_data` (anchor the folder rename pattern); `\bprod\b` must not touch `production`/`reproducible` and every hit is reviewed individually; `metadata_db` (database, roles, env-var prefixes) is not part of any rename. The static no-schema-literal DDL test flips its guard token to `catalog` — the invariant is "the configured schema name never appears in the DDL", not the literal `prod`.
8. **Provenance comments in generated corpus YAML are edited (comments only)** — the `sample_data/` → `data_samples/` path fix touches header comments in `data/sources/edwc_prd/` and `data/sources/ocs/` files; row content is untouched, and Task 10.3's 0/0/0 dry-run is the mechanical proof.
9. **CI job renames ride along** — `validate_catalog` / `load_catalog` keep job names aligned with module names; rules, credentials, and the revert job's bounded authority are unchanged. Dormant CI means zero activation risk.
10. **Ordering** — Tasks 1–8 are one mechanical pass (any internal order); Task 9's sweeps and suite gate the whole; Task 10 (DB) runs last and only needs the maintainer for the single `ALTER SCHEMA` statement. The combined commit (ref implementation + this refactor) follows Task 10's verification.
11. **Execution deviations (2026-07-29, recorded at completion)** — (a) Task 10.3's 0/0/0 prediction was 0/3/0 at first verification: the `ref` source's own description/notes prose legitimately referenced the renamed paths — genuine content updates (the provenance-comment edits everywhere else were confirmed content-neutral, zero updates from ~100 touched YAML files). The maintainer chose a full rebuild of both schemas instead of the update path, so the three rows keep `update_reason: null` and land as inserts. (b) The sweep's comment edits inside the already-applied `0001_ref_initial.sql` tripped the ref checksum guard exactly as designed; the same rebuild re-applies the edited migration and records its current checksum natively (no ledger re-baseline needed). (c) The static schema-agnosticism test's guard could not flip to a bare `catalog` word-check — the word appears legitimately in the DDL's `COMMENT ON` string literals, which survive comment-stripping — so the invariant is now the qualifier form (`catalog.`), with the rationale in the test comment. (d) A partial first attempt by an implementation agent (twice interrupted by transient 529 API errors) had already completed the Task-1 module move; the manual pass verified and continued from that state, and the final sweeps prove convergence.
12. **Round-2 renames (maintainer direction, 2026-07-29, executed in the same pre-commit window)** — Tasks 11-13: `data/` → `data_catalog/` completes the folder↔loader↔schema bijection (`data_catalog/` → `load_catalog_data` → `catalog`; `data_ref/` → `load_ref_data` → `ref`); grant scripts renamed schema-first for one convention; the spent generators moved under `code/bootstrap/` (the code/ pattern gains one documented nesting level for one-shot scaffolders). Verification identical to round 1: full suite at the 1023/17 baseline, sweep proofs zero stale tokens (`load_catalog` bare, `validate_catalog` bare, `grant_metadata_db_*`, `grant_ref_schema`, `code/generate_*`, bare `data/`), and the two breakages the move introduced (repo-root `parents[2]` hops and segment-wise `"code" / "generate_ocs_corpus"` test paths, invisible to token sweeps) were caught by the suite and fixed. The rebuild decision (Decision #11a) stands: both schemas drop and reload fresh, so no update_reason is carried anywhere.
