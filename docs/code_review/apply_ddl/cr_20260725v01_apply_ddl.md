---
name: cr_20260725v01_apply_ddl
goal: Re-review code/apply_ddl/apply_ddl.py against python-development and sql-development skills following cr_20260717v01 (both prior findings implemented).
created: 2026-07-25 13:53:33
updated: 2026-07-25 13:53:33
---

## Implementation Plan

1. [completed] Silent `finally` cleanup blocks omit close-time logging - `code/apply_ddl/apply_ddl.py`
   - 1.1. [suggestion] Line 156: `create_database_if_absent`'s `try/finally` closes the maintenance connection with `conn.close()` and no log. Exception-handling guideline 5 lists `finally` among the stages that "should all include appropriate logging"; the skill's own example emits a `logger.debug(...)` in `finally`.
        - Current: `finally:\n        conn.close()`
        - Expected: `finally:\n        conn.close()\n        logger.debug(f"Closed maintenance connection to {maint_kwargs['dbname']}")`
        - Resolution: Deferred — optional. The `finally` performs only resource cleanup; the successful/created path is already logged at INFO (lines 150, 155) and failures propagate a logged `psycopg2.Error`, so a close-time DEBUG line adds log noise without new information. Prior review (cr_20260717v01, item 5) assessed this try/finally as clean.
   - 1.2. [suggestion] Line 463: `run`'s `try/finally` closes the primary connection with `conn.close()` and no log, same as 1.1.
        - Current: `finally:\n        conn.close()`
        - Expected: `finally:\n        conn.close()\n        logger.debug(f"Closed connection to {conn_kwargs['dbname']}")`
        - Resolution: Deferred — optional. Cleanup-only `finally`; the connect is already logged at DEBUG (line 400) and every terminal outcome (in-sync, pending, applied N) logs before the block runs, so a close-time DEBUG line is noise rather than context.

## Skills with No Issues

1. Type Hints: No issues found. Every function carries modern, specific annotations — `compute_checksum(path: Path) -> str`, `_sha256_text(text: str) -> str`, `connection_kwargs(database: str, schema: str) -> dict[str, str]`, `create_database_if_absent(conn_kwargs: dict[str, str]) -> None`, `ensure_schema(conn: psycopg2.extensions.connection, schema: str) -> None`, `list_repo_migrations(ddl_dir: Path) -> list[tuple[str, Path]]`, `applied_migrations(...) -> dict[str, str]`, `verify_checksums(repo_by_version: dict[str, Path], applied: dict[str, str]) -> None`, `run(config: dict[str, Any], check_only: bool, create_db: bool) -> None`, `main() -> None`. `dict[str, Any]` for the parsed TOML config is appropriate.
2. Docstrings: No issues found. Module docstring plus Google-style docstrings on every function; `Args`/`Returns`/`Raises` match the implementation, including `connection_kwargs`'s documented `ValueError`/`RuntimeError` and `run`'s complete `Raises` list (`KeyError`, `FileNotFoundError`, `ValueError`, `psycopg2.Error`, `RuntimeError`, `SystemExit`). Docstrings document the "why" (e.g. line-ending-stable checksum rationale, why check mode skips writes).
3. Comments: No issues found. Comments explain "why" — the libpq-options / uppercase-folding schema-validation rationale (lines 45-54), numeric-vs-lexical sort (lines 210-211), dedup-on-parsed-integer (lines 214-216), the append-only and immutability invariants (lines 426-439), why `CREATE DATABASE` needs the maintenance DB + autocommit (lines 127-129, 143), and why `--check` skips the create-and-commit writes (lines 404-407, 417-420).
4. Logging: No issues found beyond the deferred item 1 above. Uses `logconfig`, no `print()`, f-strings throughout, appropriate DEBUG/INFO/ERROR levels, no redundant "Entering/Exiting" messages, and opening/closing `"=" * 60` run-boundary separators owned solely by `main` — including the `except SystemExit` arm (lines 517-523) that keeps the check-mode-pending exit symmetric with the other exit paths.
5. Exception Handling: No issues found (item 1 is a suggestion, not a defect). Specific exceptions only, no bare `except`; `apply_one` catches `psycopg2.Error`, rolls back, logs, and bare-`raise`s to preserve type (lines 359-362); `create_database_if_absent` and `run` use `try/finally` to guarantee `conn.close()`; `main` dispatches on `KeyError`, `(FileNotFoundError, ValueError, RuntimeError)`, and `psycopg2.Error` with contextful `Error:`-prefixed messages (prior review item 2, completed) and re-raises `SystemExit` unchanged; domain violations raise `RuntimeError`, never generic `Exception`.
6. Executable Scripts: No issues found. `main()` with `if __name__ == "__main__"` guard (`# pragma: no cover`), required `--config`, logging deferred until after `parse_args`, and config-existence + TOML-decode failures handled before dispatch. The `--check`/`--create-db` mode flags remain a defensible CI/maintainer deviation accepted in prior reviews.
7. Data Validation: N/A — this is a DDL migration applier, not a `data_val_` output-validation script; the `data_val_` naming / `data_validation/` directory convention does not apply.
8. Unit Tests: N/A for this source file's content. Tests exist at `code/apply_ddl/unit_tests/test_apply_ddl.py` (with `conftest.py`); they were not run as part of this review-only pass and are reviewed under their own review file.
9. SQL (best-practices): No issues found. Embedded SQL is lowercase throughout (lines 147, 153, 175, 244-249, 252-254, 274, 290, 354-355), uses explicit columns (`select version, checksum`, no `select *`), parameterized `%s` placeholders (lines 147, 354-355), and `sql.Identifier` for the dynamic database and schema names (lines 153, 175-177) preventing injection in statement text. The per-block `Level =` annotation convention targets standalone `.sql` files rather than these trivial single-statement embedded queries.
