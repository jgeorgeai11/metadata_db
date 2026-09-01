---
name: cr_20260813v02_corpus_validation
goal: Re-review code/load_catalog_data/corpus_validation.py against python-development skills since cr_20260813v01, reviewed as a group with its unit tests (test_corpus_validation.py, test_corpus_diff.py) and adding the identifier-syntax defense-in-depth gap found by that cross-check.
created: 2026-08-13 11:48:48
updated: 2026-08-13 12:21:32
---

## Implementation Plan

1. [completed] Identifier-syntax defense-in-depth coverage - `code/load_catalog_data/corpus_validation.py`
   - 1.1. [minor] Lines 278-314: `_check_identifier_syntax` re-validates `table_name`, `column_name`, `relationship_name`, and every dotted segment of `concept_id`, but not `mapping_name`. `mapping_name` is `relationship_name`'s exact analogue — both are body-authored discriminators of a composite PK, both are validated in wave 1 by `corpus_assembly` (line 799 for `relationship_name`, line 916 for `mapping_name`), and both are named in the step-4 identifier-syntax rule this function implements (`MAINTAINING.md#ci--loader`). The docstring enumerates the covered "body-derived names" without explaining why the fourth one is out of scope, so a `column_mappings` row reaching wave 2 by any route other than the assembler — the very case this defense-in-depth layer exists for, per the concept_id note at lines 304-308 — carries an unchecked `mapping_name`.
        - Current: `for key, rel in corpus.table_relationships.items(): ... validate_identifier_segment(rel.relationship_name, "relationship_name")` with no `column_mappings` counterpart
        - Expected: add the sibling loop — `for key, cm in corpus.column_mappings.items(): try: validate_identifier_segment(cm.mapping_name, "mapping_name") except ValueError as e: issues.append(f"column_mappings[{key}]: {e}")` — or, if the omission is deliberate, say so in the docstring the way the physical-names exclusion is already called out (lines 283-285).
        - Resolution: Implemented as specified — added the `column_mappings` loop immediately after the `table_relationships` loop, reporting as `column_mappings[{key}]: {e}` to match the sibling's message shape, and added `mapping_name` to the docstring's enumeration of covered body-derived names. Added accommodation: the new `except` branch would otherwise be unexecuted, so `unit_tests/test_corpus_validation.py` gains `test_validate_corpus_bad_identifier_mapping_name_raises` in the Identifier-syntax section, alongside the existing table/column/relationship name tests; `corpus_validation.py` is now at 100% statement coverage.

2. [completed] Module and function docstring completeness - `code/load_catalog_data/corpus_validation.py`
   - 2.1. [suggestion] Lines 3-8: The module docstring's step-4 contract list names "FK reference existence (including concept `related_object_ids` link resolution)" but does not mention the concept anchor-prefix resolution check (`_check_concept_anchors`, wired at line 130) or the `ref_table_id` domain-pointer check (`_check_ref_tables`, wired at line 122). Carried from cr_20260813v01 (1.1).
        - Current: `..., FK reference existence (including concept `related_object_ids` link resolution), identifier syntax, ...`
        - Expected: extend the parenthetical, e.g. `..., FK reference existence (including columns' ref_table_id pointers, concept `related_object_ids` link resolution, and concept anchor-prefix resolution), identifier syntax, ...`
        - Resolution: Deferred — the docstring frames its list as the `MAINTAINING.md#ci--loader` step-4 contract (the authoritative source), and both checks read fairly as facets of "FK reference existence" whose own docstrings (lines 255-265, 602-630) precisely document the rules; optional summary enrichment only. Same rationale as the cr_20260813v01 deferral.
   - 2.2. [suggestion] Lines 6-8: The same contract list omits the three whole-corpus grouping checks `validate_corpus` invokes: `_check_relationship_pairs` (line 158), `_check_mapping_disambiguation` (line 159), and `_check_mapping_linkability` (line 161). Carried from cr_20260813v01 (1.2).
        - Current: `..., the `cardinality` enum, deployment residency rules, and SQL expression parsability.`
        - Expected: add a clause naming the grouping checks, e.g. `..., and SQL expression parsability — plus whole-corpus grouping checks: relationship orientation-duplicate / use_when disambiguation, per-source-column mapping disambiguation, and multi-table mapping linkability.`
        - Resolution: Deferred — each of the three checkers carries a precise self-documenting docstring and the module docstring is scoped as the step-4 contract summary, so this is optional enrichment, not an inaccuracy in the code's behavior. Same rationale as the cr_20260813v01 deferral.
   - 2.3. [suggestion] Lines 392-393: The `_check_sql_expressions` docstring closes with "the parse tree is memoized for the orchestrator and the linkability check", but the memo is also consumed by `_check_mapping_codeployment` (line 745) — the co-deployment check is an unnamed third consumer. Carried from cr_20260813v01 (1.3).
        - Current: `...; the parse tree is memoized for the orchestrator and the linkability check.`
        - Expected: `...; the parse tree is memoized for the orchestrator and the mapping co-deployment and linkability checks.`
        - Resolution: Deferred — the sentence's point (the memo outlives this function) holds, `validate_corpus`'s wiring comments (lines 150-161) name every downstream consumer two screens up, and the omission misleads no maintainer about behavior; optional wording completeness only. Same rationale as the cr_20260813v01 deferral.
   - 2.4. [suggestion] Lines 881-897: `_all_connected` indexes `tables[0]` (line 888) without documenting that a non-empty list is a precondition; an empty list raises `IndexError` rather than the "trivially connected" answer the name suggests. The sole caller guards with `if len(tables) < 2: continue` (line 855), so the path is unreachable today.
        - Current: `"""Return True if every table in `tables` is reachable from the first. ... """` with no precondition note
        - Expected: add an `Args:` note (e.g. "tables: The referenced tables; must be non-empty — the caller guards with `len(tables) < 2`") or an explicit `if not tables: return True` guard.
        - Resolution: Deferred — the single call site guarantees at least two entries, and the function is private with a name that already implies "connected relative to the first" element; documenting an unreachable precondition is optional hardening only.

