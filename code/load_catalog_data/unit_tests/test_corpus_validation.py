"""Unit tests for corpus_validation.py (venue-free model).

Validity is derived from `deployment_tables`: a relationship is runnable
where its two endpoint tables are co-deployed, and a mapping's
referenced tables must share a venue. The `_happy_corpus` fixture
deploys every table so the baseline scenarios are runnable-somewhere.
"""

from dataclasses import replace

import pytest

import corpus_validation as v
from corpus_diff import Diff, RowChange
from data_model import (
    ColumnMappingRow,
    ColumnRow,
    ConceptRow,
    Corpus,
    DataSourceRow,
    DeploymentRow,
    SchemaRow,
    SystemRow,
    TableRelationshipRow,
    TableRow,
)

# Identifiers and keys from the happy corpus (venue-free), shared across
# tests so the multi-segment literals are spelled once.
_BENE_ID = "ocs.general.bene.bene_id"
_CLAIM_BENE_ID = "ocs.general.claim.bene_id"
_MBI = "edw_prd.s.bene.bene_extl_id"
_REL_KEY = ("ocs.general.bene", "ocs.general.claim", "default")
_CM_KEY = (_BENE_ID, "default")
_DROP_KEY = (_CLAIM_BENE_ID, "default")


def _dep(
    table_id: str,
    system: str,
    ds_id: str,
    db: str,
    schema: str,
    table: str,
) -> DeploymentRow:
    """Build a DeploymentRow with documented physical names."""
    return DeploymentRow(
        table_id=table_id,
        system=system,
        data_source_id=ds_id,
        physical_database_name=db,
        physical_schema_name=schema,
        physical_table_name=table,
    )


def _happy_corpus() -> Corpus:
    """Venue-free corpus that passes all validation rules.

    `ocs` deploys in warehouse; `edw_prd` deploys in edw — so the
    within-ocs relationship is runnable in warehouse and the single-table
    edw mapping is computable in edw.
    """
    systems = {
        "warehouse": SystemRow("warehouse", "src", None, None),
        "edw": SystemRow("edw", "tgt", None, None),
    }
    data_sources = {
        "ocs": DataSourceRow("ocs", "data-ops", "OCS.", None, None),
        "edw_prd": DataSourceRow("edw_prd", "data-ops", "EDW.", None, None),
    }
    schemas = {
        "ocs.general": SchemaRow(
            "ocs.general", "ocs", "general", "d", None, None
        ),
        "edw_prd.s": SchemaRow(
            "edw_prd.s", "edw_prd", "s", "d", None, None
        ),
    }
    tables = {
        "ocs.general.bene": TableRow(
            "ocs.general.bene", "ocs.general", "bene", "d", None, None
        ),
        "ocs.general.claim": TableRow(
            "ocs.general.claim", "ocs.general", "claim", "d", None, None
        ),
        "edw_prd.s.bene": TableRow(
            "edw_prd.s.bene", "edw_prd.s", "bene", "d", None, None
        ),
    }
    columns = {
        _BENE_ID: ColumnRow(
            _BENE_ID, "ocs.general.bene", "bene_id", "TEXT", False, True,
            "d", None, None,
        ),
        _CLAIM_BENE_ID: ColumnRow(
            _CLAIM_BENE_ID, "ocs.general.claim", "bene_id", "TEXT", False,
            False, "d", None, None,
        ),
        _MBI: ColumnRow(
            _MBI, "edw_prd.s.bene", "bene_extl_id", "TEXT", True, False,
            "d", None, None,
        ),
    }
    deployment_tables = {
        ("ocs.general.bene", "warehouse"): _dep(
            "ocs.general.bene", "warehouse", "ocs", "ocs", "general", "bene"
        ),
        ("ocs.general.claim", "warehouse"): _dep(
            "ocs.general.claim", "warehouse", "ocs", "ocs", "general", "claim"
        ),
        ("edw_prd.s.bene", "edw"): _dep(
            "edw_prd.s.bene", "edw", "edw_prd", "edw_prd", "s", "bene"
        ),
    }
    table_relationships = {
        _REL_KEY: TableRelationshipRow(
            table_a_id="ocs.general.bene",
            table_b_id="ocs.general.claim",
            relationship_name="default",
            join_condition=f"{_BENE_ID} = {_CLAIM_BENE_ID}",
            cardinality="many_to_one",
            use_when=None,
            notes=None,
            validated=True,
            update_reason=None,
        ),
    }
    column_mappings = {
        _CM_KEY: ColumnMappingRow(
            source_column_id=_BENE_ID,
            mapping_name="default",
            target_tables_referenced=(),
            target_expression=_MBI,
            use_when=None,
            notes=None,
            validated=True,
            update_reason=None,
        ),
        _DROP_KEY: ColumnMappingRow(
            source_column_id=_CLAIM_BENE_ID,
            mapping_name="default",
            target_tables_referenced=(),
            target_expression=None,
            use_when=None,
            notes="Intentionally dropped.",
            validated=True,
            update_reason=None,
        ),
    }
    return Corpus(
        systems=systems,
        data_sources=data_sources,
        schemas=schemas,
        tables=tables,
        columns=columns,
        deployment_tables=deployment_tables,
        table_relationships=table_relationships,
        column_mappings=column_mappings,
        concepts={},
    )


def test_validate_corpus_happy_path() -> None:
    corpus = _happy_corpus()
    memo = v.validate_corpus(corpus)
    assert set(memo.keys()) == set(corpus.column_mappings.keys())
    assert memo[_DROP_KEY] is None
    assert memo[_CM_KEY] is not None


# ---------------------------------------------------------------------------
# FK reference existence
# ---------------------------------------------------------------------------


def test_validate_corpus_missing_fk_schema_raises() -> None:
    corpus = _happy_corpus()
    corpus.schemas["ocs.general"] = replace(
        corpus.schemas["ocs.general"], data_source_id="missing_ds"
    )
    with pytest.raises(v.ValidationError, match="data_source_id='missing_ds'"):
        v.validate_corpus(corpus)


def test_validate_corpus_missing_fk_table_raises() -> None:
    corpus = _happy_corpus()
    corpus.tables["ocs.general.bene"] = replace(
        corpus.tables["ocs.general.bene"], schema_id="missing.schema"
    )
    with pytest.raises(v.ValidationError, match="schema_id='missing.schema'"):
        v.validate_corpus(corpus)


