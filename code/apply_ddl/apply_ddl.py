"""apply_ddl.py — apply pending DDL migrations to metadata_db.

Reads numbered .sql files from the migration directory configured in
the TOML config, compares to the ddl_versions tracking table in the
target database, and applies any missing migrations in numeric order.
Each migration runs inside its own transaction; if a migration fails,
that file's effects are rolled back and the script exits non-zero.

Connection host/port/user/password are read from a .env file
(POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD);
the target database name comes from the TOML config.

Used both by maintainers (full apply mode, optionally --create-db on a
fresh instance) and by the pre-merge check_schema_in_sync CI job
(--check mode).
"""

import sys
import argparse
import hashlib
import re
import tomllib
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql

# The vendored packages under code/lib (logconfig, pgconn) are resolved
# from this file's own location, so imports work from any working
# directory (not just the repo root) and CI never depends on untracked
# .claude/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import setup_logging, get_logger
from pgconn import connection_kwargs

logger = get_logger(__name__)


# Migration filenames must start with a numeric prefix, e.g.,
#   0001_initial_schema.sql
#   0002_add_some_index.sql
VERSION_RE = re.compile(r"^(\d+)_")

# The one lowercase extension migrations may use. A file whose extension is a
# case-variant of this (`.SQL`, `.Sql`) is a naming mistake, not a silent
# skip — see list_repo_migrations (mirrors the corpus wrong-extension guard).
SQL_SUFFIX = ".sql"

# SQL line comments (`--` to end of line) and block comments (`/* ... */`,
# spanning newlines). Stripped before the transaction-control scan so a
# keyword mentioned only in a comment is not a false positive.
_SQL_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)

# Transaction-control statements that must not appear at the top level of a
# migration file. apply_one wraps each migration in a single transaction and
# writes its ddl_versions row in that same transaction; an embedded COMMIT
# (or BEGIN / ROLLBACK / START TRANSACTION) would silently split that
# atomicity, leaving half a migration applied with no ledger row.
#
# The scan runs on comment-stripped text and matches a keyword only at a
# statement boundary — the start of the text or immediately after a `;` — so
# PL/pgSQL block keywords inside a `do $$ begin ... end $$` block (preceded by
# the dollar-quote, not a `;`) are not flagged. Limitation: this is lexical,
# not a full SQL parser; it does not track string literals, so a keyword
# appearing right after a `;` inside a quoted string would be a false
# positive. No migration here contains such a literal.
TXN_CONTROL_RE = re.compile(
    r"(?:^|;)\s*(commit|begin|rollback|start\s+transaction)\b",
    re.IGNORECASE,
)

def compute_checksum(path: Path) -> str:
    """Return a line-ending-stable SHA-256 of a migration file's contents.

    The file is read in text mode so universal-newline translation
    normalizes `\\r\\n`/`\\r` to `\\n` before hashing. This keeps the
    checksum identical across platforms (e.g. a repo checked out with
    CRLF on Windows vs. LF on Linux), so the immutability check compares
    logical content, not on-disk byte encoding.

    Args:
        path: Path to a `.sql` migration file.

    Returns:
        Hex-encoded SHA-256 of the file's newline-normalized text.
    """
    return _sha256_text(path.read_text(encoding="utf-8"))


