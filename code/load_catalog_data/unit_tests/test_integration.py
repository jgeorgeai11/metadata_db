"""End-to-end integration test for the metadata_db loader (gated).

Skipped unless ``METADATA_DB_INTEGRATION=1``. Requires a reachable
Postgres with rights to CREATE DATABASE and the ltree extension — run
with maintainer credentials, e.g.:

    METADATA_DB_INTEGRATION=1 \
    POSTGRES_HOST=localhost POSTGRES_PORT=5432 \
    POSTGRES_USER=<maintainer> POSTGRES_PASSWORD=<pw> \
    uv run pytest -m integration \
        code/load_catalog_data/unit_tests/test_integration.py -v

The fixture drops and recreates a throwaway database via
``apply_ddl --create-db`` (applying the single ``0001`` migration), then
tests drive the real loader against it against the venue-free, 9-table
schema (systems registry, data sources, schemas, tables, columns,
deployment_tables, relationships, mappings, concepts).

This is the only test that executes db_io.py's hand-written SQL against
the real ltree schema.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg2
import pytest

import load_catalog_data as lmd
from corpus_validation import ValidationError
from db_io import connection_kwargs
from data_model import PRIMARY_KEY_COLUMNS, TABLE_ORDER

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("METADATA_DB_INTEGRATION") != "1",
        reason="METADATA_DB_INTEGRATION!=1; skipping integration test",
    ),
]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEST_DB = "metadata_db_integration"
TEST_SCHEMA = "catalog"


# ---------------------------------------------------------------------------
# corpus staging
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    """Write ``text`` to ``path``, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _stage_full_corpus(data_root: Path) -> None:
    """Lay out a two-data-source venue-free corpus exercising every table.

    ``ocs`` deploys in warehouse; ``edw_prd`` deploys in edw. A bare
    deployments entry expands to a table-grain row per documented table.
    """
    _write(
        data_root / "systems.yaml",
        "- system: warehouse\n  description: source\n  notes: null\n  update_reason: null\n"
        "- system: edw\n  description: target\n  notes: null\n  update_reason: null\n",
    )
    ocs = data_root / "sources" / "ocs"
    ocs_schema = ocs / "general"
    _write(ocs / "data_source.yaml", "owner: data-ops\ndescription: ocs\nnotes: null\nupdate_reason: null\n")
    _write(ocs / "deployments.yaml", "- system: warehouse\n")
    _write(ocs_schema / "schema.yaml", "description: general\nnotes: null\nupdate_reason: null\n")
    _write(
        ocs_schema / "tables.yaml",
        "- table_name: bene\n  description: beneficiaries\n  notes: null\n  update_reason: null\n"
        "- table_name: claim\n  description: claims\n  notes: null\n  update_reason: null\n",
    )
    _write(
        ocs_schema / "columns.yaml",
        "- table_name: bene\n  column_name: bene_id\n  data_type: TEXT\n  is_nullable: false\n  is_primary_key: true\n  description: id\n  notes: null\n  update_reason: null\n"
        "- table_name: claim\n  column_name: bene_id\n  data_type: TEXT\n  is_nullable: false\n  description: fk\n  notes: null\n  update_reason: null\n"
        "- table_name: claim\n  column_name: clm_id\n  data_type: TEXT\n  is_nullable: false\n  is_primary_key: true\n  description: id\n  notes: null\n  update_reason: null\n",
    )
    _write(
        ocs_schema / "table_relationships.yaml",
        "- table_a_id: ocs.general.bene\n"
        "  table_b_id: ocs.general.claim\n"
        "  relationship_name: default\n"
        "  join_condition: ocs.general.bene.bene_id = ocs.general.claim.bene_id\n"
        "  cardinality: one_to_many\n"
        "  use_when: null\n"
        "  notes: null\n"
        "  validated: false\n"
        "  update_reason: null\n",
    )
    _write(
        ocs_schema / "mappings" / "edw_prd.yaml",
        "- source_column_id: ocs.general.bene.bene_id\n"
        "  mapping_name: default\n"
        "  target_expression: edw_prd.claims_vw.bene.bene_extl_id\n"
        "  use_when: null\n"
        "  notes: null\n"
        "  validated: false\n"
        "  update_reason: null\n",
    )
    _write(
        ocs / "concepts.yaml",
        "- name: concept.claim\n  label: Claim\n"
        "  definition: A single request for payment.\n"
        "  notes: null\n"
        "  related_object_ids:\n"
        "    - ocs.general.claim\n"
        "    - ocs.general.claim.clm_id\n"
        "  update_reason: null\n",
    )
    edw = data_root / "sources" / "edw_prd"
    edw_schema = edw / "claims_vw"
    _write(edw / "data_source.yaml", "owner: data-ops\ndescription: prd\nnotes: null\nupdate_reason: null\n")
    _write(edw / "deployments.yaml", "- system: edw\n")
    _write(edw_schema / "schema.yaml", "description: view\nnotes: null\nupdate_reason: null\n")
    _write(
        edw_schema / "tables.yaml",
        "- table_name: bene\n  description: bene\n  notes: null\n  update_reason: null\n",
    )
    _write(
        edw_schema / "columns.yaml",
        "- table_name: bene\n  column_name: bene_extl_id\n  data_type: TEXT\n  is_nullable: true\n  description: mbi\n  notes: null\n  update_reason: null\n",
    )


def _config(tmp_path: Path, data_root: Path) -> Path:
    """Write a loader TOML pointing at ``data_root`` and the test DB; return its path."""
    cfg = tmp_path / "loader.toml"
    cfg.write_text(
        f'data_root = "{data_root.as_posix()}"\n'
        f'database = "{TEST_DB}"\n'
        f'schema = "{TEST_SCHEMA}"\n'
    )
    return cfg