def test_validate_corpus_missing_fk_column_raises() -> None:
    corpus = _happy_corpus()
    corpus.columns[_BENE_ID] = replace(
        corpus.columns[_BENE_ID], table_id="missing.t"
    )
    with pytest.raises(v.ValidationError, match="table_id='missing.t'"):
        v.validate_corpus(corpus)


def test_validate_corpus_deployment_unknown_system_raises() -> None:
    corpus = _happy_corpus()
    key = ("ocs.general.bene", "warehouse")
    corpus.deployment_tables[key] = replace(
        corpus.deployment_tables[key], system="ghost"
    )
    with pytest.raises(v.ValidationError, match="system='ghost' not defined"):
        v.validate_corpus(corpus)


def test_validate_corpus_deployment_unknown_table_raises() -> None:
    # Defense-in-depth: a deployment whose table_id resolves to no
    # documented table (only reachable for a corpus built by some route
    # other than the loader's expansion).
    corpus = _happy_corpus()
    corpus.deployment_tables[("ocs.general.ghost", "warehouse")] = _dep(
        "ocs.general.ghost", "warehouse", "ocs", "ocs", "general", "ghost"
    )
    with pytest.raises(
        v.ValidationError, match=r"table_id='ocs.general.ghost' not defined"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_deployment_unknown_data_source_raises() -> None:
    corpus = _happy_corpus()
    key = ("ocs.general.bene", "warehouse")
    corpus.deployment_tables[key] = replace(
        corpus.deployment_tables[key], data_source_id="ghost_ds"
    )
    with pytest.raises(
        v.ValidationError, match=r"data_source_id='ghost_ds' not defined"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_deployment_data_source_id_table_id_mismatch_raises() -> None:
    # data_source_id must equal table_id's leading segment (the documented
    # redundancy). Point it at a different but existing data source so the
    # FK check passes and the redundancy check is the one that fires.
    corpus = _happy_corpus()
    key = ("ocs.general.bene", "warehouse")
    corpus.deployment_tables[key] = replace(
        corpus.deployment_tables[key], data_source_id="edw_prd"
    )
    with pytest.raises(
        v.ValidationError, match="does not equal table_id's leading segment"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_table_relationship_unknown_table_raises() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        table_a_id="ocs.general.ghost",
    )
    with pytest.raises(v.ValidationError, match="ghost"):
        v.validate_corpus(corpus)


def test_validate_corpus_table_relationship_unknown_table_b_raises() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        table_b_id="ocs.general.ghost",
    )
    with pytest.raises(
        v.ValidationError, match=r"table_b_id='ocs.general.ghost' not"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_table_a_id_case_mismatch_hint() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        table_a_id="OCS.general.bene",
    )
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    issues = excinfo.value.issues
    assert any("table_a_id" in i and "case mismatch" in i for i in issues)
    assert any("'ocs.general.bene'" in i for i in issues)


def test_validate_corpus_null_expression_requires_notes() -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_DROP_KEY] = replace(
        corpus.column_mappings[_DROP_KEY], notes=None
    )
    with pytest.raises(
        v.ValidationError, match="target_expression is null and notes is empty"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_null_expression_whitespace_notes_rejected() -> None:
    # Whitespace-only drop notes carry no rationale — treated as missing
    # (stripped-string semantics; assembly guarantees string-or-null).
    corpus = _happy_corpus()
    corpus.column_mappings[_DROP_KEY] = replace(
        corpus.column_mappings[_DROP_KEY], notes="   \t"
    )
    with pytest.raises(
        v.ValidationError, match="intentional drops require rationale"
    ):
        v.validate_corpus(corpus)


# ---------------------------------------------------------------------------
# ref_table_id resolution (columns' optional domain pointer)
# ---------------------------------------------------------------------------


def test_validate_corpus_ref_table_resolvable_cross_source_passes() -> None:
    # A ocs column pointing at an edw_prd table resolves even though the
    # two sources share no venue (ocs deploys in warehouse, edw_prd in edw):
    # the pointer is context retrieval, so no co-deployment rule applies.
    corpus = _happy_corpus()
    corpus.columns[_BENE_ID] = replace(
        corpus.columns[_BENE_ID], ref_table_id="edw_prd.s.bene"
    )
    v.validate_corpus(corpus)  # no ValidationError


def test_validate_corpus_ref_table_unresolvable_raises() -> None:
    corpus = _happy_corpus()
    corpus.columns[_BENE_ID] = replace(
        corpus.columns[_BENE_ID], ref_table_id="ref.codes.ghost"
    )
    with pytest.raises(
        v.ValidationError,
        match="ref_table_id='ref.codes.ghost' does not resolve",
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_ref_table_case_mismatch_hint() -> None:
    # A mis-cased pointer gets the near-match "did you mean …?" hint
    # naming the lowercase documented table id.
    corpus = _happy_corpus()
    corpus.columns[_BENE_ID] = replace(
        corpus.columns[_BENE_ID], ref_table_id="EDW_PRD.s.bene"
    )
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    issues = excinfo.value.issues
    assert any(
        "ref_table_id" in i and "case mismatch" in i for i in issues
    )
    assert any("'edw_prd.s.bene'" in i for i in issues)


def test_validate_corpus_null_ref_table_passes() -> None:
    # The pointer is optional: the happy corpus authors none, and the
    # baseline already passes — assert the field really is None so this
    # test keeps guarding the optionality contract.
    corpus = _happy_corpus()
    assert corpus.columns[_BENE_ID].ref_table_id is None
    v.validate_corpus(corpus)


# ---------------------------------------------------------------------------
# Identifier syntax
# ---------------------------------------------------------------------------


def test_validate_corpus_bad_identifier_table_name_raises() -> None:
    corpus = _happy_corpus()
    corpus.tables["ocs.general.bene"] = replace(
        corpus.tables["ocs.general.bene"], table_name="bad.name"
    )
    with pytest.raises(v.ValidationError, match="Invalid table_name"):
        v.validate_corpus(corpus)


def test_validate_corpus_bad_identifier_column_name_raises() -> None:
    corpus = _happy_corpus()
    corpus.columns[_BENE_ID] = replace(
        corpus.columns[_BENE_ID], column_name="bad name"
    )
    with pytest.raises(v.ValidationError, match="Invalid column_name"):
        v.validate_corpus(corpus)


def test_validate_corpus_bad_identifier_relationship_name_raises() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY], relationship_name="bad.name"
    )
    with pytest.raises(v.ValidationError, match="Invalid relationship_name"):
        v.validate_corpus(corpus)


