"""Unit tests for load_ref_data.py (the ref-schema code-set loader).

DB access is mocked at the psycopg2 boundary (fake connection/cursor per
the shared conftest fixtures); CSVs and corpus docs are written to
tmp_path using the enforced tree shapes (`data_ref/<schema>/<table>.csv`
and docs under a schema folder). The validation cores (`validate_csv`,
`validate_csv_docs_shape`) are pure functions and are exercised
directly.
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import psycopg2
import pytest

import load_ref_data as lrd


# A reduced clm_type_cd-shaped fixture: (name, data_type, is_nullable) in
# ordinal order. Deliberately narrower than the real table (which has the
# crosswalk columns too) — these tests exercise loader logic, not the
# live schema, so the shape only needs to be representative.
_LIVE_COLUMNS: list[tuple[str, str, bool]] = [
    ("code", "text", False),
    ("description", "text", False),
    ("pgm_family", "text", True),
    ("stream", "text", True),
    ("notes", "text", True),
]
_HEADER = ["code", "description", "pgm_family", "stream", "notes"]
_PK = ("code",)


def _table_docs(
    columns: list[str],
    schema: str = "codes",
    pk: tuple[str, ...] = ("code",),
    types: dict[str, str] | None = None,
    not_null: tuple[str, ...] = ("code", "description"),
) -> lrd.TableDocs:
    """Build a TableDocs with text columns unless overridden."""
    types = types or {}
    return lrd.TableDocs(
        schema=schema,
        columns=[
            lrd.DocColumn(
                name=name,
                data_type=types.get(name, "text"),
                is_nullable=name not in not_null,
                is_primary_key=name in pk,
            )
            for name in columns
        ],
    )


_DOCUMENTED = {"clm_type_cd": _table_docs(_HEADER)}


def _row(code: str = "60", description: str = "Inpatient") -> list[str]:
    return [code, description, "medicare_ffs", "nch_institutional", ""]


def _validate(
    rows: list[list[str]],
    header: list[str] | None = None,
    documented: dict[str, lrd.TableDocs] | None = None,
    max_rows: int = 1000,
    live_columns: list[tuple[str, str, bool]] | None = None,
    pk: tuple[str, ...] = _PK,
) -> list[str]:
    return lrd.validate_csv(
        "clm_type_cd",
        header if header is not None else list(_HEADER),
        rows,
        live_columns if live_columns is not None else list(_LIVE_COLUMNS),
        pk,
        documented if documented is not None else dict(_DOCUMENTED),
        max_rows,
    )


# ---------------------------------------------------------------------------
# validate_csv
# ---------------------------------------------------------------------------


def test_validate_csv_happy_path_no_issues() -> None:
    assert _validate([_row("60"), _row("40", "Outpatient")]) == []


def test_validate_csv_header_mismatch_reported() -> None:
    issues = _validate([_row()], header=["code", "wrong", "cols"])
    assert len(issues) == 1
    assert "does not equal the live table's columns" in issues[0]
    # The message advertises the reshape escape hatch, mirroring how the
    # missing-table message names --allow-missing-table.
    assert "--allow-reshaped-table clm_type_cd" in issues[0]
    assert "--dry-run" in issues[0]


def test_validate_csv_duplicate_pk_reported() -> None:
    issues = _validate([_row("60"), _row("60", "Duplicate")])
    assert len(issues) == 1
    assert "duplicate primary key" in issues[0]
    # Both line numbers are named so the author can find each offender.
    assert "line 3" in issues[0] and "line 2" in issues[0]


def test_validate_csv_no_pk_reported_not_silently_skipped() -> None:
    # A PK-less live table is a validation issue, never a silent skip of
    # duplicate detection: duplicate rows produce no dup issue (there is
    # no key to detect on) but the PK-less issue is loud in its place.
    issues = _validate([_row("60"), _row("60")], pk=())
    assert len(issues) == 1
    assert "no primary key" in issues[0]
    assert "always a mistake" in issues[0]


def test_validate_csv_unparseable_value_reported() -> None:
    live = [("code", "integer", False), ("description", "text", False)]
    issues = lrd.validate_csv(
        "clm_type_cd",
        ["code", "description"],
        [["not_a_number", "d"]],
        live,
        ("code",),
        {"clm_type_cd": _table_docs(["code", "description"])},
        1000,
    )
    assert len(issues) == 1
    assert "does not parse as integer" in issues[0]


@pytest.mark.parametrize(
    ("data_type", "good", "bad"),
    [
        ("integer", "42", "4.2"),
        ("numeric", "4.2", "x"),
        ("boolean", "true", "maybe"),
        ("date", "2026-01-31", "01/31/2026"),
        ("timestamp with time zone", "2026-01-31T00:00:00+00:00", "nope"),
    ],
)
def test_validate_csv_type_parsers(data_type: str, good: str, bad: str) -> None:
    live = [("code", "text", False), ("v", data_type, True)]
    docs = {"clm_type_cd": _table_docs(["code", "v"])}
    ok = lrd.validate_csv(
        "clm_type_cd", ["code", "v"], [["1", good]], live, ("code",), docs, 10
    )
    assert ok == []
    broken = lrd.validate_csv(
        "clm_type_cd", ["code", "v"], [["1", bad]], live, ("code",), docs, 10
    )
    assert len(broken) == 1
    assert f"does not parse as {data_type}" in broken[0]


def test_validate_csv_row_count_guardrail_uses_config_value() -> None:
    # The guardrail comes from the config knob, never a hardcoded
    # literal: 3 rows pass at max_rows=3 and fail at max_rows=2.
    rows = [_row("1"), _row("2"), _row("3")]
    assert _validate(rows, max_rows=3) == []
    issues = _validate(rows, max_rows=2)
    assert len(issues) == 1
    assert "exceeds max_rows_per_table (2)" in issues[0]


def test_validate_csv_docs_drift_reported() -> None:
    documented = {
        "clm_type_cd": _table_docs(
            ["code", "description", "pgm_family", "stream", "extra"]
        )
    }
    issues = _validate([_row()], documented=documented)
    assert len(issues) == 1
    assert "documented corpus columns disagree" in issues[0]
    assert "'notes'" in issues[0]  # undocumented
    assert "'extra'" in issues[0]  # documented but absent


def test_validate_csv_undocumented_table_reported() -> None:
    issues = _validate([_row()], documented={})
    assert len(issues) == 1
    assert "not documented in the ref corpus docs" in issues[0]


def test_validate_csv_empty_cell_in_not_null_column_reported() -> None:
    issues = _validate([["60", "", "f", "s", ""]])
    assert len(issues) == 1
    assert "'description'" in issues[0]
    assert "NOT NULL" in issues[0]


def test_validate_csv_ragged_row_reported() -> None:
    issues = _validate([["60", "Inpatient"]])
    assert len(issues) == 1
    assert "has 2 cells, expected 5" in issues[0]


def test_validate_csv_accumulates_multiple_issues() -> None:
    issues = _validate(
        [_row("60"), _row("60"), ["60", "d", "f", "s", "", "extra_cell"]],
        max_rows=2,
    )
    # Guardrail + duplicate PK + ragged row, all in one report.
    assert len(issues) == 3


# ---------------------------------------------------------------------------
# validate_csv_docs_shape (the escape-hatch validation path)
# ---------------------------------------------------------------------------


def _shape(
    header: list[str],
    rows: list[list[str]],
    documented: dict[str, lrd.TableDocs],
    max_rows: int = 1000,
) -> list[str]:
    return lrd.validate_csv_docs_shape(
        "clm_type_cd", header, rows, documented, max_rows
    )


def test_docs_shape_happy_path_no_issues() -> None:
    issues = _shape(list(_HEADER), [_row("60"), _row("40", "Out")], dict(_DOCUMENTED))
    assert issues == []


def test_docs_shape_bad_typed_value_reported() -> None:
    docs = {"clm_type_cd": _table_docs(["code", "v"], types={"v": "integer"})}
    assert _shape(["code", "v"], [["1", "7"]], docs) == []
    issues = _shape(["code", "v"], [["1", "not_int"]], docs)
    assert len(issues) == 1
    assert "does not parse as integer" in issues[0]


def test_docs_shape_unparseable_documented_type_names_vocabulary() -> None:
    # The documented data_type is a machine contract on this path: a
    # type outside the parser vocabulary is an explicit issue naming the
    # column, the offending value, and the allowed vocabulary.
    docs = {
        "clm_type_cd": _table_docs(["code", "v"], types={"v": "varchar(10)"})
    }
    issues = _shape(["code", "v"], [["1", "x"]], docs)
    assert len(issues) == 1
    assert "'varchar(10)'" in issues[0]
    assert "'v'" in issues[0]
    assert "parser vocabulary" in issues[0]
    assert "'text'" in issues[0] and "'integer'" in issues[0]


def test_docs_shape_not_null_violation_reported() -> None:
    issues = _shape(
        list(_HEADER), [["60", "", "f", "s", ""]], dict(_DOCUMENTED)
    )
    assert len(issues) == 1
    assert "'description'" in issues[0]
    assert "NOT NULL" in issues[0]


def test_docs_shape_duplicate_documented_pk_reported() -> None:
    # Duplicate rows under the docs-declared PK are caught even though
    # no live table exists to introspect a key from.
    issues = _shape(
        list(_HEADER), [_row("60"), _row("60", "Dup")], dict(_DOCUMENTED)
    )
    assert len(issues) == 1
    assert "duplicate primary key" in issues[0]


def test_docs_shape_no_documented_pk_reported() -> None:
    docs = {"clm_type_cd": _table_docs(_HEADER, pk=())}
    issues = _shape(list(_HEADER), [_row()], docs)
    assert len(issues) == 1
    assert "is_primary_key: true" in issues[0]
    assert "always a mistake" in issues[0]


def test_docs_shape_header_order_free_but_set_strict() -> None:
    # Docs column order is authoring freedom (the gate compares sets);
    # the positional checks align by name, so a reordered header still
    # validates correctly against each column's documented type.
    docs = {
        "clm_type_cd": _table_docs(
            ["code", "v"], types={"v": "integer"}, not_null=("code",)
        )
    }
    assert _shape(["v", "code"], [["7", "a"]], docs) == []
    issues = _shape(["v", "code"], [["not_int", "a"]], docs)
    assert len(issues) == 1
    assert "does not parse as integer" in issues[0]


def test_docs_shape_undocumented_table_still_checks_rows() -> None:
    # No docs shape to align against: the gate issue is reported and the
    # per-row cell counts still run (positional checks cannot).
    issues = _shape(["code", "v"], [["1", "2"], ["ragged"]], {})
    assert any("not documented in the ref corpus docs" in i for i in issues)
    assert any("has 1 cells, expected 2" in i for i in issues)


def test_docs_gate_duplicate_header_cell_named() -> None:
    # A repeated header cell fails the docs gate while both set
    # differences come out empty: without an explicit repeat message the
    # author is told only "undocumented: [], documented but absent: []",
    # which names nothing at all.
    docs = {"clm_type_cd": _table_docs(["code", "description"])}
    issues = _shape(["code", "code", "description"], [["1", "2", "d"]], docs)
    assert len(issues) == 1
    assert "repeats column(s) ['code']" in issues[0]


def test_docs_gate_duplicate_header_with_drift_reports_both() -> None:
    # A repeat alongside a genuine disagreement keeps both messages; the
    # set-difference detail is dropped only when the repeat is the whole
    # story (as in the test above).
    docs = {"clm_type_cd": _table_docs(["code", "description"])}
    issues = _shape(["code", "code", "extra"], [["1", "2", "3"]], docs)
    assert len(issues) == 2
    assert "repeats column(s) ['code']" in issues[0]
    assert "undocumented: ['extra']" in issues[1]
    assert "documented but absent: ['description']" in issues[1]


def test_docs_shape_guardrail_enforced() -> None:
    issues = _shape(
        list(_HEADER), [_row("1"), _row("2")], dict(_DOCUMENTED), max_rows=1
    )
    assert len(issues) == 1
    assert "exceeds max_rows_per_table (1)" in issues[0]


# ---------------------------------------------------------------------------
# documented_columns / documented_schemas (docs gate input)
# ---------------------------------------------------------------------------


def _doc_row(table: str, column: str, pk: bool = False) -> str:
    """One full ref docs row (the reader requires every shape field)."""
    return (
        f"- table_name: {table}\n"
        f"  column_name: {column}\n"
        f"  data_type: text\n"
        f"  is_nullable: {'false' if pk else 'true'}\n"
        f"  is_primary_key: {'true' if pk else 'false'}\n"
        f"  description: d\n"
    )


def _write_schema_yaml(folder: Path) -> None:
    """Mark a docs folder as a schema folder (it must carry schema.yaml)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "schema.yaml").write_text("description: d\n", encoding="utf-8")


