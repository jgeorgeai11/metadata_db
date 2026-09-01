---
name: cr_20260730v01_load_ref_data
goal: Re-review code/load_ref_data/load_ref_data.py against python-development and sql-development skills after the docs-shape/escape-hatch expansion (follow-up to cr_20260729v01, whose findings are all applied).
created: 2026-07-30 14:22:13
updated: 2026-07-30 14:37:28
---

## Implementation Plan

1. [completed] SQL correctness — underconstrained PK-column join - `code/load_ref_data/load_ref_data.py`
   - 1.1. [minor] Lines 470-482: `fetch_pk_columns` joins `information_schema.table_constraints` to `key_column_usage` on `constraint_name` and `table_schema` only. In Postgres, table-constraint names are unique per table, not per schema, so two ref tables carrying an identically named PK constraint would cross-match and the query would return the other table's key columns as well — wrong `pk_columns`, which silently mis-scopes duplicate-PK detection in `validate_csv`. Default `<table>_pkey` names never collide, but hand-named constraints can; the standard information_schema idiom also equates `table_name` in the join (sql-development best-practices guideline 2: joins must be explicit and correct).
        - Current: `"  on tc.constraint_name = kcu.constraint_name "` / `" and tc.table_schema = kcu.table_schema "`
        - Expected: `"  on tc.constraint_name = kcu.constraint_name "` / `" and tc.table_schema = kcu.table_schema "` / `" and tc.table_name = kcu.table_name "`
        - Resolution: Implemented as specified — added `" and tc.table_name = kcu.table_name "` to the `fetch_pk_columns` join condition (now line 477), completing the standard information_schema idiom so identically named PK constraints on different tables can no longer cross-match. All 109 unit tests in `code/load_ref_data/unit_tests/test_load_ref_data.py` pass.

2. [completed] Validation edge cases (docs-shape and parser paths) - `code/load_ref_data/load_ref_data.py`
   - 2.1. [suggestion] Lines 148-152: the comment claims the parsers "give the pre-write validation parity with what the INSERT would accept", but Python's `int`/`float` accept forms Postgres string casts reject (e.g. `int("1_0")` parses via underscore separators; `'1_0'::integer` fails), so such a value passes the pre-merge `--dry-run` yet fails the post-merge load cast (comments guideline 3: keep comments accurate).
        - Current: `# pre-write validation parity with what the INSERT would accept.`
        - Expected: soften to "approximate parity" (or tighten the int/float parsers to reject underscore/inf forms)
        - Resolution: Deferred — a curated code set containing `1_0`-style literals is a vanishingly narrow authoring edge case, and the failure mode is still loud: the real load fails inside the single transaction and rolls back completely, so no silent corruption is possible.
   - 2.2. [suggestion] Lines 679-687: a CSV with a duplicated header name on the docs-shape path fails `_docs_gate_issues`' sorted-list comparison, but both set differences come out empty, producing the unhelpful message `undocumented: [], documented but absent: []` (the exact failure the duplicate-docs guard at lines 629-643 exists to avoid on the docs side).
        - Current: `if sorted(doc_cols) != sorted(header):` (set-difference message only)
        - Expected: also detect `len(header) != len(set(header))` and name the duplicated header cell(s) in the message
        - Resolution: Deferred — on the live path (every real load and plain dry-run) a duplicated header already fails the ordered header-vs-live-columns check with a clear message listing both sides; the cryptic message only arises for a duplicated header combined with an escape-hatch dry-run, and the gate still fails loudly there.
   - 2.3. [suggestion] Lines 574-584: `documented_columns` takes each table's schema from the shard path's first part without checking it is a documented schema folder (one containing `schema.yaml`), while `list_csv_files` rejects any `data_ref/` folder that is not one — a columns shard misfiled under a non-schema folder would yield drift guidance ("add data_ref/<folder>/<table>.csv") pointing at a folder discovery would then reject.
        - Current: `schema = relative_parts[0]`
        - Expected: validate `schema` against `documented_schemas(docs_dir)` and raise a `ValueError` naming the misfiled shard
        - Resolution: Deferred — requires a doubly-misfiled docs shard (under a folder with no `schema.yaml`, yet still shaped like table docs); the outcome is a loud, if contradictory, validation error rather than silent drift, and the docs tree is itself MR-reviewed.

3. [completed] Type hints — parser-registry annotation - `code/load_ref_data/load_ref_data.py`
   - 3.1. [suggestion] Line 152: `_TYPE_PARSERS: dict[str, Any]` could be the more specific `dict[str, Callable[[str], object]]` — every value is a one-string-argument callable (type-hints guideline 3: be specific).
        - Current: `_TYPE_PARSERS: dict[str, Any] = {`
        - Expected: `_TYPE_PARSERS: dict[str, Callable[[str], object]] = {` (with `from collections.abc import Callable`)
        - Resolution: Deferred — cr_20260729v01 already accepted `Any` here as an appropriate use for the heterogeneous parser callables; the values are only consumed via `.get()` and a try/except call, so the narrower type adds an import without changing any checking outcome.