def test_validate_corpus_bad_identifier_mapping_name_raises() -> None:
    # mapping_name is relationship_name's analogue: a body-authored
    # discriminator of a composite PK. Wave 1 validates it where it is
    # authored; this is the defense-in-depth re-check for a row that
    # reached wave 2 by any other route.
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY], mapping_name="bad.name"
    )
    with pytest.raises(v.ValidationError, match="Invalid mapping_name"):
        v.validate_corpus(corpus)


# ---------------------------------------------------------------------------
# Deployment residency rules
# ---------------------------------------------------------------------------


def test_validate_corpus_physical_address_collision_rejected() -> None:
    # Two deployment rows claiming the same (system + physical names) is one
    # physical object with two documented identities — rejected.
    corpus = _happy_corpus()
    key = ("ocs.general.claim", "warehouse")
    corpus.deployment_tables[key] = replace(
        corpus.deployment_tables[key], physical_table_name="bene"  # collides w/ bene
    )
    with pytest.raises(v.ValidationError, match="physical address"):
        v.validate_corpus(corpus)


def test_validate_corpus_uppercase_physical_name_not_rechecked() -> None:
    # Physical-name case is a wave-1 assembly check (the deployment file
    # rules, enforced in corpus_assembly where the names are authored); wave 2
    # no longer double-reports it. Uniqueness (above) is what stays here.
    corpus = _happy_corpus()
    key = ("ocs.general.bene", "warehouse")
    corpus.deployment_tables[key] = replace(
        corpus.deployment_tables[key], physical_table_name="BENE"
    )
    v.validate_corpus(corpus)


# ---------------------------------------------------------------------------
# SQL: join_condition
# ---------------------------------------------------------------------------


def test_validate_corpus_unparsable_join_condition_raises() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        join_condition="SELECT FROM WHERE",
    )
    with pytest.raises(v.ValidationError, match="Failed to parse SQL"):
        v.validate_corpus(corpus)


def test_validate_corpus_join_condition_partially_qualified_raises() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        join_condition="a.b.c = d.e.f",
    )
    with pytest.raises(v.ValidationError, match="not fully qualified"):
        v.validate_corpus(corpus)


def test_validate_corpus_join_condition_unknown_column_raises() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        join_condition=f"ocs.general.bene.ghost = {_CLAIM_BENE_ID}",
    )
    with pytest.raises(v.ValidationError, match="unknown column"):
        v.validate_corpus(corpus)


def test_validate_corpus_join_condition_volatile_rejected() -> None:
    # A join_condition is held to the target-expression determinism bar.
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        join_condition=f"{_BENE_ID} = {_CLAIM_BENE_ID} AND {_CLAIM_BENE_ID} > now()",
    )
    with pytest.raises(v.ValidationError, match="volatile/context-dependent"):
        v.validate_corpus(corpus)


def test_validate_corpus_join_condition_navigation_rejected() -> None:
    # A join_condition may not embed navigation (subquery/SELECT/etc.).
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        join_condition=f"{_BENE_ID} = (SELECT {_CLAIM_BENE_ID})",
    )
    with pytest.raises(
        v.ValidationError, match="join_condition contains a"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_join_condition_null_safe_accepted() -> None:
    # Null-safe comparisons are valid boolean predicates end-to-end.
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        join_condition=f"{_BENE_ID} IS NOT DISTINCT FROM {_CLAIM_BENE_ID}",
    )
    v.validate_corpus(corpus)


# ---------------------------------------------------------------------------
# SQL: target_expression
# ---------------------------------------------------------------------------


def test_validate_corpus_target_expression_unparsable_raises() -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY], target_expression="SELECT FROM"
    )
    with pytest.raises(v.ValidationError, match="Failed to parse SQL"):
        v.validate_corpus(corpus)


def test_validate_corpus_target_expression_partially_qualified_raises() -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY], target_expression="a.b.c"
    )
    with pytest.raises(v.ValidationError, match="not fully qualified"):
        v.validate_corpus(corpus)


def test_validate_corpus_target_expression_unknown_column_raises() -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression="edw_prd.s.bene.ghost",
    )
    with pytest.raises(v.ValidationError, match="unknown column"):
        v.validate_corpus(corpus)


def test_validate_corpus_target_expression_case_mismatch_hint() -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression="EDW_PRD.s.bene.bene_extl_id",
    )
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    assert any("case mismatch" in i for i in excinfo.value.issues)
    assert any(
        "'edw_prd.s.bene.bene_extl_id'" in i for i in excinfo.value.issues
    )


def test_validate_corpus_unknown_column_no_false_case_hint() -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression="edw_prd.s.bene.totally_absent",
    )
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    assert any("unknown column" in i for i in excinfo.value.issues)
    assert not any("case mismatch" in i for i in excinfo.value.issues)


def test_validate_corpus_target_expression_source_table_self_reference_rejected() -> None:
    # A reference back to the source column's own table is rejected — a
    # value trivially equivalent to itself is not a translation.
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression=_BENE_ID,  # source column's own table (ocs.general.bene)
    )
    with pytest.raises(
        v.ValidationError, match="source column's own table"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_target_expression_constant_rejected() -> None:
    # A non-null target_expression must reference at least one column: a
    # constant identifies no target dataset. The documented route for "no
    # target equivalent" is target_expression: null plus notes.
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY], target_expression="42"
    )
    with pytest.raises(
        v.ValidationError, match="references no columns"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_mapping_to_other_table_same_source_ok() -> None:
    # Referencing a different table (even in the same data source) is a
    # legitimate equivalence — only the source column's own table is barred.
    corpus = _happy_corpus()
    # ocs tables are co-deployed in warehouse, so the cross-table expression is
    # computable there.
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression=_CLAIM_BENE_ID,  # ocs.general.claim, not bene
        use_when=None,
    )
    v.validate_corpus(corpus)


