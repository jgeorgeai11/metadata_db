"""Unit tests for corpus_diff.py (venue-free model)."""

import pytest

import corpus_diff
from corpus_validation import ValidationError, validate_update_reason
from data_model import (
    ColumnMappingRow,
    ConceptRow,
    Corpus,
    DbState,
    DeploymentRow,
    SystemRow,
    TableRelationshipRow,
    TableRow,
    empty_corpus,
    empty_db_state,
)


def _system(name: str, description: str = "x", reason: str | None = None) -> SystemRow:
    return SystemRow(system=name, description=description, notes=None, update_reason=reason)


_CLAIM_ID = "sandbox_ocs.concept.claim"


def _concept(
    definition: str = "d",
    reason: str | None = None,
    related: tuple[str, ...] = (),
) -> ConceptRow:
    return ConceptRow(
        concept_id=_CLAIM_ID,
        label="Claim",
        definition=definition,
        notes=None,
        related_object_ids=related,
        update_reason=reason,
    )


_DEP_KEY = ("ocs.general.bene", "warehouse")


def _deployment(physical_table_name: str = "bene") -> DeploymentRow:
    """A deployment fixture keyed by the composite `_DEP_KEY` tuple.

    Pure-facts shape: no notes/update_reason fields exist on the row.
    """
    return DeploymentRow(
        table_id=_DEP_KEY[0],
        system=_DEP_KEY[1],
        data_source_id="ocs",
        physical_database_name="ocs",
        physical_schema_name="general",
        physical_table_name=physical_table_name,
    )


_REL_KEY = ("ocs.general.bene", "ocs.general.claim", "default")


def _rel(
    cardinality: str | None = None, reason: str | None = None
) -> TableRelationshipRow:
    """A relationship fixture (venue-free: no system column)."""
    return TableRelationshipRow(
        table_a_id=_REL_KEY[0],
        table_b_id=_REL_KEY[1],
        relationship_name=_REL_KEY[2],
        join_condition=(
            "ocs.general.bene.bene_id = ocs.general.claim.bene_id"
        ),
        cardinality=cardinality,
        use_when=None,
        notes=None,
        validated=False,
        update_reason=reason,
    )


_MAP_KEY = ("ocs.general.bene.bene_id", "default")


def _mapping(
    target_expression: str | None = None, reason: str | None = None
) -> ColumnMappingRow:
    """A column-mapping fixture keyed by the composite `_MAP_KEY` tuple
    (source_column_id, mapping_name) — no target_system dimension."""
    return ColumnMappingRow(
        source_column_id=_MAP_KEY[0],
        mapping_name=_MAP_KEY[1],
        target_tables_referenced=(),
        target_expression=target_expression,
        use_when=None,
        notes=None,
        validated=False,
        update_reason=reason,
    )


# ---------------------------------------------------------------------------
# Diff (is_empty / summary)
# ---------------------------------------------------------------------------


def test_diff_is_empty_true_for_empty_lists() -> None:
    d = corpus_diff.Diff()
    assert d.is_empty() is True


def test_diff_summary_format() -> None:
    d = corpus_diff.Diff()
    assert d.summary() == "Diff: 0 insert(s), 0 update(s), 0 delete(s)"


# ---------------------------------------------------------------------------
# systems
# ---------------------------------------------------------------------------


def test_compute_diff_insert_only() -> None:
    corpus = empty_corpus()
    corpus.systems["warehouse"] = _system("warehouse")
    state = empty_db_state()
    d = corpus_diff.compute_diff(corpus, state)
    assert len(d.inserts) == 1
    assert d.inserts[0].table == "systems"
    assert d.inserts[0].key == "warehouse"
    assert d.inserts[0].new == corpus.systems["warehouse"]
    assert d.updates == []
    assert d.deletes == []
    # Pin the non-empty branch of both public Diff methods here: summary()
    # is the loader's dry-run headline, so its counts need an assertion,
    # not just execution.
    assert d.is_empty() is False
    assert d.summary() == "Diff: 1 insert(s), 0 update(s), 0 delete(s)"


def test_compute_diff_delete_only() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    state.systems["warehouse"] = _system("warehouse")
    d = corpus_diff.compute_diff(corpus, state)
    assert len(d.deletes) == 1
    assert d.deletes[0].old == state.systems["warehouse"]