def _sha256_text(text: str) -> str:
    """SHA-256 hex digest of `text` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_sql_comments(sql_text: str) -> str:
    """Remove SQL line and block comments from migration text.

    Used before scanning for transaction-control statements so a keyword
    appearing only inside a comment (e.g. "rationale lives in the git
    commit") is not a false positive.

    This is a lexical strip, not a full SQL parser: it does not track string
    literals, so a transaction-control keyword sitting inside a quoted string
    would survive the strip and (if at a statement boundary) trip the scan.
    Migrations here contain no such literal; the limitation is documented on
    `TXN_CONTROL_RE`.

    Args:
        sql_text: Raw migration SQL.

    Returns:
        The SQL with `--` line comments and `/* */` block comments removed.
    """
    return _SQL_COMMENT_RE.sub("", sql_text)


def check_no_transaction_control(sql_text: str, filename: str) -> None:
    """Refuse a migration that contains top-level transaction control.

    apply_ddl wraps each migration (its DDL plus the ddl_versions insert) in
    one transaction; an embedded COMMIT/BEGIN/ROLLBACK/START TRANSACTION
    would split that atomicity and could leave a partially applied migration
    with no ledger row. Comments are stripped first (see strip_sql_comments)
    and only statement-boundary keywords are matched (see TXN_CONTROL_RE), so
    the PL/pgSQL `begin`/`end` inside a `do $$ ... $$` block does not trip it.

    Args:
        sql_text: Raw migration SQL.
        filename: Migration filename, for the error message.

    Raises:
        ValueError: If a top-level transaction-control statement is present.
    """
    match = TXN_CONTROL_RE.search(strip_sql_comments(sql_text))
    if match:
        keyword = " ".join(match.group(1).upper().split())
        raise ValueError(
            f"Migration {filename} contains a top-level transaction-control "
            f"statement ({keyword!r}). apply_ddl wraps each migration in its "
            f"own transaction; embedded transaction control would split that "
            f"atomicity. Remove it (split into separate migrations if needed)."
        )


def create_database_if_absent(conn_kwargs: dict[str, str]) -> None:
    """Create the target database if it doesn't already exist.

    Connects to the `postgres` maintenance database to do so — CREATE
    DATABASE cannot run inside a transaction or from within the target
    database itself. Requires the connecting role to have CREATEDB.

    Args:
        conn_kwargs: Connection kwargs whose `dbname` is the target DB.

    Raises:
        psycopg2.Error: On connection or CREATE DATABASE failure.
    """
    target = conn_kwargs["dbname"]
    maint_kwargs = {**conn_kwargs, "dbname": "postgres"}

    logger.debug(f"Connecting to 'postgres' to check for database {target}")
    conn = psycopg2.connect(**maint_kwargs)
    try:
        # CREATE DATABASE cannot run inside a transaction block.
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from pg_database where datname = %s", (target,)
            )
            if cur.fetchone():
                logger.info(f"Database {target} already exists")
                return
            cur.execute(
                sql.SQL("create database {}").format(sql.Identifier(target))
            )
            logger.info(f"Created database {target}")
    finally:
        conn.close()


def ensure_schema(
    conn: psycopg2.extensions.connection,
    schema: str,
    schema_comment: str | None = None,
) -> None:
    """Create the target schema if it doesn't exist, then commit.

    A write, so `run` calls this only outside `--check` mode. The name is
    injected via `psycopg2.sql.Identifier` (not string formatting) so a
    schema name is never concatenated into the statement text. Idempotent —
    safe on every apply run.

    When `schema_comment` is set (the optional `schema_comment` config
    knob), it is applied as `COMMENT ON SCHEMA` here rather than in a
    migration: a schema-level comment needs the literal schema name, which
    a schema-agnostic migration cannot carry — the same reason
    `ddl_versions` is commented in `ensure_ddl_versions`. Reapplied on
    every run, so editing the knob takes effect on the next apply.

    Args:
        conn: Active psycopg2 connection.
        schema: Schema name to create if absent.
        schema_comment: Optional schema description to apply as
            `COMMENT ON SCHEMA`; None leaves any existing comment alone.
    """
    logger.debug(f"Ensuring schema {schema} exists")
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("create schema if not exists {}").format(
                sql.Identifier(schema)
            )
        )
        if schema_comment is not None:
            cur.execute(
                sql.SQL("comment on schema {} is %s").format(
                    sql.Identifier(schema)
                ),
                (schema_comment,),
            )
    conn.commit()


def list_repo_migrations(ddl_dir: Path) -> list[tuple[str, Path]]:
    """Find every numbered .sql migration in the given directory.

    Args:
        ddl_dir: Directory containing numbered .sql migration files.

    Returns:
        List of (version, path) tuples, sorted by numeric version.

    Raises:
        FileNotFoundError: If `ddl_dir` doesn't exist.
        ValueError: If a filename lacks a numeric prefix, uses a
            case-variant of the `.sql` extension (e.g. `.SQL`/`.Sql`), or two
            files share the same numeric version (e.g. "0001" and "1").
    """
    logger.debug(f"Scanning {ddl_dir} for migrations")
    if not ddl_dir.is_dir():
        raise FileNotFoundError(f"DDL directory not found: {ddl_dir}")

    entries: list[tuple[str, Path]] = []
    # Iterate the directory directly (not glob("*.sql")) so a case-variant
    # extension is caught rather than silently skipped: on a case-sensitive
    # filesystem glob("*.sql") never sees "0002.SQL", so the migration would
    # vanish without a trace. sorted() gives a deterministic scan order.
    for path in sorted(ddl_dir.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix
        if suffix != SQL_SUFFIX and suffix.lower() == SQL_SUFFIX:
            raise ValueError(
                f"Migration file has a non-lowercase SQL extension: "
                f"{path.name}. Rename it to use the lowercase "
                f"'{SQL_SUFFIX}' extension."
            )
        if suffix != SQL_SUFFIX:
            continue
        match = VERSION_RE.match(path.name)
        if not match:
            raise ValueError(
                f"Migration filename does not start with a numeric prefix: "
                f"{path.name}"
            )
        entries.append((match.group(1), path))

    # Sort by parsed integer version so that variable-width prefixes
    # (e.g. 9_x.sql before 10_x.sql) order numerically, not lexically.
    entries.sort(key=lambda entry: int(entry[0]))

    # Dedup on the parsed integer, not the raw prefix string, so that
    # numerically-equal but textually-distinct prefixes (e.g. "0001" and
    # "1") are rejected rather than both being applied.
    versions = [v for v, _ in entries]
    numeric_versions = [int(v) for v in versions]
    if len(set(numeric_versions)) != len(numeric_versions):
        # Name only the colliding prefixes — with many migrations, echoing
        # the whole list buries the pair the maintainer has to rename.
        duplicates = sorted(
            v for v in versions if numeric_versions.count(int(v)) > 1
        )
        raise ValueError(
            f"Duplicate migration versions in {ddl_dir}: {duplicates}"
        )
    logger.info(f"Found {len(entries)} migration(s) in {ddl_dir}")
    return entries


def ensure_ddl_versions(conn: psycopg2.extensions.connection) -> None:
    """Create the ddl_versions tracking table if it doesn't exist.

    Idempotent — safe to call on every run. `checksum` is NOT NULL:
    every migration records one at apply time, so the immutability check
    never has to reason about missing checksums. The table comment lives
    here (not in any numbered migration) because ddl_versions is this
    script's own table: commenting it from 0001 would make that
    migration unapplyable through any path that lacks this bootstrap.

    Args:
        conn: Active psycopg2 connection.
    """
    logger.debug("Ensuring ddl_versions table exists")
    with conn.cursor() as cur:
        cur.execute(
            """
            create table if not exists ddl_versions (
                version text primary key,
                checksum text not null,
                applied_ts timestamptz not null default now()
            )
            """
        )
        cur.execute(
            "comment on table ddl_versions is "
            "'Applied schema-migration ledger — one row per DDL version "
            "with its checksum and applied_ts'"
        )
    conn.commit()


def ddl_versions_exists(conn: psycopg2.extensions.connection) -> bool:
    """Return True if the ddl_versions tracking table exists.

    Read-only (a single `to_regclass` lookup, no DDL, no commit). Used by
    `--check` mode so it can inspect applied migrations without the
    create-and-commit that `ensure_ddl_versions` performs — keeping the
    check-mode "no writes" contract intact even against a fresh database.

    Args:
        conn: Active psycopg2 connection.

    Returns:
        True if the ddl_versions table is present in the database.
    """
    with conn.cursor() as cur:
        cur.execute("select to_regclass('ddl_versions')")
        return cur.fetchone()[0] is not None


def schema_present(conn: psycopg2.extensions.connection, schema: str) -> bool:
    """Return True if a schema of this name exists in the database.

    Reads pg_namespace (visible to every role regardless of schema
    privileges), so it reports true existence even for a schema the
    connecting role has no USAGE on. Used to tell "schema absent" (a fresh
    database — every migration is genuinely pending) apart from "schema
    present but not usable" (a permissions problem, see `run`).

    Args:
        conn: Active psycopg2 connection.
        schema: Schema name to look for.

    Returns:
        True if a schema with this name exists.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from pg_namespace where nspname = %s", (schema,)
        )
        return cur.fetchone() is not None