def test_validate_corpus_column_mapping_unknown_source_column_raises() -> None:
    corpus = _happy_corpus()
    new_key = ("ocs.general.bene.ghost", "default")
    corpus.column_mappings[new_key] = replace(
        corpus.column_mappings[_CM_KEY],
        source_column_id="ocs.general.bene.ghost",
    )
    del corpus.column_mappings[_CM_KEY]
    with pytest.raises(
        v.ValidationError,
        match="source_column_id='ocs.general.bene.ghost' not defined",
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_source_column_id_case_mismatch_hint() -> None:
    corpus = _happy_corpus()
    new_key = ("OCS.general.bene.BENE_ID", "default")
    corpus.column_mappings[new_key] = replace(
        corpus.column_mappings[_CM_KEY],
        source_column_id="OCS.general.bene.BENE_ID",
    )
    del corpus.column_mappings[_CM_KEY]
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    issues = excinfo.value.issues
    assert any("source_column_id" in i and "case mismatch" in i for i in issues)
    assert any("'ocs.general.bene.bene_id'" in i for i in issues)


def test_validate_corpus_aggregates_multiple_issues() -> None:
    corpus = _happy_corpus()
    corpus.tables["ocs.general.bene"] = replace(
        corpus.tables["ocs.general.bene"], table_name="bad.name"
    )
    corpus.columns[_BENE_ID] = replace(
        corpus.columns[_BENE_ID], column_name="bad name"
    )
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY], cardinality="NOPE"
    )
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    assert len(excinfo.value.issues) >= 3


# ---------------------------------------------------------------------------
# Builders for the relationship/mapping rule tests
# ---------------------------------------------------------------------------


def _rel(
    a: str,
    b: str,
    name: str,
    cond: str,
    cardinality: str | None = None,
    use_when: str | None = None,
) -> TableRelationshipRow:
    """Build a validated TableRelationshipRow with default use_when/notes."""
    return TableRelationshipRow(
        table_a_id=a,
        table_b_id=b,
        relationship_name=name,
        join_condition=cond,
        cardinality=cardinality,
        use_when=use_when,
        notes=None,
        validated=True,
        update_reason=None,
    )


def _cm(
    source: str,
    name: str,
    expr: str | None,
    use_when: str | None = None,
) -> ColumnMappingRow:
    """Build an unvalidated ColumnMappingRow with default use_when/notes."""
    return ColumnMappingRow(
        source_column_id=source,
        mapping_name=name,
        target_tables_referenced=(),
        target_expression=expr,
        use_when=use_when,
        notes=None,
        validated=False,
        update_reason=None,
    )


def _edw_column(table: str, column: str) -> ColumnRow:
    """Build a nullable TEXT ColumnRow in the edw_prd.s schema."""
    return ColumnRow(
        column_id=f"edw_prd.s.{table}.{column}",
        table_id=f"edw_prd.s.{table}",
        column_name=column,
        data_type="TEXT",
        is_nullable=True,
        is_primary_key=False,
        description="d",
        notes=None,
        update_reason=None,
    )


# --- Relationship co-deployment (runnable somewhere) ---


def test_validate_corpus_relationship_runnable_nowhere_rejected() -> None:
    # ocs.general.bene deploys in warehouse only; edw_prd.s.bene in edw only —
    # a relationship between them is runnable nowhere.
    corpus = _happy_corpus()
    corpus.table_relationships[
        ("ocs.general.bene", "edw_prd.s.bene", "cross")
    ] = _rel(
        "ocs.general.bene",
        "edw_prd.s.bene",
        "cross",
        f"{_BENE_ID} = {_MBI}",
    )
    with pytest.raises(v.ValidationError, match="runnable somewhere"):
        v.validate_corpus(corpus)


def test_validate_corpus_relationship_cross_source_shared_venue_accepted() -> None:
    # A cross-source pair that shares a venue only via deployments is
    # runnable there. Deploy edw_prd.s.bene in warehouse too, then relate it to
    # ocs.general.bene.
    corpus = _happy_corpus()
    corpus.deployment_tables[("edw_prd.s.bene", "warehouse")] = _dep(
        "edw_prd.s.bene", "warehouse", "edw_prd", "edw_prd", "s", "bene"
    )
    corpus.table_relationships[
        ("ocs.general.bene", "edw_prd.s.bene", "cross")
    ] = _rel(
        "ocs.general.bene",
        "edw_prd.s.bene",
        "cross",
        f"{_BENE_ID} = {_MBI}",
    )
    v.validate_corpus(corpus)


# --- B: orientation-duplicate detection ---


def test_validate_corpus_b_reverse_orientation_pair_rejected() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[
        ("ocs.general.claim", "ocs.general.bene", "default")
    ] = _rel(
        "ocs.general.claim",
        "ocs.general.bene",
        "default",
        f"{_CLAIM_BENE_ID} = {_BENE_ID}",
    )
    with pytest.raises(v.ValidationError, match="duplicate relationship"):
        v.validate_corpus(corpus)


def test_validate_corpus_b_same_pair_distinct_names_accepted() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY], use_when="Use for the FK join."
    )
    corpus.table_relationships[
        ("ocs.general.bene", "ocs.general.claim", "via_alt")
    ] = _rel(
        "ocs.general.bene",
        "ocs.general.claim",
        "via_alt",
        f"{_BENE_ID} = {_CLAIM_BENE_ID}",
        use_when="Alternative link.",
    )
    v.validate_corpus(corpus)


def test_validate_corpus_b_self_join_single_name_accepted() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[
        ("ocs.general.bene", "ocs.general.bene", "hierarchy")
    ] = _rel(
        "ocs.general.bene",
        "ocs.general.bene",
        "hierarchy",
        f"{_BENE_ID} = {_BENE_ID}",
    )
    v.validate_corpus(corpus)


# --- D: join_condition references only the two related tables ---