def test_compute_diff_update_when_content_differs() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.systems["warehouse"] = _system("warehouse", description="NEW", reason="reworded")
    state.systems["warehouse"] = _system("warehouse", description="OLD")
    d = corpus_diff.compute_diff(corpus, state)
    assert len(d.updates) == 1
    assert d.updates[0].old.description == "OLD"
    assert d.updates[0].new.description == "NEW"
    assert d.inserts == []
    assert d.deletes == []


def test_compute_diff_no_op_when_content_identical() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.systems["warehouse"] = _system("warehouse")
    state.systems["warehouse"] = _system("warehouse")
    d = corpus_diff.compute_diff(corpus, state)
    assert d.is_empty() is True


def test_compute_diff_pk_change_is_delete_plus_insert() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    old = TableRow(
        table_id="ocs.general.bene_old",
        schema_id="ocs.general",
        table_name="bene_old",
        description=None,
        notes=None,
        update_reason=None,
    )
    new = TableRow(
        table_id="ocs.general.bene_new",
        schema_id="ocs.general",
        table_name="bene_new",
        description=None,
        notes=None,
        update_reason=None,
    )
    state.tables[old.table_id] = old
    corpus.tables[new.table_id] = new
    d = corpus_diff.compute_diff(corpus, state)
    assert len(d.inserts) == 1
    assert d.inserts[0].new.table_id == "ocs.general.bene_new"
    assert len(d.deletes) == 1
    assert d.deletes[0].old.table_id == "ocs.general.bene_old"
    assert d.updates == []


# ---------------------------------------------------------------------------
# deployment_tables — composite key (table_id, system)
# ---------------------------------------------------------------------------


def test_compute_diff_deployment_insert_keyed_by_tuple() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.deployment_tables[_DEP_KEY] = _deployment()
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.inserts] == [
        ("deployment_tables", _DEP_KEY)
    ]
    assert d.updates == []
    assert d.deletes == []


def test_compute_diff_deployment_update_when_physical_name_differs() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.deployment_tables[_DEP_KEY] = _deployment(
        physical_table_name="bene_v2"
    )
    state.deployment_tables[_DEP_KEY] = _deployment(
        physical_table_name="bene"
    )
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.updates] == [
        ("deployment_tables", _DEP_KEY)
    ]
    assert d.updates[0].new.physical_table_name == "bene_v2"
    assert d.inserts == []
    assert d.deletes == []


def test_compute_diff_deployment_delete_keyed_by_tuple() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    state.deployment_tables[_DEP_KEY] = _deployment()
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.deletes] == [
        ("deployment_tables", _DEP_KEY)
    ]


def test_compute_diff_deployment_no_op_when_identical() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.deployment_tables[_DEP_KEY] = _deployment()
    state.deployment_tables[_DEP_KEY] = _deployment()
    assert corpus_diff.compute_diff(corpus, state).is_empty() is True


def test_compute_diff_deployment_insert_then_rediff_is_no_op() -> None:
    # Regression pin: the deployment insert cycle stays idempotent — run 1
    # inserts the expanded row exactly as assembled, run 2 re-diffs the
    # unchanged corpus against that row and reports an empty diff. With
    # the pure-facts shape (no update_reason to normalize) this is
    # trivially true; pinned so it stays true.
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.deployment_tables[_DEP_KEY] = _deployment()
    first = corpus_diff.compute_diff(corpus, state)
    assert len(first.inserts) == 1
    assert first.inserts[0].new == corpus.deployment_tables[_DEP_KEY]
    # Simulate apply_diff: the DB now holds the inserted row.
    state.deployment_tables[_DEP_KEY] = first.inserts[0].new
    assert corpus_diff.compute_diff(corpus, state).is_empty() is True


# ---------------------------------------------------------------------------
# update_reason semantics — uniform across authored tables
# ---------------------------------------------------------------------------


def test_compute_diff_insert_with_reason_keeps_it_and_fails_gate() -> None:
    # There is no insert normalization for any table (the old
    # deployments-only auto-null is gone with the column): an authored
    # insert carrying a non-null update_reason keeps it and remains an
    # authoring error for the update_reason gate.
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.systems["warehouse"] = _system("warehouse", reason="should be null")
    d = corpus_diff.compute_diff(corpus, state)
    assert d.inserts[0].new.update_reason == "should be null"
    with pytest.raises(ValidationError, match="must be null on fresh inserts"):
        validate_update_reason(d)


