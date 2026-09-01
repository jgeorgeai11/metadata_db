"""load_catalog_data.py — apply the YAML corpus to Postgres.

Entry point for the metadata_db loader. Reads the venue-free YAML corpus
under `data_catalog/` (`data_catalog/systems.yaml` plus `data_catalog/sources/{label}/...`),
validates per the loader contract in
`MAINTAINING.md`, and applies the diff against
Postgres in a single transaction with `*_hstry` writes.

Modes:
  - default        — full run: read, validate, diff, write.
  - --dry-run      — read, validate, diff; log summary; no writes.
  - --reset-hstry  — TRUNCATE every `*_hstry` table inside the load
                    transaction. Gated by env var
                    `METADATA_DB_ALLOW_RESET_HSTRY=1` because it is a
                    bootstrap-only operation.
  - --allow-mass-delete — bypass the mass-delete guard (which otherwise
                    refuses a diff deleting more than the configured
                    share of current DB rows — see
                    `corpus_diff.check_mass_delete`). Gated by env var
                    `METADATA_DB_ALLOW_MASS_DELETE=1` because it
                    authorizes bulk deletion.

Connection host/port/user/password come from `.env`
(`POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` /
`POSTGRES_PASSWORD`); the target database name lives in the TOML
config.
"""

import argparse
import hashlib
import os
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg2

# The vendored logging package (code/lib/logconfig) is resolved from
# this file's own location, so imports work from any working directory
# (not just the repo root) and CI never depends on untracked .claude/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import setup_logging, get_logger
from db_io import (
    apply_diff,
    connection_kwargs,
    read_db_state,
    resolve_commit_sha,
)
from corpus_diff import (
    DEFAULT_MASS_DELETE_FRACTION,
    DEFAULT_MASS_DELETE_MIN_COUNT,
    check_mass_delete,
    compute_diff,
)
from sql_parsing import compute_target_tables_referenced
from yaml_discovery import discover_yaml_files
from corpus_validation import ValidationError, validate_corpus, validate_update_reason
from corpus_assembly import assemble_corpus

logger = get_logger(__name__)


RESET_HSTRY_ENV_VAR = "METADATA_DB_ALLOW_RESET_HSTRY"

MASS_DELETE_ENV_VAR = "METADATA_DB_ALLOW_MASS_DELETE"

# Fixed, arbitrary key namespacing the loader's transaction-scoped
# advisory lock. Serializes concurrent loader sessions at the database so
# two runs cannot read-then-write against each other's stale state — a
# hazard the external merge-serialization gate does not cover for manual
# runs. Any constant works as long as it is stable across runs.
LOADER_LOCK_KEY = 8410327


class LoadInProgressError(RuntimeError):
    """Raised when another loader run already holds the advisory lock."""


def _schema_lock_key(database: str, schema: str) -> int:
    """Derive a stable int4 second key for the advisory lock from the target.

    The advisory lock is two-key: a fixed first key (`LOADER_LOCK_KEY`, for
    observability in `pg_locks`) plus this per-target second key, so loads
    against different (database, schema) pairs do not spuriously exclude each
    other while two runs against the same target still serialize. Python's
    builtin `hash` is per-process randomized for strings, so a deterministic
    SHA-256-derived value is used instead. Four bytes interpreted as a signed
    32-bit int fit `pg_try_advisory_xact_lock`'s int4 second argument.

    Args:
        database: Target database name.
        schema: Target Postgres schema.

    Returns:
        A stable signed 32-bit integer for this (database, schema) pair.
    """
    digest = hashlib.sha256(f"{database}\x00{schema}".encode()).digest()
    return int.from_bytes(digest[:4], "big", signed=True)


def _validate_mass_delete_knobs(
    fraction: Any, min_count: Any
) -> tuple[float, int]:
    """Validate the mass-delete guard knobs from config.

    A TOML string or out-of-range value would otherwise escape as an
    unhandled `TypeError` deep in the guard, or silently disable / over-fire
    it. Validated here at config load so a bad value fails fast with a clear
    config error (caught by `main`'s ValueError arm).

    Args:
        fraction: Candidate `mass_delete_fraction` — a float in [0, 1].
        min_count: Candidate `mass_delete_min_count` — a non-negative int.

    Returns:
        The validated `(fraction, min_count)` pair.

    Raises:
        ValueError: If either knob is the wrong type or out of range.
    """
    # bool is an int subclass; reject it explicitly so `true`/`false` in TOML
    # cannot masquerade as 1/0.
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
        raise ValueError(
            f"mass_delete_fraction must be a number in [0, 1], got "
            f"{type(fraction).__name__} {fraction!r}"
        )
    if not 0.0 <= float(fraction) <= 1.0:
        raise ValueError(
            f"mass_delete_fraction must be in [0, 1], got {fraction!r}"
        )
    if isinstance(min_count, bool) or not isinstance(min_count, int):
        raise ValueError(
            f"mass_delete_min_count must be a non-negative integer, got "
            f"{type(min_count).__name__} {min_count!r}"
        )
    if min_count < 0:
        raise ValueError(
            f"mass_delete_min_count must be non-negative, got {min_count!r}"
        )
    return float(fraction), min_count


