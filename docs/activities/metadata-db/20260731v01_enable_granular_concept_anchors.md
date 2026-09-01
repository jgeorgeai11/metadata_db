---
name: 20260731v01_enable_granular_concept_anchors
goal: Relax concept anchoring so a concept can be defined at table and column level, not only at data source and schema level, by letting a concepts.yaml `name` carry additional path segments relative to its file's anchor. The change spans the concept-id derivation and its name-validation rules, the anchor-resolution validation, and the `concept_id` shape CHECK, and closes with an adoption pass that re-anchors the edwc_prd concepts under an explicit anchoring criterion. Existing concepts and their ids are unchanged; the sas-data-resolution resolver (a separate repo) is explicitly out of scope and is recorded as an external dependency.
created: 2026-07-31 18:56:54
updated: 2026-08-03 10:53:00
---

## Implementation Plan

### Phase 1 — Concept id derivation and name validation

1. [completed] Compose concept ids from a dotted relative `name` and enforce the name rules - `code/load_catalog_data/corpus_assembly.py`
   - 1.1. `_assemble_concept_row` accepts a `name` of one or more dot-separated segments; the composed id is the file's path prefix, then the leading segments, then the reserved `concept` segment, then the final segment as the leaf
   - 1.2. A name with no dots composes byte-identically to the id it composes today
   - 1.3. Every segment must satisfy the existing identifier rules (lowercase `[a-z0-9_-]+`, non-empty, within the ltree label limit); the error names the failing segment and quotes the whole `name`
   - 1.4. Reject a leading, trailing, or doubled dot with a message quoting the whole `name`
   - 1.5. Reject `concept` as any segment, not only as the leaf
   - 1.6. Reject a composed anchor deeper than 4 labels (data source, schema, table, column)
   - 1.7. Reject a dotted `name` in a data-source-scoped concepts file; the message directs the author to the schema's folder
   - 1.8. `_assemble_columns` rejects `column_name` of `concept`, matching the existing table-name guard

2. [completed] Resolve table- and column-depth concept anchors - `code/load_catalog_data/corpus_validation.py`
   - 2.1. `_check_concept_anchors` resolves a 3-label anchor against `corpus.tables` and a 4-label anchor against `corpus.columns`, with a `_case_hint` on failure like the existing branches
   - 2.2. Anchor depth determines the required object kind: a 3-label anchor that is not a table fails, and a 4-label anchor that is not a column fails, each naming the expected kind
   - 2.3. The out-of-range message and the docstring state the four valid depths
   - 2.4. The malformed-shape skip is unchanged

3. [completed] Update the concept id form in the discovery module's comments - `code/load_catalog_data/yaml_discovery.py`
   - 3.1. The `RESERVED_CONCEPT_SEGMENT` comment and the module docstring's concepts entries state the four anchor depths
   - 3.2. The docstring records that file placement is unchanged: source root and schema folder, each as a single file or a shard folder

4. [completed] Update the concept id form in the data model's docstrings - `code/load_catalog_data/data_model.py`
   - 4.1. The module docstring's concept-composition line states the new id form
   - 4.2. `ConceptRow`'s docstring states the new id form and the relative-name mechanism

5. [completed] Create and run tests for id derivation and name validation - `code/load_catalog_data/unit_tests/test_corpus_assembly.py`
   - 5.1. Test single-label names in a source-scoped and a schema-scoped file produce today's ids byte-for-byte
   - 5.2. Test a two-segment name yields a table anchor and a three-segment name yields a column anchor
   - 5.3. Test each name-rule failure raises its specific message: leading dot, trailing dot, doubled dot, `concept` as leaf, `concept` as a non-leaf segment, uppercase segment, illegal-charset segment, over-long segment, over-deep name
   - 5.4. Test a dotted name in a data-source-scoped file raises, and the same name in the schema-scoped file succeeds
   - 5.5. Test a column named `concept` raises
   - 5.6. Test two shards in one `concepts/` folder composing the same id fail as a duplicate PK naming both files
   - 5.7. Run tests with `python -m pytest code/load_catalog_data/unit_tests/test_corpus_assembly.py -v`

6. [completed] Create and run tests for anchor resolution - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 6.1. Test concepts anchored at an existing table and at an existing column validate clean
   - 6.2. Test a phantom table anchor and a phantom column anchor each produce one issue naming the unresolved anchor
   - 6.3. Test a 4-label anchor whose final segment is a column of a different table fails
   - 6.4. Test the existing source-level and schema-level anchor cases still pass
   - 6.5. Run tests with `python -m pytest code/load_catalog_data/unit_tests/test_corpus_validation.py -v`

