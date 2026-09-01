---
name: "cr_20260813v01_apply_ddl"
goal: Re-review of code/apply_ddl/apply_ddl.py since cr_20260730v01 against the python-development and sql-development skills, reviewed as a group with unit_tests/test_apply_ddl.py; only deferred suggestions remain (finally-block close logging, schema-comment logging, one blank-line style nit).
created: 2026-08-13 11:05:07
updated: 2026-08-13 11:05:07
---

## Implementation Plan

1. [completed] Silent `finally` cleanup blocks omit close-time logging - `code/apply_ddl/apply_ddl.py`
   - 1.1. [suggestion] Lines 178-179: `create_database_if_absent`'s `try/finally` closes the maintenance connection with `conn.close()` and no log. Exception-handling guideline 5 lists `finally` among the stages that "should all include appropriate logging", and the skill's own example emits a `logger.debug(...)` in `finally`.
        - Current: `finally:\n        conn.close()`
        - Expected: `finally:\n        conn.close()\n        logger.debug(f"Closed maintenance connection to {maint_kwargs['dbname']}")`
        - Resolution: Deferred — optional. This `finally` performs only resource cleanup; the success paths are already logged at INFO ("already exists" line 172, "Created" line 177) and any failure propagates a `psycopg2.Error` that `main` logs, so a close-time DEBUG line adds noise without new information. Carried over unchanged from cr_20260727v01 through cr_20260730v01.
   - 1.2. [suggestion] Lines 618-619: `run`'s `try/finally` closes the primary connection with `conn.close()` and no log, identical in kind to 1.1.
        - Current: `finally:\n        conn.close()`
        - Expected: `finally:\n        conn.close()\n        logger.debug(f"Closed connection to {conn_kwargs['dbname']}")`
        - Resolution: Deferred — optional. Cleanup-only `finally`; the connect is already logged at DEBUG (line 519) and every terminal outcome (in-sync, allow-pending exemption, nothing-to-apply, applied N) logs before the block runs, so a close-time DEBUG line is noise rather than context. Carried over unchanged from cr_20260727v01 through cr_20260730v01.

2. [completed] Schema-comment application is not logged - `code/apply_ddl/apply_ddl.py`
   - 2.1. [suggestion] Lines 214-220: `ensure_schema` applies `COMMENT ON SCHEMA` when `schema_comment` is set but emits no log for it; the function's only message is the "Ensuring schema ... exists" DEBUG at line 207. Logging guideline 3 (include context) would support a DEBUG line noting the comment was (re)applied, since editing the knob takes effect silently on the next run.
        - Current: `if schema_comment is not None:\n            cur.execute(\n                sql.SQL("comment on schema {} is %s").format(...)`
        - Expected: add after the execute — `logger.debug(f"Applied schema comment to {schema}")`
        - Resolution: Deferred — optional. The DEBUG at line 207 already marks the call, the operation is idempotent and driven directly by visible config, and any failure propagates a `psycopg2.Error` that `main` logs; a per-run comment-applied line adds little diagnostic value. Carried over unchanged from cr_20260730v01 item 3.1.

3. [completed] Blank-line spacing before a top-level function - `code/apply_ddl/apply_ddl.py`
   - 3.1. [suggestion] Lines 72-73: only one blank line separates the `TXN_CONTROL_RE` constant (closing `)` at line 71) from `def compute_checksum`; PEP 8 calls for two blank lines before a top-level `def`, and every other top-level definition in this file uses two. Purely cosmetic inconsistency.
        - Current: `)\n\ndef compute_checksum(path: Path) -> str:`
        - Expected: `)\n\n\ndef compute_checksum(path: Path) -> str:`
        - Resolution: Deferred — optional. No python-development core skill covers blank-line spacing, the deviation has no behavioral effect, and a one-line whitespace edit is not worth an implementation round-trip on its own; fold into the next substantive edit that touches this region.

## Skills with No Issues