def _write_ref_docs(root: Path, columns: list[str]) -> Path:
    """Write a minimal ref corpus docs tree with a codes/columns.yaml."""
    codes = root / "ref" / "codes"
    _write_schema_yaml(codes)
    text = "".join(
        _doc_row("clm_type_cd", col, pk=(col == "code")) for col in columns
    )
    (codes / "columns.yaml").write_text(text, encoding="utf-8")
    return root / "ref"


def _expected_docs(columns: list[str]) -> dict[str, lrd.TableDocs]:
    """The TableDocs that `_write_ref_docs` output parses to."""
    return {
        "clm_type_cd": lrd.TableDocs(
            schema="codes",
            columns=[
                lrd.DocColumn(
                    name=col,
                    data_type="text",
                    is_nullable=col != "code",
                    is_primary_key=col == "code",
                )
                for col in columns
            ],
        )
    }


def test_documented_columns_reads_columns_yaml(tmp_path: Path) -> None:
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    assert lrd.documented_columns(docs_dir) == _expected_docs(_HEADER)


def test_documented_columns_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Ref docs folder not found"):
        lrd.documented_columns(tmp_path / "nope")


def test_documented_columns_unparsable_yaml_raises(tmp_path: Path) -> None:
    codes = tmp_path / "codes"
    codes.mkdir()
    (codes / "columns.yaml").write_text("a: : :\n  - bad\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Failed to read or parse ref docs"):
        lrd.documented_columns(tmp_path)