def run(
    config: dict[str, Any],
    dry_run: bool,
    reset_hstry: bool,
    allow_mass_delete: bool = False,
) -> None:
    """Orchestrate one loader invocation.

    Steps:
      1. Discover YAML files under `data_catalog/` (`data_catalog/systems.yaml` plus
         the `data_catalog/sources/` tree) and assemble corpus, expanding each
         data source's sparse `deployments.yaml` into table-grain
         `deployment_tables` rows.
         Discovery and assembly issues (misplaced files, unparsable
         YAML, bad rows, a data source deploying nowhere) are aggregated
         corpus-wide and reported together via `AssemblyError` (a
         `ValidationError` subclass).
      2. Validate corpus (FK/uniqueness/identifier syntax/SQL) — runs
         only on a cleanly assembled corpus, so wave-1 shape issues
         never cascade into spurious validation noise.
      3. Derive `column_mappings.target_tables_referenced` from the
         memoized expression trees returned by validation.
      4. Open the connection and acquire a fail-fast transaction-scoped
         advisory lock scoped to the (database, schema) target (raise
         `LoadInProgressError` if another run holds it), resolve the commit
         SHA (in both modes, for dry-run parity), then read DB state.
      5. Compute diff.
      6. Validate the update_reason discipline against the diff
         (CONTRIBUTING.md wave 3).
      7. Run the mass-delete guard (`corpus_diff.check_mass_delete`,
         CONTRIBUTING.md wave 3) — in dry-run too, so pre-merge CI
         surfaces an accidental mass deletion — unless
         `allow_mass_delete` bypasses it. The update_reason check runs
         first (matching CONTRIBUTING.md's wave-3 ordering) so a tripped
         guard never hides the update_reason report.
      8. If `dry_run`: log summary and return.
      9. Else: open transaction, apply diff (with `_hstry` writes),
         commit. Failures roll back and re-raise.

    Args:
        config: Parsed TOML config (`data_root`, `database`, `schema`,
            plus optional `mass_delete_fraction` /
            `mass_delete_min_count` guard knobs).
        dry_run: If True, perform steps 1-8 only; no DB writes.
        reset_hstry: If True, TRUNCATE every `*_hstry` table inside the
            load transaction. No writes happen in dry-run mode, but the
            env-var gate is enforced in both modes so a dry-run previews
            exactly what a real run would do.
        allow_mass_delete: If True, skip the mass-delete guard (a
            deliberate bulk removal). Requires the env-var guard in both
            modes so a dry-run previews exactly what a real run would do.

    Raises:
        KeyError: If a required config field is missing.
        TypeError: If `data_root` holds a TOML value that is not a string
            or path-like (e.g. a bare number).
        FileNotFoundError: If `data_catalog/sources/` is missing.
        ValidationError: If corpus assembly or validation fails. Both
            stages aggregate their issues (path classification, YAML
            parse, and row-shape errors surface as
            `corpus_assembly.AssemblyError`, a subclass).
        psycopg2.Error: On DB failure.
        LoadInProgressError: If another loader run already holds the
            advisory lock (subclass of RuntimeError).
        MassDeleteError: If the diff would delete more than the
            configured share of current DB rows (subclass of
            RuntimeError).
        RuntimeError: If env vars are missing, or `--reset-hstry` /
            `--allow-mass-delete` is requested without its env-var
            guard.
        ValueError: If a mass-delete guard knob (`mass_delete_fraction` /
            `mass_delete_min_count`) is the wrong type or out of range.
    """
    # Gated in dry-run too (mirroring --allow-mass-delete): the pre-merge
    # dry-run must fail exactly as the real run would.
    if reset_hstry:
        if os.environ.get(RESET_HSTRY_ENV_VAR) != "1":
            raise RuntimeError(
                f"--reset-hstry requires env var "
                f"{RESET_HSTRY_ENV_VAR}=1 to confirm intent. Refusing."
            )
    if allow_mass_delete:
        if os.environ.get(MASS_DELETE_ENV_VAR) != "1":
            raise RuntimeError(
                f"--allow-mass-delete requires env var "
                f"{MASS_DELETE_ENV_VAR}=1 to confirm intent. Refusing."
            )

    data_root = Path(config["data_root"])
    database = config["database"]
    schema = config["schema"]
    mass_delete_fraction, mass_delete_min_count = _validate_mass_delete_knobs(
        config.get("mass_delete_fraction", DEFAULT_MASS_DELETE_FRACTION),
        config.get("mass_delete_min_count", DEFAULT_MASS_DELETE_MIN_COUNT),
    )

    # Discovery issues join the assembly wave: assemble_corpus raises one
    # AssemblyError carrying both once the whole corpus has been walked.
    files, discovery_issues = discover_yaml_files(data_root)
    corpus = assemble_corpus(files, discovery_issues)
    memo = validate_corpus(corpus)

    # Derive target_tables_referenced from memoized parse trees. Ids are
    # venue-free, so every referenced table counts (no target_system to
    # filter by).
    for key, cm in list(corpus.column_mappings.items()):
        ttr = compute_target_tables_referenced(memo.get(key))
        corpus.column_mappings[key] = replace(
            cm, target_tables_referenced=tuple(ttr)
        )

    conn_kwargs = connection_kwargs(database, schema)
    logger.debug(f"Connecting to database {database} (schema {schema})")
    conn = psycopg2.connect(**conn_kwargs)
    try:
        # Serialize loader runs at the DB. Fail fast rather than block:
        # a second concurrent run should exit clearly, not hang. The lock
        # is transaction-scoped — released on commit/rollback and on the
        # connection close in the `finally` below (including dry-run).
        # Two-key: the fixed LOADER_LOCK_KEY (observable in pg_locks) plus a
        # per-(database, schema) key so loads against different schemas in
        # one database do not spuriously exclude each other.
        schema_lock_key = _schema_lock_key(database, schema)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s)",
                (LOADER_LOCK_KEY, schema_lock_key),
            )
            if not cur.fetchone()[0]:
                raise LoadInProgressError(
                    "another metadata_db load is already in progress for "
                    f"{database}.{schema} (advisory lock held); refusing to "
                    "run concurrently"
                )

        # Resolve the commit SHA before the diff in BOTH modes so a run that
        # would fail SHA resolution (git absent, non-checkout deploy) fails
        # its dry-run too — preserving dry-run/real-run parity — and so the
        # resolution is done once and passed into apply_diff.
        commit_sha = resolve_commit_sha()

        db_state = read_db_state(conn)
        diff = compute_diff(corpus, db_state)
        # update_reason discipline before the mass-delete guard
        # (CONTRIBUTING.md's wave-3 ordering): the accumulated
        # update_reason report must surface even when the mass-delete
        # guard would also trip.
        validate_update_reason(diff)
        if allow_mass_delete:
            logger.warning(
                f"Mass-delete guard bypassed (--allow-mass-delete): "
                f"{len(diff.deletes)} delete(s) will be applied unchecked"
            )
        else:
            check_mass_delete(
                diff,
                db_state,
                fraction=mass_delete_fraction,
                min_count=mass_delete_min_count,
            )

        if dry_run:
            logger.info(f"DRY RUN — {diff.summary()} (no writes)")
            return

        apply_diff(conn, diff, commit_sha, reset_hstry=reset_hstry)
    finally:
        conn.close()


