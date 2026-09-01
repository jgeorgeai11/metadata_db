"""Unit tests for corpus_assembly.py (venue-free layout).

Shape-error cases assert on the aggregated `AssemblyError.issues` (the
loader's reporting contract) rather than a first-offender raise: each
bad document is written to disk and run through the public
`assemble_corpus`, and the test checks the issue list it aggregates.

The fixture corpus is built inline (`_build_corpus_tree`) in the
venue-free layout — `data_catalog/systems.yaml` plus `data_catalog/sources/{label}/...`
with per-source `deployments.yaml` — mirroring the shipped corpus.
Multi-file defect tests mutate that clean tree and assert the specific
issue surfaces.
"""

import datetime
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

import corpus_assembly
from data_model import Corpus
from yaml_discovery import PathIdentity, discover_yaml_files


def _build_corpus_tree(root: Path) -> Path:
    """Write a small valid venue-free corpus and return the data root.

    Two data sources: `pagila` (deployed in `sandbox`) and `mart`
    (deployed in `warehouse`). The tree discovers and assembles cleanly.
    """
    (root / "systems.yaml").write_text(
        "- system: sandbox\n"
        "  description: Sandbox platform.\n"
        "  notes: null\n"
        "  update_reason: null\n"
        "- system: warehouse\n"
        "  description: Warehouse platform.\n"
        "  notes: null\n"
        "  update_reason: null\n",
        encoding="utf-8",
    )
    pagila = root / "sources" / "pagila"
    (pagila / "general").mkdir(parents=True)
    (pagila / "data_source.yaml").write_text(
        "owner: data-ops\ndescription: Pagila.\nnotes: null\nupdate_reason: null\n",
        encoding="utf-8",
    )
    (pagila / "deployments.yaml").write_text(
        "- system: sandbox\n", encoding="utf-8"
    )
    (pagila / "general" / "schema.yaml").write_text(
        "description: General schema.\n", encoding="utf-8"
    )
    (pagila / "general" / "tables.yaml").write_text(
        "- table_name: film\n  description: Films.\n"
        "- table_name: actor\n  description: Actors.\n",
        encoding="utf-8",
    )
    (pagila / "general" / "columns.yaml").write_text(
        "- table_name: film\n  column_name: film_id\n  data_type: INT\n"
        "  is_nullable: false\n  is_primary_key: true\n"
        "  description: Film id.\n"
        "- table_name: film\n  column_name: title\n  data_type: TEXT\n"
        "  is_nullable: false\n  description: Film title.\n"
        "- table_name: actor\n  column_name: actor_id\n  data_type: INT\n"
        "  is_nullable: false\n  is_primary_key: true\n"
        "  description: Actor id.\n",
        encoding="utf-8",
    )
    (pagila / "general" / "table_relationships.yaml").write_text(
        "- table_a_id: pagila.general.film\n"
        "  table_b_id: pagila.general.actor\n"
        "  relationship_name: default\n"
        "  join_condition: pagila.general.film.film_id = "
        "pagila.general.actor.actor_id\n"
        "  cardinality: many_to_one\n",
        encoding="utf-8",
    )
    mappings = pagila / "general" / "mappings"
    mappings.mkdir()
    (mappings / "pagila.yaml").write_text(
        "- source_column_id: pagila.general.film.title\n"
        "  mapping_name: default\n"
        "  target_expression: null\n"
        "  notes: Dropped in the target.\n",
        encoding="utf-8",
    )
    mart = root / "sources" / "mart"
    (mart / "analytics").mkdir(parents=True)
    (mart / "data_source.yaml").write_text(
        "owner: data-ops\ndescription: Mart.\nnotes: null\nupdate_reason: null\n",
        encoding="utf-8",
    )
    (mart / "deployments.yaml").write_text(
        "- system: warehouse\n", encoding="utf-8"
    )
    (mart / "analytics" / "schema.yaml").write_text(
        "description: Analytics schema.\n", encoding="utf-8"
    )
    (mart / "analytics" / "tables.yaml").write_text(
        "- table_name: fact\n  description: A fact table.\n", encoding="utf-8"
    )
    (mart / "analytics" / "columns.yaml").write_text(
        "- table_name: fact\n  column_name: film_id\n  data_type: INT\n"
        "  is_nullable: false\n  description: Film id in the fact table.\n",
        encoding="utf-8",
    )
    return root


def _assemble_tree(root: Path) -> Corpus:
    """Discover and assemble the corpus under `root`."""
    files, issues = discover_yaml_files(root)
    return corpus_assembly.assemble_corpus(files, issues)


def _assemble_tree_issues(root: Path) -> list[str]:
    """Assemble the (mutated) tree under `root`, returning aggregated issues."""
    files, issues = discover_yaml_files(root)
    with pytest.raises(corpus_assembly.AssemblyError) as excinfo:
        corpus_assembly.assemble_corpus(files, issues)
    return excinfo.value.issues


def _write_doc(ident: PathIdentity, doc: Any) -> None:
    """Serialize `doc` as YAML at `ident.path` so `assemble_corpus` can read it."""
    ident.path.write_text(yaml.safe_dump(doc), encoding="utf-8")


def _issues_for(doc: Any, ident: PathIdentity) -> list[str]:
    """Assemble `doc` as the single file at `ident`; return aggregated issues."""
    _write_doc(ident, doc)
    with pytest.raises(corpus_assembly.AssemblyError) as excinfo:
        corpus_assembly.assemble_corpus([ident])
    return excinfo.value.issues


def _corpus_for(doc: Any, ident: PathIdentity) -> Corpus:
    """Assemble `doc` as the single file at `ident`; return the clean corpus."""
    _write_doc(ident, doc)
    return corpus_assembly.assemble_corpus([ident])


@pytest.fixture
def example_corpus(tmp_path: Path) -> Corpus:
    """The corpus assembled from the inline venue-free fixture tree."""
    data_root = _build_corpus_tree(tmp_path)
    return _assemble_tree(data_root)


# ---------------------------------------------------------------------------
# load_yaml
# ---------------------------------------------------------------------------


def test_load_yaml_happy_path(tmp_path: Path) -> None:
    p = tmp_path / "x.yaml"
    p.write_text("a: 1\nb: [1, 2]\n", encoding="utf-8")
    assert corpus_assembly.load_yaml(p) == {"a": 1, "b": [1, 2]}


def test_load_yaml_parse_error_raises(tmp_path: Path) -> None:
    p = tmp_path / "broken.yaml"
    p.write_text("a: : :\n  - bad\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Failed to read or parse YAML"):
        corpus_assembly.load_yaml(p)


def test_load_yaml_unreadable_file_raises_value_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ValueError, match="Failed to read or parse YAML"):
        corpus_assembly.load_yaml(missing)


def test_load_yaml_duplicate_mapping_key_raises(tmp_path: Path) -> None:
    p = tmp_path / "dup.yaml"
    p.write_text("description: one\ndescription: two\n", encoding="utf-8")
    with pytest.raises(ValueError, match="found duplicate key"):
        corpus_assembly.load_yaml(p)


def test_load_yaml_unhashable_mapping_key_raises(tmp_path: Path) -> None:
    # A complex (list) mapping key is unhashable; the strict loader falls
    # through to PyYAML's standard "unhashable key" ConstructorError, wrapped
    # into the same ValueError path.
    p = tmp_path / "complex.yaml"
    p.write_text("? [1, 2]\n: value\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Failed to read or parse YAML"):
        corpus_assembly.load_yaml(p)


# ---------------------------------------------------------------------------
# Happy-path corpus round trip
# ---------------------------------------------------------------------------


def test_assemble_corpus_from_fixture(example_corpus: Corpus) -> None:
    assert set(example_corpus.systems.keys()) == {"sandbox", "warehouse"}
    assert set(example_corpus.data_sources.keys()) == {"pagila", "mart"}
    assert set(example_corpus.schemas.keys()) == {
        "pagila.general",
        "mart.analytics",
    }
    assert set(example_corpus.tables.keys()) == {
        "pagila.general.film",
        "pagila.general.actor",
        "mart.analytics.fact",
    }
    assert len(example_corpus.columns) == 4
    assert len(example_corpus.table_relationships) == 1
    assert len(example_corpus.column_mappings) == 1


def test_data_source_owner_reads_through(example_corpus: Corpus) -> None:
    # `owner` is a required data_sources field, read straight from the body.
    assert example_corpus.data_sources["pagila"].owner == "data-ops"


def test_column_mappings_null_target_expression(example_corpus: Corpus) -> None:
    # An intentional-drop mapping keyed (source_column_id, mapping_name) —
    # no target_system dimension.
    null_row = example_corpus.column_mappings[
        ("pagila.general.film.title", "default")
    ]
    assert null_row.target_expression is None
    assert null_row.notes is not None


def test_assemble_corpus_path_identity_propagates(example_corpus: Corpus) -> None:
    # data source / schema identity come from path, not file body; the id
    # carries no system segment.
    schema = example_corpus.schemas["pagila.general"]
    assert schema.data_source_id == "pagila"
    assert schema.schema_name == "general"


# ---------------------------------------------------------------------------
# Deployments — authored sparse, stored expanded
# ---------------------------------------------------------------------------


def test_deployments_bare_entry_expands_all_tables(example_corpus: Corpus) -> None:
    # A bare `- system: sandbox` deploys every documented pagila table under
    # its documented names; mart deploys its one table in warehouse.
    keys = set(example_corpus.deployment_tables.keys())
    assert keys == {
        ("pagila.general.film", "sandbox"),
        ("pagila.general.actor", "sandbox"),
        ("mart.analytics.fact", "warehouse"),
    }
    film = example_corpus.deployment_tables[("pagila.general.film", "sandbox")]
    assert film.data_source_id == "pagila"
    assert film.physical_database_name == "pagila"
    assert film.physical_schema_name == "general"
    assert film.physical_table_name == "film"
    # Pure-facts shape: expanded rows carry no freeform fields at all.
    assert not hasattr(film, "notes")
    assert not hasattr(film, "update_reason")


def test_deployments_exhaustive_schema_subset_and_rename(tmp_path: Path) -> None:
    # An exhaustive schemas/tables map subsets (only film) and renames every
    # physical level; actor is absent from the map and therefore not deployed.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n"
        "  database_name: pagila_phys\n"
        "  schemas:\n"
        "    general:\n"
        "      name: gen_phys\n"
        "      tables:\n"
        "        film: film_phys\n",
        encoding="utf-8",
    )
    corpus = _assemble_tree(root)
    pagila_deps = {
        k: v for k, v in corpus.deployment_tables.items() if v.data_source_id == "pagila"
    }
    assert set(pagila_deps) == {("pagila.general.film", "sandbox")}
    film = pagila_deps[("pagila.general.film", "sandbox")]
    assert film.physical_database_name == "pagila_phys"
    assert film.physical_schema_name == "gen_phys"
    assert film.physical_table_name == "film_phys"


def test_deployments_string_schema_value_renames_schema_only(tmp_path: Path) -> None:
    # A string schema value renames the physical schema; all its tables
    # deploy under their documented names.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  schemas:\n    general: gen_phys\n",
        encoding="utf-8",
    )
    corpus = _assemble_tree(root)
    pagila_deps = {
        k: v for k, v in corpus.deployment_tables.items() if v.data_source_id == "pagila"
    }
    assert set(pagila_deps) == {
        ("pagila.general.film", "sandbox"),
        ("pagila.general.actor", "sandbox"),
    }
    assert all(v.physical_schema_name == "gen_phys" for v in pagila_deps.values())