1. Type Hints: No issues found. All functions carry parameter and return annotations using modern syntax (`dict[str, str]`, `list[tuple[str, Path]]`, `str | None`, `dict[str, Any]` for the parsed TOML config, `psycopg2.extensions.connection`); nothing changed for the worse since cr_20260730v01.
2. Docstrings: No issues found. cr_20260730v01's sole minor (the `run` docstring omitting `schema_comment`) is resolved — the `config` Args entry now documents the knob including its ignored-in---check behavior (lines 482-484). `run`'s Raises list was verified accurate against the shared helper: `pgconn.connection_kwargs` does raise `ValueError` on a non-lowercase schema identifier and `RuntimeError` on missing `POSTGRES_*` env vars (code/lib/pgconn/pgconn.py lines 67-79). `compute_checksum` documents the why of line-ending normalization; `strip_sql_comments` and `check_no_transaction_control` document the lexical-scan limitation; `ddl_versions_exists`, `schema_present`, and `has_schema_usage` each explain their role in the --check no-writes contract.
3. Comments: No issues found. Comments explain "why" throughout and were spot-verified as current: the `sys.path` preamble comment (lines 29-32) matches the actual `parents[2] / "code" / "lib"` resolution; the iterate-directly-not-glob rationale (lines 244-247), numeric-sort and dedup-on-parsed-integer notes (lines 268-274), the `to_regclass`-NULL disambiguation in --check (lines 530-535), and the append-only/immutability invariant comments (lines 561-574) all match the code beneath them.
4. Logging: No defects; the deferred suggestions in tasks 1 and 2 are the only observations. Uses `logconfig` with `log_dir="logs/apply_ddl"` mirroring the script location, no `print()`, f-strings throughout, appropriate DEBUG/INFO/WARNING/ERROR levels (including the `--allow-pending` typo WARNING at lines 586-590), and `"=" * 60` run-boundary separators owned solely by `main`, including the symmetric `except SystemExit` arm (lines 689-695).
5. Exception Handling: No issues found. Specific exceptions only; `apply_one` catches `psycopg2.Error`, rolls back, logs with the failing version, and bare-`raise`s to preserve type (lines 465-468); `create_database_if_absent` and `run` use `try/finally` to guarantee `conn.close()`; `main` dispatches on `KeyError`, `(FileNotFoundError, ValueError, RuntimeError)`, and `psycopg2.Error` with contextful messages and re-raises `SystemExit` unchanged; domain violations raise `ValueError`/`RuntimeError`, never generic `Exception`.
6. Executable Scripts: No issues found. `main()` with `if __name__ == "__main__"` guard (`# pragma: no cover`), required `--config` pointing at the `config/` subdirectory, logging setup deferred until after `parse_args`, and config-existence plus TOML-decode failures handled before dispatch. The `--check`/`--create-db`/`--allow-pending` mode flags remain the CI/maintainer deviation accepted since cr_20260526v01.
7. Data Validation: N/A - this is a DDL migration applier, not a `data_val_` output-validation script; the data-validation conventions do not apply.
8. Unit Tests: N/A for this source file's content — tests live at code/apply_ddl/unit_tests/test_apply_ddl.py and are reviewed in cr_20260813v01_test_apply_ddl.md. Grouped-review consistency check: every public function here (`compute_checksum`, `strip_sql_comments`, `check_no_transaction_control`, `create_database_if_absent`, `ensure_schema`, `list_repo_migrations`, `ensure_ddl_versions`, `ddl_versions_exists`, `schema_present`, `has_schema_usage`, `applied_migrations`, `verify_checksums`, `apply_one`, `run`, `main`) has matching tests, and the test file's assertions agree with this file's current behavior — no divergence found; all 125 tests pass (0.45s).
9. SQL (sql-development best practices): No issues found. Embedded SQL is lowercase throughout, uses explicit columns (no `select *`), parameterizes all values via `%s` placeholders (lines 169, 218-219, 354, 377, 460-463), and injects every dynamic identifier (database name, schema name) via `psycopg2.sql.Identifier` (lines 175, 210-212, 216-218) so no identifier is ever concatenated into statement text. The per-block `Level =` annotation convention targets standalone `.sql` files, not these trivial single-statement embedded queries.
