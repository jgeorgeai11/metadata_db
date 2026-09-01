---
name: 20260722v01_aggregate_assembly_errors
goal: Aggregate discovery- and assembly-stage authoring errors the way validation already aggregates its rules — collect every path-classification and row-shape issue across the whole corpus and report them together, instead of aborting on the first offender. An author with 40 bad rows sees one report, not 40 CI round-trips; validation continues to run only on a clean corpus, so no cascade noise is introduced. Reporting-only change: loader behavior on a valid corpus is byte-identical — no DDL, no data changes, no reload.
created: 2026-07-22 14:23:28
updated: 2026-07-22 15:05:26
---

## Implementation Plan

> Ordering: the error type and discovery aggregation first (Phase 1) — they
> define the aggregation contract assembly plugs into — then the assembly
> refactor (Phase 2), the orchestrator touch (Phase 3), tests (Phase 4),
> a front-half verification against the real corpus (Phase 5), docs
> (Phase 6), and review (Phase 7).
>
> Target error model (three waves maximum, down from unbounded):
>   - Wave 1 — **discovery + assembly**, aggregated together: misplaced
>     files, unparsable YAML, wrong document shapes, and every row-level
>     shape issue (bad identifier segment, missing/mis-typed field,
>     unrecognized key, path-agreement, reserved word, duplicate PK)
>     across all files, in one report.
>   - Wave 2 — **validation** (unchanged `validate_corpus` aggregation),
>     which runs only when wave 1 is clean.
>   - Wave 3 — **update_reason discipline** (unchanged; needs the diff).
>   Environment failures (missing `systems/` root, unreadable config,
>   missing env vars) stay fail-fast — they are operator errors, not
>   authoring errors, and aggregation would not help.

### Phase 1 — Error type + discovery aggregation

1. [completed] Aggregate path-classification errors across the walk - `code/load_metadata_db/yaml_discovery.py`
   - 1.1. `discover_yaml_files`: instead of raising on the first `decode_path` failure, collect every classification error (misplaced file, bad segment charset, reserved schema word, unsupported `concepts.yaml` depth) while continuing the walk, and return the successfully classified identities alongside the collected issue strings (each naming the offending path)
   - 1.2. `FileNotFoundError` for a missing `{data_root}/systems/` stays fail-fast — an environment error, not an authoring error
   - 1.3. Update the module docstring and `discover_yaml_files` docstring to state the aggregation contract

### Phase 2 — Assembly aggregation

2. [completed] Collect row-shape issues corpus-wide; raise once, at the end - `code/load_metadata_db/corpus_assembly.py`
   - 2.1. Define `AssemblyError(ValidationError)` with a stage-naming summary line (`Corpus assembly failed with N issue(s): …`) — subclassing means the orchestrator's existing `except ValidationError` arm and per-issue logging work unchanged
   - 2.2. `assemble_corpus`: accept the discovery issues from Phase 1, accumulate assembly issues into the same list, and raise one `AssemblyError` carrying all of them after the full corpus walk; return a complete `Corpus` only when the list is empty
   - 2.3. File-level granularity: a YAML parse failure (`load_yaml`) or a wrong document shape (`_require_mapping` / `_require_list`) records **one issue for the file** and skips that file's rows; remaining files still process
   - 2.4. Row-level granularity: refactor the per-row assemblers (`_assemble_tables`, `_assemble_columns`, `_assemble_table_relationships`, `_assemble_column_mappings`, `_assemble_concepts`) so each row's shape failures — a non-mapping list item, invalid identifier segment, missing/mis-typed required field, unrecognized key, body-`system` mismatch, path-agreement violation, reserved `concept` word, malformed `related_object_ids` — record an issue naming the file and the row (by its natural identifier where derivable, else the row content) and skip that row, while sibling rows still assemble. The single-row file types (`system`, `data_source`, `schema`) behave like one-row lists
   - 2.5. Duplicate PKs (`_record`): the first occurrence keeps its place in the corpus dict; each later occurrence records an issue naming both the key and the file. Deterministic and tested — though the choice only affects messages, since a dirty run never reaches validation or the DB
   - 2.6. Keep the assembly summary log line on success only; update the module docstring

### Phase 3 — Orchestrator wording

3. [completed] Stage-neutral error surfacing - `code/load_metadata_db/load_metadata_db.py`
   - 3.1. `run`: pass discovery issues into `assemble_corpus` per the Phase 1/2 contract; update the step list in the docstring (discovery + assembly report together; validation runs only on a clean corpus — existing behavior, now stated)
   - 3.2. `main`: the `except ValidationError` arm currently logs `Validation failed: N issue(s)` — reword to rely on the exception's own stage-naming summary so `AssemblyError` does not log as a validation failure

### Phase 4 — Tests (one per changed module)

4. [completed] Update + run discovery tests - `code/load_metadata_db/unit_tests/test_yaml_discovery.py`
   - 4.1. Cover: multiple misplaced/malformed paths in one walk all reported together; valid files still classified alongside bad ones; missing `systems/` root still raises `FileNotFoundError` immediately

5. [completed] Update + run assembly tests - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
   - 5.1. Convert the existing first-offender `pytest.raises(ValueError, match=…)` assertions to assert on the aggregated `AssemblyError.issues` (each existing case remains covered — same trigger, new reporting shape)
   - 5.2. New aggregation cases: several bad rows in one file each reported and good siblings assembled; bad rows across multiple files reported together; a parse-broken file skipped as one issue while other files' issues still collected; duplicate PK first-wins with both occurrences named; discovery issues and assembly issues surfacing in one `AssemblyError`