def test_deployments_null_physical_name_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  schemas:\n    general: ~\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("null physical name" in i for i in issues)


def test_deployments_duplicate_system_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n- system: sandbox\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("appears more than once" in i for i in issues)


def test_deployments_unknown_schema_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  schemas:\n    nope: phys\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("unknown schema 'nope'" in i for i in issues)


def test_deployments_unknown_table_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  schemas:\n    general:\n      tables:\n"
        "        nope: phys\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert any("unknown table 'nope'" in i for i in issues)


def test_deployments_unrecognized_entry_key_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  bogus: 1\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("Unrecognized key" in i and "bogus" in i for i in issues)


@pytest.mark.parametrize("stale_key", ["notes", "update_reason"])
def test_deployments_stale_freeform_key_rejected(
    stale_key: str, tmp_path: Path
) -> None:
    # Venue entries carry only residency facts: the dropped freeform keys
    # fail as unrecognized keys, pointing authors at the new rules.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        f"- system: sandbox\n  {stale_key}: stale value\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any(
        "Unrecognized key" in i and stale_key in i for i in issues
    )


def test_deployments_bad_system_charset_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: SANDBOX\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("must be lowercase" in i for i in issues)


def test_deployments_empty_schemas_map_rejected(tmp_path: Path) -> None:
    # An explicit but empty `schemas: {}` deploys nothing — an authoring
    # mistake, not a silent no-op (a bare entry deploys the whole set).
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  schemas: {}\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("empty `schemas` map" in i for i in issues)


def test_deployments_empty_tables_map_rejected(tmp_path: Path) -> None:
    # An explicit but empty `tables: {}` under a schema deploys nothing.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  schemas:\n    general:\n      tables: {}\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert any("empty `tables` map" in i for i in issues)


def test_deployments_string_schema_empty_physical_name_rejected(
    tmp_path: Path,
) -> None:
    # The string-shorthand branch rejects an empty-string physical name at
    # assembly (with entry context), matching the null and mapping-form
    # `name: ''` checks — not a later validation-stage issue.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  schemas:\n    general: ''\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("empty-string physical name" in i for i in issues)


def test_deployments_bare_entry_against_empty_inventory_rejected(
    tmp_path: Path,
) -> None:
    # A bare entry against a data source with a documented schema but zero
    # documented tables expands to zero rows — surfaced with entry context
    # rather than silently deploying nothing.
    root = _build_corpus_tree(tmp_path)
    # Remove mart's only documented table, leaving the analytics schema.
    (root / "sources" / "mart" / "analytics" / "tables.yaml").write_text(
        "[]\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any(
        "warehouse" in i and "expands to zero deployment rows" in i
        for i in issues
    )


def test_deployments_string_schema_against_empty_inventory_rejected(
    tmp_path: Path,
) -> None:
    # A string-form schema entry against a documented schema with zero
    # tables expands to zero rows — surfaced, not silently deployed.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "mart" / "analytics" / "tables.yaml").write_text(
        "[]\n", encoding="utf-8"
    )
    (root / "sources" / "mart" / "deployments.yaml").write_text(
        "- system: warehouse\n  schemas:\n    analytics: analytics_phys\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert any(
        "analytics" in i and "no documented tables" in i for i in issues
    )


def test_deployments_mapping_schema_no_tables_empty_inventory_rejected(
    tmp_path: Path,
) -> None:
    # A mapping-form schema entry (name only, no `tables:`) against a
    # documented schema with zero tables also expands to zero rows.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "mart" / "analytics" / "tables.yaml").write_text(
        "[]\n", encoding="utf-8"
    )
    (root / "sources" / "mart" / "deployments.yaml").write_text(
        "- system: warehouse\n  schemas:\n    analytics:\n      name: analytics_phys\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert any(
        "analytics" in i and "no documented tables" in i for i in issues
    )


def test_deployments_deploys_nowhere_empty_inventory_diagnostic(
    tmp_path: Path,
) -> None:
    # The whole-corpus "deploys nowhere" rule distinguishes its cause: a
    # data source whose venue entries expanded against an empty inventory
    # is told to document schemas/tables first (vs. add/fix the file).
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "mart" / "analytics" / "tables.yaml").write_text(
        "[]\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any(
        "mart" in i and "document schemas/tables first" in i for i in issues
    )


def test_deployments_deploys_nowhere_failed_entries_get_generic_diagnostic(
    tmp_path: Path,
) -> None:
    # Venue entries that produced zero rows for their own reasons (here an
    # unknown schema key) against a fully documented inventory must get
    # the generic "add or fix" advice — not the empty-inventory cause,
    # which would wrongly tell the author to document schemas/tables that
    # already exist. The per-entry issue names the actual defect.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "mart" / "deployments.yaml").write_text(
        "- system: warehouse\n  schemas:\n    nope: nope_phys\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert any("unknown schema 'nope'" in i for i in issues)
    assert any(
        "mart" in i and "add or fix its deployments.yaml" in i for i in issues
    )
    assert not any("document schemas/tables first" in i for i in issues)


def test_deployments_deploys_nowhere_missing_file_diagnostic(
    tmp_path: Path,
) -> None:
    # The other "deploys nowhere" cause: a venue-less deployments.yaml is
    # told to add or fix the file (mart's documented tables are intact).
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "mart" / "deployments.yaml").write_text(
        "[]\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any(
        "mart" in i and "add or fix its deployments.yaml" in i for i in issues
    )


def test_data_source_missing_owner_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "data_source.yaml").write_text(
        "description: Pagila.\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("owner" in i for i in issues)


def test_data_source_missing_description_rejected(tmp_path: Path) -> None:
    # `description` is required exactly like `owner`.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "data_source.yaml").write_text(
        "owner: data-ops\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("description" in i.lower() for i in issues)


def test_data_source_blank_description_rejected(tmp_path: Path) -> None:
    # A present-but-blank description is rejected like a blank owner.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "data_source.yaml").write_text(
        "owner: data-ops\ndescription: '   '\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("blank `description`" in i for i in issues)


def test_data_source_missing_owner_and_description_reports_both(
    tmp_path: Path,
) -> None:
    # A file missing both required fields reports both in one run — one
    # round-trip to fix, not two.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "data_source.yaml").write_text(
        "notes: n\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("`owner`" in i and "`description`" in i for i in issues)


def test_data_source_with_no_deployments_rejected(tmp_path: Path) -> None:
    # A data source that deploys nowhere is documentation of data that
    # exists nowhere — an aggregated issue. Emptying its deployments file
    # yields zero venues.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "mart" / "deployments.yaml").write_text(
        "[]\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any(
        "mart" in i and "no deployments" in i for i in issues
    )


def test_label_collides_with_system_name_rejected(tmp_path: Path) -> None:
    # Single-segment namespaces stay disjoint: a data-source label may not
    # equal a system name. Add a system named `pagila` (a data source).
    root = _build_corpus_tree(tmp_path)
    (root / "systems.yaml").write_text(
        (root / "systems.yaml").read_text(encoding="utf-8")
        + "- system: pagila\n  description: Collides with a data source.\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert any("collides with a systems name" in i for i in issues)


# ---------------------------------------------------------------------------
# Registry (data_catalog/systems.yaml)
# ---------------------------------------------------------------------------


def _systems_ident(path: Path) -> PathIdentity:
    return PathIdentity(
        file_type="systems", database_name=None, schema_name=None, path=path
    )


def test_systems_registry_assembles_rows(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [
            {"system": "warehouse", "description": "A"},
            {"system": "edw", "description": "B"},
        ],
        _systems_ident(tmp_path / "systems.yaml"),
    )
    assert set(corpus.systems) == {"warehouse", "edw"}


def test_systems_registry_duplicate_label_rejected(tmp_path: Path) -> None:
    issues = _issues_for(
        [
            {"system": "warehouse", "description": "A"},
            {"system": "warehouse", "description": "A"},
        ],
        _systems_ident(tmp_path / "systems.yaml"),
    )
    assert any("Duplicate systems PK" in i for i in issues)


def test_systems_registry_bad_label_charset_rejected(tmp_path: Path) -> None:
    issues = _issues_for(
        [{"system": "WAREHOUSE"}],
        _systems_ident(tmp_path / "systems.yaml"),
    )
    assert len(issues) == 1
    assert "must be lowercase" in issues[0]


def test_systems_registry_missing_system_key_rejected(tmp_path: Path) -> None:
    issues = _issues_for(
        [{"description": "no system"}],
        _systems_ident(tmp_path / "systems.yaml"),
    )
    assert len(issues) == 1
    assert "system" in issues[0]


def test_systems_registry_non_mapping_entry_rejected(tmp_path: Path) -> None:
    issues = _issues_for(
        ["bare string"], _systems_ident(tmp_path / "systems.yaml")
    )
    assert any("Expected a mapping per system" in i for i in issues)


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param({"system": "warehouse"}, id="missing_description"),
        pytest.param(
            {"system": "warehouse", "description": "   "},
            id="blank_description",
        ),
    ],
)
def test_systems_registry_missing_or_blank_description_rejected(
    entry: dict[str, Any], tmp_path: Path
) -> None:
    # Required documentation prose: an undescribed venue tells a
    # consumer nothing.
    issues = _issues_for([entry], _systems_ident(tmp_path / "systems.yaml"))
    assert len(issues) == 1
    assert "blank `description`" in issues[0]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("notes: n\n", id="missing_description"),
        pytest.param("description: '   '\n", id="blank_description"),
    ],
)
def test_schema_missing_or_blank_description_rejected(
    body: str, tmp_path: Path
) -> None:
    # schema.yaml requires non-blank description prose.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "general" / "schema.yaml").write_text(
        body, encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert any("blank `description`" in i for i in issues)


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"table_name": "bene"}, id="missing_description"),
        pytest.param(
            {"table_name": "bene", "description": ""},
            id="blank_description",
        ),
    ],
)
def test_tables_missing_or_blank_description_rejected(
    row: dict[str, Any], tmp_path: Path
) -> None:
    issues = _issues_for([row], _path_id("tables", tmp_path / "tables.yaml"))
    assert len(issues) == 1
    assert "blank `description`" in issues[0]


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({}, id="missing_description"),
        pytest.param({"description": "  "}, id="blank_description"),
    ],
)
def test_columns_missing_or_blank_description_rejected(
    extra: dict[str, Any], tmp_path: Path
) -> None:
    row = {
        "table_name": "bene",
        "column_name": "bene_id",
        "data_type": "TEXT",
        "is_nullable": True,
        **extra,
    }
    issues = _issues_for([row], _path_id("columns", tmp_path / "columns.yaml"))
    assert len(issues) == 1
    assert "blank `description`" in issues[0]


# ---------------------------------------------------------------------------
# Deployment entry structural / value branches
# ---------------------------------------------------------------------------


def _set_pagila_deployments(root: Path, text: str) -> None:
    """Overwrite pagila's deployments.yaml with `text`."""
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        text, encoding="utf-8"
    )