@pytest.mark.parametrize(
    "row",
    [
        pytest.param("- column_name: code\n", id="missing_table_name"),
        pytest.param(
            "- table_name: t\n  column_name: code\n", id="missing_data_type"
        ),
        pytest.param(
            "- table_name: t\n  column_name: code\n  data_type: text\n",
            id="missing_is_nullable",
        ),
        pytest.param(
            "- table_name: t\n  column_name: code\n  data_type: text\n"
            "  is_nullable: maybe\n",
            id="non_bool_is_nullable",
        ),
        pytest.param(
            "- table_name: t\n  column_name: code\n  data_type: text\n"
            "  is_nullable: true\n  is_primary_key: yes please\n",
            id="non_bool_is_primary_key",
        ),
    ],
)
def test_documented_columns_malformed_row_raises(
    tmp_path: Path, row: str
) -> None:
    codes = tmp_path / "codes"
    codes.mkdir()
    (codes / "columns.yaml").write_text(row, encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed ref docs row"):
        lrd.documented_columns(tmp_path)


def test_documented_columns_reads_shard_folder(tmp_path: Path) -> None:
    shards = tmp_path / "codes" / "columns"
    shards.mkdir(parents=True)
    (shards / "clm.yaml").write_text(
        _doc_row("clm_type_cd", "code", pk=True), encoding="utf-8"
    )
    assert lrd.documented_columns(tmp_path) == {
        "clm_type_cd": lrd.TableDocs(
            schema="codes",
            columns=[lrd.DocColumn("code", "text", False, True)],
        )
    }


def test_documented_columns_shard_named_columns_yaml_read_once(
    tmp_path: Path,
) -> None:
    # A shard literally named columns/columns.yaml matches BOTH discovery
    # globs; path-deduplication reads it once (double-counting would trip
    # the duplicate-column error below).
    shards = tmp_path / "codes" / "columns"
    shards.mkdir(parents=True)
    (shards / "columns.yaml").write_text(
        _doc_row("clm_type_cd", "code", pk=True), encoding="utf-8"
    )
    docs = lrd.documented_columns(tmp_path)
    assert len(docs["clm_type_cd"].columns) == 1


def test_documented_columns_duplicate_rows_raise_naming_column_and_files(
    tmp_path: Path,
) -> None:
    # A duplicated (table, column) is an explicit error naming the column
    # and the offending files — not the old useless "undocumented: [],
    # documented but absent: []" list-vs-set diagnostic.
    codes = tmp_path / "codes"
    shards = codes / "columns"
    shards.mkdir(parents=True)
    (codes / "columns.yaml").write_text(
        _doc_row("clm_type_cd", "code", pk=True), encoding="utf-8"
    )
    (shards / "clm.yaml").write_text(
        _doc_row("clm_type_cd", "code"), encoding="utf-8"
    )
    with pytest.raises(ValueError) as excinfo:
        lrd.documented_columns(tmp_path)
    message = str(excinfo.value)
    assert "Duplicate documented ref column" in message
    assert "clm_type_cd.code" in message
    assert "columns.yaml" in message and "clm.yaml" in message


def test_documented_columns_outside_schema_folder_raises(
    tmp_path: Path,
) -> None:
    (tmp_path / "columns.yaml").write_text(
        _doc_row("clm_type_cd", "code", pk=True), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="outside a schema folder"):
        lrd.documented_columns(tmp_path)


def test_documented_columns_table_across_two_schemas_raises(
    tmp_path: Path,
) -> None:
    # A table's docs must live under exactly one schema folder: the same
    # table documented under two folders is an explicit error naming the
    # table and both schemas — not a silent first-folder-wins.
    codes = tmp_path / "codes"
    xwalk = tmp_path / "xwalk"
    codes.mkdir()
    xwalk.mkdir()
    (codes / "columns.yaml").write_text(
        _doc_row("clm_type_cd", "code", pk=True), encoding="utf-8"
    )
    (xwalk / "columns.yaml").write_text(
        _doc_row("clm_type_cd", "description"), encoding="utf-8"
    )
    with pytest.raises(ValueError) as excinfo:
        lrd.documented_columns(tmp_path)
    message = str(excinfo.value)
    assert "'clm_type_cd'" in message
    assert "'codes'" in message and "'xwalk'" in message
    assert "exactly one schema folder" in message


def test_documented_schemas_returns_folder_names(tmp_path: Path) -> None:
    _write_schema_yaml(tmp_path / "codes")
    _write_schema_yaml(tmp_path / "xwalk")
    (tmp_path / "data_source.yaml").write_text("x: 1\n", encoding="utf-8")
    assert lrd.documented_schemas(tmp_path) == {"codes", "xwalk"}


def test_documented_schemas_ignores_folder_without_schema_yaml(
    tmp_path: Path,
) -> None:
    # A directory counts as a documented schema only when it carries a
    # schema.yaml (every corpus schema folder has one) — a stray
    # non-schema directory under the source folder must not become a
    # legal data_ref/ folder name.
    _write_schema_yaml(tmp_path / "codes")
    (tmp_path / "scratch").mkdir()
    assert lrd.documented_schemas(tmp_path) == {"codes"}


def test_documented_schemas_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Ref docs folder not found"):
        lrd.documented_schemas(tmp_path / "nope")


# ---------------------------------------------------------------------------
# list_csv_files / read_csv / compute_csv_sha256
# ---------------------------------------------------------------------------


def _write_csv(
    tmp_path: Path, name: str, text: str, schema: str = "codes"
) -> Path:
    """Write a CSV at data_ref/<schema>/<name>; return the data_ref dir."""
    csv_dir = tmp_path / "data_ref"
    folder = csv_dir / schema
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")
    return csv_dir


def test_list_csv_files_sorted(tmp_path: Path) -> None:
    csv_dir = _write_csv(tmp_path, "b_tbl.csv", "code\n")
    _write_csv(tmp_path, "a_tbl.csv", "code\n")
    # Stray non-CSV files are silently skipped at BOTH levels: the
    # data_ref/ top level and inside a schema folder.
    (csv_dir / "notes.md").write_text("ignored", encoding="utf-8")
    (csv_dir / "codes" / "notes.md").write_text("ignored", encoding="utf-8")
    names = [p.name for p in lrd.list_csv_files(csv_dir, {"codes"})]
    assert names == ["a_tbl.csv", "b_tbl.csv"]


def test_list_csv_files_case_variant_extension_in_folder_raises(
    tmp_path: Path,
) -> None:
    csv_dir = _write_csv(tmp_path, "tbl.CSV", "code\n")
    with pytest.raises(ValueError, match="non-lowercase extension"):
        lrd.list_csv_files(csv_dir, {"codes"})


def test_list_csv_files_empty_dir_raises(tmp_path: Path) -> None:
    csv_dir = tmp_path / "data_ref"
    csv_dir.mkdir()
    with pytest.raises(ValueError, match="No .csv files found"):
        lrd.list_csv_files(csv_dir, {"codes"})


def test_list_csv_files_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        lrd.list_csv_files(tmp_path / "nope", {"codes"})


def test_list_csv_files_top_level_csv_raises(tmp_path: Path) -> None:
    csv_dir = tmp_path / "data_ref"
    csv_dir.mkdir()
    (csv_dir / "stray_tbl.csv").write_text("code\n", encoding="utf-8")
    with pytest.raises(ValueError, match="directly under"):
        lrd.list_csv_files(csv_dir, {"codes"})


def test_list_csv_files_top_level_case_variant_csv_raises(
    tmp_path: Path,
) -> None:
    # The top-level guard matches the extension case-insensitively, so a
    # misplaced STRAY.CSV is as loud as a misplaced stray.csv — without
    # that, it would be silently skipped as a "non-CSV stray file".
    csv_dir = tmp_path / "data_ref"
    csv_dir.mkdir()
    (csv_dir / "stray_tbl.CSV").write_text("code\n", encoding="utf-8")
    with pytest.raises(ValueError, match="directly under"):
        lrd.list_csv_files(csv_dir, {"codes"})


def test_list_csv_files_doubly_nested_csv_raises(tmp_path: Path) -> None:
    nested = tmp_path / "data_ref" / "codes" / "deeper"
    nested.mkdir(parents=True)
    (nested / "tbl.csv").write_text("code\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nested deeper than one schema"):
        lrd.list_csv_files(tmp_path / "data_ref", {"codes"})


def test_list_csv_files_unknown_folder_raises_naming_valid_schemas(
    tmp_path: Path,
) -> None:
    csv_dir = _write_csv(tmp_path, "tbl.csv", "code\n", schema="mystery")
    with pytest.raises(ValueError) as excinfo:
        lrd.list_csv_files(csv_dir, {"codes", "xwalk"})
    message = str(excinfo.value)
    assert "does not name a documented schema" in message
    assert "'codes'" in message and "'xwalk'" in message


def test_list_csv_files_duplicate_stem_across_folders_raises(
    tmp_path: Path,
) -> None:
    # One physical namespace underneath: two folders may not both claim a
    # stem — the error names both paths (last-one-wins is never OK).
    csv_dir = _write_csv(tmp_path, "tbl.csv", "code\n", schema="codes")
    _write_csv(tmp_path, "tbl.csv", "code\n", schema="xwalk")
    with pytest.raises(ValueError) as excinfo:
        lrd.list_csv_files(csv_dir, {"codes", "xwalk"})
    message = str(excinfo.value)
    assert "Duplicate ref CSV stem 'tbl'" in message
    assert str(csv_dir / "codes" / "tbl.csv") in message
    assert str(csv_dir / "xwalk" / "tbl.csv") in message


def test_list_csv_files_multiple_schema_folders_discovered(
    tmp_path: Path,
) -> None:
    csv_dir = _write_csv(tmp_path, "a_tbl.csv", "code\n", schema="codes")
    _write_csv(tmp_path, "b_tbl.csv", "code\n", schema="xwalk")
    names = [p.name for p in lrd.list_csv_files(csv_dir, {"codes", "xwalk"})]
    assert names == ["a_tbl.csv", "b_tbl.csv"]


def test_read_csv_header_rows_and_hash(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text('code,notes\n60,"a, quoted note"\n70,\n', encoding="utf-8")
    header, rows, csv_sha256 = lrd.read_csv(p)
    assert header == ["code", "notes"]
    assert rows == [["60", "a, quoted note"], ["70", ""]]
    # The hash is of the exact bytes read (single-read provenance).
    assert csv_sha256 == lrd.compute_csv_sha256(p.read_bytes())


def test_read_csv_empty_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        lrd.read_csv(p)


def test_read_csv_strips_excel_bom_from_header(tmp_path: Path) -> None:
    # An Excel-saved UTF-8 CSV carries a BOM; utf-8-sig decoding strips
    # it so the first header cell is 'code', not BOM-prefixed (which
    # would surface as a cryptic header mismatch).
    p = tmp_path / "t.csv"
    p.write_bytes(b"\xef\xbb\xbfcode,notes\n60,\n")
    header, rows, _ = lrd.read_csv(p)
    assert header == ["code", "notes"]
    assert rows == [["60", ""]]


def test_read_csv_invalid_utf8_raises(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_bytes(b"code\n\xff\xfe garbage\n")
    with pytest.raises(ValueError, match="Failed to parse ref CSV"):
        lrd.read_csv(p)


class _UnreadableCsvPath:
    """A CSV path whose read raises a non-FileNotFoundError OSError."""

    stem = "clm_type_cd"
    name = "clm_type_cd.csv"

    def read_bytes(self) -> bytes:
        raise PermissionError("permission denied")


def test_read_csv_oserror_surfaces_as_valueerror() -> None:
    # ANY OSError on the byte read (not just FileNotFoundError) maps to
    # the documented ValueError contract, so validate_all's per-CSV
    # except-ValueError accumulation catches it instead of crashing.
    with pytest.raises(ValueError, match="Failed to read ref CSV"):
        lrd.read_csv(_UnreadableCsvPath())


def test_read_csv_hash_reflects_single_read_not_later_file_state(
    tmp_path: Path,
) -> None:
    # Single-read provenance: mutate the file AFTER read_csv and confirm
    # the returned hash still describes the bytes that were parsed —
    # there is no second read that could pick up the mutation (the
    # TOCTOU the old two-read flow allowed).
    p = tmp_path / "t.csv"
    original = b"code,notes\n60,\n"
    p.write_bytes(original)
    _, rows, csv_sha256 = lrd.read_csv(p)
    p.write_bytes(b"code,notes\n61,tampered\n")
    assert csv_sha256 == lrd.compute_csv_sha256(original)
    assert csv_sha256 != lrd.compute_csv_sha256(p.read_bytes())
    assert rows == [["60", ""]]


def test_compute_csv_sha256_hashes_raw_bytes() -> None:
    # The hash describes the exact bytes loaded: line-ending variants are
    # DIFFERENT content (no universal-newline normalization — the CSV
    # parser, not the hash, interprets line endings).
    assert lrd.compute_csv_sha256(b"code\n60\n") != lrd.compute_csv_sha256(
        b"code\r\n60\r\n"
    )
    assert lrd.compute_csv_sha256(b"code\n60\n") == lrd.compute_csv_sha256(
        b"code\n60\n"
    )


# ---------------------------------------------------------------------------
# fetch_ref_tables / fetch_table_columns / fetch_pk_columns (introspection)
# ---------------------------------------------------------------------------


def test_fetch_ref_tables_excludes_infra_tables(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # information_schema returns code-set tables alongside the infra tables;
    # only the code-set tables survive the INFRA_TABLES subtraction.
    fake_cursor.fetchall.return_value = [
        ("clm_type_cd",),
        ("bill_fac_type_cd",),
        ("ref_load_audit",),
        ("ddl_versions",),
    ]
    assert lrd.fetch_ref_tables(fake_conn, "reference") == {
        "clm_type_cd",
        "bill_fac_type_cd",
    }


def test_fetch_table_columns_maps_nullable_string_to_bool(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # is_nullable arrives as the strings "YES"/"NO"; only "YES" maps to True.
    fake_cursor.fetchall.return_value = [
        ("code", "text", "NO"),
        ("description", "text", "NO"),
        ("notes", "text", "YES"),
    ]
    assert lrd.fetch_table_columns(fake_conn, "reference", "clm_type_cd") == [
        ("code", "text", False),
        ("description", "text", False),
        ("notes", "text", True),
    ]


def test_fetch_pk_columns_builds_tuple_in_key_order(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # The query orders by kcu.ordinal_position; the tuple preserves that order.
    fake_cursor.fetchall.return_value = [("code",), ("effective_date",)]
    assert lrd.fetch_pk_columns(fake_conn, "reference", "clm_type_cd") == (
        "code",
        "effective_date",
    )


def test_fetch_pk_columns_empty_when_no_pk(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    fake_cursor.fetchall.return_value = []
    assert lrd.fetch_pk_columns(fake_conn, "reference", "clm_type_cd") == ()


# ---------------------------------------------------------------------------
# validate_all (filename resolution + accumulation + escape hatches)
# ---------------------------------------------------------------------------


def _patch_introspection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lrd, "fetch_ref_tables", lambda conn, schema: {"clm_type_cd"}
    )
    monkeypatch.setattr(
        lrd,
        "fetch_table_columns",
        lambda conn, schema, table: list(_LIVE_COLUMNS),
    )
    monkeypatch.setattr(
        lrd, "fetch_pk_columns", lambda conn, schema, table: _PK
    )


def _append_doc_rows(docs_dir: Path, text: str) -> None:
    """Append extra rows to the codes/columns.yaml a test already wrote."""
    columns_yaml = docs_dir / "codes" / "columns.yaml"
    columns_yaml.write_text(
        columns_yaml.read_text(encoding="utf-8") + text, encoding="utf-8"
    )


def test_validate_all_unknown_table_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    csv_dir = _write_csv(tmp_path, "ghost_tbl.csv", "code\n60\n")
    loadable, issues = lrd.validate_all(
        MagicMock(),
        "reference",
        lrd.list_csv_files(csv_dir, {"codes"}),
        docs_dir,
        1000,
    )
    assert loadable == {}
    unknown = [i for i in issues if "no table named 'ghost_tbl'" in i]
    assert len(unknown) == 1
    # The error names the MR escape hatch.
    assert "--allow-missing-table ghost_tbl" in unknown[0]


def test_validate_all_unreadable_csv_accumulated_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # read_csv's ValueError (here: an empty file with no header row) is
    # accumulated as an issue and validation moves on — one bad CSV must
    # not abort the whole-corpus report with an exception.
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    csv_dir = _write_csv(tmp_path, "clm_type_cd.csv", "")
    loadable, issues = lrd.validate_all(
        MagicMock(),
        "reference",
        lrd.list_csv_files(csv_dir, {"codes"}),
        docs_dir,
        1000,
    )
    assert loadable == {}
    assert any("is empty (no header row)" in i for i in issues)


def test_validate_all_malformed_docs_raise_not_accumulated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other side of that boundary: a bad CSV is accumulated, but docs
    # that will not parse abort the run — the gate cannot be evaluated at
    # all against broken docs, so validate_all propagates the ValueError
    # (which main maps to a logged exit 1) instead of returning issues.
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    _append_doc_rows(
        docs_dir,
        "- table_name: clm_type_cd\n  column_name: broken\n"
        "  is_nullable: true\n",
    )
    csv_dir = _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    with pytest.raises(ValueError, match="Malformed ref docs row"):
        lrd.validate_all(
            MagicMock(),
            "reference",
            lrd.list_csv_files(csv_dir, {"codes"}),
            docs_dir,
            1000,
        )


def test_validate_all_happy_path_returns_loadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    csv_dir = _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n"
        "60,Inpatient,medicare_ffs,nch_institutional,\n",
    )
    loadable, issues = lrd.validate_all(
        MagicMock(),
        "reference",
        lrd.list_csv_files(csv_dir, {"codes"}),
        docs_dir,
        1000,
    )
    assert issues == []
    header, rows, csv_sha256 = loadable["clm_type_cd"]
    assert header == _HEADER
    assert rows == [["60", "Inpatient", "medicare_ffs", "nch_institutional", ""]]
    # Single-read provenance: the hash is of the bytes validate_all read.
    assert csv_sha256 == lrd.compute_csv_sha256(
        (csv_dir / "codes" / "clm_type_cd.csv").read_bytes()
    )


def test_validate_all_csv_under_wrong_schema_folder_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Discovery only proves the folder names SOME documented schema; the
    # CSV must live under ITS table's documented schema folder. A
    # misfiled CSV (a valid folder, but not this table's) is an issue
    # naming the actual folder, the documented folder, and the expected
    # path; the same CSV under the right folder produces no issue.
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, _HEADER)  # documented under codes/
    _write_schema_yaml(docs_dir / "xwalk")  # a real, but wrong, schema
    csv_text = "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n"
    csv_dir = _write_csv(tmp_path, "clm_type_cd.csv", csv_text, schema="xwalk")
    _, issues = lrd.validate_all(
        MagicMock(),
        "reference",
        lrd.list_csv_files(csv_dir, lrd.documented_schemas(docs_dir)),
        docs_dir,
        1000,
    )
    assert len(issues) == 1
    assert "'xwalk'" in issues[0] and "'codes'" in issues[0]
    assert "data_ref/codes/clm_type_cd.csv" in issues[0]
    # Moved into its documented schema folder, the CSV is issue-free.
    (csv_dir / "xwalk" / "clm_type_cd.csv").unlink()
    _write_csv(tmp_path, "clm_type_cd.csv", csv_text)
    _, issues = lrd.validate_all(
        MagicMock(),
        "reference",
        lrd.list_csv_files(csv_dir, lrd.documented_schemas(docs_dir)),
        docs_dir,
        1000,
    )
    assert issues == []


def test_validate_all_db_table_without_csv_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bidirectional drift, DB direction: a ref-schema table with no
    # matching data_ref CSV is a loud issue, not silence. An undocumented
    # table's message uses the literal <schema> placeholder (its schema
    # folder is unknowable).
    _patch_introspection(monkeypatch)
    monkeypatch.setattr(
        lrd,
        "fetch_ref_tables",
        lambda conn, schema: {"clm_type_cd", "orphan_tbl"},
    )
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    csv_dir = _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    _, issues = lrd.validate_all(
        MagicMock(),
        "reference",
        lrd.list_csv_files(csv_dir, {"codes"}),
        docs_dir,
        1000,
    )
    assert len(issues) == 1
    assert "orphan_tbl" in issues[0]
    assert "has no data_ref/<schema>/orphan_tbl.csv" in issues[0]
    # The remedy names the retire escape hatch.
    assert "--allow-dropped-table orphan_tbl" in issues[0]


def test_validate_all_documented_table_without_csv_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bidirectional drift, docs direction: a documented ref-source table
    # with no CSV is a loud issue naming its schema-folder CSV path.
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    _append_doc_rows(docs_dir, _doc_row("ghost_docs_tbl", "code", pk=True))
    csv_dir = _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    _, issues = lrd.validate_all(
        MagicMock(),
        "reference",
        lrd.list_csv_files(csv_dir, {"codes"}),
        docs_dir,
        1000,
    )
    assert len(issues) == 1
    assert "ghost_docs_tbl" in issues[0]
    assert "documented in the ref corpus docs but has no" in issues[0]
    assert "data_ref/codes/ghost_docs_tbl.csv" in issues[0]


def test_validate_all_allow_missing_table_downgrades_only_named_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The exemption is per-table: the named table's missing-from-DB error
    # becomes a warning (its CSV still validated against the docs shape);
    # a different missing table still errors.
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    _append_doc_rows(docs_dir, _doc_row("new_tbl", "code", pk=True))
    csv_dir = _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    _write_csv(tmp_path, "new_tbl.csv", "code\n1\n")
    _write_csv(tmp_path, "other_tbl.csv", "code\n1\n")
    with caplog.at_level("WARNING"):
        _, issues = lrd.validate_all(
            MagicMock(),
            "reference",
            lrd.list_csv_files(csv_dir, {"codes"}),
            docs_dir,
            1000,
            allow_missing_tables={"new_tbl"},
        )
    # other_tbl (not exempted) still gets the missing-table error;
    # new_tbl (exempted) does not.
    assert any("no table named 'other_tbl'" in i for i in issues)
    assert not any("no table named 'new_tbl'" in i for i in issues)
    assert any(
        "new_tbl" in r.message and "--allow-missing-table" in r.message
        for r in caplog.records
        if r.levelname == "WARNING"
    )


def test_validate_all_allow_missing_table_still_validates_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An exempted table's CSV is still held to the docs gate: an
    # undocumented new table (or a header/docs mismatch) fails even
    # under --allow-missing-table.
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    csv_dir = _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    _write_csv(tmp_path, "new_tbl.csv", "code\n1\n2,ragged\n")
    _, issues = lrd.validate_all(
        MagicMock(),
        "reference",
        lrd.list_csv_files(csv_dir, {"codes"}),
        docs_dir,
        1000,
        allow_missing_tables={"new_tbl"},
    )
    assert any(
        "new_tbl: not documented in the ref corpus docs" in i for i in issues
    )
    assert any("has 2 cells, expected 1" in i for i in issues)


def test_validate_all_allow_missing_table_catches_duplicate_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Docs-shape validation is full-strength: a duplicate value under the
    # docs-declared PK fails an --allow-missing-table CSV even though no
    # live table exists to introspect a key from.
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    _append_doc_rows(docs_dir, _doc_row("new_tbl", "code", pk=True))
    csv_dir = _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    _write_csv(tmp_path, "new_tbl.csv", "code\n1\n1\n")
    _, issues = lrd.validate_all(
        MagicMock(),
        "reference",
        lrd.list_csv_files(csv_dir, {"codes"}),
        docs_dir,
        1000,
        allow_missing_tables={"new_tbl"},
    )
    assert any(
        "new_tbl" in i and "duplicate primary key" in i for i in issues
    )


def test_validate_all_allow_reshaped_table_validates_docs_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The reshape escape: the CSV matches the documented (new) shape but
    # not the live (old) columns. Without the flag the live-column check
    # fails; with it the table validates against the docs and passes.
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, ["code", "description"])
    csv_dir = _write_csv(
        tmp_path, "clm_type_cd.csv", "code,description\n60,Inpatient\n"
    )
    _, strict_issues = lrd.validate_all(
        MagicMock(),
        "reference",
        lrd.list_csv_files(csv_dir, {"codes"}),
        docs_dir,
        1000,
    )
    assert any(
        "does not equal the live table's columns" in i for i in strict_issues
    )
    with caplog.at_level("WARNING"):
        loadable, issues = lrd.validate_all(
            MagicMock(),
            "reference",
            lrd.list_csv_files(csv_dir, {"codes"}),
            docs_dir,
            1000,
            allow_reshaped_tables={"clm_type_cd"},
        )
    assert issues == []
    # Docs-shape-validated tables never ride into the write phase.
    assert loadable == {}
    assert any(
        "--allow-reshaped-table" in r.message
        for r in caplog.records
        if r.levelname == "WARNING"
    )


def test_validate_all_allow_dropped_table_downgrades_only_named_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The retire escape downgrades exactly the named table's
    # DB-table-without-CSV drift issue — a different CSV-less table still
    # errors.
    _patch_introspection(monkeypatch)
    monkeypatch.setattr(
        lrd,
        "fetch_ref_tables",
        lambda conn, schema: {"clm_type_cd", "orphan_tbl", "second_orphan"},
    )
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    csv_dir = _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    with caplog.at_level("WARNING"):
        _, issues = lrd.validate_all(
            MagicMock(),
            "reference",
            lrd.list_csv_files(csv_dir, {"codes"}),
            docs_dir,
            1000,
            allow_dropped_tables={"orphan_tbl"},
        )
    assert not any("orphan_tbl:" in i for i in issues)
    assert any("second_orphan" in i for i in issues)
    assert any(
        "orphan_tbl" in r.message and "--allow-dropped-table" in r.message
        for r in caplog.records
        if r.levelname == "WARNING"
    )


def test_validate_all_inert_exemptions_warned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # CI computes each exemption from the MR's own diff, so a name that
    # downgrades nothing (a typo, or a stale entry) is a broken
    # computation that fails nothing on its own — every flag reports its
    # unused names so the dry-run log shows it.
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    csv_dir = _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    with caplog.at_level("WARNING"):
        _, issues = lrd.validate_all(
            MagicMock(),
            "reference",
            lrd.list_csv_files(csv_dir, {"codes"}),
            docs_dir,
            1000,
            allow_missing_tables={"typo_tbl"},
            allow_reshaped_tables={"typo_tbl"},
            allow_dropped_tables={"typo_tbl"},
        )
    # The inert names change no outcome — that is exactly why they need
    # the warning.
    assert issues == []
    warnings = [
        r.message for r in caplog.records if r.levelname == "WARNING"
    ]
    for flag in (
        "--allow-missing-table",
        "--allow-reshaped-table",
        "--allow-dropped-table",
    ):
        assert any(flag in m and "typo_tbl" in m for m in warnings)


def test_validate_all_matching_exemption_not_warned_as_inert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The counterpart: an exemption that actually downgrades something
    # gets its downgrade warning and never the inert-exemption one.
    _patch_introspection(monkeypatch)
    docs_dir = _write_ref_docs(tmp_path, ["code", "description"])
    csv_dir = _write_csv(
        tmp_path, "clm_type_cd.csv", "code,description\n60,Inpatient\n"
    )
    with caplog.at_level("WARNING"):
        lrd.validate_all(
            MagicMock(),
            "reference",
            lrd.list_csv_files(csv_dir, {"codes"}),
            docs_dir,
            1000,
            allow_reshaped_tables={"clm_type_cd"},
        )
    assert not any("matched no" in r.message for r in caplog.records)


def test_validate_all_multi_csv_accumulates_issues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Issues from every CSV are accumulated into one report — the loader
    # never stops at the first bad file.
    _patch_introspection(monkeypatch)
    monkeypatch.setattr(
        lrd,
        "fetch_ref_tables",
        lambda conn, schema: {"clm_type_cd", "b_tbl"},
    )
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    csv_dir = _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        # Duplicate PK — one issue.
        "code,description,pgm_family,stream,notes\n"
        "60,Inpatient,f,s,\n60,Duplicate,f,s,\n",
    )
    # Wrong header for the (mocked) live columns — another issue.
    _write_csv(tmp_path, "b_tbl.csv", "wrong\nx\n")
    _, issues = lrd.validate_all(
        MagicMock(),
        "reference",
        lrd.list_csv_files(csv_dir, {"codes"}),
        docs_dir,
        1000,
    )
    assert any("duplicate primary key" in i for i in issues)
    assert any(
        "b_tbl" in i and "does not equal the live table's columns" in i
        for i in issues
    )


# ---------------------------------------------------------------------------
# load_tables (write phase incl. the audit row)
# ---------------------------------------------------------------------------


def test_load_tables_truncates_inserts_and_writes_audit(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    loadable = {
        "clm_type_cd": (
            list(_HEADER),
            [_row("60"), _row("40", "Outpatient")],
            "hash60",
        )
    }
    lrd.load_tables(fake_conn, loadable)
    statements = [repr(c.args[0]) for c in fake_cursor.execute.call_args_list]
    assert any("truncate table" in s and "clm_type_cd" in s for s in statements)
    inserts = [s for s in statements if "insert into" in s and "clm_type_cd" in s]
    assert len(inserts) == 2
    audit_calls = [
        c
        for c in fake_cursor.execute.call_args_list
        if isinstance(c.args[0], str)
        and c.args[0].startswith("insert into ref_load_audit")
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0].args[1] == ("clm_type_cd", "hash60", 2)
    fake_conn.commit.assert_called_once()
    fake_conn.rollback.assert_not_called()


def test_load_tables_multi_table_single_commit(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # Every table reloads inside ONE transaction: per-table truncates,
    # inserts, and audit rows, then exactly one commit at the end.
    loadable = {
        "clm_type_cd": (list(_HEADER), [_row("60")], "hash_a"),
        "bill_fac_type_cd": (["code"], [["1"], ["2"]], "hash_b"),
    }
    lrd.load_tables(fake_conn, loadable)
    statements = [repr(c.args[0]) for c in fake_cursor.execute.call_args_list]
    truncates = [s for s in statements if "truncate table" in s]
    assert len(truncates) == 2
    audit_calls = [
        c
        for c in fake_cursor.execute.call_args_list
        if isinstance(c.args[0], str)
        and c.args[0].startswith("insert into ref_load_audit")
    ]
    assert len(audit_calls) == 2
    audit_binds = {c.args[1] for c in audit_calls}
    assert audit_binds == {
        ("clm_type_cd", "hash_a", 1),
        ("bill_fac_type_cd", "hash_b", 2),
    }
    fake_conn.commit.assert_called_once()
    fake_conn.rollback.assert_not_called()


def test_load_tables_binds_empty_cell_as_none(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    loadable = {
        "clm_type_cd": (list(_HEADER), [["60", "Inpatient", "", "", ""]], "h")
    }
    lrd.load_tables(fake_conn, loadable)
    row_binds = [
        c.args[1]
        for c in fake_cursor.execute.call_args_list
        if len(c.args) > 1 and isinstance(c.args[1], list)
    ]
    assert row_binds == [["60", "Inpatient", None, None, None]]


def test_load_tables_rolls_back_and_reraises_on_failure(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    fake_cursor.execute.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        lrd.load_tables(
            fake_conn, {"clm_type_cd": (list(_HEADER), [_row()], "h")}
        )
    fake_conn.rollback.assert_called_once()
    fake_conn.commit.assert_not_called()


def test_load_tables_rollback_failure_preserves_original_exception(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # A dead connection makes rollback() raise too; the ORIGINAL failure
    # must stay the raised exception (the rollback error is only logged),
    # or the root cause would be masked.
    fake_cursor.execute.side_effect = RuntimeError("original boom")
    fake_conn.rollback.side_effect = RuntimeError("rollback also dead")
    with pytest.raises(RuntimeError, match="original boom"):
        lrd.load_tables(
            fake_conn, {"clm_type_cd": (list(_HEADER), [_row()], "h")}
        )
    fake_conn.rollback.assert_called_once()
    fake_conn.commit.assert_not_called()


# ---------------------------------------------------------------------------
# check_freshness (--check verdicts)
# ---------------------------------------------------------------------------


def test_check_freshness_current_table_passes(
    tmp_path: Path, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    csv_dir = _write_csv(tmp_path, "clm_type_cd.csv", "code\n60\n")
    current_hash = lrd.compute_csv_sha256(
        (csv_dir / "codes" / "clm_type_cd.csv").read_bytes()
    )
    fake_cursor.fetchone.return_value = (current_hash,)
    issues = lrd.check_freshness(
        fake_conn, lrd.list_csv_files(csv_dir, {"codes"}), {"clm_type_cd"}
    )
    assert issues == []


def test_check_freshness_stale_table_reported(
    tmp_path: Path, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    csv_dir = _write_csv(tmp_path, "clm_type_cd.csv", "code\n60\n")
    fake_cursor.fetchone.return_value = ("someolderhash",)
    issues = lrd.check_freshness(
        fake_conn, lrd.list_csv_files(csv_dir, {"codes"}), {"clm_type_cd"}
    )
    assert len(issues) == 1
    assert "stale" in issues[0]


def test_check_freshness_never_loaded_names_loader_remedy(
    tmp_path: Path, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # Table exists but no audit row: the remedy is running the loader.
    csv_dir = _write_csv(tmp_path, "clm_type_cd.csv", "code\n60\n")
    fake_cursor.fetchone.return_value = None
    issues = lrd.check_freshness(
        fake_conn, lrd.list_csv_files(csv_dir, {"codes"}), {"clm_type_cd"}
    )
    assert len(issues) == 1
    assert "never loaded" in issues[0]
    assert "the table exists; run load_ref_data.py" in issues[0]


def test_check_freshness_missing_table_names_migration_remedy(
    tmp_path: Path, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # Table absent from the reference schema entirely: the remedy is
    # applying the ref migration, not (just) running the loader.
    csv_dir = _write_csv(tmp_path, "clm_type_cd.csv", "code\n60\n")
    issues = lrd.check_freshness(
        fake_conn, lrd.list_csv_files(csv_dir, {"codes"}), set()
    )
    assert len(issues) == 1
    assert "no such table in the reference schema" in issues[0]
    assert "apply the ref migration" in issues[0]
    # No audit query for a missing table.
    fake_cursor.execute.assert_not_called()


def test_check_freshness_query_uses_audit_pk_tiebreaker(
    tmp_path: Path, fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # Two audit rows in one transaction share a now() loaded_ts; the
    # audit_id PK is the deterministic latest-row tiebreaker.
    csv_dir = _write_csv(tmp_path, "clm_type_cd.csv", "code\n60\n")
    fake_cursor.fetchone.return_value = ("h",)
    lrd.check_freshness(
        fake_conn, lrd.list_csv_files(csv_dir, {"codes"}), {"clm_type_cd"}
    )
    query = fake_cursor.execute.call_args.args[0]
    assert "order by loaded_ts desc, audit_id desc limit 1" in query


def test_check_freshness_oserror_surfaces_as_valueerror(
    fake_conn: MagicMock, fake_cursor: MagicMock
) -> None:
    # ANY OSError on the byte read (not just FileNotFoundError) surfaces
    # as the documented ValueError contract, matching read_csv — main's
    # error mapping depends on it.
    with pytest.raises(ValueError, match="Failed to read ref CSV"):
        lrd.check_freshness(
            fake_conn, [_UnreadableCsvPath()], {"clm_type_cd"}
        )


# ---------------------------------------------------------------------------
# run (orchestration)
# ---------------------------------------------------------------------------


def _base_config(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "csv_dir": str(tmp_path / "data_ref"),
        "docs_dir": str(tmp_path / "ref"),
        "database": "metadata_db",
        "schema": "reference",
    }
    config.update(overrides)
    return config


def _patch_run_env(
    monkeypatch: pytest.MonkeyPatch, fake_conn: MagicMock
) -> None:
    for var in lrd.ENV_VARS:
        monkeypatch.setenv(var, "x")
    monkeypatch.setattr(lrd.psycopg2, "connect", lambda **kwargs: fake_conn)


def test_run_dry_run_validates_and_never_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_conn: MagicMock
) -> None:
    _patch_introspection(monkeypatch)
    _patch_run_env(monkeypatch, fake_conn)
    _write_ref_docs(tmp_path, _HEADER)
    _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    load_calls: list[Any] = []
    monkeypatch.setattr(
        lrd, "load_tables", lambda *a, **k: load_calls.append(a)
    )
    lrd.run(_base_config(tmp_path), check=False, dry_run=True)
    assert load_calls == []
    fake_conn.commit.assert_not_called()


def test_run_real_load_calls_load_tables_with_single_read_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_conn: MagicMock
) -> None:
    _patch_introspection(monkeypatch)
    _patch_run_env(monkeypatch, fake_conn)
    _write_ref_docs(tmp_path, _HEADER)
    csv_dir = _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    csv_path = csv_dir / "codes" / "clm_type_cd.csv"
    original_bytes = csv_path.read_bytes()
    captured: dict[str, Any] = {}

    def _capture(conn: Any, loadable: Any) -> None:
        captured["loadable"] = loadable
        # Prove the single read: mutate the file at load time — the hash
        # already rides in loadable, so it must still describe the bytes
        # validated, not this later state.
        csv_path.write_bytes(b"code\ntampered\n")

    monkeypatch.setattr(lrd, "load_tables", _capture)
    lrd.run(_base_config(tmp_path), check=False, dry_run=False)
    assert set(captured["loadable"]) == {"clm_type_cd"}
    _, _, csv_sha256 = captured["loadable"]["clm_type_cd"]
    assert csv_sha256 == lrd.compute_csv_sha256(original_bytes)


def test_run_validation_issues_raise_and_skip_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_conn: MagicMock
) -> None:
    _patch_introspection(monkeypatch)
    _patch_run_env(monkeypatch, fake_conn)
    _write_ref_docs(tmp_path, _HEADER)
    # Duplicate PK — validation must fail before any write.
    _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n"
        "60,Inpatient,f,s,\n60,Duplicate,f,s,\n",
    )
    monkeypatch.setattr(
        lrd,
        "load_tables",
        lambda *a, **k: pytest.fail("load_tables must not run"),
    )
    with pytest.raises(RuntimeError, match="validation failed with 1 issue"):
        lrd.run(_base_config(tmp_path), check=False, dry_run=False)


def test_run_dry_run_plumbs_exemptions_to_validate_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_conn: MagicMock
) -> None:
    # run() forwards all three exemption sets into validate_all.
    _patch_introspection(monkeypatch)
    _patch_run_env(monkeypatch, fake_conn)
    _write_ref_docs(tmp_path, _HEADER)
    _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    captured: dict[str, Any] = {}

    def _fake_validate_all(*args: Any, **kwargs: Any) -> Any:
        captured["args"] = args
        return {}, []

    monkeypatch.setattr(lrd, "validate_all", _fake_validate_all)
    lrd.run(
        _base_config(tmp_path),
        check=False,
        dry_run=True,
        allow_missing_tables={"m_tbl"},
        allow_reshaped_tables={"r_tbl"},
        allow_dropped_tables={"d_tbl"},
    )
    assert captured["args"][5:] == ({"m_tbl"}, {"r_tbl"}, {"d_tbl"})


def test_run_dry_run_allow_dropped_table_downgrades_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_conn: MagicMock
) -> None:
    # End to end through run(): a CSV-less DB table fails a strict
    # dry-run and passes with --allow-dropped-table naming it.
    _patch_introspection(monkeypatch)
    monkeypatch.setattr(
        lrd,
        "fetch_ref_tables",
        lambda conn, schema: {"clm_type_cd", "orphan_tbl"},
    )
    _patch_run_env(monkeypatch, fake_conn)
    _write_ref_docs(tmp_path, _HEADER)
    _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    with pytest.raises(RuntimeError, match="validation failed"):
        lrd.run(_base_config(tmp_path), check=False, dry_run=True)
    lrd.run(
        _base_config(tmp_path),
        check=False,
        dry_run=True,
        allow_dropped_tables={"orphan_tbl"},
    )
    fake_conn.commit.assert_not_called()


def test_run_check_mode_raises_on_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_conn: MagicMock,
    fake_cursor: MagicMock,
) -> None:
    _patch_introspection(monkeypatch)
    _patch_run_env(monkeypatch, fake_conn)
    _write_ref_docs(tmp_path, _HEADER)
    _write_csv(tmp_path, "clm_type_cd.csv", "code\n60\n")
    fake_cursor.fetchone.return_value = ("someolderhash",)
    with pytest.raises(RuntimeError, match="freshness check failed"):
        lrd.run(_base_config(tmp_path), check=True, dry_run=False)


def test_run_check_mode_passes_when_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_conn: MagicMock,
    fake_cursor: MagicMock,
) -> None:
    _patch_introspection(monkeypatch)
    _patch_run_env(monkeypatch, fake_conn)
    _write_ref_docs(tmp_path, _HEADER)
    csv_dir = _write_csv(tmp_path, "clm_type_cd.csv", "code\n60\n")
    fake_cursor.fetchone.return_value = (
        lrd.compute_csv_sha256(
            (csv_dir / "codes" / "clm_type_cd.csv").read_bytes()
        ),
    )
    lrd.run(_base_config(tmp_path), check=True, dry_run=False)
    fake_conn.commit.assert_not_called()


def test_run_check_mode_flags_db_table_without_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_conn: MagicMock,
    fake_cursor: MagicMock,
) -> None:
    # --check surfaces bidirectional drift too: a ref-schema table with
    # no CSV fails the check even when every CSV-backed table is current.
    _patch_introspection(monkeypatch)
    monkeypatch.setattr(
        lrd,
        "fetch_ref_tables",
        lambda conn, schema: {"clm_type_cd", "orphan_tbl"},
    )
    _patch_run_env(monkeypatch, fake_conn)
    _write_ref_docs(tmp_path, _HEADER)
    csv_dir = _write_csv(tmp_path, "clm_type_cd.csv", "code\n60\n")
    fake_cursor.fetchone.return_value = (
        lrd.compute_csv_sha256(
            (csv_dir / "codes" / "clm_type_cd.csv").read_bytes()
        ),
    )
    with pytest.raises(RuntimeError, match="freshness check failed"):
        lrd.run(_base_config(tmp_path), check=True, dry_run=False)


def test_run_check_mode_flags_documented_table_without_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_conn: MagicMock,
    fake_cursor: MagicMock,
) -> None:
    # --check drift, docs direction: a documented ref table with no CSV
    # fails the check.
    _patch_introspection(monkeypatch)
    _patch_run_env(monkeypatch, fake_conn)
    docs_dir = _write_ref_docs(tmp_path, _HEADER)
    _append_doc_rows(docs_dir, _doc_row("ghost_docs_tbl", "code", pk=True))
    csv_dir = _write_csv(tmp_path, "clm_type_cd.csv", "code\n60\n")
    fake_cursor.fetchone.return_value = (
        lrd.compute_csv_sha256(
            (csv_dir / "codes" / "clm_type_cd.csv").read_bytes()
        ),
    )
    with pytest.raises(RuntimeError, match="freshness check failed"):
        lrd.run(_base_config(tmp_path), check=True, dry_run=False)


def test_run_max_rows_knob_flows_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_conn: MagicMock
) -> None:
    _patch_introspection(monkeypatch)
    _patch_run_env(monkeypatch, fake_conn)
    _write_ref_docs(tmp_path, _HEADER)
    _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n"
        "60,Inpatient,f,s,\n40,Outpatient,f,s,\n",
    )
    with pytest.raises(RuntimeError, match="validation failed"):
        lrd.run(
            _base_config(tmp_path, max_rows_per_table=1),
            check=False,
            dry_run=False,
        )


