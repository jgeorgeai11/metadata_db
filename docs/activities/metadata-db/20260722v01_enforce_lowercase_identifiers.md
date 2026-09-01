---
name: 20260722v01_enforce_lowercase_identifiers
goal: Mandate lowercase for every identifier segment that composes a metadata_db ltree id — the target systems (SAS, Postgres, Snowflake) resolve unquoted identifiers case-insensitively while ltree ids are case-sensitive, so case variation creates spurious distinct ids; one regex change in the loader eliminates the class. The corpus, tests, docs, and examples are migrated, and — riding the same reload — `general` replaces `public` as the single default-schema name (both pagila's schema and the schemaless-source sentinel). The DB is rebuilt by drop-and-reload; no migration file.
created: 2026-07-22 16:14:04
updated: 2026-07-22 17:02:15
---

## Implementation Plan

> Phases: loader rule (1), corpus migration (2), tests (3), docs and
> examples (4), out-of-band rebuild + verification (5), review (6).
> Execution note: the data-side work (tasks 3, 4, 11, plus the 6.3
> fixture update, the 10.4 sentinel swap, and the `public` → `general`
> part of 6.2) was done first by direct edit on 2026-07-22 — safe out
> of order because the current loader accepts both cases, so an
> already-lowercase corpus passes before and after the rule lands. The
> remaining tasks follow the phase order.
>
> Enforcement design (two layers, one choke point):
>   - **Direct charset validation** — `validate_identifier_segment`
>     (`yaml_discovery.py`) is the single choke point every segment flows
>     through: path-derived `system` / `database_name` / `schema_name` /
>     `target_system`, body-derived `table_name` / `column_name` /
>     `relationship_name` / `mapping_name` / concept `name`, and the
>     `concept_id` re-validation in `corpus_validation`. Tightening its
>     regex from `[A-Za-z0-9_-]+` to `[a-z0-9_-]+` covers all of them,
>     and every composite id (`data_source_id`, `schema_id`, `table_id`,
>     `column_id`, `concept_id`) is lowercase by construction.
>   - **Transitive enforcement** — authored references (`table_a_id`,
>     `table_b_id`, `source_column_id`, `related_object_ids`, column refs
>     inside `join_condition` / `target_expression`) are not
>     charset-checked today and need no new check: they must resolve
>     against corpus PKs, which are now all-lowercase, so any uppercase
>     reference fails FK/resolution with the existing
>     "did you mean …? (case mismatch)" hint pointing at the fix.
>   - **Exempt** — free-text fields (`description`, `notes`, `label`,
>     `definition`, `use_when`, `update_reason`) and `data_type` (a
>     native-type string like `VARCHAR(20)`, not an identifier).

### Phase 1 — Loader rule

1. [completed] Tighten the identifier charset to lowercase with a targeted hint - `code/load_metadata_db/yaml_discovery.py`
   - 1.1. `_LABEL_RE`: `[A-Za-z0-9_-]+` → `[a-z0-9_-]+`; update the accompanying rationale comment (ltree storage, `.` separator, and now the lowercase mandate with its case-insensitive-targets rationale)
   - 1.2. `validate_identifier_segment`: when the rejected value would be legal after `str.lower()` (i.e., its only offense is uppercase letters), the error message says identifiers must be lowercase and names the lowercased form to use; otherwise the existing offending-characters message (which now also lists uppercase letters as offenders) stands
   - 1.3. Update the function docstring (charset, the two message forms) and any module-level references to the old charset
   - 1.4. No change to `decode_path` logic — path segments already route through `validate_identifier_segment` / `_validate_schema_segment`

2. [completed] Comment/docstring currency for the case rules - `code/load_metadata_db/corpus_validation.py`
   - 2.1. `_case_hint` stays (a mis-cased *reference* against the lowercase corpus is now the guaranteed shape of a case mismatch); update its docstring so "identifiers are case-sensitive (ltree does not fold case)" is joined by the lowercase mandate — corpus ids are all-lowercase, so any uppercase in a reference cannot resolve
   - 2.2. Sweep the module (and `corpus_assembly.py` / `sql_parsing.py` comments, if any reference the old charset or case-sensitivity wording) for `[A-Za-z0-9_-]` mentions; no behavior change in these modules

### Phase 2 — Corpus data migration

3. [completed] Lowercase every identifier value in the shipped corpus - `data/systems/` *(done 2026-07-22 direct edit: 49 lines across the 5 files — 27 column_names, 2 join_conditions, source_column_id + expression, 10 related_object_ids lines, 8 warehouse expressions; 3.7 grep clean; front-half discover→assemble→validate passes on the migrated corpus)*
   - 3.1. `data/systems/edw/sandbox_edw/claims_vw/columns.yaml`: lowercase all 27 uppercase `column_name` values (`MBR_SK` → `mbr_sk`, …)
   - 3.2. `data/systems/edw/sandbox_edw/claims_vw/table_relationships.yaml`: lowercase the column leaves inside both `join_condition` values
   - 3.3. `data/systems/edw/sandbox_edw/claims_vw/mappings/edw.yaml`: lowercase the `source_column_id` leaf and the column refs inside `target_expression`
   - 3.4. `data/systems/edw/sandbox_edw/claims_vw/concepts.yaml`: lowercase the column leaves in every `related_object_ids` entry (definition prose is exempt free text — leave unchanged)
   - 3.5. `data/systems/warehouse/sandbox_ocs/general/mappings/edw.yaml`: lowercase the EDW column refs inside every `target_expression`
   - 3.6. Leave every `update_reason` as-is: all are currently `null` (verified), which is exactly what the fresh reload's all-inserts diff requires
   - 3.7. Verify no uppercase identifier remains anywhere under `data/systems/`: a grep over identifier-bearing keys (`table_name`, `column_name`, `name`, `relationship_name`, `mapping_name`, `source_column_id`, `table_a_id`, `table_b_id`, `related_object_ids` entries) and the two SQL fields finds zero uppercase segments

4. [completed] Rename the pagila schema `public` → `general` - `data/systems/sandbox/pagila/` *(done 2026-07-22 direct edit: `git mv` + all 25 id references, header comments, and the schema description reworded; 4.4 grep clean; unit suite green — note task 11's example-YAML mirror rename must land together with the task 6.3 fixture update, since `test_corpus_assembly` asserts `sandbox.pagila.public` ids against the example tree)*
   - 4.1. `git mv data/systems/sandbox/pagila/public data/systems/sandbox/pagila/general` (a full rename, not case-only, so it is safe on the Windows checkout)
   - 4.2. `general/table_relationships.yaml`: update every `table_a_id` / `table_b_id` / `join_condition` reference from `sandbox.pagila.public.*` to `sandbox.pagila.general.*` (25 occurrences)
   - 4.3. `general/schema.yaml`, `tables.yaml`, `columns.yaml`: update the header comments' example ids/paths (`sandbox.pagila.public` → `sandbox.pagila.general`) and reword the schema `description` ("Default public schema…") to match the new name
   - 4.4. Verify no `pagila.public` / `pagila/public` reference remains under `data/systems/`

### Phase 3 — Tests (one task per behavioral file; one sweep for mechanical fixture updates)

5. [completed] New rule coverage + fixture sweep - `code/load_metadata_db/unit_tests/test_yaml_discovery.py`
   - 5.1. `validate_identifier_segment`: an uppercase-only-offense value is rejected with the lowercase-hint message (assert the lowercased suggestion appears); a value with uppercase *and* an illegal character is rejected with the offending-characters message; existing legal-charset cases still pass with lowercase inputs
   - 5.2. `decode_path` / `discover_yaml_files`: an uppercase path segment (folder or `mappings/{TARGET}.yaml` stem) is a classification issue aggregated across the walk, not an abort
   - 5.3. Lowercase any uppercase segments in this file's own fixtures, and rename fixture schema segments `public` → `general` (decision 11)

6. [completed] Aggregated-issue coverage for uppercase body names + fixture sweeps - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
   - 6.1. An uppercase `table_name` / `column_name` / `relationship_name` / `mapping_name` / concept `name` records one issue in the aggregated `AssemblyError` (with the lowercase hint) while lowercase siblings still assemble
   - 6.2. Lowercase the uppercase identifier fixtures throughout the file (the `OCS`-style ids), keeping deliberately-invalid negative fixtures invalid *(partially done 2026-07-22 direct edit: the fixture schema segment was already renamed `public` → `general` throughout — dotted ids and `_path_id(schema_name=…)` together — leaving only the case changes)*
   - 6.3. Update the `sandbox.pagila.public` ids in `_build_corpus_tree` and its assertions to `sandbox.pagila.general` (the fixture tree mirrors the example YAMLs renamed in task 11) — dotted ids, the `schema.schema_name` assertion, and the two `/ "pagila" / "public"` folder-path forms *(done 2026-07-22 direct edit, together with task 11)*

7. [completed] Case-mismatch reference scenarios + fixture sweep - `code/load_metadata_db/unit_tests/test_corpus_validation.py`
   - 7.1. Keep (and re-point) the `_case_hint` scenarios: an uppercase reference (`table_a_id`, `source_column_id`, `related_object_ids` entry, SQL column ref) against the now-lowercase corpus fails resolution with the "did you mean …? (case mismatch)" hint naming the lowercase id
   - 7.2. Lowercase the uppercase identifier fixtures throughout the file, and rename the fixture schema segment `public` → `general` (decision 11: `public` should appear nowhere in the repo)

8. [completed] Mechanical fixture sweep across the remaining test files - `code/load_metadata_db/unit_tests/` (`test_sql_parsing.py`, `test_corpus_diff.py`, `test_data_model.py`, `test_db_io.py`, `test_load_metadata_db.py`, `test_integration.py`, `conftest.py`)
   - 8.1. Lowercase uppercase identifier segments in fixtures/ids, update any `sandbox.pagila.public` references to `sandbox.pagila.general`, and rename fixture schema segments `public` → `general` (decision 11; `test_corpus_assembly.py` is already done — these seven files, plus the two handled in tasks 5/7, still carry `.public.` fixtures); no behavioral assertions change (these files exercise diffing, IO, parsing shape — not the charset rule)
   - 8.2. Run the full unit suite at the 100%-coverage bar

### Phase 4 — Documentation and examples

9. [completed] Schema-reference doc - `readme/metadata-db-overview.md`
   - 9.1. Replace the case-sensitivity language with the lowercase mandate: identifier segments are lowercase-only (`[a-z0-9_-]`), with the rationale (all three target systems resolve unquoted identifiers case-insensitively — Postgres folds to lower, Snowflake to upper, SAS is insensitive — while ltree is case-sensitive, so case variation would create spurious distinct ids)
   - 9.2. Record the documented assumption: no cataloged object was created with a *quoted* mixed-case identifier (which would be case-sensitive in Postgres/Snowflake); if one ever appears, its exact spelling is recorded in `notes` and the rule revisited
   - 9.3. Lowercase the example ids (`warehouse.OCS` → `warehouse.ocs`, `edw.EDW_PRD` → `edw.edw_prd`, the `database_name` examples row, and any other identifier examples); leave prose acronyms (OCS, EDW as system names in text) alone

10. [completed] Maintenance doc - `readme/metadata-db-maintenance.md`
   - 10.1. Repo-layout tree and CODEOWNERS sketch: lowercase the example paths (`OCS/` → `ocs/`, `EDW_PRD/` → `edw_prd/`, `warehouse.OCS.concept.<name>` → `warehouse.ocs.concept.<name>`)
   - 10.2. "CI & loader" step 4 identifier-syntax bullet: charset becomes `[a-z0-9_-]` (lowercase letters, digits, underscore, hyphen); the case-sensitivity sentence becomes the lowercase mandate, and the case-mismatch hint description now frames uppercase references as the guaranteed-mismatch case
   - 10.3. Change-lifecycle authoring steps: note that new folders/names must be lowercase
   - 10.4. Rename the schemaless-source sentinel `public` → `general` (decision 11): overview schemas-table rule + `schema_id` example, maintenance repo-layout tree / concepts legend / "Adding a schema" step, and the `PathIdentity.schema_name` docstring in `yaml_discovery.py`. No loader logic keys off the sentinel — a docs/docstring-only change *(done 2026-07-22 direct edit; suite green. The `warehouse.OCS.general` doc examples still get lowercased by 9.3/10.1)*

11. [completed] Example YAMLs: pagila rename + lowercase verification - `readme/metadata-db-example-yamls/` *(done 2026-07-22 direct edit: `git mv` + all id/path/legend references incl. `mappings/sandbox_warehouse.yaml`, `concepts.yaml`, and `sandbox_warehouse/system.yaml`; schema description reworded; 11.4 greps clean; landed together with the task 6.3 fixture update — suite green, 373 passed)*
   - 11.1. `git mv readme/metadata-db-example-yamls/data/systems/sandbox/pagila/public readme/.../pagila/general` (mirror of task 4.1)
   - 11.2. Update every `sandbox.pagila.public.*` reference inside the moved files: `table_relationships.yaml` (25), `mappings/sandbox_warehouse.yaml` (14 `source_column_id`s + header comments), `concepts.yaml` (4, incl. `related_object_ids` entries), `tables.yaml` / `columns.yaml` / `schema.yaml` header comments and the schema `description` prose
   - 11.3. `data/systems/sandbox_warehouse/system.yaml`: update its one `pagila/public` reference
   - 11.4. Verify every identifier-bearing field across the examples is lowercase (a scoping grep found none uppercase — uppercase there is free text) and no `pagila.public` reference remains anywhere under `readme/`

### Phase 5 — Rebuild and verify (out-of-band, maintainer-run)

12. [completed] Drop-and-reload the database, then verify - loader/DDL run, no repo file *(done 2026-07-22 17:01, data-only reset per 12.2's TRUNCATE alternative: all 17 `prod` tables truncated (`RESTART IDENTITY`) as `metadata_db_maintainer` — 120 main rows + 1 `load_audit` row wiped — then a real loader run as `metadata_db_ci` applied `Diff: 120 insert(s), 0 update(s), 0 delete(s)` in one transaction; post-reload `--dry-run` reports `0/0/0` (12.3); suite bar already met (12.4): 380 passed, 4 env-gated skips, 100% loader coverage. Note: the `load_audit.commit_sha` records HEAD `9747651` while the loaded corpus includes this activity's not-yet-committed working-tree changes — lineage is accurate again once the activity's MR lands; re-running the loader then is a no-op)*
   - 12.1. No `0002` migration and no DDL edits: the schema is unchanged; the case rule is loader-enforced only (per decision 5 below)
   - 12.2. Rebuild per the maintenance doc's "Creating or rebuilding the database" flow (drop/recreate as the `CREATEDB` role, re-apply `0001` as `metadata_db_maintainer`, re-run the grant script) — or, equivalently for this data-only reset, `TRUNCATE` all 17 tables as the maintainer; then run the loader for real (all rows insert with `update_reason = null`, satisfying insert discipline)
   - 12.3. Post-reload `--dry-run` reports `Diff: 0 insert(s), 0 update(s), 0 delete(s)`
   - 12.4. Full unit suite green at the 100%-coverage bar

## Key Data Decisions and Considerations

1. **Why lowercase-only.** All three target systems resolve *unquoted* identifiers case-insensitively (Postgres folds to lowercase, Snowflake folds to uppercase, SAS names are case-insensitive), so folding loses nothing a query needs — while the metadata_db's own ltree ids are case-sensitive, meaning `…clm.CLM_TYPE_CD` and `…clm.clm_type_cd` are *different catalog ids* for the *same physical column*. Lowercase-only makes ids canonical, eliminates the mis-cased-reference footgun class outright, matches the canonical Postgres expression dialect (whose unquoted canonical form is lowercase), and makes explicit what Windows authoring already half-imposes (case-insensitive folders mean `OCS/` and `ocs/` can't coexist, and case-only renames are a git-on-Windows hazard).

2. **Documented assumption: no quoted mixed-case objects.** Identifiers created *quoted* with mixed case in Postgres/Snowflake are case-sensitive and can only be referenced quoted with exact case; an all-lowercase id cannot express such a name. No cataloged or anticipated system (OCS, EDW, SAS datasets) has them. If one ever appears, record the exact spelling in the column/table `notes` and revisit the rule — this is written into the overview doc (task 9.2) so it is a visible assumption, not a silent one.

3. **Reject, don't fold.** The loader rejects uppercase with a message naming the lowercased form rather than silently lowercasing. Fail-loud matches the loader's design throughout, silent folding could someday mask a genuinely quoted mixed-case object (decision 2), and — with the error-aggregation activity landed — a bulk-authored file full of uppercase names surfaces as one aggregated report, so rejection costs one fix pass, not one CI round-trip per name.

4. **Single choke point; references stay transitively enforced.** Only `_LABEL_RE` changes. Authored references (`table_a_id`, `table_b_id`, `source_column_id`, `related_object_ids`, SQL column refs) get no new charset check: they already must resolve against corpus PKs, which are lowercase by construction, and a mis-cased reference already fails with the case-mismatch hint naming the correct id. Adding direct charset checks there would duplicate the rule for no additional safety.

5. **No DDL change, no `0002` migration — drop-and-reload instead.** Per explicit user decision. A DB-side `CHECK (id::text = lower(id::text))` would only defend against a writer other than the loader, which is the sole DML writer by role design; the pre-merge dry-run gate is the enforcement point. An in-place data migration was also rejected: the 28 changed-PK rows (~24% of the ~120-row corpus) sit right at the mass-delete guard threshold and would sever `_hstry` lineage row-by-row — while the sandbox corpus is fully reproducible from YAML, making the documented rebuild flow strictly simpler. Pre-launch, bootstrap-phase precedent applies. The pagila `public` → `general` rename (decision 11) changes another ~35 PKs, reinforcing the same choice.

6. **`update_reason` discipline holds under reload.** A fresh reload makes every row an insert, and insert discipline requires `update_reason: null`. Verified: every `update_reason` in the shipped corpus is already null, so no sweep is needed (task 3.6 pins this).

7. **Free text is exempt, including `data_type`.** `description`, `notes`, `label`, `definition`, `use_when`, and `update_reason` are prose; `data_type` is the source system's native type string (`VARCHAR(20)`, `TIMESTAMP_NTZ`), not an identifier segment. Concept `definition` prose that names columns inline (e.g. `CLM_TYPE_CD`) is left as authored — prose is never parsed, and normalizing it is a readability judgment for the author, not a loader rule. Exception: the pagila schema `description` ("Default public schema…") *describes the renamed object itself*, so it is reworded with the rename (tasks 4.3 / 11.2).

8. **Native display casing is not identity.** EDW/Snowflake render names uppercase, SAS renders as-typed — rendering conventions, not referential requirements, for unquoted names. If the native spelling ever matters to a reader, it belongs in `description`/`notes`.

9. **`_case_hint` is kept, not retired.** The mandate moves the mismatch to authoring time for *segments*, but *references* (which are transitively enforced) still benefit: an author pasting an uppercase column name from a Snowflake console gets "did you mean `…clm_type_cd`? (case mismatch)" instead of a bare "unknown column".

10. **Sequenced after the error-aggregation activity by design.** `20260722v01_aggregate_assembly_errors` decision 7 anticipated this mandate: charset violations will be the most common authoring error during adjustment and real-system onboarding, and aggregation ensures they surface as one report per run.

11. **`general` becomes the single default-schema name: pagila rename + sentinel rename ride the same reload.** Per user request, in two steps. First the pagila example schema was renamed `public` → `general`; then the schemaless-source *sentinel itself* was renamed `public` → `general`, matching what the warehouse sandbox (`sandbox_ocs/general/` — a SAS-style schemaless source) already practiced. After both, `public` appears nowhere in the catalog, docs, or examples, and `general` reads uniformly as "the default schema" whether the source is schemaless (sentinel role) or the schema choice is illustrative (pagila). Trade-offs accepted knowingly: (a) pagila's physical Postgres schema *is* `public`, so its catalog id no longer mirrors the physical name — acceptable for illustrative sandbox data, wrong for a real cataloged system, where the schema segment must mirror the source; (b) `general` now does double duty as sentinel and real schema name — acceptable because the sentinel is a documented authoring convention only (no loader logic keys off it). Scope: folder + ~35 id references in `data/systems/` (task 4), the example YAMLs incl. `mappings/sandbox_warehouse.yaml` and `concepts.yaml` (task 11), the `test_corpus_assembly` fixture tree (task 6.3), the sentinel mentions in both readmes and the `yaml_discovery.py` docstring (task 10.4), and any test/doc stragglers (tasks 8.1). The `docs/code_review/**/pagila/public/...` copies are historical review artifacts and are left untouched.

12. **`_case_hint` wired into the authored-reference FK messages (implementation addition, 2026-07-22).** The enforcement design and task 7.1 describe an uppercase `table_a_id` / `source_column_id` failing "with the existing case-mismatch hint" — but those FK existence checks in `corpus_validation._check_references` did not actually append `_case_hint` (only SQL column refs and `related_object_ids` entries did). Rather than water the tests down to a hint-less "not defined", the existing helper was wired into the three authored-reference FK messages (`table_a_id`, `table_b_id`, `source_column_id`), making the documented behavior true. Derived-by-construction FKs (`schema_id`, `table_id`, etc.) were left as-is — they are composed from path segments, so a case mismatch cannot arise there. This is the one deliberate behavior change beyond task 1's regex (task 2.2's "no behavior change" referred to the comment sweep).