def test_deployments_doc_not_a_list_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    _set_pagila_deployments(root, "system: sandbox\n")  # mapping, not a list
    issues = _assemble_tree_issues(root)
    assert any("Expected a YAML list" in i for i in issues)


def test_deployments_non_mapping_entry_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    _set_pagila_deployments(root, "- bare string\n")
    issues = _assemble_tree_issues(root)
    assert any("Expected a mapping per deployment entry" in i for i in issues)


def test_deployments_missing_system_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    _set_pagila_deployments(root, "- database_name: no_system_here\n")
    issues = _assemble_tree_issues(root)
    assert any("Missing or non-string `system`" in i for i in issues)


def test_deployments_null_database_name_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    _set_pagila_deployments(root, "- system: sandbox\n  database_name: null\n")
    issues = _assemble_tree_issues(root)
    assert any("database_name` must be a non-empty string" in i for i in issues)


def test_deployments_schemas_not_a_mapping_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    _set_pagila_deployments(
        root, "- system: sandbox\n  schemas:\n    - general\n"
    )
    issues = _assemble_tree_issues(root)
    assert any("`schemas` must be a mapping" in i for i in issues)


def test_deployments_schema_value_wrong_type_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    _set_pagila_deployments(
        root, "- system: sandbox\n  schemas:\n    general: 123\n"
    )
    issues = _assemble_tree_issues(root)
    assert any("must be a physical-name string" in i for i in issues)


def test_deployments_schema_mapping_unknown_key_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    _set_pagila_deployments(
        root, "- system: sandbox\n  schemas:\n    general:\n      bogus: 1\n"
    )
    issues = _assemble_tree_issues(root)
    assert any("unrecognized key" in i for i in issues)


def test_deployments_schema_name_null_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    _set_pagila_deployments(
        root, "- system: sandbox\n  schemas:\n    general:\n      name: null\n"
    )
    issues = _assemble_tree_issues(root)
    assert any("`name` must be a" in i for i in issues)


def test_deployments_tables_not_a_mapping_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    _set_pagila_deployments(
        root,
        "- system: sandbox\n  schemas:\n    general:\n      tables:\n        - film\n",
    )
    issues = _assemble_tree_issues(root)
    assert any("`tables` must be a mapping" in i for i in issues)


def test_deployments_table_null_physical_name_rejected(tmp_path: Path) -> None:
    root = _build_corpus_tree(tmp_path)
    _set_pagila_deployments(
        root,
        "- system: sandbox\n  schemas:\n    general:\n      tables:\n        film: null\n",
    )
    issues = _assemble_tree_issues(root)
    assert any("null, non-string, or empty physical name" in i for i in issues)


def test_deployments_schema_name_only_renames_and_keeps_all_tables(
    tmp_path: Path,
) -> None:
    # A schema mapping with `name` but no `tables` renames the physical
    # schema and keeps every documented table under its documented name.
    root = _build_corpus_tree(tmp_path)
    _set_pagila_deployments(
        root, "- system: sandbox\n  schemas:\n    general:\n      name: gen_phys\n"
    )
    corpus = _assemble_tree(root)
    pagila_deps = {
        k: v for k, v in corpus.deployment_tables.items() if v.data_source_id == "pagila"
    }
    assert set(pagila_deps) == {
        ("pagila.general.film", "sandbox"),
        ("pagila.general.actor", "sandbox"),
    }
    assert all(v.physical_schema_name == "gen_phys" for v in pagila_deps.values())


# ---------------------------------------------------------------------------
# Document-shape guards
# ---------------------------------------------------------------------------


def _path_id(
    file_type: str,
    path: Path,
    *,
    database_name: str = "ocs",
    schema_name: str | None = "general",
) -> PathIdentity:
    """Minimal `PathIdentity` for negative-shape tests (venue-free)."""
    return PathIdentity(
        file_type=file_type,  # type: ignore[arg-type]
        database_name=database_name,
        schema_name=schema_name,
        path=path,
    )


def test_require_mapping_rejects_list(tmp_path: Path) -> None:
    issues = _issues_for(
        [1],
        _path_id(
            "data_source",
            tmp_path / "data_source.yaml",
            schema_name=None,
        ),
    )
    assert len(issues) == 1
    assert "Expected a YAML mapping" in issues[0]


def test_require_list_rejects_mapping(tmp_path: Path) -> None:
    issues = _issues_for({"a": 1}, _path_id("tables", tmp_path / "tables.yaml"))
    assert len(issues) == 1
    assert "Expected a YAML list" in issues[0]


def test_require_list_treats_none_as_empty(tmp_path: Path) -> None:
    corpus = _corpus_for(None, _path_id("tables", tmp_path / "tables.yaml"))
    assert corpus.tables == {}


# ---------------------------------------------------------------------------
# Per-row shape errors — one parametrized test per assembler family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("doc", "expected_fragment"),
    [
        pytest.param(
            [{"description": "no name"}], "table_name", id="missing_table_name"
        ),
        pytest.param(
            ["bare string"], "Expected a mapping per table", id="non_mapping_row"
        ),
        pytest.param(
            [{"table_name": "concept", "description": "d"}],
            "reserved",
            id="reserved_concept_table_name",
        ),
    ],
)
def test_tables_invalid_row_reported(
    doc: Any, expected_fragment: str, tmp_path: Path
) -> None:
    issues = _issues_for(doc, _path_id("tables", tmp_path / "tables.yaml"))
    assert len(issues) == 1
    assert re.search(expected_fragment, issues[0])


@pytest.mark.parametrize(
    ("doc", "expected_fragment"),
    [
        pytest.param(
            [{"table_name": "bene", "data_type": "TEXT", "is_nullable": True}],
            "column_name",
            id="missing_column_name",
        ),
        pytest.param(
            [
                {
                    "table_name": "bene",
                    "column_name": "bene_id",
                    "data_type": 1,
                    "is_nullable": True,
                }
            ],
            "data_type",
            id="non_string_data_type",
        ),
        pytest.param(
            ["bare"], "Expected a mapping per column", id="non_mapping_row"
        ),
        pytest.param(
            [
                {
                    "table_name": "bene",
                    "column_name": "bene_id",
                    "data_type": "TEXT",
                    "is_nullable": True,
                    "is_primaryy_key": True,  # typo
                }
            ],
            "Unrecognized key",
            id="unknown_key",
        ),
        pytest.param(
            [
                {
                    "table_name": "bene",
                    "column_name": "bene_id",
                    "data_type": "TEXT",
                    "is_nullable": "yes",
                }
            ],
            "is_nullable",
            id="non_bool_is_nullable",
        ),
        pytest.param(
            [
                {
                    "table_name": "bene",
                    "column_name": "bene_id",
                    "data_type": "TEXT",
                    "is_nullable": False,
                    "is_primary_key": "yes",
                }
            ],
            "is_primary_key",
            id="non_bool_is_primary_key",
        ),
    ],
)
def test_columns_invalid_row_reported(
    doc: Any, expected_fragment: str, tmp_path: Path
) -> None:
    issues = _issues_for(doc, _path_id("columns", tmp_path / "columns.yaml"))
    assert len(issues) == 1
    assert re.search(expected_fragment, issues[0])


# Minimal valid relationship fields shared by the negative cases below.
_REL_FIELDS: dict[str, str] = {
    "table_a_id": "ocs.general.bene",
    "table_b_id": "ocs.general.claim",
    "relationship_name": "default",
}
_REL_JOIN_CONDITION = "ocs.general.bene.x = ocs.general.claim.x"


@pytest.mark.parametrize(
    ("doc", "expected_fragment"),
    [
        pytest.param(
            [{**_REL_FIELDS}], "join_condition", id="missing_join_condition"
        ),
        pytest.param(
            ["bare"],
            "Expected a mapping per relationship",
            id="non_mapping_row",
        ),
        pytest.param(
            [{**_REL_FIELDS, "join_conditon": _REL_JOIN_CONDITION}],
            "Unrecognized key.*join_conditon",
            id="typo_join_condition_key",
        ),
        pytest.param(
            [
                {
                    **_REL_FIELDS,
                    "join_condition": _REL_JOIN_CONDITION,
                    "cardinality": 11,
                }
            ],
            "cardinality.*string or null",
            id="non_string_cardinality",
        ),
        pytest.param(
            [
                {
                    **_REL_FIELDS,
                    "join_condition": _REL_JOIN_CONDITION,
                    "validated": "yes",
                }
            ],
            "validated.*must be a boolean",
            id="non_bool_validated",
        ),
        # There is no `system` key any longer — a stale one is unrecognized.
        pytest.param(
            [
                {
                    **_REL_FIELDS,
                    "join_condition": _REL_JOIN_CONDITION,
                    "system": "warehouse",
                }
            ],
            "Unrecognized key.*system",
            id="stale_system_key",
        ),
        pytest.param(
            [
                {
                    **_REL_FIELDS,
                    "join_condition": _REL_JOIN_CONDITION,
                    "join_type": "INNER",
                }
            ],
            "Unrecognized key.*join_type",
            id="stale_join_type_key",
        ),
    ],
)
def test_table_relationships_invalid_row_reported(
    doc: Any, expected_fragment: str, tmp_path: Path
) -> None:
    issues = _issues_for(
        doc, _path_id("table_relationships", tmp_path / "table_relationships.yaml")
    )
    assert len(issues) == 1
    assert re.search(expected_fragment, issues[0])


# Source column shared by the mapping cases; the file's PathIdentity fixes
# the folder prefix to ocs.general (path-agreement).
_MAPPING_SOURCE = "ocs.general.bene.bene_id"


@pytest.mark.parametrize(
    ("doc", "expected_fragment"),
    [
        pytest.param(
            [{"source_column_id": _MAPPING_SOURCE, "mapping_name": "default"}],
            "target_expression",
            id="missing_target_expression_key",
        ),
        pytest.param(
            [
                {
                    "source_column_id": _MAPPING_SOURCE,
                    "mapping_name": "default",
                    "target_expression": 123,
                }
            ],
            "must be a string or null",
            id="non_string_target_expression",
        ),
        pytest.param(
            [{"target_expression": None}],
            "source_column_id",
            id="missing_source_column_id",
        ),
        pytest.param(
            ["bare"],
            "Expected a mapping per column_mapping",
            id="non_mapping_row",
        ),
        pytest.param(
            [
                {
                    "source_column_id": _MAPPING_SOURCE,
                    "target_expression": None,
                    "notes": "x",
                }
            ],
            "mapping_name",
            id="missing_mapping_name",
        ),
        pytest.param(
            [
                {
                    "source_column_id": _MAPPING_SOURCE,
                    "mapping_name": "bad name",
                    "target_expression": None,
                    "notes": "x",
                }
            ],
            "mapping_name",
            id="illegal_mapping_name",
        ),
        # Path-agreement: source_column_id's {db}.{schema} prefix must
        # equal the file's folder path (ocs.general here); other.general
        # does not.
        pytest.param(
            [
                {
                    "source_column_id": "other.general.bene.bene_id",
                    "mapping_name": "default",
                    "target_expression": None,
                    "notes": "x",
                }
            ],
            "folder path",
            id="source_column_id_outside_folder",
        ),
        pytest.param(
            [
                {
                    "source_column_id": _MAPPING_SOURCE,
                    "mapping_name": "default",
                    "target_expression": None,
                    "notes": "x",
                    "bogus": 1,
                }
            ],
            "Unrecognized key",
            id="unknown_key",
        ),
        # There is no `target_system` key any longer.
        pytest.param(
            [
                {
                    "source_column_id": _MAPPING_SOURCE,
                    "mapping_name": "default",
                    "target_expression": None,
                    "notes": "x",
                    "target_system": "edw",
                }
            ],
            "Unrecognized key.*target_system",
            id="stale_target_system_key",
        ),
        pytest.param(
            [
                {
                    "source_column_id": _MAPPING_SOURCE,
                    "mapping_name": "default",
                    "target_expression": None,
                    "notes": "x",
                    "validated": 1,
                }
            ],
            "validated.*must be a boolean",
            id="non_bool_validated",
        ),
    ],
)
def test_column_mappings_invalid_row_reported(
    doc: Any, expected_fragment: str, tmp_path: Path
) -> None:
    issues = _issues_for(doc, _path_id("column_mappings", tmp_path / "edw.yaml"))
    assert len(issues) == 1
    assert re.search(expected_fragment, issues[0])