3. [completed] Comment naming consistency - `code/load_catalog_data/corpus_validation.py`
   - 3.1. [suggestion] Line 152: The `validate_corpus` section comment reads "Venue sets per table (from deployments) drive the ..." — the precise table name is `deployment_tables`, which neighboring comments (lines 115, 190) use in full. Carried from cr_20260813v01 (2.1).
        - Current: `# Venue sets per table (from deployments) drive the "runnable`
        - Expected: `# Venue sets per table (from deployment_tables) drive the "runnable`
        - Resolution: Deferred — the phrase reads naturally as generic English ("from deployments") rather than a dangling identifier, and the helper it introduces (`_deployment_venues`, line 681) documents the exact table name, so the comment is not misleading. Same rationale as the cr_20260813v01 deferral.

4. [completed] Fail-fast attribute access in `validate_update_reason` - `code/load_catalog_data/corpus_validation.py`
   - 4.1. [suggestion] Lines 960, 968: `getattr(change.new, "update_reason", None)` reads the field with a silent `None` default. Because `deployment_tables` (the only row type lacking `update_reason`) is skipped explicitly above each call (lines 956, 966), every remaining row is one of the eight authored tables that all declare `update_reason` (`data_model.py`), so the default branch is never exercised; direct access `change.new.update_reason` would be more fail-fast per the exception-handling skill. Carried from cr_20260813v01 (3.1).
        - Current: `if getattr(change.new, "update_reason", None) is not None:`
        - Expected: `if change.new.update_reason is not None:`
        - Resolution: Deferred — `RowChange.old`/`new` are typed `Any` (`corpus_diff.py` lines 76-77), so the `getattr` guard is defensive coding against a heterogeneous field; the deployment-row skip already guarantees the attribute is present, so the current form is safe and behavior-equivalent. Optional robustness/readability change only. Same rationale as the cr_20260813v01 deferral.

5. [completed] Avoid recomputing referenced tables per mapping - `code/load_catalog_data/corpus_validation.py`
   - 5.1. [suggestion] Lines 748, 854: `compute_target_tables_referenced(tree)` is called once in `_check_mapping_codeployment` and again in `_check_mapping_linkability` for the same memoized `tree` of each mapping; the derived referenced-tables list could be computed once and shared. Carried from cr_20260813v01 (4.1).
        - Current: `tables = compute_target_tables_referenced(tree)` (computed independently in both checkers)
        - Expected: compute the referenced-tables set once (e.g., alongside the memo) and pass it into both checkers.
        - Resolution: Deferred — the computation is a cheap single tree walk over already-parsed expressions and the two call sites keep each checker self-contained and independently readable; sharing would couple the two checks for negligible gain. Revisit only if profiling flags it. Same rationale as the cr_20260813v01 deferral.