def _load(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the loader for real against the test DB."""
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    lmd.main()


def _count(conn: psycopg2.extensions.connection, table: str) -> int:
    """Return the row count of ``table``."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def _scalar(conn: psycopg2.extensions.connection, query: str) -> Any:
    """Run ``query`` and return its first column of the first row, or None if empty."""
    with conn.cursor() as cur:
        cur.execute(query)
        row = cur.fetchone()
        return row[0] if row else None


def _truncate_all(conn: psycopg2.extensions.connection) -> None:
    """Reset the corpus: truncate all 9 main tables, their _hstry twins, and load_audit.

    Keeps the table list in one place so a truncate-first test stays
    independent of the module-scoped DB's leftover state without two
    hand-maintained copies drifting out of sync. Commits so the reset is
    durable before the load under test runs.
    """
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE systems, data_sources, schemas, tables, columns, "
            "deployment_tables, table_relationships, column_mappings, "
            "concepts, systems_hstry, data_sources_hstry, schemas_hstry, "
            "tables_hstry, columns_hstry, deployment_tables_hstry, "
            "table_relationships_hstry, column_mappings_hstry, "
            "concepts_hstry, load_audit "
            "RESTART IDENTITY CASCADE"
        )
    conn.commit()


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def integration_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Drop + recreate the throwaway DB and apply the single 0001 migration."""
    maint = connection_kwargs("postgres", TEST_SCHEMA)
    conn = psycopg2.connect(**maint)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    finally:
        conn.close()

    ddl_cfg = tmp_path_factory.mktemp("ddl") / "ddl.toml"
    ddl_cfg.write_text(
        'ddl_dir = "code/apply_ddl/ddl_catalog"\n'
        f'database = "{TEST_DB}"\n'
        f'schema = "{TEST_SCHEMA}"\n'
    )
    subprocess.run(
        [
            "uv", "run", "code/apply_ddl/apply_ddl.py",
            "--config", str(ddl_cfg), "--create-db",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )

    conn = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            # Each recreate restores PUBLIC's default CONNECT, which would let
            # every role in the cluster (the MCP and pgweb read-only accounts
            # included) open sessions here. Revoke it at the same place the
            # database is born — the grants/public_hardening.sql rationale;
            # this suite's role owns the DB, so its own access is unaffected.
            cur.execute(f"REVOKE CONNECT ON DATABASE {TEST_DB} FROM PUBLIC")
    finally:
        conn.close()

    yield


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_pk_agreement_and_ltree_types(integration_db: None) -> None:
    """DDL PKs match PRIMARY_KEY_COLUMNS and ID columns are ltree.

    The introspection queries below interpolate values into the SQL string
    with f-strings (e.g. ``{TEST_SCHEMA}``, ``{gone}``, ``{tbl}``, ``{idx}``)
    rather than binding them as ``%s`` parameters as the PK query does. Every
    interpolated value is a trusted module constant or an element of a literal
    tuple defined in this file, so there is no injection surface; do not copy
    this idiom for any value that is not statically known here.
    """
    conn = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        for table in TABLE_ORDER:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT kcu.column_name FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu "
                    "  ON tc.constraint_name = kcu.constraint_name "
                    " AND tc.table_schema = kcu.table_schema "
                    "WHERE tc.constraint_type = 'PRIMARY KEY' "
                    "  AND tc.table_schema = %s AND tc.table_name = %s "
                    "ORDER BY kcu.ordinal_position",
                    (TEST_SCHEMA, table),
                )
                actual = tuple(r[0] for r in cur.fetchall())
            assert actual == PRIMARY_KEY_COLUMNS[table], table
        # Every catalog object lives in TEST_SCHEMA, and `public` is gone.
        assert _scalar(
            conn,
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name = 'public'",
        ) == 0
        assert _scalar(
            conn,
            "SELECT extnamespace::regnamespace::text FROM pg_extension "
            "WHERE extname = 'ltree'",
        ) == TEST_SCHEMA
        # ID columns are ltree.
        assert _scalar(
            conn,
            "SELECT udt_name FROM information_schema.columns "
            f"WHERE table_schema='{TEST_SCHEMA}' "
            "AND table_name='columns' AND column_name='column_id'",
        ) == "ltree"
        # data_sources carries `owner` NOT NULL and has dropped the
        # venue-dependent system/database_name columns.
        assert _scalar(
            conn,
            "SELECT data_type || '/' || is_nullable FROM information_schema.columns "
            "WHERE table_name='data_sources' AND column_name='owner'",
        ) == "text/NO"
        for gone in ("system", "database_name"):
            assert _scalar(
                conn,
                "SELECT count(*) FROM information_schema.columns "
                f"WHERE table_name='data_sources' AND column_name='{gone}'",
            ) == 0
        # column_mappings dropped both target_system and source_system.
        for gone in ("target_system", "source_system"):
            assert _scalar(
                conn,
                "SELECT count(*) FROM information_schema.columns "
                f"WHERE table_name='column_mappings' AND column_name='{gone}'",
            ) == 0
        # table_relationships dropped the system column (venue validity is
        # derived from deployments).
        assert _scalar(
            conn,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='table_relationships' AND column_name='system'",
        ) == 0
        # deployment_tables is a pure-facts table: every column NOT NULL
        # and the freeform columns are gone entirely.
        assert _scalar(
            conn,
            "SELECT data_type || '/' || is_nullable FROM information_schema.columns "
            "WHERE table_name='deployment_tables' AND column_name='physical_table_name'",
        ) == "text/NO"
        assert _scalar(
            conn,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='deployment_tables' AND is_nullable='YES'",
        ) == 0
        for tbl in ("deployment_tables", "deployment_tables_hstry"):
            for gone in ("notes", "update_reason"):
                assert _scalar(
                    conn,
                    "SELECT count(*) FROM information_schema.columns "
                    f"WHERE table_name='{tbl}' AND column_name='{gone}'",
                ) == 0, (tbl, gone)
        assert _scalar(
            conn,
            "SELECT count(*) FROM information_schema.table_constraints "
            f"WHERE table_schema='{TEST_SCHEMA}' "
            "  AND table_name='deployment_tables' "
            "  AND constraint_type='UNIQUE'",
        ) >= 1
        # The physical-address UNIQUE is named and deferrable-initially-
        # immediate, so the loader can defer it within its transaction
        # while every other writer still sees it checked at statement time.
        assert _scalar(
            conn,
            "SELECT condeferrable::text || '/' || condeferred::text "
            "FROM pg_constraint "
            "WHERE conname='deployment_tables_physical_address_key'",
        ) == "true/false"
        # data_source_id redundancy (= table_id's leading segment) is
        # enforced by a CHECK constraint at the DB layer.
        assert _scalar(
            conn,
            "SELECT count(*) FROM pg_constraint "
            "WHERE conrelid = 'deployment_tables'::regclass AND contype='c'",
        ) >= 1
        # FK column data_source_id has its own btree index (the composite
        # index leads with `system`, so it does not serve it).
        assert _scalar(
            conn,
            "SELECT count(*) FROM pg_indexes "
            "WHERE tablename='deployment_tables' "
            "AND indexname='idx_deployment_tables_data_source_id'",
        ) == 1
        # deployment_tables_hstry PK is (table_id, system, end_ts).
        assert _scalar(
            conn,
            "SELECT array_agg(kcu.column_name::text ORDER BY kcu.ordinal_position) "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            " AND tc.table_schema = kcu.table_schema "
            "WHERE tc.constraint_type = 'PRIMARY KEY' "
            f"  AND tc.table_schema = '{TEST_SCHEMA}' "
            "  AND tc.table_name = 'deployment_tables_hstry'",
        ) == ["table_id", "system", "end_ts"]
        # Required documentation prose is NOT NULL (main + _hstry mirror):
        # the five descriptions, concepts.definition, and both ltree[]
        # columns.
        for tbl, col in (
            ("systems", "description"),
            ("data_sources", "description"),
            ("schemas", "description"),
            ("tables", "description"),
            ("columns", "description"),
            ("concepts", "definition"),
            ("concepts", "related_object_ids"),
            ("column_mappings", "target_tables_referenced"),
        ):
            for suffix in ("", "_hstry"):
                assert _scalar(
                    conn,
                    "SELECT is_nullable FROM information_schema.columns "
                    f"WHERE table_schema='{TEST_SCHEMA}' "
                    f"AND table_name='{tbl}{suffix}' AND column_name='{col}'",
                ) == "NO", (tbl + suffix, col)
        # M5: columns.is_primary_key exists as a NOT NULL boolean.
        assert _scalar(
            conn,
            "SELECT data_type || '/' || is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name='columns' AND column_name='is_primary_key'",
        ) == "boolean/NO"
        # GiST indexes present.
        for tbl, idx in (
            ("column_mappings", "idx_column_mappings_target_tables_gist"),
            ("concepts", "idx_concepts_id_gist"),
            ("concepts", "idx_concepts_related_objects_gist"),
            ("deployment_tables", "idx_deployment_tables_table_id_gist"),
        ):
            assert _scalar(
                conn,
                "SELECT count(*) FROM pg_indexes "
                f"WHERE tablename='{tbl}' AND indexname='{idx}'",
            ) == 1, idx
        # concepts ids are ltree; related_object_ids is ltree[] (_ltree).
        assert _scalar(
            conn,
            "SELECT udt_name FROM information_schema.columns "
            f"WHERE table_schema='{TEST_SCHEMA}' "
            "AND table_name='concepts' AND column_name='concept_id'",
        ) == "ltree"
        assert _scalar(
            conn,
            "SELECT udt_name FROM information_schema.columns "
            f"WHERE table_schema='{TEST_SCHEMA}' "
            "AND table_name='concepts' AND column_name='related_object_ids'",
        ) == "_ltree"
        # join_type is gone; cardinality is a nullable text enum.
        assert _scalar(
            conn,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='table_relationships' AND column_name='join_type'",
        ) == 0
        assert _scalar(
            conn,
            "SELECT data_type || '/' || is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name='table_relationships' AND column_name='cardinality'",
        ) == "text/YES"
    finally:
        conn.close()


def test_loader_lifecycle(
    tmp_path: Path, integration_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the loader through its full lifecycle against the real schema."""
    data_root = tmp_path / "data"
    _stage_full_corpus(data_root)
    cfg = _config(tmp_path, data_root)
    conn = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        _truncate_all(conn)
        # --- 1. Full load ---------------------------------------------
        _load(cfg, monkeypatch)
        assert _count(conn, "systems") == 2
        assert _count(conn, "data_sources") == 2
        assert _count(conn, "schemas") == 2
        assert _count(conn, "tables") == 3
        assert _count(conn, "columns") == 4
        # Bare deployments expand to a deployment_tables row per documented
        # table: ocs's two tables in warehouse plus edw_prd's one in edw.
        assert _count(conn, "deployment_tables") == 3
        assert _count(conn, "table_relationships") == 1
        assert _count(conn, "column_mappings") == 1
        assert _count(conn, "concepts") == 1
        assert _count(conn, "concepts_hstry") == 0
        # deployment physical names defaulted to the documented names.
        assert _scalar(
            conn,
            "SELECT physical_table_name FROM deployment_tables "
            "WHERE table_id = 'ocs.general.bene' AND system = 'warehouse'",
        ) == "bene"
        # venue-inventory query: which data sources deploy in warehouse?
        assert _scalar(
            conn,
            "SELECT count(DISTINCT data_source_id) FROM deployment_tables "
            "WHERE system = 'warehouse'",
        ) == 1
        assert _scalar(
            conn,
            "SELECT label FROM concepts WHERE concept_id = 'ocs.concept.claim'",
        ) == "Claim"
        # ltree subtree probe over the reserved `concept` namespace.
        assert _scalar(
            conn,
            "SELECT count(*) FROM concepts WHERE concept_id <@ 'ocs.concept'",
        ) == 1
        assert _scalar(
            conn,
            "SELECT related_object_ids::text[] FROM concepts "
            "WHERE concept_id = 'ocs.concept.claim'",
        ) == ["ocs.general.claim", "ocs.general.claim.clm_id"]
        assert _scalar(
            conn,
            "SELECT concept_id::text FROM concepts "
            "WHERE related_object_ids <@ 'ocs.general.claim'::ltree",
        ) == "ocs.concept.claim"
        assert _scalar(
            conn, "SELECT cardinality FROM table_relationships"
        ) == "one_to_many"
        assert _scalar(
            conn,
            "SELECT is_primary_key FROM columns "
            "WHERE column_id = 'ocs.general.bene.bene_id'",
        ) is True
        assert _count(conn, "load_audit") == 1
        assert _scalar(conn, "SELECT commit_sha FROM load_audit") is not None
        # 19 = 2 systems + 2 data_sources + 2 schemas + 3 tables + 4 columns
        #      + 3 deployments + 1 relationship + 1 mapping + 1 concept
        assert _scalar(conn, "SELECT inserts FROM load_audit") == 19
        # target_tables_referenced computed as ltree[] (venue-free).
        assert _scalar(
            conn,
            "SELECT target_tables_referenced::text[] FROM column_mappings",
        ) == ["edw_prd.claims_vw.bene"]
        assert _scalar(
            conn,
            "SELECT count(*) FROM columns WHERE column_id <@ 'ocs.general'",
        ) == 3

        # --- 2. Idempotent re-run (empty diff, heartbeat row) ---------
        _load(cfg, monkeypatch)
        assert _count(conn, "columns") == 4
        assert _count(conn, "columns_hstry") == 0
        assert _count(conn, "deployment_tables_hstry") == 0
        assert _count(conn, "table_relationships_hstry") == 0
        assert _count(conn, "concepts_hstry") == 0
        assert _count(conn, "load_audit") == 2

        # --- 3. Update a row -> _hstry carries the old version --------
        ocs_schema = data_root / "sources" / "ocs" / "general"
        _write(
            ocs_schema / "tables.yaml",
            "- table_name: bene\n  description: beneficiaries (v2)\n  notes: null\n  update_reason: clarified\n"
            "- table_name: claim\n  description: claims\n  notes: null\n  update_reason: null\n",
        )
        _load(cfg, monkeypatch)
        assert _scalar(
            conn,
            "SELECT description FROM tables WHERE table_id = 'ocs.general.bene'",
        ) == "beneficiaries (v2)"
        assert _count(conn, "tables_hstry") == 1
        assert _scalar(conn, "SELECT end_ts FROM tables_hstry LIMIT 1") is not None
        assert _scalar(
            conn, "SELECT update_reason FROM tables_hstry LIMIT 1"
        ) is None

        # --- 4. Deployment rename -> _hstry carries the old address ----
        # The venue entry carries only residency facts (no update_reason
        # key — deployment_tables rows are exempt from the reason
        # discipline); claim's row is content-identical, so only bene
        # updates and writes one history row.
        _write(
            data_root / "sources" / "ocs" / "deployments.yaml",
            "- system: warehouse\n"
            "  schemas:\n"
            "    general:\n"
            "      tables:\n"
            "        bene: bene_v2\n"
            "        claim: claim\n",
        )
        _load(cfg, monkeypatch)
        assert _scalar(
            conn,
            "SELECT physical_table_name FROM deployment_tables "
            "WHERE table_id = 'ocs.general.bene' AND system = 'warehouse'",
        ) == "bene_v2"
        assert _count(conn, "deployment_tables_hstry") == 1
        # Restore the bare (all-tables) deployment for later phases — also
        # an update to bene's row (one more history row, no reason).
        _write(
            data_root / "sources" / "ocs" / "deployments.yaml",
            "- system: warehouse\n",
        )
        _load(cfg, monkeypatch)
        assert _count(conn, "deployment_tables_hstry") == 2

        # --- 5. validated_ts stamped on false->true, NULL'd on true->false
        mapping = ocs_schema / "mappings" / "edw_prd.yaml"
        _write(
            mapping,
            "- source_column_id: ocs.general.bene.bene_id\n"
            "  mapping_name: default\n"
            "  target_expression: edw_prd.claims_vw.bene.bene_extl_id\n"
            "  use_when: null\n"
            "  notes: null\n"
            "  validated: true\n"
            "  update_reason: confirmed against target\n",
        )
        _load(cfg, monkeypatch)
        assert _scalar(
            conn, "SELECT validated_ts FROM column_mappings"
        ) is not None
        _write(
            mapping,
            "- source_column_id: ocs.general.bene.bene_id\n"
            "  mapping_name: default\n"
            "  target_expression: edw_prd.claims_vw.bene.bene_extl_id\n"
            "  use_when: null\n"
            "  notes: null\n"
            "  validated: false\n"
            "  update_reason: needs recheck\n",
        )
        _load(cfg, monkeypatch)
        assert _scalar(
            conn, "SELECT validated_ts FROM column_mappings"
        ) is None
        assert _scalar(
            conn,
            "SELECT count(*) FROM column_mappings_hstry "
            "WHERE validated_ts IS NOT NULL",
        ) == 1

        # --- 6. Delete a row -> gone from main, preserved in _hstry ---
        _write(ocs_schema / "table_relationships.yaml", "[]\n")
        _load(cfg, monkeypatch)
        assert _count(conn, "table_relationships") == 0
        assert _count(conn, "table_relationships_hstry") == 1

        # --- 7. is_primary_key round-trip: flip true -> false ---------
        _write(
            ocs_schema / "columns.yaml",
            "- table_name: bene\n  column_name: bene_id\n  data_type: TEXT\n  is_nullable: false\n  is_primary_key: false\n  description: id\n  notes: null\n  update_reason: regrained\n"
            "- table_name: claim\n  column_name: bene_id\n  data_type: TEXT\n  is_nullable: false\n  description: fk\n  notes: null\n  update_reason: null\n"
            "- table_name: claim\n  column_name: clm_id\n  data_type: TEXT\n  is_nullable: false\n  is_primary_key: true\n  description: id\n  notes: null\n  update_reason: null\n",
        )
        _load(cfg, monkeypatch)
        assert _scalar(
            conn,
            "SELECT is_primary_key FROM columns "
            "WHERE column_id = 'ocs.general.bene.bene_id'",
        ) is False
        assert _scalar(
            conn,
            "SELECT count(*) FROM columns_hstry "
            "WHERE column_id = 'ocs.general.bene.bene_id' "
            "  AND is_primary_key IS TRUE",
        ) == 1
    finally:
        conn.close()