def main() -> None:
    """Parse args, load config, set up logging, dispatch to `run`."""
    parser = argparse.ArgumentParser(
        description="Apply the metadata_db YAML corpus to Postgres.",
    )
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to TOML configuration file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Read-only: discover/validate/diff but do not write. "
            "Used by pre-merge CI."
        ),
    )
    parser.add_argument(
        "--reset-hstry",
        action="store_true",
        help=(
            f"TRUNCATE every *_hstry table inside the load transaction "
            f"(bootstrap only; requires env var "
            f"{RESET_HSTRY_ENV_VAR}=1)."
        ),
    )
    parser.add_argument(
        "--allow-mass-delete",
        action="store_true",
        help=(
            f"Bypass the mass-delete guard for a deliberate bulk removal "
            f"(requires env var {MASS_DELETE_ENV_VAR}=1)."
        ),
    )
    args = parser.parse_args()

    setup_logging(log_dir="logs/load_catalog_data")
    logger.info("=" * 60)

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.error(f"Failed to read config file: {e}")
        sys.exit(1)

    try:
        run(
            config,
            dry_run=args.dry_run,
            reset_hstry=args.reset_hstry,
            allow_mass_delete=args.allow_mass_delete,
        )
        logger.info("SUCCESS")
        logger.info("=" * 60)
    except KeyError as e:
        logger.error(f"Missing required config field: {e}")
        logger.info("=" * 60)
        sys.exit(1)
    except ValidationError as e:
        # ValidationError (and its assembly-stage subclass AssemblyError)
        # carries an aggregated issue list, and its own summary names the
        # failing stage — log that plus every issue so authors see every
        # problem per run without an assembly failure ever reading as a
        # validation one.
        logger.error(e.summary)
        for issue in e.issues:
            logger.error(f"  - {issue}")
        logger.info("=" * 60)
        sys.exit(1)
    except (FileNotFoundError, TypeError, ValueError, RuntimeError) as e:
        logger.error(f"{e}")
        logger.info("=" * 60)
        sys.exit(1)
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        logger.info("=" * 60)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
