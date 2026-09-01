---
name: 20260729v01_add_ref_code_tables
goal: Give curated code sets (claim type codes, Type of Bill components, status codes) a structured, queryable home for context retrieval — a `ref` schema of real typed tables hosted in the metadata_db Postgres, seeded from git-versioned CSVs, documented in the catalog as an ordinary data source, and linked from consuming columns via a new nullable `ref_table_id` pointer on `columns`. The values exist for context (human and LLM readers resolving what a code means), not for runtime joins: no relationships, mappings, or venue materialization beyond truthfully recording where the tables live. First set backfilled: `clm_type_cd` from the source data dictionary.
created: 2026-07-29 11:18:44
updated: 2026-07-29 12:52:51
---

## Implementation Plan

### Phase 1 — Catalog model change: the `ref_table` pointer

1. [completed] Add `ref_table_id` to the columns model in the initial schema - `code/apply_ddl/ddl/0001_initial_schema.sql`
   - 1.1. `columns.ref_table_id`: nullable ltree, FK to `tables(table_id)` (NO ACTION, like the other backstop FKs), with a btree FK index and the matching mirror column on `columns_hstry` (no constraints on the mirror, per the hstry convention)
   - 1.2. Comment the column: the pointer names the documented table that enumerates this column's value domain, for context retrieval — it implies no join path and carries no co-deployment semantics
   - 1.3. Pre-launch bootstrap exception: edit 0001 directly; the Task 9 rebuild re-checksums it

2. [completed] Extend the row model - `code/load_metadata_db/data_model.py`
   - 2.1. `ColumnRow` gains `ref_table_id: str | None`; content-column ordering updated wherever the dataclass/SELECT alignment requires

3. [completed] Accept the authored key in wave 1 - `code/load_metadata_db/corpus_assembly.py`
   - 3.1. `columns.yaml` rows accept optional `ref_table` (string or null; recognized-keys and non-blank-string checks per the freeform-field conventions); the value must be a 3-segment dotted table id, checked for shape in wave 1

4. [completed] Resolve the reference in wave 2 - `code/load_metadata_db/corpus_validation.py`
   - 4.1. New check: every non-null `ref_table_id` must resolve to a documented table; the error message includes near-match hints (reuse the case-mismatch hint pattern from `_check_references`)
   - 4.2. Cross-source references are expected (ocs columns pointing at ref tables) — no co-deployment or linkability rule applies

5. [completed] Read/write the new column - `code/load_metadata_db/db_io.py`
   - 5.1. SELECT/INSERT/UPDATE column lists and `_hstry` mirrors include `ref_table_id`; positional alignment with the dataclass asserted by the existing agreement tests

6. [completed] Create and run tests for the wave-1 changes - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
   - 6.1. `ref_table` accepted (string/null), rejected when blank, non-string, or not a 3-segment dotted id; absent key defaults null
   - 6.2. Run with `uv run pytest code/load_metadata_db/unit_tests/test_corpus_assembly.py -v`

7. [completed] Create and run tests for the wave-2 check - `code/load_metadata_db/unit_tests/test_corpus_validation.py`
   - 7.1. Unresolvable `ref_table_id` produces the issue with a near-match hint; a resolvable cross-source reference passes
   - 7.2. Run with `uv run pytest code/load_metadata_db/unit_tests/test_corpus_validation.py -v`

8. [completed] Create and run tests for the write path - `code/load_metadata_db/unit_tests/test_db_io.py`
   - 8.1. Column-order agreement tests extended for `ref_table_id`; diff treats it as a content column (a link edit is an update requiring `update_reason` once loaded)
   - 8.2. Run with `uv run pytest code/load_metadata_db/unit_tests/test_db_io.py -v`

9. [completed] Rebuild the local database against the edited 0001 and re-grant (run) — maintainer-run, out-of-band (2026-07-29: `DROP SCHEMA prod CASCADE`, 0001 re-applied and re-checksummed, both grant scripts run; loader deliberately NOT run per the held-load ordering)
   - 9.1. Schema-scoped rebuild per the maintenance runbook (`DROP SCHEMA prod CASCADE`, apply, both grant scripts) — but do NOT run the corpus loader: the held first load stays held until Phase 3's links and docs land (Task 23), so every row — including Task 18's `ref_table` links and Task 19's concept slimming — loads as an insert with `update_reason: null` (see Decisions #15)
   - **Left `[pending]` for the maintainer (2026-07-29):** requires the `metadata_db_maintainer` role (`DROP SCHEMA prod CASCADE` + re-grant), destructive and out-of-band per the runbook. Code side is ready: the edited 0001 carries `columns.ref_table_id` (FK + btree index + column comment + `columns_hstry` mirror), and the full unit suite is green against it (972 passed).

