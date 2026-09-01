---
name: cr_20260730v01_apply_ddl
goal: Re-review code/apply_ddl/apply_ddl.py against python-development and sql-development skills following cr_20260727v01, covering the new schema_comment config knob and ddl_versions table comment; the two deferred close-time-logging suggestions carry over.
created: 2026-07-30 14:19:14
updated: 2026-07-30 14:36:51
---

## Implementation Plan

1. [completed] Docstring omits the new optional config key - `code/apply_ddl/apply_ddl.py`
   - 1.1. [minor] Line 541: `run`'s `Args` entry for `config` lists only `ddl_dir`, `database`, and `schema`, but the function now also reads the optional `schema_comment` key (line 568) and forwards it to `ensure_schema` (line 614). Docstrings guideline 4 requires keeping docstrings current when behavior/parameters change; a caller reading this docstring would not discover the knob.
        - Current: `config: Parsed TOML config with keys \`ddl_dir\`, \`database\`, and\n            \`schema\`.`
        - Expected: `config: Parsed TOML config with keys \`ddl_dir\`, \`database\`, and\n            \`schema\`, plus the optional \`schema_comment\` (applied as\n            \`COMMENT ON SCHEMA\` on every apply run; ignored in --check).`
        - Resolution: Implemented as specified — extended the `config` Args entry in `run`'s docstring to document the optional `schema_comment` key, including that it is applied as `COMMENT ON SCHEMA` on every apply run and ignored in --check mode. No code behavior changed.

2. [completed] Silent `finally` cleanup blocks omit close-time logging - `code/apply_ddl/apply_ddl.py`
   - 2.1. [suggestion] Lines 237-238: `create_database_if_absent`'s `try/finally` closes the maintenance connection with `conn.close()` and no log. Exception-handling guideline 5 lists `finally` among the stages that "should all include appropriate logging", and the skill's own example emits a `logger.debug(...)` in `finally`.
        - Current: `finally:\n        conn.close()`
        - Expected: `finally:\n        conn.close()\n        logger.debug(f"Closed maintenance connection to {maint_kwargs['dbname']}")`
        - Resolution: Deferred — optional. This `finally` performs only resource cleanup; the success paths are already logged at INFO ("already exists" line 231, "Created" line 236) and any failure propagates a `psycopg2.Error` that `main` logs, so a close-time DEBUG line adds noise without new information. Carried over unchanged from cr_20260727v01 item 1.1.
   - 2.2. [suggestion] Lines 676-677: `run`'s `try/finally` closes the primary connection with `conn.close()` and no log, identical in kind to 2.1.
        - Current: `finally:\n        conn.close()`
        - Expected: `finally:\n        conn.close()\n        logger.debug(f"Closed connection to {conn_kwargs['dbname']}")`
        - Resolution: Deferred — optional. Cleanup-only `finally`; the connect is already logged at DEBUG (line 577) and every terminal outcome (in-sync, allow-pending exemption, nothing-to-apply, applied N) logs before the block runs, so a close-time DEBUG line is noise rather than context. Carried over unchanged from cr_20260727v01 item 1.2.

3. [completed] Schema-comment application is not logged - `code/apply_ddl/apply_ddl.py`
   - 3.1. [suggestion] Lines 273-279: `ensure_schema` applies `COMMENT ON SCHEMA` when `schema_comment` is set but emits no log for it; the function's only message is the "Ensuring schema ... exists" DEBUG at line 266. Logging guideline 3 (include context) would support a DEBUG line noting the comment was (re)applied, since editing the knob takes effect silently on the next run.
        - Current: `if schema_comment is not None:\n            cur.execute(\n                sql.SQL("comment on schema {} is %s").format(...)`
        - Expected: add after the execute — `logger.debug(f"Applied schema comment to {schema}")`
        - Resolution: Deferred — optional. The DEBUG at line 266 already marks the call, the operation is idempotent and driven directly by visible config, and any failure propagates a `psycopg2.Error` that `main` logs; a per-run comment-applied line adds little diagnostic value.

## Skills with No Issues

1. Type Hints: No issues found. The new `schema_comment: str | None = None` parameter on `ensure_schema` uses modern union syntax; all other signatures are unchanged from cr_20260727v01 and remain fully and specifically annotated (`dict[str, str]`, `list[tuple[str, Path]]`, `psycopg2.extensions.connection`, `dict[str, Any]` for the parsed TOML config).
2. Docstrings: One minor issue — see Implementation Plan item 1. Otherwise current: `ensure_schema`'s docstring documents the new `schema_comment` arg with its why (a schema-level comment needs the literal schema name, which schema-agnostic migrations cannot carry, lines 253-259), and `ensure_ddl_versions` explains why the table comment lives in the bootstrap rather than migration 0001 (lines 349-352); `run`'s `Raises` list remains complete.
3. Comments: No issues found. Comments explain "why" throughout — the libpq-`options`/uppercase-folding schema-validation rationale (lines 79-86), the statement-boundary transaction-control scan and its lexical limits (lines 55-67), iterate-directly-not-glob for case-variant extensions (lines 303-306), numeric sort and dedup-on-parsed-integer (lines 327-333), append-only/immutability invariants (lines 620-632), and the `to_regclass`-NULL disambiguation in `--check` mode (lines 588-593).
4. Logging: No defects; the three deferred suggestions above (items 2 and 3) are the only observations. Uses `logconfig`, no `print()`, f-strings throughout, appropriate DEBUG/INFO/WARNING/ERROR levels (including the `--allow-pending` typo WARNING at lines 645-648), and `"=" * 60` run-boundary separators owned solely by `main`, including the symmetric `except SystemExit` arm (lines 747-753).
5. Exception Handling: No issues found. Specific exceptions only; `apply_one` catches `psycopg2.Error`, rolls back, logs, and bare-`raise`s to preserve type (lines 524-527); `create_database_if_absent` and `run` use `try/finally` to guarantee `conn.close()`; `main` dispatches on `KeyError`, `(FileNotFoundError, ValueError, RuntimeError)`, and `psycopg2.Error` with contextful messages and re-raises `SystemExit` unchanged; domain violations raise `ValueError`/`RuntimeError`, never generic `Exception`.
6. Executable Scripts: No issues found. `main()` with `if __name__ == "__main__"` guard (`# pragma: no cover`), required `--config`, logging deferred until after `parse_args`, and config-existence + TOML-decode failures handled before dispatch. The `--check`/`--create-db`/`--allow-pending` mode flags remain the CI/maintainer deviation accepted in prior reviews.
7. Data Validation: N/A — this is a DDL migration applier, not a `data_val_` output-validation script; the data-validation conventions do not apply.
8. Unit Tests: N/A for this source file's content. Tests live at `code/apply_ddl/unit_tests/test_apply_ddl.py` and are reviewed under their own review file; they were not run as part of this review-only pass.
9. SQL (best-practices): No issues found. Embedded SQL is lowercase throughout, including the new `comment on schema {} is %s` (line 275) and `comment on table ddl_versions is ...` (lines 369-372); explicit columns (no `select *`), parameterized `%s` placeholders for values (lines 228, 278, 413, 436, 520), and `sql.Identifier` for the dynamic database and schema names (lines 234, 269-271, 276-277) so no identifier is ever concatenated into statement text. The per-block `Level =` annotation convention targets standalone `.sql` files, not these trivial single-statement embedded queries.
