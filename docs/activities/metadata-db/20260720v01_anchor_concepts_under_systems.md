---
name: 20260720v01_anchor_concepts_under_systems
goal: Drop the system-agnostic `data/concepts/` tree and anchor concepts under `data/systems/` at the data-source (`{system}/{db}/concepts.yaml`) and/or schema (`{system}/{db}/{schema}/concepts.yaml`) level, with a path-derived `concept_id` carrying a reserved `concept` segment. This removes the bespoke body-derived discovery/assembly branch and routes concept ownership to the owning system/schema team. No DDL change and no rebuild — `concept_id` stays an `ltree` PK, so the change lands with a loader edit and a reload.
created: 2026-07-20 09:40:06
updated: 2026-07-20 09:45:59
---

## Implementation Plan

> Ordering: loader discovery/assembly/model first (Phase 1) — they define how a
> concept file is found and how its `concept_id` is built — then validation
> (Phase 2), the data re-home + example (Phase 3), the per-module tests (Phase 4,
> authored against mocks / no live DB), the reload (Phase 5, the synchronization
> point), and docs/CI + review (Phases 6–7).
>
> No-rebuild rationale: `0001_initial_schema.sql`, `grant_metadata_db_ci.sql`,
> `data_model.ConceptRow`, `db_io`'s concepts SQL/params, and the shared registry
> are all **unchanged** — `concept_id` is an `ltree` PK regardless of how it is
> derived. Only *where* concept files live and *how* `concept_id` is composed
> change, confined to `yaml_discovery` + `corpus_assembly` (+ a validation touch).
> This supersedes Key Decision 2 of `20260718v01_add_concepts_table`.
>
> `concept_id` scheme: path prefix + reserved literal `concept` segment + a
> body-provided leaf `name`.
>   - data-source level: `{system}.{db}.concept.{name}`
>   - schema level:      `{system}.{db}.{schema}.concept.{name}`

### Phase 1 — Loader: discovery, assembly, model

1. [completed] Discover concepts as a path-anchored file type at the data-source and schema levels - `code/load_metadata_db/yaml_discovery.py`
   - 1.1. Remove the `data/concepts/` walk (and the optional-concepts-root handling) from `discover_yaml_files`; it walks only `data/systems/` again
   - 1.2. Revert `PathIdentity.system` to non-optional and remove the concepts-specific None allowances added in `20260718v01`; carry the concept's scope on `PathIdentity` (data-source-level = `system` + `database`, no `schema`; schema-level = `system` + `database` + `schema`)
   - 1.3. In `decode_path`, classify a `concepts.yaml` at `{system}/{db}/` as data-source-scoped and at `{system}/{db}/{schema}/` as schema-scoped (`file_type="concepts"`); a `concepts.yaml` at any other depth (system root, under `mappings/`) raises a path error
   - 1.4. Delete the concepts-first short-circuit and the `data/concepts/` containment logic (the `try/except ValueError` / `is_relative_to` block) added for the old tree

