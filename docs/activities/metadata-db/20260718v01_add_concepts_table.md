---
name: 20260718v01_add_concepts_table
goal: Add a `concepts` business-glossary table (plus its `concepts_hstry` mirror) to metadata_db — one row per business concept the data represents, captured as a freeform, never-parsed definition for a researcher or Claude Code to look up / RAG. Concepts are system-agnostic, so their YAML lives in a new `data/concepts/` tree with body-derived (not path-derived) `concept_id`s, requiring a new discovery/assembly branch; the rest of the loader consumes concepts through the existing shared registry. The change lands by editing `0001` and rebuilding the local Postgres (pre-launch bootstrap), then regranting, reloading a single seed concept (the *claim* definition), and verifying end-to-end.
created: 2026-07-18 13:15:57
updated: 2026-07-18 13:22:38
---

## Implementation Plan

> Ordering rule: the DDL + grants + shared registry/row-model land first (Phases 1–2), because the generic diff/read/apply machinery keys off `data_model` and the DDL PK must agree with it. Then the two genuinely new branches — discovery + assembly for `data/concepts/` (Phase 3) — and the mechanical per-table SQL in `db_io` (Phase 5), with minimal validation (Phase 4). Seed data + example YAML (Phase 6) and the unit-test suite (Phase 7, authored against mocks / no live DB) follow. The maintainer rebuild (Phase 8) is the synchronization point (drop `prod`, re-apply `0001`, regrant, reload the seed) — and only *after* it can the live integration run (`test_integration`, Task 17.3) and the full-suite green-bar execute against the rebuilt DB. Docs/CI and review (Phases 9–10) close it out.
>
> Architecture: every existing main table is wired through **one shared registry** in `data_model.py` (`TABLE_ORDER`, `PRIMARY_KEY_COLUMNS`, `CONTENT_COLUMNS`) that `corpus_diff`, `db_io.read_db_state`, and `db_io.apply_diff` all loop over generically. Once the registry, `ConceptRow`, the `Corpus`/`DbState` fields, and the per-table SQL/param dicts carry `concepts`, the diff/validation/orchestrator layers consume it almost for free. The only bespoke work is discovery + assembly (path-independent, body-derived `concept_id`) — see Key Decision 2.
>
> Rebuild, not a `0002`: `concepts` is folded directly into `0001_initial_schema.sql` and the whole `prod` schema is dropped and rebuilt. Pre-launch bootstrap exception (only reproducible `sandbox`/seed data, no real cataloged system depends on the DB yet) — the same exception used by `20260717v01_move_to_catalog_schema` (KD #6) and prior schema changes. Dropping `prod` also drops `ddl_versions`, so re-applying an edited `0001` produces no checksum drift. See Key Decision 1.

### Phase 1 — Schema (DDL + grants)

1. [completed] Add `concepts` + `concepts_hstry` to the base schema - `code/apply_ddl/ddl/0001_initial_schema.sql`
   - 1.1. Add a `concepts` main table (schema-unqualified, resolves via `search_path` like every other table): `concept_id ltree primary key`, `label text`, `definition text`, `notes text`, `update_reason text`, `insert_ts timestamptz not null`, `update_ts timestamptz not null`. No `validated`/`validated_ts` (a definition is not a verified equivalence), no FK columns (system-agnostic). Column order must match the `ConceptRow` field order defined in Task 3 (positional `SELECT`/construction in `db_io`)
   - 1.2. Add the `concepts_hstry` mirror: all `concepts` columns plus `end_ts timestamptz not null`, `primary key (concept_id, end_ts)`, no defaults/CHECKs (history-mirror convention, matches `systems_hstry`)
   - 1.3. Add a GiST index on `concept_id` (scalar `ltree`, mirroring the existing scalar-ltree GiST pattern in `0001`) to support the subtree/`lquery` lookups the overview advertises (`claim_type.original`, `claim_type.void`)
   - 1.4. Add `comment on table concepts is '…'` and `comment on table concepts_hstry is '…'` matching the existing comment style
   - 1.5. Update any header/inline comments in `0001` that count "7 main tables / 7 `_hstry` mirrors" → 8/8

2. [completed] Grant the CI role on the new tables - `code/apply_ddl/grant_metadata_db_ci.sql`
   - 2.1. Add `concepts` to the `grant select, insert, update, delete on <main tables>` list (grants are per-table here, not `ALL TABLES IN SCHEMA`, so a new table needs an explicit entry)
   - 2.2. Add `concepts_hstry` to the INSERT-only `grant insert on <_hstry tables>` list (append-only history)
   - 2.3. Update the header-comment table counts ("7 main tables / 7 `_hstry` tables") → 8/8; keep the `-v schema=`/`-v database=` invocation and the `usage on schema` grant unchanged

### Phase 2 — Shared registry & row model

3. [completed] Register `concepts` and add its row dataclass - `code/load_metadata_db/data_model.py`
   - 3.1. Add `@dataclass(frozen=True) class ConceptRow` with fields, **in this order**, matching the DDL column order: `concept_id: str`, `label: str | None`, `definition: str | None`, `notes: str | None`, `update_reason: str | None`, `insert_ts`, `update_ts` (timestamp types as the other row dataclasses use). Field order drives `_content_signature` and positional construction in `db_io`
   - 3.2. Append `"concepts"` to `TABLE_ORDER` (system-agnostic, no children — safe at the end)
   - 3.3. Add `"concepts": ("concept_id",)` to `PRIMARY_KEY_COLUMNS` (this defines the corpus/DbState dict key; the DDL PK from Task 1.1 must equal this — asserted by `test_pk_agreement_and_ltree_types`)
   - 3.4. Add a `"concepts"` entry to `CONTENT_COLUMNS` = the diff-relevant authored columns `{concept_id, label, definition, notes, update_reason}` (excludes `insert_ts`/`update_ts`)
   - 3.5. Add `concepts: dict[str, ConceptRow]` to both the `Corpus` and `DbState` dataclasses, and `concepts={}` to `empty_corpus()` and `empty_db_state()`
   - 3.6. Update the stale "7 main tables"/"5 tables"/"two composite-key tables" doc-comments to reflect the 8th, system-agnostic table; note `concepts` uses none of the path-derived ID builders (`data_source_id`/`schema_id`/`table_id`/`column_id`)

### Phase 3 — Discovery & assembly (the bespoke new branch)

4. [completed] Discover and classify `data/concepts/` files - `code/load_metadata_db/yaml_discovery.py`
   - 4.1. Add `"concepts"` to the `FileType` literal
   - 4.2. Make `PathIdentity` carry a concepts file whose identity is *not* system-anchored — make `system` (and the other `data/systems/`-only fields) optional, or represent a concepts file with just `file_type` + `path`. A concepts file has no `{system}.{db}.{schema}` prefix
   - 4.3. Extend `discover_yaml_files` to also walk `{data_root}/concepts/` (all `*.yaml`), routing those to the concepts branch; treat an absent `data/concepts/` as empty (not an error), and keep the existing `data/systems/`-absent handling
   - 4.4. Add a `data/concepts/` branch to `decode_path` (or a sibling `decode_concept_path`) that classifies the file as `file_type="concepts"` **before** the `systems/` resolution, so a concepts file is never misclassified under `systems/`

5. [completed] Assemble concept rows with body-derived IDs - `code/load_metadata_db/corpus_assembly.py`
   - 5.1. Add a `"concepts"` entry to `_RECOGNIZED_KEYS` = `{concept_id, label, definition, notes, update_reason}` — **without** `system` (there is no path system to agree with)
   - 5.2. Add `_assemble_concepts` (list-form file, model on `_assemble_columns`): read each entry's `concept_id` from the **body**, validate it as an `ltree` (split on `.`, run each segment through `validate_identifier_segment`), and build a `ConceptRow`. It must **not** call `_check_body_system` and must not use the path-derived ID helpers
   - 5.3. Wire `assemble_corpus`: add `"concepts"` to the `seen_keys` dict (so duplicate-PK detection via `_record` works), add an `elif ident.file_type == "concepts":` dispatch branch recording into `corpus.concepts`, and add the concepts count to the final assembly log line

### Phase 4 — Validation (minimal)

6. [completed] Confirm concepts pass validation and add `concept_id` syntax coverage - `code/load_metadata_db/corpus_validation.py`
   - 6.1. Include `concepts` in `_check_identifier_syntax` (validate every dot-delimited segment of `concept_id` via `validate_identifier_segment`) — defense-in-depth alongside the assembler check
   - 6.2. Confirm (and document via a code comment / this plan) that concepts are intentionally exempt from `_check_references` (no FKs), `_check_relationship_pairs`, `_check_mapping_disambiguation`, `_check_mapping_linkability`, `_check_sql_expressions`, `_check_join_type` (no SQL, freeform text)
   - 6.3. Verify `validate_update_reason` already covers concepts generically (it reads `getattr(row, "update_reason")` off `diff` changes) — no code change expected beyond `ConceptRow` having the field

### Phase 5 — DB I/O (mechanical per-table)

7. [completed] Add concepts read/write/history SQL - `code/load_metadata_db/db_io.py`
   - 7.1. Import `ConceptRow`; add `_SELECT_CONCEPTS` (column order = `ConceptRow` field order) and a `read_db_state` block building `ConceptRow(*row)` keyed via `pk(r, "concepts")`; add concepts to the summary log
   - 7.2. Add `_INSERT_CONCEPTS` (… `insert_ts, update_ts) VALUES (…, now(), now())`), `_UPDATE_CONCEPTS` (… `update_ts=now() WHERE concept_id=%s`), `_DELETE_CONCEPTS`, and `_HSTRY_INSERT_CONCEPTS` (`INSERT INTO concepts_hstry (…) SELECT …, now() FROM concepts WHERE concept_id=%s`)
   - 7.3. Add `"concepts_hstry"` to `_HSTRY_TABLES` (the `--reset-hstry` TRUNCATE set) and `"concepts"` to `_FK_ORDER`
   - 7.4. Add `"concepts"` branches to `_insert_params` and `_update_params` (simple single-PK table, like `systems` — no `validated`/`validated_ts` CASE guard), and add `"concepts"` to the single-PK membership in `_pk_params`
   - 7.5. Add `"concepts"` to the four dispatch dicts `_INSERT_SQL` / `_UPDATE_SQL` / `_DELETE_SQL` / `_HSTRY_INSERT_SQL`

### Phase 6 — Seed data & example YAML

8. [completed] Seed the single *claim* concept - `data/concepts/concepts.yaml`
   - 8.1. One list entry: `concept_id: claim`, `label: Claim`, and a freeform `definition` (prose) that a reader/Claude Code can look up — stating that in OCS a claim is identified by `clm_no` on the final-action record, and its equivalent in EDW is the composite key (`geo_mbr_sk`, `clm_dt_grp_sk`, `clm_type_cd`, `clm_num_sk`) restricted to the final-action version; note this is definitional, not a runnable equivalence. `update_reason: null` (fresh insert). Keep it a single record per the scope
   - 8.2. This is the only concepts data authored in this activity
   - 8.3. Validation of this data file is intrinsic to the loader (there is no separate `data_val_*.py` — the loader *is* the validator): its structure is exercised by the assembly unit test (Task 12), and its live acceptance is confirmed by the clean full-insert (Task 18.4) and the subsequent `--dry-run` `0/0/0` + row-present check (Task 18.5)

9. [completed] Add a concepts example file - `readme/metadata-db-example-yamls/concepts.yaml`
   - 9.1. One representative `concepts` entry showing every authored field (`concept_id`, `label`, `definition`, `notes`, `update_reason`), matching the "one example of each YAML file type" convention. **Placed at `readme/metadata-db-example-yamls/data/concepts/concepts.yaml`** (not the bare path in the task title) to match the existing example tree, which mirrors the real `data/` layout — see Key Decision 11.

### Phase 7 — Tests (one per changed module)

10. [completed] Update + run data_model tests - `code/load_metadata_db/unit_tests/test_data_model.py`
    - 10.1. Add `"concepts"` to the hardcoded `TABLE_ORDER` assertion; update the "seven"/count-based tests (`test_content_columns_covers_all_*`, `test_primary_key_columns_cover_all_*`, `test_empty_corpus_and_db_state_have_*_empty_dicts`, `test_primary_key_columns_are_real_dataclass_fields`) to 8; add `ConceptRow` field/PK coverage
    - 10.2. Run `uv run pytest code/load_metadata_db/unit_tests/test_data_model.py -v --cov=data_model --cov-report=term-missing`

11. [completed] Update + run discovery tests - `code/load_metadata_db/unit_tests/test_yaml_discovery.py`
    - 11.1. Cover `data/concepts/` discovery, the concepts `decode_path` branch, and that a concepts file is not misclassified under `systems/`; cover an absent `data/concepts/` treated as empty

12. [completed] Update + run assembly tests - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
    - 12.1. Cover `_assemble_concepts`: body-derived `concept_id`, dotted-ltree segment validation, recognized-keys enforcement (rejects `system`), duplicate-`concept_id` detection, and dispatch into `corpus.concepts`

13. [completed] Update + run validation tests - `code/load_metadata_db/unit_tests/test_corpus_validation.py`
    - 13.1. Assert a valid concept passes `validate_corpus`; assert a malformed `concept_id` fails `_check_identifier_syntax`; assert concepts participate in `validate_update_reason` (missing reason on update fails, fresh insert passes)

14. [completed] Update + run diff tests - `code/load_metadata_db/unit_tests/test_corpus_diff.py`
    - 14.1. Add a concepts insert / update / delete case through `compute_diff` (uses the now-`concepts`-carrying `empty_corpus`/`empty_db_state`)

15. [completed] Update + run db_io tests - `code/load_metadata_db/unit_tests/test_db_io.py`
    - 15.1. Cover `read_db_state` parsing a concepts row, the INSERT/UPDATE/DELETE/HSTRY param builders and SQL dispatch for `concepts`, and `concepts_hstry` in the `--reset-hstry` set
    - 15.2. Run with coverage

16. [completed] Verify the orchestrator is unaffected - `code/load_metadata_db/unit_tests/test_load_metadata_db.py`
    - 16.1. Confirm the `target_tables_referenced` derivation loop does not touch concepts and a corpus containing concepts flows through `run` (mocked DB); add/adjust fixtures as needed

17. [in-progress] Make the integration test concepts-aware + run live - `code/load_metadata_db/unit_tests/test_integration.py`
    - **17.1 + 17.2 completed** (authored, no live DB): concepts round-trip added to `_stage_full_corpus` + lifecycle Phase 1, explicit `concepts`/`concepts_hstry` PK + GiST-index checks in `test_pk_agreement_and_ltree_types`, TRUNCATE lists extended, and the stale "0001-0003" docstrings corrected to the single-migration reality. **17.3 deferred** — the gated live run requires `metadata_db_maintainer` (CREATE DATABASE / DROP SCHEMA); the only credentials in `.env` are the `metadata_db_ci` loader role. See Key Decision 12.
    - 17.1. (authored in Phase 7, no live DB) `test_pk_agreement_and_ltree_types` auto-covers concepts once it is in `TABLE_ORDER` + DDL; confirm the built schema has `concepts`/`concepts_hstry` with the expected PK and GiST index; add a concepts row to the lifecycle fixture and assert it round-trips (load → row present, `concepts_hstry` empty on first insert) — this round-trip assertion is the loaded-`concepts`-table output validation
    - 17.2. (authored in Phase 7) Fix the stale "0001-0003" docstring → the current single-migration reality
    - 17.3. (runs *after* the Phase 8 rebuild) Run `METADATA_DB_INTEGRATION=1 uv run pytest -m integration code/load_metadata_db/unit_tests/test_integration.py -v` against the rebuilt DB, then the full unit suite at the 100%-coverage bar

### Phase 8 — Rebuild, regrant, reload, verify (maintainer, out-of-band)

18. [pending] Rebuild `prod` with concepts, regrant, reload the seed, verify - maintainer-run, out-of-band
    - **Deferred (maintainer/out-of-band).** Every sub-step (18.1 `DROP SCHEMA prod`, 18.2 `apply_ddl`, 18.3 regrant, 18.4 CI-role reload, 18.5 verify) needs `metadata_db_maintainer` privileges not available to this implementation pass (`.env` holds only the `metadata_db_ci` role). Code is ready: the front-half (discover → assemble → validate) was smoke-tested against the real `data/` corpus and the seed `data/concepts/concepts.yaml` parses/validates cleanly. See Key Decision 12.
    - 18.1. As `metadata_db_maintainer`: `DROP SCHEMA IF EXISTS prod CASCADE;` (removes all tables + `ddl_versions` + the `ltree` extension)
    - 18.2. `uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl.toml` — recreates `prod`, sets `search_path`, re-applies the edited `0001` (now 8 tables + 8 `_hstry` + `load_audit` + `ddl_versions`, `ltree` in `prod`); records the new checksum
    - 18.3. Regrant: `psql -v schema=prod -v database=metadata_db -f code/apply_ddl/grant_metadata_db_ci.sql`
    - 18.4. Reload as the CI role: `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml` — expect a clean full-insert including the one concepts row
    - 18.5. Verify (output data validation): `concepts` holds exactly the seed row; `concepts_hstry` is empty; `apply_ddl --check` clean; a second loader `--dry-run` reports `0/0/0`; the GiST index supports an `lquery`/subtree probe on `concept_id`

### Phase 9 — Documentation & CI/config

19. [completed] Update the maintenance/runbook doc - `readme/metadata-db-maintenance.md`
    - 19.1. Add `data/concepts/` to the Repo layout tree (system-agnostic, sibling of `systems/`) and a `concepts.yaml` row to the "YAML files" table; change "one of the seven main tables" → eight
    - 19.2. Add a Change-lifecycle note for authoring concepts (body-derived `concept_id`, freeform definition, single file `data/concepts/concepts.yaml`, no path anchoring)
    - 19.3. Note the rebuild ran with concepts in `0001` (Phase 8, Task 18); no runbook-mechanism change beyond the extra table

20. [completed] Fire the loader on concepts changes - `.gitlab-ci.yml`
    - 20.1. Add `data/concepts/**/*.yaml` to the post-merge load job's `changes:` rule (alongside `data/systems/**/*.yaml`) so edits to concepts trigger a load

21. [completed] Route ownership for concepts - `.gitlab/CODEOWNERS`
    - 21.1. Add a `data/concepts/` entry (glossary is cross-system — assign to a general/glossary owner rather than a single source-system team)

### Phase 10 — Review

22. [pending] Code review of changed files and address findings - `docs/code_review/`
    - **Deferred — separate workflow.** This task runs the `code-review` and `code-implementation` skills over the changed files; it is a distinct review pass, out of scope for this implementation invocation. See Key Decision 12.
    - 22.1. Run the `code-review` skill over each changed code file (`0001_initial_schema.sql`, `grant_metadata_db_ci.sql`, `data_model.py`, `yaml_discovery.py`, `corpus_assembly.py`, `corpus_validation.py`, `db_io.py`, and the changed test files), writing `cr_*.md` per the existing layout
    - 22.2. Address findings via the `code-implementation` skill; re-run the suite at the 100%-coverage bar
    - 22.3. Mark each review resolved when fixes land

## Key Data Decisions and Considerations

1. **Edit `0001` + rebuild, not a `0002` migration** — `concepts` is a first-class member of the initial schema (the overview now describes 8 core tables), and the rebuild directive drops the whole `prod` schema, so re-applying an edited `0001` from scratch causes no `ddl_versions` checksum drift. This is the same pre-launch bootstrap exception used by `20260717v01_move_to_catalog_schema` (KD #6) and prior schema changes — permitted only because no real cataloged system depends on the DB yet and all data is reproducible seed. After the first real system lands, `0001` becomes immutable and future changes go to `0002+`.

2. **System-agnostic → body-derived `concept_id` in a new `data/concepts/` tree** — every other table's ID is derived from its `data/systems/` path, but a concept ("what a *claim* is") does not belong to any one system. So its YAML lives outside `systems/` and its `concept_id` is read from the YAML body, forcing the one genuinely new branch in `yaml_discovery` (walk + classify `data/concepts/`) and `corpus_assembly` (`_assemble_concepts`, no `_check_body_system`, no path-derived ID). This is the first file type in the repo whose identity is not path-anchored — flagged as a structural first.

3. **The shared registry makes the rest nearly free** — `corpus_diff`, `db_io.read_db_state`, and `db_io.apply_diff` loop generically over `TABLE_ORDER`/`_FK_ORDER` and `CONTENT_COLUMNS`, so once `data_model` and the `db_io` SQL/param dicts carry `concepts`, diffing/reading/writing work without bespoke logic. `sql_parsing.py`, `apply_ddl.py`, and `conftest.py` are untouched.

4. **Field/column order coupling** — `corpus_diff._content_signature` and `db_io.read_db_state` build rows positionally (`ConceptRow(*row)`, `fields(row)` ordering). The `ConceptRow` field order (Task 3.1), the `_SELECT_CONCEPTS` column order (Task 7.1), and the DDL column order (Task 1.1) must all agree. `PRIMARY_KEY_COLUMNS["concepts"]` (Task 3.3) must equal the DDL PK (Task 1.1) — the integration PK-agreement test enforces this.

5. **Minimal validation, no SQL, no FKs, no `validated`** — a definition is freeform text that is never parsed or executed, has no cross-references, and is reviewed via MR/CODEOWNERS rather than asserted as a verified equivalence. The only concept-specific rule is `concept_id` ltree-segment syntax; `update_reason` discipline is inherited generically. Deliberately omitted: `related_objects` structured links (hand-maintained tags drift from the prose — retrieval is by searching the definition text), and `validated`/`validated_ts`.

6. **Single seed record (scope)** — exactly one concept is authored this activity: the *claim* definition (OCS `clm_no` ↔ EDW 4-part composite key, final-action restriction, in prose). It doubles as the worked example proving why concepts exist (irregular cross-system correspondence that fits neither `column_mappings` nor `table_relationships`). Additional concepts (claim-type code values, etc.) are future authoring, not code.

7. **`concepts_hstry` follows the universal mirror convention** — all `concepts` columns + `end_ts`, PK `(concept_id, end_ts)`, INSERT-only grant, TRUNCATE'd by `--reset-hstry`. No new history mechanism; it rides the generic `apply_diff` path.

8. **Overview doc already updated** — `readme/metadata-db-overview.md` was edited (in the session preceding this activity) to add the §2 bullet, §3.5 consideration, the §4 `concepts` column table at parity with the other tables, and the 8/8 counts. It is therefore not a task here; those edits are still uncommitted and land with this work.

9. **CI trigger + ownership** — the post-merge load only fires on `data/systems/**` changes today, so concepts edits would silently not load until `.gitlab-ci.yml`'s `changes:` rule also lists `data/concepts/**`. CODEOWNERS needs a `data/concepts/` route; since the glossary is cross-system it should not be owned by a single source-system team.

10. **Structure deviates from the per-unit Python task pattern, by design** — this activity modifies one cohesive system (the loader) rather than building independent analytical scripts, so tasks are grouped by concern into phases (all code, then all tests, then one rebuild/run) — the same shape as `20260717v01_move_to_catalog_schema` — instead of repeating create-code→test→run→validate per file. There are no standalone `data/validation/data_val_*.py` scripts because the loader's own `corpus_validation`/assembly layer *is* the input validator and the integration round-trip + rebuild verify (Tasks 17.1, 18.5) *are* the output validation. The lone "sample" run is the single seed record, which is also the full dataset, so there is no separate full-data run to defer.

11. **Example YAML placed at the mirrored `data/` path (implementation decision)** — Task 9's title reads `readme/metadata-db-example-yamls/concepts.yaml`, but the shipped example tree mirrors the real `data/` layout (`readme/metadata-db-example-yamls/data/systems/...`). To keep the "one example of each file type" convention consistent, the concepts example was written to `readme/metadata-db-example-yamls/data/concepts/concepts.yaml`. This also lets the example tree be assembled/validated as a whole corpus — done as a smoke test, which passed with the concept present.

12. **Phase 8, Task 17.3, and Phase 10 deferred to their proper runners (implementation decision)** — Phases 1–7 and 9 are fully implemented and the runnable unit suite is green at the 100%-coverage bar (292 passed, 4 integration skipped; every changed loader module at 100%). The pieces that could *not* run in this pass, and why:
    - **Phase 8 (Task 18) + the gated integration run (Task 17.3)** need `metadata_db_maintainer` (DROP SCHEMA / CREATE DATABASE / apply_ddl). The only credentials in `.env` are the `metadata_db_ci` loader role, which by design holds no DDL rights — so the live rebuild/reload and the `METADATA_DB_INTEGRATION=1` run are left for the maintainer to execute out-of-band. Their code is authored and ready: `test_pk_agreement_and_ltree_types` auto-covers `concepts` via the `TABLE_ORDER` loop and now has explicit `concepts`/`concepts_hstry` PK + GiST assertions; the lifecycle test round-trips the seed concept. De-risked by smoke-testing the DB-free front-half (discover → assemble → validate) against the real `data/` corpus, which passed.
    - **Phase 10 (Task 22)** is a separate `code-review`→`code-implementation` workflow, not code authored here; flagged rather than run.
    - **DDL note:** the `concepts`/`concepts_hstry` DDL could not be executed (the CI role cannot CREATE), so it was verified by close review against the proven `systems`/`systems_hstry` pattern; the four-way column-order agreement (DDL ↔ `ConceptRow` ↔ `_SELECT_CONCEPTS` ↔ `PRIMARY_KEY_COLUMNS`) was checked by eye and is additionally guarded without a DB by the positional `ConceptRow(*row)` assertion in `test_db_io.py`.
