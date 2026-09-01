---
name: 20260528v01_build_loader
goal: Build the metadata_db loader at `code/load_metadata_db/` — the Python entry point that reads the YAML catalog under `data/systems/`, validates the assembled corpus per the loader contract in `readme/metadata-db-maintenance.md`, and applies the diff against Postgres in a single transaction with `_hstry` writes. Supports `--dry-run` for pre-merge CI validation and `--reset-hstry` for the bootstrap phase.
created: 2026-05-28 09:00:00
updated: 2026-05-28 09:00:00
---

## Implementation Plan

1. Add Python dependencies for YAML and SQL parsing - `pyproject.toml`
   - 1.1. Run `uv add pyyaml sqlglot`
   - 1.2. Commit the updated `pyproject.toml` and `uv.lock`

2. Create path decoding and file discovery module - `code/load_metadata_db/paths.py`
   - 2.1. Define `PathIdentity` frozen dataclass with fields: file_type (Literal of the 7 file types), system, database_name, schema_name, target_system, path
   - 2.2. Function `validate_identifier_segment(value, kind)`: raise ValueError on `.` or whitespace in the segment
   - 2.3. Function `decode_path(path, data_root)`: classify a YAML file by its location under `data/systems/` and return `PathIdentity`; validate identifier syntax on every segment during composition
   - 2.4. Function `discover_yaml_files(data_root)`: walk `data/systems/` recursively; classify each `.yaml`/`.yml`; raise on any unrecognized `.yaml`; silently ignore non-YAML files (`.gitkeep`, `README.md`)
   - 2.5. Recognize the 7 file types: `system.yaml`, `data_source.yaml`, `schema.yaml`, `tables.yaml`, `columns.yaml`, `table_relationships.yaml`, `mappings/{target_system}.yaml`

3. Create and run tests for paths module - `code/load_metadata_db/unit_tests/test_paths.py`
   - 3.1. Build tiny on-disk `data/systems/` trees in `tmp_path` covering all 7 file types
   - 3.2. Assert `decode_path` returns the correct `PathIdentity` for each
   - 3.3. Parametrize identifier-syntax negative cases (dot, whitespace) per segment kind
   - 3.4. Assert `discover_yaml_files` raises on an unrecognized `.yaml` and silently ignores `.gitkeep` / `README.md`
   - 3.5. Run with `uv run pytest code/load_metadata_db/unit_tests/test_paths.py -v`

4. Create row dataclasses and table registry - `code/load_metadata_db/schema.py`
   - 4.1. Define a frozen dataclass per main table mirroring `readme/metadata-db-overview.md`: `SystemRow`, `DataSourceRow`, `SchemaRow`, `TableRow`, `ColumnRow`, `TableRelationshipRow`, `ColumnMappingRow`
   - 4.2. Define `Corpus` and `DbState` dataclasses (each is 7 dicts keyed by PK; composite PKs are tuples)
   - 4.3. Module constants: `TABLE_ORDER` (FK order list of 7 table names) and `CONTENT_COLUMNS` (dict mapping table → frozenset of columns used for diff, excluding `insert_ts`/`update_ts`)
   - 4.4. `ColumnRef` frozen dataclass with `system/database/schema/table/column` plus `.table_id` / `.column_id` properties

5. Create and run tests for schema module - `code/load_metadata_db/unit_tests/test_schema.py`
   - 5.1. Assert `TABLE_ORDER` lists the 7 tables in FK-respecting order (parents before children)
   - 5.2. Assert `CONTENT_COLUMNS` for each table excludes `insert_ts` and `update_ts`
   - 5.3. Assert `ColumnRef.table_id` / `.column_id` produce the expected dotted strings
   - 5.4. Run tests

6. Create YAML I/O and corpus assembly - `code/load_metadata_db/yaml_io.py`
   - 6.1. Function `load_yaml(path)`: pyyaml `safe_load`, raise on parse failure
   - 6.2. Per-file-type assembler functions: produce row dataclass instances from the YAML document body plus the `PathIdentity`
   - 6.3. Function `assemble_corpus(files)`: dispatch to assemblers and accumulate into a `Corpus`
   - 6.4. Loader-managed fields (`insert_ts`, `update_ts`, `target_tables_referenced`) are NOT populated here — those happen in `db.py` and `expressions.py` later