2. [completed] Derive `concept_id` from the path + reserved `concept` segment - `code/load_metadata_db/corpus_assembly.py`
   - 2.1. Change `_RECOGNIZED_KEYS["concepts"]` to `{name, label, definition, notes, update_reason}` — the leaf is a body `name`, not `concept_id` (now derived); a body `concept_id` (or `system`) is an unrecognized key
   - 2.2. Rewrite `_assemble_concepts` to build `concept_id` = the file's `PathIdentity` prefix + the literal `concept` segment + the body `name` (validated as a single `ltree` label), yielding `{system}.{db}.concept.{name}` or `{system}.{db}.{schema}.concept.{name}`; drop the body-derived-id path (it is no longer path-independent)
   - 2.3. Enforce the reserved-segment invariant here (where the `PathIdentity` is available): the segment preceding the leaf is the literal `concept`; a schema/table literally named `concept` that would shadow the namespace is a rejected error
   - 2.4. Concepts carry no body `system` field, so `_check_body_system` does not apply; path-agreement is intrinsic (the prefix is the file's folder). Wire concepts into the existing global `seen_keys` duplicate-`concept_id` detection and update the assembly-log count + any stale "system-agnostic" comments

3. [completed] Fix the stale module docstring - `code/load_metadata_db/data_model.py`
   - 3.1. `ConceptRow`, `TABLE_ORDER`, `PRIMARY_KEY_COLUMNS`, `CONTENT_COLUMNS` are unchanged. Reword the module docstring (prior-review minor): it currently says "seven derive dotted IDs from a `data/systems/` path via the builders," but `systems` uses a bare label and there are now eight tables incl. path-anchored `concepts` — state the accurate count/derivation

### Phase 2 — Validation

4. [completed] Validate path-derived `concept_id`s - `code/load_metadata_db/corpus_validation.py`
   - 4.1. Keep concepts in `_check_identifier_syntax` — every dot-delimited segment of the path-derived `concept_id` (including the reserved `concept` segment) is a valid `ltree` label
   - 4.2. Confirm (and note via comment) concepts remain exempt from `_check_references`, `_check_relationship_pairs`, `_check_mapping_disambiguation`, `_check_mapping_linkability`, `_check_sql_expressions`, `_check_join_type` — still freeform text, no FKs, no SQL; and that `validate_update_reason` still covers them generically

### Phase 3 — Re-home concepts + example

5. [completed] Re-home the three cross-system concepts and delete `data/concepts/` - `data/systems/warehouse/sandbox_ocs/concepts.yaml` (+ delete `data/concepts/`)
   - 5.1. Move `claim`, `beneficiary`, `claim_type` into a new data-source-level file `data/systems/warehouse/sandbox_ocs/concepts.yaml` — cross-system correspondence concepts anchor to the **source** system, mirroring how `column_mappings` live in the source folder. Bodies carry `name:` (leaf) instead of `concept_id:`; resulting ids `warehouse.sandbox_ocs.concept.{claim,beneficiary,claim_type}`. Definitions preserved verbatim
   - 5.2. Delete `data/concepts/concepts.yaml` and the now-empty `data/concepts/` tree

6. [completed] Add a schema-level demo concept - `data/systems/edw/sandbox_edw/claims_vw/concepts.yaml`
   - 6.1. One system-specific concept at the schema level — `name: final_action` defining EDW's `CLM_FNL_ACT_IND` semantics (id `edw.sandbox_edw.claims_vw.concept.final_action`) — so the corpus exercises **both** anchor levels

7. [completed] Update the concepts example YAML to the anchored format - `readme/metadata-db-example-yamls/data/systems/edw/sandbox_edw/claims_vw/concepts.yaml`
   - 7.1. Replace the old `.../data/concepts/concepts.yaml` example with a schema-level `concepts.yaml` under a system path (body `name` leaf, path-derived id), matching "one example of each YAML file type"; delete the old example path

### Phase 4 — Tests (one per changed module)

8. [completed] Update + run discovery tests - `code/load_metadata_db/unit_tests/test_yaml_discovery.py`
   - 8.1. Cover data-source- and schema-level `concepts.yaml` classification, rejection of a `concepts.yaml` at a bad depth, and `PathIdentity.system` populated for concepts; drop the removed `data/concepts/`-tree cases

9. [completed] Update + run assembly tests - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
   - 9.1. Cover path-derived `concept_id` at both levels (reserved `concept` segment, body `name` leaf), path-agreement, invalid-`name` rejection, unrecognized-key rejection (a body `concept_id`), the reserved-segment-shadow guard, and cross-file duplicate-`concept_id` detection

10. [completed] Update + run validation tests - `code/load_metadata_db/unit_tests/test_corpus_validation.py`
    - 10.1. A valid anchored concept passes; a malformed segment fails `_check_identifier_syntax`; concepts still participate in `validate_update_reason`

11. [completed] Update + run diff tests and split the bundled test - `code/load_metadata_db/unit_tests/test_corpus_diff.py`
    - 11.1. Update the concepts insert/update/delete case to the new `concept_id` values; split the single bundled insert/update/delete test into focused tests (prior-review minor)

12. [completed] Update + run data_model tests - `code/load_metadata_db/unit_tests/test_data_model.py`
    - 12.1. Model/registry unchanged; adjust any concept fixtures to the new path-derived ids

13. [completed] Update + run db_io tests - `code/load_metadata_db/unit_tests/test_db_io.py`
    - 13.1. Concepts SQL/params unchanged; adjust only the concept row fixtures' `concept_id` values where asserted

14. [completed] Update + run orchestrator tests and fix the SELECT-count comment - `code/load_metadata_db/unit_tests/test_load_metadata_db.py`
    - 14.1. Fix the `stub_connect` comment (prior-review minor): `read_db_state` issues **8** SELECTs (one per main table incl. `concepts`), not 7

15. [completed] Make the integration test concepts-aware for the new homing and fix its docstring - `code/load_metadata_db/unit_tests/test_integration.py`
    - 15.1. Move the lifecycle concept fixture to an anchored `concepts.yaml` (data-source or schema level) with a path-derived id and assert it round-trips; update the module docstring to include the fourth test (prior-review minor)

### Phase 5 — Reload & verify (no rebuild)

16. [completed] Reload the corpus and verify - loader-run, out-of-band
    - 16.1. Front-half check: discover → assemble → validate the whole `data/` corpus passes with no issues (no live DB)
    - 16.2. As `metadata_db_ci`, run `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml` — expect the 3 old concept ids deleted + the 3 re-homed + 1 schema-level demo inserted (net `concepts` count = 4), with the 3 superseded ids written to `concepts_hstry`
    - 16.3. A second `--dry-run` reports `0/0/0`; verify `concepts` holds exactly the 4 anchored ids (`warehouse.sandbox_ocs.concept.*` ×3 + `edw…concept.final_action`), and a subtree probe (`concept_id ~ 'warehouse.*.concept.*'`) resolves via the GiST index
    - 16.4. Full unit suite green at the 100%-coverage bar

### Phase 6 — Documentation & CI/config

17. [completed] Update the overview - `readme/metadata-db-overview.md`
    - 17.1. §3.5: replace the "system-agnostic / beside the technical constructs" framing with "concepts are anchored under their system at the data-source or schema level; cross-system correspondence concepts live in the source system's folder like `column_mappings`"
    - 17.2. §4 `concepts` table section + §2 bullet: `concept_id` is path-derived with a reserved `concept` segment (`{system}.{db}[.{schema}].concept.{name}`); drop the "body-derived, no path" wording

18. [completed] Update the maintenance/runbook doc - `readme/metadata-db-maintenance.md`
    - 18.1. Remove `data/concepts/` from the Repo layout tree; show `concepts.yaml` as an optional file at the data-source and schema levels; update the "YAML files" table row for concepts (path-derived id, `name` leaf)

19. [completed] Update CI trigger + ownership - `.gitlab-ci.yml` and `.gitlab/CODEOWNERS`
    - 19.1. `.gitlab-ci.yml`: drop the `data/concepts/**` `changes:` rule — concept edits now live under `data/systems/**`, already covered
    - 19.2. `.gitlab/CODEOWNERS`: remove the `data/concepts/` route; concepts route to their owning system/schema via the existing `data/systems/…` rules

### Phase 7 — Review

20. [pending] Code review of changed files and address findings - `docs/code_review/`
    - 20.1. Run the `code-review` skill over each changed code file (`yaml_discovery.py`, `corpus_assembly.py`, `corpus_validation.py`, `data_model.py`, and the changed test files), writing `cr_*.md`
    - 20.2. Address findings via `code-implementation`; re-run the suite at the 100%-coverage bar

## Key Data Decisions and Considerations

1. **Supersedes `20260718v01` Key Decision 2 (system-agnostic → anchored).** That activity made concepts the one path-independent file type. In practice most concepts are system-specific (code-value meanings, per-schema terms), so anchoring them under `data/systems/` routes ownership to the authoring team and removes the bespoke body-derived branch — this *simplifies* the loader (concepts become a normal path-anchored file type) rather than adding complexity.

2. **Two anchor levels: data source and schema.** A `concepts.yaml` may sit at `{system}/{db}/` (data-source-scoped) or `{system}/{db}/{schema}/` (schema-scoped), so a code-value definition can live beside its schema while a data-source-wide term lives one level up. Both are supported; neither is required.

3. **Reserved `concept` segment.** `concept_id` = `{system}.{db}[.{schema}].concept.{name}`. Concepts are a separate catalog, so there is no PK collision with `tables`/`columns` that share a segment count; the literal `concept` segment keeps the id readable and enables subtree queries (`{system}.*.concept.*`). Resolved: the segment word is **`concept`** (singular), confirmed by Task 16.3's `warehouse.*.concept.*` probe and the `warehouse.sandbox_ocs.concept.*` ids. Exposed as `yaml_discovery.RESERVED_CONCEPT_SEGMENT`.

4. **Cross-system concepts anchor to the source system.** `claim`, `beneficiary`, `claim_type` describe a OCS↔EDW correspondence with no single owning system; they anchor to the **source** (`warehouse/sandbox_ocs`, data-source level), exactly as `column_mappings` into EDW live in the OCS source folder. Definitions are unchanged; only the identity mechanism moves. This keeps "every concept is documented from some system's viewpoint" true and removes the need for a global tree.

5. **No DDL change, no rebuild.** The `concepts`/`concepts_hstry` tables, grants, `ConceptRow`, and `db_io` SQL are untouched — `concept_id` is an `ltree` PK regardless of derivation. The change is a loader edit plus a reload; the reload applies the id changes as deletes + inserts (the 3 old ids superseded into `concepts_hstry`).

6. **Output validation is the loader itself.** There is no standalone `data_val_*` script — the loader's discover/assemble/validate *is* the input validator, and the reload round-trip + dry-run `0/0/0` + the row-present/subtree checks (Task 16) are the output validation.

7. **Folds in the deferred `cr_20260719v01` review minors.** The five minor findings from the (deleted) concepts review are absorbed where the same files are edited: the `data_model` docstring (Task 3.1), the `test_load_metadata_db` SELECT-count comment (14.1), the `test_corpus_diff` bundled test (11.1), the `test_integration` docstring (15.1), and the `yaml_discovery` `try/except`→`is_relative_to` (moot — that block is removed in 1.4).

8. **Grouped by concern, not per-unit.** Like `20260717v01`/`20260718v01`, this modifies one cohesive system (the loader), so tasks are grouped into phases (discovery/assembly/model → validation → data → tests → reload → docs) rather than repeating create→test→run per file.

9. **Reserved-segment shadow guard split across three sites (Task 2.3 reconciled).** The "shadow" the guard prevents is a `concept_id` colliding with a genuine `table_id`/`column_id`, which can only happen when a **schema** is named `concept` (collides with a data-source-level id) or a **table** is named `concept` (collides with a schema-level id) — and those names live in `schema`-path segments / `tables.yaml`, not the concepts file. So `_assemble_concepts` (which sees only its own path) cannot catch them alone. Implemented as three guards: (a) `yaml_discovery.decode_path` rejects a schema path segment equal to `concept` (via `_validate_schema_segment`, covering both the schema-file and mappings branches); (b) `corpus_assembly._assemble_tables` rejects `table_name == "concept"`; (c) `corpus_assembly._assemble_concepts` rejects a body `name == "concept"` (a doubled `…concept.concept` leaf). Together these make Key Decision 3's "a schema or table named `concept` is rejected" literally true.

10. **Example YAML placed in the existing `sandbox/pagila` tree, not the literal `edw/...` path in the Task 7 title.** `readme/metadata-db-example-yamls/` is a self-contained fictional corpus (systems `sandbox` + `sandbox_warehouse`); adding an `edw/sandbox_edw/...` file would orphan a system with no `system.yaml`/`data_source.yaml`. The schema-level example concept was placed at `readme/metadata-db-example-yamls/data/systems/sandbox/pagila/public/concepts.yaml` (id `sandbox.pagila.public.concept.active_rental`), which demonstrates the schema-level anchor while keeping the example tree coherent. The real schema-level demo still lands at the planned `data/systems/edw/sandbox_edw/claims_vw/concepts.yaml` (Task 6).

11. **`PathIdentity.system` reverted to non-optional; redundant `assert ident.system is not None` lines removed.** With `system: str` again, the six `20260718v01`-added narrowing asserts in `corpus_assembly` are always-true and were dropped (defense against a `None` system is no longer meaningful).

12. **DDL comments left stale intentionally (no DDL change / no rebuild).** `code/apply_ddl/ddl/0001_initial_schema.sql` still calls `concepts` "system-agnostic / body-derived" (incl. a live `COMMENT ON` value). That migration is already applied and immutable; correcting the wording would require a new migration and a rebuild, which this activity explicitly avoids (Key Decision 5). Left for a future DDL migration if desired.

13. **Reload performed and verified against the live `metadata_db` (Task 16).** Front-half (discover→assemble→validate) of the whole `data/` corpus passed (4 concepts). The real load as `metadata_db_ci` committed **4 inserts / 0 updates / 3 deletes** (the 3 old bare-id concepts deleted → archived to `concepts_hstry`; the 3 re-homed `warehouse.sandbox_ocs.concept.*` + 1 schema-level `edw…concept.final_action` inserted; net `concepts` = 4). A second dry-run reported `0/0/0`; the `concepts` table holds exactly the 4 anchored ids and the `warehouse.*.concept.*` lquery subtree probe resolves 3 rows via the GiST index. (`concepts_hstry` could not be read back to confirm the 3 superseded ids — the CI role is INSERT-only on `_hstry` tables by design.) Full unit suite green with every loader source module at 100% coverage.
