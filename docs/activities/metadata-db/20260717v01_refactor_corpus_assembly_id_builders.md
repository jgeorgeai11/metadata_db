---
name: 20260717v01_refactor_corpus_assembly_id_builders
goal: Remove the duplicated composite-ID string construction in `corpus_assembly.py` flagged by code review `cr_20260717v01_corpus_assembly.md` finding 1.1, where the dotted `{system}.{db}.{schema}[.{table}[.{column}]]` IDs are hand-built across six assemblers and the schema prefix is re-parsed inline in two places (the format lives in ~10 spots that must move in lockstep). Introduce a single shared set of ID builder/parser helpers beside `data_model.pk` and route every assembler through them. Behavior-preserving — the assembled IDs stay byte-identical, so no schema, corpus, or DB change.
created: 2026-07-17 10:29:53
updated: 2026-07-17 10:44:25
---

## Implementation Plan

> Scope: pure refactor, no behavior change. The helpers must produce byte-identical IDs to the current inline construction, so the existing `test_corpus_assembly.py` / `test_integration.py` suites are the correctness guard — a green run with no test-logic changes IS the proof. No `0001` edit, no DB rebuild, no corpus reload. Ordering: add + test the helpers (Phase A) before rewiring the call sites (Phase B).
>
> Source of the finding: `docs/code_review/load_metadata_db/cr_20260717v01_corpus_assembly.md` finding 1.1 (deferred twice; this activity closes it).

### Phase A — Shared ID helpers

1. [completed] Add composite-ID builder/parser helpers - `code/load_metadata_db/data_model.py`
   - 1.1. Add small pure functions beside `pk()` that define the dotted-ID format once: `data_source_id(system, database)`, `schema_id(system, database, schema)`, `table_id(system, database, schema, table)`, `column_id(system, database, schema, table, column)` (or `column_id(table_id, column)` — pick one and use it consistently), each returning the `.`-joined string
   - 1.2. Add `schema_prefix(dotted_id)` returning the first three segments (`{system}.{database}.{schema}`) of any table/column ID — the parse currently spelled `".".join(x.split(".")[:3])`
   - 1.3. Take plain string segments (not a `PathIdentity`) so `data_model.py` stays free of a `yaml_discovery` import and the module remains a pure dataclass/registry/identity module (see Key Decision 2). Full modern type hints + Google-style docstrings per `python-development`
   - 1.4. Do not change `pk()`, the dataclasses, or the registries — this task only adds the four builders + one parser

2. [completed] Add + run tests for the ID helpers - `code/load_metadata_db/unit_tests/test_data_model.py`
   - 2.1. Assert each builder composes the expected dotted string (e.g. `schema_id("sandbox", "pagila", "public") == "sandbox.pagila.public"`; `column_id(...)` chains onto `table_id(...)`)
   - 2.2. Assert `schema_prefix` returns the first three segments for a 4-segment `table_id` and a 5-segment `column_id`, and is the exact inverse the assemblers rely on (round-trip: `schema_prefix(table_id(s, d, sc, t)) == schema_id(s, d, sc)`)
   - 2.3. Run `uv run pytest code/load_metadata_db/unit_tests/test_data_model.py -v --cov=data_model --cov-report=term-missing`

### Phase B — Route assemblers through the helpers

3. [completed] Replace inline ID construction/parsing with the helpers - `code/load_metadata_db/corpus_assembly.py`
   - 3.1. `_assemble_data_source` (line ~203) and `_assemble_schema` (lines ~221-222): build `data_source_id` / `schema_id` via the helpers
   - 3.2. `_assemble_tables` (lines ~254-257) and `_assemble_columns` (lines ~308-313): build `table_id` (and `column_id`) via the helpers
   - 3.3. `_assemble_table_relationships` C2 anchor (lines ~364-366): compute the file's schema via `schema_id(...)` and the `table_a_id` prefix via `schema_prefix(raw["table_a_id"])`, replacing the inline `".".join(...[:3])`
   - 3.4. `_assemble_column_mappings` path-agreement (lines ~418, 437): compute `expected_prefix` via `schema_id(...)` and the `source_column_id` prefix via `schema_prefix(source_column_id)`
   - 3.5. Keep the existing `assert ident.database_name is not None` / `schema_name` narrowing and all error messages/behavior unchanged — only the string construction moves into helpers; the assembled IDs must be identical

4. [completed] Run tests for corpus_assembly - `code/load_metadata_db/unit_tests/test_corpus_assembly.py`
   - 4.1. No test-logic changes expected — the refactor is behavior-preserving; the existing positive/negative cases (ID composition, C2 anchor, path-agreement, unknown-key, `is_primary_key`) are the guard
   - 4.2. Run with coverage; a green run with the assertions unchanged confirms byte-identical IDs. Add a case only if coverage of a new helper branch requires it (helper-branch coverage should come from Task 2)