### Phase 2 — Schema constraint

7. [completed] Widen the `concept_id` shape constraint to the four anchor depths - `code/apply_ddl/ddl_catalog/0001_initial_schema.sql`
   - 7.1. The CHECK admits `nlevel(concept_id)` of 3 through 6 and still requires the reserved `concept` segment second-to-last (the single constraint edit; see decision 10)
   - 7.2. Update the constraint's inline comment, the header comment's `concept_id` shape bullet, and the `concepts` table comment that names the two anchor levels
   - 7.3. Apply as an in-place edit to `0001` rather than a new migration file

8. [completed] Extend the DDL tests for the widened constraint - `code/apply_ddl/unit_tests/test_apply_ddl.py`
   - 8.1. Assert statically that the `concept_id` CHECK admits levels 3-6 and pins the reserved segment second-to-last
   - 8.2. Where the env-gated integration path reaches a live database, assert depth-5 and depth-6 ids insert and a depth-7 id is rejected; otherwise record that only the static assertion runs
   - 8.3. Run tests with `python -m pytest code/apply_ddl/unit_tests/ -v`

### Phase 3 — Example corpus and backward-compatibility proof

9. [completed] Add a granular concept to the example corpus - `readme/metadata-db-example-yamls/data_catalog/sources/pagila/general/concepts.yaml`
   - 9.1. Add one table- or column-anchored concept using the relative-name form, alongside the existing source- and schema-level examples
   - 9.2. Run `python -m pytest code/load_catalog_data/unit_tests/test_example_corpus.py -v`, which stages the example tree through discovery, assembly, and validation

10. [completed] Verify backward compatibility against the live corpus - `code/load_catalog_data/load_catalog_data.py`
    - 10.1. Before applying Phases 1-2, run `uv run code/load_catalog_data/load_catalog_data.py --config code/load_catalog_data/config/load_catalog_data.toml --dry-run` on main and capture the validation result and diff summary
    - 10.2. With Phases 1-2 applied and no corpus edits, rerun the dry-run and verify the validation result and diff summary are identical to the captured baseline — an empty diff is not the gate (see decision 4)
    - 10.3. Sweep this repo for any other consumer that assumes 3- or 4-label concept ids or the `.concept.` position (`grep -rn "concept" code/ .claude/`); fix those in scope and record the rest as follow-ups
    - 10.4. Run the full loader suite with `python -m pytest code/load_catalog_data/unit_tests/ -q`

### Phase 4 — Documentation

11. [completed] Document the anchor depths, the relative-name form, and the anchoring rule - `readme/metadata-db-overview.md`
    - 11.1. The §2 concepts bullet and the `concepts` table's `concept_id` row state the id form `{database}[.{schema}[.{table}[.{column}]]].concept.{name}`, composed from the file's path prefix plus the relative segments in the body `name`
    - 11.2. §3.6 states the anchoring rule: anchor at the narrowest object whose scope covers what the concept teaches (single column → that column; multiple columns of one table → that table; anything spanning tables or teaching cross-view discipline → the schema); `related_object_ids` covers everything the concept references
    - 11.3. §3.6 states the retrieval contract explicitly: because a re-anchored concept does not repeat its anchor in `related_object_ids`, "which concepts are about object X?" is the union of two indexed lookups — anchor-prefix containment on `concept_id` and array containment on `related_object_ids` — and gives the query pair
    - 11.4. §3.6 states the boundary against a column `description`: a description says what the column is and travels with the column; a column-anchored concept is teaching prose retrieved on its own

12. [completed] Document the authoring conventions and the schema edit - `readme/metadata-db-maintenance.md`
    - 12.1. Concepts authoring: the unchanged file placements (source root and schema folder, single file or shard folder, mutually exclusive), and how a relative `name` reaches a table or column, with one worked example
    - 12.2. The rule that deeper-than-source anchors are authored in the schema's folder
    - 12.3. Sharding guidance for schemas holding many granular concepts, noting stems stay freeform and are never decoded into identity
    - 12.4. The bootstrap note records the `0001` CHECK widening alongside the existing pre-launch schema edits

13. [completed] Update the example corpus README if it enumerates id forms - `readme/metadata-db-example-yamls/README.md`
    - 13.1. Add the granular concept form where the concept id forms are listed; skip if the README does not enumerate them

### Phase 5 — Adoption and rollout

