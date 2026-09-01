---
name: 20260717v01_move_to_catalog_schema
goal: Make the target Postgres **schema** a configuration knob (mirroring the existing `database` knob) and use it to relocate every metadata_db object out of `public` into a dedicated `catalog` schema, with the `ltree` extension installed there and `public` dropped entirely. The mechanism is search_path-based — SQL stays schema-unqualified and `0001` becomes schema-agnostic — so the schema name lives once in config. Schema change lands by editing `0001` and rebuilding the local Postgres (pre-launch bootstrap); apply_ddl, the loader connection, grants, tests, and docs are updated to the knob and verified end-to-end.
created: 2026-07-17 11:59:57
updated: 2026-07-17 12:05:30
implementation_note: 2026-07-17 — Phases A–G complete and verified end-to-end. Maintainer rebuild (Phase E) run: `public` dropped, everything rebuilt into `catalog` with `ltree` there; `apply_ddl --check` clean and loader `--dry-run` `0/0/0`. Integration suite (11.4) 4 passed against the rebuilt DB; full non-integration suite 350 passed / 4 skipped at 100% coverage on changed modules. Only Phase H (code review) remains.
---

## Implementation Plan

> Ordering rule: the config knob + schema-agnostic `0001` + apply_ddl schema handling + loader/grant connection changes land first (Phase A–D, unit-tested against mocks with no live DB); then the maintainer rebuild (drop `public`, create `catalog`) is the synchronization point (Phase E); then the integration test runs live (Phase F); docs and review close it out. Suggested PR split: PR-A = the schema-agnostic `0001` + config (applied at the Phase E rebuild); PR-B = apply_ddl/loader/grant code, tests, docs.
>
> Mechanism: **set `search_path` to the configured schema on every connection and keep all SQL schema-unqualified.** This removes the hardcoded `public` references rather than swapping them for hardcoded `catalog`, so the schema name is defined once (config) and `0001` is reusable for any schema. See Key Decision 1.
>
> Not in scope / not changed: the **corpus YAML** and any `.public.` segments inside cataloged IDs (e.g. `warehouse.OCS.public.*`) — those name *cataloged user schemas* (rows in the `schemas` table), not the Postgres namespace the metadata_db lives in. No `data/` or example-YAML edits.

### Phase A — Config knob + schema-agnostic DDL

1. [completed] Add a `schema` key to both entry-point configs - `code/apply_ddl/config/apply_ddl.toml`, `code/load_metadata_db/config/load_metadata_db.toml`
   - 1.1. Add `schema = "catalog"` to each (alongside the existing `database`), with a comment noting it is the Postgres schema all metadata_db objects live in and is applied via `search_path`
   - 1.2. Keep `database` as-is; the two knobs are now symmetric

