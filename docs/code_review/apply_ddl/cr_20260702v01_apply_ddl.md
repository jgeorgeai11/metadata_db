---
name: cr_20260702v01_apply_ddl
goal: Address code quality issues identified in code/apply_ddl/apply_ddl.py to align with python-development and sql-development skills.
created: 2026-07-02 00:00:00
updated: 2026-07-02 00:00:00
---

## Implementation Plan

1. [completed] Embedded SQL formatting — `code/apply_ddl/apply_ddl.py`
   - 1.1. [minor] Lines 122, 128, 190-195, 212, 276-277: Every embedded SQL statement uses uppercase keywords, but the sql-development best-practices skill mandates "Lowercase everything" (guideline 7). This applies to `SELECT`, `FROM`, `WHERE`, `CREATE DATABASE`, `CREATE TABLE IF NOT EXISTS`, `PRIMARY KEY`, `NOT NULL`, `DEFAULT`, `INSERT INTO`, and `VALUES`. Column/type names are already lowercase; only the keywords deviate. Purely stylistic, but inconsistent with the project standard.
        - Current: `cur.execute("SELECT version, checksum FROM ddl_versions")`
        - Expected: `cur.execute("select version, checksum from ddl_versions")`
        - Current (representative DDL):
          ```sql
          CREATE TABLE IF NOT EXISTS ddl_versions (
              version text PRIMARY KEY,
              checksum text NOT NULL,
              applied_ts timestamptz NOT NULL DEFAULT now()
          )
          ```
        - Expected:
          ```sql
          create table if not exists ddl_versions (
              version text primary key,
              checksum text not null,
              applied_ts timestamptz not null default now()
          )
          ```

2. [completed] Logging run-boundary symmetry — `code/apply_ddl/apply_ddl.py`
   - 2.1. [minor] Lines 400-402 and 407-409: `main` writes the opening run separator `logger.info("=" * 60)` (line 397) but the two early-exit paths — config file not found and TOML decode/read failure — call `logger.error(...)` then `sys.exit(1)` without a closing `logger.info("=" * 60)`. Every exit path inside the `run` try/except (lines 413-426) does emit the closing separator, so these two paths are the only ones that leave the run boundary unclosed. The logging skill (guideline 7) calls for separators marking both start and end of a run.
        - Current:
          ```python
          if not config_path.exists():
              logger.error(f"Config file not found: {args.config}")
              sys.exit(1)
          ```
        - Expected:
          ```python
          if not config_path.exists():
              logger.error(f"Config file not found: {args.config}")
              logger.info("=" * 60)
              sys.exit(1)
          ```

3. [completed] Version identity: dedup by string vs. sort by int — `code/apply_ddl/apply_ddl.py`
   - 3.1. [minor] Lines 165-171: `list_repo_migrations` sorts by the parsed integer (`key=lambda entry: int(entry[0])`) but the duplicate-version guard compares the raw string prefixes (`set(versions) != len(versions)` where each `version` is `match.group(1)`). Two files whose prefixes are numerically equal but textually distinct — e.g. `0001_a.sql` (version `"0001"`) and `1_b.sql` (version `"1"`) — pass the duplicate check as two separate versions, sort to an arbitrary relative order, and would both be applied and recorded as distinct `ddl_versions` rows. In practice the zero-padded 4-digit convention avoids this, so it is a latent edge case rather than an active bug, but the sort key and the dedup key should agree.
        - Current: `if len(set(versions)) != len(versions):` (comparing raw prefix strings)
        - Expected: dedup on the same normalized integer used for sorting, e.g. compare `int(v)` values (or validate/enforce a fixed-width zero-padded prefix in `VERSION_RE`) so numerically equal prefixes are rejected as duplicates.

4. [completed] Executable-scripts CLI surface — `code/apply_ddl/apply_ddl.py`
   - 4.1. [suggestion] Lines 372-393: The executable-scripts skill (guideline 2) describes `main()` using "a single `--config` argument". This script adds `--check` and `--create-db` flags. These are reasonable CI/maintainer mode selectors and the deviation is defensible, but for strict alignment consider moving mode selection into the TOML config (e.g. a `mode`/`create_db` key) so `--config` remains the sole argument, or documenting the intentional deviation. No code change required if the deviation is accepted.

## Skills with No Issues

1. Type Hints: No issues found. Every function is fully annotated with modern syntax — `compute_checksum(path: Path) -> str`, `connection_kwargs(database: str) -> dict[str, str]`, `list_repo_migrations(ddl_dir: Path) -> list[tuple[str, Path]]`, `applied_migrations(...) -> dict[str, str]`, `run(config: dict[str, Any], check_only: bool, create_db: bool) -> None`, `main() -> None`. `psycopg2.extensions.connection` is used consistently for connection params.
2. Docstrings: No issues found. Module docstring plus Google-style docstrings on every function; Args/Returns/Raises are present where applicable and accurate. `run`'s Raises now documents the checksum/append-only `RuntimeError` and the `--check`-mode `SystemExit` (lines 299-308); `compute_checksum`'s cross-platform claim now matches the pinned `encoding="utf-8"` read (line 61).
3. Comments: No issues found. Comments explain "why" — the numeric-vs-lexical sort rationale (lines 163-164), the append-only invariant (lines 326-329), the immutability invariant and why it runs in both modes (lines 337-339), and why `CREATE DATABASE` needs the maintenance DB and autocommit (lines 102-104, 118).
4. Logging: See Implementation Plan item 2. Otherwise clean — uses `logconfig`, no `print()`, f-strings throughout, appropriate DEBUG/INFO/ERROR levels, no redundant "Entering/Exiting" messages.
5. Exception Handling: No issues found. Specific exceptions only; `apply_one` catches `psycopg2.Error`, rolls back, logs, and bare-`raise`s to preserve type (lines 281-284); `main` dispatches on `KeyError`, `(FileNotFoundError, ValueError, RuntimeError)`, and `psycopg2.Error` with contextful messages; `SystemExit` from `--check` correctly propagates past those handlers.
6. Executable Scripts: See Implementation Plan item 4 (suggestion only). `main()` with `if __name__ == "__main__"` guard, `--config` required, logging deferred until after `parse_args`, config-existence and TOML-decode failures handled.
7. Data Validation: N/A — this is a DDL migration applier, not a `data_val_` output-validation script; the `data_val_` naming / `data_validation/` directory convention does not apply.
8. Unit Tests: N/A for this source file's content. Tests exist at `code/apply_ddl/unit_tests/test_apply_ddl.py` with `code/apply_ddl/unit_tests/conftest.py`; they were not run as part of this review-only pass.
9. SQL (best-practices): See Implementation Plan item 1 (lowercase). Otherwise clean — explicit columns (`select version, checksum`, no `SELECT *`); parameterized `%s` placeholders (lines 122, 276-277); `sql.Identifier` for the dynamic database name in `CREATE DATABASE` (line 128) preventing injection; lowercase types.