# ---------------------------------------------------------------------------
# main (entry point)
# ---------------------------------------------------------------------------


def test_main_missing_config_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        lrd.sys, "argv",
        ["load_ref_data.py", "--config", str(tmp_path / "nope.toml")],
    )
    with pytest.raises(SystemExit) as excinfo:
        lrd.main()
    assert excinfo.value.code == 1


def test_main_invalid_toml_config_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "c.toml"
    config.write_text("not = valid = toml\n", encoding="utf-8")
    monkeypatch.setattr(
        lrd.sys, "argv", ["load_ref_data.py", "--config", str(config)]
    )
    with pytest.raises(SystemExit) as excinfo:
        lrd.main()
    assert excinfo.value.code == 1


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(
            FileNotFoundError("data_ref not found"), id="file_not_found"
        ),
        # The whole OSError family, not just FileNotFoundError: the
        # directory walks in run() can raise PermissionError (and
        # friends) on an unreadable data_ref/ or docs folder.
        pytest.param(
            PermissionError("data_ref unreadable"), id="permission_error"
        ),
        pytest.param(ValueError("bad csv"), id="value_error"),
        pytest.param(RuntimeError("validation failed"), id="runtime_error"),
        pytest.param(psycopg2.Error("connection lost"), id="psycopg2_error"),
    ],
)
def test_main_run_error_maps_to_exit_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    # Each documented run() failure mode (missing paths, validation
    # errors, drift/orchestration failures, DB errors) maps to a logged
    # exit 1, never an unhandled traceback.
    config = tmp_path / "c.toml"
    config.write_text('csv_dir = "x"\n', encoding="utf-8")
    monkeypatch.setattr(lrd, "run", MagicMock(side_effect=error))
    monkeypatch.setattr(
        lrd.sys, "argv",
        ["load_ref_data.py", "--config", str(config), "--dry-run"],
    )
    with pytest.raises(SystemExit) as excinfo:
        lrd.main()
    assert excinfo.value.code == 1


