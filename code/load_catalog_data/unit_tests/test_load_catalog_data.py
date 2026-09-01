"""Unit tests for the load_catalog_data entry point (venue-free model)."""

import sys
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import MagicMock

import pytest

import load_catalog_data as lmd
from data_model import DbState, SystemRow, empty_db_state

# The staged corpus and config live in conftest.py: the checker's suite
# needs the same corpus, and shared test data belongs there rather than in
# whichever test module happened to define it first.
from conftest import _stage_config, _stage_corpus


@pytest.fixture
def staged_cfg(tmp_path: Path) -> tuple[Path, Path]:
    """Stage the standard corpus and its loader.toml under `tmp_path`.

    The four-line arrange block most `main()` tests share. Function-scoped,
    so each test gets its own `tmp_path` tree and may mutate the staged
    corpus through the returned `data_root`.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        The `(data_root, cfg)` pair — the corpus root and the config path.
    """
    data_root = tmp_path / "data"
    data_root.mkdir()
    _stage_corpus(data_root)
    cfg = _stage_config(tmp_path, data_root, loader_fields=True)
    return data_root, cfg


@pytest.fixture(autouse=True)
def _stub_resolve_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub resolve_commit_sha for run().

    run() now resolves the commit SHA in both modes (dry-run parity). Stub
    it so unit tests do not depend on the ambient git checkout; tests that
    exercise resolution failure re-patch it to raise.
    """
    monkeypatch.setattr(lmd, "resolve_commit_sha", lambda: "testsha0000")


@pytest.fixture
def patched_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide the POSTGRES_* env vars so connection_kwargs succeeds."""
    monkeypatch.setenv("POSTGRES_HOST", "h")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")


@pytest.fixture
def stub_connect(
    monkeypatch: pytest.MonkeyPatch,
    fake_conn: MagicMock,
    fake_cursor: MagicMock,
) -> tuple[MagicMock, MagicMock]:
    """Patch psycopg2.connect to a fake conn (empty DB, so all inserts).

    read_db_state issues 9 SELECTs (one per main table); each fetchall
    returns [].
    """
    fake_cursor.fetchall.return_value = []
    monkeypatch.setattr(lmd.psycopg2, "connect", lambda **kw: fake_conn)
    return fake_conn, fake_cursor


def test_main_happy_path_calls_apply_diff(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
) -> None:
    data_root, cfg = staged_cfg

    called: dict[str, Any] = {}

    def fake_apply_diff(
        conn: MagicMock, diff: object, commit_sha: str, reset_hstry: bool
    ) -> None:
        called["diff"] = diff
        called["commit_sha"] = commit_sha
        called["reset_hstry"] = reset_hstry

    monkeypatch.setattr(lmd, "apply_diff", fake_apply_diff)
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])

    lmd.main()

    assert "diff" in called
    assert called["reset_hstry"] is False
    # The orchestrator-resolved SHA is passed through to apply_diff.
    assert called["commit_sha"] == "testsha0000"


def test_run_flows_concepts_and_deployments_to_apply_diff(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
) -> None:
    # A corpus containing a concept and expanded deployments flows through
    # run(): both become inserts in the diff handed to apply_diff.
    data_root, cfg = staged_cfg

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        lmd, "apply_diff",
        lambda conn, diff, commit_sha, reset_hstry: captured.update(diff=diff),
    )
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])

    lmd.main()

    diff = captured["diff"]
    concept_inserts = [c for c in diff.inserts if c.table == "concepts"]
    assert [c.key for c in concept_inserts] == ["ocs.concept.claim"]
    assert concept_inserts[0].new.label == "Claim"
    assert concept_inserts[0].new.related_object_ids == (
        "ocs.general.claim",
        "ocs.general.claim.clm_id",
    )
    rel_inserts = [c for c in diff.inserts if c.table == "table_relationships"]
    assert [c.new.cardinality for c in rel_inserts] == ["one_to_many"]
    # deployments were expanded to table-grain deployment_tables rows:
    # ocs's two tables in warehouse plus edw_prd's one table in edw.
    dep_inserts = {
        c.key for c in diff.inserts if c.table == "deployment_tables"
    }
    assert dep_inserts == {
        ("ocs.general.bene", "warehouse"),
        ("ocs.general.claim", "warehouse"),
        ("edw_prd.claims_vw.bene", "edw"),
    }


