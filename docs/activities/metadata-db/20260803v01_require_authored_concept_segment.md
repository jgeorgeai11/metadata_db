---
name: 20260803v01_require_authored_concept_segment
goal: Make a concept's `name` the literal relative id by requiring the author to write the reserved `concept` segment instead of the loader injecting it — `concept_id` becomes file prefix + `.` + `name`, byte for byte, at every anchor depth. Every composed id is unchanged, so the change is authoring-format only, proved by a 0-insert / 0-update / 0-delete dry-run against the loaded database.
created: 2026-08-03 11:06:32
updated: 2026-08-03 11:11:08
---

## Implementation Plan

1. [completed] Require the authored `concept` segment in concept names - `code/load_catalog_data/corpus_assembly.py`
   - 1.1. `_assemble_concept_row` composes `concept_id` as the file's path prefix + `.` + `name`, with no inserted segment; the `name` must contain exactly one `concept` segment, in the second-to-last position
   - 1.2. A `name` with no `concept` segment fails with a message that quotes the whole `name` and states the required form (`[{table}[.{column}].]concept.{leaf}`) (see decision 5)
   - 1.3. A `concept` segment anywhere other than second-to-last (as the leaf, as an anchor segment, or duplicated) fails with a message naming the offending position and quoting the whole `name`
   - 1.4. The existing rules keep their behavior and messages against the new grammar: per-segment identifier validation naming the failing segment, leading/trailing/doubled-dot rejection, and the 4-label anchor cap (labels before `concept`)
   - 1.5. In a data-source-scoped concepts file the `name` must be exactly `concept.{leaf}` (no anchor segments); the message directs the author to the schema's folder, as today
   - 1.6. Module comments that describe the old sandwich composition state the new rule (`concept_id` = prefix + `name`; the author writes `concept`)

2. [completed] Update the concept id composition prose in the discovery module - `code/load_catalog_data/yaml_discovery.py`
   - 2.1. The `RESERVED_CONCEPT_SEGMENT` comment and the module docstring's concepts entries state that the reserved segment is authored in `name`, not inserted, and show the id form at the four depths

3. [completed] Update the concept id composition prose in the data model - `code/load_catalog_data/data_model.py`
   - 3.1. The module docstring's concept-composition line and `ConceptRow`'s docstring state `concept_id` = file prefix + `.` + `name` and the required `concept`-second-to-last name form

4. [completed] Rewrite the concept-name tests for the authored-segment grammar - `code/load_catalog_data/unit_tests/test_corpus_assembly.py`
   - 4.1. Test valid names at every depth compose the expected ids: `concept.{leaf}` in a source-scoped and a schema-scoped file, `{table}.concept.{leaf}`, and `{table}.{column}.concept.{leaf}`
   - 4.2. Test the composed ids are byte-identical to the pre-change ids for representative live names (e.g. `concept.edw_naming_abbreviations` at source scope, `v_clm.clm_type_cd.concept.claim_type_code` at schema scope)
   - 4.3. Test each failure raises its specific message: no `concept` segment (a pre-change bare leaf), `concept` as leaf, `concept` as first of three segments, doubled `concept`, anchor segments in a source-scoped file, uppercase/illegal-charset/over-long segment, leading/trailing/doubled dot, over-deep anchor
   - 4.4. Run tests with `python -m pytest code/load_catalog_data/unit_tests/test_corpus_assembly.py -v`

5. [completed] Migrate the live concepts files to the authored form - `data_catalog/sources/edwc_prd/concepts.yaml`, `data_catalog/sources/edwc_prd/claims_vw_prd/concepts.yaml`, `data_catalog/sources/puf/hh_pps_lupa/concepts.yaml`
   - 5.1. Prefix the schema- and source-level names with `concept.` (the 11 schema-level edwc_prd names, `edw_naming_abbreviations`, `lupa_payment`) and insert `.concept` before the leaf of the 10 granular edwc_prd names — 23 names total, every composed id unchanged
   - 5.2. Rewrite each file's header comment to state the authored form (`concept_id` = folder prefix + `.` + `name`) instead of the injection convention

6. [completed] Migrate the example corpus and its README - `readme/metadata-db-example-yamls/data_catalog/sources/pagila/concepts.yaml`, `readme/metadata-db-example-yamls/data_catalog/sources/pagila/general/concepts.yaml`, `readme/metadata-db-example-yamls/README.md`
   - 6.1. Apply the same name migration and header-comment rewrite to both example concepts files
   - 6.2. Update the README where it shows the concept id forms or the `name` convention
   - 6.3. Run `python -m pytest code/load_catalog_data/unit_tests/test_example_corpus.py -v`