def test_compute_diff_reason_only_change_is_update() -> None:
    # With the deployments-only signature exclusion gone, the rule is
    # uniform: on every authored table a reason-only difference diffs as
    # an update.
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.systems["warehouse"] = _system("warehouse", reason="new reason")
    state.systems["warehouse"] = _system("warehouse", reason=None)
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.updates] == [("systems", "warehouse")]


# ---------------------------------------------------------------------------
# concepts
# ---------------------------------------------------------------------------


def test_compute_diff_concepts_insert() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.concepts[_CLAIM_ID] = _concept()
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.inserts] == [("concepts", _CLAIM_ID)]


def test_compute_diff_concepts_update() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.concepts[_CLAIM_ID] = _concept(definition="NEW", reason="reworded")
    state.concepts[_CLAIM_ID] = _concept(definition="OLD")
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.updates] == [("concepts", _CLAIM_ID)]
    assert d.updates[0].new.definition == "NEW"


def test_compute_diff_concepts_delete() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    state.concepts[_CLAIM_ID] = _concept()
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.deletes] == [("concepts", _CLAIM_ID)]


def test_compute_diff_concepts_no_op_when_identical() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.concepts[_CLAIM_ID] = _concept()
    state.concepts[_CLAIM_ID] = _concept()
    assert corpus_diff.compute_diff(corpus, state).is_empty() is True


def test_compute_diff_related_object_ids_only_change_is_update() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.concepts[_CLAIM_ID] = _concept(
        reason="added links", related=("ocs.general.bene",)
    )
    state.concepts[_CLAIM_ID] = _concept()
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.updates] == [("concepts", _CLAIM_ID)]
    assert d.updates[0].new.related_object_ids == ("ocs.general.bene",)


def test_compute_diff_identical_related_object_ids_is_no_op() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.concepts[_CLAIM_ID] = _concept(related=("ocs.general.bene",))
    state.concepts[_CLAIM_ID] = _concept(related=("ocs.general.bene",))
    assert corpus_diff.compute_diff(corpus, state).is_empty() is True


# ---------------------------------------------------------------------------
# table_relationships
# ---------------------------------------------------------------------------


def test_compute_diff_cardinality_only_change_is_update() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.table_relationships[_REL_KEY] = _rel(
        cardinality="many_to_one", reason="recorded cardinality"
    )
    state.table_relationships[_REL_KEY] = _rel(cardinality=None)
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.updates] == [
        ("table_relationships", _REL_KEY)
    ]
    assert d.inserts == []
    assert d.deletes == []


def test_compute_diff_identical_cardinality_is_no_op() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.table_relationships[_REL_KEY] = _rel(cardinality="many_to_one")
    state.table_relationships[_REL_KEY] = _rel(cardinality="many_to_one")
    assert corpus_diff.compute_diff(corpus, state).is_empty() is True


# ---------------------------------------------------------------------------
# column_mappings — composite key (source_column_id, mapping_name)
# ---------------------------------------------------------------------------


def test_compute_diff_column_mappings_insert_keyed_by_tuple() -> None:
    # column_mappings PK = (source_column_id, mapping_name); RowChange.key
    # must be that 2-tuple, exactly as the corpus dict is keyed.
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.column_mappings[_MAP_KEY] = _mapping()
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.inserts] == [
        ("column_mappings", _MAP_KEY)
    ]
    assert d.updates == []
    assert d.deletes == []


def test_compute_diff_column_mappings_update_when_expression_differs() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.column_mappings[_MAP_KEY] = _mapping(
        target_expression="edw_prd.s.mbr.mbr_sk", reason="mapped"
    )
    state.column_mappings[_MAP_KEY] = _mapping()
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.updates] == [
        ("column_mappings", _MAP_KEY)
    ]
    assert d.updates[0].new.target_expression == "edw_prd.s.mbr.mbr_sk"
    assert d.inserts == []
    assert d.deletes == []