def test_loader_rejects_design_doc_violations(tmp_path: Path) -> None:
    """The loader rejects, pre-merge, at least one violation per design rule."""
    data_root = tmp_path / "data"
    _stage_full_corpus(data_root)
    ocs_schema = data_root / "sources" / "ocs" / "general"
    # Relationship-rule violations: reverse-orientation duplicate (B) and an
    # invalid cardinality on the bene<->claim pair.
    _write(
        ocs_schema / "table_relationships.yaml",
        "- table_a_id: ocs.general.bene\n"
        "  table_b_id: ocs.general.claim\n"
        "  relationship_name: default\n"
        "  join_condition: ocs.general.bene.bene_id = ocs.general.claim.bene_id\n"
        "  cardinality: one_to_many\n"
        "  use_when: null\n  notes: null\n  validated: false\n  update_reason: null\n"
        "- table_a_id: ocs.general.claim\n"
        "  table_b_id: ocs.general.bene\n"
        "  relationship_name: default\n"
        "  join_condition: ocs.general.claim.bene_id = ocs.general.bene.bene_id\n"
        "  cardinality: MANY_TO_ONE\n"
        "  use_when: null\n  notes: null\n  validated: false\n  update_reason: null\n",
    )
    # Mapping-rule violations: a subquery expression (M1) and now() (M3).
    _write(
        ocs_schema / "mappings" / "edw_prd.yaml",
        "- source_column_id: ocs.general.bene.bene_id\n"
        "  mapping_name: default\n"
        "  target_expression: (SELECT edw_prd.claims_vw.bene.bene_extl_id FROM edw_prd.claims_vw.bene)\n"
        "  use_when: null\n  notes: null\n  validated: false\n  update_reason: null\n"
        "- source_column_id: ocs.general.claim.bene_id\n"
        "  mapping_name: default\n"
        "  target_expression: now()\n"
        "  use_when: null\n  notes: null\n  validated: false\n  update_reason: null\n",
    )
    config = {"data_root": str(data_root), "database": TEST_DB, "schema": TEST_SCHEMA}
    with pytest.raises(ValidationError) as exc:
        lmd.run(config, dry_run=True, reset_hstry=False)
    issues = exc.value.issues
    assert any("duplicate relationship" in i for i in issues)  # B
    assert any("cardinality 'MANY_TO_ONE'" in i for i in issues)  # enum
    assert any("single value-producing expression" in i for i in issues)  # M1
    assert any("volatile/context-dependent" in i for i in issues)  # M3


