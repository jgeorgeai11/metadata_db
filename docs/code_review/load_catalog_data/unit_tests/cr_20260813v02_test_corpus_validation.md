---
name: cr_20260813v02_test_corpus_validation
goal: Re-review code/load_catalog_data/unit_tests/test_corpus_validation.py against python-development skills since cr_20260813v01 (whose dead-arrange findings 1.1/1.2 are confirmed implemented), reviewed as a group with corpus_validation.py and the sibling test_corpus_diff.py, adding a coverage gap and the group's fixture-convention divergences.
created: 2026-08-13 11:48:48
updated: 2026-08-13 12:21:32
---

## Implementation Plan

1. [completed] Close the one uncovered branch in the module under test - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 1.1. [minor] Line 1210 / after line 1222: `_check_concept_anchors`' malformed-shape skip (`corpus_validation.py` lines 636-637: `if len(segments) < 3 or segments[-2] != "concept": continue`) is the module's only unexecuted statement — `python -m pytest unit_tests/test_corpus_validation.py unit_tests/test_corpus_diff.py --cov=corpus_validation --cov-report=term-missing` reports 99% with `Missing: 637`. The existing `test_validate_corpus_malformed_concept_id_raises` does not reach it: `"ocs.concept.bad id"` has three segments with `concept` second-to-last, so it passes the shape gate and fails on identifier syntax instead. The skip encodes a documented contract ("Malformed-shape ids ... are skipped here rather than double-reported", `corpus_validation.py` lines 624-626), so it deserves a pin, per the unit-tests skill's cover-all-paths guideline.
        - Current: the concepts section (lines 1183-1222) exercises only well-shaped ids; no test asserts the no-double-report behaviour for a concept id missing the reserved `concept` segment.
        - Expected: add e.g. `def test_validate_corpus_concept_id_without_reserved_segment_reports_no_anchor_issue() -> None:` that inserts `_bare_concept("ocs.general.notaconcept")` into `_happy_corpus()` and asserts `v.validate_corpus(corpus)` raises no anchor issue (the shape gate skips it; the id-syntax check and the DB CHECK own malformed shapes).
        - Resolution: Implemented as specified — added `test_validate_corpus_concept_id_without_reserved_segment_reports_no_anchor_issue` after `test_validate_corpus_malformed_concept_id_raises` in the concepts section, inserting `_bare_concept("ocs.general.notaconcept")` into `_happy_corpus()` and calling `v.validate_corpus(corpus)` with no `pytest.raises` (a raise fails the test), plus a comment recording why the shape gate skips it. `corpus_validation.py` line 637 is now covered and the module reports 100% statement coverage. Also added in this changeset (from the group's `cr_20260813v02_corpus_validation.md` finding 1.1): `test_validate_corpus_bad_identifier_mapping_name_raises` in the Identifier-syntax section, pinning the new `mapping_name` re-check.

2. [completed] Fixture construction readability - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 2.1. [suggestion] Lines 92-104, 798-801: `_happy_corpus` and the rule-D setup build `ColumnRow`s from nine positional arguments, two of which are adjacent unlabelled booleans (`is_nullable`, `is_primary_key`); a silent swap would still construct a valid row and quietly change what the test asserts. The file's other builders (`_dep` lines 47-54, `_rel` lines 658-668, `_cm` lines 678-687, `_edw_column` lines 692-702) all use keyword arguments.
        - Current: `ColumnRow(_BENE_ID, "ocs.general.bene", "bene_id", "TEXT", False, True, "d", None, None,)`
        - Expected: keyword form for at least the flags, e.g. `ColumnRow(column_id=_BENE_ID, table_id="ocs.general.bene", column_name="bene_id", data_type="TEXT", is_nullable=False, is_primary_key=True, description="d", notes=None, update_reason=None)` — or route these rows through the existing `_edw_column`-style builder.
        - Resolution: Deferred — the positional rows are correct as written (`bene_id` is a non-nullable PK, `bene_extl_id` a nullable non-PK), the field order matches `data_model.ColumnRow`, and the compact form keeps the fixture block scannable; optional readability hardening only.
   - 2.2. [suggestion] Lines 410, 468, 562: three lines exceed the ~79-column wrap that `corpus_validation.py` holds to without exception (that module has zero lines over 79); two of them are trailing comments (`# collides w/ bene`, `# source column's own table (ocs.general.bene)`) that could move above the statement.
        - Current: `corpus.deployment_tables[key], physical_table_name="bene"  # collides w/ bene` (85 cols)
        - Expected: move the trailing comment to its own line above the `replace(...)` call, and wrap the long `join_condition=` f-string at line 468.
        - Resolution: Deferred — the remaining over-length lines in the file are `def test_...() -> None:` signatures whose descriptive names are worth more than the wrap, no linter enforces a limit in `pyproject.toml`, and these three are readable as written; purely cosmetic.

3. [completed] Cross-file consistency with the sibling test module - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 3.1. [suggestion] Lines 38-54, 649-687 vs `test_corpus_diff.py` lines 21-100: the two sibling test modules answer the same question — "how do we build a row fixture?" — two different ways. The same concepts carry different names and shapes (`_dep(table_id, system, ...)` here vs `_deployment(physical_table_name=...)` there; `_cm(source, name, expr, use_when)` here vs `_mapping(target_expression, reason)` there; `_rel(a, b, name, cond, ...)` here vs a zero-positional `_rel(cardinality, reason)` there), and `_REL_KEY = ("ocs.general.bene", "ocs.general.claim", "default")` is duplicated verbatim in both. `unit_tests/conftest.py` already exists as the shared-fixture home the unit-tests skill (3.1) points at. Recorded in both reviews — see cr_20260813v02_test_corpus_diff.md finding 3.1.
        - Current: two private builder sets with overlapping intent and clashing signatures for the same row types
        - Expected: promote the shared row builders and key constants into `unit_tests/conftest.py` (or a `_row_builders.py` helper module) with one signature per row type, and import them in both test modules
        - Resolution: Deferred — each module's builders are tuned to its own needs (this file varies endpoints/expressions per test; the diff file varies only a content field on a fixed PK), so a merged signature would be wider than either caller wants; the duplication is small, local, and does not risk drift in assertions.
   - 3.2. [suggestion] Line 1180 vs `test_corpus_diff.py` line 25: both files define a module constant named `_CLAIM_ID`, but with different values — `"ocs.concept.claim"` here (consistent with this file's `ocs` corpus) and `"sandbox_ocs.concept.claim"` there (a data source no other fixture in that file mentions). One name, two id namespaces across the group.
        - Current: `_CLAIM_ID = "ocs.concept.claim"` (this file) / `_CLAIM_ID = "sandbox_ocs.concept.claim"` (sibling)
        - Expected: settle on one prefix across both modules (the `ocs` used by every other fixture id in the group) so a reader moving between the files reads one namespace
        - Resolution: Deferred — this file's value is already the consistent one, and `corpus_diff` never resolves concept anchors, so the sibling's prefix is inert; the fix belongs to the sibling (cr_20260813v02_test_corpus_diff.md finding 3.2) and is cosmetic there.

4. [completed] Test data sharing convention - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 4.1. [suggestion] Line 57: `_happy_corpus()` is a module-level builder function rather than a `@pytest.fixture`; the unit-tests skill (3.1) prefers sharing common test data via fixtures. Carried from cr_20260813v01 (2.1).
        - Current: `def _happy_corpus() -> Corpus:` called directly in each test (e.g. `corpus = _happy_corpus()`)
        - Expected: a function-scoped `@pytest.fixture` (e.g. `def happy_corpus() -> Corpus: return _happy_corpus()`) injected as a parameter
        - Resolution: Deferred — the builder pattern is deliberate and ergonomic here: nearly every test mutates the returned `Corpus` before acting, which reads more directly from a plain call than from an injected fixture; each call returns a fresh, independent object, so the isolation goal of fixtures is already met. Same rationale as the cr_20260813v01 deferral.
   - 4.2. [suggestion] Lines 1305-1408, 1517-1537: with the `corpus` parameter gone from `validate_update_reason`, the gate tests only need individual rows, yet seven of them (lines 1327, 1344, 1369, 1383, 1395, 1518, 1530) still build a full nine-table `_happy_corpus()` to pluck or `replace` one row. Carried from cr_20260813v01 (2.2).
        - Current: `corpus = _happy_corpus()` then `row = replace(corpus.systems["warehouse"], update_reason=...)`
        - Expected: derive rows from a minimal builder (or `replace` on a module-level constant row) in the `validate_update_reason` section.
        - Resolution: Deferred — building the happy corpus is cheap, keeps every section of the file on one shared baseline, and the reads are live and correct now that cr_20260813v01's dead write-backs are gone; purely an optional economy.

5. [completed] Test redundancy - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 5.1. [suggestion] Lines 941-942, 972-973, 1173-1174: three tests (`test_validate_corpus_single_relationship_null_use_when_accepted`, `test_validate_corpus_m1_scalar_accepted`, `test_validate_corpus_m9_single_table_accepted`) have the identical one-line body and re-run the exact scenario already covered by `test_validate_corpus_happy_path` (line 164). Carried from cr_20260813v01 (3.1).
        - Current: `v.validate_corpus(_happy_corpus())` repeated as the entire body of three separately named tests
        - Expected: rely on `test_validate_corpus_happy_path` alone, or keep one named acceptance test per rule only where the input differs from the baseline
        - Resolution: Deferred — the duplicate executions are cheap, and each named test documents a specific rule's acceptance baseline inside its own rule section; deleting them would trade a few milliseconds for weaker per-rule regression naming. Same rationale as the cr_20260813v01 deferral.
   - 5.2. [suggestion] Lines 777-787 and 837-847: `test_validate_corpus_b_self_join_single_name_accepted` and `test_validate_corpus_d_self_join_condition_accepted` build near-identical corpora (same self-relationship on `ocs.general.bene` with the same join condition; only the relationship name differs) and assert the same outcome. Carried from cr_20260813v01 (3.2).
        - Current: two structurally identical accepted-self-join tests under the rule-B and rule-D sections
        - Expected: a single self-join acceptance test, or a parametrized case shared by both rule sections
        - Resolution: Deferred — the two tests intentionally guard two different validation rules (B: orientation-duplicate detection; D: endpoint coverage) against independent regressions, and keeping each rule section self-contained is a readability choice consistent with the file's section-per-rule layout. Same rationale as the cr_20260813v01 deferral.

6. [completed] Docstring formatting - `code/load_catalog_data/unit_tests/test_corpus_validation.py`
   - 6.1. [suggestion] Lines 1024-1026: `_add_two_edw_tables` has a multi-line docstring whose summary wraps onto a second line with no blank line before the continuation; Google style prefers a one-line summary, a blank line, then the body. Carried from cr_20260813v01 (4.1).
        - Current: `"""Add edw tables t1, t2 (one column each). Deploy t1 in edw; t2 in edw\n    when `both_in_edw`, else warehouse only (so they share no venue)."""`
        - Expected: `"""Add edw tables t1 and t2 (one column each).\n\n    Deploy t1 in edw; t2 in edw when `both_in_edw`, else warehouse only (so they share no venue).\n    """`
        - Resolution: Deferred — a formatting nit on a private helper whose content is complete and accurate; the compact form matches the terse one-liner style used by the file's other helpers (`_dep`, `_rel`, `_cm`). Same rationale as the cr_20260813v01 deferral.

## Skills with No Issues

1. Type Hints: No issues found — every test function is annotated `-> None`, parametrized arguments are typed (`cardinality: str | None`, `expr: str`, `concept_id: str, fragment: str`), and all helpers carry full parameter and return annotations using modern syntax. Unlike the sibling module, no helper annotation contradicts its row's declared field types.
2. Docstrings: One formatting suggestion (6.1); otherwise the module has a docstring, every helper has one, and test functions convey intent via descriptive names plus rationale comments per project convention.
3. Comments: No issues found — the section headers and rule references match the current code (the deployment file rules / target-expression shape rule notes at lines 417-419 and 1071-1074 correspond to CONTRIBUTING.md's wave headings), and cr_20260813v01's dead-arrange code is gone, so no comment or setup step misstates what a test depends on.
4. Unit Tests: One coverage finding (1.1) plus suggestions (2.1, 2.2, 3.1, 3.2, 4.1, 4.2, 5.1, 5.2) — pytest throughout; file name matches `test_<module>.py`; function names follow `test_<function>_<scenario>_<expected>`; tests are AAA-structured and independent (a fresh `_happy_corpus()` per test, no shared mutable state); `pytest.raises(..., match=...)`, `@pytest.mark.parametrize`, and `pytest.param(..., id=...)` are used appropriately; assertions target the public `ValidationError.issues` API rather than private internals; the module's 110 tests pass and drive `corpus_validation.py` to 99% statement coverage.
5. Logging: N/A - test module performs no logging.
6. Exception Handling: N/A - tests assert on raised exceptions rather than handling them; no production error-handling code.
7. Executable Scripts: N/A - not an executable script.
8. Data Validation: N/A - exercises the validation module under test rather than validating pipeline I/O.
9. SAS Conversion / PySpark / Ibis: N/A - no SAS, Spark, or Ibis code present.
