---
name: 20260720v01_add_relationship_cardinality_and_concept_links
goal: Replace `table_relationships.join_type` with `table_relationships.cardinality` (an optional, loader-validated enum recording the a→b row correspondence) and add `concepts.related_object_ids` (an optional, loader-validated `ltree[]` of catalog object ids a concept is about). Cardinality records the grain fact a join predicate alone cannot express — while `join_type` recorded an analytical choice that belongs to the consuming query, so it is dropped; related-object links give concepts deterministic anchors so retrieval can mechanically pull the concepts attached to the objects in a query instead of depending on prose spelling ids exactly. Lands as a pre-launch `0001` edit + rebuild (bootstrap exception; sandbox-only data), also absorbing the stale concepts DDL comments deferred by `20260720v01_anchor_concepts_under_systems` Key Decision 12.
created: 2026-07-20 11:43:52
updated: 2026-07-20 12:35:00
---

## Implementation Plan

> Ordering: DDL first (Phase 1) — it fixes the column shapes and positions every
> later layer must agree with — then the loader model/assembly (Phase 2),
> validation (Phase 3), DB I/O (Phase 4), data + example YAMLs (Phase 5), the
> per-module tests (Phase 6, authored against mocks / no live DB), the rebuild +
> reload (Phase 7, the synchronization point), and docs + review (Phases 8–9).
>
> Column semantics being changed:
>   - `table_relationships.cardinality` (ADDED) — nullable text enum
>     `one_to_one | one_to_many | many_to_one | many_to_many`, read **a→b**
>     (e.g. `many_to_one` = many `table_a` rows match one `table_b` row).
>     NULL = not yet recorded (never guessed via a default). Validated by the
>     loader pre-merge (case-sensitive) and by a DB CHECK.
>   - `table_relationships.join_type` (REMOVED) — it recorded which rows a
>     consumer should keep (INNER/LEFT/…), an analytical choice that depends on
>     the consuming query, not a fact about the data. The fact `LEFT` used to
>     imply — unmatched rows exist on a side — is carried by `cardinality`
>     plus `notes`. A YAML row still carrying `join_type:` fails loudly as an
>     unrecognized key (Decision F), forcing explicit cleanup.
>   - `concepts.related_object_ids` (ADDED) — nullable `ltree[]` of ids the
>     concept is about; each entry must resolve to an existing `systems` /
>     `data_sources` / `schemas` / `tables` / `columns` / `concepts` PK in the
>     corpus. Authored in YAML (unlike the loader-derived
>     `column_mappings.target_tables_referenced`), order preserved, duplicates
>     and self-references rejected. GiST-indexed (`gist__ltree_ops`) so "which
>     concepts reference object X?" runs as an array-containment lookup.

### Phase 1 — DDL (pre-launch `0001` edit; no new migration)

