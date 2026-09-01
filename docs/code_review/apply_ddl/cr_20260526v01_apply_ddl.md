---
name: cr_20260526v01_apply_ddl
goal: Address code quality issues identified in code/apply_ddl/apply_ddl.py to align with python-development and sql-development skills.
created: 2026-05-26 00:00:00
updated: 2026-07-01 00:00:00
---

## Implementation Plan

1. [completed] Fix migration ordering correctness - `code/apply_ddl/apply_ddl.py`
   - 1.1. [critical] Line 130: Migrations were sorted lexicographically by full filename, but the module docstring, the `list_repo_migrations` docstring, and `run` rely on "numeric order". `VERSION_RE` captures `\d+` of arbitrary length, so unpadded or variable-width prefixes (e.g. `9_x.sql` vs `10_x.sql`) sorted wrong (`"10" < "9"`). Resolved: `list_repo_migrations` now sorts via `entries.sort(key=lambda entry: int(entry[0]))` (line 160) before the duplicate check and return; the docstring now says "sorted by numeric version". Verified against source.

2. [completed] Type hints - `code/apply_ddl/apply_ddl.py`
   - 2.1. [major] `config: dict` was under-specified. Resolved: `run(config: dict[str, Any], ...)` (line 293) with `from typing import Any` in the imports (line 25). Verified against source.

3. [pending] Migration-checksum immutability logic - `code/apply_ddl/apply_ddl.py`
   - 3.1. [minor] Lines 47-53, 61: `compute_checksum` docstring overclaims cross-platform stability. The newline claim is correct — `path.read_text()` (line 61) applies universal-newline translation, so a CRLF checkout and an LF checkout hash identically (verified: both produce the same SHA-256). But the docstring also states the checksum "compares logical content, not on-disk byte encoding." `read_text()` with no `encoding=` argument decodes using the platform/locale default (e.g. cp1252 on Windows, utf-8 on Linux), so a migration containing non-ASCII bytes could hash differently across platforms despite identical content. This is not "encoding"-stable; only newline-stable.
        - Current: `return hashlib.sha256(path.read_text().encode("utf-8")).hexdigest()`
        - Expected: `return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()` (pin the read encoding to match the output encoding and the docstring's claim); or narrow the docstring to describe only newline stability.
   - 3.2. [suggestion] Lines 273-274: `apply_one` reads the file from disk twice — `sql_text = path.read_text()` then `compute_checksum(path)` re-reads the same bytes. Besides the redundant I/O, a change to the file between the two reads would apply one body while recording a checksum of a different one. Read once and hash the in-memory text.
        - Current:
          ```python
          sql_text = path.read_text()
          checksum = compute_checksum(path)
          ```
        - Expected (illustrative):
          ```python
          sql_text = path.read_text(encoding="utf-8")
          checksum = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()
          ```
   - 3.3. [suggestion] Lines 303-311: `run` docstring `Raises` section is incomplete for the checksum path. It documents `RuntimeError` for missing env vars and for "the DB has migrations not present in the repo" (the append-only check at lines 333-338), but not the immutability violation raised by `verify_checksums` (line 343) when an applied migration's file was edited. Add that case so the documented contract matches behavior.

## Skills with No Issues

1. Type Hints: No new issues. The checksum additions carry full modern annotations — `compute_checksum(path: Path) -> str`, `applied_migrations(...) -> dict[str, str | None]`, `verify_checksums(repo_by_version: dict[str, Path], applied: dict[str, str | None]) -> None`, `apply_one(..., version: str, path: Path) -> None`. Item 2.1 remains resolved.
2. Docstrings: All checksum functions have Google-style docstrings with Args/Returns/Raises as applicable. Two accuracy gaps noted (items 3.1 and 3.3) are content overclaims/omissions, not missing-docstring or formatting issues.
3. Comments: No issues found. New comments explain "why" — the append-only invariant (lines 329-332), the immutability invariant and why it runs in both modes (lines 340-342), and the NULL-checksum skip rationale in `verify_checksums` (line 241).
4. Logging: No issues found. `verify_checksums` raising and the append-only check surface through `run`/`main`'s existing ERROR logging (lines 422-425); f-strings throughout; no `print()`; `"=" * 60` run boundaries retained.
5. Exception Handling: No issues found. `verify_checksums` fails fast with a specific `RuntimeError` carrying the offending versions (lines 246-250); `apply_one` still catches `psycopg2.Error`, rolls back, logs, and bare-`raise`s to preserve type (lines 285-288); `run`'s append-only `RuntimeError` is specific and contextful. All caught by `main`'s specific handlers.
6. Executable Scripts: No issues found. `main()` with `if __name__ == "__main__"` guard, `--config` TOML plus mode flags, logging deferred until after argparse. The checksum feature added no new CLI surface.
7. Data Validation: N/A - migration applier, not a `data_val_` output-validation script; the `data_val_` prefix / `data_validation/` directory convention does not apply.
8. Unit Tests: N/A for this file's content; tests live at `code/apply_ddl/unit_tests/test_apply_ddl.py`. Per the task, the suite passes at 100% coverage and was not run.
9. SQL (best-practices): No issues found. The `checksum text` column is added idempotently — `CREATE TABLE IF NOT EXISTS` includes it (line 188) plus a defensive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS checksum text` (line 194) for pre-existing tables. `applied_migrations` selects explicit columns (`SELECT version, checksum`, line 212), no `SELECT *`; the insert uses `%s` placeholders (lines 279-283); all lowercase types.
