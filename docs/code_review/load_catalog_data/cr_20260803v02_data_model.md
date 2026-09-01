---
name: cr_20260803v02_data_model
goal: Re-review of code/load_catalog_data/data_model.py against python-development skills covering the uncommitted concept-id docstring rewrite on top of fefe736, superseding cr_20260803v01.
created: 2026-08-03 11:30:49
updated: 2026-08-03 11:30:49
---

## Implementation Plan

1. [completed] Optional enhancements - `code/load_catalog_data/data_model.py`
   - 1.1. [suggestion] Line 656: `pk(row: Any, ...)` accepts any of the nine row dataclasses. A `RowType` union alias (`SystemRow | DataSourceRow | ... | ConceptRow`) would make accepted inputs explicit and let a type checker catch a wrong-object call, per type-hints guideline 3 (be specific, avoid `Any`).
        - Current: `def pk(row: Any, table: str) -> str | tuple[str, ...]:`
        - Expected: `def pk(row: RowType, table: str) -> str | tuple[str, ...]:` where `RowType` aliases the union of the nine row dataclasses.
        - Resolution: Deferred — `Any` remains defensible for a generic registry helper that only reads the PK attributes named in `PRIMARY_KEY_COLUMNS`; declined across cr_20260713v02 through cr_20260803v01, re-listed for visibility only.
   - 1.2. [suggestion] Line 444: `TableRelationshipKey` is the only composite-key alias without an inline comment naming its components; `DeploymentKey` (line 443) and `ColumnMappingKey` (line 445) both carry one.
        - Current: `TableRelationshipKey = tuple[str, str, str]`
        - Expected: `TableRelationshipKey = tuple[str, str, str]  # (table_a_id, table_b_id, relationship_name)`
        - Resolution: Deferred — cosmetic consistency only; the components are already documented in `PRIMARY_KEY_COLUMNS` (line 70), the `Corpus` docstring, and `pk()`'s Returns section, so the missing comment cannot mislead. Declined across the prior review series.

## Skills with No Issues

1. docstrings (currency of the rewritten concept-id prose): No issues found — the only working-tree changes since fefe736 are the module docstring (lines 23-28), the `ConceptRow` docstring (lines 400-407), and the `concept_id` Attribute (lines 416-418), and every rewritten claim matches the loader: `concept_id` is composed as the file's path prefix + `.` + the body `name`, byte for byte, with nothing inserted (`corpus_assembly._assemble_concept_row`, corpus_assembly.py:1169: `concept_id = f"{prefix}.{name}"`); the required `name` form `[{table}[.{column}].]concept.{leaf}` with the reserved `concept` segment written by the author exactly once, second-to-last, is enforced by `corpus_assembly._validate_concept_name` (corpus_assembly.py:992-1132), including the anchor-deepening and leaf-final rules; the reserved segment itself is `RESERVED_CONCEPT_SEGMENT = "concept"` (yaml_discovery.py:130).
2. docstrings (Google style, all other content): No issues found — module, every dataclass, and every function documented; `SystemRow`, `ConceptRow`, `ColumnRef` use `Attributes:`; `pk` documents Args/Returns/Raises. All unchanged claims were verified in cr_20260803v01 earlier today (positional-construction agreement with `db_io._SELECT_*`, cross-references to `corpus_diff.py`, `corpus_validation`, `db_io._validated_ts_update_args`, the DDL path, and the `split_schema_id`/`schema_prefix` inverse claims) and the diff since touches none of them.
3. comments (currency): No issues found — cr_20260803v01's minor is applied: the `PRIMARY_KEY_COLUMNS` comment (line 62) now names the actual integration test `test_pk_agreement_and_ltree_types` (`code/load_catalog_data/unit_tests/test_integration.py:237`). The `TABLE_ORDER` FK-ordering comment (lines 37-44) and the `ref_table_id`/`validated_ts` defaulted-field comments (lines 280-281, 352-356, 386-390) all describe current behavior; the "why" convention is followed throughout.
4. type-hints (completeness): No issues found — every function carries parameter and return annotations: the id builders `data_source_id` (line 557), `schema_id` (576), `table_id` (589), `column_id` (603), `schema_prefix` (619), `split_schema_id` (636); the factories `empty_corpus` (522), `empty_db_state` (537); `iter_tables` (552) returning `Iterator[str]`; `pk` (656); and the `ColumnRef.table_id`/`column_id` properties (512, 517). All module constants and every dataclass field are annotated. The only `Any` is the deliberate one in `pk` (item 1.1).
5. type-hints (modern syntax): No issues found — optionals use `str | None` / `datetime | None` throughout; containers use `tuple[str, ...]`, `dict[...]`, `frozenset[str]`; `Iterator` imported from `collections.abc` (line 31), `typing` supplies only `Any` (34). No `Optional`/`List`/`Dict`.
6. type-hints (model ↔ registry consistency): No issues found — `TABLE_ORDER`, `PRIMARY_KEY_COLUMNS`, and `CONTENT_COLUMNS` all name the same 9 tables; `CONTENT_COLUMNS` maps one-to-one onto each dataclass's authored fields (`DeploymentRow` is the 6 pure-fact columns with no `notes`/`update_reason`; loader-managed `validated_ts` excluded for `table_relationships`/`column_mappings`); `Corpus`/`DbState`/`empty_corpus`/`empty_db_state` all carry the same 9 dicts.
7. logging: N/A — pure data/registry module with no runtime side effects; nothing to log.
8. exception-handling: N/A — no try/except; `pk()` deliberately propagates and documents `KeyError` for an unknown table (lines 674-675).
9. executable-scripts: N/A — not an executable script (no `main`/CLI/TOML config).
10. data-validation: N/A — not a `data_val_` script; model ↔ DDL agreement is guarded by `test_pk_agreement_and_ltree_types` and `test_data_model.py`.
11. unit-tests: N/A for this file — coverage lives in `code/load_catalog_data/unit_tests/test_data_model.py` and the integration test in `test_integration.py`.