def has_schema_usage(
    conn: psycopg2.extensions.connection, schema: str
) -> bool:
    """Return True if the connecting role has USAGE on an existing schema.

    Distinguishes the two reasons `to_regclass('ddl_versions')` returns NULL:
    a genuinely absent table vs. a table the role cannot see because it lacks
    USAGE on the schema. Only meaningful when the schema exists (see
    `schema_present`) — `has_schema_privilege` errors on a missing schema.

    Args:
        conn: Active psycopg2 connection.
        schema: Name of an existing schema.

    Returns:
        True if the current role holds USAGE on `schema`.
    """
    with conn.cursor() as cur:
        cur.execute("select has_schema_privilege(%s, 'USAGE')", (schema,))
        return bool(cur.fetchone()[0])


def applied_migrations(
    conn: psycopg2.extensions.connection,
) -> dict[str, str]:
    """Return applied migration versions mapped to their stored checksum.

    Args:
        conn: Active psycopg2 connection.

    Returns:
        Mapping of version string -> stored checksum.
    """
    with conn.cursor() as cur:
        cur.execute("select version, checksum from ddl_versions")
        return {row[0]: row[1] for row in cur.fetchall()}


def verify_checksums(
    repo_by_version: dict[str, Path],
    applied: dict[str, str],
) -> None:
    """Refuse to proceed if any applied migration's file was edited.

    Compares each applied migration still present in the repo against the
    current file's checksum.

    Args:
        repo_by_version: Mapping of version -> migration file path.
        applied: Mapping of applied version -> stored checksum.

    Raises:
        RuntimeError: If one or more applied migrations differ from their
            recorded checksum (append-only / immutability violation).
    """
    edited: list[str] = []
    for version, stored in applied.items():
        path = repo_by_version.get(version)
        if path is None:
            # Missing-from-repo is handled by the append-only check in run().
            continue
        if compute_checksum(path) != stored:
            edited.append(version)
    if edited:
        raise RuntimeError(
            f"Migration(s) edited after being applied (append-only "
            f"violation): {sorted(edited)}. Applied migrations are "
            f"immutable — make changes in a new migration."
        )


