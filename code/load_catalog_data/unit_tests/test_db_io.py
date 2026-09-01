"""Unit tests for db_io.py (venue-free model, 9 main tables)."""

import re
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import psycopg2
import pytest

import db_io
from corpus_diff import Diff, RowChange
from data_model import (
    ColumnMappingRow,
    ColumnRow,
    ConceptRow,
    DataSourceRow,
    DeploymentRow,
    SchemaRow,
    SystemRow,
    TableRelationshipRow,
    TableRow,
    TABLE_ORDER,
)


# The resolved SHA apply_diff now receives as a parameter (resolution moved
# to the orchestrator — see load_catalog_data.run). apply_diff records it
# verbatim in the load_audit row.
_SHA = "testsha0000"

# The DDL file whose constraint declarations the db_io constants must match
# (see the DDL-agreement tests).
_DDL_PATH = (
    Path(db_io.__file__).resolve().parents[2]
    / "code"
    / "apply_ddl"
    / "ddl_catalog"
    / "0001_initial_schema.sql"
)


@pytest.fixture(autouse=True)
def _stub_commit_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default a GITHUB_SHA so the direct resolve_commit_sha tests (and
    any incidental git shell-out) do not depend on the ambient environment.
    apply_diff no longer resolves the SHA itself — it takes it as an
    argument — so this only affects resolve_commit_sha's own tests."""
    monkeypatch.setenv("GITHUB_SHA", "testsha0000")


# connection_kwargs itself is the shared pgconn helper, covered directly
# by code/lib/pgconn/unit_tests/test_pgconn.py; db_io re-exports it for
# the orchestrator and the integration suite (`from db_io import
# connection_kwargs` must keep resolving — collection of those files
# proves it).


# ---------------------------------------------------------------------------
# read_db_state
# ---------------------------------------------------------------------------


