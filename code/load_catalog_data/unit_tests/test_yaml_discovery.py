"""Unit tests for yaml_discovery.py (venue-free path grammar)."""

from pathlib import Path

import pytest

import yaml_discovery


def _make_tree(root: Path) -> None:
    """Build a small corpus covering 8 of the 9 file types (all but concepts).

    Layout: `data_catalog/systems.yaml` (venue registry) plus a single data
    source `ocs` under `data_catalog/sources/`, with a deployments file and one
    schema `general` carrying the schema-level file types and a mappings
    file.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "systems.yaml").write_text("- system: sandbox\n")
    schema_dir = root / "sources" / "ocs" / "general"
    schema_dir.mkdir(parents=True)
    (root / "sources" / "ocs" / "data_source.yaml").write_text(
        "owner: data-ops\n"
    )
    (root / "sources" / "ocs" / "deployments.yaml").write_text(
        "- system: sandbox\n"
    )
    (schema_dir / "schema.yaml").write_text("description: x\n")
    (schema_dir / "tables.yaml").write_text("[]\n")
    (schema_dir / "columns.yaml").write_text("[]\n")
    (schema_dir / "table_relationships.yaml").write_text("[]\n")
    mappings = schema_dir / "mappings"
    mappings.mkdir()
    (mappings / "edw.yaml").write_text("[]\n")


# Number of files _make_tree lays down (systems, data_source,
# deployments, schema, tables, columns, table_relationships,
# column_mappings) — everything but concepts.
_BASE_FILE_COUNT = 8


def test_decode_path_systems_registry(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    p = tmp_path / "systems.yaml"
    ident = yaml_discovery.decode_path(p, tmp_path)
    assert ident.file_type == "systems"
    assert ident.database_name is None
    assert ident.schema_name is None
    assert ident.path == p


def test_decode_path_data_source(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    p = tmp_path / "sources" / "ocs" / "data_source.yaml"
    ident = yaml_discovery.decode_path(p, tmp_path)
    assert ident.file_type == "data_source"
    assert ident.database_name == "ocs"
    assert ident.schema_name is None


def test_decode_path_deployments(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    p = tmp_path / "sources" / "ocs" / "deployments.yaml"
    ident = yaml_discovery.decode_path(p, tmp_path)
    assert ident.file_type == "deployments"
    assert ident.database_name == "ocs"
    assert ident.schema_name is None


def test_decode_path_schema(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    p = tmp_path / "sources" / "ocs" / "general" / "schema.yaml"
    ident = yaml_discovery.decode_path(p, tmp_path)
    assert ident.file_type == "schema"
    assert ident.schema_name == "general"


@pytest.mark.parametrize(
    ("filename", "expected_type"),
    [
        ("tables.yaml", "tables"),
        ("columns.yaml", "columns"),
        ("table_relationships.yaml", "table_relationships"),
    ],
)
def test_decode_path_schema_level_files(
    tmp_path: Path, filename: str, expected_type: str
) -> None:
    _make_tree(tmp_path)
    p = tmp_path / "sources" / "ocs" / "general" / filename
    ident = yaml_discovery.decode_path(p, tmp_path)
    assert ident.file_type == expected_type
    assert ident.database_name == "ocs"
    assert ident.schema_name == "general"


def test_decode_path_mappings(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    p = (
        tmp_path
        / "sources"
        / "ocs"
        / "general"
        / "mappings"
        / "edw.yaml"
    )
    ident = yaml_discovery.decode_path(p, tmp_path)
    assert ident.file_type == "column_mappings"
    # The filename stem is a grouping label, not an identity segment.
    assert ident.database_name == "ocs"
    assert ident.schema_name == "general"


def test_decode_path_mappings_stem_charset_validated(tmp_path: Path) -> None:
    # The mappings filename stem is charset-validated like any segment,
    # even though it is not decoded into an identity.
    mappings = tmp_path / "sources" / "ocs" / "general" / "mappings"
    mappings.mkdir(parents=True)
    p = mappings / "bad.stem.yaml"  # extra dot inside the stem
    p.write_text("[]\n")
    with pytest.raises(ValueError, match="Invalid mapping_file segment"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_mappings_at_data_source_level_raises(tmp_path: Path) -> None:
    # A `mappings/` folder directly under {label}/ is the wrong depth
    # (mappings belong under a schema). It gets a dedicated classification
    # error naming the correct location, not a schema named `mappings`.
    mappings = tmp_path / "sources" / "ocs" / "mappings"
    mappings.mkdir(parents=True)
    p = mappings / "edw.yaml"
    p.write_text("[]\n")
    with pytest.raises(ValueError, match="mappings/ folder at the data-source"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_mappings_at_data_source_level_schema_filename_raises(
    tmp_path: Path,
) -> None:
    # Even when the filename would match a schema-level type, a
    # data-source-level mappings/ folder is rejected rather than decoded
    # as a schema literally named `mappings`.
    mappings = tmp_path / "sources" / "ocs" / "mappings"
    mappings.mkdir(parents=True)
    p = mappings / "tables.yaml"
    p.write_text("[]\n")
    with pytest.raises(ValueError, match="mappings/ folder at the data-source"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_concepts_data_source_level(tmp_path: Path) -> None:
    # A concepts.yaml at {label}/ is data-source-scoped: it carries
    # database_name from the path but no schema_name.
    _make_tree(tmp_path)
    p = tmp_path / "sources" / "ocs" / "concepts.yaml"
    p.write_text("[]\n")
    ident = yaml_discovery.decode_path(p, tmp_path)
    assert ident.file_type == "concepts"
    assert ident.database_name == "ocs"
    assert ident.schema_name is None
    assert ident.path == p


def test_decode_path_concepts_schema_level(tmp_path: Path) -> None:
    # A concepts.yaml at {label}/{schema}/ is schema-scoped: it carries
    # database_name + schema_name from the path.
    _make_tree(tmp_path)
    p = tmp_path / "sources" / "ocs" / "general" / "concepts.yaml"
    p.write_text("[]\n")
    ident = yaml_discovery.decode_path(p, tmp_path)
    assert ident.file_type == "concepts"
    assert ident.database_name == "ocs"
    assert ident.schema_name == "general"


def test_decode_path_concepts_under_mappings_raises(tmp_path: Path) -> None:
    # `concepts.yaml` is a reserved filename valid only at the data-source
    # and schema depths. Under a schema's mappings/ folder it is at the
    # wrong depth — a path error, not silently a mappings file.
    mappings = tmp_path / "sources" / "ocs" / "general" / "mappings"
    mappings.mkdir(parents=True)
    p = mappings / "concepts.yaml"
    p.write_text("[]\n")
    with pytest.raises(ValueError, match="unsupported depth"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_concepts_directly_under_sources_raises(
    tmp_path: Path,
) -> None:
    # The pathological shallow case: `sources/concepts.yaml` has no
    # data-source segment above it, so there is no parent to inspect for
    # the reserved-filename check. It is unsupported like any other
    # off-depth concepts file. (Line coverage cannot see this half of the
    # guard — it shares a statement with the deeper-path half above.)
    sources = tmp_path / "sources"
    sources.mkdir(parents=True)
    p = sources / "concepts.yaml"
    p.write_text("[]\n")
    with pytest.raises(ValueError, match="unsupported depth"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_deployments_at_schema_level_raises(tmp_path: Path) -> None:
    # deployments.yaml is a data-source-level file; at the schema level it
    # is misplaced.
    schema_dir = tmp_path / "sources" / "ocs" / "general"
    schema_dir.mkdir(parents=True)
    p = schema_dir / "deployments.yaml"
    p.write_text("[]\n")
    with pytest.raises(ValueError, match="Unrecognized YAML location"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_reserved_concept_schema_raises(tmp_path: Path) -> None:
    # A schema literally named `concept` would shadow the reserved segment
    # in a data-source-level concept_id, so it is rejected at decode time.
    schema_dir = tmp_path / "sources" / "ocs" / "concept"
    schema_dir.mkdir(parents=True)
    p = schema_dir / "schema.yaml"
    p.write_text("description: x\n")
    with pytest.raises(ValueError, match="reserved"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_outside_data_root_raises(tmp_path: Path) -> None:
    other = tmp_path / "elsewhere" / "systems.yaml"
    other.parent.mkdir(parents=True)
    other.write_text("[]\n")
    with pytest.raises(ValueError, match="not under data root"):
        yaml_discovery.decode_path(other, tmp_path / "data")


def test_decode_path_not_registry_nor_sources_raises(tmp_path: Path) -> None:
    # A yaml under the corpus root but neither the registry nor under
    # sources/ (e.g. a leftover systems/ folder from the old grammar) is a
    # path error.
    stray_dir = tmp_path / "systems"
    stray_dir.mkdir(parents=True)
    p = stray_dir / "warehouse.yaml"
    p.write_text("x: 1\n")
    with pytest.raises(ValueError, match="nor under the sources tree"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_unrecognized_shape_raises(tmp_path: Path) -> None:
    (tmp_path / "sources" / "ocs").mkdir(parents=True)
    weird = tmp_path / "sources" / "ocs" / "weird.yaml"
    weird.write_text("x: 1\n")
    with pytest.raises(ValueError, match="Unrecognized YAML location"):
        yaml_discovery.decode_path(weird, tmp_path)


def test_decode_path_unrecognized_schema_level_filename_raises(
    tmp_path: Path,
) -> None:
    schema_dir = tmp_path / "sources" / "ocs" / "general"
    schema_dir.mkdir(parents=True)
    weird = schema_dir / "other.yaml"
    weird.write_text("x: 1\n")
    with pytest.raises(ValueError, match="Unrecognized YAML location"):
        yaml_discovery.decode_path(weird, tmp_path)


@pytest.mark.parametrize(
    ("value", "kind", "expected"),
    [
        # Each case carries the message it must fail with: the charset
        # rejection and the empty-value rejection are different errors,
        # and a bare `pytest.raises(ValueError)` could not tell a
        # regression that swapped them from correct behavior.
        ("bad.name", "data_source", "offending character"),
        ("has space", "data_source", "offending character"),
        ("with\ttab", "schema_name", "offending character"),
        ("with\nnewline", "table_name", "offending character"),
        ("", "data_source", "Empty data_source segment"),
        # Punctuation outside the lowercase ltree charset [a-z0-9_-] is rejected.
        ("has$dollar", "column_name", "offending character"),
        ("dx@1", "column_name", "offending character"),
        ("pct%col", "column_name", "offending character"),
        ("col#1", "table_name", "offending character"),
        # The third message family: an over-length label fails on the
        # ltree limit, not the charset.
        (
            "a" * 256,
            "column_name",
            "exceeds the 255-character ltree label limit",
        ),
    ],
)
def test_validate_identifier_segment_negative(
    value: str, kind: str, expected: str
) -> None:
    with pytest.raises(ValueError, match=expected):
        yaml_discovery.validate_identifier_segment(value, kind)


@pytest.mark.parametrize(
    "value",
    [
        "warehouse",
        "ocs",
        "edw_prd",
        "claims_vw",
        "bene_extl_id",
        "has-hyphen",
        "2024_snapshot",
    ],
)
def test_validate_identifier_segment_accepts_ltree_legal(value: str) -> None:
    # Lowercase letters, digits, underscore, hyphen, and a leading digit
    # are all valid lowercase ltree labels (verified on PG 18.3).
    yaml_discovery.validate_identifier_segment(value, "column_name")


def test_validate_identifier_segment_error_names_offending_char() -> None:
    with pytest.raises(ValueError, match=r"\$"):
        yaml_discovery.validate_identifier_segment("a$b", "data_source")


@pytest.mark.parametrize("value", ["CLM_TYPE_CD", "MixedCase", "EDW_PRD"])
def test_validate_identifier_segment_uppercase_names_lowercase_form(
    value: str,
) -> None:
    # A value whose only offense is uppercase letters gets the lowercase
    # mandate message naming the exact form to author instead.
    with pytest.raises(ValueError, match="must be lowercase") as excinfo:
        yaml_discovery.validate_identifier_segment(value, "column_name")
    assert repr(value.lower()) in str(excinfo.value)


def test_validate_identifier_segment_uppercase_plus_illegal_char() -> None:
    # Uppercase combined with an out-of-charset character cannot be fixed
    # by lowercasing alone, so the offending-characters message stands —
    # and it lists the uppercase letters as offenders too.
    with pytest.raises(
        ValueError, match="offending character"
    ) as excinfo:
        yaml_discovery.validate_identifier_segment("CLM$CD", "column_name")
    msg = str(excinfo.value)
    assert "'$'" in msg
    assert "'C'" in msg


def test_decode_path_invalid_segment_raises(tmp_path: Path) -> None:
    ds_dir = tmp_path / "sources" / "bad.name"
    ds_dir.mkdir(parents=True)
    p = ds_dir / "data_source.yaml"
    p.write_text("owner: data-ops\n")
    with pytest.raises(ValueError, match="Invalid data_source segment"):
        yaml_discovery.decode_path(p, tmp_path)


def test_discover_yaml_files_finds_all(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert issues == []
    types = sorted(i.file_type for i in ids)
    assert types == sorted(
        [
            "systems",
            "data_source",
            "deployments",
            "schema",
            "tables",
            "columns",
            "table_relationships",
            "column_mappings",
        ]
    )


def test_discover_yaml_files_ignores_non_yaml(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    schema_dir = tmp_path / "sources" / "ocs" / "general"
    (schema_dir / ".gitkeep").write_text("")
    (schema_dir / "README.md").write_text("# notes")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT
    assert issues == []


def test_discover_yaml_files_unrecognized_yaml_reported(tmp_path: Path) -> None:
    # A misplaced .yaml is an authoring error: it is collected as an
    # issue (naming the offending path) instead of aborting the walk,
    # and every valid file is still classified alongside it.
    _make_tree(tmp_path)
    schema_dir = tmp_path / "sources" / "ocs" / "general"
    (schema_dir / "stray.yaml").write_text("x: 1\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT
    assert len(issues) == 1
    assert "Unrecognized YAML location" in issues[0]
    assert "stray.yaml" in issues[0]


def test_discover_yaml_files_stray_under_old_systems_folder_reported(
    tmp_path: Path,
) -> None:
    # A leftover file under a `systems/` folder at the corpus root (the old grammar) is
    # neither the registry nor under sources/ — reported as an issue, the
    # walk continuing past it.
    _make_tree(tmp_path)
    old = tmp_path / "systems" / "warehouse"
    old.mkdir(parents=True)
    (old / "system.yaml").write_text("x: 1\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT
    assert len(issues) == 1
    assert "nor under the sources tree" in issues[0]


def test_discover_yaml_files_aggregates_multiple_issues(tmp_path: Path) -> None:
    # Several classification errors in one walk are all reported
    # together — misplaced file, concepts.yaml at an unsupported depth,
    # and a bad identifier segment — while the valid files still classify.
    _make_tree(tmp_path)
    schema_dir = tmp_path / "sources" / "ocs" / "general"
    (schema_dir / "stray.yaml").write_text("x: 1\n")
    (schema_dir / "mappings" / "concepts.yaml").write_text("[]\n")
    bad_ds = tmp_path / "sources" / "bad.name"
    bad_ds.mkdir()
    (bad_ds / "data_source.yaml").write_text("owner: data-ops\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT
    assert len(issues) == 3
    joined = "\n".join(issues)
    assert "Unrecognized YAML location" in joined
    assert "unsupported depth" in joined
    assert "Invalid data_source segment" in joined


def test_discover_yaml_files_orders_issues_by_kind_then_path(
    tmp_path: Path,
) -> None:
    # The documented ordering guarantee: every classification issue in
    # sorted path order, then every wrong-extension issue in sorted path
    # order. Callers surface these in one AssemblyError, so the order must
    # be identical across machines regardless of raw filesystem walk order.
    _make_tree(tmp_path)
    schema_dir = tmp_path / "sources" / "ocs" / "general"
    (schema_dir / "a_stray.yaml").write_text("x: 1\n")
    (schema_dir / "b_stray.yaml").write_text("x: 1\n")
    # Both would classify cleanly under the `.yaml` spelling, so each is a
    # wrong-extension issue: `sources/ocs/concepts.yml` sorts before
    # `sources/ocs/general/columns.yml`.
    (tmp_path / "sources" / "ocs" / "concepts.yml").write_text("[]\n")
    (schema_dir / "columns.yml").write_text("[]\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT
    assert len(issues) == 4
    # Kind first: the two classification issues precede the two
    # wrong-extension issues.
    assert "Unrecognized YAML location" in issues[0]
    assert "Unrecognized YAML location" in issues[1]
    assert "Wrong YAML extension" in issues[2]
    assert "Wrong YAML extension" in issues[3]
    # Path order within each kind.
    assert "a_stray.yaml" in issues[0]
    assert "b_stray.yaml" in issues[1]
    assert "concepts.yml" in issues[2]
    assert "columns.yml" in issues[3]


def test_discover_yaml_files_uppercase_segments_reported(
    tmp_path: Path,
) -> None:
    # Uppercase path segments — a data-source folder and a
    # mappings/{TARGET}.yaml stem — violate the lowercase mandate. Each
    # records one classification issue carrying the lowercase hint, the
    # walk continues, and the valid files still classify. The names
    # deliberately have no lowercase sibling in the tree: a case-only
    # variant of an existing path would collide on a case-insensitive
    # (Windows) filesystem.
    _make_tree(tmp_path)
    bad_ds = tmp_path / "sources" / "EDW_PRD"
    bad_ds.mkdir()
    (bad_ds / "data_source.yaml").write_text("owner: data-ops\n")
    mappings = tmp_path / "sources" / "ocs" / "general" / "mappings"
    (mappings / "SNOWFLAKE.yaml").write_text("[]\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT
    assert len(issues) == 2
    joined = "\n".join(issues)
    assert joined.count("must be lowercase") == 2
    assert "'edw_prd'" in joined
    assert "'snowflake'" in joined


def test_discover_yaml_files_missing_sources_root_raises(tmp_path: Path) -> None:
    # An absent sources/ root is an environment error (wrong data_root),
    # not an authoring error — it stays fail-fast, never aggregated.
    with pytest.raises(FileNotFoundError, match="sources root not found"):
        yaml_discovery.discover_yaml_files(tmp_path)


def test_discover_yaml_files_finds_anchored_concepts(tmp_path: Path) -> None:
    # concepts files live under sources/ like every other type: a single
    # walk finds them. Here one data-source-level and one schema-level
    # concepts file join the base files.
    _make_tree(tmp_path)
    (tmp_path / "sources" / "ocs" / "concepts.yaml").write_text("[]\n")
    (
        tmp_path / "sources" / "ocs" / "general" / "concepts.yaml"
    ).write_text("[]\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert issues == []
    concepts = [i for i in ids if i.file_type == "concepts"]
    assert len(concepts) == 2
    assert all(i.database_name == "ocs" for i in concepts)
    assert {i.schema_name for i in concepts} == {None, "general"}
    assert len(ids) == _BASE_FILE_COUNT + 2


def test_discover_yaml_files_no_concepts_is_fine(tmp_path: Path) -> None:
    # A corpus need not define any concepts — the base tree has none.
    _make_tree(tmp_path)
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert [i for i in ids if i.file_type == "concepts"] == []
    assert len(ids) == _BASE_FILE_COUNT
    assert issues == []


def test_discover_yaml_files_ignores_stray_yml_unrecognized_stem(
    tmp_path: Path,
) -> None:
    # A `.yml` whose name would NOT classify even as `.yaml` (an
    # unrecognized stem) is not a mis-extensioned corpus file — it stays
    # ignored like any other non-YAML file.
    _make_tree(tmp_path)
    schema_dir = tmp_path / "sources" / "ocs" / "general"
    (schema_dir / "extra.yml").write_text("x: 1\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT
    assert issues == []


def test_discover_yaml_files_wrong_extension_concepts_yml_reported(
    tmp_path: Path,
) -> None:
    # A mis-extensioned `concepts.yml` used to yield a green pipeline
    # while delete-by-absence removed its rows; it is now a wave-1 issue
    # naming the file and the required `.yaml` spelling.
    _make_tree(tmp_path)
    (tmp_path / "sources" / "ocs" / "concepts.yml").write_text("[]\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT  # the .yml is not loaded
    assert len(issues) == 1
    assert "Wrong YAML extension" in issues[0]
    assert "concepts.yml" in issues[0]
    assert "concepts.yaml" in issues[0]


def test_discover_yaml_files_wrong_extension_case_variant_reported(
    tmp_path: Path,
) -> None:
    # Case-variant extensions (`.YAML`) are wrong too — only lowercase
    # `.yaml` is canonical. The file lives in a schema with no lowercase
    # `tables.yaml` sibling (a case-only twin would collide on Windows).
    _make_tree(tmp_path)
    other = tmp_path / "sources" / "ocs" / "other"
    other.mkdir()
    (other / "schema.yaml").write_text("description: x\n")
    (other / "tables.YAML").write_text("[]\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT + 1  # + other/schema.yaml
    assert len(issues) == 1
    assert "Wrong YAML extension" in issues[0]
    assert "tables.YAML" in issues[0]


def test_discover_yaml_files_wrong_extension_mappings_yml_reported(
    tmp_path: Path,
) -> None:
    # A mappings file (any stem under mappings/) with a `.yml` extension
    # is a mis-extensioned corpus file.
    _make_tree(tmp_path)
    mappings = tmp_path / "sources" / "ocs" / "general" / "mappings"
    (mappings / "snowflake.yml").write_text("[]\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT
    assert len(issues) == 1
    assert "Wrong YAML extension" in issues[0]
    assert "snowflake.yml" in issues[0]


def test_discover_yaml_files_md_and_txt_stay_ignored(tmp_path: Path) -> None:
    # Files that are not YAML at all remain silently ignored — even with
    # a recognized stem.
    _make_tree(tmp_path)
    schema_dir = tmp_path / "sources" / "ocs" / "general"
    (schema_dir / "tables.md").write_text("# notes\n")
    (schema_dir / "columns.txt").write_text("notes\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT
    assert issues == []


# ---------------------------------------------------------------------------
# ltree label-length cap — an over-length segment fails wave 1
# ---------------------------------------------------------------------------


def test_validate_identifier_segment_over_length_rejected() -> None:
    # A 256-character segment exceeds the ltree label limit and fails wave 1
    # with the length message (not a charset one).
    value = "a" * 256
    with pytest.raises(
        ValueError, match="exceeds the 255-character ltree label limit"
    ):
        yaml_discovery.validate_identifier_segment(value, "column_name")


def test_validate_identifier_segment_max_length_accepted() -> None:
    # Exactly 255 characters is at the limit and passes.
    yaml_discovery.validate_identifier_segment("a" * 255, "column_name")


# ---------------------------------------------------------------------------
# Deterministic discovery order — sorted, stable across calls
# ---------------------------------------------------------------------------


def test_discover_yaml_files_returns_sorted_order(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    ids, _ = yaml_discovery.discover_yaml_files(tmp_path)
    paths = [ident.path for ident in ids]
    assert paths == sorted(paths)


def test_discover_yaml_files_order_is_stable_across_calls(
    tmp_path: Path,
) -> None:
    # Determinism: two discoveries of the same tree yield identical order,
    # independent of the filesystem's raw walk order.
    _make_tree(tmp_path)
    first, _ = yaml_discovery.discover_yaml_files(tmp_path)
    second, _ = yaml_discovery.discover_yaml_files(tmp_path)
    assert [i.path for i in first] == [i.path for i in second]


# ---------------------------------------------------------------------------
# Shard folders — the folder form of the four row-list types
# ---------------------------------------------------------------------------


# The four row-list types authorable as a `<type>/` shard folder, each
# with the FileType its shards classify to. Shared by every shard-folder
# test below and held to yaml_discovery's own table by the drift canary,
# so a fifth type added to the module cannot slip in untested.
_SHARD_FOLDER_CASES: list[tuple[str, str]] = [
    ("tables", "tables"),
    ("columns", "columns"),
    ("table_relationships", "table_relationships"),
    ("concepts", "concepts"),
]

# The shard folders valid only under a schema — `concepts/` is excluded
# because it is also valid directly under `{label}/`.
_SCHEMA_ONLY_SHARD_FOLDERS: list[str] = [
    "tables",
    "columns",
    "table_relationships",
]


def test_decode_path_shard_folder_cases_cover_module_tables() -> None:
    # Drift canary: the case lists above must name exactly the folders the
    # module recognizes. A fifth row-list type added to
    # `_SHARD_FOLDER_TYPES` (or a change to which folders are schema-only)
    # would otherwise gain no coverage here — silently, since every test
    # below would still pass on the stale names.
    assert {folder for folder, _ in _SHARD_FOLDER_CASES} == set(
        yaml_discovery._SHARD_FOLDER_TYPES
    )
    assert set(_SCHEMA_ONLY_SHARD_FOLDERS) == set(
        yaml_discovery._SCHEMA_ONLY_SHARD_FOLDERS
    )


@pytest.mark.parametrize(("folder", "expected_type"), _SHARD_FOLDER_CASES)
def test_decode_path_schema_level_shard_folder(
    tmp_path: Path, folder: str, expected_type: str
) -> None:
    # {label}/{schema}/<type>/{stem}.yaml classifies to the same FileType
    # and identity fields as the single <type>.yaml, with the shard-folder
    # provenance recorded for the assembly-side mutual-exclusion rule.
    shard_dir = tmp_path / "sources" / "ocs" / "general" / folder
    shard_dir.mkdir(parents=True)
    p = shard_dir / "clm.yaml"
    p.write_text("[]\n")
    ident = yaml_discovery.decode_path(p, tmp_path)
    assert ident.file_type == expected_type
    assert ident.database_name == "ocs"
    assert ident.schema_name == "general"
    assert ident.from_shard_folder is True


def test_decode_path_data_source_level_concepts_shard_folder(
    tmp_path: Path,
) -> None:
    # {label}/concepts/{stem}.yaml is the folder form of the
    # data-source-level {label}/concepts.yaml: concepts type, no schema.
    shard_dir = tmp_path / "sources" / "ocs" / "concepts"
    shard_dir.mkdir(parents=True)
    p = shard_dir / "billing.yaml"
    p.write_text("[]\n")
    ident = yaml_discovery.decode_path(p, tmp_path)
    assert ident.file_type == "concepts"
    assert ident.database_name == "ocs"
    assert ident.schema_name is None
    assert ident.from_shard_folder is True


def test_decode_path_single_file_forms_not_shard_marked(tmp_path: Path) -> None:
    # Regression: every single-file shape still classifies unchanged, and
    # none carries shard-folder provenance.
    _make_tree(tmp_path)
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert issues == []
    assert len(ids) == _BASE_FILE_COUNT
    assert all(i.from_shard_folder is False for i in ids)


def test_decode_path_shard_stem_charset_validated(tmp_path: Path) -> None:
    # The shard filename stem is charset-validated like a mappings stem,
    # even though it is a grouping label never decoded into an identity.
    shard_dir = tmp_path / "sources" / "ocs" / "general" / "columns"
    shard_dir.mkdir(parents=True)
    p = shard_dir / "bad.stem.yaml"  # extra dot inside the stem
    p.write_text("[]\n")
    with pytest.raises(ValueError, match="Invalid shard_file segment"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_shard_stem_uppercase_rejected(tmp_path: Path) -> None:
    # An uppercase shard stem violates the lowercase mandate; the message
    # names the lowercase form to use. No lowercase sibling exists (a
    # case-only twin would collide on a case-insensitive filesystem).
    shard_dir = tmp_path / "sources" / "ocs" / "general" / "columns"
    shard_dir.mkdir(parents=True)
    p = shard_dir / "BENE.yaml"
    p.write_text("[]\n")
    with pytest.raises(ValueError, match="must be lowercase"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_shard_stem_over_length_rejected(tmp_path: Path) -> None:
    # A 256-character stem exceeds the ltree label limit. The file is not
    # created on disk (the over-long name may exceed the platform's path
    # limit); decode_path classifies by path alone.
    p = (
        tmp_path / "sources" / "ocs" / "general" / "columns"
        / ("a" * 256 + ".yaml")
    )
    with pytest.raises(
        ValueError, match="exceeds the 255-character ltree label limit"
    ):
        yaml_discovery.decode_path(p, tmp_path)


@pytest.mark.parametrize("name", [folder for folder, _ in _SHARD_FOLDER_CASES])
def test_decode_path_reserved_shard_schema_name_raises(
    tmp_path: Path, name: str
) -> None:
    # A schema literally named after a shard folder is reserved: the
    # folder grammar makes it ambiguous at the schema-segment position.
    # Surfaced here through a mappings path, where the segment is
    # unambiguously at the schema position.
    mappings = tmp_path / "sources" / "ocs" / name / "mappings"
    mappings.mkdir(parents=True)
    p = mappings / "edw.yaml"
    p.write_text("[]\n")
    with pytest.raises(
        ValueError, match="is reserved: it is a shard-folder name"
    ):
        yaml_discovery.decode_path(p, tmp_path)


@pytest.mark.parametrize("folder", _SCHEMA_ONLY_SHARD_FOLDERS)
def test_decode_path_shard_folder_at_data_source_level_raises(
    tmp_path: Path, folder: str
) -> None:
    # tables/, columns/, and table_relationships/ folders live under a
    # schema; directly under {label}/ they get the dedicated wrong-depth
    # error naming the correct location (only concepts/ is valid there).
    shard_dir = tmp_path / "sources" / "ocs" / folder
    shard_dir.mkdir(parents=True)
    p = shard_dir / "bene.yaml"
    p.write_text("[]\n")
    with pytest.raises(
        ValueError, match=f"{folder}/ folder at the data-source level"
    ) as excinfo:
        yaml_discovery.decode_path(p, tmp_path)
    msg = str(excinfo.value)
    assert "must live under a schema" in msg
    assert "concepts/" in msg


def test_decode_path_shard_folder_wrong_depth_schema_filename_raises(
    tmp_path: Path,
) -> None:
    # Even when the filename would match a schema-level type, a
    # data-source-level shard folder is rejected with the wrong-depth
    # error rather than decoded as a schema named `tables` (mirroring the
    # wrong-depth mappings/ handling).
    shard_dir = tmp_path / "sources" / "ocs" / "tables"
    shard_dir.mkdir(parents=True)
    p = shard_dir / "schema.yaml"
    p.write_text("description: x\n")
    with pytest.raises(
        ValueError, match="tables/ folder at the data-source level"
    ):
        yaml_discovery.decode_path(p, tmp_path)


@pytest.mark.parametrize(
    "folder",
    [
        # Each is the folder path relative to the data source, at the depth
        # where that type's single file lives: schema.yaml under a schema,
        # data_source.yaml and deployments.yaml directly under {label}/.
        "general/schema",
        "data_source",
        "deployments",
    ],
)
def test_decode_path_no_folder_form_for_single_purpose_types(
    tmp_path: Path, folder: str
) -> None:
    # schema.yaml, data_source.yaml, and deployments.yaml have no folder
    # form: a schema/, data_source/, or deployments/ folder is an
    # unrecognized location.
    shard_dir = tmp_path / "sources" / "ocs" / folder
    shard_dir.mkdir(parents=True)
    p = shard_dir / "core.yaml"
    p.write_text("[]\n")
    with pytest.raises(ValueError, match="Unrecognized YAML location"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_concepts_yaml_inside_other_shard_folder_raises(
    tmp_path: Path,
) -> None:
    # `concepts.yaml` stays a reserved filename: inside a non-concepts
    # shard folder it is a path error, never silently loaded as that
    # folder's row type (a misplaced concepts file must not become
    # columns rows).
    shard_dir = tmp_path / "sources" / "ocs" / "general" / "columns"
    shard_dir.mkdir(parents=True)
    p = shard_dir / "concepts.yaml"
    p.write_text("[]\n")
    with pytest.raises(ValueError, match="unsupported depth"):
        yaml_discovery.decode_path(p, tmp_path)


def test_decode_path_concepts_yaml_inside_concepts_shard_folder_ok(
    tmp_path: Path,
) -> None:
    # Inside a concepts/ shard folder both readings are concepts, so a
    # shard whose stem is spelled `concepts` is a valid shard.
    shard_dir = tmp_path / "sources" / "ocs" / "general" / "concepts"
    shard_dir.mkdir(parents=True)
    p = shard_dir / "concepts.yaml"
    p.write_text("[]\n")
    ident = yaml_discovery.decode_path(p, tmp_path)
    assert ident.file_type == "concepts"
    assert ident.schema_name == "general"
    assert ident.from_shard_folder is True


def test_discover_yaml_files_finds_shard_folders(tmp_path: Path) -> None:
    # A schema authored with the folder form discovers cleanly: each
    # shard classifies as its type with shard provenance, alongside the
    # single-file base tree.
    _make_tree(tmp_path)
    other = tmp_path / "sources" / "ocs" / "other"
    other.mkdir()
    (other / "schema.yaml").write_text("description: x\n")
    shard_dir = other / "columns"
    shard_dir.mkdir()
    (shard_dir / "clm.yaml").write_text("[]\n")
    (shard_dir / "bene.yaml").write_text("[]\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert issues == []
    assert len(ids) == _BASE_FILE_COUNT + 3  # schema.yaml + 2 shards
    shards = [i for i in ids if i.from_shard_folder]
    assert len(shards) == 2
    assert all(i.file_type == "columns" for i in shards)
    assert all(i.schema_name == "other" for i in shards)


def test_discover_yaml_files_wrong_extension_in_shard_folder_reported(
    tmp_path: Path,
) -> None:
    # A `.yml` shard at a recognized shard location is a mis-extensioned
    # corpus file: a wave-1 issue, never a silent skip (delete-by-absence
    # would otherwise remove its previously loaded rows).
    _make_tree(tmp_path)
    other = tmp_path / "sources" / "ocs" / "other"
    shard_dir = other / "columns"
    shard_dir.mkdir(parents=True)
    (other / "schema.yaml").write_text("description: x\n")
    (shard_dir / "bene.yml").write_text("[]\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT + 1  # the .yml is not loaded
    assert len(issues) == 1
    assert "Wrong YAML extension" in issues[0]
    assert "bene.yml" in issues[0]


def test_discover_yaml_files_case_variant_extension_in_shard_folder_reported(
    tmp_path: Path,
) -> None:
    # A case-variant extension (`.YAML`) inside a shard folder is wrong
    # too — only lowercase `.yaml` is canonical. No lowercase `bene.yaml`
    # sibling exists (a case-only twin would collide on Windows).
    _make_tree(tmp_path)
    other = tmp_path / "sources" / "ocs" / "other"
    shard_dir = other / "columns"
    shard_dir.mkdir(parents=True)
    (other / "schema.yaml").write_text("description: x\n")
    (shard_dir / "bene.YAML").write_text("[]\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT + 1
    assert len(issues) == 1
    assert "Wrong YAML extension" in issues[0]
    assert "bene.YAML" in issues[0]


def test_discover_yaml_files_case_variant_shard_folder_fails_loudly(
    tmp_path: Path,
) -> None:
    # A case-variant shard folder name (`Columns/`) never matches the
    # lowercase grammar: its contents surface as classification issues
    # rather than being silently skipped. No lowercase `columns/` sibling
    # exists in this schema (a case-only twin would collide on Windows).
    _make_tree(tmp_path)
    other = tmp_path / "sources" / "ocs" / "other"
    shard_dir = other / "Columns"
    shard_dir.mkdir(parents=True)
    (other / "schema.yaml").write_text("description: x\n")
    (shard_dir / "bene.yaml").write_text("[]\n")
    ids, issues = yaml_discovery.discover_yaml_files(tmp_path)
    assert len(ids) == _BASE_FILE_COUNT + 1
    assert len(issues) == 1
    assert "Unrecognized YAML location" in issues[0]
    assert "bene.yaml" in issues[0]