def test_main_passes_schema_to_connection(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    fake_conn: MagicMock,
    fake_cursor: MagicMock,
) -> None:
    data_root, cfg = staged_cfg
    fake_cursor.fetchall.return_value = []

    captured: dict[str, Any] = {}

    def _capture_connect(**kw: object) -> MagicMock:
        captured.update(kw)
        return fake_conn

    monkeypatch.setattr(lmd.psycopg2, "connect", _capture_connect)
    monkeypatch.setattr(lmd, "apply_diff", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])

    lmd.main()

    assert captured["options"] == "-c search_path=catalog"


def test_main_missing_schema_field_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
) -> None:
    cfg = tmp_path / "loader.toml"
    cfg.write_text('data_root = "data"\ndatabase = "metadata_db"\n', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1


def test_main_dry_run_does_not_call_apply_diff(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
) -> None:
    data_root, cfg = staged_cfg

    sentinel = MagicMock()
    monkeypatch.setattr(lmd, "apply_diff", sentinel)
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg), "--dry-run"])

    lmd.main()
    sentinel.assert_not_called()


def test_main_validation_failure_logs_all_issues_and_exits_1(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    rel_path = data_root / "sources" / "ocs" / "general" / "table_relationships.yaml"
    rel_path.write_text(
        rel_path.read_text(encoding="utf-8")
        + "\n- table_a_id: ocs.general.bene\n"
        "  table_b_id: ocs.general.claim\n"
        "  relationship_name: bad\n"
        "  join_condition: SELECT FROM WHERE\n"
        "  cardinality: null\n"
        "  use_when: null\n"
        "  notes: null\n"
        "  validated: false\n"
        "  update_reason: null\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg), "--dry-run"])
    caplog.set_level("ERROR")
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1
    assert any("Failed to parse SQL" in r.getMessage() for r in caplog.records)
    assert any(
        "Corpus validation failed" in r.getMessage() for r in caplog.records
    )


def test_main_missing_config_file_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["lmd", "--config", str(tmp_path / "missing.toml")]
    )
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1


def test_main_bad_toml_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "loader.toml"
    cfg.write_text("not = a = valid = toml\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1


def test_main_missing_config_field_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
) -> None:
    cfg = tmp_path / "loader.toml"
    cfg.write_text('data_root = "data"\n', encoding="utf-8")  # missing `database`
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1


def test_main_non_string_data_root_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A mistyped required field is a config error like any other: `Path(5)`
    # raises TypeError, and main() must report it on the same clean exit-1
    # path rather than letting it escape as an unhandled traceback.
    cfg = tmp_path / "loader.toml"
    cfg.write_text(
        'data_root = 5\ndatabase = "metadata_db"\nschema = "catalog"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    caplog.set_level("ERROR")

    with pytest.raises(SystemExit) as excinfo:
        lmd.main()

    assert excinfo.value.code == 1
    # Reported through a logged error arm (the exact CPython wording of the
    # TypeError is not this suite's business).
    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_run_reset_hstry_without_env_guard_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"data_root": "data", "database": "metadata_db"}
    monkeypatch.delenv("METADATA_DB_ALLOW_RESET_HSTRY", raising=False)
    with pytest.raises(RuntimeError, match="METADATA_DB_ALLOW_RESET_HSTRY"):
        lmd.run(config, dry_run=False, reset_hstry=True)


def test_run_reset_hstry_without_env_guard_raises_in_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The env gate applies in dry-run too (mirroring --allow-mass-delete):
    # the pre-merge dry-run must fail exactly as a real run would.
    config = {"data_root": "data", "database": "metadata_db"}
    monkeypatch.delenv("METADATA_DB_ALLOW_RESET_HSTRY", raising=False)
    with pytest.raises(RuntimeError, match="METADATA_DB_ALLOW_RESET_HSTRY"):
        lmd.run(config, dry_run=True, reset_hstry=True)


def test_main_dry_run_reset_hstry_without_env_guard_exits_1(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    monkeypatch.delenv("METADATA_DB_ALLOW_RESET_HSTRY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["lmd", "--config", str(cfg), "--dry-run", "--reset-hstry"],
    )
    caplog.set_level("ERROR")
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1
    assert any(
        "METADATA_DB_ALLOW_RESET_HSTRY" in r.getMessage()
        and "Refusing" in r.getMessage()
        for r in caplog.records
    )


def test_main_reset_hstry_without_env_guard_exits_1(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
) -> None:
    data_root, cfg = staged_cfg
    monkeypatch.delenv("METADATA_DB_ALLOW_RESET_HSTRY", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["lmd", "--config", str(cfg), "--reset-hstry"]
    )
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1


def test_main_db_error_exits_1(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
) -> None:
    data_root, cfg = staged_cfg

    def boom(**kw: object) -> NoReturn:
        raise lmd.psycopg2.OperationalError("cannot connect")

    monkeypatch.setattr(lmd.psycopg2, "connect", boom)
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1


def test_main_misplaced_yaml_exits_1_as_assembly_failure(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    (data_root / "sources" / "ocs" / "general" / "stray.yaml").write_text(
        "x: 1\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    caplog.set_level("ERROR")
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1
    assert any(
        "Corpus assembly failed" in r.getMessage() for r in caplog.records
    )
    assert any("stray.yaml" in r.getMessage() for r in caplog.records)


def test_main_reset_hstry_with_env_guard_calls_apply_diff_with_truncate(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
) -> None:
    data_root, cfg = staged_cfg
    monkeypatch.setenv("METADATA_DB_ALLOW_RESET_HSTRY", "1")
    called: dict[str, Any] = {}
    monkeypatch.setattr(
        lmd,
        "apply_diff",
        lambda conn, diff, commit_sha, reset_hstry: called.update(
            reset_hstry=reset_hstry
        ),
    )
    monkeypatch.setattr(
        sys, "argv", ["lmd", "--config", str(cfg), "--reset-hstry"]
    )
    lmd.main()
    assert called["reset_hstry"] is True


def test_main_yaml_parse_error_exits_1(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    bad = data_root / "systems.yaml"
    bad.write_text(": : : bad ::: yaml\n  - x:\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    caplog.set_level("ERROR")
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1
    assert any(
        "Failed to read or parse YAML" in r.getMessage()
        for r in caplog.records
    )


def test_main_multiple_shape_errors_all_logged(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    ocs_schema = data_root / "sources" / "ocs" / "general"
    tables_path = ocs_schema / "tables.yaml"
    tables_path.write_text(
        tables_path.read_text(encoding="utf-8")
        + "- description: row without a table_name\n",
        encoding="utf-8",
    )
    columns_path = ocs_schema / "columns.yaml"
    columns_path.write_text(
        columns_path.read_text(encoding="utf-8")
        + "- table_name: bene\n  column_name: extra\n  data_type: TEXT\n"
        "  is_nullable: false\n  is_primaryy_key: true\n",  # typo'd key
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg), "--dry-run"])
    caplog.set_level("ERROR")
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1
    messages = [r.getMessage() for r in caplog.records]
    assert any("Corpus assembly failed with 2 issue(s)" in m for m in messages)
    assert any("table_name" in m for m in messages)
    assert any("is_primaryy_key" in m for m in messages)


def test_main_validation_does_not_run_on_dirty_assembly(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    ocs_schema = data_root / "sources" / "ocs" / "general"
    tables_path = ocs_schema / "tables.yaml"
    tables_path.write_text(
        tables_path.read_text(encoding="utf-8")
        + "- description: row without a table_name\n",
        encoding="utf-8",
    )
    mappings_path = ocs_schema / "mappings" / "edw_prd.yaml"
    mappings_path.write_text(
        mappings_path.read_text(encoding="utf-8")
        + "- source_column_id: ocs.general.bene.ghost_col\n"
        "  mapping_name: ghost\n"
        "  target_expression: null\n"
        "  notes: FK error — ghost_col is not a defined column\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg), "--dry-run"])
    caplog.set_level("ERROR")
    with pytest.raises(SystemExit) as excinfo:
        lmd.main()
    assert excinfo.value.code == 1
    messages = [r.getMessage() for r in caplog.records]
    assert any("Corpus assembly failed" in m for m in messages)
    assert any("table_name" in m for m in messages)
    assert not any("Corpus validation failed" in m for m in messages)
    assert not any("not defined in columns" in m for m in messages)


def _legacy_db_state(n: int) -> DbState:
    """A DbState of `n` systems absent from the staged corpus (all deletes)."""
    state = empty_db_state()
    for i in range(n):
        state.systems[f"legacy{i}"] = SystemRow(
            system=f"legacy{i}",
            description="legacy venue",
            notes=None,
            update_reason=None,
        )
    return state


def test_main_mass_delete_guard_blocks_and_skips_apply(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    monkeypatch.setattr(lmd, "read_db_state", lambda conn: _legacy_db_state(40))
    sentinel = MagicMock()
    monkeypatch.setattr(lmd, "apply_diff", sentinel)
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    caplog.set_level("ERROR")

    with pytest.raises(SystemExit) as excinfo:
        lmd.main()

    assert excinfo.value.code == 1
    sentinel.assert_not_called()
    assert any("mass-delete guard" in r.getMessage() for r in caplog.records)


def test_main_mass_delete_guard_applies_in_dry_run(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    monkeypatch.setattr(lmd, "read_db_state", lambda conn: _legacy_db_state(40))
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg), "--dry-run"])
    caplog.set_level("ERROR")

    with pytest.raises(SystemExit) as excinfo:
        lmd.main()

    # Pin the intended failure: the whole pipeline runs here, so a
    # dry-run exiting 1 for any other reason must not pass this test.
    assert excinfo.value.code == 1
    assert any("mass-delete guard" in r.getMessage() for r in caplog.records)


def _legacy_db_state_with_stale_warehouse(n: int) -> DbState:
    """`n` legacy systems (deletes) plus a stale `warehouse` row (an update).

    The staged corpus documents `warehouse` with description "source" and a
    null update_reason, so a DB row carrying a different description
    diffs as an update whose corpus row violates the update_reason
    discipline.
    """
    state = _legacy_db_state(n)
    state.systems["warehouse"] = SystemRow(
        system="warehouse",
        description="outdated description",
        notes=None,
        update_reason=None,
    )
    return state


def test_main_update_reason_report_not_masked_by_mass_delete_guard(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # When a diff both violates the update_reason discipline and trips
    # the mass-delete guard, the update_reason check runs first
    # (CONTRIBUTING.md's wave-3 ordering): the accumulated update_reason
    # report surfaces instead of being hidden behind MassDeleteError.
    data_root, cfg = staged_cfg
    monkeypatch.setattr(
        lmd, "read_db_state", lambda conn: _legacy_db_state_with_stale_warehouse(40)
    )
    sentinel = MagicMock()
    monkeypatch.setattr(lmd, "apply_diff", sentinel)
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    caplog.set_level("ERROR")

    with pytest.raises(SystemExit) as excinfo:
        lmd.main()

    assert excinfo.value.code == 1
    sentinel.assert_not_called()
    messages = [r.getMessage() for r in caplog.records]
    assert any("required on every update" in m for m in messages)
    assert not any("mass-delete guard" in m for m in messages)


def test_main_allow_mass_delete_without_env_guard_exits_1(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    monkeypatch.delenv("METADATA_DB_ALLOW_MASS_DELETE", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["lmd", "--config", str(cfg), "--allow-mass-delete"]
    )
    caplog.set_level("ERROR")

    with pytest.raises(SystemExit) as excinfo:
        lmd.main()

    assert excinfo.value.code == 1
    assert any(
        "METADATA_DB_ALLOW_MASS_DELETE" in r.getMessage() for r in caplog.records
    )


def test_main_dry_run_allow_mass_delete_without_env_guard_exits_1(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The env gate applies in dry-run too (mirroring --reset-hstry): the
    # dry-run must preview exactly what a real run would do, so moving the
    # gate behind a `dry_run` check has to fail here.
    data_root, cfg = staged_cfg
    monkeypatch.delenv("METADATA_DB_ALLOW_MASS_DELETE", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["lmd", "--config", str(cfg), "--dry-run", "--allow-mass-delete"],
    )
    caplog.set_level("ERROR")

    with pytest.raises(SystemExit) as excinfo:
        lmd.main()

    assert excinfo.value.code == 1
    assert any(
        "METADATA_DB_ALLOW_MASS_DELETE" in r.getMessage() for r in caplog.records
    )


def test_main_allow_mass_delete_with_env_bypasses_guard(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
) -> None:
    data_root, cfg = staged_cfg
    monkeypatch.setenv("METADATA_DB_ALLOW_MASS_DELETE", "1")
    monkeypatch.setattr(lmd, "read_db_state", lambda conn: _legacy_db_state(40))
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        lmd,
        "apply_diff",
        lambda conn, diff, commit_sha, reset_hstry: captured.update(diff=diff),
    )
    monkeypatch.setattr(
        sys, "argv", ["lmd", "--config", str(cfg), "--allow-mass-delete"]
    )

    lmd.main()

    assert len(captured["diff"].deletes) == 40


@pytest.mark.parametrize(
    "knob_line",
    [
        pytest.param("mass_delete_fraction = 1.0", id="fraction"),
        pytest.param("mass_delete_min_count = 100", id="min_count"),
    ],
)
def test_main_mass_delete_config_knobs_raise_threshold(
    knob_line: str,
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
) -> None:
    # Either knob, raised in config, has to reach check_mass_delete: the
    # 40 staged deletes are under a 1.0 fraction and under a 100-row floor,
    # so a dropped or swapped argument would let the guard trip.
    data_root, cfg = staged_cfg
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + knob_line + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lmd, "read_db_state", lambda conn: _legacy_db_state(40))
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        lmd,
        "apply_diff",
        lambda conn, diff, commit_sha, reset_hstry: captured.update(diff=diff),
    )
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])

    lmd.main()

    assert len(captured["diff"].deletes) == 40


def test_run_acquires_advisory_lock_before_reading_db(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
) -> None:
    data_root, cfg = staged_cfg
    _, fake_cursor = stub_connect
    monkeypatch.setattr(lmd, "apply_diff", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])

    lmd.main()

    first_sql = fake_cursor.execute.call_args_list[0][0][0]
    assert "pg_try_advisory_xact_lock" in first_sql


def test_run_lock_not_acquired_exits_1_without_writing(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    _, fake_cursor = stub_connect
    fake_cursor.fetchone.return_value = (False,)
    sentinel = MagicMock()
    monkeypatch.setattr(lmd, "apply_diff", sentinel)
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    caplog.set_level("ERROR")

    with pytest.raises(SystemExit) as excinfo:
        lmd.main()

    assert excinfo.value.code == 1
    sentinel.assert_not_called()
    assert any(
        "already in progress" in r.getMessage() for r in caplog.records
    )


# ---------------------------------------------------------------------------
# SHA resolution parity in dry-run — resolution failures fail dry-run too
# ---------------------------------------------------------------------------


def test_dry_run_invokes_sha_resolution_and_failure_surfaces(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
) -> None:
    # Dry-run resolves the SHA in the same place a real run does, so a run
    # that would fail SHA resolution fails its dry-run too (parity).
    data_root, cfg = staged_cfg

    def _boom() -> NoReturn:
        raise RuntimeError("could not resolve commit SHA")

    monkeypatch.setattr(lmd, "resolve_commit_sha", _boom)
    sentinel = MagicMock()
    monkeypatch.setattr(lmd, "apply_diff", sentinel)
    monkeypatch.setattr(
        sys, "argv", ["lmd", "--config", str(cfg), "--dry-run"]
    )

    with pytest.raises(SystemExit) as excinfo:
        lmd.main()

    assert excinfo.value.code == 1
    # No writes even attempted — the failure is in the read-only preamble.
    sentinel.assert_not_called()


# ---------------------------------------------------------------------------
# mass-delete config-knob validation — bad knobs are clean config errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "knob_line",
    [
        pytest.param('mass_delete_fraction = "high"', id="fraction_string"),
        pytest.param("mass_delete_fraction = 2.0", id="fraction_out_of_range"),
        pytest.param("mass_delete_fraction = -0.1", id="fraction_negative"),
        pytest.param("mass_delete_fraction = true", id="fraction_bool"),
        pytest.param('mass_delete_min_count = "lots"', id="min_count_string"),
        pytest.param("mass_delete_min_count = -1", id="min_count_negative"),
        pytest.param("mass_delete_min_count = true", id="min_count_bool"),
        pytest.param("mass_delete_min_count = 2.5", id="min_count_float"),
    ],
)
def test_main_bad_mass_delete_knob_exits_1(
    knob_line: str,
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A mistyped or out-of-range guard knob is a config error: clean exit-1
    # through main()'s ValueError arm, not an unhandled TypeError traceback.
    data_root, cfg = staged_cfg
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + knob_line + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])
    caplog.set_level("ERROR")

    with pytest.raises(SystemExit) as excinfo:
        lmd.main()

    assert excinfo.value.code == 1
    assert any("mass_delete" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# schema-scoped advisory lock — per-(database, schema) serialization
# ---------------------------------------------------------------------------


def test_schema_lock_key_differs_by_target_and_is_stable() -> None:
    catalog = lmd._schema_lock_key("metadata_db", "catalog")
    staging = lmd._schema_lock_key("metadata_db", "staging")
    other_db = lmd._schema_lock_key("other_db", "catalog")
    assert catalog != staging
    assert catalog != other_db
    # Deterministic across calls (not process-randomized like hash()).
    assert lmd._schema_lock_key("metadata_db", "catalog") == catalog
    # Fits a signed 32-bit int (pg_try_advisory_xact_lock's int4 argument).
    assert -(2**31) <= catalog < 2**31


def test_advisory_lock_call_carries_schema_scoped_second_key(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    patched_env: None,
    stub_connect: tuple[MagicMock, MagicMock],
) -> None:
    data_root, cfg = staged_cfg
    _, fake_cursor = stub_connect
    monkeypatch.setattr(lmd, "apply_diff", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["lmd", "--config", str(cfg)])

    lmd.main()

    lock_call = next(
        c
        for c in fake_cursor.execute.call_args_list
        if "pg_try_advisory_xact_lock" in c.args[0]
    )
    # Two-key form: fixed first key plus the (database, schema) second key.
    assert "%s, %s" in lock_call.args[0]
    params = lock_call.args[1]
    assert params[0] == lmd.LOADER_LOCK_KEY
    # _stage_config uses database=metadata_db, schema=catalog.
    assert params[1] == lmd._schema_lock_key("metadata_db", "catalog")
