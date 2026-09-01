---
name: cr_20260729v01_load_ref_data
goal: Review code/load_ref_data/load_ref_data.py against python-development and sql-development skills (first review of this file).
created: 2026-07-29 14:36:10
updated: 2026-07-29 14:36:10
---

## Implementation Plan

1. [completed] SQL style — embedded queries deviate from the lowercase convention - `code/load_ref_data/load_ref_data.py`
   - 1.1. [minor] Lines 265-266, 288-291, 315-323, 591, 595-597, 614-616, 652-655: every embedded SQL statement is uppercase (`SELECT`/`FROM`/`WHERE`/`AND`/`ORDER BY`/`INSERT INTO`/`VALUES`/`TRUNCATE TABLE`/`LIMIT`). sql-development best-practices guideline 7 requires lowercasing everything, and the sibling loader `code/apply_ddl/apply_ddl.py` already follows this (`select 1 from pg_database ...`, `insert into ddl_versions ...` at its lines 225/496). The casing split makes the two loaders read inconsistently.
        - Current: `"SELECT table_name FROM information_schema.tables "` / `"WHERE table_schema = %s AND table_type = 'BASE TABLE'"`
        - Expected: `"select table_name from information_schema.tables "` / `"where table_schema = %s and table_type = 'BASE TABLE'"` (apply the same lowercasing to all embedded statements)
        - Resolution: Implemented as specified — lowercased every keyword (`select`/`from`/`where`/`and`/`order by`/`insert into`/`values`/`truncate table`/`limit`) across all seven embedded statements (fetch_ref_tables, fetch_table_columns, fetch_pk_columns, and the truncate/insert/audit-insert/freshness queries in load_tables and check_freshness). String literal values `'BASE TABLE'` and `'PRIMARY KEY'` left as-is.
   - 1.2. [minor] Line 317: the PK-column query uses a bare `JOIN`. sql-development best-practices guideline 2 requires the explicit `inner join` keyword.
        - Current: `"JOIN information_schema.key_column_usage kcu "`
        - Expected: `"inner join information_schema.key_column_usage kcu "` (lowercased per 1.1)
        - Resolution: Implemented as specified — replaced the bare `JOIN` with `inner join` (lowercased per 1.1).

2. [completed] Exception handling in `read_csv` - `code/load_ref_data/load_ref_data.py`
   - 2.1. [minor] Lines 237-238: the empty-file `ValueError` is raised inside the `except StopIteration` handler without `from None`, so Python appends the raw `StopIteration` as a "During handling of the above exception" chained traceback — noise that obscures the intended message. exception-handling guideline 3/6 covers deliberate chain control; `from None` is the right choice when the caught exception carries no useful context.
        - Current: `except StopIteration:\n            raise ValueError(f"Ref CSV {path} is empty (no header row)")`
        - Expected: `except StopIteration:\n            raise ValueError(f"Ref CSV {path} is empty (no header row)") from None`
        - Resolution: Implemented as specified — appended `from None` to suppress the chained `StopIteration` traceback (wrapped across lines for the 100-char limit).
   - 2.2. [minor] Lines 232-241: only `OSError` is caught, but `csv.reader` iteration (`next(reader)` and the row comprehension) can raise `csv.Error` on malformed content (e.g. a NUL byte or a field exceeding the field-size limit). A `csv.Error` escapes `read_csv`, is not one of the types `main` handles (`KeyError`, `FileNotFoundError`/`ValueError`/`RuntimeError`, `psycopg2.Error`), and would surface as an unhandled traceback — contrary to the docstring's "or unreadable" `ValueError` contract (line 230).
        - Current: `except OSError as e:\n        raise ValueError(f"Failed to read ref CSV {path}: {e}") from e`
        - Expected: `except (OSError, csv.Error) as e:\n        raise ValueError(f"Failed to read ref CSV {path}: {e}") from e`
        - Resolution: Implemented as specified — broadened the catch to `(OSError, csv.Error)` so malformed-CSV errors surface as the documented `ValueError`.

3. [completed] Logging — silent `finally` connection close - `code/load_ref_data/load_ref_data.py`
   - 3.1. [suggestion] Lines 744-745: `run`'s `try/finally` closes the connection with `conn.close()` and no log. exception-handling guideline 5 lists `finally` among the stages that "should all include appropriate logging".
        - Current: `finally:\n        conn.close()`
        - Expected: `finally:\n        conn.close()\n        logger.debug(f"Closed connection to {config['database']}")`
        - Resolution: Deferred — optional. Cleanup-only `finally`; every terminal outcome (check current, validation passed, dry-run stop, committed reload) already logs before the block runs, so a close-time DEBUG line is noise rather than context. Matches the deferred decision on the identical pattern in cr_20260727v01_apply_ddl item 1.2.