def test_validate_corpus_d_third_table_rejected() -> None:
    corpus = _happy_corpus()
    corpus.tables["ocs.general.other"] = TableRow(
        "ocs.general.other", "ocs.general", "other", "d", None, None
    )
    corpus.columns["ocs.general.other.k"] = ColumnRow(
        "ocs.general.other.k", "ocs.general.other", "k", "TEXT", True, False,
        "d", None, None,
    )
    corpus.deployment_tables[("ocs.general.other", "warehouse")] = _dep(
        "ocs.general.other", "warehouse", "ocs", "ocs", "general", "other"
    )
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        join_condition=(
            f"{_BENE_ID} = {_CLAIM_BENE_ID} "
            f"AND {_CLAIM_BENE_ID} = ocs.general.other.k"
        ),
    )
    with pytest.raises(v.ValidationError, match="neither table_a nor table_b"):
        v.validate_corpus(corpus)


def test_validate_corpus_d_only_one_endpoint_rejected() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        join_condition=f"{_BENE_ID} = {_BENE_ID}",  # only bene
    )
    with pytest.raises(
        v.ValidationError, match="does not reference both endpoints"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_d_functions_and_casts_accepted() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        join_condition=f"LOWER({_BENE_ID}) = LOWER({_CLAIM_BENE_ID})",
    )
    v.validate_corpus(corpus)


def test_validate_corpus_d_self_join_condition_accepted() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[
        ("ocs.general.bene", "ocs.general.bene", "self")
    ] = _rel(
        "ocs.general.bene",
        "ocs.general.bene",
        "self",
        f"{_BENE_ID} = {_BENE_ID}",
    )
    v.validate_corpus(corpus)


# --- cardinality enum ---


@pytest.mark.parametrize(
    "cardinality",
    ["one_to_one", "one_to_many", "many_to_one", "many_to_many", None],
)
def test_validate_corpus_valid_cardinality_accepted(
    cardinality: str | None,
) -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY], cardinality=cardinality
    )
    v.validate_corpus(corpus)


@pytest.mark.parametrize("cardinality", ["1_to_many", "MANY_TO_ONE"])
def test_validate_corpus_invalid_cardinality_rejected(cardinality: str) -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY], cardinality=cardinality
    )
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    issues = excinfo.value.issues
    assert any(f"cardinality {cardinality!r}" in i for i in issues)
    assert any("many_to_one" in i for i in issues)


# --- G: join_condition must be a boolean predicate ---


def test_validate_corpus_g_bare_column_rejected() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY], join_condition=_BENE_ID
    )
    with pytest.raises(v.ValidationError, match="not a boolean predicate"):
        v.validate_corpus(corpus)


def test_validate_corpus_g_and_predicate_accepted() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY],
        join_condition=(
            f"{_BENE_ID} = {_CLAIM_BENE_ID} AND {_CLAIM_BENE_ID} IS NOT NULL"
        ),
    )
    v.validate_corpus(corpus)


# --- use_when required when a pair carries multiple relationships ---


def test_validate_corpus_multi_relationship_pair_missing_use_when_rejected() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[
        ("ocs.general.bene", "ocs.general.claim", "via_alt")
    ] = _rel(
        "ocs.general.bene",
        "ocs.general.claim",
        "via_alt",
        f"{_BENE_ID} = {_CLAIM_BENE_ID}",
        use_when="Alternative.",
    )
    with pytest.raises(v.ValidationError, match="must set use_when"):
        v.validate_corpus(corpus)


def test_validate_corpus_multi_relationship_pair_whitespace_use_when_rejected() -> None:
    # A whitespace-only use_when disambiguates nothing — treated as
    # missing (stripped-string semantics).
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY], use_when="   "
    )
    corpus.table_relationships[
        ("ocs.general.bene", "ocs.general.claim", "via_alt")
    ] = _rel(
        "ocs.general.bene",
        "ocs.general.claim",
        "via_alt",
        f"{_BENE_ID} = {_CLAIM_BENE_ID}",
        use_when="Alternative.",
    )
    with pytest.raises(v.ValidationError, match="must set use_when"):
        v.validate_corpus(corpus)


def test_validate_corpus_single_relationship_null_use_when_accepted() -> None:
    v.validate_corpus(_happy_corpus())


# --- M1: target_expression is an expression, not a statement ---


def test_validate_corpus_m1_subquery_rejected() -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression=f"(SELECT {_MBI} FROM edw_prd.s.bene)",
    )
    with pytest.raises(
        v.ValidationError, match="single value-producing expression"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_m1_trailing_statement_rejected() -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression=f"{_MBI}; DROP TABLE edw_prd.s.bene",
    )
    with pytest.raises(
        v.ValidationError, match="single value-producing expression"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_m1_scalar_accepted() -> None:
    v.validate_corpus(_happy_corpus())


# --- M2: allowed construct taxonomy (all accepted) ---


@pytest.mark.parametrize(
    "expr",
    [
        f"CASE WHEN {_MBI} IS NULL THEN 'x' ELSE {_MBI} END",  # conditional
        f"SUM({_MBI})",  # cross-grain aggregate
        f"SUM({_MBI}) FILTER (WHERE {_MBI} IS NOT NULL)",  # FILTER
        f"SUM(CASE WHEN {_MBI} IS NOT NULL THEN 1 ELSE 0 END)",  # SUM(CASE)
        f"ROW_NUMBER() OVER (PARTITION BY {_MBI} ORDER BY {_MBI})",  # window
        f"string_agg({_MBI}, ',' ORDER BY {_MBI})",  # ordered aggregate
        f"LAST_VALUE({_MBI}) OVER (PARTITION BY {_MBI} ORDER BY {_MBI})",
    ],
)
def test_validate_corpus_m2_allowed_constructs_accepted(expr: str) -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY], target_expression=expr
    )
    v.validate_corpus(corpus)


# --- M3: determinism ---


@pytest.mark.parametrize("expr", ["now()", "random()", "current_user"])
def test_validate_corpus_m3_volatile_rejected(expr: str) -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY], target_expression=expr
    )
    with pytest.raises(v.ValidationError, match="volatile/context-dependent"):
        v.validate_corpus(corpus)


def test_validate_corpus_m3_at_time_zone_accepted() -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression=f"{_MBI} AT TIME ZONE 'UTC'",
    )
    v.validate_corpus(corpus)


# --- mapping co-deployment ---