# ---------------------------------------------------------------------------
# Duplicate mapping identity — (source_column_id, mapping_name)
# ---------------------------------------------------------------------------


def test_column_mappings_duplicate_identity_reported(tmp_path: Path) -> None:
    issues = _issues_for(
        [
            {
                "source_column_id": _MAPPING_SOURCE,
                "mapping_name": "default",
                "target_expression": None,
                "notes": "a",
            },
            {
                "source_column_id": _MAPPING_SOURCE,
                "mapping_name": "default",
                "target_expression": None,
                "notes": "b",
            },
        ],
        _path_id("column_mappings", tmp_path / "edw.yaml"),
    )
    assert any("Duplicate column_mappings PK" in i for i in issues)


# ---------------------------------------------------------------------------
# table_a_id anchors the file's schema
# ---------------------------------------------------------------------------


def test_table_relationships_table_a_outside_schema_reported(
    tmp_path: Path,
) -> None:
    issues = _issues_for(
        [
            {
                "table_a_id": "other.general.bene",  # not this schema
                "table_b_id": "ocs.general.claim",
                "relationship_name": "default",
                "join_condition": "other.general.bene.x = ocs.general.claim.x",
            }
        ],
        _path_id("table_relationships", tmp_path / "table_relationships.yaml"),
    )
    assert len(issues) == 1
    assert "is not in this file's schema" in issues[0]


def test_table_relationships_table_b_other_source_passes(
    tmp_path: Path,
) -> None:
    # table_a_id anchors the file (ocs.general); table_b_id may reach into
    # another schema/data source (venue co-deployment is a validation rule).
    corpus = _corpus_for(
        [
            {
                "table_a_id": "ocs.general.bene",
                "table_b_id": "other.general.claim",
                "relationship_name": "default",
                "join_condition": "ocs.general.bene.x = other.general.claim.x",
            }
        ],
        _path_id("table_relationships", tmp_path / "table_relationships.yaml"),
    )
    (row,) = corpus.table_relationships.values()
    assert row.table_b_id == "other.general.claim"


def test_table_relationships_cardinality_reads_through(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [
            {
                "table_a_id": "ocs.general.bene",
                "table_b_id": "ocs.general.claim",
                "relationship_name": "default",
                "join_condition": "ocs.general.bene.x = ocs.general.claim.x",
                "cardinality": "many_to_one",
            }
        ],
        _path_id("table_relationships", tmp_path / "table_relationships.yaml"),
    )
    (row,) = corpus.table_relationships.values()
    assert row.cardinality == "many_to_one"


def test_table_relationships_cardinality_absent_is_none(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [
            {
                "table_a_id": "ocs.general.bene",
                "table_b_id": "ocs.general.claim",
                "relationship_name": "default",
                "join_condition": "ocs.general.bene.x = ocs.general.claim.x",
            }
        ],
        _path_id("table_relationships", tmp_path / "table_relationships.yaml"),
    )
    (row,) = corpus.table_relationships.values()
    assert row.cardinality is None


# ---------------------------------------------------------------------------
# is_primary_key grain flag
# ---------------------------------------------------------------------------


def test_columns_is_primary_key_true(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [
            {
                "table_name": "bene",
                "column_name": "bene_id",
                "data_type": "TEXT",
                "is_nullable": False,
                "is_primary_key": True,
                "description": "d",
            }
        ],
        _path_id("columns", tmp_path / "columns.yaml"),
    )
    (row,) = corpus.columns.values()
    assert row.is_primary_key is True


def test_columns_is_primary_key_defaults_false(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [
            {
                "table_name": "bene",
                "column_name": "descr",
                "data_type": "TEXT",
                "is_nullable": True,
                "description": "d",
            }
        ],
        _path_id("columns", tmp_path / "columns.yaml"),
    )
    (row,) = corpus.columns.values()
    assert row.is_primary_key is False


# ---------------------------------------------------------------------------
# ref_table domain pointer
# ---------------------------------------------------------------------------

# Minimal valid column fields shared by the ref_table cases below.
_COL_FIELDS: dict[str, Any] = {
    "table_name": "bene",
    "column_name": "clm_type_cd",
    "data_type": "TEXT",
    "is_nullable": False,
    "description": "d",
}


def test_columns_ref_table_string_accepted(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [{**_COL_FIELDS, "ref_table": "ref.codes.clm_type_cd"}],
        _path_id("columns", tmp_path / "columns.yaml"),
    )
    (row,) = corpus.columns.values()
    assert row.ref_table_id == "ref.codes.clm_type_cd"


def test_columns_ref_table_explicit_null_accepted(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [{**_COL_FIELDS, "ref_table": None}],
        _path_id("columns", tmp_path / "columns.yaml"),
    )
    (row,) = corpus.columns.values()
    assert row.ref_table_id is None


def test_columns_ref_table_absent_defaults_null(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [dict(_COL_FIELDS)],
        _path_id("columns", tmp_path / "columns.yaml"),
    )
    (row,) = corpus.columns.values()
    assert row.ref_table_id is None


@pytest.mark.parametrize(
    ("ref_table", "expected_fragment"),
    [
        pytest.param(11, "`ref_table` must be a string or null", id="non_string"),
        pytest.param(True, "`ref_table` must be a string or null", id="bool"),
        pytest.param("", "3-segment dotted table id", id="blank"),
        pytest.param("   ", "3-segment dotted table id", id="whitespace_only"),
        pytest.param("ref.codes", "3-segment dotted table id", id="two_segments"),
        pytest.param(
            "ref.codes.clm_type_cd.code",
            "3-segment dotted table id",
            id="four_segments",
        ),
        pytest.param("REF.codes.clm_type_cd", "lowercase", id="uppercase_segment"),
        pytest.param("ref..clm_type_cd", "Empty ref_table segment", id="empty_segment"),
    ],
)
def test_columns_ref_table_bad_value_rejected(
    ref_table: Any, expected_fragment: str, tmp_path: Path
) -> None:
    issues = _issues_for(
        [{**_COL_FIELDS, "ref_table": ref_table}],
        _path_id("columns", tmp_path / "columns.yaml"),
    )
    assert len(issues) == 1
    assert expected_fragment in issues[0]


# ---------------------------------------------------------------------------
# concepts — path-derived id (file prefix + `.` + authored body `name`)
# ---------------------------------------------------------------------------


def _concept_path_id(
    path: Path, *, schema_name: str | None = None
) -> PathIdentity:
    """A concepts `PathIdentity` anchored at the data-source or schema level."""
    return PathIdentity(
        file_type="concepts",
        database_name="sandbox_ocs",
        schema_name=schema_name,
        path=path,
    )


def test_assemble_concepts_data_source_level_id(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [
            {
                "name": "concept.claim",
                "label": "Claim",
                "definition": "A claim.",
                "notes": None,
                "update_reason": None,
            }
        ],
        _concept_path_id(tmp_path / "concepts.yaml"),
    )
    assert set(corpus.concepts) == {"sandbox_ocs.concept.claim"}
    row = corpus.concepts["sandbox_ocs.concept.claim"]
    assert row.label == "Claim"
    assert row.definition == "A claim."


def test_assemble_concepts_schema_level_id(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [{"name": "concept.final_action", "definition": "d"}],
        _concept_path_id(
            tmp_path / "concepts.yaml", schema_name="claims_vw"
        ),
    )
    assert set(corpus.concepts) == {
        "sandbox_ocs.claims_vw.concept.final_action"
    }


@pytest.mark.parametrize(
    ("doc", "expected_fragment"),
    [
        pytest.param(
            [{"name": "bad segment", "definition": "d"}],
            "name",
            id="illegal_name",
        ),
        pytest.param(
            # Anchor segments are legal only in a schema-scoped file;
            # this helper's default identity is data-source-scoped.
            [{"name": "claim_type.concept.original", "definition": "d"}],
            "data-source-scoped",
            id="anchored_name_data_source_scope",
        ),
        pytest.param(
            # The reserved segment alone has no leaf — it sits at the
            # leaf position instead of second-to-last.
            [{"name": "concept", "definition": "d"}],
            "reserved",
            id="reserved_segment_only_name",
        ),
        pytest.param([{"label": "no name"}], "name", id="missing_name"),
        pytest.param(
            [{"name": "concept.claim", "concept_id": "claim"}],
            "Unrecognized key.*concept_id",
            id="concept_id_body_key",
        ),
        pytest.param(
            [
                {
                    "name": "concept.claim",
                    "definition": "d",
                    "related_object_ids": "sandbox_ocs.general.clm",
                }
            ],
            "related_object_ids.*must be a list",
            id="related_object_ids_non_list",
        ),
        pytest.param(
            [
                {
                    "name": "concept.claim",
                    "definition": "d",
                    "related_object_ids": ["sandbox_ocs.general.clm", 5],
                }
            ],
            "entries must be strings",
            id="related_object_ids_non_string_entry",
        ),
        pytest.param(
            [
                {
                    "name": "concept.claim",
                    "definition": "d",
                    "related_object_ids": [
                        "sandbox_ocs.general.clm",
                        "sandbox_ocs.general.clm",
                    ],
                }
            ],
            "duplicate `related_object_ids`",
            id="related_object_ids_duplicate",
        ),
        pytest.param(
            [
                {
                    "name": "concept.claim",
                    "definition": "d",
                    "related_object_ids": ["sandbox_ocs.concept.claim"],
                }
            ],
            "self-reference",
            id="related_object_ids_self_reference",
        ),
        pytest.param(
            ["bare"], "Expected a mapping per concept", id="non_mapping_row"
        ),
    ],
)
def test_assemble_concepts_invalid_row_reported(
    doc: Any, expected_fragment: str, tmp_path: Path
) -> None:
    issues = _issues_for(doc, _concept_path_id(tmp_path / "concepts.yaml"))
    assert len(issues) == 1
    assert re.search(expected_fragment, issues[0])


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"name": "concept.claim"}, id="missing_definition"),
        pytest.param(
            {"name": "concept.claim", "definition": "   "},
            id="blank_definition",
        ),
    ],
)
def test_assemble_concepts_missing_or_blank_definition_rejected(
    row: dict[str, Any], tmp_path: Path
) -> None:
    # Required prose: a definition-less concept is a glossary entry with
    # nothing to look up.
    issues = _issues_for([row], _concept_path_id(tmp_path / "concepts.yaml"))
    assert len(issues) == 1
    assert "blank `definition`" in issues[0]


