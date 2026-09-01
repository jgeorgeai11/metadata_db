"""Unit tests for data_model.py."""

import dataclasses

import pytest

import data_model as sch


# Map each main-table name to its row dataclass, for the registry checks.
_ROW_CLASS = {
    "systems": sch.SystemRow,
    "data_sources": sch.DataSourceRow,
    "schemas": sch.SchemaRow,
    "tables": sch.TableRow,
    "columns": sch.ColumnRow,
    "deployment_tables": sch.DeploymentRow,
    "table_relationships": sch.TableRelationshipRow,
    "column_mappings": sch.ColumnMappingRow,
    "concepts": sch.ConceptRow,
}


@pytest.fixture
def sample_relationship_row() -> sch.TableRelationshipRow:
    """A minimal TableRelationshipRow for identity/default checks.

    Built without validated_ts, like corpus rows assembled from YAML, so
    tests can also observe that field's default. There is no `system`
    field: venue validity is derived from the endpoints' deployments.
    """
    return sch.TableRelationshipRow(
        table_a_id="a",
        table_b_id="b",
        relationship_name="default",
        join_condition="a.x = b.y",
        cardinality="many_to_one",
        use_when=None,
        notes=None,
        validated=False,
        update_reason=None,
    )


@pytest.fixture
def sample_deployment_row() -> sch.DeploymentRow:
    """A minimal DeploymentRow for identity/field checks."""
    return sch.DeploymentRow(
        table_id="pagila.general.film",
        system="sandbox",
        data_source_id="pagila",
        physical_database_name="pagila",
        physical_schema_name="general",
        physical_table_name="film",
    )