def test_main_check_and_dry_run_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "c.toml"
    config.write_text('csv_dir = "x"\n', encoding="utf-8")
    monkeypatch.setattr(
        lrd.sys, "argv",
        ["load_ref_data.py", "--config", str(config), "--check", "--dry-run"],
    )
    with pytest.raises(SystemExit) as excinfo:
        lrd.main()
    assert excinfo.value.code == 1


def test_main_missing_required_config_field_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "c.toml"
    # No csv_dir/database/schema — run() raises KeyError, mapped to exit 1.
    config.write_text('docs_dir = "x"\n', encoding="utf-8")
    monkeypatch.setattr(
        lrd.sys, "argv", ["load_ref_data.py", "--config", str(config)]
    )
    with pytest.raises(SystemExit) as excinfo:
        lrd.main()
    assert excinfo.value.code == 1


def test_main_happy_dry_run_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_conn: MagicMock,
) -> None:
    _patch_introspection(monkeypatch)
    _patch_run_env(monkeypatch, fake_conn)
    _write_ref_docs(tmp_path, _HEADER)
    _write_csv(
        tmp_path,
        "clm_type_cd.csv",
        "code,description,pgm_family,stream,notes\n60,Inpatient,f,s,\n",
    )
    config = tmp_path / "c.toml"
    config.write_text(
        f'csv_dir = "{(tmp_path / "data_ref").as_posix()}"\n'
        f'docs_dir = "{(tmp_path / "ref").as_posix()}"\n'
        'database = "metadata_db"\n'
        'schema = "reference"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        lrd.sys, "argv",
        ["load_ref_data.py", "--config", str(config), "--dry-run"],
    )
    lrd.main()  # no SystemExit — clean validate-only pass
    fake_conn.commit.assert_not_called()