7. Create and run tests for yaml_io module - `code/load_metadata_db/unit_tests/test_yaml_io.py`
   - 7.1. Use the 7 example YAMLs at `readme/metadata-db-example-yamls/` as regression fixtures
   - 7.2. For each file type, assert `assemble_corpus` produces the expected row count and that path-derived identity components match the source folder
   - 7.3. Cover special cases: `target_expression: null` (column_mappings), the `public` sentinel for schemaless data sources
   - 7.4. Run tests

8. Create SQL expression parser and helpers - `code/load_metadata_db/expressions.py`
   - 8.1. Function `parse_expression(sql_text, dialect="postgres")`: returns `sqlglot.Expression`; raise `ValueError` on parse failure with the offending text in the message
   - 8.2. Function `extract_column_refs(expr)`: walk the parse tree, return `list[ColumnRef]` for every fully-qualified column reference (4-part identifier required); raise `ValueError` on partially-qualified references
   - 8.3. Function `compute_target_tables_referenced(expr, target_system)`: return sorted, de-duped list of `table_id` strings whose system equals `target_system`; return `[]` for a `None` expression

9. Create and run tests for expressions module - `code/load_metadata_db/unit_tests/test_expressions.py`
   - 9.1. Parametrize: single column ref; the `COALESCE` example from `readme/metadata-db-example-yamls/mappings/edw.yaml`; a `join_condition` with two refs across two tables; an unparsable string; a 3-segment ref that must error
   - 9.2. Assert `compute_target_tables_referenced` returns sorted, de-duped `table_id`s and `[]` for `None`
   - 9.3. Run tests

10. Create corpus validation rules - `code/load_metadata_db/validation.py`
    - 10.1. Define `ValidationError(Exception)` carrying an aggregated `list[str]` of issue messages (never fail-fast)
    - 10.2. Function `validate_corpus(corpus)`: accumulate issues across all sub-checks and raise once
        - 10.2.1. Uniqueness: no duplicate PKs within each table
        - 10.2.2. Reference existence: every FK resolves to a row defined in the corpus
        - 10.2.3. Identifier syntax: name components in row-body fields (`table_name`, `column_name`, `relationship_name`) contain no `.` or whitespace
        - 10.2.4. Within-row consistency:
            - 10.2.4.1. `table_relationships`: `table_a_id` and `table_b_id` both decode to the row's `system`
            - 10.2.4.2. `column_mappings`: `source_column_id`'s system prefix equals `source_system`; every entry in `target_tables_referenced` has `target_system` as its system prefix; `source_system` != `target_system`
            - 10.2.4.3. `column_mappings`: a row with `target_expression: null` must have non-null `notes`
        - 10.2.5. SQL expression parsability: parse every non-null `target_expression` and every `join_condition`; assert every extracted `ColumnRef` resolves to a known `columns` row
    - 10.3. Function `validate_update_reason(corpus, diff)`: for each insert in the diff require `update_reason is None` on the corpus row; for each update require non-null; deletes have no constraint
    - 10.4. Memoize parsed expression trees so `compute_target_tables_referenced` (step 13.5 of orchestration) can reuse them without re-parsing

11. Create and run tests for validation module - `code/load_metadata_db/unit_tests/test_validation.py`
    - 11.1. One happy-path `Corpus` fixture
    - 11.2. One negative test per rule asserting the exact `ValidationError` issue substring with `pytest.raises(match=...)`
    - 11.3. Aggregation test: a corpus with three independent violations surfaces three issues in a single raise
    - 11.4. Run tests

12. Create diff computation - `code/load_metadata_db/diff.py`
    - 12.1. Dataclasses `RowChange(table, key, old, new)` and `Diff(inserts, updates, deletes)`; `Diff.is_empty()` returns True iff all three lists are empty
    - 12.2. Function `compute_diff(corpus, db_state)`: per-PK classify into inserts (in corpus only), deletes (in DB only), and updates (in both with `CONTENT_COLUMNS` differing); content-equal rows are no-ops
    - 12.3. PK changes are surfaced as one delete + one insert, never as an update (PKs are path-derived; in-place identity change would falsely preserve `insert_ts` and skip the `_hstry` write)