def apply_one(
    conn: psycopg2.extensions.connection,
    version: str,
    path: Path,
) -> None:
    """Apply a single migration file inside one transaction.

    On success, commits both the migration's DDL and the matching
    ddl_versions row. On failure, rolls back both — leaving the DB
    in its prior state.

    Args:
        conn: Active psycopg2 connection.
        version: Migration version string, e.g. "0001".
        path: Path to the .sql file containing the migration.

    Raises:
        ValueError: If the migration contains a top-level transaction-control
            statement (checked before execution).
        psycopg2.Error: If the migration SQL or the tracking insert
            fails. The transaction is rolled back before re-raising.
    """
    sql_text = path.read_text(encoding="utf-8")
    check_no_transaction_control(sql_text, path.name)
    checksum = _sha256_text(sql_text)
    logger.info(f"Applying {path.name}")
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text)
            cur.execute(
                "insert into ddl_versions (version, checksum) "
                "values (%s, %s)",
                (version, checksum),
            )
        conn.commit()
    except psycopg2.Error as e:
        conn.rollback()
        logger.error(f"Migration {version} failed: {e}")
        raise
    else:
        logger.info(f"Applied {version}")


def run(
    config: dict[str, Any],
    check_only: bool,
    create_db: bool,
    allow_pending: list[str] | None = None,
) -> None:
    """Execute the apply (or --check) flow given a parsed config.

    Args:
        config: Parsed TOML config with keys `ddl_dir`, `database`, and
            `schema`, plus the optional `schema_comment` (applied as
            `COMMENT ON SCHEMA` on every apply run; ignored in --check).
        check_only: If True, only reports whether the DB is in sync; no
            writes. Exits non-zero if pending migrations exist.
        create_db: If True, create the target database first if absent.
            Ignored in check mode.
        allow_pending: Migration filenames (e.g. "0002_add_index.sql") that
            may be repo-present-but-unapplied without failing `--check`. This
            is how a migration MR's own pipeline passes: the CI job lists the
            MR's newly added migration files, which cannot yet be applied
            (they are only applied post-merge). Ignored outside `--check`.

    Raises:
        KeyError: If a required config field is missing.
        FileNotFoundError: If `ddl_dir` doesn't exist.
        ValueError: If migration filenames are malformed, or the configured
            schema name is not a valid Postgres identifier.
        psycopg2.Error: On any database failure.
        RuntimeError: If env vars are missing, the DB has migrations not
            present in the repo, an already-applied migration's file has
            changed (checksum mismatch / append-only violation), or the role
            lacks USAGE on an existing target schema in `--check` mode.
        SystemExit: Raised with code 1 in --check mode when non-exempt
            migrations are pending.
    """
    ddl_dir = Path(config["ddl_dir"])
    schema = config["schema"]
    schema_comment = config.get("schema_comment")
    conn_kwargs = connection_kwargs(config["database"], schema)

    repo_migrations = list_repo_migrations(ddl_dir)
    repo_by_version = {v: p for v, p in repo_migrations}

    if create_db and not check_only:
        create_database_if_absent(conn_kwargs)

    logger.debug(f"Connecting to database {conn_kwargs['dbname']}")
    conn = psycopg2.connect(**conn_kwargs)
    try:
        if check_only:
            # --check must not write. Skip the create-and-commit that
            # ensure_ddl_versions performs; an absent tracking table just
            # means nothing has been applied yet, so every repo migration
            # is pending and the DB is reported not-in-sync.
            if ddl_versions_exists(conn):
                applied_map = applied_migrations(conn)
            else:
                # to_regclass returns NULL both when ddl_versions genuinely
                # does not exist and when the role cannot see it (no USAGE on
                # the schema). Probe USAGE separately so a permissions problem
                # is reported as such instead of silently misreporting every
                # migration as pending. An absent schema is a genuinely fresh
                # database, so it falls through to "all pending".
                if schema_present(conn, schema) and not has_schema_usage(
                    conn, schema
                ):
                    raise RuntimeError(
                        f"The connecting role lacks USAGE on schema "
                        f"{schema!r}, so applied migrations cannot be read "
                        f"(ddl_versions is invisible, not absent). Grant "
                        f"USAGE on the schema (see the grant scripts) and "
                        f"retry."
                    )
                logger.debug(
                    "ddl_versions table absent; treating all repo "
                    "migrations as pending"
                )
                applied_map = {}
        else:
            # Create the target schema before anything lands in it. This is
            # a write, so it stays out of --check mode (above): an absent
            # schema in check mode behaves like an absent ddl_versions —
            # nothing applied, every migration pending.
            ensure_schema(conn, schema, schema_comment)
            ensure_ddl_versions(conn)
            applied_map = applied_migrations(conn)
        applied = set(applied_map)

        # Append-only invariant: the repo must contain every version the
        # DB has seen. A DB-side version absent from the repo means
        # someone deleted or renamed a migration file — refuse rather
        # than silently proceed.
        unknown = applied - set(repo_by_version)
        if unknown:
            raise RuntimeError(
                f"DB has migrations not present in repo: {sorted(unknown)}. "
                f"Refusing to proceed (migrations are append-only)."
            )

        # Immutability invariant: an already-applied migration whose file
        # has since changed is an append-only violation. Enforced in both
        # apply and --check mode so the MR pipeline surfaces it too.
        verify_checksums(repo_by_version, applied_map)

        pending = [(v, p) for v, p in repo_migrations if v not in applied]

        if check_only:
            allowed = set(allow_pending or [])
            # Warn on exemptions that match nothing pending — usually a typo
            # in the CI-computed --allow-pending list, and easy to miss
            # because it does not fail the check on its own.
            pending_names = {p.name for _, p in pending}
            unused = allowed - pending_names
            if unused:
                logger.warning(
                    f"--allow-pending entries match no pending migration: "
                    f"{sorted(unused)}"
                )
            blocking = [(v, p) for v, p in pending if p.name not in allowed]
            if blocking:
                blocking_versions = [v for v, _ in blocking]
                logger.error(
                    f"Pending migrations (not yet applied): "
                    f"{blocking_versions}"
                )
                sys.exit(1)
            exempted = [p.name for _, p in pending if p.name in allowed]
            if exempted:
                logger.info(
                    f"DB in sync except for --allow-pending migration(s): "
                    f"{exempted}"
                )
                return
            logger.info("DB is in sync with the migration directory")
            return

        if not pending:
            logger.info(
                "Nothing to apply; DB is in sync with the migration directory"
            )
            return

        for version, path in pending:
            apply_one(conn, version, path)
        logger.info(f"Applied {len(pending)} migration(s)")
    finally:
        conn.close()