14. [completed] Re-anchor the edwc_prd concepts per the anchoring criterion - `data_catalog/sources/edwc_prd/claims_vw_prd/concepts.yaml`
    - 14.1. Re-anchor the five single-column concepts to their columns: `claim_type_code` → `v_clm.clm_type_cd`, `claim_phases` → `v_clm.clm_phase_cd`, `srcsys_claim_status_codes` → `v_clm_srcsys.clm_crnt_stus_cd`, `final_action_indicator` → `v_clm.clm_fnl_act_ind`, `beneficiary_surrogate_key` → `v_mbr.mbr_sk`
    - 14.2. Re-anchor the five one-table concepts to table `v_clm`: `type_of_bill`, `claim_family_and_effective_key`, `original_claim_bundle`, `claim_change_actions`, `as_is_vs_as_was_reporting`
    - 14.3. Leave the eleven cross-table / cross-view concepts at schema level: `claim_table_taxonomy`, `four_part_claim_key`, `partition_filter_discipline`, `ss_claim_types_and_records`, `claim_product_deep_vs_wide`, `beneficiary_external_ids`, `provider_data_sources`, `npi_column_conventions`, `part_d_payment_composition`, `gpi_drug_hierarchy`, `ndc_reference_validity`
    - 14.4. Drop each re-anchored concept's anchor object from its `related_object_ids` where present (see decision 13)
    - 14.5. Update any reference that spells a re-anchored concept's full old id (other concepts' `related_object_ids`, docs); prose cross-references by leaf name are unaffected because leaves do not change
    - 14.6. Verify no old id survives: for each of the ten re-anchored names, `grep -rn "claims_vw_prd.concept.<name>" data_catalog/ readme/ docs/` returns nothing

15. [completed] Rebuild, load, and verify - `code/load_catalog_data/load_catalog_data.py`
    - 15.1. Dry-run the edited corpus and verify validation is clean; the diff against the possibly-stale shared database is informational only (see decision 4) — the authoritative check is the post-rebuild empty diff in 15.3
    - 15.2. Rebuild per the maintenance runbook: drop `catalog` and `reference`, apply both DDL streams, run the three grant scripts, load ref, load catalog
    - 15.3. Verify the post-rebuild dry-run reports 0 inserts / 0 updates / 0 deletes
    - 15.4. Verify retrieval both ways: a subtree query (`concept_id <@ '<schema>'::ltree`) returns the re-anchored rows, and the union query from Task 11.3 (anchor-prefix on `concept_id` unioned with `related_object_ids` containment) answers "which concepts are about `v_clm.clm_type_cd`?" and "which concepts are about `v_clm`?" correctly

## Key Data Decisions and Considerations