def test_table_order_is_fk_respecting() -> None:
    # Parents before children: systems -> data_sources -> schemas ->
    # tables -> columns; deployment_tables FKs back to tables/systems/
    # data_sources so it follows columns; table_relationships /
    # column_mappings FK back to tables/columns. concepts has no FK
    # columns, so it is safe at the end.
    assert sch.TABLE_ORDER == (
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


def test_content_columns_excludes_timestamps_for_every_table() -> None:
    """No table's CONTENT_COLUMNS includes loader-managed timestamps."""
    for table, cols in sch.CONTENT_COLUMNS.items():
        assert "insert_ts" not in cols, table
        assert "update_ts" not in cols, table


def test_content_columns_covers_all_nine_tables() -> None:
    """CONTENT_COLUMNS has an entry for every table in TABLE_ORDER."""
    assert set(sch.CONTENT_COLUMNS.keys()) == set(sch.TABLE_ORDER)
    assert len(sch.TABLE_ORDER) == 9


def test_column_ref_table_id_and_column_id() -> None:
    """ColumnRef derives dotted table_id and column_id from its 4 parts.

    Ids are venue-free: there is no leading system segment.
    """
    ref = sch.ColumnRef(
        database="ocs",
        schema="general",
        table="bene",
        column="bene_id",
    )
    assert ref.table_id == "ocs.general.bene"
    assert ref.column_id == "ocs.general.bene.bene_id"


def test_empty_corpus_and_db_state_have_nine_empty_dicts() -> None:
    corpus = sch.empty_corpus()
    state = sch.empty_db_state()
    for t in sch.TABLE_ORDER:
        assert getattr(corpus, t) == {}
        assert getattr(state, t) == {}
    # deployment_tables and concepts must be among the initialized dicts.
    assert corpus.deployment_tables == {}
    assert state.deployment_tables == {}
    assert corpus.concepts == {}
    assert state.concepts == {}


def test_iter_tables_matches_table_order() -> None:
    """iter_tables() yields table names in TABLE_ORDER sequence."""
    assert tuple(sch.iter_tables()) == sch.TABLE_ORDER


def test_primary_key_columns_cover_all_nine_tables() -> None:
    """PRIMARY_KEY_COLUMNS has an entry for every table in TABLE_ORDER."""
    assert set(sch.PRIMARY_KEY_COLUMNS.keys()) == set(sch.TABLE_ORDER)


def test_primary_key_columns_are_real_dataclass_fields() -> None:
    # Guards against a typo in PRIMARY_KEY_COLUMNS: every named PK column
    # must be an actual field on the corresponding row dataclass.
    for table, cols in sch.PRIMARY_KEY_COLUMNS.items():
        field_names = {f.name for f in dataclasses.fields(_ROW_CLASS[table])}
        for col in cols:
            assert col in field_names, f"{table}.{col} not a field"


def test_pk_returns_bare_value_for_single_column_pk() -> None:
    """pk() returns an unwrapped scalar (not a 1-tuple) for single-column
    PKs."""
    row = sch.SystemRow(
        system="sandbox",
        description="Sandbox venue.",
        notes=None,
        update_reason=None,
    )
    assert sch.pk(row, "systems") == "sandbox"


def test_pk_returns_tuple_for_deployment_tables_composite_pk(
    sample_deployment_row: sch.DeploymentRow,
) -> None:
    """pk() returns the (table_id, system) tuple for deployment_tables."""
    assert sch.pk(sample_deployment_row, "deployment_tables") == (
        "pagila.general.film",
        "sandbox",
    )


def test_pk_returns_tuple_for_table_relationships_composite_pk(
    sample_relationship_row: sch.TableRelationshipRow,
) -> None:
    """pk() returns the 3-part (table_a_id, table_b_id, relationship_name)
    tuple for table_relationships."""
    assert sch.pk(sample_relationship_row, "table_relationships") == (
        "a",
        "b",
        "default",
    )


def test_pk_returns_tuple_for_column_mappings_composite_pk() -> None:
    """pk() returns the 2-part (source_column_id, mapping_name) tuple for
    column_mappings — there is no target_system dimension."""
    cm = sch.ColumnMappingRow(
        source_column_id="d.s.t.c",
        mapping_name="default",
        target_tables_referenced=(),
        target_expression=None,
        use_when=None,
        notes="x",
        validated=False,
        update_reason=None,
    )
    assert sch.pk(cm, "column_mappings") == ("d.s.t.c", "default")


def test_pk_raises_key_error_for_unknown_table() -> None:
    """pk() raises KeyError when given a name absent from PRIMARY_KEY_COLUMNS."""
    row = sch.SystemRow(
        system="s", description="A venue.", notes=None, update_reason=None
    )
    with pytest.raises(KeyError):
        sch.pk(row, "not_a_table")


def test_columns_is_primary_key_is_content_column_and_field() -> None:
    # is_primary_key is diffed like any other content field (a flag
    # change surfaces as an in-place update), and is a real ColumnRow field.
    assert "is_primary_key" in sch.CONTENT_COLUMNS["columns"]
    field_names = {f.name for f in dataclasses.fields(sch.ColumnRow)}
    assert "is_primary_key" in field_names


def test_columns_ref_table_id_is_content_column_and_defaulted_field() -> None:
    # ref_table_id is diffed like any other content field (a link edit
    # surfaces as an in-place update requiring update_reason once
    # loaded); on the dataclass it is the trailing defaulted field
    # (mirroring the validated_ts placement precedent) so positional
    # construction without it still works and defaults to None.
    assert "ref_table_id" in sch.CONTENT_COLUMNS["columns"]
    field_list = [f.name for f in dataclasses.fields(sch.ColumnRow)]
    assert field_list[-1] == "ref_table_id"
    row = sch.ColumnRow(
        "ocs.general.bene.bene_id", "ocs.general.bene", "bene_id", "TEXT",
        False, True, "d", None, None,
    )
    assert row.ref_table_id is None


def test_columns_primary_key_columns_unchanged_by_is_primary_key() -> None:
    # is_primary_key adds grain knowledge, not a new identity: the columns
    # PK stays (column_id,) — is_primary_key is a content flag.
    assert sch.PRIMARY_KEY_COLUMNS["columns"] == ("column_id",)


def test_data_source_row_has_owner_not_system_or_database_name() -> None:
    # DataSourceRow is keyed by the label alone; it carries an `owner` and
    # has dropped the venue-dependent `system`/`database_name` fields.
    assert sch.PRIMARY_KEY_COLUMNS["data_sources"] == ("data_source_id",)
    field_names = {f.name for f in dataclasses.fields(sch.DataSourceRow)}
    assert "owner" in field_names
    assert "system" not in field_names
    assert "database_name" not in field_names
    content = sch.CONTENT_COLUMNS["data_sources"]
    assert "owner" in content
    assert "system" not in content
    assert "database_name" not in content


def test_deployment_row_fields_pk_and_content() -> None:
    # deployment_tables is the one home of venue-dependent truth: PK
    # (table_id, system), field order matching the DDL for positional
    # DeploymentRow(*row) in db_io, and every field diffable. Pure-facts
    # shape: no notes/update_reason (nor any other freeform field).
    assert sch.PRIMARY_KEY_COLUMNS["deployment_tables"] == (
        "table_id",
        "system",
    )
    field_names = [f.name for f in dataclasses.fields(sch.DeploymentRow)]
    assert field_names == [
        "table_id",
        "system",
        "data_source_id",
        "physical_database_name",
        "physical_schema_name",
        "physical_table_name",
    ]
    assert "notes" not in field_names
    assert "update_reason" not in field_names
    assert sch.CONTENT_COLUMNS["deployment_tables"] == frozenset(
        field_names
    )


def test_deployment_row_is_frozen_and_hashable(
    sample_deployment_row: sch.DeploymentRow,
) -> None:
    hash(sample_deployment_row)
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample_deployment_row.system = "edw"  # type: ignore[misc]


def test_concept_row_fields_and_pk() -> None:
    # concepts is single-PK on concept_id; ConceptRow field order must
    # match the DDL column order (positional ConceptRow(*row) in db_io).
    assert sch.PRIMARY_KEY_COLUMNS["concepts"] == ("concept_id",)
    field_names = [f.name for f in dataclasses.fields(sch.ConceptRow)]
    assert field_names == [
        "concept_id",
        "label",
        "definition",
        "notes",
        "related_object_ids",
        "update_reason",
    ]


def test_concept_content_columns_exclude_timestamps() -> None:
    # A concept carries no validated flag and no FK columns; its diffable
    # content is exactly the six authored fields (no insert_ts/update_ts).
    assert sch.CONTENT_COLUMNS["concepts"] == frozenset(
        {
            "concept_id",
            "label",
            "definition",
            "notes",
            "related_object_ids",
            "update_reason",
        }
    )


def test_pk_returns_bare_value_for_concepts() -> None:
    """pk() returns the bare concept_id for the single-PK concepts table."""
    row = sch.ConceptRow(
        concept_id="sandbox_ocs.concept.claim",
        label="Claim",
        definition="A claim.",
        notes=None,
        related_object_ids=(),
        update_reason=None,
    )
    assert sch.pk(row, "concepts") == "sandbox_ocs.concept.claim"


def test_relationship_has_no_system_and_cardinality_replaces_join_type() -> None:
    # cardinality is a diffable content column (a cardinality-only change
    # surfaces as an update); join_type is gone; there is no system field.
    content = sch.CONTENT_COLUMNS["table_relationships"]
    assert "cardinality" in content
    assert "join_type" not in content
    assert "system" not in content
    field_names = [
        f.name for f in dataclasses.fields(sch.TableRelationshipRow)
    ]
    assert "join_type" not in field_names
    assert "system" not in field_names
    # Positional row-building in db_io requires cardinality right after
    # join_condition.
    assert field_names[
        field_names.index("join_condition") + 1
    ] == "cardinality"


def test_concept_related_object_ids_is_content_column_and_tuple() -> None:
    # related_object_ids diffs like any other content field (a links-only
    # change is an update, not a no-op) and is stored as a tuple so the
    # frozen ConceptRow stays hashable.
    assert "related_object_ids" in sch.CONTENT_COLUMNS["concepts"]
    row = sch.ConceptRow(
        concept_id="pagila.general.concept.active_rental",
        label=None,
        definition="A rental that is currently open.",
        notes=None,
        related_object_ids=("pagila.general.rental",),
        update_reason=None,
    )
    hash(row)
    assert row.related_object_ids == ("pagila.general.rental",)


def test_validated_ts_is_a_field_on_both_validated_tables() -> None:
    """Both validated row dataclasses carry a validated_ts field."""
    for cls in (sch.TableRelationshipRow, sch.ColumnMappingRow):
        field_names = {f.name for f in dataclasses.fields(cls)}
        assert "validated_ts" in field_names, cls.__name__


def test_validated_ts_excluded_from_content_columns() -> None:
    # validated_ts is loader-managed; if it leaked into CONTENT_COLUMNS,
    # every load would churn update_ts / emit spurious _hstry rows.
    for table in ("table_relationships", "column_mappings"):
        assert "validated_ts" not in sch.CONTENT_COLUMNS[table], table


def test_column_mappings_pk_and_content_drop_target_system() -> None:
    # mapping identity loses its target dimension: the PK is
    # (source_column_id, mapping_name); mapping_name and use_when are
    # content columns; target_system / source_system are gone entirely.
    assert sch.PRIMARY_KEY_COLUMNS["column_mappings"] == (
        "source_column_id",
        "mapping_name",
    )
    content = sch.CONTENT_COLUMNS["column_mappings"]
    assert "mapping_name" in content
    assert "use_when" in content
    assert "target_system" not in content
    assert "source_system" not in content
    field_names = {f.name for f in dataclasses.fields(sch.ColumnMappingRow)}
    assert "mapping_name" in field_names
    assert "use_when" in field_names
    assert "target_system" not in field_names
    assert "source_system" not in field_names


def test_validated_ts_defaults_to_none(
    sample_relationship_row: sch.TableRelationshipRow,
) -> None:
    # Corpus rows (built from YAML) never set validated_ts; the default
    # keeps existing positional/kwarg construction working.
    assert sample_relationship_row.validated_ts is None


def test_data_source_id_returns_label() -> None:
    """data_source_id() is the identity on a single-segment label."""
    assert sch.data_source_id("pagila") == "pagila"


def test_schema_id_composes_dotted_string() -> None:
    """schema_id() joins database and schema with a dot (venue-free)."""
    assert sch.schema_id("pagila", "general") == "pagila.general"


def test_table_id_composes_dotted_string() -> None:
    """table_id() joins database, schema, and table with dots."""
    assert (
        sch.table_id("pagila", "general", "film")
        == "pagila.general.film"
    )


def test_column_id_chains_onto_table_id() -> None:
    """column_id() appends the column name to an existing table_id."""
    tbl_id = sch.table_id("pagila", "general", "film")
    assert sch.column_id(tbl_id, "film_id") == "pagila.general.film.film_id"


def test_schema_prefix_of_table_id_returns_first_two_segments() -> None:
    """schema_prefix() on a 3-segment table_id keeps the first two."""
    tbl_id = sch.table_id("pagila", "general", "film")
    assert sch.schema_prefix(tbl_id) == "pagila.general"


def test_schema_prefix_of_column_id_returns_first_two_segments() -> None:
    """schema_prefix() on a 4-segment column_id keeps the first two."""
    col_id = sch.column_id(
        sch.table_id("pagila", "general", "film"), "film_id"
    )
    assert sch.schema_prefix(col_id) == "pagila.general"


def test_schema_prefix_is_inverse_of_table_id() -> None:
    # The assemblers rely on schema_prefix recovering a table_id's owning
    # schema exactly: schema_prefix(table_id(...)) == schema_id(...).
    assert sch.schema_prefix(
        sch.table_id("pagila", "general", "film")
    ) == sch.schema_id("pagila", "general")


def test_split_schema_id_is_inverse_of_schema_id() -> None:
    """split_schema_id() recovers the (database, schema) pair exactly."""
    assert sch.split_schema_id(sch.schema_id("pagila", "general")) == (
        "pagila",
        "general",
    )


def test_split_schema_id_splits_on_first_dot_only() -> None:
    """split_schema_id() uses maxsplit=1: extra dots stay in the schema
    segment rather than raising or being dropped."""
    assert sch.split_schema_id("pagila.general.extra") == (
        "pagila",
        "general.extra",
    )


def test_system_row_is_frozen_and_hashable() -> None:
    row = sch.SystemRow(
        system="s", description="A venue.", notes=None, update_reason=None
    )
    # frozen=True dataclasses are hashable; assignment raises.
    hash(row)
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.system = "x"  # type: ignore[misc]