def main() -> None:
    """Parse args, load config, set up logging, dispatch to `run`."""
    parser = argparse.ArgumentParser(
        description="Apply pending DDL migrations to metadata_db.",
    )
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to TOML configuration file.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Read-only mode: exit non-zero if any migration in the repo "
            "is unapplied. No writes."
        ),
    )
    parser.add_argument(
        "--create-db",
        action="store_true",
        help=(
            "Create the target database first if it doesn't exist "
            "(requires CREATEDB privilege). Ignored with --check."
        ),
    )
    parser.add_argument(
        "--allow-pending",
        action="append",
        default=[],
        metavar="FILENAME",
        help=(
            "Migration filename that may be repo-present-but-unapplied "
            "without failing --check (repeatable). Used by a migration MR's "
            "own pipeline to exempt the files it adds. Ignored without "
            "--check."
        ),
    )
    args = parser.parse_args()

    setup_logging(log_dir="logs/apply_ddl")
    logger.info("=" * 60)

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"Config file not found: {args.config}")
        logger.info("=" * 60)
        sys.exit(1)

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.error(f"Failed to read config file: {e}")
        logger.info("=" * 60)
        sys.exit(1)

    try:
        run(
            config,
            check_only=args.check,
            create_db=args.create_db,
            allow_pending=args.allow_pending,
        )
        logger.info("SUCCESS")
        logger.info("=" * 60)
    except SystemExit:
        # run() exits non-zero in --check mode when migrations are pending.
        # Emit the closing separator here so this path is symmetric with the
        # opening separator and the other exit paths, then re-raise unchanged
        # to preserve the exit code.
        logger.info("=" * 60)
        raise
    except KeyError as e:
        logger.error(f"Missing required config field: {e}")
        logger.info("=" * 60)
        sys.exit(1)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        logger.error(f"Error: {e}")
        logger.info("=" * 60)
        sys.exit(1)
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        logger.info("=" * 60)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