def test_assemble_concepts_related_object_ids_reads_through(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [
            {
                "name": "concept.claim",
                "definition": "d",
                "related_object_ids": [
                    "sandbox_ocs.general.clm",
                    "sandbox_ocs.general.clm.claim_no",
                ],
            }
        ],
        _concept_path_id(tmp_path / "concepts.yaml"),
    )
    (row,) = corpus.concepts.values()
    assert row.related_object_ids == (
        "sandbox_ocs.general.clm",
        "sandbox_ocs.general.clm.claim_no",
    )


def test_assemble_concepts_related_object_ids_absent_is_empty(tmp_path: Path) -> None:
    corpus = _corpus_for(
        [
            {"name": "concept.claim", "definition": "d"},
            {
                "name": "concept.bene",
                "definition": "d",
                "related_object_ids": None,
            },
        ],
        _concept_path_id(tmp_path / "concepts.yaml"),
    )
    assert corpus.concepts["sandbox_ocs.concept.claim"].related_object_ids == ()
    assert corpus.concepts["sandbox_ocs.concept.bene"].related_object_ids == ()


def test_assemble_concepts_authored_name_composes_pre_change_id_byte_for_byte(
    tmp_path: Path,
) -> None:
    # Backward compatibility: `concept.{leaf}` composes exactly the id
    # the injecting loader composed from a bare leaf, at both file
    # scopes — the change is authoring-format only.
    row = [{"name": "concept.claim", "definition": "d"}]
    ds_corpus = _corpus_for(row, _concept_path_id(tmp_path / "ds.yaml"))
    sch_corpus = _corpus_for(
        row, _concept_path_id(tmp_path / "sch.yaml", schema_name="general")
    )
    assert set(ds_corpus.concepts) == {"sandbox_ocs.concept.claim"}
    assert set(sch_corpus.concepts) == {"sandbox_ocs.general.concept.claim"}


@pytest.mark.parametrize(
    ("database_name", "schema_name", "name", "expected_id"),
    [
        pytest.param(
            "edwc_prd",
            None,
            "concept.edw_naming_abbreviations",
            "edwc_prd.concept.edw_naming_abbreviations",
            id="live_source_scope",
        ),
        pytest.param(
            "edwc_prd",
            "claims_vw_prd",
            "v_clm.clm_type_cd.concept.claim_type_code",
            "edwc_prd.claims_vw_prd.v_clm.clm_type_cd"
            ".concept.claim_type_code",
            id="live_column_anchor",
        ),
        pytest.param(
            "edwc_prd",
            "claims_vw_prd",
            "concept.four_part_claim_key",
            "edwc_prd.claims_vw_prd.concept.four_part_claim_key",
            id="live_schema_scope",
        ),
    ],
)
def test_assemble_concepts_live_names_compose_unchanged_ids(
    database_name: str,
    schema_name: str | None,
    name: str,
    expected_id: str,
    tmp_path: Path,
) -> None:
    # Representative migrated names from the shipped corpus compose ids
    # byte-identical to the pre-change loader's output.
    ident = PathIdentity(
        file_type="concepts",
        database_name=database_name,
        schema_name=schema_name,
        path=tmp_path / "concepts.yaml",
    )
    corpus = _corpus_for([{"name": name, "definition": "d"}], ident)
    assert set(corpus.concepts) == {expected_id}


def test_assemble_concepts_table_anchor_name(tmp_path: Path) -> None:
    # `{table}.concept.{leaf}` in a schema-scoped file: the anchor
    # segment deepens the anchor to a table of that schema.
    corpus = _corpus_for(
        [{"name": "clm.concept.claim_type", "definition": "d"}],
        _concept_path_id(tmp_path / "concepts.yaml", schema_name="general"),
    )
    assert set(corpus.concepts) == {
        "sandbox_ocs.general.clm.concept.claim_type"
    }


def test_assemble_concepts_column_anchor_name(tmp_path: Path) -> None:
    # `{table}.{column}.concept.{leaf}` in a schema-scoped file: a
    # column anchor.
    corpus = _corpus_for(
        [{"name": "clm.clm_type_cd.concept.claim_type", "definition": "d"}],
        _concept_path_id(tmp_path / "concepts.yaml", schema_name="general"),
    )
    assert set(corpus.concepts) == {
        "sandbox_ocs.general.clm.clm_type_cd.concept.claim_type"
    }


@pytest.mark.parametrize(
    ("name", "expected_fragment"),
    [
        pytest.param(
            # A pre-change bare leaf: the author must now write the
            # reserved segment, and the message states the required form.
            "claim",
            "no reserved 'concept' segment",
            id="missing_concept_segment",
        ),
        pytest.param(
            "claim",
            "[{table}[.{column}].]concept.{leaf}",
            id="missing_concept_segment_states_form",
        ),
        pytest.param(
            ".clm.concept.claim", "leading dot", id="leading_dot"
        ),
        pytest.param(
            "clm.concept.claim.", "trailing dot", id="trailing_dot"
        ),
        pytest.param(
            "clm..concept.claim", "doubled dot", id="doubled_dot"
        ),
        pytest.param(
            "clm.concept", "the leaf position", id="concept_as_leaf"
        ),
        pytest.param(
            # First of three segments: an anchor position, named 1-based.
            "concept.clm.claim",
            "at segment 1 of 3 (an anchor position)",
            id="concept_as_anchor_segment",
        ),
        pytest.param(
            "concept.concept",
            "more than once (segments 1, 2 of 2)",
            id="doubled_concept_segment",
        ),
        pytest.param(
            "CLM.concept.claim", "lowercase", id="uppercase_segment"
        ),
        pytest.param(
            "cl m.concept.claim",
            "offending character",
            id="illegal_charset_segment",
        ),
        pytest.param(
            ("x" * 256) + ".concept.claim",
            "ltree label limit",
            id="over_long_segment",
        ),
        pytest.param(
            # 2 schema-prefix labels + 3 anchor segments = a 5-label
            # anchor; nothing below a column (4 labels) exists.
            "clm.clm_type_cd.extra.concept.claim",
            "anchor of 5 labels",
            id="over_deep_name",
        ),
    ],
)
def test_assemble_concepts_bad_relative_name_rejected(
    name: str, expected_fragment: str, tmp_path: Path
) -> None:
    # Each name-rule failure raises its specific message and quotes the
    # whole `name` (segment failures also name the failing segment).
    issues = _issues_for(
        [{"name": name, "definition": "d"}],
        _concept_path_id(tmp_path / "concepts.yaml", schema_name="general"),
    )
    assert len(issues) == 1
    assert expected_fragment in issues[0]
    assert repr(name) in issues[0]


def test_assemble_concepts_anchored_name_scope_rule(tmp_path: Path) -> None:
    # The same anchored name is rejected in a data-source-scoped file
    # (the message directs the author to the schema's folder) and
    # accepted in the schema-scoped file.
    row = [{"name": "clm.concept.claim_type", "definition": "d"}]
    issues = _issues_for(row, _concept_path_id(tmp_path / "ds.yaml"))
    assert len(issues) == 1
    assert "data-source-scoped" in issues[0]
    assert "schema's folder" in issues[0]
    corpus = _corpus_for(
        row, _concept_path_id(tmp_path / "sch.yaml", schema_name="general")
    )
    assert set(corpus.concepts) == {
        "sandbox_ocs.general.clm.concept.claim_type"
    }


def test_columns_reserved_concept_column_name_rejected(
    tmp_path: Path,
) -> None:
    # A column literally named `concept` would shadow the reserved
    # segment of a table-anchored concept_id — matching the table guard.
    issues = _issues_for(
        [{**_COL_FIELDS, "column_name": "concept"}],
        _path_id("columns", tmp_path / "columns.yaml"),
    )
    assert len(issues) == 1
    assert "reserved" in issues[0]
    assert "column_name" in issues[0]