### Phase C — Verify & review

5. [completed] Regression verification - `code/load_metadata_db/unit_tests`
   - 5.1. Run the full loader unit suite at the 100%-coverage bar: `uv run pytest code/load_metadata_db/unit_tests -v --cov --cov-report=term-missing`
   - 5.2. Behavior-preserving sanity check against the live DB (no rebuild): `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml --dry-run` (CI role) — expect an empty diff (the corpus already loaded in the prior activity's Task 16 assembles to identical IDs, so nothing changes)
   - 5.3. Integration tests are optional here (no schema/behavior change); run them only if the dry-run surfaces an unexpected diff

6. [completed] Code review of changed files and address findings - `docs/code_review/`
   - 6.1. Run `code-review-agent` against the two changed code files (`data_model.py`, `corpus_assembly.py`), writing `cr_*.md` per the existing layout
   - 6.2. Address findings via the `code-implementation` skill; re-run the suite at the 100%-coverage bar
   - 6.3. In `cr_20260717v01_corpus_assembly.md`, update finding 1.1's Resolution from "Not implemented (declined)" to "Implemented (this activity)" and cross-reference this activity file

## Key Data Decisions and Considerations

1. **Behavior-preserving, so tests are the proof** — the refactor must not change a single assembled ID. The existing `test_corpus_assembly.py` and `test_integration.py` cases assert exact IDs, C2 anchoring, and path-agreement, so a green run with unchanged test logic is the correctness guarantee. This is why there is no `0001` edit, DB rebuild, or corpus reload — only a `--dry-run` empty-diff sanity check.

2. **Helpers take string segments and live in `data_model.py` (not `PathIdentity`, not a new module)** — placing them beside `pk()` matches the finding ("belongs beside `data_model.pk`") and keeps identity concerns in one module. Taking plain `system`/`database`/`schema`/… strings (rather than a `yaml_discovery.PathIdentity`) keeps `data_model.py` free of any project import — it is currently pure stdlib + dataclasses, and a `PathIdentity` parameter would couple it to `yaml_discovery`. The small verbosity at call sites (`schema_id(ident.system, ident.database_name, ident.schema_name)`) is acceptable and preserves the existing `assert ... is not None` narrowing. Rejected alternatives: (a) `PathIdentity`-typed builders — cleaner call sites but couples `data_model`→`yaml_discovery`; (b) a new `identifiers.py` module — over-engineering for ~5 helpers.

3. **`column_id` signature — pick one and use it consistently** — either `column_id(system, database, schema, table, column)` (parallel to the other builders) or `column_id(table_id_str, column)` (chains onto `table_id`). The chaining form mirrors the current code (`column_id = f"{table_id}.{column_name}"`); the flat form is more uniform. Either is fine; decide at implementation and keep it consistent. Task 2.2's round-trip test pins whichever is chosen.

4. **Closes a twice-deferred finding** — `cr_20260717v01_corpus_assembly.md` finding 1.1 (and its v02 predecessor) flagged this duplication and deferred it both times as out of scope for the feature change. It was scoped out of the validation-rules activity (`20260716v01`) deliberately — that change had just landed and been reviewed — and carved into this dedicated refactor so it stops getting silently deferred. Low risk, real maintainability payoff (the ID format stops being defined in ~10 places).

5. **Implementation notes (2026-07-17)** — Phases A/B/C landed behavior-preserving. Chose the chaining `column_id(table_id, column)` signature (Decision 3) as it mirrors the existing code; call sites use non-colliding local names (`ds_id`, `sch_id`, `tbl_id`, `col_id`) so the imported helper names are never shadowed inside loops. Full loader unit suite: 257 passed / 4 skipped, all loader source files at 100% coverage, no test-logic changes. Task 5.2 `--dry-run` logged `Diff: 0 insert(s), 0 update(s), 0 delete(s)` — byte-identical IDs confirmed; Task 5.3 integration run skipped (empty diff). Task 6 complete: code review (v02) of both changed files found 0 critical/major/minor; the only items were previously-declined suggestions (`RowType` alias; docstring-`Raises:` consistency; I/O-boundary log; `assert` narrowing), each re-declined with rationale in `cr_20260717v02_{data_model,corpus_assembly}.md`. Finding 1.1 in `cr_20260717v01_corpus_assembly.md` flipped to "Implemented".

6. **The only real risk is a subtle transcription error** — e.g. a helper that joins segments in the wrong order or drops a segment would change IDs and break FK resolution. Task 2 (direct helper tests incl. the `schema_prefix ∘ table_id` round-trip) plus Task 4 (unchanged assembler assertions) plus Task 5.2 (empty-diff dry-run) are three independent checks that the IDs are unchanged.
