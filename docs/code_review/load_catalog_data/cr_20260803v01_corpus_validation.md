---
name: cr_20260803v01_corpus_validation
goal: Re-review code/load_catalog_data/corpus_validation.py against python-development skills after the code/lib logconfig path fix, the `_check_ref_tables` addition (ca45b27), and the table/column-depth concept-anchor extension (773d09e) since cr_20260729v01, whose four suggestions were all deferred.
created: 2026-08-03 10:09:05
updated: 2026-08-03 10:09:05
---

## Implementation Plan

1. [completed] Module and function docstring completeness - `code/load_catalog_data/corpus_validation.py`
   - 1.1. [suggestion] Lines 4-6: The module docstring's step-4 contract list names "FK reference existence (including concept `related_object_ids` link resolution)" but does not mention the concept anchor-prefix resolution check (`_check_concept_anchors`, wired in at line 130, implemented at line 600) or the newer `ref_table_id` domain-pointer check (`_check_ref_tables`, wired in at line 122). Carried from cr_20260729v01 (1.1).
        - Current: `..., FK reference existence (including concept `related_object_ids` link resolution), identifier syntax, ...`
        - Expected: extend the parenthetical, e.g. `..., FK reference existence (including columns' ref_table_id pointers, concept `related_object_ids` link resolution, and concept anchor-prefix resolution), identifier syntax, ...`
        - Resolution: Deferred — the docstring frames its list as the `readme/metadata-db-maintenance.md#ci--loader` step-4 contract (the authoritative source), and both checks read fairly as facets of "FK reference existence" whose own docstrings (lines 255-265, 601-629) precisely document the rules; optional summary enrichment only.
   - 1.2. [suggestion] Lines 6-7: The same contract list omits the three whole-corpus grouping checks `validate_corpus` invokes: `_check_relationship_pairs` (line 158), `_check_mapping_disambiguation` (line 159), and `_check_mapping_linkability` (line 161). Carried from cr_20260729v01 (1.2).
        - Current: `..., the `cardinality` enum, deployment residency rules, and SQL expression parsability.`
        - Expected: add a clause naming the grouping checks, e.g. `..., and SQL expression parsability — plus whole-corpus grouping checks: relationship orientation-duplicate / use_when disambiguation, per-source-column mapping disambiguation, and multi-table mapping linkability.`
        - Resolution: Deferred — each of the three checkers carries a precise self-documenting docstring and the module docstring is scoped as the step-4 contract summary, so this is optional enrichment, not an inaccuracy in the code's behavior.
   - 1.3. [suggestion] Lines 390-392: The `_check_sql_expressions` docstring closes with "the parse tree is memoized for the orchestrator and the linkability check", but the memo is also consumed by `_check_mapping_codeployment` (line 743) — the co-deployment check is an unnamed third consumer.
        - Current: `...; the parse tree is memoized for the orchestrator and the linkability check.`
        - Expected: `...; the parse tree is memoized for the orchestrator and the mapping co-deployment and linkability checks.`
        - Resolution: Deferred — the sentence's point (the memo outlives this function) holds, `validate_corpus`'s wiring comments (lines 150-161) name every downstream consumer two screens up, and the omission misleads no maintainer about behavior; optional wording completeness only.

2. [completed] Comment naming consistency - `code/load_catalog_data/corpus_validation.py`
   - 2.1. [suggestion] Line 152: The `validate_corpus` section comment reads "Venue sets per table (from deployments) drive the ..." — the precise table name is `deployment_tables`, which neighboring comments (lines 115, 189) use in full. Carried from cr_20260729v01 (2.1).
        - Current: `# Venue sets per table (from deployments) drive the "runnable`
        - Expected: `# Venue sets per table (from deployment_tables) drive the "runnable`
        - Resolution: Deferred — the phrase reads naturally as generic English ("from deployments") rather than a dangling identifier, and the helper it introduces (`_deployment_venues`, line 680) documents the exact table name, so the comment is not misleading.

3. [completed] Fail-fast attribute access in `validate_update_reason` - `code/load_catalog_data/corpus_validation.py`
   - 3.1. [suggestion] Lines 961, 969: `getattr(change.new, "update_reason", None)` reads the field with a silent `None` default. Because `deployment_tables` (the only row type lacking `update_reason`) is skipped explicitly above each call (lines 957, 967), every remaining row is one of the eight authored tables that all declare `update_reason` (`data_model.py`), so the default branch is never exercised; direct access `change.new.update_reason` would be more fail-fast per the exception-handling skill. Carried from cr_20260729v01 (3.1).
        - Current: `if getattr(change.new, "update_reason", None) is not None:`
        - Expected: `if change.new.update_reason is not None:`
        - Resolution: Deferred — `RowChange.new` is loosely typed (`corpus_diff.py`), so the `getattr` guard is defensive coding against a heterogeneous field; the deployment-row skip already guarantees the attribute is present, so the current form is safe and behavior-equivalent. Optional robustness/readability change only.

