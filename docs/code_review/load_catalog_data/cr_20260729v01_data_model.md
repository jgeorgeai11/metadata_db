---
name: cr_20260729v01_data_model
goal: Re-review of code/load_catalog_data/data_model.py against python-development skills after the module rename from load_metadata_db to load_catalog_data, superseding cr_20260724v02.
created: 2026-07-29 14:38:40
updated: 2026-07-29 14:38:40
---

## Implementation Plan

1. [completed] Optional enhancements - `code/load_catalog_data/data_model.py`
   - 1.1. [suggestion] Line 647: `pk(row: Any, ...)` accepts any of the eight row dataclasses. A `RowType` union alias (`SystemRow | DataSourceRow | ... | ConceptRow`) would make accepted inputs explicit and let a type checker catch a wrong-object call, per type-hints guideline 3 (be specific, avoid `Any`).
        - Current: `def pk(row: Any, table: str) -> str | tuple[str, ...]:`
        - Expected: `def pk(row: RowType, table: str) -> str | tuple[str, ...]:` where `RowType` aliases the union of the eight row dataclasses.
        - Resolution: Deferred — `Any` remains defensible for a generic registry helper that only reads the PK attributes named in `PRIMARY_KEY_COLUMNS`; declined across cr_20260713v02, cr_20260717v01/v02, cr_20260720v01, and cr_20260724v01/v02, re-listed for visibility only.
   - 1.2. [suggestion] Line 435: `TableRelationshipKey` is the only composite-key alias without an inline comment naming its components; `DeploymentKey` (line 434) and `ColumnMappingKey` (line 436) both carry one.
        - Current: `TableRelationshipKey = tuple[str, str, str]`
        - Expected: `TableRelationshipKey = tuple[str, str, str]  # (table_a_id, table_b_id, relationship_name)`
        - Resolution: Deferred — cosmetic consistency only; the components are already documented in `PRIMARY_KEY_COLUMNS` (line 67), the `Corpus` docstring, and `pk()`'s Returns section, so the missing comment cannot mislead. Declined across the prior review series.

## Skills with No Issues

1. type-hints (completeness): No issues found — every function carries parameter and return annotations: the id builders `data_source_id` (line 548), `schema_id` (567), `table_id` (580), `column_id` (594), `schema_prefix` (610), `split_schema_id` (627); the factories `empty_corpus` (513), `empty_db_state` (528); `iter_tables` (543) returning `Iterator[str]`; `pk` (647); and the `ColumnRef.table_id`/`column_id` properties (502, 507). All module constants and every dataclass field are annotated. The only `Any` is the deliberate one in `pk` (item 1.1).
2. type-hints (modern syntax): No issues found — optionals use `str | None` / `datetime | None` throughout; containers use `tuple[str, ...]`, `dict[...]`, `frozenset[str]`. No `Optional`/`List`/`Dict`. `description` is bare `str` on `SystemRow`/`DataSourceRow`/`SchemaRow`/`TableRow`/`ColumnRow` and `definition` bare `str` on `ConceptRow`, matching the NOT NULL DDL.
3. type-hints (import location): No issues found — `Iterator` from `collections.abc` (line 28), `dataclass` from `dataclasses` (29), `datetime` from `datetime` (30); `typing` supplies only `Any` (31).
4. type-hints (model ↔ registry consistency): No issues found — `TABLE_ORDER`, `PRIMARY_KEY_COLUMNS`, and `CONTENT_COLUMNS` all name the same 9 tables including `deployment_tables`; `CONTENT_COLUMNS` maps one-to-one onto each dataclass's non-defaulted fields (`DeploymentRow` is the 6 pure-fact columns with no `notes`/`update_reason`; `validated_ts` is excluded for `table_relationships`/`column_mappings`); `Corpus`/`DbState`/`empty_corpus`/`empty_db_state` all carry the `deployment_tables` dict.
5. docstrings (Google style, accuracy): No issues found — module, every dataclass, and every function documented; `SystemRow`, `ConceptRow`, `ColumnRef` use `Attributes:`; `pk` documents Args/Returns/Raises. The cr_20260724v02 TableRelationshipRow DDL-order fix is present (lines 335-337). All five positional-construction claims verified against `db_io._SELECT_COLUMNS`/`_SELECT_DEPLOYMENT_TABLES`/`_SELECT_TABLE_RELATIONSHIPS`/`_SELECT_COLUMN_MAPPINGS`/`_SELECT_CONCEPTS` (db_io.py:256-288) — every field order matches. Cross-references verified in the renamed module: `db_io._validated_ts_update_args` (db_io.py:741), `corpus_assembly._assemble_concepts` (1028), `_assemble_deployments` (1547), `assemble_corpus` (1655), `corpus_validation._check_relationship_codeployment` (662); `sql_parsing.py` and `corpus_diff.py` present. The DDL path in `ConceptRow` (line 403), `code/apply_ddl/ddl_catalog/0001_initial_schema.sql`, exists post-rename. `split_schema_id`/`schema_prefix` inverse claims hold (labels are single ltree segments).
6. comments (why-not-what, current): No issues found — the `TABLE_ORDER` FK-ordering comment places `deployment_tables` after `columns`; the `CONTENT_COLUMNS` note (lines 124-126) that `deployment_tables` has no `update_reason` matches the DDL; the `validated_ts` loader-managed comments (lines 349-353, 383-387) match `db_io._validated_ts_update_args`; the `PRIMARY_KEY_COLUMNS` comment's `test_pk_agreement` reference resolves to `unit_tests/test_integration.py`, and its claim that `corpus_assembly.assemble_corpus` and `db_io.read_db_state` derive keys via `pk()` is confirmed (corpus_assembly.py:1804-1884, db_io.py:305+).
7. logging: N/A — pure data/registry module with no runtime side effects; nothing to log.
8. exception-handling: N/A — no try/except; `pk()` deliberately propagates and documents `KeyError` for an unknown table (lines 665-666).
9. executable-scripts: N/A — not an executable script (no `main`/CLI/TOML config).
10. data-validation: N/A — not a `data_val_` script; model ↔ DDL agreement is guarded by `test_pk_agreement` and `test_data_model.py`.
11. unit-tests: N/A for this file — coverage lives in `code/load_catalog_data/unit_tests/test_data_model.py` and the integration `test_pk_agreement`.