1. **Relative `name`, not a fully-qualified one** — the initial sketch was to spell the whole id in the `name` field. Every id in this corpus is path-derived from the file's location; making `concept_id` the one authored id creates a second source of truth that can disagree with the folder it sits in, which either forces a redundant agreement check or breaks the guarantee that a folder tells you what lives under it. Carrying only the extra depth in `name` keeps path-derivation intact and leaves every existing concept byte-identical.
2. **Dotted names only in a schema-scoped file (Task 1.7)** — a schema-scoped file is safe by construction, since its fixed prefix can only reach its own tables and columns. A data-source-scoped file could otherwise reach any object under the source, making a column concept legally authorable in two places and leaving "where are the concepts about schema X?" answerable only by grepping. This is the one optional rule in the plan: dropping it buys the ability to keep all of a source's concepts in one file, at the cost of that locality.
3. **Multiple concept files per schema already work** — `{schema}/concepts/{stem}.yaml` shards exist today with freeform stems, mutual exclusivity against the single `{schema}/concepts.yaml`, and cross-file duplicate-PK detection naming both files. Granular anchoring makes sharding by table attractive, which is why Task 12.3 documents it rather than the plan adding machinery.
4. **Backward compatibility is total and proved before adoption** — a `name` with no dots composes exactly today's id, so Phases 1-4 could ship without touching a concept. Task 10 makes that a gate, phrased as an identical before/after dry-run comparison rather than an empty diff: the shared database is known to lag the corpus (the pagila data source was removed and the ocs work landed since the last load), so an empty diff is the wrong expectation and would fail for reasons unrelated to this activity. If a full rebuild happens before Phase 3, the comparison degenerates to the original empty-diff check.
5. **The sas-data-resolution resolver is out of scope, and that is a recorded external dependency, not an oversight** — the resolver lives in the skills repo (`.claude/skills/sas-data-resolution/scripts/resolve_schema.py`), and per maintainer direction this activity does not touch it. Its `build_concepts_sql`/`concept_scope` match concept namespaces by equality against 1- and 2-segment scopes, so until it is updated in its own activity, table- and column-anchored concepts will not surface in SAS conversion resolutions — silently, with no error or log line. Two facts for that follow-up activity: (a) the fix must not be a naive subtree match, because the scope always contains the bare database prefix and `anchor <@ any(scope)` would pull in sibling schemas' concepts that today's equality match deliberately excludes — the correct rule is anchor equal to any scope entry, or strictly under a 2-segment (schema) scope entry; (b) its tests need a "sibling schema of an in-play database stays excluded" case, and while in the file, confirm the `prod.*` schema qualification still matches the deployment it queries. The catalog side of this activity is correct without the resolver change; the dependency only gates when granular concepts become visible to SAS conversions.
6. **Name validation is enumerated rather than delegated** — the existing segment validator covers charset, case, emptiness, and length for one segment at a time. Splitting a dotted name leaves the composition rules (stray dots, a reserved word in a non-leaf position, depth, which segment failed) without an owner unless this activity creates one, and a permissive parser produces ids that fail confusingly at write time or silently anchor somewhere unintended.
7. **Depth is capped at four labels in the loader as well as the constraint** — the catalog addresses data source, schema, table, and column, so a five-label anchor is not a thing; failing at authoring time with a clear message beats a raw CHECK violation at write time.
8. **`concept` stays reserved at every position** — already rejected as a schema name, a table name, and a concept leaf. A column named `concept` becomes shadowing for the first time under this change, hence the guards in Tasks 1.5 and 1.8. Verified: no column named `concept` exists anywhere in the current corpus, so the new guard is purely prospective and cannot break assembly of what is already authored.
9. **Anchor depth determines object kind (Task 2.2)** — resolution is an exact id lookup in the right space, so a column name that exists on a different table correctly fails. A permissive "does this id exist anywhere?" check would let a concept anchor to the wrong object kind, which is why Task 6.3 tests it.
10. **Editing `0001` rather than adding `0002`** follows the documented pre-launch exception: no production consumer, all data reproducible from YAML, and dev-phase policy already lands every corpus update via a full rebuild. After activation `0001` is immutable and the same change costs a migration plus a coordinated rollout. The shape CHECK exists only on `concepts` — `concepts_hstry` carries none — so Task 7 is a single constraint edit.
11. **Anchoring is organizational; retrieval is a two-lookup union** — the GiST indexes on `concept_id` and `related_object_ids` both exist today. Because a re-anchored concept no longer repeats its anchor in `related_object_ids` (decision 13), a `related_object_ids` containment lookup alone no longer answers "which concepts are about object X?" for the concept's own anchor; the contract is the union of anchor-prefix containment on `concept_id` and array containment on `related_object_ids`. Task 11.3 writes that contract down and Task 15.4 exercises it. The gain of anchoring is that a concept's home and id state what it is about, and that schema-level concept files stop being mixed-granularity dumping grounds; the functional gain is modest and the organizational one compounds as the corpus grows.
12. **The concept-versus-description boundary must be written down** or column concepts will duplicate column descriptions: the description says what the column is and travels with the column; a column-anchored concept is prose too long or too cross-cutting for a description, retrieved on its own.
13. **Whether a re-anchored concept still lists its anchor in `related_object_ids`** is convention, not enforcement — the loader rejects only a self-reference to the concept's own id. Task 14.4 drops it as redundant so the choice is deliberate, and the union retrieval contract (decision 11) is what keeps that drop safe for consumers.
14. **Re-anchoring changes a PK**, so it is a delete plus an insert rather than an update; the dev-phase rebuild makes that moot, which is a further reason to do the adoption pass now.
15. **The anchoring criterion and the full triage are explicit (Task 14)** — anchor at the narrowest object whose scope covers what the concept teaches: a concept about one column anchors at that column; a concept whose handles are several columns of one table anchors at that table; a concept spanning tables or teaching cross-view discipline stays at schema level. Applying it to all 21 edwc_prd concepts gives 5 column anchors, 5 table anchors on `v_clm`, and 11 schema-level stays — the full-criterion option confirmed by the maintainer. The judgment calls, made deliberately: `srcsys_claim_status_codes` anchors at the FISS status column even though it mentions the undocumented MCS/VMS analogue (`clm_pd_stus_cd` stays in `related_object_ids` as the documented gap); `partition_filter_discipline` stays at schema although both its columns sit on `v_clm`, because it teaches a filter-every-view-in-the-join discipline, not a fact about one table's rows; `part_d_payment_composition` stays at schema because its amounts span `v_clm_line` and `v_clm_line_rx`.
16. **Implementation record (2026-08-03)** — Tasks 1-15 are complete. Baseline (Task 10.1, captured on main before Phases 1-2): 63 YAML files, corpus of 23 concepts, validation passed, diff 0 inserts / 0 updates / 0 deletes; the post-change no-corpus-edit rerun (Task 10.2) was identical, so backward compatibility held as the empty-diff form of the gate (the shared DB was current, not stale as decision 4 anticipated). The Task 15.1 dry-run of the re-anchored corpus validates clean with diff 10 inserts / 0 updates / 10 deletes — exactly the ten re-anchored PKs as delete+insert (decision 14). Task 10.3 sweep found no other in-repo consumer decoding concept-id depth (`db_io.py` treats `concept_id` opaquely; grant scripts only name tables); the sas-data-resolution resolver remains the recorded external dependency (decision 5). Test runs: `test_corpus_assembly.py` 212 passed; `test_corpus_validation.py` 110 passed; `apply_ddl` unit tests 138 passed; full loader suite 709 passed / 19 env-gated integration skips; `test_example_corpus.py` 4 passed. The env-gated integration suite gained a depth-7 rejection case and a depth-5/6 acceptance test (runs under `METADATA_DB_INTEGRATION=1` against the rebuilt DB). Rebuild record (Tasks 15.2-15.4, 2026-08-03, local instance): the pre-rebuild database still carried the old `nlevel in (3, 4)` CHECK, so the re-anchored corpus could not have loaded there — the 15.1 dry-run passed only because it does not write. Both schemas were dropped and rebuilt per the maintenance runbook (catalog and ref DDL, the three grant scripts, ref load of 319 codes, catalog load); the live CHECK is now `nlevel >= 3 and <= 6`, and the post-rebuild dry-run reports 0 inserts / 0 updates / 0 deletes. Loaded concept depths: 1 at depth 3, 12 at depth 4, 5 at depth 5, 5 at depth 6; the schema subtree query returns all 21 view layer concepts. The union query for `v_clm.clm_type_cd` returns four concepts — `claim_type_code` (anchored at that column, reachable only via the `concept_id` leg), `claim_phases` (anchored at a sibling column, linked), and the schema-level `four_part_claim_key` and `partition_filter_discipline` (linked) — confirming both legs of the retrieval contract are required and that a `related_object_ids`-only consumer now misses the anchored concept (decision 5).
17. **Rebuild record (2026-08-03, maintainer-approved)** — the schema-scoped rebuild ran to completion after the maintainer approved the destructive step that had blocked the implementation pass: `drop schema catalog / reference cascade` as `metadata_db_maintainer`, `apply_ddl.py` with both configs (fresh `0001` checksums in both `ddl_versions` ledgers), the three grant scripts (`grant_catalog_ci.sql` / `grant_catalog_ci_ro.sql` with `-v schema=catalog -v database=metadata_db`, `grant_ref_ro.sql` with `-v schema=reference`), `load_ref_data.py` as maintainer (319 `clm_type_cd` rows), and a real `load_catalog_data.py` run (~23,800 columns / ~4,000 mappings / 23 concepts, one `load_audit` row). Task 15.3 gate: the post-rebuild dry-run reports 0 inserts / 0 updates / 0 deletes with clean validation. Task 15.4: the schema-subtree query returns all 21 edwc_prd concepts under their new ids (5 column-anchored, 5 table-anchored on `v_clm`, 11 schema-level), and the documented union query answers both probes correctly — `v_clm.clm_type_cd` returns `claim_type_code` (via the anchor leg) plus `claim_phases`, `four_part_claim_key`, and `partition_filter_discipline` (via the related leg); `v_clm` returns the ten anchored at or under the table plus the schema-level and cross-table linkers. `apply_ddl.py --check` passes for both configs, so the `check_schema_in_sync` CI job is green again.
18. **Output validation is the loader's own gates** — this activity changes catalog metadata and loader code rather than producing data files, so the wave-1/wave-2 validation, the example-corpus test (Task 9.2), the backward-compatibility gate (Task 10.2), and the post-rebuild empty diff (Task 15.3) serve as the output validation, as in the preceding metadata-db activities.