6. [completed] Update + run orchestrator tests - `code/load_metadata_db/unit_tests/test_load_metadata_db.py`
   - 6.1. Cover: a corpus with multiple shape errors exits 1 logging every issue (not just the first); validation rules do not run when assembly is dirty (e.g. a corpus with both a shape error and an FK error reports only the shape error in wave 1); the happy path is unchanged

### Phase 5 — Verify against the real corpus

7. [completed] Front-half + full-suite verification - loader-run, out-of-band
   - 7.1. Discover → assemble → validate the shipped `data/` corpus: passes with zero issues (reporting-only change; the corpus is clean)
   - 7.2. A `--dry-run` against the live DB reports `Diff: 0/0/0` — loader behavior on a valid corpus is byte-identical
   - 7.3. Full unit suite green at the 100%-coverage bar

### Phase 6 — Documentation

8. [completed] Update the maintenance doc - `readme/metadata-db-maintenance.md`
   - 8.1. "CI & loader" step 1/4 wording: state that discovery and assembly errors are aggregated like validation issues — the doc's existing "authors see every problem per run" promise now holds for all three waves — and describe the wave model in one sentence (shape issues first; cross-reference validation runs once shape is clean)

### Phase 7 — Review

9. [completed] Code review of changed files and address findings - `docs/code_review/`
   - 9.1. Run the `code-review` skill over `yaml_discovery.py`, `corpus_assembly.py`, `load_metadata_db.py`, and the three changed test files, writing `cr_*.md`
   - 9.2. Address findings via `code-implementation`; re-run the suite at the 100%-coverage bar

## Key Data Decisions and Considerations

1. **Per-stage aggregation, not merged stages.** Assembly could build a partial corpus from the good rows and let validation run over it, producing one unified report — but a skipped column row would then spawn phantom "unknown column" errors in every mapping and expression referencing it, and suppressing those cascades means threading skipped-id awareness through every validation rule. Instead, validation runs only on a clean corpus (unchanged), and the author sees **at most three waves**: all shape issues, then all cross-reference issues, then `update_reason` discipline. Deliberately accepted trade-off — recorded so a future "finish the job" unification is weighed against the cascade-suppression cost, not assumed to be an oversight.

2. **Discovery errors join the assembly wave.** A misplaced file and a malformed row are the same authoring class and should be fixed in the same pass, so `decode_path` failures aggregate into the same report rather than forming a fourth wave. Environment failures (missing `systems/` root, config, env vars) remain fail-fast: they mean the run itself is misconfigured, and listing them alongside YAML issues would misdirect the author.

3. **`AssemblyError` subclasses `ValidationError`.** The orchestrator already catches `ValidationError` and logs each issue; subclassing gets identical CLI/CI behavior with no new handler, while the exception's own summary line names the stage. No import cycle: `corpus_validation` does not import `corpus_assembly`.

4. **Granularity: one issue per bad row, one issue per structurally broken file.** A file that cannot parse (or whose document is the wrong shape) cannot yield rows, so it contributes a single issue and its rows are skipped; a well-formed file with bad rows contributes one issue per bad row while good siblings still assemble — which keeps cross-file duplicate-PK detection meaningful on the surviving rows.

5. **Duplicate-PK first-wins is cosmetic but deliberate.** With aggregation, a duplicate can no longer abort the walk, so the corpus dict must hold one of the occurrences; the first wins and every later one is reported. The choice affects only message content — a run with any issue never reaches validation or the DB — but it is pinned by a test so the behavior is stable.

6. **Reporting-only: no DDL, no data change, no reload.** A clean corpus assembles into an identical `Corpus` and the load path is untouched; Phase 5's dry-run `0/0/0` is the proof. `apply_ddl` and `revert_merge` are out of scope.

7. **Sequenced before the lowercase-mandate activity.** The mandate will make charset violations the most common authoring error during adjustment, and upcoming real-system onboarding means bulk-authored files where first-offender reporting costs one CI round-trip per mistake. Landing aggregation first means the mandate rolls out with one-report ergonomics from day one.

8. **Grouped by concern, not per-unit.** Same structure as the prior metadata-db activities: one cohesive change to the loader's error-reporting layer, phased as discovery → assembly → orchestrator → tests → verify → docs rather than create→test→run per file; the loader's own front-half run (Phase 5) is the output validation, per house precedent.

9. **(Implementation note) One small out-of-plan touch to `corpus_validation.py`.** `AssemblyError`'s stage-naming summary is implemented via an overridable `_SUMMARY_PREFIX` class attribute on `ValidationError` (plus, from code review, a first-class `summary` attribute the orchestrator logs instead of parsing `str(e)`). Behavior-preserving for validation — its summary line and per-issue logging are byte-identical — and keeps the two stages' summary formats from drifting apart.

10. **(Implementation note) Phase 5 verified.** Front-half run over the shipped `data/` corpus: 23 files discovered, 0 issues, assembled (3/3/3/13/69/11/12/6) and validated clean; live-DB `--dry-run` with the shipped config logged `DRY RUN — Diff: 0 insert(s), 0 update(s), 0 delete(s)`; full unit suite 373 passed with 100% coverage on every loader source module (integration tests env-gated as usual).

11. **(Implementation note) Inline Phase 7 attempt discarded.** The implementation agent initially wrote six `cr_20260722v01_*` review files itself and addressed 3 minor findings in the same pass (concept row-helper ordering, `e.summary` instead of string-parsing, test blank-line style — those code fixes are kept). The self-review files were deleted as not arms-length; Task 9 is reset to pending for a proper `code-review` skill run with independent reviewer agents.