def _add_two_edw_tables(corpus: Corpus, *, both_in_edw: bool = True) -> None:
    """Add edw tables t1, t2 (one column each). Deploy t1 in edw; t2 in edw
    when `both_in_edw`, else warehouse only (so they share no venue)."""
    for t in ("t1", "t2"):
        corpus.tables[f"edw_prd.s.{t}"] = TableRow(
            f"edw_prd.s.{t}", "edw_prd.s", t, "d", None, None
        )
    corpus.columns["edw_prd.s.t1.a"] = _edw_column("t1", "a")
    corpus.columns["edw_prd.s.t2.b"] = _edw_column("t2", "b")
    corpus.deployment_tables[("edw_prd.s.t1", "edw")] = _dep(
        "edw_prd.s.t1", "edw", "edw_prd", "edw_prd", "s", "t1"
    )
    t2_system = "edw" if both_in_edw else "warehouse"
    corpus.deployment_tables[("edw_prd.s.t2", t2_system)] = _dep(
        "edw_prd.s.t2", t2_system, "edw_prd", "edw_prd", "s", "t2"
    )


def _link_t1_t2(corpus: Corpus) -> None:
    """Document a relationship linking t1 and t2 (linkability floor)."""
    corpus.table_relationships[
        ("edw_prd.s.t1", "edw_prd.s.t2", "default")
    ] = _rel(
        "edw_prd.s.t1",
        "edw_prd.s.t2",
        "default",
        "edw_prd.s.t1.a = edw_prd.s.t2.b",
    )


def test_validate_corpus_mapping_not_codeployed_rejected() -> None:
    # A multi-table expression whose tables are linkable but share no venue
    # is computable nowhere.
    corpus = _happy_corpus()
    _add_two_edw_tables(corpus, both_in_edw=False)
    _link_t1_t2(corpus)
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression="edw_prd.s.t1.a || edw_prd.s.t2.b",
    )
    with pytest.raises(
        v.ValidationError, match="not co-deployed in any single venue"
    ):
        v.validate_corpus(corpus)


def test_validate_corpus_single_table_mapping_deployed_nowhere_rejected() -> None:
    # Co-deployment applies to single-table expressions too: a documented
    # table excluded by every exhaustive deployments map deploys nowhere,
    # so a mapping targeting it is computable nowhere (stricter than the
    # target-expression shape rule's multi-table wording — deliberately so).
    corpus = _happy_corpus()
    corpus.tables["edw_prd.s.t3"] = TableRow(
        "edw_prd.s.t3", "edw_prd.s", "t3", "d", None, None
    )
    corpus.columns["edw_prd.s.t3.c"] = _edw_column("t3", "c")
    # No deployment_tables row for t3: documented but deployed nowhere.
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression="edw_prd.s.t3.c",
    )
    with pytest.raises(
        v.ValidationError, match="not co-deployed in any single venue"
    ):
        v.validate_corpus(corpus)


# --- M8 (now per source column): use_when for multiple mappings ---


def test_validate_corpus_multiple_mappings_missing_use_when_rejected() -> None:
    corpus = _happy_corpus()
    # existing bene default has use_when=None; add a second on the same source.
    corpus.column_mappings[(_BENE_ID, "alt")] = _cm(
        _BENE_ID, "alt", _MBI, use_when="Legacy pipeline."
    )
    with pytest.raises(v.ValidationError, match="must set use_when"):
        v.validate_corpus(corpus)


def test_validate_corpus_multiple_mappings_whitespace_use_when_rejected() -> None:
    # Stripped-string semantics mirror the relationship rule: a
    # whitespace-only use_when is treated as missing.
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY], use_when=" \t "
    )
    corpus.column_mappings[(_BENE_ID, "alt")] = _cm(
        _BENE_ID, "alt", _MBI, use_when="Legacy pipeline."
    )
    with pytest.raises(v.ValidationError, match="must set use_when"):
        v.validate_corpus(corpus)


def test_validate_corpus_multiple_mappings_with_use_when_accepted() -> None:
    corpus = _happy_corpus()
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY], use_when="Preferred."
    )
    corpus.column_mappings[(_BENE_ID, "alt")] = _cm(
        _BENE_ID, "alt", _MBI, use_when="Legacy pipeline."
    )
    v.validate_corpus(corpus)


# --- M9: multi-table target_expression linkability ---


def test_validate_corpus_m9_unlinkable_multi_table_rejected() -> None:
    corpus = _happy_corpus()
    _add_two_edw_tables(corpus)  # both in edw → co-deployed, but not linked
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression="edw_prd.s.t1.a || edw_prd.s.t2.b",
    )
    with pytest.raises(v.ValidationError, match="not all linkable"):
        v.validate_corpus(corpus)


def test_validate_corpus_m9_unknown_table_no_phantom_linkability_issue() -> None:
    # A typo'd table in a multi-table target_expression yields the
    # unknown-column issue only: the linkability check skips expressions
    # referencing undocumented tables (like the co-deployment check), so
    # one typo does not also read as "not all linkable".
    corpus = _happy_corpus()
    _add_two_edw_tables(corpus)
    _link_t1_t2(corpus)
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression="edw_prd.s.t1.a || edw_prd.s.ghost.b",
    )
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    issues = excinfo.value.issues
    assert any("unknown column" in i for i in issues)
    assert not any("not all linkable" in i for i in issues)


def test_validate_corpus_m9_linked_multi_table_accepted() -> None:
    corpus = _happy_corpus()
    _add_two_edw_tables(corpus)
    _link_t1_t2(corpus)
    corpus.column_mappings[_CM_KEY] = replace(
        corpus.column_mappings[_CM_KEY],
        target_expression="edw_prd.s.t1.a || edw_prd.s.t2.b",
    )
    v.validate_corpus(corpus)


def test_validate_corpus_m9_single_table_accepted() -> None:
    v.validate_corpus(_happy_corpus())


# --- concepts ---


_CLAIM_ID = "ocs.concept.claim"


def test_validate_corpus_valid_concept_passes() -> None:
    corpus = _happy_corpus()
    corpus.concepts[_CLAIM_ID] = ConceptRow(
        concept_id=_CLAIM_ID,
        label="Claim",
        definition="A claim.",
        notes=None,
        related_object_ids=(),
        update_reason=None,
    )
    v.validate_corpus(corpus)