def test_assemble_corpus_rejects_same_concept_id_across_shards(
    tmp_path: Path,
) -> None:
    # Two shards of one concepts/ folder composing the same id are a
    # duplicate PK; the issue names both files.
    data_root = _build_corpus_tree(tmp_path)
    shard_dir = data_root / "sources" / "pagila" / "general" / "concepts"
    shard_dir.mkdir()
    (shard_dir / "film_a.yaml").write_text(
        "- name: film.concept.usage\n  definition: a\n", encoding="utf-8"
    )
    (shard_dir / "film_b.yaml").write_text(
        "- name: film.concept.usage\n  definition: b\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(data_root)
    assert len(issues) == 1
    assert "Duplicate concepts PK" in issues[0]
    assert "'pagila.general.film.concept.usage'" in issues[0]
    assert str(shard_dir / "film_a.yaml") in issues[0]
    assert str(shard_dir / "film_b.yaml") in issues[0]


def test_assemble_corpus_dispatches_concepts(tmp_path: Path) -> None:
    data_root = _build_corpus_tree(tmp_path)
    (data_root / "sources" / "pagila" / "concepts.yaml").write_text(
        "- name: concept.rental\n  label: Rental\n  definition: A rental.\n"
        "  notes: null\n  update_reason: null\n",
        encoding="utf-8",
    )
    corpus = _assemble_tree(data_root)
    assert "pagila.concept.rental" in corpus.concepts
    assert corpus.concepts["pagila.concept.rental"].label == "Rental"


def test_assemble_corpus_rejects_duplicate_concept_id(tmp_path: Path) -> None:
    data_root = _build_corpus_tree(tmp_path)
    (data_root / "sources" / "pagila" / "concepts.yaml").write_text(
        "- name: concept.claim\n  definition: a\n"
        "- name: concept.claim\n  definition: b\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(data_root)
    assert any("Duplicate concepts PK" in i for i in issues)


def test_assemble_corpus_rejects_duplicate_table_id(tmp_path: Path) -> None:
    data_root = _build_corpus_tree(tmp_path)
    (
        data_root / "sources" / "pagila" / "general" / "tables.yaml"
    ).write_text(
        "- table_name: film\n  description: a\n"
        "- table_name: film\n  description: b\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(data_root)
    assert any("Duplicate tables PK" in i for i in issues)


# ---------------------------------------------------------------------------
# Aggregation — every discovery/assembly issue in one AssemblyError
# ---------------------------------------------------------------------------


def test_assemble_corpus_reports_every_bad_row_in_one_file(tmp_path: Path) -> None:
    doc = [
        {"table_name": "bene", "description": "good"},
        {"description": "missing table_name"},
        {"table_name": "concept", "description": "reserved word"},
        {"table_name": "bene", "description": "duplicate of the good row"},
    ]
    issues = _issues_for(doc, _path_id("tables", tmp_path / "tables.yaml"))
    assert len(issues) == 3
    assert "table_name" in issues[0]
    assert "reserved" in issues[1]
    assert "Duplicate tables PK" in issues[2]


def test_assemble_corpus_reports_bad_rows_across_files(tmp_path: Path) -> None:
    tables_ident = _path_id("tables", tmp_path / "tables.yaml")
    columns_ident = _path_id("columns", tmp_path / "columns.yaml")
    _write_doc(tables_ident, [{"description": "missing table_name"}])
    _write_doc(
        columns_ident,
        [
            {
                "table_name": "bene",
                "column_name": "bene_id",
                "data_type": 1,
                "is_nullable": True,
            }
        ],
    )
    with pytest.raises(corpus_assembly.AssemblyError) as excinfo:
        corpus_assembly.assemble_corpus([tables_ident, columns_ident])
    issues = excinfo.value.issues
    assert len(issues) == 2
    # Both files' issues aggregate; assembly walks files in sorted path
    # order (deterministic across machines), so assert order-independently.
    assert any("table_name" in i for i in issues)
    assert any("data_type" in i for i in issues)


def test_assemble_corpus_uppercase_body_names_reported(tmp_path: Path) -> None:
    # Every body-derived identifier kind flows through
    # validate_identifier_segment: an uppercase table_name, column_name,
    # relationship_name, mapping_name, and concept name each record one
    # issue carrying the lowercase-hint message.
    tables_ident = _path_id("tables", tmp_path / "tables.yaml")
    columns_ident = _path_id("columns", tmp_path / "columns.yaml")
    rel_ident = _path_id(
        "table_relationships", tmp_path / "table_relationships.yaml"
    )
    map_ident = _path_id("column_mappings", tmp_path / "edw.yaml")
    concepts_ident = _concept_path_id(tmp_path / "concepts.yaml")
    _write_doc(
        tables_ident,
        [
            {"table_name": "BENE", "description": "uppercase"},
            {"table_name": "bene", "description": "lowercase sibling"},
        ],
    )
    _write_doc(
        columns_ident,
        [
            {
                "table_name": "bene",
                "column_name": "BENE_ID",
                "data_type": "TEXT",
                "is_nullable": True,
                "description": "uppercase",
            },
            {
                "table_name": "bene",
                "column_name": "bene_id",
                "data_type": "TEXT",
                "is_nullable": True,
                "description": "lowercase sibling",
            },
        ],
    )
    _write_doc(
        rel_ident,
        [
            {
                "table_a_id": "ocs.general.bene",
                "table_b_id": "ocs.general.claim",
                "relationship_name": "DEFAULT",
                "join_condition": "ocs.general.bene.x = ocs.general.claim.x",
            },
            {
                "table_a_id": "ocs.general.bene",
                "table_b_id": "ocs.general.claim",
                "relationship_name": "default",
                "join_condition": "ocs.general.bene.x = ocs.general.claim.x",
            },
        ],
    )
    _write_doc(
        map_ident,
        [
            {
                "source_column_id": "ocs.general.bene.bene_id",
                "mapping_name": "LEGACY",
                "target_expression": None,
                "notes": "x",
            },
            {
                "source_column_id": "ocs.general.bene.bene_id",
                "mapping_name": "default",
                "target_expression": None,
                "notes": "x",
            },
        ],
    )
    _write_doc(
        concepts_ident,
        [
            {"name": "concept.CLAIM", "definition": "d"},
            {"name": "concept.claim", "definition": "d"},
        ],
    )
    idents = [tables_ident, columns_ident, rel_ident, map_ident, concepts_ident]
    with pytest.raises(corpus_assembly.AssemblyError) as excinfo:
        corpus_assembly.assemble_corpus(idents)
    issues = excinfo.value.issues
    assert len(issues) == 5
    assert all("must be lowercase" in i for i in issues)
    joined = "\n".join(issues)
    for suggestion in ("'bene'", "'bene_id'", "'default'", "'legacy'", "'claim'"):
        assert suggestion in joined


def test_assemble_corpus_parse_broken_file_is_one_issue(tmp_path: Path) -> None:
    broken_ident = _path_id("tables", tmp_path / "tables.yaml")
    broken_ident.path.write_text("a: : :\n  - bad\n", encoding="utf-8")
    columns_ident = _path_id("columns", tmp_path / "columns.yaml")
    _write_doc(columns_ident, ["bare"])
    with pytest.raises(corpus_assembly.AssemblyError) as excinfo:
        corpus_assembly.assemble_corpus([broken_ident, columns_ident])
    issues = excinfo.value.issues
    assert len(issues) == 2
    # Order-independent: assembly walks files in sorted path order.
    assert any("Failed to read or parse YAML" in i for i in issues)
    assert any("Expected a mapping per column" in i for i in issues)


def test_assemble_corpus_issue_order_independent_of_input_order(
    tmp_path: Path,
) -> None:
    # Assembly sorts files by path, so the aggregated issue order is
    # deterministic regardless of the order the caller passes files in.
    tables_ident = _path_id("tables", tmp_path / "tables.yaml")
    columns_ident = _path_id("columns", tmp_path / "columns.yaml")
    _write_doc(tables_ident, [{"description": "missing table_name"}])
    _write_doc(columns_ident, ["bare"])

    def _run(order: list[PathIdentity]) -> list[str]:
        with pytest.raises(corpus_assembly.AssemblyError) as excinfo:
            corpus_assembly.assemble_corpus(order)
        return excinfo.value.issues

    forward = _run([tables_ident, columns_ident])
    reversed_order = _run([columns_ident, tables_ident])
    assert forward == reversed_order
    # columns.yaml sorts before tables.yaml, so its issue leads.
    assert "Expected a mapping per column" in forward[0]


def test_assemble_corpus_wrong_document_shape_is_one_issue(tmp_path: Path) -> None:
    issues = _issues_for(
        {"table_name": "bene"}, _path_id("tables", tmp_path / "tables.yaml")
    )
    assert len(issues) == 1
    assert "Expected a YAML list" in issues[0]


def test_assemble_corpus_duplicate_pk_first_wins_names_both_files(
    tmp_path: Path,
) -> None:
    first_ident = _path_id("tables", tmp_path / "first_tables.yaml")
    second_ident = _path_id("tables", tmp_path / "second_tables.yaml")
    _write_doc(first_ident, [{"table_name": "bene", "description": "first"}])
    _write_doc(second_ident, [{"table_name": "bene", "description": "second"}])
    with pytest.raises(corpus_assembly.AssemblyError) as excinfo:
        corpus_assembly.assemble_corpus([first_ident, second_ident])
    issues = excinfo.value.issues
    assert len(issues) == 1
    assert "Duplicate tables PK" in issues[0]
    assert "ocs.general.bene" in issues[0]
    assert str(second_ident.path) in issues[0]
    assert f"first occurrence in {first_ident.path} kept" in issues[0]


# ---------------------------------------------------------------------------
# Freeform-field typing — string-or-null on every row type
# ---------------------------------------------------------------------------


# One (file_type, base row, freeform key) case per row-type/key pair. The
# bad values parametrized below are what unquoted YAML mints: an int, a
# bool, and a date (`update_reason: 2024-01-01` parses as datetime.date).
_FREEFORM_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("tables", {"table_name": "bene", "description": "d"}, "notes"),
    ("tables", {"table_name": "bene", "description": "d"}, "update_reason"),
    (
        "columns",
        {
            "table_name": "bene",
            "column_name": "bene_id",
            "data_type": "TEXT",
            "is_nullable": True,
            "description": "d",
        },
        "notes",
    ),
    (
        "table_relationships",
        {**_REL_FIELDS, "join_condition": _REL_JOIN_CONDITION},
        "use_when",
    ),
    (
        "table_relationships",
        {**_REL_FIELDS, "join_condition": _REL_JOIN_CONDITION},
        "update_reason",
    ),
    (
        "column_mappings",
        {
            "source_column_id": _MAPPING_SOURCE,
            "mapping_name": "default",
            "target_expression": None,
            "notes": "x",
        },
        "use_when",
    ),
    (
        "column_mappings",
        {
            "source_column_id": _MAPPING_SOURCE,
            "mapping_name": "default",
            "target_expression": None,
            "notes": "x",
        },
        "update_reason",
    ),
]


@pytest.mark.parametrize(
    "bad_value",
    [
        pytest.param(7, id="int"),
        pytest.param(True, id="bool"),
        pytest.param(datetime.date(2024, 1, 1), id="yaml_date"),
    ],
)
@pytest.mark.parametrize(
    ("file_type", "base_row", "key"),
    [pytest.param(t, r, k, id=f"{t}.{k}") for t, r, k in _FREEFORM_CASES],
)
def test_freeform_field_non_string_rejected(
    file_type: str,
    base_row: dict[str, Any],
    key: str,
    bad_value: Any,
    tmp_path: Path,
) -> None:
    # A mistyped freeform value used to pass all validation and fail only
    # inside the post-merge write transaction; now it is a wave-1 issue.
    row = {**base_row, key: bad_value}
    filename = "edw.yaml" if file_type == "column_mappings" else f"{file_type}.yaml"
    issues = _issues_for([row], _path_id(file_type, tmp_path / filename))
    assert len(issues) == 1
    assert f"`{key}` must be a string or null" in issues[0]
    assert type(bad_value).__name__ in issues[0]


@pytest.mark.parametrize(
    "bad_value",
    [pytest.param(7, id="int"), pytest.param(datetime.date(2024, 1, 1), id="yaml_date")],
)
@pytest.mark.parametrize("key", ["notes", "update_reason"])
def test_freeform_field_non_string_rejected_systems(
    key: str, bad_value: Any, tmp_path: Path
) -> None:
    issues = _issues_for(
        [{"system": "warehouse", "description": "A", key: bad_value}],
        _systems_ident(tmp_path / "systems.yaml"),
    )
    assert len(issues) == 1
    assert f"`{key}` must be a string or null" in issues[0]


@pytest.mark.parametrize(
    ("file_name", "body"),
    [
        pytest.param(
            "data_source.yaml",
            {"owner": "data-ops", "description": "d", "notes": 5},
            id="data_source_notes",
        ),
        pytest.param(
            "schema.yaml",
            {"description": "d", "update_reason": datetime.date(2024, 1, 1)},
            id="schema_update_reason",
        ),
    ],
)
def test_freeform_field_non_string_rejected_single_row_files(
    file_name: str, body: dict[str, Any], tmp_path: Path
) -> None:
    file_type = file_name.removesuffix(".yaml")
    schema_name = "general" if file_type == "schema" else None
    issues = _issues_for(
        body,
        _path_id(file_type, tmp_path / file_name, schema_name=schema_name),
    )
    assert len(issues) == 1
    assert "must be a string or null" in issues[0]


@pytest.mark.parametrize(
    "bad_value", [pytest.param(7, id="int"), pytest.param(False, id="bool")]
)
@pytest.mark.parametrize("key", ["label", "notes", "update_reason"])
def test_freeform_field_non_string_rejected_concepts(
    key: str, bad_value: Any, tmp_path: Path
) -> None:
    issues = _issues_for(
        [{"name": "concept.claim", "definition": "d", key: bad_value}],
        _concept_path_id(tmp_path / "concepts.yaml"),
    )
    assert len(issues) == 1
    assert f"`{key}` must be a string or null" in issues[0]


def test_freeform_fields_null_and_string_still_pass(tmp_path: Path) -> None:
    # Explicit nulls and genuine strings assemble cleanly on every
    # freeform key (the typing rule rejects only non-null non-strings).
    corpus = _corpus_for(
        [
            {
                "table_name": "bene",
                "description": "d",
                "notes": None,
                "update_reason": None,
            },
            {
                "table_name": "claim",
                "description": "d",
                "notes": "Free text.",
                "update_reason": "Renamed per source docs.",
            },
        ],
        _path_id("tables", tmp_path / "tables.yaml"),
    )
    assert corpus.tables["ocs.general.claim"].notes == "Free text."
    assert corpus.tables["ocs.general.bene"].notes is None


# ---------------------------------------------------------------------------
# Cascade suppression — one defect, one issue
# ---------------------------------------------------------------------------


def test_bad_table_row_no_unknown_table_cascade(tmp_path: Path) -> None:
    # A single broken tables.yaml row must not also surface as a phantom
    # "unknown table" from the deployments map that references it.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "general" / "tables.yaml").write_text(
        "- table_name: film\n"  # missing description -> rejected
        "- table_name: actor\n  description: Actors.\n",
        encoding="utf-8",
    )
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  schemas:\n    general:\n      tables:\n"
        "        film: film\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert len(issues) == 1
    assert "blank `description`" in issues[0]
    assert not any("unknown table" in i for i in issues)


def test_bad_table_row_no_zero_rows_or_deploys_nowhere_cascade(
    tmp_path: Path,
) -> None:
    # mart's only documented table breaks; its bare deployment entry now
    # expands to zero rows and the data source deploys nowhere — both are
    # cascades of the one row defect and must stay silent.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "mart" / "analytics" / "tables.yaml").write_text(
        "- table_name: fact\n", encoding="utf-8"  # missing description
    )
    issues = _assemble_tree_issues(root)
    assert len(issues) == 1
    assert "blank `description`" in issues[0]
    assert not any("expands to zero deployment rows" in i for i in issues)
    assert not any("no deployments" in i for i in issues)


