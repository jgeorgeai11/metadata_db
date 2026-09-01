"""Unit tests for apply_ddl.py.

The database boundary (psycopg2) is mocked; filesystem interactions use
pytest's tmp_path so the real file logic runs. run() is tested by mocking
its leaf helpers, which are themselves unit-tested individually. The
static-DDL-invariant tests at the bottom read the shipped catalog and
ref DDL (0001_initial_schema.sql, the ddl_ref migrations) and the
per-role grant scripts as text.
"""

import logging
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import apply_ddl

_APPLY_DDL_DIR = Path(apply_ddl.__file__).resolve().parent
_DDL_0001 = _APPLY_DDL_DIR / "ddl_catalog" / "0001_initial_schema.sql"
_DDL_REF_0001 = _APPLY_DDL_DIR / "ddl_ref" / "0001_ref_initial.sql"
_GRANTS_DIR = _APPLY_DDL_DIR / "grants"
_GRANT_SCRIPT = _GRANTS_DIR / "metadata_db_ci.sql"
_GRANT_SCRIPT_RO = _GRANTS_DIR / "metadata_db_ci_ro.sql"


def _migrations(*versions: str) -> list[tuple[str, Path]]:
    """Build a list of (version, path) tuples for run() tests."""
    return [(v, Path(f"{v}_x.sql")) for v in versions]


# ---------------------------------------------------------------------------
# list_repo_migrations
# ---------------------------------------------------------------------------


def test_list_repo_migrations_sorted_returns_versions(tmp_path: Path) -> None:
    (tmp_path / "0002_second.sql").write_text("select 1;")
    (tmp_path / "0001_first.sql").write_text("select 1;")

    result = apply_ddl.list_repo_migrations(tmp_path)

    assert [v for v, _ in result] == ["0001", "0002"]
    assert [p.name for _, p in result] == ["0001_first.sql", "0002_second.sql"]


def test_list_repo_migrations_unpadded_prefixes_returns_numeric_order(
    tmp_path: Path,
) -> None:
    # Unpadded numeric prefixes must sort numerically (1, 2, 10), not
    # lexically (1, 10, 2).
    (tmp_path / "2_b.sql").write_text("select 1;")
    (tmp_path / "10_c.sql").write_text("select 1;")
    (tmp_path / "1_a.sql").write_text("select 1;")

    result = apply_ddl.list_repo_migrations(tmp_path)

    assert [v for v, _ in result] == ["1", "2", "10"]


def test_list_repo_migrations_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert apply_ddl.list_repo_migrations(tmp_path) == []


def test_list_repo_migrations_missing_dir_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError, match="DDL directory not found"):
        apply_ddl.list_repo_migrations(missing)


def test_list_repo_migrations_bad_filename_raises(tmp_path: Path) -> None:
    (tmp_path / "no_prefix.sql").write_text("select 1;")
    with pytest.raises(ValueError, match="numeric prefix"):
        apply_ddl.list_repo_migrations(tmp_path)