def test_validate_corpus_schema_level_concept_id_passes() -> None:
    corpus = _happy_corpus()
    cid = "edw_prd.s.concept.final_action"
    corpus.concepts[cid] = ConceptRow(
        concept_id=cid,
        label="Final-action record",
        definition="d",
        notes=None,
        related_object_ids=(),
        update_reason=None,
    )
    v.validate_corpus(corpus)


def test_validate_corpus_malformed_concept_id_raises() -> None:
    corpus = _happy_corpus()
    bad = "ocs.concept.bad id"
    corpus.concepts[bad] = ConceptRow(
        concept_id=bad,
        label=None,
        definition="d",
        notes=None,
        related_object_ids=(),
        update_reason=None,
    )
    with pytest.raises(v.ValidationError, match="Invalid concept_id"):
        v.validate_corpus(corpus)


def test_validate_corpus_concept_id_without_reserved_segment_reports_no_anchor_issue() -> None:
    # A concept_id missing the reserved `concept` segment is malformed in
    # shape, which the id-syntax check and the DB CHECK own; the anchor
    # check skips it rather than double-reporting a phantom anchor. Every
    # segment here is a legal ltree label, so the corpus validates clean.
    corpus = _happy_corpus()
    cid = "ocs.general.notaconcept"
    corpus.concepts[cid] = _bare_concept(cid)
    v.validate_corpus(corpus)


def _concept_with_links(links: tuple[str, ...]) -> ConceptRow:
    """A claim concept carrying the given `related_object_ids` links."""
    return ConceptRow(
        concept_id=_CLAIM_ID,
        label="Claim",
        definition="A claim.",
        notes=None,
        related_object_ids=links,
        update_reason=None,
    )


def test_validate_corpus_related_objects_five_id_spaces_resolve() -> None:
    # One entry per linkable id space: data source, schema, table, column,
    # and another concept — every one resolves, so no issues. `systems` is
    # deliberately not linkable.
    corpus = _happy_corpus()
    other = "ocs.concept.beneficiary"
    corpus.concepts[other] = ConceptRow(
        concept_id=other,
        label=None,
        definition="d",
        notes=None,
        related_object_ids=(),
        update_reason=None,
    )
    corpus.concepts[_CLAIM_ID] = _concept_with_links(
        (
            "ocs",  # data_sources
            "ocs.general",  # schemas
            "ocs.general.bene",  # tables
            "ocs.general.bene.bene_id",  # columns
            other,  # concepts
        )
    )
    v.validate_corpus(corpus)


def test_validate_corpus_related_objects_system_link_rejected() -> None:
    # A system id is not a linkable target — concepts are about data, not
    # the venue registry.
    corpus = _happy_corpus()
    corpus.concepts[_CLAIM_ID] = _concept_with_links(("warehouse",))
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    assert any(
        _CLAIM_ID in i and "'warehouse'" in i and "does not resolve" in i
        for i in excinfo.value.issues
    )


def test_validate_corpus_related_objects_unresolved_entry_rejected() -> None:
    corpus = _happy_corpus()
    corpus.concepts[_CLAIM_ID] = _concept_with_links(
        ("ocs.general.ghost_table",)
    )
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    issues = excinfo.value.issues
    assert any(
        _CLAIM_ID in i and "ocs.general.ghost_table" in i for i in issues
    )


def test_validate_corpus_related_objects_case_mismatch_hint() -> None:
    corpus = _happy_corpus()
    corpus.concepts[_CLAIM_ID] = _concept_with_links(("OCS.general.bene",))
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    issues = excinfo.value.issues
    assert any("case mismatch" in i for i in issues)
    assert any("'ocs.general.bene'" in i for i in issues)


# ---------------------------------------------------------------------------
# validate_update_reason (eight authored-row tables; deployment_tables
# rows are derived and exempt)
# ---------------------------------------------------------------------------


def test_validate_update_reason_concept_insert_with_reason_raises() -> None:
    row = ConceptRow(
        concept_id=_CLAIM_ID,
        label=None,
        definition="d",
        notes=None,
        related_object_ids=(),
        update_reason="should be null",
    )
    diff = Diff(
        inserts=[RowChange(table="concepts", key=_CLAIM_ID, old=None, new=row)],
        updates=[],
        deletes=[],
    )
    with pytest.raises(v.ValidationError, match="must be null on fresh inserts"):
        v.validate_update_reason(diff)


def test_validate_update_reason_deployment_update_is_exempt() -> None:
    # deployment_tables rows are derived and carry no update_reason
    # attribute at all: a changed row raises no update_reason issue (the
    # rationale is the git commit via load_audit).
    corpus = _happy_corpus()
    key = ("ocs.general.bene", "warehouse")
    old = corpus.deployment_tables[key]
    new = replace(old, physical_table_name="bene_v2")
    diff = Diff(
        inserts=[],
        updates=[
            RowChange(table="deployment_tables", key=key, old=old, new=new)
        ],
        deletes=[],
    )
    v.validate_update_reason(diff)


def test_validate_update_reason_deployment_exempt_but_authored_row_still_checked() -> None:
    # The exemption is deployment_tables-only: in the same diff, a changed
    # authored-table row with a null update_reason still raises.
    corpus = _happy_corpus()
    key = ("ocs.general.bene", "warehouse")
    dep_old = corpus.deployment_tables[key]
    dep_new = replace(dep_old, physical_table_name="bene_v2")
    sys_row = replace(corpus.systems["warehouse"], update_reason=None)
    diff = Diff(
        inserts=[],
        updates=[
            RowChange(
                table="deployment_tables", key=key, old=dep_old, new=dep_new
            ),
            RowChange(
                table="systems", key="warehouse", old=None, new=sys_row
            ),
        ],
        deletes=[],
    )
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_update_reason(diff)
    issues = excinfo.value.issues
    assert len(issues) == 1
    assert "systems[warehouse]" in issues[0]


def test_validate_update_reason_deployment_fresh_insert_passes() -> None:
    corpus = _happy_corpus()
    key = ("ocs.general.bene", "warehouse")
    row = corpus.deployment_tables[key]
    diff = Diff(
        inserts=[
            RowChange(table="deployment_tables", key=key, old=None, new=row)
        ],
        updates=[],
        deletes=[],
    )
    v.validate_update_reason(diff)


