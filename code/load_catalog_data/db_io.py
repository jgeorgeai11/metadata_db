"""db_io.py — Postgres read / write helpers for the loader.

`read_db_state` issues one SELECT per main table and builds a `DbState`
mirroring the corpus shape. `apply_diff` performs the entire load
inside one transaction: writes old rows to `*_hstry` for to-be-updated
and to-be-deleted rows, then applies the inserts, updates, and deletes
to the main tables. If `reset_hstry` is True, all 9 `*_hstry` tables
are truncated inside the same transaction first (atomic bootstrap
reset).

`column_mappings.target_tables_referenced` is a Postgres `ltree[]`;
psycopg2 does not recognize that type, so writes cast `%s::ltree[]`
(psycopg2 adapts a Python list to `text[]`) and reads cast
`target_tables_referenced::text[]` so the driver returns a Python list.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import sql

# The vendored packages under code/lib (logconfig, pgconn) are resolved
# from this file's own location, so imports work from any working
# directory (not just the repo root) and CI never depends on untracked
# .claude/. `connection_kwargs` is re-exported here: the orchestrator
# and the integration suite import it from db_io, the loader's one
# database-boundary module.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import get_logger
from pgconn import connection_kwargs  # noqa: F401 (re-export)
from corpus_diff import Diff, RowChange
from data_model import (
    ColumnMappingRow,
    ColumnRow,
    ConceptRow,
    DataSourceRow,
    DbState,
    DeploymentRow,
    SchemaRow,
    SystemRow,
    TableRelationshipRow,
    TableRow,
    empty_db_state,
    pk,
)

logger = get_logger(__name__)


# Repository root, derived from this module's own location
# (code/load_catalog_data/db_io.py -> two parents up). `resolve_commit_sha`
# pins `git rev-parse HEAD` here so a loader run from any working
# directory records THIS repo's HEAD in load_audit.commit_sha — not
# whatever repository the process happens to be started from.
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Ceiling for the `git rev-parse` subprocess, seconds. Resolving HEAD is
# instantaneous on a healthy checkout; a hang (e.g. a stuck filesystem)
# must not stall the loader indefinitely.
_GIT_TIMEOUT_SECONDS = 30


# Name of the deployment_tables physical-address UNIQUE constraint.
# Declared `deferrable initially immediate` in
# code/apply_ddl/ddl_catalog/0001_initial_schema.sql; apply_diff defers it for the
# load transaction so a legal end-state that passes through a transient
# mid-transaction address collision (an address swap or chain between
# updated rows applied in arbitrary order) settles at commit instead of
# aborting. This literal MUST match the constraint name in the DDL.
DEPLOYMENT_TABLES_PHYSICAL_ADDRESS_CONSTRAINT = (
    "deployment_tables_physical_address_key"
)

# Name of the columns.ref_table_id foreign-key constraint. Declared
# `deferrable initially immediate` in
# code/apply_ddl/ddl_catalog/0001_initial_schema.sql; apply_diff defers it
# for the load transaction because the deletes->updates->inserts phase
# order can violate it mid-transaction on a legal end state — an in-place
# columns UPDATE may point at a `tables` row INSERTed later in the same
# transaction (linking a column to a same-MR ref table). Deferred, the FK
# is enforced once at COMMIT instead. This literal MUST match the
# constraint name in the DDL.
COLUMNS_REF_TABLE_ID_FK_CONSTRAINT = "columns_ref_table_id_fkey"

# The statements apply_diff issues to defer those constraints. Built as
# plain strings (not psycopg2 sql.Composed) because each constraint name
# is a fixed, trusted module constant — there is no user input to
# parameterize, and a plain string keeps the emitted SQL directly
# inspectable.
_SET_CONSTRAINTS_DEFER_DEPLOYMENT_TABLES = (
    f"SET CONSTRAINTS {DEPLOYMENT_TABLES_PHYSICAL_ADDRESS_CONSTRAINT} "
    f"DEFERRED"
)
_SET_CONSTRAINTS_DEFER_COLUMNS_REF_TABLE_ID = (
    f"SET CONSTRAINTS {COLUMNS_REF_TABLE_ID_FK_CONSTRAINT} DEFERRED"
)


def _warn_if_dirty_working_tree() -> None:
    """Log a WARNING if the local git working tree has uncommitted changes.

    Best-effort lineage caveat for local / manual runs: the fallback path of
    `resolve_commit_sha` records HEAD, but a dirty tree means the loaded YAML
    may differ from what that commit contains, so `load_audit.commit_sha`
    would not fully describe the loaded content. This warns (never fails) —
    a manual run against a scratch DB with uncommitted edits is a legitimate
    workflow, and the warning plus the recorded SHA make the caveat
    auditable. Any failure to run `git status` is swallowed: the check is
    diagnostic, not a gate.
    """
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    if status.returncode == 0 and status.stdout.strip():
        logger.warning(
            "git working tree is dirty (uncommitted changes); the recorded "
            "load_audit.commit_sha may not describe the loaded corpus content"
        )


def resolve_commit_sha() -> str:
    """Resolve the git commit the loaded corpus reflects, for load_audit.

    Prefers the CI-provided `$GITHUB_SHA` (on the post-merge `push`
    event this is the just-landed merge commit itself — exactly the
    provenance `load_audit` should record); otherwise falls back to
    `git rev-parse HEAD` for local / manual runs. The subprocess is
    pinned to this repository's root (`_REPO_ROOT`, derived from the
    module's own location — never the process cwd) so a loader started
    from another directory cannot record a different repo's HEAD, which
    would corrupt drift detection and lineage. A `_GIT_TIMEOUT_SECONDS`
    ceiling keeps a wedged git from stalling the loader. On the local
    fallback path a dirty working tree is surfaced as a WARNING (not an
    error) — see `_warn_if_dirty_working_tree`.

    Returns:
        The commit SHA string.

    Raises:
        RuntimeError: If `$GITHUB_SHA` is unset and `git rev-parse
            HEAD` fails, hangs past the timeout, or git is not
            installed (e.g. not a git checkout).
    """
    ci_sha = os.environ.get("GITHUB_SHA", "").strip()
    if ci_sha:
        return ci_sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as e:
        logger.error("git executable not found while resolving commit SHA")
        raise RuntimeError(
            "could not resolve commit SHA for load_audit: GITHUB_SHA is "
            "unset/blank and the git executable was not found"
        ) from e
    except subprocess.TimeoutExpired as e:
        logger.error(
            f"`git rev-parse HEAD` timed out after "
            f"{_GIT_TIMEOUT_SECONDS}s in {_REPO_ROOT}"
        )
        raise RuntimeError(
            f"could not resolve commit SHA for load_audit: GITHUB_SHA "
            f"is unset/blank and `git rev-parse HEAD` timed out after "
            f"{_GIT_TIMEOUT_SECONDS}s"
        ) from e
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        logger.error("Failed to resolve commit SHA for load_audit")
        raise RuntimeError(
            "could not resolve commit SHA for load_audit: GITHUB_SHA is "
            f"unset/blank and `git rev-parse HEAD` failed: "
            f"{result.stderr.strip() or 'empty output'}"
        )
    # Local fallback only (the CI path returned above): surface a dirty
    # working tree as a lineage caveat without blocking the run.
    _warn_if_dirty_working_tree()
    return sha


# ---------------------------------------------------------------------------
# read_db_state
# ---------------------------------------------------------------------------


_SELECT_SYSTEMS = (
    "SELECT system, description, notes, update_reason FROM systems"
)
_SELECT_DATA_SOURCES = (
    "SELECT data_source_id, owner, description, notes, "
    "update_reason FROM data_sources"
)
_SELECT_SCHEMAS = (
    "SELECT schema_id, data_source_id, schema_name, description, notes, "
    "update_reason FROM schemas"
)
_SELECT_TABLES = (
    "SELECT table_id, schema_id, table_name, description, notes, "
    "update_reason FROM tables"
)
# Column order matches ColumnRow field order (rows built positionally):
# ref_table_id sits LAST — it is a defaulted trailing field on the
# dataclass (see data_model.ColumnRow), deviating from the DDL order.
_SELECT_COLUMNS = (
    "SELECT column_id, table_id, column_name, data_type, is_nullable, "
    "is_primary_key, description, notes, update_reason, ref_table_id "
    "FROM columns"
)
# Column order matches DeploymentRow field order (rows built positionally).
_SELECT_DEPLOYMENT_TABLES = (
    "SELECT table_id, system, data_source_id, physical_database_name, "
    "physical_schema_name, physical_table_name "
    "FROM deployment_tables"
)
_SELECT_TABLE_RELATIONSHIPS = (
    "SELECT table_a_id, table_b_id, relationship_name, "
    "join_condition, cardinality, use_when, notes, validated, "
    "update_reason, validated_ts FROM table_relationships"
)
# target_tables_referenced is cast ltree[] -> text[] so psycopg2 parses
# it into a Python list (it does not recognize the ltree[] type and would
# otherwise return the raw '{...}' array string, silently breaking the
# tuple() conversion and diff idempotency).
_SELECT_COLUMN_MAPPINGS = (
    "SELECT source_column_id, mapping_name, "
    "target_tables_referenced::text[], target_expression, use_when, notes, "
    "validated, update_reason, validated_ts FROM column_mappings"
)
# Column order must match ConceptRow field order (rows are built
# positionally in read_db_state). related_object_ids is cast
# ltree[] -> text[] so psycopg2 parses it into a Python list, mirroring
# target_tables_referenced above.
_SELECT_CONCEPTS = (
    "SELECT concept_id, label, definition, notes, "
    "related_object_ids::text[], update_reason FROM concepts"
)


def read_db_state(conn: psycopg2.extensions.connection) -> DbState:
    """Read current rows from all 9 main tables into a `DbState`.

    Args:
        conn: Active psycopg2 connection.

    Returns:
        A populated `DbState`.
    """
    state = empty_db_state()
    with conn.cursor() as cur:
        cur.execute(_SELECT_SYSTEMS)
        for row in cur.fetchall():
            r = SystemRow(*row)
            state.systems[pk(r, "systems")] = r

        cur.execute(_SELECT_DATA_SOURCES)
        for row in cur.fetchall():
            r2 = DataSourceRow(*row)
            state.data_sources[pk(r2, "data_sources")] = r2

        cur.execute(_SELECT_SCHEMAS)
        for row in cur.fetchall():
            r3 = SchemaRow(*row)
            state.schemas[pk(r3, "schemas")] = r3

        cur.execute(_SELECT_TABLES)
        for row in cur.fetchall():
            r4 = TableRow(*row)
            state.tables[pk(r4, "tables")] = r4

        cur.execute(_SELECT_COLUMNS)
        for row in cur.fetchall():
            r5 = ColumnRow(*row)
            state.columns[pk(r5, "columns")] = r5

        cur.execute(_SELECT_DEPLOYMENT_TABLES)
        for row in cur.fetchall():
            rdep = DeploymentRow(*row)
            state.deployment_tables[pk(rdep, "deployment_tables")] = rdep

        cur.execute(_SELECT_TABLE_RELATIONSHIPS)
        for row in cur.fetchall():
            r6 = TableRelationshipRow(*row)
            state.table_relationships[pk(r6, "table_relationships")] = r6

        cur.execute(_SELECT_COLUMN_MAPPINGS)
        for row in cur.fetchall():
            (
                scid,
                mn,
                ttr,
                te,
                uw,
                notes,
                validated,
                ur,
                vts,
            ) = row
            # text[]/ltree[] decodes as a Python list; coerce to tuple for hash.
            r7 = ColumnMappingRow(
                source_column_id=scid,
                mapping_name=mn,
                target_tables_referenced=tuple(ttr or ()),
                target_expression=te,
                use_when=uw,
                notes=notes,
                validated=validated,
                update_reason=ur,
                validated_ts=vts,
            )
            state.column_mappings[pk(r7, "column_mappings")] = r7

        cur.execute(_SELECT_CONCEPTS)
        for row in cur.fetchall():
            cid, label, definition, notes, related, ur = row
            # text[]/ltree[] decodes as a Python list (NULL as None);
            # coerce to tuple for hash — `x or ()` maps both NULL and {}
            # to (), keeping the read symmetric with the write (an empty
            # tuple writes as an empty array) so re-loads diff as no-ops.
            r8 = ConceptRow(
                concept_id=cid,
                label=label,
                definition=definition,
                notes=notes,
                related_object_ids=tuple(related or ()),
                update_reason=ur,
            )
            state.concepts[pk(r8, "concepts")] = r8

    logger.info(
        "Read DB state: "
        f"{len(state.systems)} systems, "
        f"{len(state.data_sources)} data_sources, "
        f"{len(state.schemas)} schemas, "
        f"{len(state.tables)} tables, "
        f"{len(state.columns)} columns, "
        f"{len(state.deployment_tables)} deployment_tables, "
        f"{len(state.table_relationships)} table_relationships, "
        f"{len(state.column_mappings)} column_mappings, "
        f"{len(state.concepts)} concepts"
    )
    return state


# ---------------------------------------------------------------------------
# apply_diff
# ---------------------------------------------------------------------------


# Insert / update / delete SQL templates and the column-order of each
# row dataclass for parameter binding.

# Per table: four SQL dicts (_INSERT_SQL / _UPDATE_SQL / _DELETE_SQL /
# _HSTRY_INSERT_SQL, each table -> statement) plus the param builders
# (_insert_params / _update_params / _pk_params) that bind each row
# dataclass in its statement's column order.
#
# All SQL uses %s placeholders; psycopg2 handles type adaptation
# (booleans, lists -> arrays, None -> NULL).

_INSERT_SYSTEMS = (
    "INSERT INTO systems (system, description, notes, update_reason, "
    "insert_ts, update_ts) VALUES (%s, %s, %s, %s, now(), now())"
)
_INSERT_DATA_SOURCES = (
    "INSERT INTO data_sources (data_source_id, owner, "
    "description, notes, update_reason, insert_ts, update_ts) "
    "VALUES (%s, %s, %s, %s, %s, now(), now())"
)
_INSERT_SCHEMAS = (
    "INSERT INTO schemas (schema_id, data_source_id, schema_name, "
    "description, notes, update_reason, insert_ts, update_ts) "
    "VALUES (%s, %s, %s, %s, %s, %s, now(), now())"
)
_INSERT_TABLES = (
    "INSERT INTO tables (table_id, schema_id, table_name, description, "
    "notes, update_reason, insert_ts, update_ts) "
    "VALUES (%s, %s, %s, %s, %s, %s, now(), now())"
)
_INSERT_COLUMNS = (
    "INSERT INTO columns (column_id, table_id, column_name, data_type, "
    "is_nullable, is_primary_key, description, notes, update_reason, "
    "ref_table_id, insert_ts, update_ts) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())"
)
_INSERT_DEPLOYMENT_TABLES = (
    "INSERT INTO deployment_tables (table_id, system, data_source_id, "
    "physical_database_name, physical_schema_name, physical_table_name, "
    "insert_ts, update_ts) "
    "VALUES (%s, %s, %s, %s, %s, %s, now(), now())"
)
_INSERT_TABLE_RELATIONSHIPS = (
    "INSERT INTO table_relationships (table_a_id, table_b_id, "
    "relationship_name, join_condition, cardinality, use_when, "
    "notes, validated, update_reason, validated_ts, insert_ts, update_ts) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
    "CASE WHEN %s THEN now() ELSE NULL END, now(), now())"
)
_INSERT_COLUMN_MAPPINGS = (
    "INSERT INTO column_mappings (source_column_id, "
    "mapping_name, target_tables_referenced, target_expression, use_when, "
    "notes, validated, update_reason, validated_ts, insert_ts, update_ts) "
    "VALUES (%s, %s, %s::ltree[], %s, %s, %s, %s, %s, "
    "CASE WHEN %s THEN now() ELSE NULL END, now(), now())"
)
# related_object_ids binds a Python list with a %s::ltree[] cast
# (psycopg2 adapts the list to text[]), mirroring column_mappings'
# target_tables_referenced; an empty tuple writes as an empty array.
_INSERT_CONCEPTS = (
    "INSERT INTO concepts (concept_id, label, definition, notes, "
    "related_object_ids, update_reason, insert_ts, update_ts) "
    "VALUES (%s, %s, %s, %s, %s::ltree[], %s, now(), now())"
)

_UPDATE_SYSTEMS = (
    "UPDATE systems SET description=%s, notes=%s, update_reason=%s, "
    "update_ts=now() WHERE system=%s"
)
_UPDATE_DATA_SOURCES = (
    "UPDATE data_sources SET owner=%s, description=%s, "
    "notes=%s, update_reason=%s, update_ts=now() WHERE data_source_id=%s"
)
_UPDATE_SCHEMAS = (
    "UPDATE schemas SET data_source_id=%s, schema_name=%s, description=%s, "
    "notes=%s, update_reason=%s, update_ts=now() WHERE schema_id=%s"
)
_UPDATE_TABLES = (
    "UPDATE tables SET schema_id=%s, table_name=%s, description=%s, "
    "notes=%s, update_reason=%s, update_ts=now() WHERE table_id=%s"
)
_UPDATE_COLUMNS = (
    "UPDATE columns SET table_id=%s, column_name=%s, data_type=%s, "
    "is_nullable=%s, is_primary_key=%s, description=%s, notes=%s, "
    "update_reason=%s, ref_table_id=%s, update_ts=now() WHERE column_id=%s"
)
# Composite-PK UPDATE / DELETE statements MUST include every PK part in
# WHERE — otherwise an update for one relationship_name would clobber
# rows for sibling relationship_names sharing (a, b). deployment_tables'
# PK is (table_id, system); its physical-name columns and data_source_id
# are the mutable content.
_UPDATE_DEPLOYMENT_TABLES = (
    "UPDATE deployment_tables SET data_source_id=%s, "
    "physical_database_name=%s, physical_schema_name=%s, "
    "physical_table_name=%s, update_ts=now() "
    "WHERE table_id=%s AND system=%s"
)
# validated_ts=CASE WHEN <stamp_now> THEN now() ELSE <else_value> END:
# on a validated false->true transition stamp_now is true (-> now());
# otherwise the else value is NULL (row is/became unvalidated) or the
# preserved prior validated_ts (row stayed validated). See _update_params.
_UPDATE_TABLE_RELATIONSHIPS = (
    "UPDATE table_relationships SET join_condition=%s, "
    "cardinality=%s, use_when=%s, notes=%s, validated=%s, update_reason=%s, "
    "validated_ts=CASE WHEN %s THEN now() ELSE %s END, update_ts=now() "
    "WHERE table_a_id=%s AND table_b_id=%s AND relationship_name=%s"
)
_UPDATE_COLUMN_MAPPINGS = (
    "UPDATE column_mappings SET target_tables_referenced=%s::ltree[], "
    "target_expression=%s, use_when=%s, notes=%s, validated=%s, "
    "update_reason=%s, "
    "validated_ts=CASE WHEN %s THEN now() ELSE %s END, update_ts=now() "
    "WHERE source_column_id=%s AND mapping_name=%s"
)
# concept_id (PK) is excluded from SET, like every other main table's
# UPDATE — a PK change is a delete+insert, never an in-place update.
_UPDATE_CONCEPTS = (
    "UPDATE concepts SET label=%s, definition=%s, notes=%s, "
    "related_object_ids=%s::ltree[], update_reason=%s, update_ts=now() "
    "WHERE concept_id=%s"
)

# One row per successful real loader run (see apply_diff).
_INSERT_LOAD_AUDIT = (
    "INSERT INTO load_audit (commit_sha, inserts, updates, deletes, "
    "reset_hstry) VALUES (%s, %s, %s, %s, %s)"
)

_DELETE_SYSTEMS = "DELETE FROM systems WHERE system=%s"
_DELETE_DATA_SOURCES = "DELETE FROM data_sources WHERE data_source_id=%s"
_DELETE_SCHEMAS = "DELETE FROM schemas WHERE schema_id=%s"
_DELETE_TABLES = "DELETE FROM tables WHERE table_id=%s"
_DELETE_COLUMNS = "DELETE FROM columns WHERE column_id=%s"
_DELETE_DEPLOYMENT_TABLES = (
    "DELETE FROM deployment_tables WHERE table_id=%s AND system=%s"
)
_DELETE_TABLE_RELATIONSHIPS = (
    "DELETE FROM table_relationships "
    "WHERE table_a_id=%s AND table_b_id=%s AND relationship_name=%s"
)
_DELETE_COLUMN_MAPPINGS = (
    "DELETE FROM column_mappings "
    "WHERE source_column_id=%s AND mapping_name=%s"
)
_DELETE_CONCEPTS = "DELETE FROM concepts WHERE concept_id=%s"

# `*_hstry` insert templates — mirror the main-table shape plus end_ts.
_HSTRY_INSERT_SYSTEMS = (
    "INSERT INTO systems_hstry (system, description, notes, update_reason, "
    "insert_ts, update_ts, end_ts) "
    "SELECT system, description, notes, update_reason, insert_ts, "
    "update_ts, now() FROM systems WHERE system=%s"
)
_HSTRY_INSERT_DATA_SOURCES = (
    "INSERT INTO data_sources_hstry (data_source_id, owner, "
    "description, notes, update_reason, insert_ts, update_ts, end_ts) "
    "SELECT data_source_id, owner, description, notes, "
    "update_reason, insert_ts, update_ts, now() FROM data_sources "
    "WHERE data_source_id=%s"
)
_HSTRY_INSERT_SCHEMAS = (
    "INSERT INTO schemas_hstry (schema_id, data_source_id, schema_name, "
    "description, notes, update_reason, insert_ts, update_ts, end_ts) "
    "SELECT schema_id, data_source_id, schema_name, description, notes, "
    "update_reason, insert_ts, update_ts, now() FROM schemas "
    "WHERE schema_id=%s"
)
_HSTRY_INSERT_TABLES = (
    "INSERT INTO tables_hstry (table_id, schema_id, table_name, "
    "description, notes, update_reason, insert_ts, update_ts, end_ts) "
    "SELECT table_id, schema_id, table_name, description, notes, "
    "update_reason, insert_ts, update_ts, now() FROM tables "
    "WHERE table_id=%s"
)
_HSTRY_INSERT_COLUMNS = (
    "INSERT INTO columns_hstry (column_id, table_id, column_name, "
    "data_type, is_nullable, is_primary_key, ref_table_id, description, "
    "notes, update_reason, insert_ts, update_ts, end_ts) "
    "SELECT column_id, table_id, column_name, data_type, is_nullable, "
    "is_primary_key, ref_table_id, description, notes, update_reason, "
    "insert_ts, update_ts, now() "
    "FROM columns WHERE column_id=%s"
)
_HSTRY_INSERT_DEPLOYMENT_TABLES = (
    "INSERT INTO deployment_tables_hstry (table_id, system, "
    "data_source_id, physical_database_name, physical_schema_name, "
    "physical_table_name, insert_ts, update_ts, end_ts) "
    "SELECT table_id, system, data_source_id, physical_database_name, "
    "physical_schema_name, physical_table_name, "
    "insert_ts, update_ts, now() FROM deployment_tables "
    "WHERE table_id=%s AND system=%s"
)
_HSTRY_INSERT_TABLE_RELATIONSHIPS = (
    "INSERT INTO table_relationships_hstry (table_a_id, table_b_id, "
    "relationship_name, join_condition, cardinality, use_when, "
    "notes, validated, update_reason, validated_ts, insert_ts, update_ts, "
    "end_ts) "
    "SELECT table_a_id, table_b_id, relationship_name, "
    "join_condition, cardinality, use_when, notes, validated, update_reason, "
    "validated_ts, insert_ts, update_ts, now() FROM table_relationships "
    "WHERE table_a_id=%s AND table_b_id=%s AND relationship_name=%s"
)
_HSTRY_INSERT_COLUMN_MAPPINGS = (
    "INSERT INTO column_mappings_hstry (source_column_id, "
    "mapping_name, target_tables_referenced, target_expression, use_when, "
    "notes, validated, update_reason, validated_ts, insert_ts, update_ts, "
    "end_ts) "
    "SELECT source_column_id, mapping_name, "
    "target_tables_referenced, target_expression, use_when, notes, validated, "
    "update_reason, validated_ts, insert_ts, update_ts, now() "
    "FROM column_mappings "
    "WHERE source_column_id=%s AND mapping_name=%s"
)
_HSTRY_INSERT_CONCEPTS = (
    "INSERT INTO concepts_hstry (concept_id, label, definition, notes, "
    "related_object_ids, update_reason, insert_ts, update_ts, end_ts) "
    "SELECT concept_id, label, definition, notes, related_object_ids, "
    "update_reason, insert_ts, update_ts, now() FROM concepts "
    "WHERE concept_id=%s"
)

# History tables — used by --reset-hstry truncate.
_HSTRY_TABLES: tuple[str, ...] = (
    "systems_hstry",
    "data_sources_hstry",
    "schemas_hstry",
    "tables_hstry",
    "columns_hstry",
    "deployment_tables_hstry",
    "table_relationships_hstry",
    "column_mappings_hstry",
    "concepts_hstry",
)


def _insert_params(table: str, row: Any) -> tuple[Any, ...]:
    """Bound parameters for the INSERT statement of the given table.

    `update_reason` is bound as NULL for every authored table regardless of
    the row's value: a fresh insert never carries a reason (the row is new,
    there is nothing to explain the change of). This held implicitly because
    `validate_update_reason` runs first in the sole call path and rejects a
    reason-bearing insert; binding NULL here makes apply_diff self-defending
    against any future caller that skips that check (and keeps the DB's
    update_reason pairing CHECK satisfied). deployment_tables carries no
    update_reason column at all.
    """
    if table == "systems":
        return (row.system, row.description, row.notes, None)
    if table == "data_sources":
        return (
            row.data_source_id,
            row.owner,
            row.description,
            row.notes,
            None,
        )
    if table == "schemas":
        return (
            row.schema_id,
            row.data_source_id,
            row.schema_name,
            row.description,
            row.notes,
            None,
        )
    if table == "tables":
        return (
            row.table_id,
            row.schema_id,
            row.table_name,
            row.description,
            row.notes,
            None,
        )
    if table == "columns":
        # Trailing ref_table_id matches _INSERT_COLUMNS' column list
        # (update_reason stays at its established index for the
        # bind-NULL contract asserted in the unit tests).
        return (
            row.column_id,
            row.table_id,
            row.column_name,
            row.data_type,
            row.is_nullable,
            row.is_primary_key,
            row.description,
            row.notes,
            None,
            row.ref_table_id,
        )
    if table == "deployment_tables":
        return (
            row.table_id,
            row.system,
            row.data_source_id,
            row.physical_database_name,
            row.physical_schema_name,
            row.physical_table_name,
        )
    if table == "table_relationships":
        # Trailing row.validated is the CASE guard for validated_ts:
        # stamp now() on insert only when the row arrives validated.
        return (
            row.table_a_id,
            row.table_b_id,
            row.relationship_name,
            row.join_condition,
            row.cardinality,
            row.use_when,
            row.notes,
            row.validated,
            None,
            row.validated,
        )
    if table == "column_mappings":
        # Trailing row.validated is the CASE guard for validated_ts.
        return (
            row.source_column_id,
            row.mapping_name,
            list(row.target_tables_referenced),
            row.target_expression,
            row.use_when,
            row.notes,
            row.validated,
            None,
            row.validated,
        )
    if table == "concepts":
        # related_object_ids binds as a list for the %s::ltree[] cast
        # (mirroring column_mappings.target_tables_referenced above).
        return (
            row.concept_id,
            row.label,
            row.definition,
            row.notes,
            list(row.related_object_ids),
            None,
        )
    raise ValueError(f"Unknown table {table}")


def _validated_ts_update_args(row: Any, old: Any) -> tuple[bool, Any]:
    """Compute the (stamp_now, else_value) pair for a validated_ts UPDATE.

    Encodes the transition rules for the loader-managed validated_ts:
      - becomes/stays validated with no prior timestamp -> stamp now()
        (covers false->true, and a validated row whose validated_ts is
        somehow NULL — self-heals rather than tripping the CHECK)
      - stays validated with a prior timestamp -> preserve it
      - becomes/stays unvalidated -> NULL

    Args:
        row: The new (corpus) row.
        old: The current DB row being superseded.

    Returns:
        (stamp_now, else_value) bound to the SQL
        `CASE WHEN %s THEN now() ELSE %s END`.
    """
    if not row.validated:
        return False, None
    if old.validated and old.validated_ts is not None:
        return False, old.validated_ts
    return True, None


def _update_params(table: str, row: Any, old: Any, key: Any) -> tuple[Any, ...]:
    """Bound parameters for the UPDATE statement of the given table.

    `old` is the current DB row (used to compute the validated_ts
    transition for the two validated-bearing tables); it is ignored for
    the other seven tables.
    """
    if table == "systems":
        return (row.description, row.notes, row.update_reason, key)
    if table == "data_sources":
        return (
            row.owner,
            row.description,
            row.notes,
            row.update_reason,
            key,
        )
    if table == "schemas":
        return (
            row.data_source_id,
            row.schema_name,
            row.description,
            row.notes,
            row.update_reason,
            key,
        )
    if table == "tables":
        return (
            row.schema_id,
            row.table_name,
            row.description,
            row.notes,
            row.update_reason,
            key,
        )
    if table == "columns":
        return (
            row.table_id,
            row.column_name,
            row.data_type,
            row.is_nullable,
            row.is_primary_key,
            row.description,
            row.notes,
            row.update_reason,
            row.ref_table_id,
            key,
        )
    if table == "deployment_tables":
        # key is (table_id, system); the PK columns are excluded from SET.
        return (
            row.data_source_id,
            row.physical_database_name,
            row.physical_schema_name,
            row.physical_table_name,
            key[0],
            key[1],
        )
    if table == "table_relationships":
        # key is (table_a_id, table_b_id, relationship_name)
        stamp_now, else_value = _validated_ts_update_args(row, old)
        return (
            row.join_condition,
            row.cardinality,
            row.use_when,
            row.notes,
            row.validated,
            row.update_reason,
            stamp_now,
            else_value,
            key[0],
            key[1],
            key[2],
        )
    if table == "column_mappings":
        # key is (source_column_id, mapping_name)
        stamp_now, else_value = _validated_ts_update_args(row, old)
        return (
            list(row.target_tables_referenced),
            row.target_expression,
            row.use_when,
            row.notes,
            row.validated,
            row.update_reason,
            stamp_now,
            else_value,
            key[0],
            key[1],
        )
    if table == "concepts":
        # Simple single-PK table (like systems): no validated/validated_ts
        # CASE guard. key is the bare concept_id. related_object_ids binds
        # as a list for the %s::ltree[] cast.
        return (
            row.label,
            row.definition,
            row.notes,
            list(row.related_object_ids),
            row.update_reason,
            key,
        )
    raise ValueError(f"Unknown table {table}")


def _pk_params(table: str, key: Any) -> tuple[Any, ...]:
    """Bound parameters for DELETE / hstry-insert (PK-only WHERE)."""
    if table in (
        "systems",
        "data_sources",
        "schemas",
        "tables",
        "columns",
        "concepts",
    ):
        return (key,)
    if table in ("deployment_tables", "column_mappings"):
        return (key[0], key[1])
    if table == "table_relationships":
        return (key[0], key[1], key[2])
    raise ValueError(f"Unknown table {table}")


_INSERT_SQL: dict[str, str] = {
    "systems": _INSERT_SYSTEMS,
    "data_sources": _INSERT_DATA_SOURCES,
    "schemas": _INSERT_SCHEMAS,
    "tables": _INSERT_TABLES,
    "columns": _INSERT_COLUMNS,
    "deployment_tables": _INSERT_DEPLOYMENT_TABLES,
    "table_relationships": _INSERT_TABLE_RELATIONSHIPS,
    "column_mappings": _INSERT_COLUMN_MAPPINGS,
    "concepts": _INSERT_CONCEPTS,
}
_UPDATE_SQL: dict[str, str] = {
    "systems": _UPDATE_SYSTEMS,
    "data_sources": _UPDATE_DATA_SOURCES,
    "schemas": _UPDATE_SCHEMAS,
    "tables": _UPDATE_TABLES,
    "columns": _UPDATE_COLUMNS,
    "deployment_tables": _UPDATE_DEPLOYMENT_TABLES,
    "table_relationships": _UPDATE_TABLE_RELATIONSHIPS,
    "column_mappings": _UPDATE_COLUMN_MAPPINGS,
    "concepts": _UPDATE_CONCEPTS,
}
_DELETE_SQL: dict[str, str] = {
    "systems": _DELETE_SYSTEMS,
    "data_sources": _DELETE_DATA_SOURCES,
    "schemas": _DELETE_SCHEMAS,
    "tables": _DELETE_TABLES,
    "columns": _DELETE_COLUMNS,
    "deployment_tables": _DELETE_DEPLOYMENT_TABLES,
    "table_relationships": _DELETE_TABLE_RELATIONSHIPS,
    "column_mappings": _DELETE_COLUMN_MAPPINGS,
    "concepts": _DELETE_CONCEPTS,
}
_HSTRY_INSERT_SQL: dict[str, str] = {
    "systems": _HSTRY_INSERT_SYSTEMS,
    "data_sources": _HSTRY_INSERT_DATA_SOURCES,
    "schemas": _HSTRY_INSERT_SCHEMAS,
    "tables": _HSTRY_INSERT_TABLES,
    "columns": _HSTRY_INSERT_COLUMNS,
    "deployment_tables": _HSTRY_INSERT_DEPLOYMENT_TABLES,
    "table_relationships": _HSTRY_INSERT_TABLE_RELATIONSHIPS,
    "column_mappings": _HSTRY_INSERT_COLUMN_MAPPINGS,
    "concepts": _HSTRY_INSERT_CONCEPTS,
}


# FK-respecting order; reverse for deletes. `deployment_tables`
# references tables, systems, and data_sources (all earlier), so it sits
# after columns. `concepts` has no FK columns, so its position is
# unconstrained — placed last for consistency with TABLE_ORDER.
_FK_ORDER: tuple[str, ...] = (
    "systems",
    "data_sources",
    "schemas",
    "tables",
    "columns",
    "deployment_tables",
    "table_relationships",
    "column_mappings",
    "concepts",
)


def _bucket(buckets: dict[str, list[RowChange]], c: RowChange) -> None:
    """Append a change to its per-table bucket, failing loudly if unknown.

    The buckets are pre-seeded from `_FK_ORDER`, so indexing them directly
    would raise a contextless `KeyError` for a table this module does not
    know about (e.g. one added to `data_model.TABLE_ORDER` but missed in
    `_FK_ORDER`) — and that `KeyError` is misreported as a config error by
    `load_catalog_data.main`. Raising `ValueError` here matches the param
    builders' unknown-table behavior and names the offending table.

    Args:
        buckets: Per-table lists keyed by every table in `_FK_ORDER`.
        c: The change to file under its table.

    Raises:
        ValueError: If `c.table` is not a key in `buckets`.
    """
    if c.table not in buckets:
        raise ValueError(f"Unknown table {c.table}")
    buckets[c.table].append(c)


def apply_diff(
    conn: psycopg2.extensions.connection,
    diff: Diff,
    commit_sha: str,
    reset_hstry: bool,
) -> None:
    """Apply the diff against the DB in a single transaction.

    Sequence (all inside one transaction):
      0. Defer the deployment_tables physical-address UNIQUE
         (`DEPLOYMENT_TABLES_PHYSICAL_ADDRESS_CONSTRAINT`) so it is
         re-checked once at commit rather than after each row (lets a
         validated address swap/chain between updated rows settle), and
         the columns.ref_table_id FK
         (`COLUMNS_REF_TABLE_ID_FK_CONSTRAINT`) so a columns UPDATE may
         point at a `tables` row this same transaction INSERTs later
         (updates run before inserts in the phase order).
      1. If `reset_hstry`: TRUNCATE every `*_hstry` table.
      2. Deletes in REVERSE FK order — insert old row to `*_hstry`
         (with `end_ts = now()`), then DELETE from main.
      3. Updates in FK order — insert old row to `*_hstry` (carrying
         the OLD `update_reason`), then UPDATE main with new content
         (stamping validated_ts on any validated false->true transition).
      4. Inserts in FK order — INSERT into main with `insert_ts =
         update_ts = now()` and `update_reason = NULL`.
      5. Insert one `load_audit` row (commit SHA + diff counts).

    The connection is committed on success and rolled back on any
    exception before re-raising.

    Args:
        conn: Active psycopg2 connection.
        diff: The diff to apply.
        commit_sha: The resolved commit SHA the loaded corpus reflects,
            recorded in the load_audit row. Resolution moved to the
            orchestrator (see load_catalog_data.run) so dry-run and real
            runs exercise the same SHA-resolution failure surface; this
            function simply records the value it is given.
        reset_hstry: If True, TRUNCATE every `*_hstry` first.

    Raises:
        psycopg2.Error: On any database failure (after rollback).
        ValueError: If a `RowChange` names a table outside `_FK_ORDER`.
    """
    deletes_by_table: dict[str, list[RowChange]] = {t: [] for t in _FK_ORDER}
    updates_by_table: dict[str, list[RowChange]] = {t: [] for t in _FK_ORDER}
    inserts_by_table: dict[str, list[RowChange]] = {t: [] for t in _FK_ORDER}
    for c in diff.deletes:
        _bucket(deletes_by_table, c)
    for c in diff.updates:
        _bucket(updates_by_table, c)
    for c in diff.inserts:
        _bucket(inserts_by_table, c)

    try:
        with conn.cursor() as cur:
            # Defer the deployment_tables physical-address UNIQUE for this
            # transaction: an address swap or chain between updated rows is
            # a legal end state, but applying the per-row UPDATEs in
            # arbitrary order can transiently collide mid-transaction. With
            # the constraint deferred it is re-checked once at commit, when
            # the rows have settled. Every other writer still sees it
            # checked at statement time (it is `initially immediate`).
            cur.execute(_SET_CONSTRAINTS_DEFER_DEPLOYMENT_TABLES)
            # Defer the columns.ref_table_id FK too: updates run before
            # inserts in the phase order, so an in-place columns UPDATE
            # linking to a table INSERTed by this same transaction would
            # otherwise fail at statement time on a legal end state.
            # Deferred, the FK is enforced once at commit.
            cur.execute(_SET_CONSTRAINTS_DEFER_COLUMNS_REF_TABLE_ID)
            if reset_hstry:
                # TRUNCATE is transactional in Postgres — doing it
                # inside the load transaction means a failed apply
                # also reverts the truncate (atomic bootstrap reset).
                for hstry in _HSTRY_TABLES:
                    cur.execute(
                        sql.SQL("TRUNCATE TABLE {}").format(
                            sql.Identifier(hstry)
                        )
                    )
                logger.info("Reset every *_hstry table (truncated)")

            # Deletes in reverse FK order.
            for table in reversed(_FK_ORDER):
                for c in deletes_by_table[table]:
                    cur.execute(
                        _HSTRY_INSERT_SQL[table], _pk_params(table, c.key)
                    )
                    cur.execute(
                        _DELETE_SQL[table], _pk_params(table, c.key)
                    )

            # Updates in FK order.
            for table in _FK_ORDER:
                for c in updates_by_table[table]:
                    cur.execute(
                        _HSTRY_INSERT_SQL[table], _pk_params(table, c.key)
                    )
                    cur.execute(
                        _UPDATE_SQL[table],
                        _update_params(table, c.new, c.old, c.key),
                    )

            # Inserts in FK order.
            for table in _FK_ORDER:
                for c in inserts_by_table[table]:
                    cur.execute(
                        _INSERT_SQL[table], _insert_params(table, c.new)
                    )

            # Audit row (every real run, incl. empty diffs) — same
            # transaction, so loaded_ts == this run's now() == the
            # update_ts/insert_ts of any rows it wrote (lineage join).
            cur.execute(
                _INSERT_LOAD_AUDIT,
                (
                    commit_sha,
                    len(diff.inserts),
                    len(diff.updates),
                    len(diff.deletes),
                    reset_hstry,
                ),
            )

        conn.commit()
        logger.info(
            f"apply_diff committed: {diff.summary()} @ {commit_sha}"
            + (" (reset_hstry=True)" if reset_hstry else "")
        )
    except Exception as e:
        # Log before rollback: on a dead connection rollback() itself
        # raises, and logging first ensures the root cause is captured
        # rather than masked by the rollback failure.
        logger.error(f"apply_diff failed; rolling back: {e}")
        conn.rollback()
        raise