def test_deployment_address_swap_commits_in_one_run(
    tmp_path: Path, integration_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two updated deployment rows swapping physical addresses commit in one run.

    Swapping the physical table names of two rows in the same venue is a
    legal end state (each address stays unique), but the per-row UPDATEs
    transiently collide mid-transaction. The loader defers the
    physical-address UNIQUE so the pair settles at commit instead of
    aborting. Truncates first so this load is independent of other tests.
    """
    data_root = tmp_path / "data"
    _stage_full_corpus(data_root)
    cfg = _config(tmp_path, data_root)
    conn = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        _truncate_all(conn)

        # Initial load: bene->bene, claim->claim in warehouse.
        _load(cfg, monkeypatch)
        assert _scalar(
            conn,
            "SELECT physical_table_name FROM deployment_tables "
            "WHERE table_id = 'ocs.general.bene' AND system = 'warehouse'",
        ) == "bene"

        # Swap the two physical table names within warehouse in a single edit:
        # bene now sits at physical `claim` and claim at physical `bene`.
        # Each row's UPDATE transiently collides with the other's current
        # address; the deferred constraint lets the pair commit as one.
        _write(
            data_root / "sources" / "ocs" / "deployments.yaml",
            "- system: warehouse\n"
            "  schemas:\n"
            "    general:\n"
            "      tables:\n"
            "        bene: claim\n"
            "        claim: bene\n",
        )
        _load(cfg, monkeypatch)
        assert _scalar(
            conn,
            "SELECT physical_table_name FROM deployment_tables "
            "WHERE table_id = 'ocs.general.bene' AND system = 'warehouse'",
        ) == "claim"
        assert _scalar(
            conn,
            "SELECT physical_table_name FROM deployment_tables "
            "WHERE table_id = 'ocs.general.claim' AND system = 'warehouse'",
        ) == "bene"
    finally:
        conn.close()


def _run_config(data_root: Path) -> dict[str, Any]:
    """A parsed-config dict for driving `lmd.run` directly."""
    return {
        "data_root": str(data_root),
        "database": TEST_DB,
        "schema": TEST_SCHEMA,
    }


def _all_table_counts(conn: psycopg2.extensions.connection) -> dict[str, int]:
    """Row counts for every main table, every _hstry mirror, and load_audit."""
    tables = (
        list(TABLE_ORDER)
        + [f"{t}_hstry" for t in TABLE_ORDER]
        + ["load_audit"]
    )
    return {t: _count(conn, t) for t in tables}


def test_advisory_lock_excludes_concurrent_runs(
    tmp_path: Path, integration_db: None
) -> None:
    """A held loader lock fail-fasts both a real run and a dry-run.

    A second connection holding the transaction-scoped advisory lock
    stands in for a concurrent loader session (both modes acquire the
    lock through the same code path, so this also documents that a
    long dry-run's held lock excludes a concurrent real run — the
    reader blocks the writer — and vice versa).
    """
    data_root = tmp_path / "data"
    _stage_full_corpus(data_root)
    config = _run_config(data_root)

    holder = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        with holder.cursor() as cur:
            # Take the loader's lock and keep the transaction open — the
            # xact-scoped lock is released only on commit/rollback. This must
            # be the SAME two-key lock the loader now acquires
            # (LOADER_LOCK_KEY + the schema-scoped second key); Postgres
            # treats the 1-arg and 2-arg advisory-lock functions as distinct
            # lock spaces, so the single-key form would not exclude the run.
            # Compute the second key via the loader's own helper so the test
            # cannot drift from the loader again.
            cur.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s)",
                (lmd.LOADER_LOCK_KEY, lmd._schema_lock_key(TEST_DB, TEST_SCHEMA)),
            )
            assert cur.fetchone()[0] is True

        # A real run fails fast while the lock is held...
        with pytest.raises(lmd.LoadInProgressError, match="already in progress"):
            lmd.run(config, dry_run=False, reset_hstry=False)
        # ...and a dry-run is excluded exactly the same way (so a held
        # dry-run lock symmetrically excludes a real run).
        with pytest.raises(lmd.LoadInProgressError, match="already in progress"):
            lmd.run(config, dry_run=True, reset_hstry=False)
    finally:
        holder.close()

    # Once the holder releases (connection closed -> rollback), the same
    # dry-run goes through.
    lmd.run(config, dry_run=True, reset_hstry=False)


def test_clean_dry_run_is_pure(
    tmp_path: Path, integration_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A no-change dry-run leaves every table count and load_audit unchanged."""
    data_root = tmp_path / "data"
    _stage_full_corpus(data_root)
    cfg = _config(tmp_path, data_root)
    conn = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        _truncate_all(conn)
        _load(cfg, monkeypatch)
        before = _all_table_counts(conn)
        assert before["load_audit"] == 1

        lmd.run(_run_config(data_root), dry_run=True, reset_hstry=False)

        # No writes anywhere: main tables, _hstry mirrors, and no
        # load_audit heartbeat row for a dry-run.
        assert _all_table_counts(conn) == before
    finally:
        conn.close()


def test_lineage_join_ties_rows_to_their_run(
    tmp_path: Path, integration_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_audit.loaded_ts equals the write timestamps of that run's rows.

    After a second real run that inserts, updates, and deletes,
    loaded_ts must equal the insert_ts/update_ts of rows written that
    run and the end_ts of rows superseded that run — so the docs'
    headline lineage query (join a row's update_ts to load_audit) returns
    the run's commit SHA.
    """
    data_root = tmp_path / "data"
    _stage_full_corpus(data_root)
    cfg = _config(tmp_path, data_root)
    conn = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        _truncate_all(conn)
        _load(cfg, monkeypatch)

        # Run 2: update bene, insert a new table, delete the relationship.
        ocs_schema = data_root / "sources" / "ocs" / "general"
        _write(
            ocs_schema / "tables.yaml",
            "- table_name: bene\n  description: beneficiaries (v2)\n  notes: null\n  update_reason: clarified\n"
            "- table_name: claim\n  description: claims\n  notes: null\n  update_reason: null\n"
            "- table_name: enrollment\n  description: enrollments\n  notes: null\n  update_reason: null\n",
        )
        _write(ocs_schema / "table_relationships.yaml", "[]\n")
        _load(cfg, monkeypatch)

        run2_ts = _scalar(
            conn,
            "SELECT loaded_ts FROM load_audit ORDER BY load_id DESC LIMIT 1",
        )
        # Updated row: update_ts == the run's loaded_ts.
        assert _scalar(
            conn,
            "SELECT update_ts FROM tables WHERE table_id = 'ocs.general.bene'",
        ) == run2_ts
        # Inserted row: insert_ts == update_ts == the run's loaded_ts.
        assert _scalar(
            conn,
            "SELECT insert_ts FROM tables "
            "WHERE table_id = 'ocs.general.enrollment'",
        ) == run2_ts
        # Superseded rows (the updated bene's prior version and the deleted
        # relationship): end_ts == the run's loaded_ts.
        assert _scalar(
            conn,
            "SELECT end_ts FROM tables_hstry "
            "WHERE table_id = 'ocs.general.bene'",
        ) == run2_ts
        assert _scalar(
            conn, "SELECT end_ts FROM table_relationships_hstry LIMIT 1"
        ) == run2_ts
        # The headline lineage query: a row joins to the commit that
        # produced it.
        assert _scalar(
            conn,
            "SELECT la.commit_sha FROM tables t "
            "JOIN load_audit la ON la.loaded_ts = t.update_ts "
            "WHERE t.table_id = 'ocs.general.bene'",
        ) == _scalar(
            conn,
            "SELECT commit_sha FROM load_audit ORDER BY load_id DESC LIMIT 1",
        )
    finally:
        conn.close()


def test_reset_hstry_truncates_inside_load_transaction(
    tmp_path: Path, integration_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--reset-hstry truncates every _hstry table inside the load transaction.

    Runs under the maintainer credentials the integration suite already
    requires (TRUNCATE needs them — the CI role's INSERT-only _hstry
    grant deliberately lacks it; that privilege gap is documented in the
    maintenance doc, not tested here). The truncate being transactional
    is pinned by the reset flag landing in the same run's audit row.
    """
    data_root = tmp_path / "data"
    _stage_full_corpus(data_root)
    cfg = _config(tmp_path, data_root)
    conn = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        _truncate_all(conn)
        _load(cfg, monkeypatch)

        # Produce a history row (update bene), then reset.
        ocs_schema = data_root / "sources" / "ocs" / "general"
        _write(
            ocs_schema / "tables.yaml",
            "- table_name: bene\n  description: beneficiaries (v2)\n  notes: null\n  update_reason: clarified\n"
            "- table_name: claim\n  description: claims\n  notes: null\n  update_reason: null\n",
        )
        _load(cfg, monkeypatch)
        assert _count(conn, "tables_hstry") == 1
        # Release the verification connection's read transaction (the
        # _count above opened one; conn is not autocommit) BEFORE the
        # reset run: an "idle in transaction" session holds ACCESS SHARE
        # on tables_hstry, and the loader's TRUNCATE needs ACCESS
        # EXCLUSIVE — without this rollback the run below deadlocks
        # forever. Read-only transaction, so nothing is lost. The other
        # tests don't need it: a normal load takes only row-level locks,
        # which ACCESS SHARE does not block.
        conn.rollback()

        monkeypatch.setenv("METADATA_DB_ALLOW_RESET_HSTRY", "1")
        lmd.run(_run_config(data_root), dry_run=False, reset_hstry=True)

        for table in TABLE_ORDER:
            assert _count(conn, f"{table}_hstry") == 0, table
        # The reset ran inside the load transaction that also wrote its
        # audit row (same commit), flagged reset_hstry.
        assert _scalar(
            conn,
            "SELECT reset_hstry FROM load_audit ORDER BY load_id DESC LIMIT 1",
        ) is True
    finally:
        conn.close()


def test_multiple_mappings_and_cross_source_mapping(
    tmp_path: Path, integration_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Load the two behaviors the mapping_name discriminator unlocks.

    (a) one source column mapped twice, told apart by mapping_name with no
    PK collision; (b) a mapping toward another table in the same data
    source (a legitimate cross-table equivalence). Truncates first so this
    full-insert load is independent of the lifecycle test's leftover state.
    """
    data_root = tmp_path / "data"
    _stage_full_corpus(data_root)
    ocs_schema = data_root / "sources" / "ocs" / "general"
    # (a) two mappings for bene.bene_id, named default + alt (both need
    # use_when under the per-source-column discipline).
    _write(
        ocs_schema / "mappings" / "edw_prd.yaml",
        "- source_column_id: ocs.general.bene.bene_id\n"
        "  mapping_name: default\n"
        "  target_expression: edw_prd.claims_vw.bene.bene_extl_id\n"
        "  use_when: Prefer for the current pipeline.\n"
        "  notes: null\n"
        "  validated: false\n"
        "  update_reason: null\n"
        "- source_column_id: ocs.general.bene.bene_id\n"
        "  mapping_name: alt\n"
        "  target_expression: edw_prd.claims_vw.bene.bene_extl_id\n"
        "  use_when: Prefer for the legacy pipeline.\n"
        "  notes: null\n"
        "  validated: false\n"
        "  update_reason: null\n",
    )
    # (b) a within-data-source mapping toward another table (ocs tables are
    # co-deployed in warehouse). Not the source column's own table.
    _write(
        ocs_schema / "mappings" / "ocs.yaml",
        "- source_column_id: ocs.general.claim.bene_id\n"
        "  mapping_name: default\n"
        "  target_expression: ocs.general.bene.bene_id\n"
        "  use_when: null\n"
        "  notes: null\n"
        "  validated: false\n"
        "  update_reason: null\n",
    )
    cfg = _config(tmp_path, data_root)
    conn = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        _truncate_all(conn)

        _load(cfg, monkeypatch)

        # (a) bene.bene_id has two mappings — no PK collision.
        assert _scalar(
            conn,
            "SELECT count(*) FROM column_mappings "
            "WHERE source_column_id = 'ocs.general.bene.bene_id'",
        ) == 2
        assert _scalar(
            conn,
            "SELECT array_agg(mapping_name ORDER BY mapping_name) "
            "FROM column_mappings "
            "WHERE source_column_id = 'ocs.general.bene.bene_id'",
        ) == ["alt", "default"]

        # (b) the within-data-source mapping loaded, with
        # target_tables_referenced pointing at the other ocs table.
        assert _scalar(
            conn,
            "SELECT target_tables_referenced::text[] FROM column_mappings "
            "WHERE source_column_id = 'ocs.general.claim.bene_id'",
        ) == ["ocs.general.bene"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DB-level backstops (Task 23): direct non-loader INSERTs are rejected
# ---------------------------------------------------------------------------

# Valid parent rows in an isolated `zt` namespace (won't collide with the
# committed `ocs`/`edw_prd` corpus). Each case runs inside a transaction that
# is rolled back, so nothing persists.
_TS = "now(), now()"
_DS_ZT = (
    "INSERT INTO data_sources "
    "(data_source_id, owner, description, notes, update_reason, "
    f"insert_ts, update_ts) VALUES ('zt','o','d',NULL,NULL,{_TS})"
)
_SCHEMA_ZT = (
    "INSERT INTO schemas "
    "(schema_id, data_source_id, schema_name, description, notes, "
    f"update_reason, insert_ts, update_ts) VALUES ('zt.s','zt','s','d',"
    f"NULL,NULL,{_TS})"
)
_TABLE_A = (
    "INSERT INTO tables "
    "(table_id, schema_id, table_name, description, notes, update_reason, "
    f"insert_ts, update_ts) VALUES ('zt.s.a','zt.s','a','d',NULL,NULL,{_TS})"
)
_TABLE_B = (
    "INSERT INTO tables "
    "(table_id, schema_id, table_name, description, notes, update_reason, "
    f"insert_ts, update_ts) VALUES ('zt.s.b','zt.s','b','d',NULL,NULL,{_TS})"
)
_REL_COLS = (
    "(table_a_id, table_b_id, relationship_name, join_condition, "
    "cardinality, use_when, notes, validated, validated_ts, update_reason, "
    "insert_ts, update_ts)"
)
_REL_FWD = (
    f"INSERT INTO table_relationships {_REL_COLS} VALUES "
    f"('zt.s.a','zt.s.b','default','x=y',NULL,NULL,NULL,false,NULL,NULL,{_TS})"
)
_REL_REVERSE = (
    f"INSERT INTO table_relationships {_REL_COLS} VALUES "
    f"('zt.s.b','zt.s.a','default','x=y',NULL,NULL,NULL,false,NULL,NULL,{_TS})"
)

# Each case: (id, setup statements applied first, the violating statement,
# the psycopg2 error class the violation must raise). CHECK constraints raise
# CheckViolation; the unique indexes raise UniqueViolation — asserting the
# specific class keeps a case from passing on an unrelated failure.
_BACKSTOP_VIOLATIONS: list[tuple[str, list[str], str, type[psycopg2.Error]]] = [
    (
        "hierarchy_prefix_mismatch",
        [_DS_ZT],
        # schema_id's leading label ('xx') disagrees with data_source_id
        # ('zt'); the FK on data_source_id is still satisfied.
        "INSERT INTO schemas (schema_id, data_source_id, schema_name, "
        "description, notes, update_reason, insert_ts, update_ts) VALUES "
        f"('xx.s','zt','s','d',NULL,NULL,{_TS})",
        psycopg2.errors.CheckViolation,
    ),
    (
        "wrong_leaf_name",
        [_DS_ZT],
        # schema_name ('wrongname') != the id's last label ('s').
        "INSERT INTO schemas (schema_id, data_source_id, schema_name, "
        "description, notes, update_reason, insert_ts, update_ts) VALUES "
        f"('zt.s','zt','wrongname','d',NULL,NULL,{_TS})",
        psycopg2.errors.CheckViolation,
    ),
    (
        "uppercase_id",
        [],
        # A case-variant id breaks the lowercase-identity invariant.
        "INSERT INTO systems (system, description, notes, update_reason, "
        f"insert_ts, update_ts) VALUES ('ZT_SYS','d',NULL,NULL,{_TS})",
        psycopg2.errors.CheckViolation,
    ),
    (
        "duplicate_loaded_ts",
        [
            "INSERT INTO load_audit (commit_sha, inserts, updates, deletes, "
            "loaded_ts) VALUES ('a',0,0,0, TIMESTAMPTZ '2001-01-01 00:00:00+00')"
        ],
        # A second audit row at the same timestamp breaks the UNIQUE index.
        "INSERT INTO load_audit (commit_sha, inserts, updates, deletes, "
        "loaded_ts) VALUES ('b',0,0,0, TIMESTAMPTZ '2001-01-01 00:00:00+00')",
        psycopg2.errors.UniqueViolation,
    ),
    (
        "reverse_orientation_pair",
        [_DS_ZT, _SCHEMA_ZT, _TABLE_A, _TABLE_B, _REL_FWD],
        # The reverse orientation of the same pair+name collides on the
        # unordered-pair UNIQUE index.
        _REL_REVERSE,
        psycopg2.errors.UniqueViolation,
    ),
    (
        "malformed_concept_id",
        [],
        # nlevel 2 (no reserved `concept` segment) fails the shape CHECK.
        "INSERT INTO concepts (concept_id, label, definition, notes, "
        "related_object_ids, update_reason, insert_ts, update_ts) VALUES "
        f"('zt.bad','l','d',NULL,'{{}}'::ltree[],NULL,{_TS})",
        psycopg2.errors.CheckViolation,
    ),
    (
        "over_deep_concept_id",
        [],
        # nlevel 7 (a 5-label anchor) exceeds the deepest anchor shape —
        # a column anchor (6 labels total) — and fails the shape CHECK.
        "INSERT INTO concepts (concept_id, label, definition, notes, "
        "related_object_ids, update_reason, insert_ts, update_ts) VALUES "
        f"('zt.s.t.c.x.concept.n','l','d',NULL,'{{}}'::ltree[],NULL,{_TS})",
        psycopg2.errors.CheckViolation,
    ),
    (
        "update_reason_on_fresh_insert",
        [],
        # A fresh insert (insert_ts = update_ts) may not carry a reason.
        "INSERT INTO systems (system, description, notes, update_reason, "
        f"insert_ts, update_ts) VALUES ('zt_sys','d',NULL,'a reason',{_TS})",
        psycopg2.errors.CheckViolation,
    ),
]


@pytest.mark.parametrize(
    ("setup", "violating", "expected_exc"),
    [pytest.param(s, v, e, id=cid) for cid, s, v, e in _BACKSTOP_VIOLATIONS],
)
def test_direct_insert_violating_backstop_is_rejected(
    integration_db: None,
    setup: list[str],
    violating: str,
    expected_exc: type[psycopg2.Error],
) -> None:
    """Every new declarative backstop rejects a non-loader INSERT that violates it.

    Each case runs in its own transaction and is rolled back, so nothing
    persists (and the isolated `zt` namespace never collides with the
    committed corpus). Asserting the case's specific error class keeps a
    statement that fails for the wrong reason from passing.
    """
    conn = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        cur = conn.cursor()
        for stmt in setup:
            cur.execute(stmt)
        with pytest.raises(expected_exc):
            cur.execute(violating)
    finally:
        conn.rollback()
        conn.close()


def test_direct_insert_table_and_column_anchor_concepts_accepted(
    integration_db: None,
) -> None:
    """The widened concept_id shape CHECK admits table- and column-anchored ids.

    Table-anchored (5 labels) and column-anchored (6 labels) ids are both
    accepted; concepts carry no FK columns, so a direct insert needs no
    parent rows. Rolled back — nothing persists.
    """
    conn = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        cur = conn.cursor()
        for concept_id in ("zt.s.t.concept.n", "zt.s.t.c.concept.n"):
            cur.execute(
                "INSERT INTO concepts (concept_id, label, definition, "
                "notes, related_object_ids, update_reason, insert_ts, "
                "update_ts) VALUES "
                f"('{concept_id}','l','d',NULL,'{{}}'::ltree[],NULL,{_TS})"
            )
        cur.execute(
            "SELECT count(*) FROM concepts WHERE concept_id <@ 'zt'::ltree"
        )
        assert cur.fetchone()[0] == 2
    finally:
        conn.rollback()
        conn.close()


def test_loader_run_satisfies_new_backstops(
    tmp_path: Path, integration_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normal loader run against the rebuilt schema satisfies every new backstop.

    Regression proof that the write path never trips its own constraints. A
    load that violated any CHECK/unique index would raise inside apply_diff's
    transaction and fail here.
    """
    data_root = tmp_path / "data"
    _stage_full_corpus(data_root)
    cfg = _config(tmp_path, data_root)
    conn = psycopg2.connect(**connection_kwargs(TEST_DB, TEST_SCHEMA))
    try:
        _truncate_all(conn)
        _load(cfg, monkeypatch)
        # The load committed, so every backstop accepted the loader's rows.
        assert _count(conn, "load_audit") == 1
        assert _count(conn, "systems") == 2
        assert _count(conn, "concepts") == 1
        # Spot-check the lowercase-identity and update_reason-pairing
        # backstops hold on the freshly written rows.
        assert _scalar(
            conn,
            "SELECT count(*) FROM columns "
            "WHERE column_id::text <> lower(column_id::text)",
        ) == 0
        assert _scalar(
            conn,
            "SELECT count(*) FROM tables "
            "WHERE (update_reason IS NULL) <> (insert_ts = update_ts)",
        ) == 0
    finally:
        conn.close()
