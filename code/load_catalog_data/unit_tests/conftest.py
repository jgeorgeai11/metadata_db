"""Shared fixtures for load_catalog_data unit tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# load_catalog_data modules live one directory up from unit_tests/. Put
# that directory on the path so `import yaml_discovery` etc. resolve regardless
# of pytest's rootdir handling.
LOADER_DIR = Path(__file__).resolve().parent.parent
if str(LOADER_DIR) not in sys.path:
    sys.path.insert(0, str(LOADER_DIR))


def _write(path: Path, text: str) -> None:
    """Create parent dirs and write `text` to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _stage_corpus(data_root: Path) -> None:
    """Write a small venue-free corpus compliant with every rule.

    Two data sources: `ocs` (deployed in warehouse) and `edw_prd` (deployed
    in edw). Exercises each file type — the systems registry, deployments,
    a relationship (runnable in warehouse), a mapping computable in edw, and
    a data-source-level concept. Kept inline so it is stable regardless
    of the shipped corpus.

    Shared here rather than in one suite: the offline checker runs the
    loader's first three stages verbatim, so `test_load_catalog_data.py`
    and `test_check_corpus.py` need the same corpus, and the checker's
    suite must not have to import a sibling test module (which would drag
    `psycopg2` into a deliberately database-free run) to get it.
    """
    _write(
        data_root / "systems.yaml",
        "- system: warehouse\n  description: source\n  notes: null\n  update_reason: null\n"
        "- system: edw\n  description: target\n  notes: null\n  update_reason: null\n",
    )
    ocs = data_root / "sources" / "ocs"
    ocs_schema = ocs / "general"
    _write(ocs / "data_source.yaml", "owner: data-ops\ndescription: ocs\nnotes: null\nupdate_reason: null\n")
    _write(ocs / "deployments.yaml", "- system: warehouse\n")
    _write(ocs_schema / "schema.yaml", "description: general\nnotes: null\nupdate_reason: null\n")
    _write(
        ocs_schema / "tables.yaml",
        "- table_name: bene\n  description: beneficiaries\n  notes: null\n  update_reason: null\n"
        "- table_name: claim\n  description: claims\n  notes: null\n  update_reason: null\n",
    )
    _write(
        ocs_schema / "columns.yaml",
        "- table_name: bene\n  column_name: bene_id\n  data_type: TEXT\n  is_nullable: false\n  is_primary_key: true\n  description: id\n  notes: null\n  update_reason: null\n"
        "- table_name: claim\n  column_name: bene_id\n  data_type: TEXT\n  is_nullable: false\n  description: fk\n  notes: null\n  update_reason: null\n"
        "- table_name: claim\n  column_name: clm_id\n  data_type: TEXT\n  is_nullable: false\n  is_primary_key: true\n  description: id\n  notes: null\n  update_reason: null\n",
    )
    _write(
        ocs_schema / "table_relationships.yaml",
        "- table_a_id: ocs.general.bene\n"
        "  table_b_id: ocs.general.claim\n"
        "  relationship_name: default\n"
        "  join_condition: ocs.general.bene.bene_id = ocs.general.claim.bene_id\n"
        "  cardinality: one_to_many\n"
        "  use_when: null\n"
        "  notes: null\n"
        "  validated: false\n"
        "  update_reason: null\n",
    )
    _write(
        ocs_schema / "mappings" / "edw_prd.yaml",
        "- source_column_id: ocs.general.bene.bene_id\n"
        "  mapping_name: default\n"
        "  target_expression: edw_prd.claims_vw.bene.bene_extl_id\n"
        "  use_when: null\n"
        "  notes: null\n"
        "  validated: false\n"
        "  update_reason: null\n",
    )
    # A data-source-level concept anchored under ocs; concept_id is
    # path-derived: ocs.concept.claim. Its related_object_ids resolve to
    # staged catalog rows.
    _write(
        ocs / "concepts.yaml",
        "- name: concept.claim\n  label: Claim\n  definition: A claim.\n"
        "  notes: null\n"
        "  related_object_ids:\n"
        "    - ocs.general.claim\n"
        "    - ocs.general.claim.clm_id\n"
        "  update_reason: null\n",
    )
    edw = data_root / "sources" / "edw_prd"
    edw_schema = edw / "claims_vw"
    _write(edw / "data_source.yaml", "owner: data-ops\ndescription: prd\nnotes: null\nupdate_reason: null\n")
    _write(edw / "deployments.yaml", "- system: edw\n")
    _write(edw_schema / "schema.yaml", "description: view\nnotes: null\nupdate_reason: null\n")
    _write(
        edw_schema / "tables.yaml",
        "- table_name: bene\n  description: bene\n  notes: null\n  update_reason: null\n",
    )
    _write(
        edw_schema / "columns.yaml",
        "- table_name: bene\n  column_name: bene_extl_id\n  data_type: TEXT\n  is_nullable: true\n  description: mbi\n  notes: null\n  update_reason: null\n",
    )


def _stage_config(
    tmp_path: Path, data_root: Path, *, loader_fields: bool = False
) -> Path:
    """Write a minimal TOML config pointing at `data_root` and return its path.

    Args:
        tmp_path: pytest's per-test temporary directory.
        data_root: Corpus root the config's `data_root` should name.
        loader_fields: If True, also write the connection fields
            (`database` / `schema`) the loader requires. The offline
            checker reads `data_root` alone, so its tests leave them out —
            which keeps those runs honest about what the checker consumes.

    Returns:
        Path to the written config file.
    """
    text = f'data_root = "{data_root.as_posix()}"\n'
    if loader_fields:
        text += 'database = "metadata_db"\nschema = "catalog"\n'
    cfg = tmp_path / "loader.toml"
    cfg.write_text(text, encoding="utf-8")
    return cfg


@pytest.fixture
def fake_cursor() -> MagicMock:
    """A mock psycopg2 cursor (the object yielded by `with conn.cursor()`)."""
    return MagicMock()


@pytest.fixture
def fake_conn(fake_cursor: MagicMock) -> MagicMock:
    """A mock psycopg2 connection whose cursor() context manager yields
    `fake_cursor`."""
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = fake_cursor
    conn.cursor.return_value.__exit__.return_value = False
    return conn