def test_compute_diff_column_mappings_delete_keyed_by_tuple() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    state.column_mappings[_MAP_KEY] = _mapping()
    d = corpus_diff.compute_diff(corpus, state)
    assert [(c.table, c.key) for c in d.deletes] == [
        ("column_mappings", _MAP_KEY)
    ]


def test_compute_diff_column_mappings_no_op_when_identical() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.column_mappings[_MAP_KEY] = _mapping()
    state.column_mappings[_MAP_KEY] = _mapping()
    assert corpus_diff.compute_diff(corpus, state).is_empty() is True


# ---------------------------------------------------------------------------
# mixed classifications (cross-table)
# ---------------------------------------------------------------------------


def test_compute_diff_mixed_classifications() -> None:
    corpus = empty_corpus()
    state = empty_db_state()
    corpus.systems["warehouse"] = _system("warehouse", description="NEW", reason="x")
    corpus.systems["edw"] = _system("edw")
    state.systems["warehouse"] = _system("warehouse", description="OLD")
    state.systems["cdr"] = _system("cdr")
    d = corpus_diff.compute_diff(corpus, state)
    assert {c.key for c in d.inserts} == {"edw"}
    assert {c.key for c in d.updates} == {"warehouse"}
    assert {c.key for c in d.deletes} == {"cdr"}


# ---------------------------------------------------------------------------
# check_mass_delete
# ---------------------------------------------------------------------------


def _state_with_systems(n: int) -> DbState:
    """Build a DbState holding `n` systems rows (sys0..sys{n-1})."""
    state = empty_db_state()
    for i in range(n):
        state.systems[f"sys{i}"] = _system(f"sys{i}")
    return state


def _keep_in_corpus(state: DbState, keep: range) -> Corpus:
    """Build a corpus retaining only `state`'s systems named in `keep`."""
    corpus = empty_corpus()
    for i in keep:
        corpus.systems[f"sys{i}"] = state.systems[f"sys{i}"]
    return corpus


def test_check_mass_delete_below_min_count_passes() -> None:
    state = _state_with_systems(5)
    d = corpus_diff.compute_diff(empty_corpus(), state)
    assert len(d.deletes) == 5
    corpus_diff.check_mass_delete(d, state, fraction=0.25, min_count=10)


def test_check_mass_delete_exceeding_fraction_raises() -> None:
    state = _state_with_systems(10)
    d = corpus_diff.compute_diff(empty_corpus(), state)
    with pytest.raises(
        corpus_diff.MassDeleteError, match="delete 10 of 10 current DB rows"
    ):
        corpus_diff.check_mass_delete(d, state, fraction=0.25, min_count=5)


def test_check_mass_delete_below_fraction_passes() -> None:
    state = _state_with_systems(100)
    d = corpus_diff.compute_diff(_keep_in_corpus(state, range(10, 100)), state)
    assert len(d.deletes) == 10
    corpus_diff.check_mass_delete(d, state, fraction=0.25, min_count=5)


def test_check_mass_delete_exact_fraction_boundary_passes() -> None:
    state = _state_with_systems(100)
    d = corpus_diff.compute_diff(_keep_in_corpus(state, range(25, 100)), state)
    assert len(d.deletes) == 25
    corpus_diff.check_mass_delete(d, state, fraction=0.25, min_count=5)


def test_check_mass_delete_counts_deployment_tables_in_total() -> None:
    # The denominator spans every main table, deployment_tables included:
    # 10 deployment deletes against 10 deployment_tables + 30 systems is
    # 25% of 40 rows, within a 25% threshold.
    state = empty_db_state()
    for i in range(30):
        state.systems[f"sys{i}"] = _system(f"sys{i}")
    for i in range(10):
        key = (f"ocs.general.t{i}", "warehouse")
        state.deployment_tables[key] = DeploymentRow(
            table_id=f"ocs.general.t{i}",
            system="warehouse",
            data_source_id="ocs",
            physical_database_name="ocs",
            physical_schema_name="general",
            physical_table_name=f"t{i}",
        )
    corpus = empty_corpus()
    for name, row in state.systems.items():
        corpus.systems[name] = row
    d = corpus_diff.compute_diff(corpus, state)
    assert len(d.deletes) == 10
    assert all(c.table == "deployment_tables" for c in d.deletes)
    corpus_diff.check_mass_delete(d, state, fraction=0.25, min_count=5)