4. [completed] Docstring — `load_tables` `Raises` narrower than the catch - `code/load_ref_data/load_ref_data.py`
   - 4.1. [suggestion] Lines 584-585 vs. 622-625: the docstring documents `Raises: psycopg2.Error`, but the handler is `except Exception as e:` followed by a bare `raise`, so any exception type raised inside the transaction propagates (after rollback). The broad catch is the correct idiom for guaranteeing rollback, but the `Raises` section understates what can escape.
        - Current: `Raises:\n        psycopg2.Error: On any database failure (after rollback).`
        - Expected: note that any exception raised during the load triggers rollback and re-raises unchanged (e.g. `Raises: Exception: Any error raised during the load is re-raised unchanged after rollback (in practice psycopg2.Error).`)
        - Resolution: Deferred — optional. The broad `except Exception` exists to guarantee rollback on any failure and re-raises the original type unchanged; within the block only `cur.execute` can realistically fail (`csv_hashes` keys are built from the same stems as `loadable`, so the `csv_hashes[table]` lookup cannot `KeyError`), so `psycopg2.Error` is the only type that propagates in practice and the doc is accurate for real inputs.

## Skills with No Issues

1. Type Hints: No issues found. Every function carries modern, specific annotations — `connection_kwargs(database: str, schema: str) -> dict[str, str]`, `compute_csv_sha256(path: Path) -> str`, `list_csv_files(csv_dir: Path) -> list[Path]`, `read_csv(path: Path) -> tuple[list[str], list[list[str]]]`, `fetch_ref_tables(conn: psycopg2.extensions.connection, schema: str) -> set[str]`, `fetch_table_columns(...) -> list[tuple[str, str, bool]]`, `fetch_pk_columns(...) -> tuple[str, ...]`, `documented_columns(docs_dir: Path) -> dict[str, list[str]]`, `validate_csv(...) -> list[str]`, `validate_all(...) -> tuple[dict[str, tuple[list[str], list[list[str]]]], list[str]]`, `load_tables(...) -> None`, `check_freshness(...) -> list[str]`, `run(config: dict[str, Any], check: bool, dry_run: bool) -> None`, `main() -> None`. `dict[str, Any]` for the parsed TOML config and the `_TYPE_PARSERS: dict[str, Any]` registry (heterogeneous parser callables) are appropriate uses of `Any`.
2. Docstrings: No issues found beyond the deferred item 4 above. Module docstring plus Google-style docstrings on every function; `Args`/`Returns`/`Raises` otherwise match the implementation, and they explain the "why" (git/MR review replaces `update_reason`/`_hstry`, empty-cell = NULL convention, line-ending-stable hash, the docs-vs-reality drift gate, no-concurrency-lock rationale). `run`'s `Raises` (lines 691-697) fully covers `KeyError`, `FileNotFoundError`, `ValueError`, `RuntimeError`, and `psycopg2.Error`.
3. Comments: No issues found. Comments consistently explain "why" — the never-hardcoded `DEFAULT_MAX_ROWS_PER_TABLE` note (lines 79-82), the case-variant extension guard (lines 84-87), the infra-table exclusion (lines 89-91), the schema-name regex mirroring apply_ddl/db_io (lines 93-96), the text-insert/Postgres-cast parser rationale (lines 98-101), stopping per-value checks after a header mismatch (lines 434-436), and the `enumerate(..., start=2)` "header is line 1" note (line 466).
4. Logging: No issues found beyond the deferred item 3 above. Uses `logconfig`, no `print()`, f-strings throughout, appropriate DEBUG/INFO/ERROR levels, no "Entering/Exiting" noise, and opening/closing `"=" * 60` run-boundary separators owned solely by `main` (including the mutually-exclusive-flags, missing-config, and TOML-decode early-exit arms).
5. Exception Handling: No issues found beyond items 2 and 4 above. No bare `except`; `documented_columns` wraps `OSError`/`yaml.YAMLError` in `ValueError ... from e`; `load_tables` catches broadly to guarantee rollback then bare-`raise`s to preserve the type; `main` dispatches on `KeyError`, `(FileNotFoundError, ValueError, RuntimeError)`, and `psycopg2.Error` with contextful `Error:`-prefixed messages; domain failures raise `ValueError`/`RuntimeError`, never a generic `Exception`.
6. Executable Scripts: No issues found. `main()` with `if __name__ == "__main__"` guard (`# pragma: no cover`), required `--config`, logging deferred until after `parse_args`, and config-existence + TOML-decode failures handled before dispatch. The `--check`/`--dry-run` mode flags are the same defensible CI/maintainer deviation from the single-`--config` rule accepted for the sibling `apply_ddl.py`, and they are validated as mutually exclusive (lines 784-787).
7. Data Validation: N/A — this is a ref-data loader/validator, not a `data_val_` output-validation script, so the `data_val_` naming / `data_validation/` directory convention does not apply.
8. Unit Tests: N/A for this source file's content. Tests exist at `code/load_ref_data/unit_tests/test_load_ref_data.py` (with `conftest.py`) and are reviewed under their own review file; they were not run as part of this review-only pass.
9. SQL (best-practices): Issues found — see items 1.1 and 1.2. Otherwise clean: explicit column lists (no `SELECT *`), parameterized `%s` placeholders (lines 266, 289, 315, 616, 653), and `sql.Identifier`/`sql.Placeholder` composition for the dynamic table and column names (lines 591-605) preventing injection in statement text. The per-block `Level =` annotation convention targets standalone `.sql` files, not these single-statement embedded queries.
