"""Unit tests for the offline corpus checker (check_corpus.py)."""

import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import check_corpus

# The staged corpus is the loader's own: the checker runs the loader's
# first three stages verbatim, so the same fixture corpus exercises it.
# It comes from conftest.py rather than the sibling test module, whose
# import would pull psycopg2 into this database-free suite.
from conftest import _stage_config, _stage_corpus


@pytest.fixture(autouse=True)
def _restore_root_handlers() -> Iterator[None]:
    """Detach the handlers each `main()` run adds to the ROOT logger.

    `_mirror_log_to_stderr` attaches a stream handler to the root logger
    (and `setup_logging` a file handler) on every run; without cleanup
    they accumulate across tests and leak into other modules' tests.
    """
    root = logging.getLogger()
    before = root.handlers[:]
    yield
    for handler in root.handlers[:]:
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


@pytest.fixture
def staged_cfg(tmp_path: Path) -> tuple[Path, Path]:
    """Stage the standard corpus and a config pointing at it under `tmp_path`.

    The config carries `data_root` only — the one field the checker reads;
    the loader's connection fields are irrelevant to the offline run, and
    leaving them out keeps that true.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        The `(data_root, cfg)` pair — the corpus root and the config path.
    """
    data_root = tmp_path / "data"
    data_root.mkdir()
    _stage_corpus(data_root)
    cfg = _stage_config(tmp_path, data_root)
    return data_root, cfg


def test_main_clean_corpus_returns_and_logs_corpus_ok(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    monkeypatch.setattr(sys, "argv", ["check_corpus", "--config", str(cfg)])
    caplog.set_level(logging.INFO)

    check_corpus.main()  # returns (exit status 0), no SystemExit

    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("Corpus OK") for m in messages)
    # The summary counts the staged corpus: 2 data sources, 3 tables.
    assert any("2 data sources" in m and "3 tables" in m for m in messages)
    # The two diff-time rules are called out as not covered here.
    assert any("Not checked here" in m for m in messages)


def test_main_clean_corpus_mirrors_progress_to_stderr(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The stderr mirror is the contributor-facing output: a clean run must
    # say so on stderr, not only in the JSONL file.
    data_root, cfg = staged_cfg
    monkeypatch.setattr(sys, "argv", ["check_corpus", "--config", str(cfg)])
    caplog.set_level(logging.INFO)

    check_corpus.main()

    captured = capsys.readouterr()
    assert "Corpus OK" in captured.err
    # stdout stays free for a caller to redirect or pipe.
    assert captured.out == ""


def test_mirror_log_to_stderr_attaches_plain_info_handler_to_root() -> None:
    root = logging.getLogger()
    before = root.handlers[:]

    check_corpus._mirror_log_to_stderr()

    added = [h for h in root.handlers if h not in before]
    assert len(added) == 1
    handler = added[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.level == logging.INFO
    # Plain message text — no JSON envelope for the person at the terminal.
    record = logging.LogRecord(
        "any", logging.INFO, "path", 1, "hello → world", None, None
    )
    assert handler.format(record) == "hello → world"


def test_main_assembly_failure_exits_1_and_logs_each_issue(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    (data_root / "sources" / "ocs" / "general" / "stray.yaml").write_text(
        "x: 1\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys, "argv", ["check_corpus", "--config", str(cfg)])
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as excinfo:
        check_corpus.main()

    assert excinfo.value.code == 1
    messages = [r.getMessage() for r in caplog.records]
    assert any("Corpus assembly failed" in m for m in messages)
    assert any("stray.yaml" in m for m in messages)


def test_main_validation_failure_exits_1_and_logs_one_record_per_issue(
    staged_cfg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_root, cfg = staged_cfg
    rel_path = (
        data_root / "sources" / "ocs" / "general" / "table_relationships.yaml"
    )
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
    monkeypatch.setattr(sys, "argv", ["check_corpus", "--config", str(cfg)])
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as excinfo:
        check_corpus.main()

    assert excinfo.value.code == 1
    messages = [r.getMessage() for r in caplog.records]
    # Issues are reported exactly as the loader reports them: the summary
    # record plus one record per issue, never one blob embedding the whole
    # issue list (the JSONL log is a machine audience). An issue string may
    # itself contain a newline (sqlglot's parse-error highlight), so the
    # single-line pin applies to the summary record only.
    summary = next(m for m in messages if "Corpus validation failed" in m)
    assert "\n" not in summary
    assert any(m.startswith("  - ") and "Failed to parse SQL" in m for m in messages)


def test_main_missing_corpus_root_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = _stage_config(tmp_path, tmp_path / "no_such_dir")
    monkeypatch.setattr(sys, "argv", ["check_corpus", "--config", str(cfg)])
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as excinfo:
        check_corpus.main()

    assert excinfo.value.code == 1
    assert any(
        "Corpus root not found" in r.getMessage() and "no_such_dir" in r.getMessage()
        for r in caplog.records
    )


def test_main_missing_config_file_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    missing = tmp_path / "missing.toml"
    monkeypatch.setattr(sys, "argv", ["check_corpus", "--config", str(missing)])
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as excinfo:
        check_corpus.main()

    assert excinfo.value.code == 1
    assert any(
        "Config file not found" in r.getMessage() for r in caplog.records
    )


def test_main_bad_toml_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = tmp_path / "checker.toml"
    cfg.write_text("not = a = valid = toml\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check_corpus", "--config", str(cfg)])
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as excinfo:
        check_corpus.main()

    assert excinfo.value.code == 1
    assert any(
        "Failed to read config file" in r.getMessage() for r in caplog.records
    )


def test_main_missing_data_root_field_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = tmp_path / "checker.toml"
    cfg.write_text('database = "metadata_db"\n', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check_corpus", "--config", str(cfg)])
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as excinfo:
        check_corpus.main()

    assert excinfo.value.code == 1
    assert any(
        "Missing required config field" in r.getMessage()
        and "data_root" in r.getMessage()
        for r in caplog.records
    )


def test_main_zero_arguments_reads_data_root_from_loader_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The zero-argument run CONTRIBUTING.md advertises: --config defaults
    # to the loader's own TOML (found script-relative, from any cwd), and
    # the corpus root is that file's data_root ("data_catalog", resolved
    # from the cwd — here an empty tmp dir, so the run fails on it, which
    # is the proof the value came from the loader's config).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_corpus"])
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as excinfo:
        check_corpus.main()

    assert excinfo.value.code == 1
    assert any(
        "Corpus root not found: data_catalog" in r.getMessage()
        for r in caplog.records
    )