7. [completed] Update the concept authoring documentation - `readme/metadata-db-overview.md`
   - 7.1. The §2 concepts bullet, the `concepts` table's `concept_id` row, and §3.6 state the authored form: the `name` is the relative id (`[{table}[.{column}].]concept.{leaf}`) and `concept_id` = folder prefix + `.` + `name`, with nothing inserted

8. [completed] Update the maintenance doc's authoring conventions - `readme/metadata-db-maintenance.md`
   - 8.1. The concepts-authoring section and its worked example show the authored `concept` segment at each depth and the one composition rule

9. [completed] Verify ids are unchanged against the loaded database - `code/load_catalog_data/load_catalog_data.py`
   - 9.1. Run the full loader suite with `python -m pytest code/load_catalog_data/unit_tests/ -q`
   - 9.2. Run `uv run code/load_catalog_data/load_catalog_data.py --config code/load_catalog_data/config/load_catalog_data.toml --dry-run` and verify clean validation and a diff of 0 inserts / 0 updates / 0 deletes (see decision 4)

## Key Data Decisions and Considerations

1. **The author writes `concept`; the loader stops injecting it (maintainer decision, 2026-08-03)** — under 20260731v01 the loader sandwiched the reserved segment between the name's anchor segments and its leaf, making concepts the one file type whose authored text plus folder prefix did not equal its id. That injection broke round-tripping (the id's tail `{column}.concept.{leaf}` appeared nowhere in the YAML, defeating grep in both directions) and forced readers to parse anchor-vs-leaf by counting segments. With the segment authored, `concept_id` = folder prefix + `.` + `name` with no transform, the visible `concept` marker separates anchor from leaf, and validation flips from "reject `concept` anywhere" to "require exactly one, second-to-last" — the same single check, easier to teach.
2. **Uniform at every depth** — schema- and source-level names carry the `concept.` prefix too (`concept.claim_type_code`), even though they were previously a bare leaf. Keeping the common case dotless would preserve the injection for 13 of the 23 live concepts and make the rule "sometimes type it," recreating the confusion this activity removes. One rule, no exceptions: the name is the relative id.
3. **Full-id names were considered and rejected (maintainer-confirmed)** — spelling the entire `concept_id` in `name` maximizes grep-ability but duplicates the folder prefix on every row, requires a prefix-agreement check (a new failure mode policing a duplication we would have created), and makes folder renames edit every row to restate what the path already says. Definition sites in this corpus author only what the folder cannot supply; reference sites (`related_object_ids`, mappings) spell full ids. The relative form keeps that boundary.
4. **Ids are byte-identical, so this is authoring-format only** — no PK changes, no delete+insert diff, no DDL edit (the `concept_id` CHECK already pins the reserved segment second-to-last and is agnostic to who wrote it), no `corpus_validation` change (`_check_concept_anchors` parses composed ids, which are unchanged), no rebuild, and no effect on the recorded sas-data-resolution external dependency (it reads the database, not the YAML). Task 9.2's 0/0/0 dry-run against the loaded database is the machine proof.
5. **Loader and YAML must land in the same MR** — pre-change YAML (bare leaves) fails the new loader's missing-`concept` rule, and post-change YAML fails the old loader's reject-`concept` rule, so the code and the five concepts files are not separately mergeable. The missing-`concept` error message is the migration-facing surface and states the new form (Task 1.2).
6. **Inventory is complete and small** — three live concepts files (edwc_prd source root: 1 concept; edwc_prd Medicare view schema: 21; puf hh_pps_lupa: 1) and two example-corpus files; 23 live names plus the example names. No other file type or consumer decodes the `name` field (the 20260803 review sweep found none).
7. **Output validation is the loader's own gates** — the rewritten unit tests (Task 4), the example-corpus staging test (Task 6.3), the full suite (Task 9.1), and the 0/0/0 dry-run (Task 9.2) serve as the output validation, as in the preceding metadata-db activities.
8. **Implementation notes (2026-08-03)** — all gates green: 218 test_corpus_assembly tests, 4 test_example_corpus tests, full suite 715 passed / 19 skipped (the DB-bound skips), and the dry-run against the loaded database validated cleanly with 23 concepts assembled and a 0-insert / 0-update / 0-delete diff. Two small extensions beyond the task lists, both required for consistency: (a) overview §5 wave-1 rule 5 also described the old reject-`concept`-anywhere grammar, so it was rewritten to the require-exactly-one-second-to-last rule alongside Task 7's three spots; (b) the concepts fixtures in `test_load_catalog_data.py` and `test_integration.py` authored pre-change bare-leaf names (`name: claim`) and were migrated to `name: concept.claim` so the full suite passes under the new grammar (composed ids unchanged). The loader helper was renamed `_split_concept_name` → `_validate_concept_name` (it no longer splits; composition is the one rule `concept_id = prefix + "." + name`).