13. Create and run tests for diff module - `code/load_metadata_db/unit_tests/test_diff.py`
    - 13.1. In-memory `Corpus` + `DbState` fixtures
    - 13.2. Assert all three classifications produced correctly
    - 13.3. Assert content equality ignores `insert_ts` / `update_ts` (idempotency check)
    - 13.4. Assert a renamed table (PK change) becomes one delete + one insert, not an update
    - 13.5. Run tests

14. Create DB read/write helpers - `code/load_metadata_db/db.py`
    - 14.1. Function `connection_kwargs(database)`: lift the pattern from `code/apply_ddl/apply_ddl.py` (load `.env`, read `POSTGRES_*`, return psycopg2 kwargs)
    - 14.2. Function `read_db_state(conn)`: one SELECT per main table; build `DbState`
    - 14.3. Function `apply_diff(conn, diff, reset_hstry)`: a single transaction
        - 14.3.1. If `reset_hstry`: `TRUNCATE` all 7 `*_hstry` tables (still inside the transaction)
        - 14.3.2. Deletes in **reverse FK order** (column_mappings, table_relationships, columns, tables, schemas, data_sources, systems): INSERT old row → `*_hstry` with `end_ts=now()`, then DELETE from main
        - 14.3.3. Updates in FK order: INSERT old row → `*_hstry` with `end_ts=now()` carrying the OLD `update_reason`, then UPDATE main with new content and bumped `update_ts`
        - 14.3.4. Inserts in FK order: INSERT into main with `update_reason = NULL`, `insert_ts = update_ts = now()`
    - 14.4. UPDATE / DELETE statements for composite-key tables (`table_relationships`, `column_mappings`) must include all three PK components in the WHERE clause

15. Create and run tests for db module - `code/load_metadata_db/unit_tests/test_db.py`
    - 15.1. Mirror `code/apply_ddl/unit_tests/conftest.py` `fake_conn` / `fake_cursor` fixtures
    - 15.2. Assert `read_db_state` issues one SELECT per main table and assembles `DbState`
    - 15.3. Assert `apply_diff` issues the expected SQL sequence per change kind; `conn.commit` on success; `conn.rollback` + re-raise on injected exception
    - 15.4. Assert `reset_hstry=True` issues 7 TRUNCATEs inside the same transaction (no autocommit toggle)
    - 15.5. Assert composite-key UPDATE / DELETE statements include all 3 PK components in WHERE
    - 15.6. Run tests

16. Create entry point - `code/load_metadata_db/load_metadata_db.py`
    - 16.1. Argparse: required `--config`, plus boolean flags `--dry-run` and `--reset-hstry`
    - 16.2. `setup_logging(log_dir="logs/load_metadata_db")` after argparse so `--help` doesn't create log files
    - 16.3. Function `run(config, dry_run, reset_hstry)` orchestrating: discover → assemble → validate (part A, then part B SQL parsability) → derive `target_tables_referenced` → read DB → compute diff → validate `update_reason` → if dry-run log Diff summary and return → else open transaction → `apply_diff(conn, diff, reset_hstry)` → commit
    - 16.4. Gate `--reset-hstry` behind env var `METADATA_DB_ALLOW_RESET_HSTRY=1`; refuse with a clear error otherwise
    - 16.5. Exception handling per the apply_ddl pattern: specific `except` arms per error type with `sys.exit(1)` and a distinct log message per arm

17. Create TOML config - `code/load_metadata_db/config/load_metadata_db.toml`
    - 17.1. Fields: `data_root` (default `"data"`) and `database` (default `"metadata_db"`)
    - 17.2. Header comment with the usage line: `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml [--dry-run] [--reset-hstry]`

18. Create and run orchestration tests - `code/load_metadata_db/unit_tests/test_load_metadata_db.py`
    - 18.1. Patch `psycopg2.connect` to a `fake_conn`; point `--config` at a `tmp_path` TOML; point `data_root` at a `tmp_path` corpus tree
    - 18.2. Assert exit codes: 0 on success, 1 on validation/DB failure
    - 18.3. Assert `apply_diff` is called for the normal run path and NOT for `--dry-run`
    - 18.4. Assert `ValidationError` surfaces *all* aggregated issues in the log (use `caplog`)
    - 18.5. Assert `--reset-hstry` without the env guard exits with a clear error
    - 18.6. Run tests

