---
name: cr_20260729v01_corpus_validation
goal: Re-review code/load_catalog_data/corpus_validation.py against python-development skills after the load_catalog_data rename and the deployment_tables / NOT-NULL / cr_20260724v03 changes since cr_20260727v01, whose one minor docstring finding (1.1) is confirmed applied.
created: 2026-07-29 14:36:35
updated: 2026-07-29 14:36:35
---

## Implementation Plan

1. [completed] Module docstring completeness - `code/load_catalog_data/corpus_validation.py`
   - 1.1. [suggestion] Lines 3-6: The module docstring's step-4 contract list names "FK reference existence (including concept `related_object_ids` link resolution)" but does not mention the concept anchor-prefix resolution check (`_check_concept_anchors`, wired in at line 126 and implemented at line 596) — a distinct referential rule the module itself describes as "the one referential hole a plain FK cannot express" (lines 122-125). Carried from cr_20260727v01 (2.1).
        - Current: `..., FK reference existence (including concept `related_object_ids` link resolution), identifier syntax, ...`
        - Expected: extend the parenthetical, e.g. `..., FK reference existence (including concept `related_object_ids` link resolution and concept anchor-prefix resolution), identifier syntax, ...`
        - Resolution: Deferred — the docstring frames its list as the `readme/metadata-db-maintenance.md#ci--loader` step-4 contract (the authoritative source), the inline comment at lines 122-125 and `_check_concept_anchors`'s own docstring precisely document the rule, and the check reads fairly as a facet of "FK reference existence"; optional summary enrichment only.
   - 1.2. [suggestion] Lines 6-8: The same contract list omits the three whole-corpus grouping checks `validate_corpus` invokes: `_check_relationship_pairs` (line 154), `_check_mapping_disambiguation` (line 155), and `_check_mapping_linkability` (line 157). Carried from cr_20260727v01 (2.2).
        - Current: `..., the `cardinality` enum, deployment residency rules, and SQL expression parsability.`
        - Expected: add a clause naming the grouping checks, e.g. `..., and SQL expression parsability — plus whole-corpus grouping checks: relationship orientation-duplicate / use_when disambiguation, per-source-column mapping disambiguation, and multi-table mapping linkability.`
        - Resolution: Deferred — each of the three checkers carries a precise self-documenting docstring and the docstring is scoped as the step-4 contract summary, so this is optional enrichment, not an inaccuracy in the code's behavior.

2. [completed] Comment naming consistency - `code/load_catalog_data/corpus_validation.py`
   - 2.1. [suggestion] Line 148: The `validate_corpus` section comment reads "Venue sets per table (from deployments) drive the ..." — after the b0daa49 rename the precise table name is `deployment_tables`, and neighboring comments (lines 111, 185) use the full name. Carried from cr_20260727v01 (3.1).
        - Current: `# Venue sets per table (from deployments) drive the "runnable`
        - Expected: `# Venue sets per table (from deployment_tables) drive the "runnable`
        - Resolution: Deferred — the phrase reads naturally as generic English ("from deployments") rather than a dangling identifier, and the helper it introduces (`_deployment_venues`, line 649) documents the exact table name, so the comment is not misleading.

3. [completed] Fail-fast attribute access in `validate_update_reason` - `code/load_catalog_data/corpus_validation.py`
   - 3.1. [suggestion] Lines 930, 938: `getattr(change.new, "update_reason", None)` reads the field with a silent `None` default. Because `deployment_tables` (the only row type lacking `update_reason`) is skipped explicitly one line above each call (lines 926, 936), every remaining row is one of the eight authored tables that all declare `update_reason` (`data_model.py`), so the default branch is never exercised. Direct access `change.new.update_reason` would be more fail-fast per the exception-handling skill. Carried from cr_20260727v01 (4.1).
        - Current: `if getattr(change.new, "update_reason", None) is not None:`
        - Expected: `if change.new.update_reason is not None:`
        - Resolution: Deferred — `RowChange.new` is typed `Any` (`corpus_diff.py`), so the `getattr` guard is defensive coding against a heterogeneous field; the deployment-row skip already guarantees the attribute is present, so the current form is safe and behavior-equivalent. Optional robustness/readability change only.

4. [completed] Avoid recomputing referenced tables per mapping - `code/load_catalog_data/corpus_validation.py`
   - 4.1. [suggestion] Lines 715, 821: `compute_target_tables_referenced(tree)` is called once in `_check_mapping_codeployment` and again in `_check_mapping_linkability` for the same memoized `tree` of each mapping; the derived referenced-tables list could be computed once and shared. Carried from cr_20260727v01 (5.1).
        - Current: `tables = compute_target_tables_referenced(tree)` (computed independently in both checkers)
        - Expected: compute the referenced-tables set once (e.g., alongside the memo) and pass it into both checkers.
        - Resolution: Deferred — the computation is a cheap single tree walk over already-parsed expressions and the two call sites keep each checker self-contained and independently readable; sharing would couple the two checks for negligible gain. Revisit only if profiling flags it.

## Skills with No Issues

1. Type Hints skill: No issues found. Every function carries complete, modern annotations; the `_check_*(corpus: Corpus, issues: list[str]) -> None` accumulator signature is applied uniformly, `validate_corpus -> dict[ColumnMappingKey, exp.Expression | None]` and the memo-threading params stay consistent with `ColumnMappingKey` in `data_model.py`, and `_case_hint(object_id: str, known_ids: Iterable[str]) -> str` uses the abstract `Iterable` correctly for its heterogeneous callers.
2. Docstrings skill: Two optional module-docstring completeness suggestions (1.1, 1.2); no blocking issues. The prior minor currency finding (cr_20260727v01 1.1 — the missing join_condition minimum-column-reference clause) is confirmed applied: the `_check_sql_expressions` docstring now documents "requires at least one column reference (a constant predicate relates no endpoints)" at lines 384-386. Every public and helper function carries a full Google-style docstring documenting the "why".
3. Comments skill: One naming-consistency suggestion only (2.1); otherwise comments explain "why" and match code (e.g., the data_source_id/table_id redundancy note at lines 204-208, the concept-anchor variable-depth note at lines 619-620).
4. Logging skill: No issues found. `get_logger(__name__)` per the library-module pattern (lines 33, 48); no `print()`; both validators emit a single INFO pass milestone (lines 161, 950); no Entering/Exiting noise; f-strings throughout.
5. Exception Handling skill: One optional fail-fast suggestion (3.1); no blocking issues. No bare `except`; every handler catches the specific `ValueError` raised by `validate_identifier_segment`, `parse_expression`, and `extract_column_refs` and accumulates into `issues`, surfacing all via `ValidationError`. The two `raise ValidationError(issues)` calls (lines 160, 948) are fresh raises outside any `except`, so `from e` chaining does not apply.
6. Data Validation skill: N/A - governs `data_val_`-prefixed output-validation scripts; this is loader business-logic validation, not a data-quality output check.
7. Executable Scripts skill: N/A - imported library module with no `main()` / `__main__` entry point.
8. Unit Tests skill: N/A - source file, not a test file; tests live under `unit_tests/` (`test_corpus_validation.py`).
9. SAS Conversion skill: N/A - no SAS source involved.
10. PySpark skill: N/A - no PySpark/Databricks code.
11. Ibis skill: N/A - no Ibis code.
