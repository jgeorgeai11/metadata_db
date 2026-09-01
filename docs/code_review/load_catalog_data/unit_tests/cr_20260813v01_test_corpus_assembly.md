---
name: cr_20260813v01_test_corpus_assembly
goal: Address code quality issues identified in code/load_catalog_data/unit_tests/test_corpus_assembly.py to align with python-development (unit-tests, comments, type-hints, docstrings) skills; re-review since cr_20260803v02, whose deferred suggestions remain in place and whose scope now includes comment staleness introduced by the puf-source removal.
created: 2026-08-13 11:46:14
updated: 2026-08-13 12:22:11
---

## Implementation Plan

1. [completed] Refresh stale test comments - `code/load_catalog_data/unit_tests/test_corpus_assembly.py`
   - 1.1. [minor] Line 1648: `test_assemble_concepts_live_names_compose_unchanged_ids` states its parameters are "Representative migrated names from the shipped corpus", but the `live_schema_scope` case (lines 1632-1638) uses `puf` / `hh_pps_lupa`, and commit bfae7c6 removed the puf data source — `data_catalog/sources/` now holds only `ocs`, `edwc_prd`, and `ref`. The test still passes (it assembles a synthetic tmp_path corpus), so the false claim is invisible at run time; the other two cases (`edwc_prd.concept.edw_naming_abbreviations`, `edwc_prd.claims_vw_prd.v_clm.clm_type_cd.concept.claim_type_code`) remain live.
        - Current: `pytest.param("puf", "hh_pps_lupa", "concept.lupa_payment", "puf.hh_pps_lupa.concept.lupa_payment", id="live_schema_scope")`
        - Expected: `pytest.param("edwc_prd", "claims_vw_prd", "concept.four_part_claim_key", "edwc_prd.claims_vw_prd.concept.four_part_claim_key", id="live_schema_scope")`
        - Resolution: Implemented as specified — the `live_schema_scope` case (lines 1632-1638) now uses the replacement params, restoring the "representative migrated names from the shipped corpus" claim. Verified live before substituting: `data_catalog/sources/edwc_prd/claims_vw_prd/concepts.yaml` line 76 authors `- name: concept.four_part_claim_key` in a schema-scoped file, so the composed id is `edwc_prd.claims_vw_prd.concept.four_part_claim_key`. The test's comment was left as written (its "pre-change loader" framing is the subject of deferred suggestion 1.2). 218 tests in this file and 725 in the loader suite pass.
   - 1.2. [suggestion] Lines 1599-1611: `test_assemble_concepts_authored_name_composes_pre_change_id_byte_for_byte` and its comment ("Backward compatibility: ... exactly the id the injecting loader composed from a bare leaf") reference a pre-change loader that no longer exists in the repo, so a reader has nothing to compare "byte-for-byte" against; the surviving value of the test is that it pins `concept.{leaf}` id composition at both file scopes.
        - Current: `def test_assemble_concepts_authored_name_composes_pre_change_id_byte_for_byte(`
        - Expected: `def test_assemble_concepts_authored_name_composes_id_at_both_scopes(` with the comment restated as the current rule (path scope + `.` + authored name) rather than a comparison to a removed loader
        - Resolution: Deferred — optional; the migration framing is historical context only, the assertions still pin the live composition rule, and renaming a passing test churns the suite without changing coverage.

2. [completed] Tighten loose substring assertions (1.1-1.2 carried over from cr_20260803v02) - `code/load_catalog_data/unit_tests/test_corpus_assembly.py`
   - 2.1. [suggestion] Line 530: `test_data_source_missing_owner_rejected` asserts the bare substring `"owner"`, which also appears in the offending row's `repr` embedded in every data_source message, so it cannot distinguish the missing-field message from an unrelated one. The companion `test_data_source_missing_owner_and_description_reports_both` (line 563) already uses the precise backticked form.
        - Current: `assert any("owner" in i for i in issues)`
        - Expected: `assert any("Missing or blank `owner`" in i for i in issues)`
        - Resolution: Deferred — optional precision improvement; the assertion still verifies the intended behavior and the exact message form is pinned by the neighbouring both-missing test, so tightening here adds little coverage.
   - 2.2. [suggestion] Line 540: `test_data_source_missing_description_rejected` asserts `"description" in i.lower()`, an even broader match (the word appears in many unrelated messages and in the row `repr`).
        - Current: `assert any("description" in i.lower() for i in issues)`
        - Expected: `assert any("Missing or blank `description`" in i for i in issues)`
        - Resolution: Deferred — optional; the loose form is intentionally lenient and the exact wording is asserted by the both-missing test, so the risk of a false pass is low.
   - 2.3. [suggestion] Line 641: `test_systems_registry_missing_system_key_rejected` asserts `"system" in issues[0]`, which matches the file path (`systems.yaml`) present in every systems message as well as `"Expected a mapping per system"` — so the assertion would pass on the wrong failure. `_assemble_system_row` raises `Missing or non-string `system` in ...`, and line 760 already asserts that exact fragment for the deployments equivalent.
        - Current: `assert "system" in issues[0]`
        - Expected: `assert "Missing or non-string `system`" in issues[0]`
        - Resolution: Deferred — optional; the paired `assert len(issues) == 1` bounds the failure to this single-row document, so the loose fragment cannot silently mask a different defect in practice.

