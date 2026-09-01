---
name: 20260728v01_add_corpus_shard_folders
goal: Let a schema's row-list YAML files be optionally split into folders of shard files — `tables/`, `columns/`, `table_relationships/`, and `concepts/` as alternatives to the single `<type>.yaml` — mirroring the existing `mappings/{name}.yaml` pattern. Shard filenames are freeform grouping labels the loader never decodes; the single-file and folder forms are mutually exclusive per schema and type. Motivated by the EDW view layer corpus (~1,000 views / ~19k columns in one physical schema), whose generated YAML will be sharded by subject area.
created: 2026-07-28 10:15:44
updated: 2026-07-28 10:44:05
---

## Implementation Plan

### Phase A — Loader

1. [completed] Extend the path grammar with the shard-folder shapes and reserved segments - `code/load_metadata_db/yaml_discovery.py`
   - 1.1. Recognize the folder forms in `_decode_parts`, classifying to the same `FileType` and identity fields as their single-file equivalents: `{label}/{schema}/tables/{stem}.yaml`, `{label}/{schema}/columns/{stem}.yaml`, `{label}/{schema}/table_relationships/{stem}.yaml`, `{label}/{schema}/concepts/{stem}.yaml` (schema-level concepts), and `{label}/concepts/{stem}.yaml` (data-source-level concepts, `schema_name=None`)
   - 1.2. Validate each shard filename stem as an identifier segment (charset + length), exactly as `mappings/{name}.yaml` stems are validated today; the stem is a grouping label only — never decoded into any identity (see Decisions #2)
   - 1.3. Reserve the four folder names as schema path segments: a schema literally named `tables`, `columns`, `table_relationships`, or `concepts` is rejected with a dedicated message (mirroring the existing `concept` and `mappings` reservations) — without this, `{label}/concepts/{stem}.yaml` is ambiguous with a schema named `concepts`
   - 1.4. Reject a shard folder at the wrong depth with a dedicated error naming the correct location (e.g. `{label}/columns/{stem}.yaml` — columns folders live under a schema; only `concepts/` is valid at the data-source level), mirroring the existing wrong-depth `mappings/` error
   - 1.5. Record on the returned `PathIdentity` whether the file came from a shard folder (needed by the Task 3 mutual-exclusion rule); keep the wrong-extension guard working for the new shapes (it re-derives classification via `_decode_parts`, so a `columns/bene.yml` or `columns/bene.YAML` at a recognized location must fail wave 1 like any mis-extensioned corpus file)
   - 1.6. Update the module docstring's grammar listing and the `decode_path`/`discover_yaml_files` docstrings to the extended grammar

2. [completed] Create and run tests for the grammar extension - `code/load_metadata_db/unit_tests/test_yaml_discovery.py`
   - 2.1. Each of the five folder shapes classifies to the right `FileType`/`database_name`/`schema_name`; stems are charset/length-validated; `schema.yaml`, `data_source.yaml`, and `deployments.yaml` have no folder form (a `schema/` or `deployments/` folder is an unrecognized location)
   - 2.2. Reserved schema names (`tables`, `columns`, `table_relationships`, `concepts`) rejected with the dedicated message; wrong-depth shard folders rejected naming the correct location
   - 2.3. Wrong-extension and case-variant files inside shard folders (`columns/bene.yml`, `columns/bene.YAML`) surface as wave-1 issues; a case-variant folder name (`Columns/`) fails loudly rather than silently skipping its contents
   - 2.4. Existing single-file shapes still classify unchanged (regression)
   - 2.5. Run with `uv run pytest code/load_metadata_db/unit_tests/test_yaml_discovery.py -v`

3. [completed] Add the mutual-exclusion rule and confirm shard-aware assembly semantics - `code/load_metadata_db/corpus_assembly.py`
   - 3.1. Wave-1 rule: for a given scope and file type, the single-file form and the folder form may not both be present — one issue per offending (type, scope) pair naming both paths (e.g. `{label}/{schema}/columns.yaml` and `{label}/{schema}/columns/`); scopes are the schema level for `tables`/`columns`/`table_relationships`/schema-level `concepts`, and the data-source level for data-source-level `concepts`
   - 3.2. Rows from multiple shards of one type union exactly as today (assembly is already multi-file: keyed dicts + `_record` duplicate-PK detection naming both files); add/adjust docstrings and the module docstring so the union-across-shards behavior is documented rather than incidental
   - 3.3. Confirm and document the cascade-suppression semantics for sharded `tables/`: a shard file that fails to parse marks the schema's table inventory as incomplete for deployment-expansion suppression (`_record_file_failure`), same as a broken single `tables.yaml`
   - 3.4. No other assembly changes: per-row assemblers, recognized-keys checks, and concept-id derivation from `PathIdentity` fields are form-agnostic and must remain untouched

4. [completed] Create and run tests for shard-aware assembly - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
   - 4.1. A schema's columns split across `columns/clm.yaml` + `columns/bene.yaml` assembles identically to the equivalent single `columns.yaml` (same corpus rows); same for a two-shard `tables/`, `table_relationships/`, and a data-source-level `concepts/`
   - 4.2. Both forms present for one (type, scope) raises the mutual-exclusion issue naming both paths; a folder for one type alongside single files for the others is legal (per-type independence)
   - 4.3. The same PK defined in two shards records the duplicate-PK issue naming both shard files; a misfiled row (a `bene` table's column in `columns/clm.yaml`) assembles cleanly (documented as convention-only — see Decisions #2)
   - 4.4. A broken shard in `tables/` suppresses the deployment-expansion cascade exactly like a broken `tables.yaml`
   - 4.5. Run with `uv run pytest code/load_metadata_db/unit_tests/test_corpus_assembly.py -v`

5. [completed] Run the loader in dry-run mode against the live corpus and DB (run)
   - 5.1. `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml --dry-run` from the repo root, after Tasks 1–4 land
   - 5.2. Confirm a clean, empty diff — the live corpus uses only single-file forms, so this change must be a pure no-op against it

### Phase B — Documentation and example corpus

6. [completed] Update the overview doc - `readme/metadata-db-overview.md`
   - 6.1. §5 rule 1 (file placement): add the five folder shapes as recognized locations, and state the mutual-exclusion rule (single file or folder per type and scope, never both)
   - 6.2. §5 rule 5 (reserved segments): extend from `concept` to also reserve `tables`, `columns`, `table_relationships`, `concepts`, and `mappings` as schema names, with the rationale (folder-form ambiguity)
   - 6.3. State that shard filename stems are grouping labels (charset-validated, never decoded, not stored) — the same contract `mappings/{name}.yaml` already documents

7. [completed] Update the maintenance doc - `readme/metadata-db-maintenance.md`
   - 7.1. Repo layout: show the folder form as an optional alternative under a schema (a `columns/` folder of shards in the tree sketch, marked optional)
   - 7.2. YAML-files table: for the four row-list types, note "one file per schema, or a `<type>/` folder of shard files — the two forms are mutually exclusive; shard filenames are grouping labels (by convention the subject area)"
   - 7.3. Change lifecycle: a bullet on when to use the folder form (large generated schemas), that re-sharding is identity-neutral (moving rows between shards produces an empty loader diff), and that which-rows-in-which-shard is an authoring convention enforced by review, not by the loader
   - 7.4. Remove or update the "Other things to consider" bullet if it mentions single-file constraints affected by this change

8. [completed] Demonstrate the folder form in the example corpus - `readme/metadata-db-example-yamls/`
   - 8.1. Convert one schema's columns to the folder form (split `data/sources/mart/analytics/columns.yaml` into `columns/` shards grouped by table or area) so the example corpus demonstrates both forms; keep every other type single-file
   - 8.2. Update the example README's file-inventory and field-legend text for the new layout

9. [completed] Create and run a test that stages the example corpus - `code/load_metadata_db/unit_tests/test_example_corpus.py`
   - 9.1. New test module that runs `discover_yaml_files` → `assemble_corpus` → `validate_corpus` against `readme/metadata-db-example-yamls/data` and asserts zero discovery/assembly/validation issues — no such committed coverage exists today (the example corpus has only ever been verified ad hoc), so this closes that gap while pinning the Task 8 layout
   - 9.2. Assert the folder form is genuinely exercised: at least one classified identity comes from a `columns/` shard folder
   - 9.3. Run with `uv run pytest code/load_metadata_db/unit_tests/test_example_corpus.py -v`

## Key Data Decisions and Considerations

1. **Folder form is uniform across the four row-list types; `schema.yaml`, `data_source.yaml`, and `deployments.yaml` are excluded** — those three are single-row or single-purpose files where sharding is meaningless. One rule to learn: "a `<type>/` folder is a split `<type>.yaml`". `mappings/` keeps its existing folder-only grammar unchanged.
2. **Shard filename stems are freeform grouping labels — the loader never validates a row against its shard file** (maintainer decision, 2026-07-28). This matches the documented `mappings/` philosophy ("a grouping convention, not decoded by the loader"). Recorded consequence: a misfiled row (a CLM column in `bene.yaml`) loads silently and correctly; filing consistency is a convention + MR-review concern. For the EDW corpus the shards will be generated by subject-area prefix, so consistency comes from the generator. No per-table stem enforcement now or later unless drift proves to be a real problem.
3. **Mutual exclusion is a hard wave-1 error, not a merge** — allowing `columns.yaml` and `columns/` to coexist would invite split-brain authoring (some rows here, some there, each form individually valid). One form per (type, scope).
4. **Four new reserved schema-name segments** — `tables`, `columns`, `table_relationships`, `concepts` join `concept` (and the wrong-depth-guarded `mappings`) as names a schema may not take, because the folder grammar makes them ambiguous at the schema-segment position. No existing or anticipated schema uses these names; the restriction is permanent and documented in overview §5 rule 5.
5. **Assembly is already multi-file — the blast radius is small** — `assemble_corpus` unions identities into keyed dicts with cross-file duplicate-PK detection naming both files (built for `mappings/`); no single-file-per-schema assumption exists. The loader work concentrates in `yaml_discovery._decode_parts` (path grammar) plus the one new mutual-exclusion rule in assembly.
6. **Case handling on this filesystem** — Windows resolves names case-insensitively, so the grammar-side handling mirrors the existing wrong-extension approach: a case-variant shard folder (`Columns/`) or shard extension (`bene.YML`) must fail loudly at wave 1, never silently skip. The wrong-extension guard already re-derives classification through `_decode_parts`, so the new shapes inherit it; tests pin it.
7. **No DDL, no DB change, no rebuild** — this is loader + docs only. Row identity is field-derived and the diff is content-based, so adopting the folder form (or re-sharding later) for an already-loaded schema produces an empty diff; Task 5's live dry-run pins the no-op guarantee for the current corpus.
8. **`readme/metadata-db-data-authoring-rules.md` is updated separately** — that doc currently lives on the unmerged `puf-data-val` branch, so this activity does not touch it (avoiding a cross-branch conflict); after that branch merges, add a short "when to split into folders" note there matching Task 7.3's guidance.
9. **Motivation and sequencing** — this lands before any EDW view layer YAML is generated (~1,000 views / ~19k columns / ~6.4 MB as a single `columns.yaml`), so the generated corpus can be authored sharded by subject area (`V_<AREA>` prefixes, ~40 shards) from the start. The EDW generation itself is a separate future activity.
10. **CI is unaffected** — the pre-merge dry-run and post-merge load run the same loader; no pipeline or credential changes are involved.
11. **No committed test stages the example corpus today** (verified 2026-07-28: nothing under `code/` references `metadata-db-example-yamls`) — its past "assembles cleanly" verifications were ad-hoc loader runs. Task 9 turns that into permanent regression coverage; `validate_corpus` needs no DB connection, so the test runs in the plain unit suite.
12. **Implementation notes (2026-07-28, all tasks completed):**
    - 12.1. **`concepts.yaml` reserved-filename refinement** — the reserved-filename rule got one shard-aware nuance: `concepts.yaml` inside a `concepts/` shard folder is a valid shard (both readings are concepts — no type confusion), while inside `mappings/` or a *non-concepts* shard folder (`columns/concepts.yaml`) it stays the "unsupported depth" error, so a misplaced concepts file can never silently load as another type's rows. Pinned by tests.
    - 12.2. **Wrong-depth vs. reserved-name error boundary** — `{label}/<tables|columns|table_relationships>/{stem}.yaml` (exact 3-segment shape) gets the dedicated wrong-depth error; the reserved-schema-name message (`_validate_schema_segment`) fires where the segment is unambiguously at the schema position (e.g. `{label}/tables/mappings/x.yaml`, `{label}/concepts/tables/x.yaml`). Both messages are reachable through `decode_path` and tested.
    - 12.3. **Task 5 result** — live dry-run after Tasks 1–4: 69 YAML files discovered, 0 classification issues, diff 0 inserts / 0 updates / 0 deletes (pure no-op confirmed).
    - 12.4. **Task 7.4 finding** — neither "Other things to consider" bullet mentions single-file constraints affected by this change; both left unchanged.
    - 12.5. **Task 8 shape** — `mart/analytics` has a single table (`film_performance`), so its `columns.yaml` was split by subject area (`columns/identity.yaml` + `columns/metrics.yaml`) rather than by table, keeping a genuine two-shard union in the example.
    - 12.6. **Coverage** — full unit suite 664 passed / 17 skipped; `yaml_discovery.py` 100%, new `corpus_assembly._shard_form_conflicts` fully covered (the 5 uncovered `corpus_assembly` lines are pre-existing deployment-expansion branches).