def test_bad_table_row_no_documented_tables_cascade(tmp_path: Path) -> None:
    # The string-form schema entry's "no documented tables" diagnostic is
    # likewise suppressed when the inventory is empty only because the
    # schema's table rows were rejected.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "mart" / "analytics" / "tables.yaml").write_text(
        "- table_name: fact\n", encoding="utf-8"  # missing description
    )
    (root / "sources" / "mart" / "deployments.yaml").write_text(
        "- system: warehouse\n  schemas:\n    analytics: analytics_phys\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert len(issues) == 1
    assert "blank `description`" in issues[0]
    assert not any("no documented tables" in i for i in issues)


def test_bad_table_row_no_documented_tables_mapping_form_cascade(
    tmp_path: Path,
) -> None:
    # The mapping-form schema entry (`name:` only, no `tables:`) shares
    # the string form's suppression: no "no documented tables" cascade
    # when the inventory is empty only because the schema's table rows
    # were rejected in wave 1.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "mart" / "analytics" / "tables.yaml").write_text(
        "- table_name: fact\n", encoding="utf-8"  # missing description
    )
    (root / "sources" / "mart" / "deployments.yaml").write_text(
        "- system: warehouse\n  schemas:\n    analytics:\n"
        "      name: analytics_phys\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert len(issues) == 1
    assert "blank `description`" in issues[0]
    assert not any("no documented tables" in i for i in issues)


def test_bad_schema_row_no_unknown_schema_cascade(tmp_path: Path) -> None:
    # A rejected schema.yaml must not resurface as a phantom "unknown
    # schema" from a deployments map that names the schema explicitly.
    # The schema's tables.yaml is emptied too: with documented tables
    # the schema would stay known through its table inventory, so only
    # a truly absent schema reaches the unknown-schema suppression.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "mart" / "analytics" / "schema.yaml").write_text(
        "description: ''\n", encoding="utf-8"  # blank -> rejected
    )
    (root / "sources" / "mart" / "analytics" / "tables.yaml").write_text(
        "[]\n", encoding="utf-8"
    )
    (root / "sources" / "mart" / "deployments.yaml").write_text(
        "- system: warehouse\n  schemas:\n    analytics: analytics_phys\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert len(issues) == 1
    assert "blank `description`" in issues[0]
    assert not any("unknown schema 'analytics'" in i for i in issues)


def test_broken_tables_file_suppresses_deployment_cascades(
    tmp_path: Path,
) -> None:
    # A tables.yaml that fails wholesale (unparsable) leaves its schema's
    # table names unknowable: every deployment consequence is suppressed,
    # leaving the one parse issue.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "mart" / "analytics" / "tables.yaml").write_text(
        "a: : :\n  - bad\n", encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    assert len(issues) == 1
    assert "Failed to read or parse YAML" in issues[0]


def test_genuinely_unknown_table_still_reported(tmp_path: Path) -> None:
    # Suppression is scoped to rejected rows: a deployments map naming a
    # table that never existed still gets the real "unknown table" issue.
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  schemas:\n    general:\n      tables:\n"
        "        nope: nope_phys\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert any("unknown table 'nope'" in i for i in issues)


# ---------------------------------------------------------------------------
# Physical-name lowercase — a wave-1 issue (moved from wave 2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("deployments_text", "expected_kind"),
    [
        pytest.param(
            "- system: sandbox\n  database_name: PAGILA_PHYS\n",
            "physical_database_name",
            id="database_name",
        ),
        pytest.param(
            "- system: sandbox\n  schemas:\n    general: GEN_PHYS\n",
            "physical_schema_name",
            id="schema_string_form",
        ),
        pytest.param(
            "- system: sandbox\n  schemas:\n    general:\n"
            "      name: GEN_PHYS\n",
            "physical_schema_name",
            id="schema_name_key",
        ),
        pytest.param(
            "- system: sandbox\n  schemas:\n    general:\n      tables:\n"
            "        film: FILM_PHYS\n",
            "physical_table_name",
            id="table_value",
        ),
    ],
)
def test_uppercase_physical_name_is_wave_one_issue(
    deployments_text: str, expected_kind: str, tmp_path: Path
) -> None:
    # The deployment file rules (CONTRIBUTING.md wave 1): explicit physical
    # names are lowercase, checked
    # at assembly alongside the explicit/non-null checks — exactly one
    # issue (no wave-2 double report; validation never runs on a corpus
    # that failed assembly, and its physical-name case check is gone).
    root = _build_corpus_tree(tmp_path)
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        deployments_text, encoding="utf-8"
    )
    issues = _assemble_tree_issues(root)
    lowercase_issues = [i for i in issues if "must be lowercase" in i]
    assert len(lowercase_issues) == 1
    assert expected_kind in lowercase_issues[0]


# ---------------------------------------------------------------------------
# YAML merge keys are rejected with a clear message
# ---------------------------------------------------------------------------


def test_merge_key_rejected_with_clear_message(tmp_path: Path) -> None:
    ident = _path_id("tables", tmp_path / "tables.yaml")
    ident.path.write_text(
        "- &base\n"
        "  description: Shared description.\n"
        "  table_name: film\n"
        "- <<: *base\n"
        "  table_name: actor\n",
        encoding="utf-8",
    )
    with pytest.raises(corpus_assembly.AssemblyError) as excinfo:
        corpus_assembly.assemble_corpus([ident])
    issues = excinfo.value.issues
    assert len(issues) == 1
    assert "merge key" in issues[0]
    assert "spell out each field explicitly" in issues[0]
    assert str(ident.path) in issues[0]
    # The cryptic constructor message is gone.
    assert "could not determine a constructor" not in issues[0]


def test_assemble_corpus_merges_discovery_and_assembly_issues(
    tmp_path: Path,
) -> None:
    # End-to-end: a misplaced file (discovery) and a bad row (assembly)
    # surface together in one AssemblyError.
    data_root = _build_corpus_tree(tmp_path)
    schema_dir = data_root / "sources" / "pagila" / "general"
    (schema_dir / "stray.yaml").write_text("x: 1\n", encoding="utf-8")
    tables_path = schema_dir / "tables.yaml"
    tables_path.write_text(
        tables_path.read_text(encoding="utf-8")
        + "- description: row without a table_name\n",
        encoding="utf-8",
    )
    files, discovery_issues = discover_yaml_files(data_root)
    assert len(discovery_issues) == 1
    with pytest.raises(corpus_assembly.AssemblyError) as excinfo:
        corpus_assembly.assemble_corpus(files, discovery_issues)
    issues = excinfo.value.issues
    joined = "\n".join(issues)
    assert "Unrecognized YAML location" in joined  # discovery
    assert any("table_name" in i for i in issues)  # assembly
    assert str(excinfo.value).startswith("Corpus assembly failed")


# ---------------------------------------------------------------------------
# data_type non-blank — a blank type is a wave-1 shape issue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_data_type",
    [pytest.param("", id="empty"), pytest.param("   ", id="whitespace")],
)
def test_columns_blank_data_type_rejected(
    bad_data_type: str, tmp_path: Path
) -> None:
    # data_type is strip-checked like description: a blank value would load
    # a column documented with no type.
    doc = [
        {
            "table_name": "bene",
            "column_name": "id",
            "data_type": bad_data_type,
            "is_nullable": True,
            "description": "An id.",
        }
    ]
    issues = _issues_for(doc, _path_id("columns", tmp_path / "columns.yaml"))
    assert len(issues) == 1
    assert "blank `data_type`" in issues[0]


def test_columns_nonblank_data_type_passes(tmp_path: Path) -> None:
    doc = [
        {
            "table_name": "bene",
            "column_name": "id",
            "data_type": "INT",
            "is_nullable": True,
            "description": "An id.",
        }
    ]
    corpus = _corpus_for(doc, _path_id("columns", tmp_path / "columns.yaml"))
    assert len(corpus.columns) == 1


# ---------------------------------------------------------------------------
# Whitespace-only freeform values rejected — one spelling of absent (NULL)
# ---------------------------------------------------------------------------

_COLUMN_ROW: dict[str, Any] = {
    "table_name": "bene",
    "column_name": "id",
    "data_type": "INT",
    "is_nullable": True,
    "description": "An id.",
}


@pytest.mark.parametrize(
    ("file_type", "row", "key"),
    [
        pytest.param("columns", _COLUMN_ROW, "notes", id="notes_column"),
        pytest.param(
            "columns", _COLUMN_ROW, "update_reason", id="update_reason_column"
        ),
        pytest.param(
            "table_relationships",
            {**_REL_FIELDS, "join_condition": _REL_JOIN_CONDITION},
            "use_when",
            id="use_when_relationship",
        ),
        pytest.param(
            "concepts",
            {"name": "concept.gloss", "definition": "A term."},
            "label",
            id="label_concept",
        ),
    ],
)
def test_whitespace_only_freeform_value_rejected(
    file_type: str, row: dict[str, Any], key: str, tmp_path: Path
) -> None:
    # A whitespace-only freeform value is an ambiguous second spelling of
    # absent — rejected in wave 1 so stored rows have one spelling (NULL).
    doc = [{**row, key: "   "}]
    ident = _path_id(file_type, tmp_path / f"{file_type}.yaml")
    issues = _issues_for(doc, ident)
    assert len(issues) == 1
    assert "non-whitespace content" in issues[0]
    assert key in issues[0]