def test_read_db_state_issues_nine_selects_and_builds_state(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # One row per main table, in the order of the nine SELECT calls.
    fake_cursor.fetchall.side_effect = [
        [("warehouse", "desc", None, None)],
        [("ocs", "data-ops", "d", None, None)],
        [("ocs.general", "ocs", "general", "d", None, None)],
        [("ocs.general.bene", "ocs.general", "bene", "d", None, None)],
        # columns — positional ColumnRow(*row); ref_table_id last
        # (trailing defaulted field, matching _SELECT_COLUMNS).
        [
            (
                "ocs.general.bene.bene_id",
                "ocs.general.bene",
                "bene_id",
                "TEXT",
                False,
                True,
                "d",
                None,
                None,
                "ref.codes.bene_id_type",
            )
        ],
        # deployment_tables — positional DeploymentRow(*row); pure-facts
        # shape (no notes/update_reason columns).
        [
            (
                "ocs.general.bene",
                "warehouse",
                "ocs",
                "ocs",
                "general",
                "bene",
            )
        ],
        # table_relationships — no system column.
        [
            (
                "ocs.general.bene",
                "ocs.general.claim",
                "default",
                "x=y",
                "many_to_one",
                None,
                None,
                True,
                None,
                None,
            )
        ],
        # column_mappings — no target_system column.
        [
            (
                "ocs.general.bene.bene_id",
                "default",
                ["edw_prd.s.bene"],
                "edw_prd.s.bene.bene_id",
                None,
                None,
                True,
                None,
                None,
            )
        ],
        # concepts — venue-free path-derived id.
        [
            (
                "sandbox_ocs.concept.claim",
                "Claim",
                "A claim.",
                None,
                ["sandbox_ocs.general.clm"],
                None,
            )
        ],
    ]

    state = db_io.read_db_state(fake_conn)

    assert state.systems["warehouse"].system == "warehouse"
    assert state.data_sources["ocs"].owner == "data-ops"
    assert state.schemas["ocs.general"].schema_name == "general"
    assert state.tables["ocs.general.bene"].table_name == "bene"
    assert state.columns["ocs.general.bene.bene_id"].is_nullable is False
    assert state.columns["ocs.general.bene.bene_id"].is_primary_key is True
    assert (
        state.columns["ocs.general.bene.bene_id"].ref_table_id
        == "ref.codes.bene_id_type"
    )
    dep_key = ("ocs.general.bene", "warehouse")
    assert state.deployment_tables[dep_key].physical_table_name == "bene"
    assert state.deployment_tables[dep_key].data_source_id == "ocs"
    rel_key = ("ocs.general.bene", "ocs.general.claim", "default")
    assert state.table_relationships[rel_key].cardinality == "many_to_one"
    cm_key = ("ocs.general.bene.bene_id", "default")
    assert state.column_mappings[cm_key].target_tables_referenced == (
        "edw_prd.s.bene",
    )
    concept = state.concepts["sandbox_ocs.concept.claim"]
    assert concept.concept_id == "sandbox_ocs.concept.claim"
    assert concept.label == "Claim"
    assert concept.definition == "A claim."
    assert concept.related_object_ids == ("sandbox_ocs.general.clm",)

    select_calls = [
        c
        for c in fake_cursor.execute.call_args_list
        if "SELECT" in c.args[0].upper()
    ]
    assert len(select_calls) == 9


def test_read_db_state_handles_null_target_tables_referenced(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    fake_cursor.fetchall.side_effect = [
        [], [], [], [], [], [], [],  # systems..table_relationships
        [
            (
                "ocs.general.bene.bene_id",
                "default",
                None,
                None,
                None,
                "dropped",
                False,
                None,
                None,
            )
        ],
        [],  # concepts
    ]
    state = db_io.read_db_state(fake_conn)
    cm_key = ("ocs.general.bene.bene_id", "default")
    assert state.column_mappings[cm_key].target_tables_referenced == ()


@pytest.mark.parametrize("db_value", [None, []])
def test_read_db_state_handles_null_and_empty_related_object_ids(
    fake_conn: MagicMock, fake_cursor: MagicMock, db_value: list[str] | None
) -> None:
    fake_cursor.fetchall.side_effect = [
        [], [], [], [], [], [], [], [],  # through column_mappings
        [("sandbox_ocs.concept.claim", None, "d", None, db_value, None)],
    ]
    state = db_io.read_db_state(fake_conn)
    concept = state.concepts["sandbox_ocs.concept.claim"]
    assert concept.related_object_ids == ()


# ---------------------------------------------------------------------------
# apply_diff fixtures
# ---------------------------------------------------------------------------


def _system_row(name: str = "warehouse", reason: str | None = None) -> SystemRow:
    return SystemRow(system=name, description="d", notes=None, update_reason=reason)


def _data_source_row(reason: str | None = None) -> DataSourceRow:
    return DataSourceRow(
        data_source_id="ocs",
        owner="data-ops",
        description="d",
        notes=None,
        update_reason=reason,
    )


def _schema_row(reason: str | None = None) -> SchemaRow:
    return SchemaRow(
        schema_id="ocs.general",
        data_source_id="ocs",
        schema_name="general",
        description="d",
        notes=None,
        update_reason=reason,
    )


def _table_row(
    table_id: str = "ocs.general.bene", reason: str | None = None
) -> TableRow:
    return TableRow(
        table_id=table_id,
        schema_id="ocs.general",
        table_name=table_id.rsplit(".", 1)[1],
        description="d",
        notes=None,
        update_reason=reason,
    )


def _column_row(
    reason: str | None = None, ref_table_id: str | None = None
) -> ColumnRow:
    return ColumnRow(
        column_id="ocs.general.bene.bene_id",
        table_id="ocs.general.bene",
        column_name="bene_id",
        data_type="TEXT",
        is_nullable=False,
        is_primary_key=False,
        description="d",
        notes=None,
        update_reason=reason,
        ref_table_id=ref_table_id,
    )


def _dep_key() -> tuple[str, str]:
    return ("ocs.general.bene", "warehouse")


def _dep_row(physical_table_name: str = "bene") -> DeploymentRow:
    return DeploymentRow(
        table_id="ocs.general.bene",
        system="warehouse",
        data_source_id="ocs",
        physical_database_name="ocs",
        physical_schema_name="general",
        physical_table_name=physical_table_name,
    )


def _rel_key() -> tuple[str, str, str]:
    return ("ocs.general.bene", "ocs.general.claim", "default")


def _rel_row(reason: str | None = None) -> TableRelationshipRow:
    return TableRelationshipRow(
        table_a_id="ocs.general.bene",
        table_b_id="ocs.general.claim",
        relationship_name="default",
        join_condition="x=y",
        cardinality="many_to_one",
        use_when=None,
        notes=None,
        validated=True,
        update_reason=reason,
    )


def _cm_key() -> tuple[str, str]:
    return ("ocs.general.bene.bene_id", "default")


def _cm_row(reason: str | None = None) -> ColumnMappingRow:
    return ColumnMappingRow(
        source_column_id="ocs.general.bene.bene_id",
        mapping_name="default",
        target_tables_referenced=("edw_prd.s.bene",),
        target_expression="edw_prd.s.bene.bene_id",
        use_when=None,
        notes=None,
        validated=True,
        update_reason=reason,
    )


_CLAIM_ID = "sandbox_ocs.concept.claim"


def _concept_row(
    reason: str | None = None,
    related: tuple[str, ...] = ("sandbox_ocs.general.clm",),
) -> ConceptRow:
    return ConceptRow(
        concept_id=_CLAIM_ID,
        label="Claim",
        definition="A claim.",
        notes=None,
        related_object_ids=related,
        update_reason=reason,
    )


# ---------------------------------------------------------------------------
# apply_diff
# ---------------------------------------------------------------------------


def test_apply_diff_insert_only_commits(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    diff = Diff(
        inserts=[
            RowChange("systems", "warehouse", None, _system_row("warehouse"))
        ]
    )
    db_io.apply_diff(fake_conn, diff, _SHA, reset_hstry=False)
    fake_conn.commit.assert_called_once()
    fake_conn.rollback.assert_not_called()
    inserts = [
        c
        for c in fake_cursor.execute.call_args_list
        if c.args[0].startswith("INSERT INTO systems ")
    ]
    assert len(inserts) == 1


def test_apply_diff_defers_deployment_tables_constraint_first(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # The load transaction defers the deployment_tables physical-address
    # UNIQUE (so a validated address swap/chain between updated rows
    # settles at commit); it must be the very first statement issued.
    db_io.apply_diff(fake_conn, Diff(), _SHA, reset_hstry=False)
    first = fake_cursor.execute.call_args_list[0].args[0]
    assert first == db_io._SET_CONSTRAINTS_DEFER_DEPLOYMENT_TABLES
    assert first.startswith("SET CONSTRAINTS ")
    assert db_io.DEPLOYMENT_TABLES_PHYSICAL_ADDRESS_CONSTRAINT in first
    assert (
        db_io.DEPLOYMENT_TABLES_PHYSICAL_ADDRESS_CONSTRAINT
        == "deployment_tables_physical_address_key"
    )


def test_apply_diff_issues_both_set_constraints_statements(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # Step 0 defers BOTH deferrable constraints — the deployment_tables
    # physical-address UNIQUE and the columns.ref_table_id FK — before any
    # table writes, so each is enforced once at commit for this transaction.
    db_io.apply_diff(fake_conn, Diff(), _SHA, reset_hstry=False)
    statements = [c.args[0] for c in fake_cursor.execute.call_args_list]
    set_constraints = [
        s
        for s in statements
        if isinstance(s, str) and s.startswith("SET CONSTRAINTS ")
    ]
    assert set_constraints == [
        db_io._SET_CONSTRAINTS_DEFER_DEPLOYMENT_TABLES,
        db_io._SET_CONSTRAINTS_DEFER_COLUMNS_REF_TABLE_ID,
    ]
    second = statements[1]
    assert second == db_io._SET_CONSTRAINTS_DEFER_COLUMNS_REF_TABLE_ID
    assert db_io.COLUMNS_REF_TABLE_ID_FK_CONSTRAINT in second
    assert (
        db_io.COLUMNS_REF_TABLE_ID_FK_CONSTRAINT
        == "columns_ref_table_id_fkey"
    )


def test_apply_diff_link_to_new_table_updates_columns_before_tables_insert(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # The exact phase-ordering scenario the deferral exists for: one MR
    # links an existing column to a NEW ref table (columns UPDATE +
    # tables INSERT). Updates run before inserts, so the UPDATE points at
    # a tables row that does not exist yet at statement time — legal only
    # because both SET CONSTRAINTS ... DEFERRED precede every write.
    new_table = "ref.codes.bene_id_type"
    diff = Diff(
        updates=[
            RowChange(
                "columns",
                "ocs.general.bene.bene_id",
                old=_column_row(),
                new=_column_row(reason="link to ref", ref_table_id=new_table),
            )
        ],
        inserts=[RowChange("tables", new_table, None, _table_row(new_table))],
    )
    db_io.apply_diff(fake_conn, diff, _SHA, reset_hstry=False)
    statements = [c.args[0] for c in fake_cursor.execute.call_args_list]
    defer_idx = statements.index(
        db_io._SET_CONSTRAINTS_DEFER_COLUMNS_REF_TABLE_ID
    )
    update_idx = next(
        i for i, s in enumerate(statements) if s.startswith("UPDATE columns")
    )
    insert_idx = next(
        i
        for i, s in enumerate(statements)
        if s.startswith("INSERT INTO tables ")
    )
    # Deferral first, then the UPDATE that dangles until the INSERT lands.
    assert defer_idx < update_idx < insert_idx
    # The UPDATE really binds the new table id as ref_table_id.
    update_call = next(
        c.args
        for c in fake_cursor.execute.call_args_list
        if c.args[0].startswith("UPDATE columns")
    )
    assert new_table in update_call[1]


def test_apply_diff_update_writes_hstry_then_updates(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    new = _system_row("warehouse", reason="reworded")
    old = _system_row("warehouse", reason=None)
    diff = Diff(updates=[RowChange("systems", "warehouse", old=old, new=new)])
    db_io.apply_diff(fake_conn, diff, _SHA, reset_hstry=False)
    statements = [c.args[0] for c in fake_cursor.execute.call_args_list]
    assert any("INSERT INTO systems_hstry" in s for s in statements)
    assert any(s.startswith("UPDATE systems") for s in statements)
    h_idx = next(i for i, s in enumerate(statements) if "systems_hstry" in s)
    u_idx = next(i for i, s in enumerate(statements) if s.startswith("UPDATE systems"))
    assert h_idx < u_idx


def test_apply_diff_delete_writes_hstry_then_deletes_in_reverse_fk_order(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    diff = Diff(
        deletes=[
            RowChange("systems", "warehouse", old=_system_row("warehouse"), new=None),
            RowChange(
                "tables", "ocs.general.bene", old=_table_row(), new=None
            ),
        ]
    )
    db_io.apply_diff(fake_conn, diff, _SHA, reset_hstry=False)
    statements = [c.args[0] for c in fake_cursor.execute.call_args_list]
    t_idx = next(
        i for i, s in enumerate(statements) if s.startswith("DELETE FROM tables")
    )
    s_idx = next(
        i for i, s in enumerate(statements) if s.startswith("DELETE FROM systems")
    )
    assert t_idx < s_idx
    # The hstry insert must precede its DELETE: once the row is gone there
    # is nothing left to copy into history.
    t_hstry_idx = next(
        i for i, s in enumerate(statements) if "tables_hstry" in s
    )
    s_hstry_idx = next(
        i for i, s in enumerate(statements) if "systems_hstry" in s
    )
    assert t_hstry_idx < t_idx
    assert s_hstry_idx < s_idx


def test_apply_diff_deployment_delete_precedes_tables_delete(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # deployment_tables references tables, so its DELETE must run before
    # the tables DELETE (reverse FK order — children before parents).
    diff = Diff(
        deletes=[
            RowChange("tables", "ocs.general.bene", old=_table_row(), new=None),
            RowChange(
                "deployment_tables", _dep_key(), old=_dep_row(), new=None
            ),
        ]
    )
    db_io.apply_diff(fake_conn, diff, _SHA, reset_hstry=False)
    statements = [c.args[0] for c in fake_cursor.execute.call_args_list]
    d_idx = next(
        i
        for i, s in enumerate(statements)
        if s.startswith("DELETE FROM deployment_tables")
    )
    t_idx = next(
        i for i, s in enumerate(statements) if s.startswith("DELETE FROM tables")
    )
    assert d_idx < t_idx


def test_apply_diff_relationship_update_includes_all_three_pk_parts(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    diff = Diff(
        updates=[
            RowChange(
                "table_relationships", _rel_key(), old=_rel_row(),
                new=_rel_row(reason="reworded"),
            )
        ]
    )
    db_io.apply_diff(fake_conn, diff, _SHA, reset_hstry=False)
    update_call = next(
        c.args
        for c in fake_cursor.execute.call_args_list
        if c.args[0].startswith("UPDATE table_relationships")
    )
    assert "table_a_id=%s" in update_call[0]
    assert "table_b_id=%s" in update_call[0]
    assert "relationship_name=%s" in update_call[0]
    assert update_call[1][-3:] == _rel_key()


def test_apply_diff_deployment_update_includes_both_pk_parts(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    diff = Diff(
        updates=[
            RowChange(
                "deployment_tables", _dep_key(), old=_dep_row(),
                new=_dep_row(physical_table_name="bene_v2"),
            )
        ]
    )
    db_io.apply_diff(fake_conn, diff, _SHA, reset_hstry=False)
    update_call = next(
        c.args
        for c in fake_cursor.execute.call_args_list
        if c.args[0].startswith("UPDATE deployment_tables")
    )
    assert "table_id=%s" in update_call[0]
    assert "system=%s" in update_call[0]
    assert update_call[1][-2:] == _dep_key()


def test_apply_diff_column_mapping_delete_includes_both_pk_parts(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    diff = Diff(
        deletes=[
            RowChange("column_mappings", _cm_key(), old=_cm_row(), new=None)
        ]
    )
    db_io.apply_diff(fake_conn, diff, _SHA, reset_hstry=False)
    delete_call = next(
        c.args
        for c in fake_cursor.execute.call_args_list
        if c.args[0].startswith("DELETE FROM column_mappings")
    )
    assert "source_column_id=%s" in delete_call[0]
    assert "mapping_name=%s" in delete_call[0]
    assert "target_system" not in delete_call[0]
    assert delete_call[1] == _cm_key()


def test_apply_diff_reset_hstry_truncates_all_nine_tables(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    db_io.apply_diff(fake_conn, Diff(), _SHA, reset_hstry=True)
    truncate_calls = [
        c
        for c in fake_cursor.execute.call_args_list
        if "TRUNCATE TABLE" in repr(c.args[0])
    ]
    assert len(truncate_calls) == 9
    assert "deployment_tables_hstry" in db_io._HSTRY_TABLES
    assert "concepts_hstry" in db_io._HSTRY_TABLES
    assert any(
        "deployment_tables_hstry" in repr(c.args[0]) for c in truncate_calls
    )
    fake_conn.commit.assert_called_once()


def test_apply_diff_rolls_back_on_exception_and_reraises(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    fake_cursor.execute.side_effect = psycopg2.Error("boom")
    diff = Diff(
        inserts=[RowChange("systems", "warehouse", None, _system_row("warehouse"))]
    )
    with pytest.raises(psycopg2.Error):
        db_io.apply_diff(fake_conn, diff, _SHA, reset_hstry=False)
    fake_conn.rollback.assert_called_once()
    fake_conn.commit.assert_not_called()


def test_apply_diff_uses_passed_sha_and_does_not_resolve_internally(
    fake_conn: MagicMock, fake_cursor: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Resolution moved to the orchestrator: apply_diff must record the SHA it
    # is handed and never call resolve_commit_sha itself (so it would not
    # even notice a broken resolver).
    def _boom() -> str:
        raise AssertionError("apply_diff must not resolve the SHA itself")

    monkeypatch.setattr(db_io, "resolve_commit_sha", _boom)
    diff = Diff(
        inserts=[RowChange("systems", "warehouse", None, _system_row("warehouse"))]
    )
    db_io.apply_diff(fake_conn, diff, "handed-sha-9999", reset_hstry=False)
    fake_conn.commit.assert_called_once()
    audit_calls = [
        c
        for c in fake_cursor.execute.call_args_list
        if c.args[0].startswith("INSERT INTO load_audit ")
    ]
    assert len(audit_calls) == 1
    # The passed SHA is the first bound parameter of the load_audit insert.
    assert audit_calls[0].args[1][0] == "handed-sha-9999"


@pytest.mark.parametrize(
    ("table", "row_factory", "reason_index"),
    [
        ("systems", lambda: _system_row(reason="x"), 3),
        ("data_sources", lambda: _data_source_row(reason="x"), 4),
        ("schemas", lambda: _schema_row(reason="x"), 5),
        ("tables", lambda: _table_row(reason="x"), 5),
        ("columns", lambda: _column_row(reason="x"), 8),
        ("table_relationships", lambda: _rel_row(reason="x"), 8),
        ("column_mappings", lambda: _cm_row(reason="x"), 7),
        ("concepts", lambda: _concept_row(reason="x"), 5),
    ],
)
def test_insert_params_bind_update_reason_null_even_when_row_has_value(
    table: str, row_factory: Any, reason_index: int
) -> None:
    # A fresh insert never records a reason (there is nothing to explain
    # the change of), regardless of what the row carries — apply_diff is
    # self-defending against a caller that skipped validate_update_reason.
    params = db_io._insert_params(table, row_factory())
    assert params[reason_index] is None


def test_resolve_commit_sha_warns_on_dirty_working_tree(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Local fallback (GITHUB_SHA unset) with a dirty tree emits a WARNING
    # lineage caveat without failing.
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def _fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> MagicMock:
        result = MagicMock()
        result.returncode = 0
        if cmd[:2] == ["git", "rev-parse"]:
            result.stdout = "deadbeef\n"
            result.stderr = ""
        else:  # git status --porcelain
            result.stdout = " M data_catalog/sources/ocs/data_source.yaml\n"
            result.stderr = ""
        return result

    monkeypatch.setattr(db_io.subprocess, "run", _fake_run)
    with caplog.at_level("WARNING"):
        assert db_io.resolve_commit_sha() == "deadbeef"
    assert any(
        "working tree is dirty" in r.message for r in caplog.records
    )


def test_resolve_commit_sha_silent_on_clean_working_tree(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def _fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> MagicMock:
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        # rev-parse -> sha; status --porcelain -> clean (empty).
        result.stdout = "deadbeef\n" if cmd[:2] == ["git", "rev-parse"] else ""
        return result

    monkeypatch.setattr(db_io.subprocess, "run", _fake_run)
    with caplog.at_level("WARNING"):
        assert db_io.resolve_commit_sha() == "deadbeef"
    assert not any(
        "working tree is dirty" in r.message for r in caplog.records
    )


@pytest.mark.parametrize(
    "status_error",
    [
        pytest.param(FileNotFoundError("git"), id="git_missing"),
        pytest.param(
            subprocess.TimeoutExpired(
                cmd="git status --porcelain", timeout=30
            ),
            id="git_status_timeout",
        ),
    ],
)
def test_resolve_commit_sha_returns_sha_when_dirty_check_raises(
    status_error: Exception,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The dirty-tree check is diagnostic, not a gate: if the `git status`
    # leg dies, resolve_commit_sha must still return the SHA rev-parse
    # already produced rather than failing the load.
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def _fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> MagicMock:
        if cmd[:2] == ["git", "rev-parse"]:
            result = MagicMock()
            result.returncode = 0
            result.stdout = "deadbeef\n"
            result.stderr = ""
            return result
        raise status_error

    monkeypatch.setattr(db_io.subprocess, "run", _fake_run)
    with caplog.at_level("WARNING"):
        assert db_io.resolve_commit_sha() == "deadbeef"
    assert not any(
        "working tree is dirty" in r.message for r in caplog.records
    )


def test_resolve_commit_sha_silent_when_dirty_check_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A nonzero `git status` (e.g. not a work tree) is not evidence of a
    # dirty tree, so no WARNING is emitted and the SHA still comes back.
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def _fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> MagicMock:
        result = MagicMock()
        result.stderr = ""
        if cmd[:2] == ["git", "rev-parse"]:
            result.returncode = 0
            result.stdout = "deadbeef\n"
        else:  # git status --porcelain fails
            result.returncode = 128
            result.stdout = "fatal: not a git repository\n"
        return result

    monkeypatch.setattr(db_io.subprocess, "run", _fake_run)
    with caplog.at_level("WARNING"):
        assert db_io.resolve_commit_sha() == "deadbeef"
    assert not any(
        "working tree is dirty" in r.message for r in caplog.records
    )


def _one_insert_per_table_diff() -> Diff:
    """One insert RowChange per main table, exercising every _insert_params
    branch."""
    return Diff(
        inserts=[
            RowChange("systems", "warehouse", None, _system_row()),
            RowChange("data_sources", "ocs", None, _data_source_row()),
            RowChange("schemas", "ocs.general", None, _schema_row()),
            RowChange("tables", "ocs.general.bene", None, _table_row()),
            RowChange(
                "columns", "ocs.general.bene.bene_id", None, _column_row()
            ),
            RowChange("deployment_tables", _dep_key(), None, _dep_row()),
            RowChange("table_relationships", _rel_key(), None, _rel_row()),
            RowChange("column_mappings", _cm_key(), None, _cm_row()),
            RowChange("concepts", _CLAIM_ID, None, _concept_row()),
        ]
    )


def test_apply_diff_runs_all_nine_table_inserts(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    db_io.apply_diff(fake_conn, _one_insert_per_table_diff(), _SHA, reset_hstry=False)
    fake_conn.commit.assert_called_once()
    statements = [c.args[0] for c in fake_cursor.execute.call_args_list]
    main_inserts = [
        s
        for s in statements
        if s.startswith("INSERT INTO ")
        and "_hstry" not in s
        and not s.startswith("INSERT INTO load_audit")
    ]
    assert len(main_inserts) == 9
    assert any(
        s.startswith("INSERT INTO deployment_tables ") for s in statements
    )
    assert any(s.startswith("INSERT INTO concepts ") for s in statements)


def test_apply_diff_runs_all_nine_table_updates_and_deletes(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    updates = [
        RowChange("systems", "warehouse", _system_row(), _system_row(reason="r")),
        RowChange(
            "data_sources", "ocs", _data_source_row(),
            _data_source_row(reason="r"),
        ),
        RowChange(
            "schemas", "ocs.general", _schema_row(), _schema_row(reason="r")
        ),
        RowChange(
            "tables", "ocs.general.bene", _table_row(), _table_row(reason="r")
        ),
        RowChange(
            "columns", "ocs.general.bene.bene_id", _column_row(),
            _column_row(reason="r"),
        ),
        RowChange(
            "deployment_tables", _dep_key(), _dep_row(),
            _dep_row(physical_table_name="bene_v2"),
        ),
        RowChange(
            "table_relationships", _rel_key(), _rel_row(), _rel_row(reason="r")
        ),
        RowChange("column_mappings", _cm_key(), _cm_row(), _cm_row(reason="r")),
        RowChange("concepts", _CLAIM_ID, _concept_row(), _concept_row(reason="r")),
    ]
    deletes = [
        RowChange("systems", "warehouse", _system_row(), None),
        RowChange("data_sources", "ocs", _data_source_row(), None),
        RowChange("schemas", "ocs.general", _schema_row(), None),
        RowChange("tables", "ocs.general.bene", _table_row(), None),
        RowChange("columns", "ocs.general.bene.bene_id", _column_row(), None),
        RowChange("deployment_tables", _dep_key(), _dep_row(), None),
        RowChange("table_relationships", _rel_key(), _rel_row(), None),
        RowChange("column_mappings", _cm_key(), _cm_row(), None),
        RowChange("concepts", _CLAIM_ID, _concept_row(), None),
    ]
    db_io.apply_diff(
        fake_conn, Diff(updates=updates, deletes=deletes), _SHA,
        reset_hstry=False,
    )
    fake_conn.commit.assert_called_once()
    statements = [c.args[0] for c in fake_cursor.execute.call_args_list]
    update_stmts = [s for s in statements if s.startswith("UPDATE ")]
    delete_stmts = [s for s in statements if s.startswith("DELETE FROM ")]
    hstry_inserts = [
        s for s in statements if s.startswith("INSERT INTO ") and "_hstry (" in s
    ]
    assert len(update_stmts) == 9
    assert len(delete_stmts) == 9
    assert len(hstry_inserts) == 18  # one per update + one per delete
    assert any(s.startswith("UPDATE deployment_tables ") for s in statements)
    assert any(
        s.startswith("DELETE FROM deployment_tables ") for s in statements
    )


# ---------------------------------------------------------------------------
# error paths in _insert_params / _update_params / _pk_params
# ---------------------------------------------------------------------------


def test_insert_params_unknown_table_raises() -> None:
    with pytest.raises(ValueError, match="Unknown table"):
        db_io._insert_params("nope", _system_row())


def test_update_params_unknown_table_raises() -> None:
    with pytest.raises(ValueError, match="Unknown table"):
        db_io._update_params("nope", _system_row(), None, "k")


def test_pk_params_unknown_table_raises() -> None:
    with pytest.raises(ValueError, match="Unknown table"):
        db_io._pk_params("nope", "k")


@pytest.mark.parametrize("phase", ["deletes", "updates", "inserts"])
def test_apply_diff_unknown_table_raises_value_error(
    phase: str, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # Bucketing a RowChange for a table outside _FK_ORDER must name the
    # table like the param builders do, not raise a bare KeyError that
    # load_catalog_data.main misreports as a missing config field.
    change = RowChange("nope", "k", old=None, new=_system_row())
    diff = Diff(**{phase: [change]})
    with pytest.raises(ValueError, match="Unknown table nope"):
        db_io.apply_diff(fake_conn, diff, _SHA, reset_hstry=False)


# ---------------------------------------------------------------------------
# SQL-constant contracts
# ---------------------------------------------------------------------------

# _INSERT_COLUMNS order: column_id, table_id, column_name, data_type,
# is_nullable, is_primary_key, description, notes, update_reason, ref_table_id.
COLUMNS_INSERT_IS_NULLABLE_IDX = 4
COLUMNS_INSERT_IS_PRIMARY_KEY_IDX = 5
# _UPDATE_COLUMNS SET order: table_id, column_name, data_type, is_nullable,
# is_primary_key, description, notes, update_reason, ref_table_id, <WHERE key>.
COLUMNS_UPDATE_IS_NULLABLE_IDX = 3
COLUMNS_UPDATE_IS_PRIMARY_KEY_IDX = 4
# column_mappings UPDATE params: ttr, target_expression, use_when, notes,
# validated, update_reason, stamp_now, else_value, k0, k1.
CM_UPDATE_STAMP_NOW_IDX = 6
CM_UPDATE_ELSE_VALUE_IDX = 7
# table_relationships UPDATE params (no system column): join_condition,
# cardinality, use_when, notes, validated, update_reason, stamp_now,
# else_value, k0, k1, k2.
REL_UPDATE_STAMP_NOW_IDX = 6
REL_UPDATE_ELSE_VALUE_IDX = 7


def test_columns_sql_carries_is_primary_key() -> None:
    for stmt in (
        db_io._SELECT_COLUMNS,
        db_io._INSERT_COLUMNS,
        db_io._UPDATE_COLUMNS,
        db_io._HSTRY_INSERT_COLUMNS,
    ):
        assert "is_primary_key" in stmt


def test_columns_sql_carries_ref_table_id() -> None:
    # ref_table_id is read, written, updated, and mirrored to _hstry like
    # any other content column.
    for stmt in (
        db_io._SELECT_COLUMNS,
        db_io._INSERT_COLUMNS,
        db_io._UPDATE_COLUMNS,
        db_io._HSTRY_INSERT_COLUMNS,
    ):
        assert "ref_table_id" in stmt
    # _SELECT_COLUMNS order feeds positional ColumnRow(*row):
    # ref_table_id must be the last selected column (the dataclass's
    # trailing defaulted field).
    select_list = db_io._SELECT_COLUMNS.split("SELECT ")[1].split(" FROM ")[0]
    assert select_list.split(", ")[-1] == "ref_table_id"


def test_columns_insert_and_update_params_carry_ref_table_id() -> None:
    # INSERT params: ..., description, notes, update_reason(None),
    # ref_table_id — matching _INSERT_COLUMNS' column list.
    insert_params = db_io._insert_params(
        "columns", _column_row(ref_table_id="ref.codes.bene_id_type")
    )
    assert insert_params[-1] == "ref.codes.bene_id_type"
    # UPDATE params: SET columns then the WHERE key — ref_table_id sits
    # right before the key, matching _UPDATE_COLUMNS' SET order.
    update_params = db_io._update_params(
        "columns",
        _column_row(reason="r", ref_table_id="ref.codes.bene_id_type"),
        None,
        "ocs.general.bene.bene_id",
    )
    assert update_params[-2] == "ref.codes.bene_id_type"
    assert update_params[-1] == "ocs.general.bene.bene_id"


def test_columns_null_ref_table_id_binds_none() -> None:
    # A column with no domain pointer binds NULL on both write paths.
    assert db_io._insert_params("columns", _column_row())[-1] is None
    update_params = db_io._update_params(
        "columns", _column_row(reason="r"), None, "ocs.general.bene.bene_id"
    )
    assert update_params[-2] is None


def test_columns_insert_params_carry_is_primary_key() -> None:
    row = ColumnRow(
        "ocs.general.bene.bene_id", "ocs.general.bene", "bene_id", "TEXT",
        False, True, "d", None, None,
    )
    params = db_io._insert_params("columns", row)
    assert params[COLUMNS_INSERT_IS_NULLABLE_IDX] is False
    assert params[COLUMNS_INSERT_IS_PRIMARY_KEY_IDX] is True


def test_columns_update_params_carry_is_primary_key() -> None:
    row = ColumnRow(
        "ocs.general.bene.bene_id", "ocs.general.bene", "bene_id", "TEXT",
        False, True, "d", None, "r",
    )
    params = db_io._update_params(
        "columns", row, None, "ocs.general.bene.bene_id"
    )
    assert params[COLUMNS_UPDATE_IS_NULLABLE_IDX] is False
    assert params[COLUMNS_UPDATE_IS_PRIMARY_KEY_IDX] is True
    assert params[-1] == "ocs.general.bene.bene_id"


def test_data_sources_sql_carries_owner_not_system_or_database_name() -> None:
    for stmt in (
        db_io._SELECT_DATA_SOURCES,
        db_io._INSERT_DATA_SOURCES,
        db_io._UPDATE_DATA_SOURCES,
        db_io._HSTRY_INSERT_DATA_SOURCES,
    ):
        assert "owner" in stmt
        assert "database_name" not in stmt
        # `system` must not appear as a data_sources column any longer.
        assert "system" not in stmt


def test_data_sources_insert_params_positional_order() -> None:
    params = db_io._insert_params("data_sources", _data_source_row())
    assert params == ("ocs", "data-ops", "d", None, None)


def test_deployment_tables_sql_and_dispatch_wired() -> None:
    # The DELETE is keyed by PK only, so it carries no physical-name
    # columns. Pure-facts shape: no notes/update_reason in any statement.
    for stmt in (
        db_io._SELECT_DEPLOYMENT_TABLES,
        db_io._INSERT_DEPLOYMENT_TABLES,
        db_io._UPDATE_DEPLOYMENT_TABLES,
        db_io._HSTRY_INSERT_DEPLOYMENT_TABLES,
    ):
        assert "physical_database_name" in stmt
        assert "physical_schema_name" in stmt
        assert "physical_table_name" in stmt
        assert "notes" not in stmt
        assert "update_reason" not in stmt
        assert "deployment_tables" in stmt
    assert db_io._DELETE_DEPLOYMENT_TABLES.startswith(
        "DELETE FROM deployment_tables"
    )
    assert db_io._HSTRY_INSERT_DEPLOYMENT_TABLES.startswith(
        "INSERT INTO deployment_tables_hstry"
    )
    assert (
        db_io._INSERT_SQL["deployment_tables"]
        is db_io._INSERT_DEPLOYMENT_TABLES
    )
    assert (
        db_io._UPDATE_SQL["deployment_tables"]
        is db_io._UPDATE_DEPLOYMENT_TABLES
    )
    assert (
        db_io._DELETE_SQL["deployment_tables"]
        is db_io._DELETE_DEPLOYMENT_TABLES
    )
    assert (
        db_io._HSTRY_INSERT_SQL["deployment_tables"]
        is db_io._HSTRY_INSERT_DEPLOYMENT_TABLES
    )
    assert "deployment_tables" in db_io._FK_ORDER


def test_deployment_tables_insert_params_positional_order() -> None:
    params = db_io._insert_params("deployment_tables", _dep_row())
    assert params == (
        "ocs.general.bene",
        "warehouse",
        "ocs",
        "ocs",
        "general",
        "bene",
    )


def test_deployment_tables_update_params_set_then_pk() -> None:
    params = db_io._update_params(
        "deployment_tables",
        _dep_row(physical_table_name="bene_v2"),
        None,
        _dep_key(),
    )
    # data_source_id, physical_database_name, physical_schema_name,
    # physical_table_name, table_id, system.
    assert params == (
        "ocs",
        "ocs",
        "general",
        "bene_v2",
        "ocs.general.bene",
        "warehouse",
    )


def test_deployment_tables_pk_params_two_parts() -> None:
    assert db_io._pk_params("deployment_tables", _dep_key()) == _dep_key()


def test_column_mappings_sql_casts_array_to_ltree() -> None:
    assert "::ltree[]" in db_io._INSERT_COLUMN_MAPPINGS
    assert "::ltree[]" in db_io._UPDATE_COLUMN_MAPPINGS


def test_column_mappings_sql_drops_target_system() -> None:
    for stmt in (
        db_io._SELECT_COLUMN_MAPPINGS,
        db_io._INSERT_COLUMN_MAPPINGS,
        db_io._UPDATE_COLUMN_MAPPINGS,
        db_io._DELETE_COLUMN_MAPPINGS,
        db_io._HSTRY_INSERT_COLUMN_MAPPINGS,
    ):
        assert "target_system" not in stmt
        assert "source_system" not in stmt
        assert "mapping_name" in stmt


def test_column_mappings_pk_params_two_parts() -> None:
    assert db_io._pk_params("column_mappings", _cm_key()) == _cm_key()


def test_concepts_insert_params_positional_order() -> None:
    params = db_io._insert_params("concepts", _concept_row())
    assert params == (
        _CLAIM_ID,
        "Claim",
        "A claim.",
        None,
        ["sandbox_ocs.general.clm"],
        None,
    )


def test_concepts_update_params_set_columns_then_pk() -> None:
    params = db_io._update_params(
        "concepts", _concept_row(reason="r"), None, _CLAIM_ID
    )
    assert params == (
        "Claim",
        "A claim.",
        None,
        ["sandbox_ocs.general.clm"],
        "r",
        _CLAIM_ID,
    )


def test_concepts_empty_related_object_ids_binds_empty_list() -> None:
    insert_params = db_io._insert_params("concepts", _concept_row(related=()))
    assert insert_params[4] == []
    update_params = db_io._update_params(
        "concepts", _concept_row(reason="r", related=()), None, _CLAIM_ID
    )
    assert update_params[3] == []


def test_concepts_sql_carries_related_object_ids_with_ltree_cast() -> None:
    assert "related_object_ids::text[]" in db_io._SELECT_CONCEPTS
    assert "%s::ltree[]" in db_io._INSERT_CONCEPTS
    assert "related_object_ids=%s::ltree[]" in db_io._UPDATE_CONCEPTS
    assert "related_object_ids" in db_io._HSTRY_INSERT_CONCEPTS


def test_relationship_sql_drops_system_and_carries_cardinality() -> None:
    for stmt in (
        db_io._SELECT_TABLE_RELATIONSHIPS,
        db_io._INSERT_TABLE_RELATIONSHIPS,
        db_io._UPDATE_TABLE_RELATIONSHIPS,
        db_io._HSTRY_INSERT_TABLE_RELATIONSHIPS,
    ):
        assert "cardinality" in stmt
        assert "join_type" not in stmt
        # No `system` column on relationships any longer.
        assert "system" not in stmt


def test_relationship_insert_and_update_params_carry_cardinality() -> None:
    # _INSERT_TABLE_RELATIONSHIPS binds cardinality at index 4 (after
    # join_condition, no system); _UPDATE at index 1.
    assert db_io._insert_params("table_relationships", _rel_row())[4] == "many_to_one"
    params = db_io._update_params(
        "table_relationships", _rel_row(reason="r"), _rel_row(), _rel_key()
    )
    assert params[1] == "many_to_one"


def test_concepts_pk_params_single_key() -> None:
    assert db_io._pk_params("concepts", _CLAIM_ID) == (_CLAIM_ID,)


def test_concepts_dispatch_dicts_wired() -> None:
    assert db_io._INSERT_SQL["concepts"] is db_io._INSERT_CONCEPTS
    assert db_io._UPDATE_SQL["concepts"] is db_io._UPDATE_CONCEPTS
    assert db_io._DELETE_SQL["concepts"] is db_io._DELETE_CONCEPTS
    assert db_io._HSTRY_INSERT_SQL["concepts"] is db_io._HSTRY_INSERT_CONCEPTS
    assert "concepts" in db_io._FK_ORDER


def test_concepts_sql_has_no_validated_and_carries_content_columns() -> None:
    for stmt in (
        db_io._SELECT_CONCEPTS,
        db_io._INSERT_CONCEPTS,
        db_io._UPDATE_CONCEPTS,
        db_io._HSTRY_INSERT_CONCEPTS,
    ):
        assert "validated" not in stmt
        assert "definition" in stmt
    assert "SET label=" in db_io._UPDATE_CONCEPTS
    assert "WHERE concept_id=%s" in db_io._UPDATE_CONCEPTS


def test_hstry_insert_sql_carries_validated_ts() -> None:
    assert "validated_ts" in db_io._HSTRY_INSERT_COLUMN_MAPPINGS
    assert "validated_ts" in db_io._HSTRY_INSERT_TABLE_RELATIONSHIPS


# ---------------------------------------------------------------------------
# resolve_commit_sha
# ---------------------------------------------------------------------------


def test_resolve_commit_sha_prefers_ci_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    assert db_io.resolve_commit_sha() == "abc123"


def test_resolve_commit_sha_falls_back_to_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    fake = MagicMock(returncode=0, stdout="deadbeef\n", stderr="")
    monkeypatch.setattr(db_io.subprocess, "run", lambda *a, **k: fake)
    assert db_io.resolve_commit_sha() == "deadbeef"


def test_resolve_commit_sha_raises_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    fake = MagicMock(returncode=128, stdout="", stderr="not a git repo")
    monkeypatch.setattr(db_io.subprocess, "run", lambda *a, **k: fake)
    with pytest.raises(RuntimeError, match="could not resolve commit SHA"):
        db_io.resolve_commit_sha()


def test_resolve_commit_sha_missing_git_binary_maps_to_runtimeerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def _no_git(*a: object, **k: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(db_io.subprocess, "run", _no_git)
    with pytest.raises(RuntimeError, match="git executable was not found"):
        db_io.resolve_commit_sha()


def test_resolve_commit_sha_pins_repo_root_cwd_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The subprocess must run in THIS repo's root (derived from the
    # module location, never the process cwd — a loader started from
    # another directory would otherwise record the wrong repo's HEAD)
    # and carry a timeout ceiling.
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    captured: dict[str, object] = {}

    def _capture(*a: object, **k: object) -> MagicMock:
        captured.update(k)
        return MagicMock(returncode=0, stdout="deadbeef\n", stderr="")

    monkeypatch.setattr(db_io.subprocess, "run", _capture)
    assert db_io.resolve_commit_sha() == "deadbeef"
    assert captured["cwd"] == db_io._REPO_ROOT
    assert db_io._REPO_ROOT == Path(db_io.__file__).resolve().parents[2]
    assert captured["timeout"] == db_io._GIT_TIMEOUT_SECONDS


def test_resolve_commit_sha_whitespace_ci_sha_falls_back_to_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A whitespace-only GITHUB_SHA strips to empty and falls back to
    # `git rev-parse HEAD` — never returned as a blank SHA.
    monkeypatch.setenv("GITHUB_SHA", "   \t")
    fake = MagicMock(returncode=0, stdout="cafef00d\n", stderr="")
    monkeypatch.setattr(db_io.subprocess, "run", lambda *a, **k: fake)
    assert db_io.resolve_commit_sha() == "cafef00d"


def test_resolve_commit_sha_timeout_maps_to_runtimeerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def _hang(*a: object, **k: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git rev-parse HEAD", timeout=30)

    monkeypatch.setattr(db_io.subprocess, "run", _hang)
    with pytest.raises(RuntimeError, match="timed out"):
        db_io.resolve_commit_sha()


# ---------------------------------------------------------------------------
# DDL agreement — the deferred-constraint name literals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "constraint",
    [
        pytest.param(
            db_io.DEPLOYMENT_TABLES_PHYSICAL_ADDRESS_CONSTRAINT,
            id="deployment_tables_physical_address",
        ),
        pytest.param(
            db_io.COLUMNS_REF_TABLE_ID_FK_CONSTRAINT,
            id="columns_ref_table_id_fk",
        ),
    ],
)
def test_deferred_constraint_names_match_ddl(constraint: str) -> None:
    # apply_diff defers these constraints by name every load; each
    # literal MUST match the DDL's declaration, so a future rename
    # fails this unit test instead of every load (mirrors
    # test_pk_agreement's DDL-vs-code pattern).
    ddl_text = _DDL_PATH.read_text(encoding="utf-8")
    pattern = r"constraint\s+" + re.escape(constraint) + r"\b"
    assert re.search(pattern, ddl_text, flags=re.IGNORECASE), (
        f"constraint {constraint!r} not declared in {_DDL_PATH}"
    )


# ---------------------------------------------------------------------------
# data_model agreement — the table-name tuples
# ---------------------------------------------------------------------------


def test_fk_order_and_hstry_tables_match_table_order() -> None:
    # corpus_diff.compute_diff emits RowChange.table values drawn from
    # data_model.TABLE_ORDER, and apply_diff buckets them by _FK_ORDER
    # (with _HSTRY_TABLES driving the reset truncate). A table added to
    # TABLE_ORDER but missed here must fail this test rather than every
    # load. Set comparison for _FK_ORDER: its ORDERING is FK-driven and
    # free to diverge, only its MEMBERSHIP must agree.
    assert set(db_io._FK_ORDER) == set(TABLE_ORDER)
    assert len(db_io._FK_ORDER) == len(TABLE_ORDER)
    assert db_io._HSTRY_TABLES == tuple(f"{t}_hstry" for t in TABLE_ORDER)


# ---------------------------------------------------------------------------
# load_audit write
# ---------------------------------------------------------------------------


def test_apply_diff_writes_load_audit_row_with_counts(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    diff = Diff(
        inserts=[RowChange("systems", "warehouse", None, _system_row("warehouse"))]
    )
    # The SHA recorded is the one handed to apply_diff (not the environment).
    db_io.apply_diff(fake_conn, diff, "sha-xyz", reset_hstry=False)
    audit = [
        c
        for c in fake_cursor.execute.call_args_list
        if c.args[0].startswith("INSERT INTO load_audit")
    ]
    assert len(audit) == 1
    assert audit[0].args[1] == ("sha-xyz", 1, 0, 0, False)


def test_apply_diff_empty_diff_still_writes_load_audit_heartbeat(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    db_io.apply_diff(fake_conn, Diff(), _SHA, reset_hstry=False)
    audit = [
        c
        for c in fake_cursor.execute.call_args_list
        if c.args[0].startswith("INSERT INTO load_audit")
    ]
    assert len(audit) == 1
    assert audit[0].args[1][1:] == (0, 0, 0, False)


# ---------------------------------------------------------------------------
# validated_ts transition stamping (via _update_params)
# ---------------------------------------------------------------------------


def _cm(
    validated: bool, validated_ts: str | None = None, reason: str | None = None
) -> ColumnMappingRow:
    return ColumnMappingRow(
        source_column_id="ocs.general.bene.bene_id",
        mapping_name="default",
        target_tables_referenced=("edw_prd.s.bene",),
        target_expression="edw_prd.s.bene.bene_id",
        use_when=None,
        notes=None,
        validated=validated,
        update_reason=reason,
        validated_ts=validated_ts,
    )


def _rel(
    validated: bool, validated_ts: str | None = None, reason: str | None = None
) -> TableRelationshipRow:
    return TableRelationshipRow(
        table_a_id="ocs.general.bene",
        table_b_id="ocs.general.claim",
        relationship_name="default",
        join_condition="x=y",
        cardinality=None,
        use_when=None,
        notes=None,
        validated=validated,
        update_reason=reason,
        validated_ts=validated_ts,
    )


def test_validated_ts_stamped_on_false_to_true() -> None:
    p = db_io._update_params("column_mappings", _cm(True), _cm(False), _cm_key())
    assert p[CM_UPDATE_STAMP_NOW_IDX] is True


def test_validated_ts_restamped_when_validated_but_ts_missing() -> None:
    p = db_io._update_params(
        "column_mappings", _cm(True, reason="r"), _cm(True, validated_ts=None),
        _cm_key(),
    )
    assert p[CM_UPDATE_STAMP_NOW_IDX] is True


def test_validated_ts_nulled_on_true_to_false() -> None:
    p = db_io._update_params(
        "column_mappings", _cm(False), _cm(True, "2020-01-01"), _cm_key()
    )
    assert p[CM_UPDATE_STAMP_NOW_IDX] is False
    assert p[CM_UPDATE_ELSE_VALUE_IDX] is None


def test_validated_ts_preserved_when_staying_true() -> None:
    p = db_io._update_params(
        "column_mappings", _cm(True, reason="r"), _cm(True, "2020-01-01"),
        _cm_key(),
    )
    assert p[CM_UPDATE_STAMP_NOW_IDX] is False
    assert p[CM_UPDATE_ELSE_VALUE_IDX] == "2020-01-01"


def test_validated_ts_null_when_staying_false() -> None:
    p = db_io._update_params("column_mappings", _cm(False), _cm(False), _cm_key())
    assert p[CM_UPDATE_STAMP_NOW_IDX] is False
    assert p[CM_UPDATE_ELSE_VALUE_IDX] is None


def test_validated_ts_stamped_on_false_to_true_relationships() -> None:
    p = db_io._update_params(
        "table_relationships", _rel(True), _rel(False), _rel_key()
    )
    assert p[REL_UPDATE_STAMP_NOW_IDX] is True


def test_validated_ts_preserved_when_staying_true_relationships() -> None:
    p = db_io._update_params(
        "table_relationships", _rel(True, reason="r"), _rel(True, "2020-01-01"),
        _rel_key(),
    )
    assert p[REL_UPDATE_STAMP_NOW_IDX] is False
    assert p[REL_UPDATE_ELSE_VALUE_IDX] == "2020-01-01"