def test_validate_update_reason_insert_with_reason_raises() -> None:
    corpus = _happy_corpus()
    row = replace(corpus.systems["warehouse"], update_reason="should be null")
    diff = Diff(
        inserts=[RowChange(table="systems", key="warehouse", old=None, new=row)],
        updates=[],
        deletes=[],
    )
    with pytest.raises(v.ValidationError, match="must be null on fresh inserts"):
        v.validate_update_reason(diff)


def test_validate_update_reason_update_without_reason_raises() -> None:
    corpus = _happy_corpus()
    row = replace(corpus.systems["warehouse"], update_reason=None)
    diff = Diff(
        inserts=[],
        updates=[RowChange(table="systems", key="warehouse", old=None, new=row)],
        deletes=[],
    )
    with pytest.raises(v.ValidationError, match="required on every update"):
        v.validate_update_reason(diff)


def test_validate_update_reason_happy() -> None:
    diff = Diff(inserts=[], updates=[], deletes=[])
    v.validate_update_reason(diff)


# ---------------------------------------------------------------------------
# Concept anchor existence — the anchor prefix must resolve in the corpus
# ---------------------------------------------------------------------------


def _bare_concept(concept_id: str) -> ConceptRow:
    """A minimal valid concept row at the given id."""
    return ConceptRow(
        concept_id=concept_id,
        label=None,
        definition="A definition.",
        notes=None,
        related_object_ids=(),
        update_reason=None,
    )


@pytest.mark.parametrize(
    ("concept_id", "fragment"),
    [
        pytest.param(
            "ghost.concept.x",
            "does not resolve to a data_sources id",
            id="nonexistent_data_source",
        ),
        pytest.param(
            "ocs.ghost.concept.x",
            "does not resolve to a schemas id",
            id="nonexistent_schema",
        ),
        pytest.param(
            # A single-label anchor that names a *system* is not a data
            # source — systems and data_sources label spaces are disjoint.
            "warehouse.concept.x",
            "does not resolve to a data_sources id",
            id="system_label_anchor",
        ),
        pytest.param(
            "ocs.general.ghost.concept.x",
            "does not resolve to a tables id",
            id="nonexistent_table",
        ),
        pytest.param(
            "ocs.general.bene.ghost.concept.x",
            "does not resolve to a columns id",
            id="nonexistent_column",
        ),
        pytest.param(
            # Anchor depth determines the object kind, and resolution is
            # an exact lookup in that kind's space: `bene_id` exists as a
            # column — but of ocs.general.bene/claim, not edw_prd.s.bene.
            "edw_prd.s.bene.bene_id.concept.x",
            "does not resolve to a columns id",
            id="column_of_different_table",
        ),
        pytest.param(
            # 5 anchor labels: deeper than any addressable object. The
            # message names the four valid depths.
            "ocs.general.bene.bene_id.extra.concept.x",
            "a data source (1 label), a schema (2 labels), a table "
            "(3 labels), or a column (4 labels)",
            id="anchor_deeper_than_column",
        ),
    ],
)
def test_validate_corpus_concept_bad_anchor_rejected(
    concept_id: str, fragment: str
) -> None:
    corpus = _happy_corpus()
    corpus.concepts[concept_id] = _bare_concept(concept_id)
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    assert any(
        concept_id in i and fragment in i for i in excinfo.value.issues
    )


def test_validate_corpus_data_source_and_schema_anchors_pass() -> None:
    # A data-source-level concept (anchor = existing data source) and a
    # schema-level concept (anchor = existing schema) both resolve.
    corpus = _happy_corpus()
    corpus.concepts["ocs.concept.claim"] = _bare_concept("ocs.concept.claim")
    corpus.concepts["ocs.general.concept.x"] = _bare_concept(
        "ocs.general.concept.x"
    )
    v.validate_corpus(corpus)


def test_validate_corpus_table_and_column_anchors_pass() -> None:
    # A table-level concept (anchor = existing table) and a column-level
    # concept (anchor = existing column) both resolve.
    corpus = _happy_corpus()
    corpus.concepts["ocs.general.bene.concept.x"] = _bare_concept(
        "ocs.general.bene.concept.x"
    )
    corpus.concepts["ocs.general.bene.bene_id.concept.x"] = _bare_concept(
        "ocs.general.bene.bene_id.concept.x"
    )
    v.validate_corpus(corpus)


# ---------------------------------------------------------------------------
# Whitespace-only update_reason on an update is treated as missing
# ---------------------------------------------------------------------------


def test_validate_update_reason_update_with_whitespace_reason_raises() -> None:
    corpus = _happy_corpus()
    row = replace(corpus.systems["warehouse"], update_reason="   ")
    diff = Diff(
        inserts=[],
        updates=[RowChange(table="systems", key="warehouse", old=None, new=row)],
        deletes=[],
    )
    with pytest.raises(v.ValidationError, match="null or blank"):
        v.validate_update_reason(diff)


def test_validate_update_reason_update_with_real_reason_passes() -> None:
    corpus = _happy_corpus()
    row = replace(corpus.systems["warehouse"], update_reason="renamed platform")
    diff = Diff(
        inserts=[],
        updates=[RowChange(table="systems", key="warehouse", old=None, new=row)],
        deletes=[],
    )
    v.validate_update_reason(diff)


# ---------------------------------------------------------------------------
# join_condition must reference at least one column (even on a self-join)
# ---------------------------------------------------------------------------


def test_validate_corpus_self_join_constant_condition_rejected() -> None:
    # The hole this closes: a self-relationship's `1 = 1` used to pass
    # because the both-endpoints coverage check is gated on differing
    # endpoints.
    corpus = _happy_corpus()
    corpus.table_relationships[
        ("ocs.general.bene", "ocs.general.bene", "self")
    ] = _rel("ocs.general.bene", "ocs.general.bene", "self", "1 = 1")
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    assert any("references no columns" in i for i in excinfo.value.issues)


def test_validate_corpus_distinct_endpoint_constant_condition_rejected() -> None:
    corpus = _happy_corpus()
    corpus.table_relationships[_REL_KEY] = replace(
        corpus.table_relationships[_REL_KEY], join_condition="1 = 1"
    )
    with pytest.raises(v.ValidationError) as excinfo:
        v.validate_corpus(corpus)
    assert any("references no columns" in i for i in excinfo.value.issues)