### Phase 2 — The ref pipeline (schema, data, loader)

10. [completed] Create the ref schema DDL under a second apply_ddl config - `code/ref_tables/ddl/0001_ref_initial.sql`
    - 10.1. First migration creates `clm_type_cd` (code text primary key, description text not null, pgm_family text, stream text, notes text, effective date, obsolete date — final column set decided during implementation from the source dictionary sheet's attributes) with COMMENT ON statements
    - 10.2. Companion config `code/apply_ddl/config/apply_ddl_ref.toml`: `ddl_dir = "code/ref_tables/ddl"`, `database = "metadata_db"`, `schema = "ref"` — apply_ddl needs no code change (it is schema-agnostic); migrations/checksums/sync-check come for free (apply_ddl auto-creates an independent `ref.ddl_versions` ledger)
    - 10.3. The migration also creates `ref_load_audit` — the ref analogue of `load_audit`, deliberately thinner: one append-only row per table per loader run (`table_name`, `csv_sha256`, `row_count`, `loaded_ts` default now()), with a COMMENT stating its purpose (freshness detection — no per-row lineage, git is the row history)

11. [completed] Seed the first CSV - `ref_data/clm_type_cd.csv`
    - 11.1. Generated once from the source dictionary's `CLM_TYPE_CD Desc` sheet (values + descriptions + section-derived stream/family attributes) by a one-shot scratchpad script, then committed; the CSV is thereafter the hand-maintained source of truth (same contract as bootstrapped corpus YAML)
    - 11.2. Header row must match the DDL column names exactly

12. [completed] Create the ref data loader - `code/ref_tables/load_ref_data.py`
    - 12.1. For each `ref_data/*.csv`: validate against the live table (filename resolves to a table in the `ref` schema; header equals the table's columns; PK uniqueness; values parse to the column types; row count under the configured `max_rows_per_table` guardrail — a config knob, default 1,000, never a hardcoded literal), then truncate-and-reload every table in one transaction
    - 12.2. Consistency gate: the documented corpus columns for each ref table (from `data/sources/ref/`) must equal the CSV header/DDL columns — ref is the one source where docs-vs-reality drift is mechanically preventable, so it fails loudly here
    - 12.3. No diffing, no row history: git/MR review of the CSVs is the change discipline (stated in the header docstring)
    - 12.4. Config `code/ref_tables/config/load_ref_data.toml`: csv dir, database, schema, `max_rows_per_table`; connection via the standard `.env` POSTGRES_* pattern, maintainer credentials
    - 12.5. Each run appends one `ref_load_audit` row per loaded table (CSV content hash, row count, loaded_ts) inside the load transaction
    - 12.6. `--check` mode (freshness drift detection, mirroring `apply_ddl --check`): for each `ref_data/*.csv`, compare its content hash against the latest `ref_load_audit` row; exit non-zero naming any table that is stale or never loaded; read-only, writes nothing
    - 12.7. `--dry-run` mode (validate-only, mirroring the corpus loader): run every 12.1/12.2 validation and stop before the truncate — read-only, runnable under the RO role; this is what the pre-merge CI job invokes so a malformed CSV cannot merge

13. [completed] Create and run tests for the ref loader - `code/ref_tables/unit_tests/test_load_ref_data.py`
    - 13.1. Fixtures: header mismatch, duplicate PK, unparseable value, row-count guardrail (from config, not a literal), docs-vs-CSV drift, happy path incl. the audit-row write, and `--check` verdicts (current, stale, never-loaded) (mock or throwaway-schema pattern per the integration conventions)
    - 13.2. Run with `uv run pytest code/ref_tables/unit_tests/ -v`

14. [completed] Grant read access on the ref schema - `code/apply_ddl/grant_ref_schema.sql`
    - 14.1. Maintainer-run psql script granting USAGE on `ref` and SELECT on its tables (including `ref_load_audit`) to `metadata_db_ci`, `metadata_db_ci_ro`, and `mcp_ro_metadata`; writes stay with the maintainer role; same `-v` conventions as the existing grant scripts

15. [completed] Apply the ref DDL, load the CSV, run grants (run) — maintainer-run, out-of-band (2026-07-29: `ref.ddl_versions` ledger created with 0001_ref_initial applied; 317 rows in `ref.clm_type_cd`; `ref_load_audit` row written with the CSV hash; read grants applied)
    - 15.1. `uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl_ref.toml`, then the ref loader, then the grant script; verify `select count(*) from ref.clm_type_cd` matches the CSV
    - **Left `[pending]` for the maintainer (2026-07-29):** requires the `metadata_db_maintainer` role. Everything it consumes is ready: the ref migration, `ref_data/clm_type_cd.csv` (317 codes), the loader (51 unit tests green), and `grant_ref_schema.sql`.

### Phase 3 — Catalog documentation and linking

16. [completed] Register the hosting venue - `data/systems.yaml`
    - 16.1. Add `metadata_db`: the catalog's own Postgres instance, hosting the `ref` reference schema alongside the catalog

17. [completed] Document the ref source - `data/sources/ref/`
    - 17.1. `data_source.yaml` (owner = the ref steward team, description stating the values-are-owned-here responsibility), schema `codes` with `schema.yaml`, `tables.yaml` (clm_type_cd + description and provenance), `columns.yaml` documenting each typed column
    - 17.2. `deployments.yaml`: system `metadata_db` with `database_name: metadata_db` and `schemas: {codes: ref}` — the documented labels (`ref.codes`) map to the physical address (`metadata_db.ref`) via the standard rename knobs
    - 17.3. Hand-authored (a handful of small files); no generator

18. [completed] Link the consuming columns (scripted one-shot edit) - `data/sources/edwc_prd/` and `data/sources/ocs/`
    - 18.1. Set `ref_table: ref.codes.clm_type_cd` on every edwc_prd column named `clm_type_cd` and on each ocs `clm.clm_type` column (scripted line-targeted edit in the style of the description-enrichment passes; structural verification that only the intended rows changed)
    - 18.2. Still pre-first-load (the held load runs at Task 23, after these links — see Decisions #15), so no `update_reason` is required on any linked row

19. [completed] Slim the claim_type_code concept - `data/sources/edwc_prd/claims_vw_prd/concepts.yaml`
    - 19.1. The definition keeps the family/encoding teaching; the 76-line notes enumeration is replaced by a pointer to `ref.codes.clm_type_cd` (concepts teach, ref tables enumerate); `related_object_ids` gains the ref table id

20. [completed] Update the maintenance doc - `readme/metadata-db-maintenance.md`
    - 20.1. The ref architecture (ref_data/ = the data, data/sources/ref/ = the metadata), the add-a-new-ref-table runbook (check the schema's table list -> CSV -> ref migration -> corpus docs -> consumer links -> MR -> maintainer applies+loads), the two-config apply_ddl usage, grant script, and the values-ownership note; CODEOWNERS sketch lines routing `ref_data/` and `data/sources/ref/` to the steward
    - 20.2. State that ref sits outside the auto-revert perimeter by construction: `ref_data/` is outside `data/`, so CSV MRs never create a load job and never engage `revert_failed_load`; a failed manual ref load rolls back its transaction and is recovered via the manual runbook (fix forward or hand-revert), mirroring the manual corpus-load stance

### Phase 4 — Gates, final docs, held load, and dormant CI

21. [completed] Validate the assembled corpus (run) — (2026-07-29: dry-run against the rebuilt empty schema — ~26,000 inserts / 0 / 0, zero issues)
    - 21.1. Loader dry-run gate against the rebuilt (empty) prod schema: assembly, wave-2 `ref_table` resolution, and all existing rules pass with a clean, insert-only diff and zero issues — with the load held there is no update path and no guard to trip (the original draft's parenthetical expecting the sandbox mass-delete guard reflected a stale DB state and is superseded; see Decisions #15)
    - **Partially run; DB-side gate left `[pending]` on Task 9 (2026-07-29):** the front half (discover → assemble → validate, including the 118 `ref_table` link resolutions, the ref source docs, and the slimmed concept) was run against the full `data/` corpus and passes with zero issues. The dry-run's diff/update_reason wave needs the rebuilt DB (the current schema predates `columns.ref_table_id`, so `read_db_state`'s SELECT would fail) — run it after Task 9's rebuild.

22. [completed] Update the overview doc - `readme/metadata-db-overview.md`
    - 22.1. §4 `columns` table: add the `ref_table_id` row — nullable ltree FK to `tables.table_id`, naming the documented table that enumerates this column's value domain; context retrieval only, no join-path or co-deployment semantics; state explicitly that the link is a reviewed assertion — the loader verifies the target table exists, never that the column's actual values fall within the enumerated codes (the catalog never touches instance data)
    - 22.2. §5: wave-1 rule addition (optional `ref_table`, non-blank string, 3-segment dotted shape) and wave-2 rule addition (must resolve to a documented table, near-match hints); DB-level backstops gain the new FK and its btree index
    - 22.3. Brief §3-level note on the ref pattern (curated code sets live as real tables in the metadata_db instance's `ref` schema, documented as an ordinary source; the maintenance doc carries the runbook)

23. [completed] Run the held first load and verify (run) — maintainer-run, out-of-band (2026-07-29: load_id 1, ~26,000 inserts / 0 / 0; per-table counts verified — ~1,000 tables, ~22,800 columns, ~1,300 mappings, 139 relationships, 18 concepts, ~1,000 deployment rows; 118 columns carry `ref_table_id = 'ref.codes.clm_type_cd'` in the DB)
    - 23.1. After Tasks 9–22 land, run the loader for real: a single insert-only load landing the enriched edwc_prd corpus, the ocs source, the ref source docs, and every `ref_table` link in one pass; verify `load_audit` and per-source counts
    - **Left `[pending]` for the maintainer (2026-07-29):** runs after Task 9's rebuild and Task 21's DB-side dry-run gate. The corpus it will load already validates cleanly front-half (zero issues; 118 `ref_table` links, all `update_reason: null`).

24. [completed] Extend the dormant CI config for the ref stream - `.gitlab-ci.yml`
    - 24.1. `check_schema_in_sync` runs `apply_ddl.py --check` twice — once per config (prod and ref) — since each `ddl_versions` ledger only knows its own migration stream; optionally a `load_ref_data.py --check` freshness line in the same job. The MR-diff `--allow-pending` computation must be stream-aware: newly added files under `code/apply_ddl/ddl/` feed the prod check, `code/ref_tables/ddl/` the ref check — otherwise a ref-migration MR can never pass its own pipeline
    - 24.2. New dormant MR job `validate_ref_data`: on `ref_data/**` changes, run `load_ref_data.py --dry-run` (validate-only, RO role) so a malformed CSV fails pre-merge like malformed corpus YAML
    - 24.3. Activation-checklist additions: run `grant_ref_schema.sql` after ref DDL applies; record that ref loading is maintainer-manual — `ref_data/**` changes do NOT trigger an automatic load job, mirroring the manual-DDL stance (values change rarely and deserve chosen timing), with `--check` surfacing a forgotten load; state that from activation onward `0001_initial_schema.sql` (and `0001_ref_initial.sql`) are immutable — the pre-launch edit-and-rebuild exception is incompatible with an active checksum gate, so all subsequent schema changes go in numbered migrations; state that activation also ends the "held load" pattern — with CI active, every `data/**` merge loads or auto-reverts immediately, so migration+rebuild sequences must complete before dependent data MRs merge (`check_schema_in_sync` enforces the ordering); and note the parked item that the env-gated integration suite could run in CI via a Postgres `services:` container (revisit at activation, not built dormant)

## Key Data Decisions and Considerations

1. **Purpose is context retrieval, not computation** — The ref tables exist so a consumer (human or LLM) resolving a column can read its value domain. They are deliberately NOT wired for runtime joins: no `table_relationships` to consumers, no `column_mappings`, no materialization into analytic venues, and therefore none of the co-deployment machinery. If a runtime-joinable copy is ever wanted in a venue, that is a separate decision.
2. **Separate typed tables over a generic code_sets/code_values model** — Maintainer decision (2026-07-29): real code sets carry heterogeneous attributes (claim types have program family and stream; other sets will differ), which a generic value table flattens into prose. One table per set keeps attributes typed, and the catalog documents them with existing machinery — the only metadata-model change is the `ref_table_id` pointer.
3. **`ref_table` naming** — The pointer is `ref_table`/`ref_table_id` (not `code_table`): it matches the schema name and does not presume every referenced table is a code list.
4. **CSV per table; git is the history** — `ref_data/<table>.csv` (header = DDL columns) is the source of truth, reviewable in MR diffs and editable by non-engineers. The ref loader is deliberately dumb (validate, truncate, reload, one transaction); MR review replaces `update_reason`, and git history replaces `_hstry`. This trade is stated in the loader docstring and maintenance doc.
5. **`ref_data/` lives outside `data/`** — `data/` is the corpus root (metadata about the world); `ref_data/` is the one piece of the world the repo itself hosts. Keeping them separate preserves the corpus tree's meaning and keeps the YAML discovery walk irrelevant to ref data.
6. **apply_ddl reuse via a second config** — The ref schema gets numbered migrations, checksums, and the sync check with zero new DDL tooling, because apply_ddl is already schema-agnostic. The ref DDL dir lives under `code/ref_tables/` beside the loader so the ref pipeline is one module.
7. **Docs-vs-reality drift is mechanically prevented for ref only** — For every other source the catalog can drift from the remote system; for ref we control both ends, so the loader's consistency gate (corpus columns == CSV header == DDL) makes the catalog guaranteed-accurate for this source. This is the strongest accuracy statement in the catalog and worth the small check.
8. **Deployment truthfulness** — The ref tables are deployed to exactly one venue: the metadata_db Postgres itself, registered as a system. The documented-vs-physical name mapping (`ref.codes` -> `metadata_db.ref`) exercises the standard rename knobs rather than inventing anything.
9. **Linking policy: every carrier of the value** — `ref_table` is set on every column that holds the set's values (all edwc_prd `clm_type_cd` columns, not just the "main" one), because context lookup should work from whichever column a consumer lands on. Scripted, verified, and cheap while everything is pre-first-load.
10. **Curated sets only, enforced** — The ref loader's `max_rows_per_table` guardrail (config knob, default 1,000) blocks open-ended domains (NDC, ICD) from being smuggled in as "code sets"; those remain data documented the ordinary way.
11. **Discoverability** — One schema's table list is the registry; within-schema name uniqueness is automatic. The maintenance runbook's first step ("check the ref table list") plus wave-2 near-match hints on bad `ref_table` references cover the rest; the elaborate cross-corpus uniqueness rules from earlier design discussion became unnecessary under this shape.
12. **First set and future sets** — `clm_type_cd` (from the source dictionary sheet) is the backfill because it is the most load-bearing code set and its concept currently carries the enumeration as prose. Obvious next candidates, deliberately out of scope here: the Type of Bill component sets, `clm_adj_type_cd`, bene/provider status codes, and the OCS layouts' TABLE OF CODES appendices (a rich future source).
13. **Concept/description cleanup policy** — Concepts teach; ref tables enumerate. The claim_type_code concept sheds its enumeration (Task 19). Column descriptions that embed "1 = ..." value prose are left as-is for now (they came verbatim from source dictionaries); a future pass may slim ones whose column gains a `ref_table` link.
14. **Ordering** — Tasks 1-8 (model change + tests) must precede Task 9 (rebuild); Phase 2 can run in parallel with Phase 1 (the `ref` schema is untouched by the prod-scoped rebuild, so Task 15 is independent of Task 9); Task 18's links and Task 21's gate only validate after the model change and Task 17's docs land; Task 19 depends on Task 17; Task 23 (the held first load) runs last, after Task 21's clean gate. Suggested MR split: MR-1 = Phase 1, MR-2 = Phase 2 + Task 24 (the CI extension rides with the ref pipeline it describes), MR-3 = Phase 3 + Phase 4's Tasks 21-22 (Task 23 is a post-merge run, not MR content).
15. **Ordering correction (2026-07-29 review)** — As originally drafted, Task 9's rebuild included the corpus reload while Task 18 claimed its links were "pre-first-load" — contradictory: a Task 9 reload would make Task 18's links (and Task 19's concept edit) updates requiring `update_reason` under rule 20. Corrected: Task 9 applies schema + grants only; the single held first load moved to Task 23, after the links, the concept slimming, and the Task 21 gate — the entire corpus (edwc_prd enrichment, ocs, ref docs, links) lands as inserts with `update_reason: null`. Task 21's stale parenthetical (expecting the sandbox mass-delete guard, which the DB had already applied at load 3) was superseded in the same pass, and Task 22 was added — the overview doc must document the new column and rules (§4/§5), an omission relative to repo convention for model changes.
16. **Freshness amendments (2026-07-29 review)** — Ref was the one part of the system with no "main ahead of DB" detection, despite being the source that claims mechanical accuracy (Decision #7). Added: `ref_load_audit` in the first ref migration (Task 10.3 — append-only per-table run rows with CSV content hash; deliberately thinner than prod's `load_audit`, no per-row lineage, git is the row history), the loader's audit write (12.5) and `--check` freshness mode (12.6), and the dormant-CI extension (Task 24: `check_schema_in_sync` runs both apply_ddl configs since each `ddl_versions` ledger only knows its own stream, plus optional ref freshness check). Also per standing maintainer direction against hardcoded limits, the curated-set guardrail became the `max_rows_per_table` config knob (12.1/12.4). Ref loading stays maintainer-manual — no automatic load job on `ref_data/**` changes, mirroring the manual-DDL stance; `--check` is what surfaces a forgotten load. A second CI pass (same review) added the ref loader's `--dry-run` validate-only mode + the dormant `validate_ref_data` MR job (12.7/24.2 — pre-merge gates are what keep `main` loadable; the 2026-07-29 enrichment incident is the cautionary example), the stream-aware `--allow-pending` requirement (24.1), and two activation-checklist truths (24.3): activation freezes the 0001 files (the pre-launch edit-and-rebuild exception cannot coexist with an active checksum gate), and the integration-suite-in-CI idea stays parked until a runner exists.
17. **Implementation decisions (2026-07-29 build)** — (a) **`clm_type_cd` final column set** (per 10.1's "decided during implementation"): `code text PK, description text not null, pgm_family text, stream text, notes text`. The candidate effective/obsolete date columns were dropped — the source dictionary's `CLM_TYPE_CD Desc` sheet carries no dating, and backfilled guesses would be worse than absence (add them in a later ref migration if a dated source appears). `pgm_family`/`stream` are nullable because code 0 ("No Description Available") precedes every section heading and belongs to no family. The CSV holds 317 codes across 18 sheet sections. (b) **Link count is 112 + 6, not 113 + 6**: the worklist's 113 was a substring grep artifact — one match is `clm_type_cd_desc` (a description column, not a carrier of code values), correctly left unlinked; all 112 edwc_prd columns named exactly `clm_type_cd` plus the six ocs `clm.clm_type` columns are linked (118 total, verified structurally by the one-shot script and resolved in wave 2). (c) **The optional ref-freshness line in `check_schema_in_sync` (24.1) was deliberately NOT wired**: an MR editing a ref CSV would always see the DB "stale" against its own change (ref loading is post-merge and manual), deadlocking its own pipeline with no `--allow-pending` analogue — freshness stays a maintainer-run `--check` (rationale recorded in the CI file; revisit a `changes:`-guarded variant at activation). (d) **`ref_table_id` dataclass placement**: on `ColumnRow` it sits last as a defaulted field (mirroring the documented `validated_ts` precedent) while the DDL places it after `is_primary_key`; `db_io._SELECT_COLUMNS` matches the dataclass order (positional row build), and the deviation is documented on the dataclass. It IS a content column (in `CONTENT_COLUMNS`), unlike `validated_ts`. (e) **Empty CSV cell = NULL** is the ref loader's one spelling of absent (validated against NOT NULL columns pre-write); values are inserted as text and cast by Postgres, with parse-parity checks (`_TYPE_PARSERS`) run before any write.
18. **Considered and deliberately not done (2026-07-29 review)** — (a) No example-corpus demonstration of `ref_table` yet: the example set has no natural domain-enumerating table, and a contrived link would teach the wrong semantics; revisit when the example corpus gains a ref-like table. (b) The authoring-rules doc (`readme/metadata-db-data-authoring-rules.md`, still on the unmerged `puf-data-val` branch) gains a ref-linking rule after that branch merges. (c) No concurrency lock in the ref loader: concurrent truncate-and-reload runs self-serialize through TRUNCATE's ACCESS EXCLUSIVE table locks and are idempotent for identical inputs — the corpus loader's read-then-write race does not exist here. (d) One `ref_table` per column is a known constraint — adequate for `clm_type_cd` (stream/family live as attributes in one table); revisit only if a real column's domain spans multiple sets.