19. Create integration test (gated) - `code/load_metadata_db/unit_tests/test_integration.py`
    - 19.1. Apply `pytest.mark.integration`; skip when `METADATA_DB_INTEGRATION` env var != `"1"`
    - 19.2. Bootstrap via `apply_ddl --create-db` against a throwaway DB or assume the local `metadata_db`
    - 19.3. Run loader against a small fixture corpus tree; assert rows landed (`SELECT count(*)` per table)
    - 19.4. Re-run; assert `Diff.is_empty()` is True (idempotency)
    - 19.5. Modify a row's `description` and add a non-null `update_reason`; re-run; assert main row updated AND old row exists in `*_hstry` with the OLD `update_reason` and `end_ts` populated
    - 19.6. Delete the row from the corpus; re-run; assert main has no row for that PK and `*_hstry` has the prior version
    - 19.7. Run via `METADATA_DB_INTEGRATION=1 uv run pytest -m integration code/load_metadata_db/unit_tests/test_integration.py -v`

20. End-to-end manual verification against the local Postgres
    - 20.1. Stage a small `data/systems/warehouse/...` tree (system + data_source + schema + tables + columns + one cross-system mapping)
    - 20.2. Dry-run: `uv run code/load_metadata_db/load_metadata_db.py --config code/load_metadata_db/config/load_metadata_db.toml --dry-run` — expect a no-error Diff summary
    - 20.3. Real run; verify rows landed (`SELECT count(*)` per table)
    - 20.4. Modify-and-rerun cycle to exercise `_hstry` write semantics
    - 20.5. Clean up via `METADATA_DB_ALLOW_RESET_HSTRY=1 uv run code/load_metadata_db/load_metadata_db.py --config ... --reset-hstry`

21. Code review and address findings
    - 21.1. Run `code-review-agent` against each module under `code/load_metadata_db/` (mirror the Phase 1 review pattern under `docs/code_review/apply_ddl/`)
    - 21.2. Address findings via `code-implementation-agent`; re-run the suite at 100% coverage
    - 21.3. Mark each review's `Status & Next Steps` resolved when fixes land

## Key Data Decisions and Considerations

1. **YAML parser = pyyaml** — chosen over `ruamel.yaml` because the loader only reads YAML; pyyaml is simpler, faster, and the user approved adding it
2. **SQL parser = sqlglot, dialect = postgres** — validation must resolve every column reference in `target_expression` and `join_condition`; sqlglot handles 4-part dotted identifiers, quoted strings, CASE/COALESCE/CAST reliably; user approved adding it
3. **Identity change handled as delete + insert** — PKs are path-derived; treating a PK change as an in-place update would falsely preserve `insert_ts` and skip the `_hstry` write
4. **Composite-key UPDATEs include all PK parts in WHERE** — easy footgun on `table_relationships` (3-part PK) and `column_mappings` (3-part PK); explicit unit test required
5. **Aggregated error reporting** — `validate_corpus` collects issues across all rules and raises once; authors see every problem per run instead of a fix-one-at-a-time loop
6. **`--reset-hstry` inside the load transaction** — TRUNCATE is transactional in Postgres; doing it inside the same transaction as the DML means a failed load also reverts the truncate (atomic bootstrap reset)
7. **`--reset-hstry` gated by env var `METADATA_DB_ALLOW_RESET_HSTRY=1`** — prevents accidental local invocation from wiping history; mirrors the maintainer-only intent in the design doc
8. **Strict file discovery** — any `.yaml`/`.yml` under `data/systems/` that doesn't match one of the 7 recognized file types is a discovery error; non-YAML files (`.gitkeep`, `README.md`) are silently ignored
9. **No re-read of DB state inside the apply transaction** — merge serialization (Phase 4 design) guarantees only one loader runs at a time; reading state once at step 6 of the orchestrator is safe and simpler
10. **`target_tables_referenced` derivation reuses parse trees from validation** — memoize so each `target_expression` is parsed once per loader run