@pytest.mark.parametrize(
    "flag",
    ["--allow-missing-table", "--allow-reshaped-table", "--allow-dropped-table"],
)
@pytest.mark.parametrize(
    "extra_argv",
    [pytest.param([], id="real_load"), pytest.param(["--check"], id="check")],
)
def test_main_exemption_flags_require_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    flag: str,
    extra_argv: list[str],
) -> None:
    # Every exemption flag exists solely for the pre-merge dry-run; a
    # real load (or --check) must never skip a drift or missing-table
    # error.
    config = tmp_path / "c.toml"
    config.write_text('csv_dir = "x"\n', encoding="utf-8")
    monkeypatch.setattr(
        lrd.sys, "argv",
        [
            "load_ref_data.py", "--config", str(config),
            flag, "some_tbl", *extra_argv,
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        lrd.main()
    assert excinfo.value.code == 1


def test_main_exemption_flags_plumb_to_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Repeatable exemption flags arrive as sets on run(), one per kind.
    config = tmp_path / "c.toml"
    config.write_text('csv_dir = "x"\n', encoding="utf-8")
    mock_run = MagicMock(return_value=None)
    monkeypatch.setattr(lrd, "run", mock_run)
    monkeypatch.setattr(
        lrd.sys, "argv",
        [
            "load_ref_data.py", "--config", str(config), "--dry-run",
            "--allow-missing-table", "tbl_a",
            "--allow-missing-table", "tbl_b",
            "--allow-reshaped-table", "tbl_c",
            "--allow-dropped-table", "tbl_d",
        ],
    )
    lrd.main()
    assert mock_run.call_args.kwargs["allow_missing_tables"] == {
        "tbl_a",
        "tbl_b",
    }
    assert mock_run.call_args.kwargs["allow_reshaped_tables"] == {"tbl_c"}
    assert mock_run.call_args.kwargs["allow_dropped_tables"] == {"tbl_d"}


# connection_kwargs itself is the shared pgconn helper, covered directly
# by code/lib/pgconn/unit_tests/test_pgconn.py; this module re-exports it
# (and ENV_VARS, used by _patch_run_env above), and run()'s tests
# exercise the call through that re-export.