def test_freeform_null_and_real_content_pass(tmp_path: Path) -> None:
    # Explicit null and genuine non-blank content both load unchanged.
    doc = [{**_COLUMN_ROW, "notes": None, "update_reason": "renamed column"}]
    corpus = _corpus_for(doc, _path_id("columns", tmp_path / "columns.yaml"))
    (column,) = corpus.columns.values()
    assert column.notes is None
    # Authored content is never trimmed.
    assert column.update_reason == "renamed column"


# ---------------------------------------------------------------------------
# Physical-name validated before wave-1-cascade suppression
# ---------------------------------------------------------------------------


def test_rejected_table_row_still_reports_invalid_physical_name(
    tmp_path: Path,
) -> None:
    # A wave-1-rejected table row must not suppress an independent invalid
    # physical name on the same deployment line: report the physical-name
    # issue; suppress only the phantom unknown-table issue.
    root = _build_corpus_tree(tmp_path)
    # Reject `film` (missing description); keep `actor` valid so pagila
    # still deploys somewhere (no deploys-nowhere cascade to reason about).
    (root / "sources" / "pagila" / "general" / "tables.yaml").write_text(
        "- table_name: film\n"  # missing description -> rejected
        "- table_name: actor\n  description: Actors.\n",
        encoding="utf-8",
    )
    # Deploy referencing the rejected `film` with an UPPERCASE (invalid)
    # physical name, alongside a valid `actor`.
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  schemas:\n    general:\n      tables:\n"
        "        film: FILM_PHYS\n"
        "        actor: actor\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    # The independent physical-name defect is reported...
    assert any(
        "must be lowercase" in i and "physical_table_name" in i
        for i in issues
    )
    # ...while the phantom "unknown table" for the rejected row stays quiet.
    assert not any("unknown table 'film'" in i for i in issues)
    # The original row defect is still surfaced.
    assert any("blank `description`" in i for i in issues)


# ---------------------------------------------------------------------------
# Shard folders — union semantics, mutual exclusion, cascade suppression
# ---------------------------------------------------------------------------


def test_sharded_columns_assemble_identically_to_single_file(
    tmp_path: Path,
) -> None:
    # A schema's columns split across two shards assembles to the exact
    # same corpus as the single columns.yaml. The other types stay
    # single-file, pinning per-type independence (a folder for one type
    # alongside single files for the others is legal).
    root = _build_corpus_tree(tmp_path)
    baseline = _assemble_tree(root)
    general = root / "sources" / "pagila" / "general"
    (general / "columns.yaml").unlink()
    shard_dir = general / "columns"
    shard_dir.mkdir()
    (shard_dir / "film.yaml").write_text(
        "- table_name: film\n  column_name: film_id\n  data_type: INT\n"
        "  is_nullable: false\n  is_primary_key: true\n"
        "  description: Film id.\n"
        "- table_name: film\n  column_name: title\n  data_type: TEXT\n"
        "  is_nullable: false\n  description: Film title.\n",
        encoding="utf-8",
    )
    (shard_dir / "actor.yaml").write_text(
        "- table_name: actor\n  column_name: actor_id\n  data_type: INT\n"
        "  is_nullable: false\n  is_primary_key: true\n"
        "  description: Actor id.\n",
        encoding="utf-8",
    )
    sharded = _assemble_tree(root)
    assert sharded == baseline


def test_sharded_tables_assemble_identically_to_single_file(
    tmp_path: Path,
) -> None:
    # Same union guarantee for tables/ — including the deployment
    # expansion, which runs against the shard-assembled inventory.
    root = _build_corpus_tree(tmp_path)
    baseline = _assemble_tree(root)
    general = root / "sources" / "pagila" / "general"
    (general / "tables.yaml").unlink()
    shard_dir = general / "tables"
    shard_dir.mkdir()
    (shard_dir / "film.yaml").write_text(
        "- table_name: film\n  description: Films.\n", encoding="utf-8"
    )
    (shard_dir / "actor.yaml").write_text(
        "- table_name: actor\n  description: Actors.\n", encoding="utf-8"
    )
    sharded = _assemble_tree(root)
    assert sharded == baseline


def test_sharded_table_relationships_assemble_identically_to_single_file(
    tmp_path: Path,
) -> None:
    # Same union guarantee for table_relationships/; an empty shard file
    # contributes zero rows (like an empty single file).
    root = _build_corpus_tree(tmp_path)
    baseline = _assemble_tree(root)
    general = root / "sources" / "pagila" / "general"
    single = general / "table_relationships.yaml"
    text = single.read_text(encoding="utf-8")
    single.unlink()
    shard_dir = general / "table_relationships"
    shard_dir.mkdir()
    (shard_dir / "film.yaml").write_text(text, encoding="utf-8")
    (shard_dir / "empty.yaml").write_text("[]\n", encoding="utf-8")
    sharded = _assemble_tree(root)
    assert sharded == baseline


def test_sharded_data_source_concepts_assemble_identically_to_single_file(
    tmp_path: Path,
) -> None:
    # A data-source-level concepts/ folder unions like the single
    # {label}/concepts.yaml; concept_ids come from the path scope, never
    # the shard stem.
    root = _build_corpus_tree(tmp_path)
    single = root / "sources" / "pagila" / "concepts.yaml"
    single.write_text(
        "- name: concept.rental\n  definition: A rental event.\n"
        "- name: concept.inventory\n  definition: A stocked copy.\n",
        encoding="utf-8",
    )
    baseline = _assemble_tree(root)
    single.unlink()
    shard_dir = root / "sources" / "pagila" / "concepts"
    shard_dir.mkdir()
    (shard_dir / "ops.yaml").write_text(
        "- name: concept.rental\n  definition: A rental event.\n",
        encoding="utf-8",
    )
    (shard_dir / "stock.yaml").write_text(
        "- name: concept.inventory\n  definition: A stocked copy.\n",
        encoding="utf-8",
    )
    sharded = _assemble_tree(root)
    assert sharded == baseline
    assert set(sharded.concepts) == {
        "pagila.concept.rental",
        "pagila.concept.inventory",
    }


def test_both_forms_present_schema_scope_rejected(tmp_path: Path) -> None:
    # columns.yaml and a columns/ folder in one schema is split-brain
    # authoring: one issue for the (type, scope) pair naming both paths.
    root = _build_corpus_tree(tmp_path)
    general = root / "sources" / "pagila" / "general"
    shard_dir = general / "columns"
    shard_dir.mkdir()
    # A valid, non-duplicate row so the exclusion is the only defect.
    (shard_dir / "extra.yaml").write_text(
        "- table_name: film\n  column_name: length\n  data_type: INT\n"
        "  is_nullable: true\n  description: Film length.\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert len(issues) == 1
    assert "mutually exclusive" in issues[0]
    assert "'columns'" in issues[0]
    assert "schema pagila.general" in issues[0]
    assert str(general / "columns.yaml") in issues[0]
    assert str(shard_dir) in issues[0]


def test_both_forms_present_data_source_scope_rejected(
    tmp_path: Path,
) -> None:
    # The data-source-level concepts scope gets the same rule: the single
    # {label}/concepts.yaml and a {label}/concepts/ folder may not
    # coexist.
    root = _build_corpus_tree(tmp_path)
    pagila = root / "sources" / "pagila"
    (pagila / "concepts.yaml").write_text(
        "- name: concept.rental\n  definition: A rental event.\n",
        encoding="utf-8",
    )
    shard_dir = pagila / "concepts"
    shard_dir.mkdir()
    (shard_dir / "stock.yaml").write_text(
        "- name: concept.inventory\n  definition: A stocked copy.\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert len(issues) == 1
    assert "mutually exclusive" in issues[0]
    assert "data source 'pagila'" in issues[0]
    assert str(pagila / "concepts.yaml") in issues[0]
    assert str(shard_dir) in issues[0]


def test_duplicate_pk_across_shards_names_both_files(tmp_path: Path) -> None:
    # The same PK defined in two shards is the ordinary cross-file
    # duplicate: one issue naming the offending shard and the shard whose
    # first occurrence is kept.
    root = _build_corpus_tree(tmp_path)
    general = root / "sources" / "pagila" / "general"
    (general / "columns.yaml").unlink()
    shard_dir = general / "columns"
    shard_dir.mkdir()
    row = (
        "- table_name: film\n  column_name: film_id\n  data_type: INT\n"
        "  is_nullable: false\n  is_primary_key: true\n"
        "  description: Film id.\n"
    )
    (shard_dir / "clm.yaml").write_text(row, encoding="utf-8")
    (shard_dir / "bene.yaml").write_text(row, encoding="utf-8")
    issues = _assemble_tree_issues(root)
    assert len(issues) == 1
    assert "Duplicate columns PK" in issues[0]
    assert "pagila.general.film.film_id" in issues[0]
    # Sorted path order: bene.yaml is first-seen, clm.yaml the duplicate.
    assert str(shard_dir / "bene.yaml") in issues[0]
    assert str(shard_dir / "clm.yaml") in issues[0]


def test_misfiled_row_in_wrong_shard_assembles_cleanly(
    tmp_path: Path,
) -> None:
    # Shard stems are grouping labels only (Decisions #2): a row filed in
    # the "wrong" shard (actor's column in film.yaml) still assembles
    # correctly — its identity comes from the row body, and filing
    # consistency is an authoring convention, not a loader rule.
    root = _build_corpus_tree(tmp_path)
    general = root / "sources" / "pagila" / "general"
    (general / "columns.yaml").unlink()
    shard_dir = general / "columns"
    shard_dir.mkdir()
    (shard_dir / "film.yaml").write_text(
        "- table_name: film\n  column_name: film_id\n  data_type: INT\n"
        "  is_nullable: false\n  is_primary_key: true\n"
        "  description: Film id.\n"
        "- table_name: film\n  column_name: title\n  data_type: TEXT\n"
        "  is_nullable: false\n  description: Film title.\n"
        # Misfiled: actor's column in the film shard.
        "- table_name: actor\n  column_name: actor_id\n  data_type: INT\n"
        "  is_nullable: false\n  is_primary_key: true\n"
        "  description: Actor id.\n",
        encoding="utf-8",
    )
    corpus = _assemble_tree(root)
    assert "pagila.general.actor.actor_id" in corpus.columns


def test_broken_tables_shard_suppresses_deployment_cascades(
    tmp_path: Path,
) -> None:
    # An unparsable shard in a tables/ folder leaves part of the schema's
    # table inventory unknowable, exactly like a broken single
    # tables.yaml: the deployment reference to the lost table is a
    # suppressed cascade, leaving the one parse issue.
    root = _build_corpus_tree(tmp_path)
    general = root / "sources" / "pagila" / "general"
    (general / "tables.yaml").unlink()
    shard_dir = general / "tables"
    shard_dir.mkdir()
    (shard_dir / "film.yaml").write_text(
        "- table_name: film\n  description: Films.\n", encoding="utf-8"
    )
    (shard_dir / "actor.yaml").write_text(
        "a: : :\n  - bad\n", encoding="utf-8"  # unparsable
    )
    (root / "sources" / "pagila" / "deployments.yaml").write_text(
        "- system: sandbox\n  schemas:\n    general:\n      tables:\n"
        "        actor: actor\n",
        encoding="utf-8",
    )
    issues = _assemble_tree_issues(root)
    assert len(issues) == 1
    assert "Failed to read or parse YAML" in issues[0]