6. [completed] Structural and type-alias consistency - `code/load_catalog_data/corpus_validation.py`
   - 6.1. [suggestion] Lines 640-678: The four-depth anchor resolution in `_check_concept_anchors` is an if/elif ladder of four near-identical branches differing only in the id space and the object-kind phrase; a depth-keyed table would collapse the ladder to one lookup-and-report block. Carried from cr_20260813v01 (5.1).
        - Current: `if len(anchor_labels) == 1: ... elif len(anchor_labels) == 2: ... elif len(anchor_labels) == 3: ... elif len(anchor_labels) == 4: ... else: ...`
        - Expected: a `dict[int, tuple[Mapping[str, object], str, str]]` keyed by anchor depth, with a single shared issue-append using the looked-up space and phrasing.
        - Resolution: Deferred — the ladder is explicit, each branch's message is hand-tuned prose, the depth set (1-4) is fixed by the concept_id shape CHECK so no fifth branch is coming, and the table-driven form would trade four readable blocks for indirection. Optional structural refactor only. Same rationale as the cr_20260813v01 deferral.
   - 6.2. [suggestion] Lines 332, 775, 777: Composite-key annotations spell out raw tuple shapes (`tuple[str, str]` for deployment keys, `tuple[str, str, str]` for relationship keys) where `data_model` exports the `DeploymentKey` and `TableRelationshipKey` aliases (lines 443-444 there) — the module already imports and uses `ColumnMappingKey` (lines 47, 818), so using the sibling aliases would name the intent consistently. Carried from cr_20260813v01 (5.2).
        - Current: `seen_addresses: dict[tuple[str, str, str, str], tuple[str, str]] = {}` / `by_pair_name: dict[tuple[frozenset[str], str], tuple[str, str, str]] = {}`
        - Expected: import `DeploymentKey` and `TableRelationshipKey` from `data_model` and use them as the value types (the four-string address tuple is local and stays literal).
        - Resolution: Deferred — the raw tuples are structurally identical to the aliases, the surrounding comments (lines 774, 776) already name what the values are, and the annotations are correct as written; optional readability alignment only. Same rationale as the cr_20260813v01 deferral.

## Skills with No Issues

1. Type Hints: One optional alias-consistency suggestion (6.2); no blocking issues. Every function carries complete, modern annotations; `validate_update_reason(diff: "Diff") -> None` uses the `TYPE_CHECKING`-guarded forward reference (lines 49-50) correctly, and `validate_corpus`'s `dict[ColumnMappingKey, exp.Expression | None]` return is specific.
2. Docstrings: Four optional completeness suggestions (2.1-2.4); no blocking issues. Every module-level function and the `ValidationError` class is documented Google-style, and the doc cross-references resolve — `MAINTAINING.md#ci--loader` step 4 exists (MAINTAINING.md line 450+) and the "CONTRIBUTING.md wave 1 / wave 2" references (lines 324-325, 737-738, 971-972) match CONTRIBUTING.md's wave headings (lines 553, 564).
3. Comments: One naming-consistency suggestion only (3.1); otherwise comments explain "why" and match the code (the data_source_id/table_id redundancy note at lines 208-213, the malformed-shape skip note at lines 624-626, the self-join constant-predicate rationale at lines 438-441).
4. Logging: No issues found. `get_logger(__name__)` per the library-module pattern (lines 37, 52); no `print()`; both validators emit a single INFO pass milestone (lines 165, 982); no Entering/Exiting noise.
5. Exception Handling: One optional fail-fast suggestion (4.1); no blocking issues. No bare `except`; every handler catches the specific `ValueError` raised by `validate_identifier_segment`, `parse_expression`, and `extract_column_refs` and accumulates into `issues`, surfacing all via `ValidationError`. The two `raise ValidationError(issues)` calls (lines 164, 981) are fresh raises outside any `except`, so `from e` chaining does not apply.
6. Data Validation: N/A - governs `data_val_`-prefixed output-validation scripts; this is loader business-logic validation, not a data-quality output check.
7. Executable Scripts: N/A - imported library module with no `main()` / `__main__` entry point.
8. Unit Tests: N/A - source file, not a test file; its tests live in `unit_tests/test_corpus_validation.py` (reviewed in cr_20260813v02_test_corpus_validation.md, which records the one uncovered branch, line 637).
9. SAS Conversion: N/A - no SAS source involved.
10. PySpark: N/A - no PySpark/Databricks code.
11. Ibis: N/A - no Ibis code.