2. [completed] Make the base schema fully schema-agnostic - `code/apply_ddl/ddl/0001_initial_schema.sql`
   - 2.1. Keep `create extension if not exists ltree;` unqualified — with `search_path` set to the target schema (Task 3) the extension installs *into that schema*, so its `ltree` type and `gist__ltree_ops` operator class resolve for the column definitions and GiST indexes without a `public` dependency
   - 2.2. Remove every `public.` qualifier from the `comment on table public.<t>` statements → unqualified `comment on table <t>` (resolves via `search_path`); the 14 tables + `ddl_versions`/`load_audit` comments
   - 2.3. Drop the `comment on schema public is …` statement — a schema-level comment cannot be schema-agnostic; move its description text into the maintenance doc (Task 13) or have apply_ddl set it (Key Decision 7). No schema-name literal may remain in `0001`
   - 2.4. Leave all `create table` / `create index` statements unqualified (they already are — they land in the `search_path` schema); confirm no other `public`/schema literal remains
   - 2.5. Pre-launch bootstrap: edits an already-applied migration; permitted while pre-launch (only `sandbox.*`, reproducible from YAML — precedent: `enable_cross_source_mappings` KD #5, `design_hardening` KD #6). The Phase E rebuild re-applies `0001` fresh; no `0002`

### Phase B — apply_ddl schema handling

3. [completed] Read the `schema` config and create/target it via search_path - `code/apply_ddl/apply_ddl.py`
   - 3.1. `connection_kwargs`: add a `schema` parameter and set `options=f"-c search_path={schema}"` on the returned kwargs so every statement resolves to that schema (setting search_path to a not-yet-existing schema at connect time is harmless — it is evaluated per-statement)
   - 3.2. `run`: read `config["schema"]`; after connecting (and before `ensure_ddl_versions`), execute `create schema if not exists <schema>` (via `psycopg2.sql.Identifier`) and commit, so `ddl_versions` and all migration objects land in that schema
   - 3.3. `create_database_if_absent` connects to the `postgres` maintenance DB — unaffected by the schema option; leave its logic, just carry the `schema` through `connection_kwargs`
   - 3.4. Keep `ensure_ddl_versions` / `ddl_versions_exists` / `apply_one` SQL unqualified — they now resolve to `<schema>` via `search_path`; no per-statement qualification
   - 3.5. `--check` stays read-only: it must not create the schema (a `create schema if not exists` is a write). If the schema is absent, treat it like an absent `ddl_versions` (nothing applied → migrations pending) — guard the create behind `not check_only`

4. [completed] Update + run tests for apply_ddl - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 4.1. Assert `connection_kwargs(db, schema)` includes `options` with `-c search_path=<schema>`
   - 4.2. Assert `run` (non-check) issues `create schema if not exists <schema>` before the `ddl_versions` create; assert `--check` does NOT issue a `create schema`
   - 4.3. Assert a missing `schema` config key raises the existing `KeyError` path (config-field validation)
   - 4.4. Keep the checksum/immutability/append-only cases green; run `uv run pytest code/apply_ddl/unit_tests/test_apply_ddl.py -v --cov=apply_ddl --cov-report=term-missing`

### Phase C — Loader connection

5. [completed] Thread the schema into the loader's connection - `code/load_metadata_db/db_io.py`
   - 5.1. `connection_kwargs`: add a `schema` parameter and set `options=f"-c search_path={schema}"` (mirror of Task 3.1); all `read_db_state` / `apply_diff` / `_hstry` `TRUNCATE` SQL stays unqualified and now resolves to `<schema>`
   - 5.2. No SQL-statement changes — the search_path option is the only mechanism

6. [completed] Pass the configured schema at the loader entry point - `code/load_metadata_db/load_metadata_db.py`
   - 6.1. Read `config["schema"]` and pass it to `connection_kwargs(config["database"], config["schema"])`
   - 6.2. Surface a clear error if `schema` is absent from config (consistent with the existing missing-field handling)

7. [completed] Update + run tests for db_io - `code/load_metadata_db/unit_tests/test_db_io.py`
   - 7.1. Assert `connection_kwargs(db, schema)` includes `options` with `-c search_path=<schema>`; keep the existing env-var-missing `RuntimeError` case
   - 7.2. Run with coverage

8. [completed] Update + run tests for the loader orchestrator - `code/load_metadata_db/unit_tests/test_load_metadata_db.py`
   - 8.1. Update the config fixtures to include `schema`; assert the schema is passed through to `connection_kwargs`; assert a missing `schema` key fails cleanly
   - 8.2. Run with coverage

### Phase D — Grants

9. [completed] Parameterize the grant script by schema and database - `code/apply_ddl/grant_metadata_db_ci.sql`
   - 9.1. Replace `set search_path = public;` with a psql-variable form: `\set` defaults + `set search_path = :"schema";` so the maintainer invokes with `-v schema=catalog -v database=metadata_db`
   - 9.2. Add `grant usage on schema :"schema" to metadata_db_ci;` (USAGE on the new schema is required before any table grant resolves)
   - 9.3. Parameterize `grant connect on database :"database"` (removes the hardcoded `metadata_db`), completing the db knob
   - 9.4. Keep the DML / INSERT-only-`_hstry` / `ddl_versions` / `load_audit` grant model unchanged; the unqualified table names now resolve to `:"schema"` via the search_path set at the top
   - 9.5. Document the new invocation (with `-v`) in the header comment and the maintenance doc (Task 13)

### Phase E — Rebuild, regrant, reload, verify (maintainer, out-of-band)

10. [completed] Rebuild into `catalog`, drop `public`, regrant, reload, verify - maintainer-run, out-of-band
    - DONE (2026-07-17, maintainer-run): dropped `catalog`+`public` (17 objects incl. `ltree`), re-applied the schema-agnostic `0001` into `catalog`, regranted with `-v schema=catalog -v database=metadata_db` (6 grants incl. `usage on schema`), reloaded the corpus as the CI role (clean full-insert). Verified: only `catalog` schema exists (`public` gone), `ltree` in `catalog`, 16 tables in `catalog`, `is_primary_key`/GiST present, `@>` containment works, `apply_ddl --check` clean, loader `--dry-run` `0/0/0`. Nuance surfaced: `search_path` is set per-connection via options (not persisted on the role), so an ad-hoc `psql` session must `set search_path = catalog` or qualify `catalog.*` — the app connections handle it automatically.
    - 10.1. As `metadata_db_maintainer` (needs CREATE on the database to make a schema; owns the objects): `DROP SCHEMA IF EXISTS catalog CASCADE; DROP SCHEMA IF EXISTS public CASCADE;` — removes the old tables AND `public` (and the `ltree` extension that lived there); the DB is left with no non-system schema
    - 10.2. `uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl.toml` — apply_ddl creates the `catalog` schema, sets `search_path=catalog`, re-applies the schema-agnostic `0001` (installing `ltree` into `catalog`, creating all 14 tables + `ddl_versions` there); `ddl_versions` records the new checksum
    - 10.3. Regrant as maintainer: `psql -v schema=catalog -v database=metadata_db -f code/apply_ddl/grant_metadata_db_ci.sql`
    - 10.4. Reload the corpus as the CI role: `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml` — expect a clean full-insert load (validation unchanged; the search_path lands rows in `catalog`)
    - 10.5. Verify: `public` schema is gone (`select … from information_schema.schemata`); `ltree` extension is in `catalog` (`select extnamespace::regnamespace from pg_extension where extname='ltree'`); all 16 tables in `catalog`; an `@>` containment query over `catalog.column_mappings.target_tables_referenced` works; `apply_ddl.py --check` clean; a loader `--dry-run` reports `0/0/0` (DB aligned with code)
    - 10.6. Confirm the CI role can connect and DML only via the schema (a `metadata_db_ci` probe), and still cannot DDL

### Phase F — Integration test

11. [completed] Make the integration test schema-aware - `code/load_metadata_db/unit_tests/test_integration.py` (+ `unit_tests/conftest.py` if the DB fixture lives there)
    - 11.1–11.3 DONE (code): `TEST_SCHEMA = "catalog"` added; the throwaway-DB `.pytest_ddl.toml` and the loader config now carry `schema`; all `connection_kwargs(...)` calls pass `TEST_SCHEMA`; `test_pk_agreement_and_ltree_types` queries `table_schema = TEST_SCHEMA` and asserts `public` is absent and `ltree` is installed in `catalog`. The DB fixture (in `test_integration.py`, not conftest) now also runs `DROP SCHEMA IF EXISTS public CASCADE` after apply, mirroring the production rebuild so the no-`public` assertion is valid.
    - 11.4 DONE (2026-07-17): `METADATA_DB_INTEGRATION=1 uv run pytest -m integration ...` → 4 passed against the rebuilt DB; full non-integration unit suite 350 passed / 4 skipped, 100% coverage on changed modules. (Two fixes applied during the run: the inline loader config in `test_loader_rejects_design_doc_violations` was missing `schema`; the fixture was not dropping `public`.)
    - 11.1. The throwaway-DB fixture must create/target the `catalog` schema the same way production does (via the `schema` config / search_path), so the loader and apply path exercise the real mechanism
    - 11.2. `test_pk_agreement_and_ltree_types`: query the built schema with `table_schema = 'catalog'` (not `public`); assert the 14 tables + `is_primary_key` + the GiST index exist there
    - 11.3. Add an assertion that no `public` schema is present and that `ltree` is installed in `catalog`
    - 11.4. Run `METADATA_DB_INTEGRATION=1 uv run pytest -m integration code/load_metadata_db/unit_tests/test_integration.py -v` against the rebuilt DB, then the full unit suite at the 100%-coverage bar

### Phase G — Documentation

12. [completed] Update the schema reference - `readme/metadata-db-overview.md`
    - 12.1. State that all metadata_db objects live in the `catalog` Postgres schema (not `public`), reachable via `search_path`; update any example queries that assumed `public`
    - 12.2. Note the `database` + `schema` config knobs and that the SQL is schema-unqualified by design

13. [completed] Update the maintenance/runbook doc - `readme/metadata-db-maintenance.md`
    - 13.1. Rebuild runbook: the drop-`public`-and-`catalog` + apply + regrant sequence (Task 10), including the `-v schema= -v database=` grant invocation
    - 13.2. Document the `schema` config key, the search_path mechanism, `ltree` living in `catalog`, and that `public` is intentionally absent (hardening bonus: no PUBLIC default-privilege surface)
    - 13.3. Carry the (former `comment on schema public`) description text here if it was dropped from `0001` (Task 2.3)

### Phase H — Review

14. [blocked] Code review of changed files and address findings - `docs/code_review/`
    - BLOCKED: 14.1 requires spawning `code-review-agent`, which is outside this implementation agent's scope (an agent does not spawn other agents). To be run separately once the code changes are reviewed; findings then addressed via the `code-implementation` skill (14.2) and each review marked resolved (14.3).
    - 14.1. Run `code-review-agent` against each changed code file (`0001_initial_schema.sql`, `apply_ddl.py`, `db_io.py`, `load_metadata_db.py`, `grant_metadata_db_ci.sql`), writing `cr_*.md` per the existing layout
    - 14.2. Address findings via the `code-implementation` skill; re-run the suite at the 100%-coverage bar
    - 14.3. Mark each review's Status resolved when fixes land

## Key Data Decisions and Considerations

1. **search_path mechanism, not fully-qualified names** — set `search_path` to the configured schema on each connection (psycopg2 `options='-c search_path=<schema>'`) and keep every SQL statement schema-unqualified. This is far less churn than qualifying ~40 statements in `db_io.py`/`0001`, mirrors how `database` already works, and — critically — makes `0001` *schema-agnostic* (no `public`/`catalog` literal), so the same migration builds into whatever schema the config names. Rejected: hardcoding `catalog` everywhere (swaps one magic string for another, no reuse) and per-statement qualification (invasive).

2. **`ltree` installed into `catalog`, enabling `public` to be dropped** — `create extension if not exists ltree;` unqualified installs into the current schema, which is `catalog` once `search_path` is set; its `ltree` type and `gist__ltree_ops` operator class then resolve for the ID columns and GiST indexes. This removes the last reason `public` had to exist, so the DB ends with exactly one non-system schema.

3. **`public` dropped entirely (per request)** — `DROP SCHEMA IF EXISTS public CASCADE` at rebuild. Bonus hardening: it also removes the PUBLIC role's default-privilege surface on `public` (the project already revokes CREATE there). Requires the maintainer to own/drop it; the maintainer already applies DDL so it has the needed rights — confirm CREATE-on-database for `create schema` and ownership for the drop (Task 10 verifies).

4. **Schema is a real config knob, defaulting to `catalog`** — not hardcoded. `database` is already configurable; the same files change either way; the knob kills all magic-string `public` references, centralizes the name in the two TOMLs, and lets the integration test (or future environments) isolate by schema. Low marginal cost over hardcoding, higher cleanliness.

5. **Database name fully parameterized too** — while here, remove the hardcoded `metadata_db` in `grant_metadata_db_ci.sql` (`grant connect on database`) via a psql `-v database=` variable, so both knobs are genuinely config-driven. `connection_kwargs` already takes the DB name from config; this closes the one SQL script that hardcoded it.

6. **Edit `0001` + rebuild, not a `0002` ALTER** — pre-launch bootstrap exception (only `sandbox.*`, reproducible from YAML), consistent with every prior schema change this workstream. An ALTER-based `SET SCHEMA` across 16 tables + moving the extension + dropping `public` is materially riskier than a clean rebuild. After the first real system lands, `0001` becomes immutable.

7. **`comment on schema` cannot be schema-agnostic** — it needs the literal schema name, which conflicts with a reusable `0001`. Decision: drop it from `0001` and record the schema's description in the maintenance doc; optionally have apply_ddl issue `comment on schema <schema>` from the config value. The per-table/`obj_description` comments stay (unqualified, resolve via search_path).

8. **Corpus and cataloged IDs are untouched** — the `.public.` inside IDs like `warehouse.OCS.public.bene` names a *cataloged* user schema (a `schemas` row), not the Postgres namespace being moved. No `data/`, example-YAML, or `schemas`-row changes; the `--dry-run 0/0/0` in Task 10.5 confirms the relocation changed only where the tables live, not their contents.

9. **`--check` must stay write-free** — creating the schema is a write, so it is guarded behind `not check_only`; an absent schema in check mode is treated like an absent `ddl_versions` (all migrations pending), preserving the CI drift-check's no-writes contract.

10. **Test surface** — `connection_kwargs` now returns an `options` key (unit-test assertions in `test_apply_ddl`/`test_db_io` update); `test_load_metadata_db` config fixtures gain `schema`; `test_integration` must build/target `catalog` and assert schema placement + `public` absence. No production behavior beyond placement changes, so the corpus-validation/diff/sql-parsing suites are unaffected.

11. **Implementation status / blockers (2026-07-17)** — Phases A–D (config, schema-agnostic `0001`, apply_ddl schema handling, loader connection, grant script) and Phase G (both docs) are complete; the Phase F integration-test *code* is schema-aware. Verified via the full non-integration unit suite: **350 passed, 4 integration skipped**, with `apply_ddl.py`, `db_io.py`, and `load_metadata_db.py` each at **100% coverage**. Three items are deferred as out of the implementation agent's scope:
    - **Phase E (Task 10)** — the maintainer-run, out-of-band rebuild (`DROP SCHEMA public/catalog CASCADE`, re-apply, regrant, reload, verify). Destructive DDL against a live DB with maintainer credentials; run by the maintainer per the (now updated) maintenance runbook.
    - **Task 11.4** — the live integration run needs `METADATA_DB_INTEGRATION=1` against the DB rebuilt in Phase E.
    - **Phase H (Task 14)** — spawning `code-review-agent`; run separately, then address findings via the `code-implementation` skill.
    - **`grant_metadata_db_ci.sql`** was updated to the `\if :{?var}`-defaulted `-v schema=`/`-v database=` form but not psql-executed here (no live DB); it is exercised as part of Phase E.