1. [completed] Swap `join_type` for `cardinality`, add the concepts array + GiST index, and refresh stale concepts comments - `code/apply_ddl/ddl/0001_initial_schema.sql`
   - 1.1. `table_relationships`: drop the `join_type` column and its CHECK; add `cardinality text` in its place with `check (cardinality in ('one_to_one', 'one_to_many', 'many_to_one', 'many_to_many'))` (NULL passes the CHECK — unknown is allowed); add an inline comment stating the a→b reading, that the reverse orientation is derivable because the unordered pair is unique per `relationship_name`, and that join-type selection (inner vs. outer) is the consumer's per-query choice informed by cardinality and `notes`
   - 1.2. `table_relationships_hstry`: mirror the swap (drop `join_type`, add `cardinality text`; no CHECK on the mirror, matching `validated_ts` precedent)
   - 1.3. `concepts`: add `related_object_ids ltree[]` between `notes` and `update_reason`, with an inline comment (authored links to the catalog objects the concept is about; loader-validated to resolve; retrieval anchor for RAG)
   - 1.4. `concepts_hstry`: mirror `related_object_ids ltree[]` in the same position
   - 1.5. Add `create index idx_concepts_related_objects_gist on concepts using gist (related_object_ids gist__ltree_ops)` beside the existing `idx_column_mappings_target_tables_gist`, with the same "plain gist does not serve array containment" comment
   - 1.6. Update the `comment on table` values for `table_relationships` (cardinality in, join_type out) and `concepts` (mention related_object_ids, and fix the stale "system-agnostic / body-derived" wording deferred by the prior activity's Key Decision 12)
   - 1.7. `grant_metadata_db_ci.sql` needs no change (grants are table-level)

### Phase 2 — Loader: model + assembly

2. [completed] Extend the row dataclasses and diff registry - `code/load_metadata_db/data_model.py`
   - 2.1. `TableRelationshipRow`: remove `join_type`; add `cardinality: str | None` in its position, matching the DDL/SELECT column order (rows are built positionally in `db_io`); keep `validated_ts` last with its default
   - 2.2. `ConceptRow`: add `related_object_ids: tuple[str, ...]` between `notes` and `update_reason` (tuple, not list, so the frozen dataclass stays hashable — mirroring `ColumnMappingRow.target_tables_referenced`); update the class docstring's field-order note
   - 2.3. `CONTENT_COLUMNS`: for `table_relationships` remove `join_type` and add `cardinality`; for `concepts` add `related_object_ids` — so changes to either new field diff as updates
   - 2.4. `PRIMARY_KEY_COLUMNS` and `TABLE_ORDER` are unchanged

3. [completed] Accept the new body keys (and stop accepting `join_type`) in assembly - `code/load_metadata_db/corpus_assembly.py`
   - 3.1. `_RECOGNIZED_KEYS`: for `table_relationships` remove `join_type` and add `cardinality` — a YAML row still carrying `join_type:` is rejected as an unrecognized key naming the offending key and file (the deliberate loud-failure path for stale YAML); for `concepts` add `related_object_ids`
   - 3.2. `_assemble_table_relationships`: remove the `join_type` read and its `INNER` default; read optional `cardinality` (string or null; enum membership is validated in Phase 3)
   - 3.3. `_assemble_concepts`: read optional `related_object_ids` (must be null or a list of strings — a non-list or non-string entry is a `ValueError` naming the file and row); coerce to a tuple preserving author order; reject duplicate entries within a row and an entry equal to the row's own `concept_id` (self-reference) here where the row context is at hand

### Phase 3 — Validation

4. [completed] Validate the enum and resolve the links - `code/load_metadata_db/corpus_validation.py`
   - 4.1. Remove `_VALID_JOIN_TYPES` and `_check_join_type`; add `_VALID_CARDINALITIES` and a `_check_cardinality` rule on the same pattern: a non-null `cardinality` must be one of the four values (case-sensitive), reported per-row so the pre-merge dry-run fails before the DB CHECK would
   - 4.2. Add `_check_concept_related_objects`: every entry of every concept's `related_object_ids` must resolve to a PK defined in the corpus across the six id-keyed tables (`systems`, `data_sources`, `schemas`, `tables`, `columns`, `concepts`); an unresolved entry is an issue naming the concept and the offending id, with a case-mismatch "did you mean …?" hint (generalize or parallel `_case_mismatch_hint`, which is currently columns-only)
   - 4.3. Wire both rules into `validate_corpus` and update the module docstring / the concepts-exemption comment in `_check_identifier_syntax` (concepts now carry one referential check; they remain exempt from SQL/pair/disambiguation rules)

### Phase 4 — DB I/O

5. [completed] Carry the column swap and the new array through every SQL path - `code/load_metadata_db/db_io.py`
   - 5.1. `_SELECT_TABLE_RELATIONSHIPS`: replace `join_type` with `cardinality` (positional build must keep matching `TableRelationshipRow` field order)
   - 5.2. `_SELECT_CONCEPTS`: add `related_object_ids::text[]` between `notes` and `update_reason` (the `::text[]` cast mirrors `target_tables_referenced` so psycopg2 returns a Python list); in `read_db_state`, build `ConceptRow` with `tuple(x or ())` null-handling like the column-mappings read
   - 5.3. INSERT/UPDATE for `table_relationships`: swap `join_type` for `cardinality` in the SQL and in `_insert_params` / `_update_params`
   - 5.4. INSERT/UPDATE for `concepts`: add `related_object_ids` with the `%s::ltree[]` cast and `list(row.related_object_ids)` binds, mirroring column mappings; an empty tuple writes as an empty array (or NULL — pick one and keep read/write symmetric so diff idempotency holds)
   - 5.5. `_HSTRY_INSERT_TABLE_RELATIONSHIPS` and `_HSTRY_INSERT_CONCEPTS`: apply the same swap/addition to the mirrored column lists

### Phase 5 — Data + example YAMLs

6. [completed] Swap `join_type` for `cardinality` on the sandbox relationships - `data/systems/{warehouse/sandbox_ocs/general,edw/sandbox_edw/claims_vw,sandbox/pagila/public}/table_relationships.yaml`
   - 6.1. In all three files, remove every `join_type:` line and add `cardinality`, derived from the join semantics already stated in each row's `notes` (e.g. EDW `clm_line`→`clm` and `clm`→`bene` are `many_to_one`); move any row-keeping guidance a `join_type` implied (e.g. "left-join to keep claims without a bene row") into `notes`; `update_reason` stays null — after the Phase 7 rebuild every row is a fresh insert

7. [completed] Link the sandbox concepts to their objects - `data/systems/warehouse/sandbox_ocs/concepts.yaml` and `data/systems/edw/sandbox_edw/claims_vw/concepts.yaml`
   - 7.1. Populate `related_object_ids` on all four concepts with the ids their definitions already name inline — e.g. `claim` → the OCS `clm` table + `claim_no`/`person_key` columns, the EDW `clm` table, and the EDW `final_action` concept; `final_action` → the EDW column it defines. Links are anchors for retrieval, not a replacement for naming objects in the prose (see Key Decision 4)

8. [completed] Update the example YAMLs - `readme/metadata-db-example-yamls/data/systems/`
   - 8.1. `sandbox/pagila/public/table_relationships.yaml` and `sandbox_warehouse/mart/analytics/table_relationships.yaml`: remove `join_type` lines and legend entries; add `cardinality` to every row, with the field-legend comment documenting the a→b reading, the four values, and that join-type selection is the consumer's per-query choice
   - 8.2. `sandbox/pagila/public/concepts.yaml`: add `related_object_ids` to the `active_rental` example, referencing only ids that exist in the example corpus (it is validated as a corpus by tests), with a legend comment stating optionality and the resolution rule

### Phase 6 — Tests (one per changed module)

9. [completed] Update + run data_model tests - `code/load_metadata_db/unit_tests/test_data_model.py`
   - 9.1. Cover the `CONTENT_COLUMNS` changes (a cardinality-only or links-only change is an update, not a no-op; `join_type` is gone from the set) and the dataclass field changes; keep the DDL/dataclass PK-agreement test green

10. [completed] Update + run assembly tests - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
    - 10.1. Cover: `cardinality` read through (present, absent → None); a row carrying `join_type:` rejected as an unrecognized key; `related_object_ids` read through (present, absent → empty tuple); rejection of a non-list `related_object_ids`, a non-string entry, a duplicate entry, and a self-reference; remaining unknown keys still rejected

11. [completed] Update + run validation tests - `code/load_metadata_db/unit_tests/test_corpus_validation.py`
    - 11.1. Cover: valid cardinality values pass; an invalid value (`1_to_many`, `MANY_TO_ONE`) fails with the enum named; null passes; the old `_check_join_type` cases are removed. `related_object_ids` entries resolving to each of the six id spaces pass; an unresolved id fails naming the concept and entry; a case-only mismatch gets the "did you mean" hint

12. [completed] Update + run diff tests - `code/load_metadata_db/unit_tests/test_corpus_diff.py`
    - 12.1. A cardinality change and a related_object_ids change each classify as an update; identical values remain a no-op (idempotency); relationship fixtures lose `join_type`

13. [completed] Update + run db_io tests - `code/load_metadata_db/unit_tests/test_db_io.py`
    - 13.1. Cover the relationship column swap and the concepts array in SELECT row-building (including the `::text[]`→tuple coercion and empty/NULL array handling), insert/update bind params (including the `::ltree[]` cast and list conversion), and the `_hstry` mirrored column lists

14. [completed] Update + run orchestrator tests - `code/load_metadata_db/unit_tests/test_load_metadata_db.py`
    - 14.1. Update the staged corpus: relationship rows carry `cardinality` (no `join_type`), the concept carries `related_object_ids`; assert both flow through to the diff handed to `apply_diff`

15. [completed] Extend the integration round-trip - `code/load_metadata_db/unit_tests/test_integration.py`
    - 15.1. Round-trip both new fields against the live schema (gated by `METADATA_DB_INTEGRATION=1`): cardinality survives load→read→re-diff as a no-op; `related_object_ids` writes as `ltree[]` and reads back equal; a containment probe (`related_object_ids @> '{…}'::ltree[]`) returns the linking concept; relationship fixtures lose `join_type`

### Phase 7 — Rebuild & reload (out-of-band; the synchronization point)

16. [completed] Rebuild the database on the edited `0001` and reload the corpus - loader-run, out-of-band
    - 16.1. Front-half check first: discover → assemble → validate the whole `data/` corpus passes with the new fields populated and no `join_type` keys remaining (no live DB)
    - 16.2. Rebuild per the maintenance runbook "Creating or rebuilding the database": as the `CREATEDB` role, `DROP DATABASE … WITH (FORCE)` / `CREATE DATABASE` / `ALTER … OWNER TO metadata_db_maintainer`; as `metadata_db_maintainer`, `apply_ddl.py` (the edited `0001` builds the new shape into `prod`), then `grant_metadata_db_ci.sql` with `-v schema=prod -v database=metadata_db`
    - 16.3. As `metadata_db_ci`, run the loader for real — every row is an insert into the empty rebuild (`update_reason` null throughout; the mass-delete guard sees zero deletes); then a second `--dry-run` reports `0/0/0`
    - 16.4. Verify: `table_relationships` has no `join_type` column, `cardinality` is populated on the sandbox rows, and the CHECK rejects an out-of-enum manual value; `concepts.related_object_ids` populated on the four concepts and the containment probe (e.g. `related_object_ids @> '{edw.sandbox_edw.claims_vw.clm}'::ltree[]`) resolves via `idx_concepts_related_objects_gist`
    - 16.5. Full unit suite green at the 100%-coverage bar

### Phase 8 — Documentation

17. [completed] Update the overview - `readme/metadata-db-overview.md`
    - 17.1. §4 `table_relationships`: remove the `join_type` row; add the `cardinality` row (nullable enum, a→b reading, four values, reverse orientation derivable via the unordered-pair rule, validated pre-merge + DB CHECK, NULL = not recorded); scrub the remaining `join_type` mentions (the §2 bullet, the loader-checks summary in §4) and state that inner/outer selection is the consumer's per-query choice informed by cardinality and `notes`
    - 17.2. §4 `concepts`: add the `related_object_ids` row and **supersede the "Structured `related_objects` links were deliberately left out" paragraph** — the RAG/embeddings consumer is the "real need" that paragraph anticipated; state the drift answer (loader-validated resolution, so a broken link fails the load exactly like any FK) and the boundary (links are retrieval anchors; the definition prose still names objects inline)
    - 17.3. §2 bullet list: mention cardinality under join relationships and object links under concepts, one clause each

18. [completed] Update the maintenance/runbook doc - `readme/metadata-db-maintenance.md`
    - 18.1. Change-lifecycle step 2: the relationships bullet replaces "Choose a valid `join_type`" with "optionally record `cardinality` (a→b)"; the concepts bullet gains "optionally list `related_object_ids`, each resolving to a cataloged object"
    - 18.2. "CI & loader" step 4: replace the `join_type` enum check with the cardinality enum check in the within-row list; add the related-object resolution check to the reference-existence list; note the concepts exemption paragraph now carries this one referential rule

### Phase 9 — Review

19. [pending] Code review of changed files and address findings - `docs/code_review/`
    - 19.1. Run the `code-review` skill over each changed code file (`0001_initial_schema.sql`, `data_model.py`, `corpus_assembly.py`, `corpus_validation.py`, `db_io.py`, and the changed test files), writing `cr_*.md`
    - 19.2. Address findings via `code-implementation`; re-run the suite at the 100%-coverage bar

## Key Data Decisions and Considerations

1. **`join_type` is removed, not kept alongside `cardinality` — a fact/choice separation.** `join_type` recorded which rows a consumer should keep (INNER vs. LEFT …), an analytical choice that depends on the consuming query; the catalog's job is the facts: the predicate (`join_condition`), the correspondence shape (`cardinality`), and any row-existence caveats (`notes`). The one fact `LEFT` used to imply — unmatched rows exist on a side — moves to `cardinality` + `notes` (see Decision 7 for the structured-optionality follow-up). Removing it now is nearly free because this activity already rebuilds and touches every layer it lives in; removing it post-launch would be a migration plus coordinated cleanup.

2. **Cardinality is nullable with no default — unknown is never guessed.** A defaulted cardinality would be an unverified claim that grain reasoning (the whole point of the field) would silently trust. NULL means "not yet recorded"; authors add values as they verify them, and the `validated` flag continues to govern trust in the row as a whole.

3. **Direction reads a→b, and one stored orientation suffices.** `many_to_one` means many `table_a` rows correspond to one `table_b` row. Because the loader already rejects the same unordered pair documented in both orientations under one `relationship_name`, each pair has a single stored orientation and the reverse reading is mechanically derivable (swap the sides of the enum). No reverse rows, no symmetry bookkeeping.

4. **`related_object_ids` is authored, not derived — the inverse of `target_tables_referenced`.** A mapping's referenced tables are extractable from SQL, so the loader derives them; a concept's related objects live in prose the loader never parses, so the author states them and the loader *verifies* them (every entry must resolve to a corpus PK across the six id-keyed tables). This answers the drift objection that led `metadata-db-overview.md` to omit links originally: a hand-maintained link that goes stale now fails the pre-merge dry-run like any other broken reference. This supersedes that overview paragraph (Task 17.2). Links complement prose — retrieval uses array containment (GiST `gist__ltree_ops`) for deterministic anchoring, while definitions still name objects inline; relationship/mapping rows (composite tuple PKs) are not linkable targets — a concept about a join names the two tables instead.

5. **DDL lands as a `0001` edit + rebuild, not a `0002` migration.** The pre-launch bootstrap exception still applies (only sandbox systems are loaded; the corpus is fully reproducible from YAML). This keeps the launch schema in one canonical file, makes the `join_type` **column drop** trivial (no `ALTER TABLE … DROP COLUMN` migration with `_hstry` coordination), absorbs the stale concepts DDL comments deferred by `20260720v01_anchor_concepts_under_systems` Key Decision 12, clears bootstrap-phase `_hstry` churn, and — because the reloaded DB is empty — lets Phase 5 populate the new fields as fresh inserts with `update_reason: null`. Once a real system is loaded this door closes; the next such change is a numbered migration.

6. **Stale `join_type:` keys fail loudly by design.** Removing `join_type` from `_RECOGNIZED_KEYS` means any YAML row still carrying it is rejected as an unrecognized key (Decision F), naming the key and file — so the Phase 5/6 cleanup cannot be partially forgotten, and any future branch authored against the old format fails the dry-run instead of silently dropping the field.

7. **Optionality is deferred, deliberately.** Whether unmatched rows exist on a side (what `LEFT`/`FULL` hinted at) is a real fact, but structuring it (e.g. per-side `optional` booleans, or an extended enum like `one_to_zero_or_one`) multiplies the value space before any consumer needs it. For now it lives in `notes`; if grain-aware tooling later needs it structurally, it is an additive nullable column following this activity's pattern.

8. **Enum spellings are snake_case words, not symbols.** `one_to_one | one_to_many | many_to_one | many_to_many` rather than `1:1`/`1:N` — symbol forms invite inconsistent variants (`1:n`, `N:1`) and read worse in YAML. Case-sensitive exact match, enforced by the loader pre-merge (dry-run parity) and the DB CHECK at write time, the same pattern `join_type` used.

9. **Read/write symmetry for the empty array.** Whether an empty `related_object_ids` stores as NULL or `{}` must match what `read_db_state` coerces back (`tuple(x or ())` maps both to `()`), or a no-change reload would diff as an update and break idempotency. Task 5.4 fixes one choice and Task 13.1/15.1 pin it with tests — the same hazard `target_tables_referenced` already navigates.

10. **Output validation is the loader itself.** As with the prior two activities, there is no standalone validation script: the loader's assemble/validate front-half is the input gate, and the rebuild round-trip — real load, dry-run `0/0/0`, CHECK rejection probe, absent-`join_type` column check, and the GiST containment probe (Task 16.4) — is the output validation.

11. **Grouped by concern, not per-unit.** Same structure as `20260717v01`/`20260718v01`/`20260720v01`: one cohesive system (DDL + loader), so phases run DDL → model → validation → I/O → data → tests → reload → docs rather than repeating create→test→run per file.

12. **Out of scope.** The other candidate columns discussed alongside these (`systems.dialect`, `tables.grain`, `tables.canonical_filter`, `tables.table_type`, `columns.sensitivity`, `columns.ordinal_position`) are deliberately excluded; each would follow this same pattern in its own activity. The untracked `.claude/logconfig` dependency and CWD-relative imports are a separate known issue and do not block this activity (all runs are from the repo root).

13. **GiST serves the ltree-scalar probes, not the anyarray form (found during Phase 7 verification).** On the live PG 15 instance, the array-vs-array containment `related_object_ids @> '{x}'::ltree[]` resolves to the generic anyarray operator, which **no GiST opclass serves** — `EXPLAIN` with `enable_seqscan=off` still seq-scans. The `gist__ltree_ops`-served probe is the ltree[]-vs-ltree form: `related_object_ids <@ 'x'::ltree` ("any linked id at-or-under X") demonstrably uses `idx_concepts_related_objects_gist`. The plan's probe (Tasks 1.5/15.1/16.4) was written in the array form; both forms were kept — the array form for exact membership (correct, unindexed, fine at catalog scale) and the scalar form as the indexed retrieval query. The DDL comment on the new index documents this accurately; the pre-existing identical overstatement on `idx_column_mappings_target_tables_gist`'s comment was left untouched (out of scope — it would require another `0001` edit + rebuild for a comment).

14. **"All four concepts" read as four distinct concept names; all six rows were linked.** Task 7.1/16.4 say "the four concepts", but the corpus holds six concept rows (claim + beneficiary in both OCS and EDW, plus claim_type and final_action). All six received `related_object_ids` — the four distinct names across both anchors — since every definition names objects inline and partial linking would make retrieval coverage arbitrary.

15. **Rebuild ran twice.** The first rebuild surfaced Decision 13; the concepts-index DDL comment was corrected and — because `ddl_versions` checksums applied migrations — the drop/create/apply/grant/load sequence was re-run so the stored `0001` checksum matches the final file. Final state verified: 120 fresh inserts (`update_reason` null), second dry-run `0/0/0`, CHECK rejects an out-of-enum cardinality, all 11 relationships carry `cardinality`, all 6 concepts carry links, GiST probe index-served. The gated integration suite was also run live (4 passed) against the throwaway `metadata_db_integration` DB.