4. [completed] Avoid recomputing referenced tables per mapping - `code/load_catalog_data/corpus_validation.py`
   - 4.1. [suggestion] Lines 746, 852: `compute_target_tables_referenced(tree)` is called once in `_check_mapping_codeployment` and again in `_check_mapping_linkability` for the same memoized `tree` of each mapping; the derived referenced-tables list could be computed once and shared. Carried from cr_20260729v01 (4.1).
        - Current: `tables = compute_target_tables_referenced(tree)` (computed independently in both checkers)
        - Expected: compute the referenced-tables set once (e.g., alongside the memo) and pass it into both checkers.
        - Resolution: Deferred — the computation is a cheap single tree walk over already-parsed expressions and the two call sites keep each checker self-contained and independently readable; sharing would couple the two checks for negligible gain. Revisit only if profiling flags it.

5. [completed] Structural and type-alias consistency - `code/load_catalog_data/corpus_validation.py`
   - 5.1. [suggestion] Lines 639-677: The new four-depth anchor resolution in `_check_concept_anchors` is an if/elif ladder of four near-identical branches differing only in the id space and the object-kind phrase; a depth-keyed table (e.g., `{1: (corpus.data_sources, "data_sources id", "a data-source-level concept must live under an existing data source"), ...}`) would collapse the ladder to one lookup-and-report block and make adding a depth a one-line change.
        - Current: `if len(anchor_labels) == 1: ... elif len(anchor_labels) == 2: ... elif len(anchor_labels) == 3: ... elif len(anchor_labels) == 4: ... else: ...`
        - Expected: a `dict[int, tuple[Mapping[str, object], str, str]]` keyed by anchor depth, with a single shared issue-append using the looked-up space and phrasing.
        - Resolution: Deferred — the ladder is explicit, each branch's message is hand-tuned prose, the depth set (1-4) is fixed by the concept_id shape CHECK so no fifth branch is coming, and the table-driven form would trade four readable blocks for indirection. Optional structural refactor only.
   - 5.2. [suggestion] Lines 331, 773, 775: Composite-key annotations spell out raw tuple shapes (`tuple[str, str]` for deployment keys, `tuple[str, str, str]` for relationship keys) where `data_model` exports the `DeploymentKey` and `TableRelationshipKey` aliases — the module already imports and uses `ColumnMappingKey` (lines 47, 816), so using the sibling aliases would name the intent consistently.
        - Current: `seen_addresses: dict[tuple[str, str, str, str], tuple[str, str]] = {}` / `by_pair_name: dict[tuple[frozenset[str], str], tuple[str, str, str]] = {}`
        - Expected: import `DeploymentKey` and `TableRelationshipKey` from `data_model` and use them as the value types (the four-string address tuple is local and stays literal).
        - Resolution: Deferred — the raw tuples are structurally identical to the aliases, the surrounding comments (lines 772, 774) already name what the values are, and the annotations are correct as written; optional readability alignment only.

## Skills with No Issues

1. Type Hints skill: One optional alias-consistency suggestion (5.2); no blocking issues. Every function carries complete, modern annotations; the `_check_*(corpus: Corpus, issues: list[str]) -> None` accumulator signature is applied uniformly, `validate_corpus -> dict[ColumnMappingKey, exp.Expression | None]` matches the memo threading, and `_case_hint(object_id: str, known_ids: Iterable[str]) -> str` uses the abstract `Iterable` correctly for its heterogeneous callers (dicts, key views, and a set union).
2. Docstrings skill: Three optional completeness suggestions (1.1, 1.2, 1.3); no blocking issues. The new `_check_ref_tables` and the extended `_check_concept_anchors` both carry accurate Google-style docstrings documenting the "why" (the anchor-depth-determines-kind rule at lines 618-622 matches the implemented ladder exactly), and `validate_corpus` / `validate_update_reason` document Args, Returns, and Raises correctly — the Returns claim about the memo holds because a partially populated memo is never returned (a failed parse always lands in `issues`, raising `ValidationError` before return).
3. Comments skill: One naming-consistency suggestion only (2.1); otherwise comments explain "why" and match code (e.g., the relocated logconfig path rationale at lines 33-35, the data_source_id/table_id redundancy note at lines 208-212, the malformed-shape skip note at lines 633-634).
4. Logging skill: No issues found. `get_logger(__name__)` per the library-module pattern (lines 37, 52); no `print()`; both validators emit a single INFO pass milestone (lines 165, 981); no Entering/Exiting noise; f-strings throughout.
5. Exception Handling skill: One optional fail-fast suggestion (3.1); no blocking issues. No bare `except`; every handler catches the specific `ValueError` raised by `validate_identifier_segment`, `parse_expression`, and `extract_column_refs` and accumulates into `issues`, surfacing all via `ValidationError`. The two `raise ValidationError(issues)` calls (lines 164, 980) are fresh raises outside any `except`, so `from e` chaining does not apply.
6. Data Validation skill: N/A - governs `data_val_`-prefixed output-validation scripts; this is loader business-logic validation, not a data-quality output check.
7. Executable Scripts skill: N/A - imported library module with no `main()` / `__main__` entry point.
8. Unit Tests skill: N/A - source file, not a test file; tests live under `unit_tests/` (`test_corpus_validation.py`).
9. SAS Conversion skill: N/A - no SAS source involved.
10. PySpark skill: N/A - no PySpark/Databricks code.
11. Ibis skill: N/A - no Ibis code.