def test_list_repo_migrations_duplicate_version_raises(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("select 1;")
    (tmp_path / "0001_b.sql").write_text("select 1;")
    with pytest.raises(ValueError, match="Duplicate"):
        apply_ddl.list_repo_migrations(tmp_path)


def test_list_repo_migrations_numerically_equal_duplicate_raises(
    tmp_path: Path,
) -> None:
    # Textually-distinct prefixes that parse to the same integer ("0001"
    # and "1") must be rejected — otherwise both would be applied.
    (tmp_path / "0001_a.sql").write_text("select 1;")
    (tmp_path / "1_b.sql").write_text("select 1;")
    with pytest.raises(ValueError, match="Duplicate"):
        apply_ddl.list_repo_migrations(tmp_path)


# connection_kwargs itself is the shared pgconn helper, covered directly
# by code/lib/pgconn/unit_tests/test_pgconn.py; the run() tests below
# patch `apply_ddl.connection_kwargs` (the module-level import), which is
# where this module looks the name up.


# ---------------------------------------------------------------------------
# ensure_ddl_versions / ensure_schema / ddl_versions_exists / schema_present /
# has_schema_usage / applied_migrations
# ---------------------------------------------------------------------------


def test_ensure_ddl_versions_creates_and_commits(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    apply_ddl.ensure_ddl_versions(fake_conn)

    # Two statements: the CREATE TABLE bootstrap and its table comment
    # (the comment lives here, not in 0001 — ddl_versions is this
    # script's own table).
    assert fake_cursor.execute.call_count == 2
    create_sql = fake_cursor.execute.call_args_list[0][0][0]
    assert "create table if not exists ddl_versions" in create_sql
    assert "checksum text not null" in create_sql
    comment_sql = fake_cursor.execute.call_args_list[1][0][0]
    assert "comment on table ddl_versions" in comment_sql
    fake_conn.commit.assert_called_once()


def test_ensure_ddl_versions_pins_applied_ts_column() -> None:
    # The docs' ddl_versions snippet mirrors this exact column shape; pin
    # it so a drive-by type change fails a unit test, not the docs.
    import inspect

    source = inspect.getsource(apply_ddl.ensure_ddl_versions)
    assert "applied_ts timestamptz not null default now()" in source


def test_ensure_schema_creates_and_commits(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    apply_ddl.ensure_schema(fake_conn, "catalog")

    fake_cursor.execute.assert_called_once()
    # The statement is a psycopg2.sql.Composed (Identifier-injected), so it
    # isn't a plain string — match on its repr like the TRUNCATE tests do.
    create_sql = repr(fake_cursor.execute.call_args[0][0])
    assert "create schema if not exists" in create_sql
    assert "catalog" in create_sql
    fake_conn.commit.assert_called_once()


def test_ensure_schema_applies_comment_when_configured(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # The optional schema_comment knob lands as COMMENT ON SCHEMA with the
    # name Identifier-injected and the text as a bound parameter.
    apply_ddl.ensure_schema(fake_conn, "catalog", "The metadata_db catalog")

    assert fake_cursor.execute.call_count == 2
    comment_call = fake_cursor.execute.call_args_list[1]
    assert "comment on schema" in repr(comment_call[0][0])
    assert "catalog" in repr(comment_call[0][0])
    assert comment_call[0][1] == ("The metadata_db catalog",)
    fake_conn.commit.assert_called_once()


def test_ensure_schema_no_comment_when_knob_absent(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # None (knob absent) means no COMMENT statement — any existing comment
    # is left untouched.
    apply_ddl.ensure_schema(fake_conn, "catalog", None)

    assert fake_cursor.execute.call_count == 1
    assert "comment on schema" not in repr(fake_cursor.execute.call_args[0][0])


def test_ddl_versions_exists_true_when_present(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # to_regclass returns the table's regclass (non-null) when it exists.
    fake_cursor.fetchone.return_value = ("ddl_versions",)

    assert apply_ddl.ddl_versions_exists(fake_conn) is True
    fake_conn.commit.assert_not_called()


def test_ddl_versions_exists_false_when_absent(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # to_regclass returns NULL when the table doesn't exist.
    fake_cursor.fetchone.return_value = (None,)

    assert apply_ddl.ddl_versions_exists(fake_conn) is False
    fake_conn.commit.assert_not_called()


def test_schema_present_true_when_exists(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # pg_namespace yields a row when a schema of this name exists.
    fake_cursor.fetchone.return_value = (1,)

    assert apply_ddl.schema_present(fake_conn, "catalog") is True
    # Read-only probe: the schema name is the only bound parameter.
    assert fake_cursor.execute.call_args[0][1] == ("catalog",)
    fake_conn.commit.assert_not_called()


def test_schema_present_false_when_absent(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # No matching pg_namespace row on a fresh database.
    fake_cursor.fetchone.return_value = None

    assert apply_ddl.schema_present(fake_conn, "catalog") is False
    fake_conn.commit.assert_not_called()


def test_has_schema_usage_true_when_granted(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # has_schema_privilege(..., 'USAGE') returns True when the role holds USAGE.
    fake_cursor.fetchone.return_value = (True,)

    assert apply_ddl.has_schema_usage(fake_conn, "catalog") is True
    # Read-only probe: the schema name is the only bound parameter.
    assert fake_cursor.execute.call_args[0][1] == ("catalog",)
    fake_conn.commit.assert_not_called()


def test_has_schema_usage_false_when_denied(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # False when the role lacks USAGE on the (existing) schema; the bool(...)
    # parse must collapse the single-column row to a plain boolean.
    fake_cursor.fetchone.return_value = (False,)

    assert apply_ddl.has_schema_usage(fake_conn, "catalog") is False
    fake_conn.commit.assert_not_called()


def test_applied_migrations_returns_version_checksum_map(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    fake_cursor.fetchall.return_value = [("0001", "sum1"), ("0002", "sum2")]

    assert apply_ddl.applied_migrations(fake_conn) == {
        "0001": "sum1",
        "0002": "sum2",
    }


def test_applied_migrations_empty_returns_empty_dict(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    fake_cursor.fetchall.return_value = []

    assert apply_ddl.applied_migrations(fake_conn) == {}


# ---------------------------------------------------------------------------
# apply_one
# ---------------------------------------------------------------------------


def test_apply_one_executes_migration_and_records_version(
    tmp_path: Path, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    migration = tmp_path / "0001_test.sql"
    migration.write_text("create table foo (id int);")

    apply_ddl.apply_one(fake_conn, "0001", migration)

    # First execute runs the migration body; second records the version
    # plus its content checksum.
    assert fake_cursor.execute.call_count == 2
    assert "create table foo" in fake_cursor.execute.call_args_list[0][0][0]
    insert_call = fake_cursor.execute.call_args_list[1]
    assert "insert into ddl_versions" in insert_call[0][0]
    assert "checksum" in insert_call[0][0]
    version_arg, checksum_arg = insert_call[0][1]
    assert version_arg == "0001"
    assert checksum_arg == apply_ddl.compute_checksum(migration)
    fake_conn.commit.assert_called_once()


def test_apply_one_rolls_back_on_error(
    tmp_path: Path, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    migration = tmp_path / "0001_test.sql"
    migration.write_text("bad sql;")
    fake_cursor.execute.side_effect = apply_ddl.psycopg2.Error("boom")

    with pytest.raises(apply_ddl.psycopg2.Error):
        apply_ddl.apply_one(fake_conn, "0001", migration)

    fake_conn.rollback.assert_called_once()
    fake_conn.commit.assert_not_called()


# ---------------------------------------------------------------------------
# compute_checksum / verify_checksums
# ---------------------------------------------------------------------------


def test_compute_checksum_content_sensitive(tmp_path: Path) -> None:
    a = tmp_path / "a.sql"
    a.write_text("create table foo (id int);")
    b = tmp_path / "b.sql"
    b.write_text("create table bar (id int);")

    assert apply_ddl.compute_checksum(a) == apply_ddl.compute_checksum(a)
    assert apply_ddl.compute_checksum(a) != apply_ddl.compute_checksum(b)


def test_compute_checksum_line_ending_normalized(tmp_path: Path) -> None:
    # Same logical content, LF vs CRLF on disk, must hash identically so
    # the immutability check is stable across Windows/Linux checkouts.
    lf = tmp_path / "lf.sql"
    lf.write_bytes(b"create table t (id int);\nselect 1;\n")
    crlf = tmp_path / "crlf.sql"
    crlf.write_bytes(b"create table t (id int);\r\nselect 1;\r\n")

    assert apply_ddl.compute_checksum(lf) == apply_ddl.compute_checksum(crlf)


def test_compute_checksum_cr_only_normalized(tmp_path: Path) -> None:
    # Classic-Mac CR-only endings are the third universal-newline form;
    # they must normalize to the same digest as LF.
    lf = tmp_path / "lf.sql"
    lf.write_bytes(b"create table t (id int);\nselect 1;\n")
    cr = tmp_path / "cr.sql"
    cr.write_bytes(b"create table t (id int);\rselect 1;\r")

    assert apply_ddl.compute_checksum(lf) == apply_ddl.compute_checksum(cr)


def test_verify_checksums_matching_passes(tmp_path: Path) -> None:
    m = tmp_path / "0001_x.sql"
    m.write_text("select 1;")

    # Should not raise.
    apply_ddl.verify_checksums({"0001": m}, {"0001": apply_ddl.compute_checksum(m)})


def test_verify_checksums_mismatch_raises(tmp_path: Path) -> None:
    m = tmp_path / "0001_x.sql"
    m.write_text("select 1;")

    with pytest.raises(RuntimeError, match="append-only"):
        apply_ddl.verify_checksums({"0001": m}, {"0001": "deadbeef"})


def test_verify_checksums_missing_from_repo_skipped() -> None:
    # A version absent from the repo is the append-only check's concern,
    # not verify_checksums'; it must not raise here.
    apply_ddl.verify_checksums({}, {"0001": "somesum"})


def test_verify_checksums_multiple_violations_all_named(tmp_path: Path) -> None:
    # Every edited migration is named (sorted) in one message, so a
    # multi-file violation is fixed in one round-trip.
    m1 = tmp_path / "0001_x.sql"
    m1.write_text("select 1;")
    m2 = tmp_path / "0002_y.sql"
    m2.write_text("select 2;")

    with pytest.raises(RuntimeError) as exc:
        apply_ddl.verify_checksums(
            {"0001": m1, "0002": m2},
            {"0002": "stale2", "0001": "stale1"},
        )

    message = str(exc.value)
    assert "['0001', '0002']" in message
    assert "append-only" in message


# ---------------------------------------------------------------------------
# create_database_if_absent
# ---------------------------------------------------------------------------


def test_create_database_creates_when_absent(
    monkeypatch: pytest.MonkeyPatch, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    fake_cursor.fetchone.return_value = None  # database not found
    mock_connect = MagicMock(return_value=fake_conn)
    monkeypatch.setattr(apply_ddl.psycopg2, "connect", mock_connect)

    apply_ddl.create_database_if_absent(
        {"host": "h", "port": "5432", "user": "u", "password": "p", "dbname": "mydb"}
    )

    # The maintenance connection must override dbname to "postgres" — CREATE
    # DATABASE cannot run from within the target database.
    assert mock_connect.call_args.kwargs["dbname"] == "postgres"
    # SELECT, then CREATE DATABASE.
    assert fake_cursor.execute.call_count == 2
    # Existence check is parameterized on the target database name.
    assert fake_cursor.execute.call_args_list[0][0][1] == ("mydb",)
    assert fake_conn.autocommit is True
    fake_conn.close.assert_called_once()


def test_create_database_error_propagates_and_closes_connection(
    monkeypatch: pytest.MonkeyPatch, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # A CREATE DATABASE failure (e.g. missing CREATEDB privilege) must
    # propagate as psycopg2.Error and still close the maintenance
    # connection via the finally block.
    fake_cursor.execute.side_effect = apply_ddl.psycopg2.Error("no CREATEDB")
    monkeypatch.setattr(
        apply_ddl.psycopg2, "connect", MagicMock(return_value=fake_conn)
    )

    with pytest.raises(apply_ddl.psycopg2.Error, match="no CREATEDB"):
        apply_ddl.create_database_if_absent(
            {"host": "h", "port": "5432", "user": "u", "password": "p", "dbname": "mydb"}
        )

    fake_conn.close.assert_called_once()


def test_create_database_skips_when_present(
    monkeypatch: pytest.MonkeyPatch, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    fake_cursor.fetchone.return_value = (1,)  # database exists
    mock_connect = MagicMock(return_value=fake_conn)
    monkeypatch.setattr(apply_ddl.psycopg2, "connect", mock_connect)

    apply_ddl.create_database_if_absent(
        {"host": "h", "port": "5432", "user": "u", "password": "p", "dbname": "mydb"}
    )

    # The maintenance connection must override dbname to "postgres".
    assert mock_connect.call_args.kwargs["dbname"] == "postgres"
    # Only the existence SELECT ran; no CREATE DATABASE.
    assert fake_cursor.execute.call_count == 1
    # Existence check is parameterized on the target database name.
    assert fake_cursor.execute.call_args_list[0][0][1] == ("mydb",)
    fake_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# run — orchestration / branching
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replace run()'s leaf helpers with mocks; return them keyed by name."""
    mocks = {
        "connection_kwargs": MagicMock(return_value={"dbname": "mydb"}),
        "list_repo_migrations": MagicMock(),
        "create_database_if_absent": MagicMock(),
        "ensure_schema": MagicMock(),
        "ensure_ddl_versions": MagicMock(),
        "ddl_versions_exists": MagicMock(return_value=True),
        "schema_present": MagicMock(return_value=True),
        "has_schema_usage": MagicMock(return_value=True),
        "applied_migrations": MagicMock(),
        "verify_checksums": MagicMock(),
        "apply_one": MagicMock(),
        "connect": MagicMock(return_value=MagicMock()),
    }
    monkeypatch.setattr(apply_ddl, "schema_present", mocks["schema_present"])
    monkeypatch.setattr(
        apply_ddl, "has_schema_usage", mocks["has_schema_usage"]
    )
    monkeypatch.setattr(apply_ddl, "connection_kwargs", mocks["connection_kwargs"])
    monkeypatch.setattr(
        apply_ddl, "list_repo_migrations", mocks["list_repo_migrations"]
    )
    monkeypatch.setattr(
        apply_ddl, "create_database_if_absent", mocks["create_database_if_absent"]
    )
    monkeypatch.setattr(apply_ddl, "ensure_schema", mocks["ensure_schema"])
    monkeypatch.setattr(
        apply_ddl, "ensure_ddl_versions", mocks["ensure_ddl_versions"]
    )
    monkeypatch.setattr(
        apply_ddl, "ddl_versions_exists", mocks["ddl_versions_exists"]
    )
    monkeypatch.setattr(
        apply_ddl, "applied_migrations", mocks["applied_migrations"]
    )
    monkeypatch.setattr(
        apply_ddl, "verify_checksums", mocks["verify_checksums"]
    )
    monkeypatch.setattr(apply_ddl, "apply_one", mocks["apply_one"])
    monkeypatch.setattr(apply_ddl.psycopg2, "connect", mocks["connect"])
    return mocks


def _config() -> dict[str, str]:
    """Build a minimal valid parsed-config dict for run() tests."""
    return {"ddl_dir": "x", "database": "mydb", "schema": "catalog"}


def test_run_check_in_sync_does_not_apply(patched_run: dict[str, MagicMock]) -> None:
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=True, create_db=False)

    patched_run["apply_one"].assert_not_called()


def test_run_check_pending_exits_nonzero(
    patched_run: dict[str, MagicMock],
) -> None:
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {}

    with pytest.raises(SystemExit) as exc:
        apply_ddl.run(_config(), check_only=True, create_db=False)

    assert exc.value.code == 1
    # Read-only contract: check mode must never apply a migration.
    patched_run["apply_one"].assert_not_called()


def test_run_check_mode_does_not_write(
    patched_run: dict[str, MagicMock],
) -> None:
    # No-writes contract: --check must never call ensure_ddl_versions
    # (which does create-table-if-not-exists + commit).
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=True, create_db=False)

    patched_run["ensure_ddl_versions"].assert_not_called()


def test_run_check_mode_absent_table_treats_all_pending(
    patched_run: dict[str, MagicMock],
) -> None:
    # Fresh DB: the tracking table doesn't exist yet, so --check must treat
    # every repo migration as pending and exit non-zero without writing.
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["ddl_versions_exists"].return_value = False

    with pytest.raises(SystemExit) as exc:
        apply_ddl.run(_config(), check_only=True, create_db=False)

    assert exc.value.code == 1
    # Absent table means nothing applied; applied_migrations must not run.
    patched_run["applied_migrations"].assert_not_called()
    patched_run["ensure_ddl_versions"].assert_not_called()
    patched_run["apply_one"].assert_not_called()


def test_run_apply_mode_ensures_table(
    patched_run: dict[str, MagicMock],
) -> None:
    # Apply mode still bootstraps the tracking table via ensure_ddl_versions.
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=False, create_db=False)

    patched_run["ensure_ddl_versions"].assert_called_once()


def test_run_apply_mode_creates_schema_before_ddl_versions(
    patched_run: dict[str, MagicMock],
) -> None:
    # The target schema must be created (with the configured name) before the
    # ddl_versions table so the tracking table lands inside the schema.
    call_order: list[str] = []
    patched_run["ensure_schema"].side_effect = (
        lambda *a, **k: call_order.append("schema")
    )
    patched_run["ensure_ddl_versions"].side_effect = (
        lambda *a, **k: call_order.append("versions")
    )
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=False, create_db=False)

    patched_run["ensure_schema"].assert_called_once()
    # The configured schema name is passed through as the second arg.
    assert patched_run["ensure_schema"].call_args[0][1] == "catalog"
    assert call_order == ["schema", "versions"]


def test_run_apply_mode_forwards_schema_comment(
    patched_run: dict[str, MagicMock],
) -> None:
    # The schema_comment config knob only reaches the database through
    # this forward, and ensure_schema applies it silently — without this
    # assertion, dropping the third argument would leave the suite green.
    config = {**_config(), "schema_comment": "The metadata_db catalog"}
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(config, check_only=False, create_db=False)

    call_args = patched_run["ensure_schema"].call_args
    assert call_args[0][2] == "The metadata_db catalog"


def test_run_apply_mode_omitted_schema_comment_forwards_none(
    patched_run: dict[str, MagicMock],
) -> None:
    # The knob is optional: absent from the config, ensure_schema must be
    # told there is no comment rather than receiving the schema name twice.
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=False, create_db=False)

    assert patched_run["ensure_schema"].call_args[0][2] is None


def test_run_check_mode_does_not_create_schema(
    patched_run: dict[str, MagicMock],
) -> None:
    # No-writes contract: --check must never create the schema (a write).
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=True, create_db=False)

    patched_run["ensure_schema"].assert_not_called()


def test_run_missing_schema_config_raises_keyerror(
    patched_run: dict[str, MagicMock],
) -> None:
    # A config without `schema` must trip the KeyError config-field path.
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    with pytest.raises(KeyError, match="schema"):
        apply_ddl.run(
            {"ddl_dir": "x", "database": "mydb"},
            check_only=False,
            create_db=False,
        )


def test_run_applies_only_pending(patched_run: dict[str, MagicMock]) -> None:
    patched_run["list_repo_migrations"].return_value = _migrations("0001", "0002")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=False, create_db=False)

    # Only 0002 is pending; apply_one(conn, version, path) -> version is [0][1].
    assert patched_run["apply_one"].call_count == 1
    assert patched_run["apply_one"].call_args[0][1] == "0002"


def test_run_applies_pending_preserving_list_order(
    patched_run: dict[str, MagicMock],
) -> None:
    # run() applies pending migrations in the order list_repo_migrations
    # returns them; the numeric sort itself lives in — and is tested for —
    # list_repo_migrations (see the unpadded-prefixes test above).
    patched_run["list_repo_migrations"].return_value = _migrations(
        "0001", "0002", "0003"
    )
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=False, create_db=False)

    applied_order = [
        call[0][1] for call in patched_run["apply_one"].call_args_list
    ]
    assert applied_order == ["0002", "0003"]


def test_run_no_pending_skips_apply(patched_run: dict[str, MagicMock]) -> None:
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=False, create_db=False)

    patched_run["apply_one"].assert_not_called()


def test_run_unknown_db_version_raises(patched_run: dict[str, MagicMock]) -> None:
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0099": "c99"}

    with pytest.raises(RuntimeError, match="not present in repo"):
        apply_ddl.run(_config(), check_only=False, create_db=False)


def test_run_check_mode_unknown_db_version_raises(
    patched_run: dict[str, MagicMock],
) -> None:
    # The append-only rule is enforced on the pre-merge CI path too:
    # --check refuses when the DB records a version the repo lacks
    # (someone deleted or renamed a migration file).
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0099": "c99"}

    with pytest.raises(RuntimeError, match="not present in repo"):
        apply_ddl.run(_config(), check_only=True, create_db=False)

    patched_run["apply_one"].assert_not_called()


def test_run_create_db_calls_create(patched_run: dict[str, MagicMock]) -> None:
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=False, create_db=True)

    patched_run["create_database_if_absent"].assert_called_once()


def test_run_create_db_skipped_in_check_mode(
    patched_run: dict[str, MagicMock],
) -> None:
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=True, create_db=True)

    patched_run["create_database_if_absent"].assert_not_called()


def test_run_verifies_checksums_in_check_mode(
    patched_run: dict[str, MagicMock],
) -> None:
    # Immutability must be enforced in --check too, so the MR pipeline
    # surfaces an edited migration before merge.
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(_config(), check_only=True, create_db=False)

    patched_run["verify_checksums"].assert_called_once()


def test_run_propagates_checksum_violation(
    patched_run: dict[str, MagicMock],
) -> None:
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "stale"}
    patched_run["verify_checksums"].side_effect = RuntimeError(
        "append-only violation"
    )

    with pytest.raises(RuntimeError, match="append-only"):
        apply_ddl.run(_config(), check_only=False, create_db=False)

    # A detected violation must abort before any migration is applied.
    patched_run["apply_one"].assert_not_called()


# ---------------------------------------------------------------------------
# check_no_transaction_control / strip_sql_comments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("select 1;\ncommit;", id="commit"),
        pytest.param("begin;\nselect 1;", id="begin"),
        pytest.param("select 1;\nrollback;", id="rollback"),
        pytest.param("start transaction;\nselect 1;", id="start_transaction"),
        pytest.param("SELECT 1;\nCOMMIT;", id="uppercase_commit"),
    ],
)
def test_check_no_transaction_control_rejects_bare_statement(body: str) -> None:
    # An embedded transaction-control statement would split the migration's
    # single-transaction atomicity — refuse it before execution.
    with pytest.raises(ValueError, match="transaction-control"):
        apply_ddl.check_no_transaction_control(body, "0002_x.sql")


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("select 1;\n-- remember to commit later\n", id="line"),
        pytest.param("/* rollback plan here */\nselect 1;", id="block"),
        pytest.param(
            "-- start transaction below\nselect 1;", id="line_start_txn"
        ),
    ],
)
def test_check_no_transaction_control_ignores_keyword_in_comment(
    body: str,
) -> None:
    # A keyword mentioned only inside a comment is stripped before scanning,
    # so it must not trip the guard.
    apply_ddl.check_no_transaction_control(body, "0002_x.sql")


def test_check_no_transaction_control_allows_plpgsql_do_block() -> None:
    # A PL/pgSQL `begin`/`end` inside a `do $$ ... $$` block is a block
    # keyword, not transaction control — it is preceded by the dollar-quote,
    # not a `;`, so the statement-boundary scan must not flag it. This mirrors
    # the version-assert block now shipped in 0001.
    body = (
        "do $$\nbegin\n"
        "    if current_setting('server_version_num')::int < 160000 then\n"
        "        raise exception 'too old';\n"
        "    end if;\nend\n$$;\n"
        "create table foo (id int);"
    )
    apply_ddl.check_no_transaction_control(body, "0001_initial_schema.sql")


def test_check_no_transaction_control_allows_commit_sha_identifier() -> None:
    # The word boundary must not flag `commit_sha` (an identifier), only a
    # standalone `commit` statement.
    apply_ddl.check_no_transaction_control(
        "create table t (commit_sha text);", "0002_x.sql"
    )


def test_strip_sql_comments_removes_line_and_block_comments() -> None:
    # Pin the strip's contract directly (not just via the guard tests above):
    # a `--` comment is removed to end of line with the newline kept, a
    # `/* */` comment is removed even when it spans lines, and the
    # surrounding SQL is untouched.
    sql = (
        "select 1; -- remember to commit later\n"
        "/* rollback\n   plan here */\n"
        "select 2;"
    )

    stripped = apply_ddl.strip_sql_comments(sql)

    assert stripped == "select 1; \n\nselect 2;"


def test_apply_one_refuses_transaction_control_before_execution(
    tmp_path: Path, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # The guard runs in apply_one before any cur.execute — a bad migration
    # never touches the database.
    migration = tmp_path / "0002_bad.sql"
    migration.write_text("create table foo (id int);\ncommit;")

    with pytest.raises(ValueError, match="transaction-control"):
        apply_ddl.apply_one(fake_conn, "0002", migration)

    fake_cursor.execute.assert_not_called()
    fake_conn.commit.assert_not_called()


# ---------------------------------------------------------------------------
# list_repo_migrations — case-variant .sql extension
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        pytest.param("0002_x.SQL", id="upper"),
        pytest.param("0002_x.Sql", id="mixed"),
    ],
)
def test_list_repo_migrations_case_variant_extension_raises(
    tmp_path: Path, bad_name: str
) -> None:
    # A case-variant .sql extension is a naming mistake, not a silent skip:
    # on a case-sensitive filesystem glob("*.sql") would never see it.
    (tmp_path / "0001_first.sql").write_text("select 1;")
    (tmp_path / bad_name).write_text("select 1;")

    with pytest.raises(ValueError, match="non-lowercase SQL extension"):
        apply_ddl.list_repo_migrations(tmp_path)


def test_list_repo_migrations_lowercase_sql_unaffected(tmp_path: Path) -> None:
    # Lowercase .sql files, and unrelated extensions, are handled normally
    # (the latter simply ignored).
    (tmp_path / "0001_first.sql").write_text("select 1;")
    (tmp_path / "notes.txt").write_text("ignore me")
    # A directory entry is skipped before the extension/prefix checks —
    # iterdir() surfaces directories, glob("*.sql") would not.
    (tmp_path / "0002_wip.sql").mkdir()

    result = apply_ddl.list_repo_migrations(tmp_path)

    assert [p.name for _, p in result] == ["0001_first.sql"]


# ---------------------------------------------------------------------------
# run — --allow-pending exemption and permissions-vs-absent split
# ---------------------------------------------------------------------------


def test_run_check_allow_pending_exempts_named_file(
    patched_run: dict[str, MagicMock],
) -> None:
    # A migration MR exempts its own newly added file: check passes even
    # though the file is repo-present-but-unapplied.
    patched_run["list_repo_migrations"].return_value = _migrations("0001", "0002")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    # Must not raise SystemExit.
    apply_ddl.run(
        _config(),
        check_only=True,
        create_db=False,
        allow_pending=["0002_x.sql"],
    )

    patched_run["apply_one"].assert_not_called()


def test_run_check_allow_pending_still_fails_other_pending(
    patched_run: dict[str, MagicMock],
) -> None:
    # Exempting one file does not exempt a different pending migration.
    patched_run["list_repo_migrations"].return_value = _migrations(
        "0001", "0002", "0003"
    )
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    with pytest.raises(SystemExit) as exc:
        apply_ddl.run(
            _config(),
            check_only=True,
            create_db=False,
            allow_pending=["0002_x.sql"],
        )

    assert exc.value.code == 1


def test_run_check_allow_pending_unused_entry_warns(
    patched_run: dict[str, MagicMock], caplog: pytest.LogCaptureFixture
) -> None:
    # An exemption matching nothing pending is almost always a typo in the
    # CI-computed list; it does not fail the check, so the WARNING is the
    # only signal a maintainer gets.
    caplog.set_level(logging.WARNING)
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["applied_migrations"].return_value = {"0001": "c1"}

    apply_ddl.run(
        _config(),
        check_only=True,
        create_db=False,
        allow_pending=["0099_typo.sql"],
    )

    assert "match no pending migration" in caplog.text
    assert "0099_typo.sql" in caplog.text


def test_run_check_absent_schema_no_privilege_raises(
    patched_run: dict[str, MagicMock],
) -> None:
    # ddl_versions invisible (to_regclass NULL) because the role lacks USAGE
    # on an existing schema is a permissions error, not "all pending".
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["ddl_versions_exists"].return_value = False
    patched_run["schema_present"].return_value = True
    patched_run["has_schema_usage"].return_value = False

    with pytest.raises(RuntimeError, match="USAGE on schema"):
        apply_ddl.run(_config(), check_only=True, create_db=False)


def test_run_check_absent_schema_genuinely_missing_all_pending(
    patched_run: dict[str, MagicMock],
) -> None:
    # A genuinely absent schema (fresh DB) is not a permissions problem: it
    # falls through to "all pending" and exits non-zero.
    patched_run["list_repo_migrations"].return_value = _migrations("0001")
    patched_run["ddl_versions_exists"].return_value = False
    patched_run["schema_present"].return_value = False

    with pytest.raises(SystemExit) as exc:
        apply_ddl.run(_config(), check_only=True, create_db=False)

    assert exc.value.code == 1
    # An absent schema needs no privilege probe.
    patched_run["has_schema_usage"].assert_not_called()


def test_main_allow_pending_plumbs_to_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Repeatable --allow-pending flags arrive as a list on run().
    config_file = tmp_path / "cfg.toml"
    config_file.write_text('ddl_dir = "x"\ndatabase = "mydb"\n')
    monkeypatch.setattr(apply_ddl, "setup_logging", lambda *a, **k: None)
    mock_run = MagicMock(return_value=None)
    monkeypatch.setattr(apply_ddl, "run", mock_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply_ddl.py",
            "--config",
            str(config_file),
            "--check",
            "--allow-pending",
            "0002_a.sql",
            "--allow-pending",
            "0003_b.sql",
        ],
    )

    apply_ddl.main()

    assert mock_run.call_args.kwargs["allow_pending"] == [
        "0002_a.sql",
        "0003_b.sql",
    ]


