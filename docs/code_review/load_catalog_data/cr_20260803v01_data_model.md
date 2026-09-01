---
name: cr_20260803v01_data_model
goal: Re-review of code/load_catalog_data/data_model.py against python-development skills after the granular concept-anchor change (773d09e), superseding cr_20260729v01.
created: 2026-08-03 10:07:01
updated: 2026-08-03 10:07:01
---

## Implementation Plan

1. [completed] Keep comments current - `code/load_catalog_data/data_model.py`
   - 1.1. [minor] Line 61: The `PRIMARY_KEY_COLUMNS` comment names the guarding integration test `test_pk_agreement`, but no test with that exact name exists — the function is `test_pk_agreement_and_ltree_types` (`code/load_catalog_data/unit_tests/test_integration.py:237`). Per comments guideline 3 (keep comments current), the reference should use the actual test name so a reader locating it by exact name is not misled.
        - Current: `# — the integration test `test_pk_agreement` asserts that they do.`
        - Expected: `# — the integration test `test_pk_agreement_and_ltree_types` asserts that they do.`
        - Resolution: Fixed as documented — updated the `PRIMARY_KEY_COLUMNS` comment (data_model.py:61) to reference `test_pk_agreement_and_ltree_types`, matching the actual function at `code/load_catalog_data/unit_tests/test_integration.py:237`.

2. [completed] Optional enhancements - `code/load_catalog_data/data_model.py`
   - 2.1. [suggestion] Line 653: `pk(row: Any, ...)` accepts any of the nine row dataclasses. A `RowType` union alias (`SystemRow | DataSourceRow | ... | ConceptRow`) would make accepted inputs explicit and let a type checker catch a wrong-object call, per type-hints guideline 3 (be specific, avoid `Any`).
        - Current: `def pk(row: Any, table: str) -> str | tuple[str, ...]:`
        - Expected: `def pk(row: RowType, table: str) -> str | tuple[str, ...]:` where `RowType` aliases the union of the nine row dataclasses.
        - Resolution: Deferred — `Any` remains defensible for a generic registry helper that only reads the PK attributes named in `PRIMARY_KEY_COLUMNS`; declined across cr_20260713v02 through cr_20260729v01, re-listed for visibility only.
   - 2.2. [suggestion] Line 441: `TableRelationshipKey` is the only composite-key alias without an inline comment naming its components; `DeploymentKey` (line 440) and `ColumnMappingKey` (line 442) both carry one.
        - Current: `TableRelationshipKey = tuple[str, str, str]`
        - Expected: `TableRelationshipKey = tuple[str, str, str]  # (table_a_id, table_b_id, relationship_name)`
        - Resolution: Deferred — cosmetic consistency only; the components are already documented in `PRIMARY_KEY_COLUMNS` (line 69), the `Corpus` docstring, and `pk()`'s Returns section, so the missing comment cannot mislead. Declined across the prior review series.

## Skills with No Issues

1. type-hints (completeness): No issues found — every function carries parameter and return annotations: the id builders `data_source_id` (line 554), `schema_id` (573), `table_id` (586), `column_id` (600), `schema_prefix` (616), `split_schema_id` (633); the factories `empty_corpus` (519), `empty_db_state` (534); `iter_tables` (549) returning `Iterator[str]`; `pk` (653); and the `ColumnRef.table_id`/`column_id` properties (509, 514). All module constants and every dataclass field are annotated. The only `Any` is the deliberate one in `pk` (item 2.1).
2. type-hints (modern syntax): No issues found — optionals use `str | None` / `datetime | None` throughout; containers use `tuple[str, ...]`, `dict[...]`, `frozenset[str]`. No `Optional`/`List`/`Dict`. Required prose columns (`description`, `definition`) are bare `str`, matching the NOT NULL DDL.
3. type-hints (import location): No issues found — `Iterator` from `collections.abc` (line 30), `dataclass` from `dataclasses` (31), `datetime` from `datetime` (32); `typing` supplies only `Any` (33).
4. type-hints (model ↔ registry consistency): No issues found — `TABLE_ORDER`, `PRIMARY_KEY_COLUMNS`, and `CONTENT_COLUMNS` all name the same 9 tables; `CONTENT_COLUMNS` maps one-to-one onto each dataclass's authored fields (`DeploymentRow` is the 6 pure-fact columns with no `notes`/`update_reason`; loader-managed `validated_ts` is excluded for `table_relationships`/`column_mappings`); `Corpus`/`DbState`/`empty_corpus`/`empty_db_state` all carry the same 9 dicts.
5. docstrings (Google style, accuracy): No issues found — module, every dataclass, and every function documented; `SystemRow`, `ConceptRow`, `ColumnRef` use `Attributes:`; `pk` documents Args/Returns/Raises. The 773d09e granular-anchor rewrite (module docstring lines 23-27, `ConceptRow` lines 397-406) matches `corpus_assembly._assemble_concepts` (corpus_assembly.py:1168): anchor deepens via leading segments of a dotted relative `name` to `{database}[.{schema}[.{table}[.{column}]]].concept.{name}`, a dotless `name` composes the file-anchor id unchanged, and `related_object_ids` resolution in `corpus_validation._check_concept_related_objects` (corpus_validation.py:569) confirms the Attributes claim. All positional-construction claims verified against `db_io._SELECT_COLUMNS`/`_SELECT_DEPLOYMENT_TABLES`/`_SELECT_TABLE_RELATIONSHIPS`/`_SELECT_COLUMN_MAPPINGS`/`_SELECT_CONCEPTS` (db_io.py:273-305) — every field order matches, including `ref_table_id` last for `ColumnRow` and `validated_ts` last for the two validated tables. Cross-references resolve: `db_io._validated_ts_update_args` (db_io.py:758), `corpus_validation._check_relationship_codeployment` (corpus_validation.py:693), `sql_parsing.py`, `corpus_diff.py`, `readme/metadata-db-overview.md`, and the DDL path `code/apply_ddl/ddl_catalog/0001_initial_schema.sql` all exist. `split_schema_id`/`schema_prefix` inverse claims hold (labels are single ltree segments).
6. logging: N/A — pure data/registry module with no runtime side effects; nothing to log.
7. exception-handling: N/A — no try/except; `pk()` deliberately propagates and documents `KeyError` for an unknown table (lines 671-672).
8. executable-scripts: N/A — not an executable script (no `main`/CLI/TOML config).
9. data-validation: N/A — not a `data_val_` script; model ↔ DDL agreement is guarded by `test_pk_agreement_and_ltree_types` and `test_data_model.py`.
10. unit-tests: N/A for this file — coverage lives in `code/load_catalog_data/unit_tests/test_data_model.py` and the integration test in `test_integration.py`.
