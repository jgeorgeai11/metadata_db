---
name: cr_20260730v01_0001_initial_schema
goal: Address code quality issues identified in code/apply_ddl/ddl_catalog/0001_initial_schema.sql to align with sql-development best-practices; re-review since cr_20260729v01 after commit 968ab41 shifted line numbers, with all findings re-verified against the current file.
created: 2026-07-30 14:18:21
updated: 2026-07-30 14:18:21
---

## Implementation Plan

1. [completed] Formatting: string-literal lines exceed 100 chars - `code/apply_ddl/ddl_catalog/0001_initial_schema.sql`
   - 1.1. [suggestion] Lines 665, 668, 674, 677, 680, 684, 688, 691, 695, 702, 705, 708, 711, 714, 717, 720, 723, 726, 731: the `comment on table ...` / `comment on column ...` statements carry single-line string literals ranging from 122 to 307 chars, exceeding the sql-development 100-char-per-line formatting standard (best-practices guideline 7). These are string-literal data values stored verbatim by `pg_catalog.obj_description()`, not code. Wrapping them means either embedding a newline into the stored one-line description or splitting into concatenated literals across lines (which risks dropped word-boundary spaces and reduces readability). The migration is also checksum-tracked by apply_ddl.py, so any edit forces a re-baseline. All non-literal DDL/statement lines are within 100 chars.
        - Current (line 684, representative — the longest at 307 chars):
          ```sql
          comment on table deployment_tables is
              'One row per (documented table, system): the venue the table is materialized in and its physical database/schema/table names there. The single home of venue-dependent truth — absence of a row means not deployed. Pure facts: every column NOT NULL, no freeform columns; named for its table x venue grain';
          ```
        - Expected: keep each description under 100 chars per line where achievable without harming the stored text, otherwise leave as-is.
        - Resolution: Deferred — accept-as-is; wrapping would embed a newline into the stored one-line object comment or split it into concatenated literals (harming the description text), and the file is checksum-tracked so editing forces a re-baseline.
   - 1.2. [suggestion] Line 68: the `raise exception` message literal in the PostgreSQL-version-assertion `do` block is 108 chars, exceeding the 100-char standard (best-practices guideline 7). Like the object-comment literals in 1.1, this is a string-literal data value (the error text surfaced to the operator), not a code expression; splitting it across concatenated literals would risk a dropped space at the join and reduce readability of the message.
        - Current:
          ```sql
              'metadata_db requires PostgreSQL 16 or newer (hyphenated ltree labels); server_version_num = %',
          ```
        - Expected: keep the message on one line if splitting harms readability, otherwise wrap under 100 chars.
        - Resolution: Deferred — accept-as-is; the value is a single operator-facing error message and splitting it into concatenated literals risks a dropped word-boundary space while adding no clarity, and the file is checksum-tracked so editing forces a re-baseline.

## Skills with No Issues

1. sql-development / Explicit column references: N/A - DDL file, no SELECT or joins.
2. sql-development / Explicit joins: N/A - no joins in DDL.
3. sql-development / Explicit group/order by: N/A - no group by or order by in DDL.
4. sql-development / Prefer `union all`: N/A - no set operations.
5. sql-development / Data handling (NULL/CAST): No issues - nullability is declared explicitly via `not null` throughout (the DDL analogue), including the required-documentation NOT NULL backstops (all five descriptions, `concepts.definition`, `data_sources.owner`, both ltree[] columns at lines 316 and 360). Conditional-null invariants are enforced with explicit CHECKs: the `update_reason` pairing `check ((update_reason is null) = (insert_ts = update_ts))` on every authored main table (lines 96, 115, 136, 155, 203, 296, 334, 377), `check (validated = (validated_ts is not null))` (lines 294, 332), and `check (target_expression is not null or notes is not null)` (line 331); redundant columns are pinned by hierarchy/leaf-name/lowercase CHECKs (e.g. lines 130-134, 149-153, 197-201, 238-245).
6. sql-development / Prefer CTEs over nested subqueries: N/A - no subqueries.
7. sql-development / Query block annotation (Level = ...): N/A - DDL, not a query with CTEs.
8. sql-development / Formatting - lowercase: No issues - all keywords, identifiers, and types are lowercase (including `generated always as identity` at line 531); the only mixed-case text is inside comments (natural-language prose, e.g. "GENERATED ALWAYS AS IDENTITY" at line 528) and string-literal data values (e.g. the "NOT NULL" occurrences in stored description literals at line 684).
9. sql-development / Formatting - 4-space indent: No issues - column definitions, CHECK constraints, wrapped continuation lines (e.g. the named FK at lines 187-190, the `constraint ... unique (...)` at lines 256-259, the `check (cardinality in ...)` at lines 281-283, and the multi-column index definitions at lines 566-590) and the `do $$ ... $$` version-assertion block use 4-space indentation.
10. sql-development / Formatting - max 100 chars per line: Issue found - see findings 1.1 and 1.2 (object-description string literals and the one error-message literal only; all DDL/statement lines are within 100 chars).
11. sql-development / Comments (why not what, above the line): No issues - comments explain rationale and design intent (e.g. venue-free ltree identity at lines 11-19, the NOT-NULL backstop inventory at lines 21-28, the declarative-backstop inventory at lines 30-55, the PG16 version assertion at lines 58-63, records-grain `is_primary_key` at lines 164-168, the deferrable `ref_table_id` FK and its shared constraint-name literal at lines 170-186, sparse-authored deployments and redundant `data_source_id` at lines 206-225, the deferred physical-address uniqueness constraint at lines 247-255, derived join venues at lines 262-268, loader-managed `validated_ts` at lines 287-288/324-325, the concept_id shape CHECK at lines 364-369, the hstry-mirror no-constraint rationale at lines 383-385 and per-mirror notes, the index/opclass strategy at lines 540-646, and the schema-agnostic unqualified-name rationale at lines 649-661) and are placed above the relevant statements, not inline.
12. sql-development / dbt (optional): N/A - not a dbt project file.
13. python-development (all core skills): N/A - target is a `.sql` file.