# ---------------------------------------------------------------------------
# main — argument / config error paths
# ---------------------------------------------------------------------------


def test_main_config_not_found_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(apply_ddl, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(
        sys, "argv", ["apply_ddl.py", "--config", "/no/such/file.toml"]
    )

    with pytest.raises(SystemExit) as exc:
        apply_ddl.main()

    assert exc.value.code == 1
    assert "Config file not found" in caplog.text


def test_main_run_failure_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    config_file = tmp_path / "cfg.toml"
    config_file.write_text('ddl_dir = "x"\ndatabase = "mydb"\n')
    monkeypatch.setattr(apply_ddl, "setup_logging", lambda *a, **k: None)

    def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(apply_ddl, "run", _boom)
    monkeypatch.setattr(
        sys, "argv", ["apply_ddl.py", "--config", str(config_file)]
    )

    with pytest.raises(SystemExit) as exc:
        apply_ddl.main()

    assert exc.value.code == 1
    # The RuntimeError arm logs the bare exception text.
    assert "simulated failure" in caplog.text


def test_main_success_does_not_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "cfg.toml"
    config_file.write_text('ddl_dir = "x"\ndatabase = "mydb"\n')
    monkeypatch.setattr(apply_ddl, "setup_logging", lambda *a, **k: None)
    mock_run = MagicMock(return_value=None)
    monkeypatch.setattr(apply_ddl, "run", mock_run)
    monkeypatch.setattr(
        sys, "argv", ["apply_ddl.py", "--config", str(config_file)]
    )

    # Should complete without raising SystemExit, and must dispatch to run().
    apply_ddl.main()

    mock_run.assert_called_once()


def test_main_bad_toml_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    config_file = tmp_path / "cfg.toml"
    config_file.write_text("this = = not valid toml ===")
    monkeypatch.setattr(apply_ddl, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(
        sys, "argv", ["apply_ddl.py", "--config", str(config_file)]
    )

    with pytest.raises(SystemExit) as exc:
        apply_ddl.main()

    assert exc.value.code == 1
    assert "Failed to read config file" in caplog.text


def test_main_missing_config_field_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    config_file = tmp_path / "cfg.toml"
    config_file.write_text('ddl_dir = "x"\ndatabase = "mydb"\n')
    monkeypatch.setattr(apply_ddl, "setup_logging", lambda *a, **k: None)

    def _missing_field(*a: object, **k: object) -> None:
        raise KeyError("database")

    monkeypatch.setattr(apply_ddl, "run", _missing_field)
    monkeypatch.setattr(
        sys, "argv", ["apply_ddl.py", "--config", str(config_file)]
    )

    with pytest.raises(SystemExit) as exc:
        apply_ddl.main()

    assert exc.value.code == 1
    assert "Missing required config field" in caplog.text


def test_main_check_pending_logs_closing_separator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # --check pending makes run() raise SystemExit(1). main() must still emit
    # the closing separator (run-boundary symmetry) before it propagates.
    caplog.set_level(logging.INFO)
    config_file = tmp_path / "cfg.toml"
    config_file.write_text('ddl_dir = "x"\ndatabase = "mydb"\n')
    monkeypatch.setattr(apply_ddl, "setup_logging", lambda *a, **k: None)

    def _pending(*a: object, **k: object) -> None:
        raise SystemExit(1)

    monkeypatch.setattr(apply_ddl, "run", _pending)
    monkeypatch.setattr(
        sys, "argv", ["apply_ddl.py", "--config", str(config_file), "--check"]
    )

    with pytest.raises(SystemExit) as exc:
        apply_ddl.main()

    assert exc.value.code == 1
    # Opening separator (before run) plus the closing one on this exit path.
    assert caplog.text.count("=" * 60) == 2
    # A pending-check exit is not a success.
    assert "SUCCESS" not in caplog.text


@pytest.mark.parametrize(
    ("extra_argv", "expected_check", "expected_create"),
    [
        pytest.param([], False, False, id="defaults"),
        pytest.param(["--check"], True, False, id="check"),
        pytest.param(["--create-db"], False, True, id="create_db"),
        pytest.param(["--check", "--create-db"], True, True, id="both"),
    ],
)
def test_main_flag_plumbing_to_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extra_argv: list[str],
    expected_check: bool,
    expected_create: bool,
) -> None:
    # argparse -> run() plumbing: each CLI flag must arrive as the
    # matching keyword argument.
    config_file = tmp_path / "cfg.toml"
    config_file.write_text('ddl_dir = "x"\ndatabase = "mydb"\n')
    monkeypatch.setattr(apply_ddl, "setup_logging", lambda *a, **k: None)
    mock_run = MagicMock(return_value=None)
    monkeypatch.setattr(apply_ddl, "run", mock_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["apply_ddl.py", "--config", str(config_file), *extra_argv],
    )

    apply_ddl.main()

    assert mock_run.call_args.kwargs["check_only"] is expected_check
    assert mock_run.call_args.kwargs["create_db"] is expected_create


def test_main_db_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    config_file = tmp_path / "cfg.toml"
    config_file.write_text('ddl_dir = "x"\ndatabase = "mydb"\n')
    monkeypatch.setattr(apply_ddl, "setup_logging", lambda *a, **k: None)

    def _db_error(*a: object, **k: object) -> None:
        raise apply_ddl.psycopg2.Error("db boom")

    monkeypatch.setattr(apply_ddl, "run", _db_error)
    monkeypatch.setattr(
        sys, "argv", ["apply_ddl.py", "--config", str(config_file)]
    )

    with pytest.raises(SystemExit) as exc:
        apply_ddl.main()

    assert exc.value.code == 1
    assert "Database error" in caplog.text


# ---------------------------------------------------------------------------
# Static DDL invariants — the shipped 0001 and grant script as text
# ---------------------------------------------------------------------------


def _sql_text_without_comments(path: Path) -> str:
    """A shipped DDL file's text with `--` line comments stripped."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(re.sub(r"--.*$", "", line) for line in lines)


def _ddl_text_without_comments() -> str:
    """The shipped catalog 0001 with `--` line comments stripped."""
    return _sql_text_without_comments(_DDL_0001)


def _created_tables(ddl_text: str) -> set[str]:
    """Table names 0001 creates (comment-stripped text expected)."""
    return set(re.findall(r"create table (\S+)", ddl_text))


def test_ddl_0001_is_schema_agnostic() -> None:
    # The documented contract: 0001 contains no schema-qualified name —
    # it lands wherever the connection's search_path points (see
    # pgconn.connection_kwargs, imported by apply_ddl). Checked on
    # comment-stripped text so `--` prose cannot trip or hide a
    # violation. The guard is the
    # qualifier form `catalog.` (not the bare word): the schema is named
    # `catalog`, and that word legitimately appears in COMMENT ON string
    # literals, which are statements and survive comment-stripping.
    text = _ddl_text_without_comments()

    assert not re.search(r"\bcatalog\.", text)
    # Every created/altered/indexed/commented object name is unqualified.
    object_names = (
        re.findall(r"create table (\S+)", text)
        + re.findall(r"create index \S+\s+on (\S+)", text)
        + re.findall(r"comment on table (\S+)", text)
    )
    assert object_names, "expected 0001 to define objects"
    for name in object_names:
        assert "." not in name, f"schema-qualified name in 0001: {name}"


def test_ddl_0001_does_not_touch_ddl_versions() -> None:
    # ddl_versions is apply_ddl.py's own table (created and commented in
    # its bootstrap); 0001 referencing it would make the migration
    # unapplyable through any other path.
    text = _ddl_text_without_comments()
    assert "ddl_versions" not in text


def test_grant_script_table_lists_agree_with_ddl() -> None:
    # The grant script's own header declares its dependency on 0001's
    # canonical table set; pin the agreement so adding a table to the
    # DDL without granting it (or vice versa) fails a unit test.
    ddl_tables = _created_tables(_ddl_text_without_comments())
    main_tables = {
        t
        for t in ddl_tables
        if not t.endswith("_hstry") and t != "load_audit"
    }
    hstry_tables = {t for t in ddl_tables if t.endswith("_hstry")}

    # Comment-stripped for the same reason as the read-only sibling below:
    # `--` prose must not be able to satisfy a grant-list match.
    grant_text = _sql_text_without_comments(_GRANT_SCRIPT)

    def _grant_list(grant_clause: str) -> set[str]:
        match = re.search(
            re.escape(grant_clause) + r"\s+(.*?)\s*to metadata_db_ci;",
            grant_text,
            flags=re.DOTALL,
        )
        assert match, f"grant clause not found: {grant_clause}"
        return {t.strip() for t in match.group(1).split(",")}

    assert _grant_list("grant select, insert, update, delete on") == main_tables
    assert _grant_list("grant insert on") == hstry_tables
    assert "grant select on ddl_versions" in grant_text
    assert "grant select, insert on load_audit" in grant_text


def test_grant_script_ro_table_list_agrees_with_ddl() -> None:
    # The read-only role enumerates the same 9 main tables in a second
    # file, so the "add a main table to BOTH grant lists" obligation both
    # headers state has a test behind it. Also pins the exclusions: no
    # _hstry mirror and no load_audit may appear in the read-only script.
    ddl_tables = _created_tables(_ddl_text_without_comments())
    main_tables = {
        t
        for t in ddl_tables
        if not t.endswith("_hstry") and t != "load_audit"
    }

    # Comment-stripped so `--` prose naming a table cannot satisfy either
    # the grant-list match or the exclusion assertions below.
    statements = _sql_text_without_comments(_GRANT_SCRIPT_RO)

    match = re.search(
        r"grant select on\s+(.*?)\s*to metadata_db_ci_ro;",
        statements,
        flags=re.DOTALL,
    )
    assert match, "main-table grant clause not found"
    assert {t.strip() for t in match.group(1).split(",")} == main_tables

    assert "_hstry" not in statements
    assert "load_audit" not in statements


def test_ddl_0001_has_load_audit_loaded_ts_unique_index() -> None:
    # Backs the lineage join and the drift check's ORDER BY loaded_ts, and is
    # UNIQUE so one audit row per timestamp is enforced (no join fan-out).
    text = _ddl_text_without_comments()
    assert re.search(
        r"create unique index idx_load_audit_loaded_ts "
        r"on load_audit\(loaded_ts\)",
        text,
    )


def _table_body(ddl_text: str, table: str) -> str:
    """Return the parenthesized body of `create table <table> ( ... );`.

    Args:
        ddl_text: DDL text (comment-stripped expected).
        table: Table name whose body to extract.

    Returns:
        The text between the table's opening `(` and its closing `\\n);`.
    """
    match = re.search(
        rf"create table {re.escape(table)} \((.*?)\n\);",
        ddl_text,
        flags=re.DOTALL,
    )
    assert match, f"create table {table} not found"
    return match.group(1)


@pytest.mark.parametrize(
    ("table", "constraint"),
    [
        # Hierarchy-consistency CHECKs: each child id embeds its parent's id.
        ("schemas", "check (data_source_id = subltree(schema_id, 0, 1))"),
        ("tables", "check (schema_id = subltree(table_id, 0, 2))"),
        ("columns", "check (table_id = subltree(column_id, 0, 3))"),
        # Leaf-name redundancy CHECKs: the name column equals the id's leaf.
        ("schemas", "check (schema_name = subpath(schema_id, -1, 1)::text)"),
        ("tables", "check (table_name = subpath(table_id, -1, 1)::text)"),
        ("columns", "check (column_name = subpath(column_id, -1, 1)::text)"),
        # Lowercase-identity CHECKs: every ltree id is already lowercase.
        ("systems", "check (system::text = lower(system::text))"),
        (
            "data_sources",
            "check (data_source_id::text = lower(data_source_id::text))",
        ),
        ("schemas", "check (schema_id::text = lower(schema_id::text))"),
        ("tables", "check (table_id::text = lower(table_id::text))"),
        ("columns", "check (column_id::text = lower(column_id::text))"),
        ("concepts", "check (concept_id::text = lower(concept_id::text))"),
        # deployment_tables physical-name lowercase CHECKs.
        (
            "deployment_tables",
            "check (physical_database_name = lower(physical_database_name))",
        ),
        (
            "deployment_tables",
            "check (physical_schema_name = lower(physical_schema_name))",
        ),
        (
            "deployment_tables",
            "check (physical_table_name = lower(physical_table_name))",
        ),
    ],
)
def test_ddl_0001_main_table_check_present(table: str, constraint: str) -> None:
    # Pin each new single-table CHECK to the exact table body it belongs in
    # (mirrors the existing constraint-name binding test).
    body = _table_body(_ddl_text_without_comments(), table)
    assert constraint in body, f"{constraint!r} missing from {table}"


@pytest.mark.parametrize(
    "table",
    [
        "systems",
        "data_sources",
        "schemas",
        "tables",
        "columns",
        "table_relationships",
        "column_mappings",
        "concepts",
    ],
)
def test_ddl_0001_update_reason_pairing_check_on_authored_tables(
    table: str,
) -> None:
    # Every authored main table backstops the update_reason pairing (Task
    # 1.7); deployment_tables is excluded (pure facts, no update_reason).
    body = _table_body(_ddl_text_without_comments(), table)
    assert "check ((update_reason is null) = (insert_ts = update_ts))" in body


def test_ddl_0001_deployment_tables_has_no_update_reason_pairing() -> None:
    # deployment_tables has no update_reason column, so the pairing CHECK
    # must not appear there.
    body = _table_body(_ddl_text_without_comments(), "deployment_tables")
    assert "update_reason" not in body


def test_ddl_0001_concept_id_shape_check_present() -> None:
    # concept_id shape: 3 to 6 labels (data-source, schema, table, and
    # column anchors) with the reserved `concept` segment pinned
    # second-to-last. Live-DB acceptance/rejection of depth-5/6/7 ids is
    # exercised by the env-gated integration suite
    # (code/load_catalog_data/unit_tests/test_integration.py); here only
    # this static assertion runs.
    body = _table_body(_ddl_text_without_comments(), "concepts")
    assert "nlevel(concept_id) between 3 and 6" in body
    assert "subpath(concept_id, -2, 1) = 'concept'::ltree" in body


def test_ddl_0001_has_unordered_pair_unique_index() -> None:
    # Both orientations of one table pair + relationship_name cannot
    # coexist (unordered-pair uniqueness, enforced via least/greatest).
    text = _ddl_text_without_comments()
    assert re.search(
        r"create unique index idx_table_relationships_unordered_pair",
        text,
    )
    assert "least(table_a_id, table_b_id)" in text
    assert "greatest(table_a_id, table_b_id)" in text


def test_ddl_0001_has_version_assert_block() -> None:
    # PostgreSQL 16+ is asserted up front so a too-old server
    # fails at apply time, not months later mid-load.
    text = _ddl_text_without_comments()
    assert "server_version_num" in text
    assert "160000" in text
    assert re.search(r"do \$\$", text)


@pytest.mark.parametrize(
    "hstry_table",
    [
        "systems_hstry",
        "data_sources_hstry",
        "schemas_hstry",
        "tables_hstry",
        "columns_hstry",
        "deployment_tables_hstry",
        "table_relationships_hstry",
        "column_mappings_hstry",
        "concepts_hstry",
    ],
)
def test_ddl_0001_hstry_mirrors_have_no_constraints(hstry_table: str) -> None:
    # The _hstry mirrors deliberately stay constraint-free:
    # history legitimately holds superseded values. Only the composite
    # primary key is allowed — no CHECKs, no UNIQUEs, and no FKs (history
    # legitimately holds links to since-deleted rows).
    body = _table_body(_ddl_text_without_comments(), hstry_table)
    assert "check (" not in body
    assert "unique" not in body
    assert "references" not in body


def test_ddl_0001_columns_ref_table_id_fk_named_and_deferrable() -> None:
    # columns.ref_table_id is the catalog's one mutable non-PK FK: the
    # loader's updates-before-inserts phase order can point an in-place
    # UPDATE at a tables row inserted later in the same transaction, so
    # the FK must be NAMED (the loader defers it by name — see
    # db_io.COLUMNS_REF_TABLE_ID_FK_CONSTRAINT) and declared
    # `deferrable initially immediate` so it settles at COMMIT.
    body = _table_body(_ddl_text_without_comments(), "columns")
    match = re.search(
        r"ref_table_id ltree\s+"
        r"constraint columns_ref_table_id_fkey\s+"
        r"references tables\(table_id\)\s+"
        r"deferrable initially immediate",
        body,
    )
    assert match, "named deferrable columns_ref_table_id_fkey not in columns"


def test_ddl_ref_every_created_table_declares_a_primary_key() -> None:
    # The DDL-side layer of the ref PK guard: every table any ref
    # migration creates must declare a primary key, so a PK-less
    # migration fails review-time CI before it ever reaches a live DB
    # (the loader's runtime issue is the second layer, catching a
    # PK-less table however it arose). Silently loading duplicate codes
    # into a curated set would defeat the purpose of ref.
    migration_files = sorted(_DDL_REF_0001.parent.glob("*.sql"))
    assert migration_files, "expected ref migrations under ddl_ref/"
    for path in migration_files:
        text = _sql_text_without_comments(path)
        tables = re.findall(r"create table (\S+)", text)
        assert tables, f"expected {path.name} to create tables"
        for table in tables:
            body = _table_body(text, table)
            assert "primary key" in body, (
                f"{path.name}: create table {table} declares no primary "
                f"key — every ref code-set table needs one"
            )


def test_ddl_ref_0001_ref_load_audit_has_identity_pk() -> None:
    # The audit_id identity PK makes audit rows individually addressable
    # and gives the freshness query its deterministic tiebreaker
    # (`order by loaded_ts desc, audit_id desc`); GENERATED ALWAYS AS
    # IDENTITY mirrors the catalog's load_audit.load_id.
    body = _table_body(
        _sql_text_without_comments(_DDL_REF_0001), "ref_load_audit"
    )
    assert "audit_id bigint generated always as identity primary key" in body


def test_ddl_0001_has_no_redundant_source_column_index() -> None:
    # (source_column_id) is the leading prefix of column_mappings' PK, so
    # a standalone index on it is pure overhead — keep it dropped.
    text = _ddl_text_without_comments()
    assert "idx_column_mappings_source_column\n" not in text
    assert not re.search(
        r"create index \S+\s+on column_mappings\(source_column_id\)", text
    )