4. [completed] Logging and docstring nuances (two carryovers, one new) - `code/load_ref_data/load_ref_data.py`
   - 4.1. [suggestion] Lines 1370-1371: `run`'s `try/finally` closes the connection with no log (exception-handling guideline 5 lists `finally` among the stages that should log).
        - Current: `finally:\n        conn.close()`
        - Expected: `finally:\n        conn.close()\n        logger.debug(f"Closed connection to {config['database']}")`
        - Resolution: Deferred — same decision as cr_20260729v01 item 3.1: cleanup-only `finally`; every terminal outcome (check current, validation passed, dry-run stop, committed reload) already logs before the block runs, so a close-time DEBUG line is noise rather than context.
   - 4.2. [suggestion] Lines 1140-1141 vs. 1178-1189: `load_tables` documents `Raises: psycopg2.Error`, but the rollback handler is `except Exception` plus a bare `raise`, so any exception type raised inside the transaction propagates.
        - Current: `Raises:\n        psycopg2.Error: On any database failure (after rollback).`
        - Expected: note that any exception raised during the load is re-raised unchanged after rollback (in practice `psycopg2.Error`)
        - Resolution: Deferred — same decision as cr_20260729v01 item 4.1: the broad catch exists to guarantee rollback and re-raises the original type unchanged; inside the block only `cur.execute` can realistically fail, so `psycopg2.Error` is the only type that propagates in practice and the doc is accurate for real inputs.
   - 4.3. [suggestion] Lines 1360-1362: `Validation passed for {len(loadable)} ref table(s)` undercounts on an escape-hatch dry-run — docs-shape-validated tables (`--allow-missing-table`/`--allow-reshaped-table`) are deliberately never in `loadable`, so a run that validated four CSVs can report three (logging guideline 3: include context).
        - Current: `logger.info(f"Validation passed for {len(loadable)} ref table(s)")`
        - Expected: log both counts, e.g. `f"Validation passed for {len(csv_files)} ref CSV(s) ({len(loadable)} loadable)"`
        - Resolution: Deferred — each docs-shape-validated table already emits its own WARNING line naming it immediately above (lines 1066-1071, 1082-1087), so the run log as a whole is unambiguous about what was validated; the count nuance only appears on dry-run CI logs.

## Skills with No Issues

1. Type Hints: Issues limited to the deferred suggestion 3.1 — every function is fully annotated with modern, specific syntax, including the reshaped signatures (`read_csv(path: Path) -> tuple[list[str], list[list[str]], str]`, `compute_csv_sha256(data: bytes) -> str`, `documented_columns(docs_dir: Path) -> dict[str, TableDocs]`, `validate_all(...) -> tuple[dict[str, tuple[list[str], list[list[str]], str]], list[str]]`, `set[str] | None` for the three escape-hatch parameters) and the `DocColumn`/`TableDocs` NamedTuples.
2. Docstrings: Issues limited to the deferred suggestion 4.2 — module docstring plus Google-style docstrings on every function and both NamedTuples; `Args`/`Returns`/`Raises` match the implementations (including `read_csv`'s three-tuple return with the hash, `check_freshness`'s ValueError contract, and `run`'s five-type `Raises` list), and the "why" is documented throughout (single-read TOCTOU guarantee, escape-hatch semantics, no-concurrency-lock rationale).
3. Comments: Issues limited to the deferred suggestion 2.1 — comments consistently explain "why" (never-hardcoded guardrail default at lines 128-131, the `newline=""`/`utf-8-sig` note at 385-386, the shard-dedup rationale at 560-561, the docs-gate "useless empty-sets message" motivation at 634-636, the header-is-line-1 notes, the dry-run-only-flags rationale at 1454-1456).
4. Logging: Issues limited to the deferred suggestions 4.1/4.3 — `logconfig` with no `print()`, f-strings throughout, appropriate DEBUG/INFO/WARNING/ERROR levels, escape-hatch downgrades logged as WARNING, and the `"=" * 60` run separators owned solely by `main` on every exit arm.
5. Exception Handling: Issues limited to the deferred suggestions 4.1/4.2 — no bare `except`; `from e`/`from None` chain control is correct (`read_csv` lines 382/393/396, `documented_columns` 588-591, `check_freshness` 1238-1240); `load_tables` catches broadly to guarantee rollback, logs a rollback-failure without masking the root cause, and bare-`raise`s; `main` dispatches specific types with contextful messages.
6. Executable Scripts: No issues found — `main()` with `if __name__ == "__main__"` guard (`# pragma: no cover`), required `--config` with the TOML in `code/load_ref_data/config/`, logging deferred until after `parse_args`, config-existence and TOML-decode failures handled before dispatch, `--check`/`--dry-run` mutual exclusion enforced, and the dry-run-only escape flags rejected outside `--dry-run`. The mode flags remain the same defensible CI/maintainer deviation from the single-`--config` rule accepted in cr_20260729v01.
7. Data Validation: N/A — this is a ref-data loader/validator, not a `data_val_` output-validation script, so the `data_val_` naming / `data_validation/` directory convention does not apply.
8. Unit Tests: N/A for this source file's content — tests live at `code/load_ref_data/unit_tests/` and are reviewed under their own review file.
9. SQL (best-practices): Issues limited to 1.1 — all seven embedded statements are now lowercase with `inner join` (the cr_20260729v01 items 1.1/1.2 are applied), columns are explicit, values are parameterized via `%s`, and dynamic identifiers are composed with `sql.Identifier`/`sql.Placeholder` (lines 1145-1160). The `Level =` block-annotation convention targets standalone `.sql` files, not these single-statement embedded queries.