3. [completed] Reduce duplication among test fixtures and helpers - `code/load_catalog_data/unit_tests/test_corpus_assembly.py`
   - 3.1. [suggestion] Lines 279-281, 298-300, 836-838: the same "filter deployment_tables to pagila" dict comprehension is spelled out three times, and each copy runs to ~90 characters against a file that otherwise wraps near 79. A small helper beside `_assemble_tree` would collapse all three.
        - Current: `pagila_deps = {k: v for k, v in corpus.deployment_tables.items() if v.data_source_id == "pagila"}`
        - Expected: `pagila_deps = _deployment_rows_for(corpus, "pagila")` with `def _deployment_rows_for(corpus: Corpus, data_source_id: str) -> dict[tuple[str, str], Any]:` defined once
        - Resolution: Deferred — optional DRY cleanup; three occurrences of a one-line comprehension keep each test readable in isolation, which is the more valuable property in a 2,800-line suite.
   - 3.2. [suggestion] Lines 1342-1348 and 2489-2495: `_COL_FIELDS` and `_COLUMN_ROW` are two module-level "minimal valid column row" constants differing only in incidental values (`clm_type_cd`/TEXT/not-null vs `id`/INT/nullable), defined ~1,150 lines apart. A reader has to check which one a test uses to know the row's shape.
        - Current: two constants, `_COL_FIELDS` (line 1342) and `_COLUMN_ROW` (line 2489)
        - Expected: one shared minimal-column constant reused by both the ref_table and whitespace-freeform sections
        - Resolution: Deferred — optional consolidation; each constant sits next to the section it serves and the values are deliberately distinct per section, so merging them would move a definition far from its use.

4. [completed] Define shared helpers before first use (carried over from cr_20260803v02) - `code/load_catalog_data/unit_tests/test_corpus_assembly.py`
   - 4.1. [suggestion] Lines 703 / 725: `test_tables_missing_or_blank_description_rejected` and `test_columns_missing_or_blank_description_rejected` call `_path_id(...)`, but that helper is defined later at line 851 (under the "Document-shape guards" banner). It works because pytest resolves the name at run time, but forward references make the file harder to follow top-to-bottom.
        - Current: `_path_id` first used at line 703, defined at line 851
        - Expected: move `_path_id` above its earliest use, or relocate the two description tests to sit after the helper's definition
        - Resolution: Deferred — cosmetic ordering only; the helper resolves correctly at collection/run time and the current grouping keeps `_path_id` beside the shape-guard tests it primarily serves.

## Skills with No Issues

1. unit-tests: No issues found — the file/function names follow `test_<module>.py` and `test_<function>_<scenario>_<expected>`; pytest is used throughout with `@pytest.mark.parametrize` (including stacked parametrization and explicit `pytest.param` ids), `pytest.raises`, `tmp_path`, and a function-scoped `example_corpus` fixture; every test builds its own tree so none depends on execution order (the loader's own order independence is pinned by `test_assemble_corpus_issue_order_independent_of_input_order` at line 1995); assertions target the public `assemble_corpus` / `load_yaml` / `AssemblyError.issues` contract rather than private state. Coverage was verified: `uv run pytest code/load_catalog_data/unit_tests/test_corpus_assembly.py --cov=corpus_assembly --cov-report=term-missing` reports 218 passed and 100% statement coverage of `corpus_assembly.py` (639 statements, 0 missed).
2. type-hints: No issues found — every helper, fixture, test, nested function (`_run` at line 2005), and shared dict/list constant carries modern annotations (`list[str]`, `str | None`, `dict[str, Any]`, `list[tuple[str, dict[str, Any], str]]`). The single `# type: ignore[arg-type]` in `_path_id` (line 860) is a justified narrowing to the `file_type` Literal.
3. docstrings: No issues found — the module docstring states the suite's contract (aggregate on `AssemblyError.issues`, inline venue-free fixture tree) and every shared helper and fixture (`_build_corpus_tree`, `_assemble_tree`, `_assemble_tree_issues`, `_write_doc`, `_issues_for`, `_corpus_for`, `example_corpus`, `_systems_ident`, `_set_pagila_deployments`, `_path_id`, `_concept_path_id`) has one; per standard pytest practice the test functions rely on descriptive names plus intent comments.
4. comments: Issues found — see task 1 (stale references to the removed puf data source and to the pre-change loader). The remaining comments are sound: they explain the rule or regression under test rather than restating the code, and the cr_20260803v02-era task-number references ("Task 6.1"-"Task 6.4") have since been replaced with rule descriptions.
5. exception-handling: N/A — test code asserts on raised exceptions via `pytest.raises`; it does not implement production exception handling.
6. logging: N/A — no logging in test code.
7. data-validation: N/A — this is the test suite exercising the loader's validation, not a module performing validation itself.
8. executable-scripts: N/A — not an executable script.
</content>
</invoke>
