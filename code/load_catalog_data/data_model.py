"""data_model.py — row dataclasses and table registry for metadata_db.

One frozen dataclass per main table mirrors the columns in
`SCHEMA.md`, minus loader-managed timestamps that
are written at apply time (`insert_ts`, `update_ts`). Computed columns
such as `column_mappings.target_tables_referenced` are present on the
dataclass but are populated by the loader (after parsing
`target_expression`) rather than authored in YAML. Equality of these
rows is value-based, which makes diff computation trivial — see
`corpus_diff.py`.

Identity is venue-free: no id contains a system name. `systems` is a
registry keyed by a bare `system` label, referenced only by
`deployment_tables`. `data_sources` is keyed by a globally unique
catalog label (a single segment); `schemas`, `tables`, and `columns`
compose dotted composite IDs via the builders below (`schema_id`,
`table_id`, `column_id`) as `{database}.{schema}.{table}.{column}`
where `{database}` is the data-source label. `deployment_tables` is
keyed by `(table_id, system)` — the one place venue-dependent facts
(which system hosts a table, under what physical names) live.
`table_relationships` is keyed by a composite tuple of table ids and a
name; `column_mappings` by `(source_column_id, mapping_name)`; and
`concepts` composes `{database}[.{schema}[.{table}[.{column}]]].concept.{leaf}`
as the file's path prefix + `.` + the body `name`, byte for byte — the
`name` is the id relative to the file's anchor
(`[{table}[.{column}].]concept.{leaf}`), with the reserved `concept`
segment written by the author, second-to-last; see
`corpus_assembly._assemble_concepts`.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any


# FK-respecting order of the 9 main tables. The loader applies inserts
# and updates in this order so parent rows exist before children
# reference them; deletes run in reverse order so children are removed
# before their parents. `deployment_tables` sits after `columns` (it
# references tables, systems, and data_sources — all earlier) and before
# `table_relationships`. `concepts` has no FK columns (its path prefix
# references no row), so it is safe at the end (no parent to precede it,
# no child to follow).
TABLE_ORDER: tuple[str, ...] = (
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


# Primary-key columns per main table, in declaration order. This is the
# single source of truth for how the corpus and DbState dicts are keyed:
# both `corpus_assembly.assemble_corpus` and `db_io.read_db_state` derive their keys
# via `pk()` below. The DDL's PRIMARY KEY constraints must match this map
# — the integration test `test_pk_agreement_and_ltree_types` asserts that they do.
PRIMARY_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "systems": ("system",),
    "data_sources": ("data_source_id",),
    "schemas": ("schema_id",),
    "tables": ("table_id",),
    "columns": ("column_id",),
    "deployment_tables": ("table_id", "system"),
    "table_relationships": ("table_a_id", "table_b_id", "relationship_name"),
    "column_mappings": ("source_column_id", "mapping_name"),
    "concepts": ("concept_id",),
}


# Columns considered for diff equality, per main table. Excludes
# `insert_ts`/`update_ts` (loader-managed) so re-running with no YAML
# changes is a no-op. PK columns are included — a PK change manifests
# as a delete+insert in `corpus_diff.py`, not as an in-place update.
CONTENT_COLUMNS: dict[str, frozenset[str]] = {
    "systems": frozenset(
        {"system", "description", "notes", "update_reason"}
    ),
    "data_sources": frozenset(
        {
            "data_source_id",
            "owner",
            "description",
            "notes",
            "update_reason",
        }
    ),
    "schemas": frozenset(
        {
            "schema_id",
            "data_source_id",
            "schema_name",
            "description",
            "notes",
            "update_reason",
        }
    ),
    "tables": frozenset(
        {
            "table_id",
            "schema_id",
            "table_name",
            "description",
            "notes",
            "update_reason",
        }
    ),
    "columns": frozenset(
        {
            "column_id",
            "table_id",
            "column_name",
            "data_type",
            "is_nullable",
            "is_primary_key",
            "ref_table_id",
            "description",
            "notes",
            "update_reason",
        }
    ),
    # Pure-facts table: identity plus the physical names only. There is
    # no `update_reason` to exclude — the column does not exist (see
    # SCHEMA.md `deployment_tables`).
    "deployment_tables": frozenset(
        {
            "table_id",
            "system",
            "data_source_id",
            "physical_database_name",
            "physical_schema_name",
            "physical_table_name",
        }
    ),
    "table_relationships": frozenset(
        {
            "table_a_id",
            "table_b_id",
            "relationship_name",
            "join_condition",
            "cardinality",
            "use_when",
            "notes",
            "validated",
            "update_reason",
        }
    ),
    "column_mappings": frozenset(
        {
            "source_column_id",
            "mapping_name",
            "target_tables_referenced",
            "target_expression",
            "use_when",
            "notes",
            "validated",
            "update_reason",
        }
    ),
    "concepts": frozenset(
        {
            "concept_id",
            "label",
            "definition",
            "notes",
            "related_object_ids",
            "update_reason",
        }
    ),
}


@dataclass(frozen=True)
class SystemRow:
    """One `systems` row.

    Attributes:
        system: PK; system identifier (e.g., `warehouse`).
        description: Freeform; required (an undescribed venue tells a
            consumer nothing).
        notes: Freeform; may be None.
        update_reason: NULL for fresh inserts; non-null on updates.
    """

    system: str
    description: str
    notes: str | None
    update_reason: str | None


@dataclass(frozen=True)
class DataSourceRow:
    """One `data_sources` row. PK = `data_source_id` (a single label).

    `data_source_id` is the globally unique catalog label chosen at
    authoring time (e.g. `sandbox_ocs`, `puf_clfs`); it defaults to the
    physical database name but need not equal it — per-venue physical
    names live in `deployment_tables`. `owner` is the team accountable
    for the documentation and `description` is the prose the catalog is
    documentation for (both required). There is no `system` or
    `database_name` field: residency is venue-dependent and lives in
    `deployment_tables`, and the label serves as the `database` segment
    of every id beneath it.
    """

    data_source_id: str
    owner: str
    description: str
    notes: str | None
    update_reason: str | None


@dataclass(frozen=True)
class SchemaRow:
    """One `schemas` row. PK = `schema_id` (`{database}.{schema_name}`).

    `description` is required prose (loader-enforced, NOT NULL in DDL).
    """

    schema_id: str
    data_source_id: str
    schema_name: str
    description: str
    notes: str | None
    update_reason: str | None


@dataclass(frozen=True)
class TableRow:
    """One `tables` row. PK = `table_id` (`{database}.{schema}.{table}`).

    `description` is required prose (loader-enforced, NOT NULL in DDL).
    """

    table_id: str
    schema_id: str
    table_name: str
    description: str
    notes: str | None
    update_reason: str | None


@dataclass(frozen=True)
class ColumnRow:
    """One `columns` row. PK = `column_id` (4 segments).

    `is_primary_key` records the table's grain: a table's primary key is
    the set of its columns flagged True (a composite key is multiple
    flags). It is consumer knowledge for deriving an aggregate/multi-table
    mapping's grouping, not a loader-enforced constraint — the columns PK
    stays `column_id` (`{database}.{schema}.{table}.{column}`).
    `description` is required prose (loader-enforced, NOT NULL in DDL).

    `ref_table_id` is the optional domain pointer: the documented table
    that enumerates this column's value domain (authored as `ref_table`
    in `columns.yaml`, resolved against `tables` in wave 2). Context
    retrieval only — no join path, no co-deployment semantics. It is an
    authored content column (diffed like any other field), but sits LAST
    as a defaulted field, deviating from the DDL (which places it after
    `is_primary_key`): the default keeps existing positional
    construction working, mirroring `TableRelationshipRow.validated_ts`.
    Field order must match `db_io._SELECT_COLUMNS` — rows are built
    positionally there.
    """

    column_id: str
    table_id: str
    column_name: str
    data_type: str
    is_nullable: bool
    is_primary_key: bool
    description: str
    notes: str | None
    update_reason: str | None
    # Authored content column (IN CONTENT_COLUMNS, unlike validated_ts);
    # defaulted only so it may sit last (see the class docstring).
    ref_table_id: str | None = None


@dataclass(frozen=True)
class DeploymentRow:
    """One `deployment_tables` row. PK = (`table_id`, `system`).

    One row per (documented table, venue): the table is materialized in
    `system` under these physical names. The single home of
    venue-dependent truth — absence of a row means "not deployed" (there
    are no tombstones). Authored sparse in a per-data-source
    `deployments.yaml` (a bare venue entry = all schemas/tables, original
    names; an exhaustive `schemas:` map subsets/renames) and expanded to
    explicit table-grain rows by the loader (see
    `corpus_assembly._assemble_deployments`).

    Pure-facts shape: exactly the identity, `data_source_id`, and the
    three physical names — no freeform columns. `notes`/`update_reason`
    were dropped because entry-grain authoring and table-grain storage
    never agreed on what an inherited freeform value meant; rationale
    lives in git via `load_audit`, caveats via concepts (see
    SCHEMA.md `deployment_tables` for the full
    reasoning).

    `data_source_id` is redundant with `table_id`'s leading segment but
    stored for query convenience. The three `physical_*_name` values are
    plain text (not ltree segments) but authored lowercase like every
    catalog name, so the physical-address uniqueness check is plain
    equality. Field order must match the DDL column order and
    `db_io._SELECT_DEPLOYMENT_TABLES` — rows are built positionally
    there.
    """

    table_id: str
    system: str
    data_source_id: str
    physical_database_name: str
    physical_schema_name: str
    physical_table_name: str


@dataclass(frozen=True)
class TableRelationshipRow:
    """One `table_relationships` row.

    PK = (`table_a_id`, `table_b_id`, `relationship_name`).

    There is no `system` column: a relationship records join *logic*, and
    the venues where it can run are derived — the intersection of the two
    endpoint tables' `deployments` sets (loader-enforced non-empty; see
    `corpus_validation._check_relationship_codeployment`). `cardinality`
    records the a->b row correspondence (`one_to_one`, `one_to_many`,
    `many_to_one`, `many_to_many`; e.g. `many_to_one` = many `table_a`
    rows match one `table_b` row). None means "not yet recorded" — never
    guessed via a default. Field order must match
    `db_io._SELECT_TABLE_RELATIONSHIPS` — rows are built positionally
    there. (It intentionally deviates from the DDL, which orders
    `validated_ts` before `update_reason`: the loader-managed
    `validated_ts` is a defaulted field and must sit last.)
    """

    table_a_id: str
    table_b_id: str
    relationship_name: str
    join_condition: str
    cardinality: str | None
    use_when: str | None
    notes: str | None
    validated: bool
    update_reason: str | None
    # Loader-managed (not authored in YAML, excluded from CONTENT_COLUMNS):
    # set to now() on a validated false->true transition, NULL'd on
    # true->false, preserved when it stays validated. psycopg2 decodes the
    # timestamptz column to datetime; corpus rows leave it None. See
    # db_io._validated_ts_update_args.
    validated_ts: datetime | None = None


@dataclass(frozen=True)
class ColumnMappingRow:
    """One `column_mappings` row.

    PK = (`source_column_id`, `mapping_name`). There is no
    `target_system` field: the expression's own references identify the
    target dataset, and *where* the equivalent is computable is derived
    from those tables' deployments. `mapping_name` distinguishes multiple
    mappings from the same source column (like
    `table_relationships.relationship_name`) and should say what the
    mapping is *toward*; `use_when` documents when to prefer one over the
    others. There is no `source_system` field — the source is the leading
    label of `source_column_id`. `target_tables_referenced` is a tuple
    (not list) so the dataclass stays hashable; it's populated by the
    loader after parsing `target_expression` and may be empty when
    `target_expression` is None.
    """

    source_column_id: str
    mapping_name: str
    target_tables_referenced: tuple[str, ...]
    target_expression: str | None
    use_when: str | None
    notes: str | None
    validated: bool
    update_reason: str | None
    # Loader-managed (not authored in YAML, excluded from CONTENT_COLUMNS):
    # set to now() on a validated false->true transition, NULL'd on
    # true->false, preserved when it stays validated. psycopg2 decodes the
    # timestamptz column to datetime; corpus rows leave it None. See
    # db_io._validated_ts_update_args.
    validated_ts: datetime | None = None


@dataclass(frozen=True)
class ConceptRow:
    """One `concepts` row. PK = `concept_id`.

    Business-glossary entry anchored under a data source at the
    data-source, schema, table, or column level. `concept_id` is
    path-derived like every other row —
    `{database}[.{schema}[.{table}[.{column}]]].concept.{leaf}`, the
    file's anchor prefix + `.` + the body `name`, byte for byte. The
    `name` is the id relative to that anchor, of the required form
    `[{table}[.{column}].]concept.{leaf}`: the author writes the
    reserved `concept` segment second-to-last, any segments before it
    deepen the anchor to a table or column, and the final segment is
    the leaf (see `corpus_assembly._assemble_concepts`). `definition` is freeform
    prose that is never parsed or executed, so there are no FK columns, no
    SQL, and no `validated` flag. Field order must match the DDL column
    order (`code/apply_ddl/ddl_catalog/0001_initial_schema.sql`) and
    `db_io._SELECT_CONCEPTS` — rows are built positionally there, so
    `related_object_ids` sits between `notes` and `update_reason` exactly
    as in the DDL.

    Attributes:
        concept_id: PK; dotted `ltree` id — the anchor path prefix +
            `.` + the body `name` (which carries the reserved `concept`
            segment), with nothing inserted.
        label: Short human-readable name; may be None.
        definition: Freeform prose definition; required (a
            definition-less concept is a glossary entry with nothing to
            look up).
        notes: Freeform; may be None.
        related_object_ids: Authored links to the catalog objects the
            concept is about; each entry must resolve to a corpus PK
            across the data_sources / schemas / tables / columns /
            concepts id spaces (validated in `corpus_validation`).
            A tuple (not list) so the frozen dataclass stays hashable —
            mirroring `ColumnMappingRow.target_tables_referenced`; author
            order is preserved and `()` means no links.
        update_reason: NULL for fresh inserts; non-null on updates.
    """

    concept_id: str
    label: str | None
    definition: str
    notes: str | None
    related_object_ids: tuple[str, ...]
    update_reason: str | None


# Composite-PK tuples used to index the corpus / db state dicts.
DeploymentKey = tuple[str, str]  # (table_id, system)
TableRelationshipKey = tuple[str, str, str]
ColumnMappingKey = tuple[str, str]  # (source_column_id, mapping_name)


@dataclass(frozen=True)
class Corpus:
    """The complete YAML-derived corpus, one dict per main table.

    Dicts are keyed by primary key — a single string for the six
    single-PK tables (`systems`..`columns` plus `concepts`), a 2-tuple
    for `deployment_tables` (`(table_id, system)`) and `column_mappings`
    (`(source_column_id, mapping_name)`), and a 3-tuple for
    `table_relationships`.
    """

    systems: dict[str, SystemRow]
    data_sources: dict[str, DataSourceRow]
    schemas: dict[str, SchemaRow]
    tables: dict[str, TableRow]
    columns: dict[str, ColumnRow]
    deployment_tables: dict[DeploymentKey, DeploymentRow]
    table_relationships: dict[TableRelationshipKey, TableRelationshipRow]
    column_mappings: dict[ColumnMappingKey, ColumnMappingRow]
    concepts: dict[str, ConceptRow]


@dataclass(frozen=True)
class DbState:
    """Current DB state read at the start of a load run.

    Shape mirrors `Corpus` exactly so the diff routine can treat both
    sides uniformly.
    """

    systems: dict[str, SystemRow]
    data_sources: dict[str, DataSourceRow]
    schemas: dict[str, SchemaRow]
    tables: dict[str, TableRow]
    columns: dict[str, ColumnRow]
    deployment_tables: dict[DeploymentKey, DeploymentRow]
    table_relationships: dict[TableRelationshipKey, TableRelationshipRow]
    column_mappings: dict[ColumnMappingKey, ColumnMappingRow]
    concepts: dict[str, ConceptRow]


@dataclass(frozen=True)
class ColumnRef:
    """A fully-qualified column reference parsed from SQL.

    Always 4 segments: `{database}.{schema}.{table}.{column}`. Ids are
    venue-free, so there is no leading system segment. Used by
    `sql_parsing.py` and `corpus_validation.py` to validate that every
    column referenced in a `target_expression` or `join_condition`
    resolves to a known `columns` row.

    Attributes:
        database: Database (data-source label) segment.
        schema: Schema name segment.
        table: Table name segment.
        column: Column name segment.
    """

    database: str
    schema: str
    table: str
    column: str

    @property
    def table_id(self) -> str:
        """The composed `table_id` (first 3 segments joined by `.`)."""
        return f"{self.database}.{self.schema}.{self.table}"

    @property
    def column_id(self) -> str:
        """The composed `column_id` (all 4 segments joined by `.`)."""
        return f"{self.table_id}.{self.column}"


def empty_corpus() -> Corpus:
    """Build an empty corpus with all 9 dicts initialized."""
    return Corpus(
        systems={},
        data_sources={},
        schemas={},
        tables={},
        columns={},
        deployment_tables={},
        table_relationships={},
        column_mappings={},
        concepts={},
    )


def empty_db_state() -> DbState:
    """Build an empty `DbState` with all 9 dicts initialized."""
    return DbState(
        systems={},
        data_sources={},
        schemas={},
        tables={},
        columns={},
        deployment_tables={},
        table_relationships={},
        column_mappings={},
        concepts={},
    )


def iter_tables() -> Iterator[str]:
    """Yield table names in `TABLE_ORDER`."""
    yield from TABLE_ORDER


def data_source_id(database: str) -> str:
    """Return the `data_source_id` for a data-source label.

    Ids are venue-free: the data-source label *is* its id (a single
    ltree segment) and also serves as the `{database}` segment of every
    id beneath it. This trivial builder exists so every assembler in
    `corpus_assembly.py` derives the id one way and the format can only
    change here. Takes a plain string (not a `PathIdentity`) to keep this
    module free of a `yaml_discovery` import.

    Args:
        database: The data-source label (the `{database}` segment).

    Returns:
        The `data_source_id` (equal to `database`).
    """
    return database


def schema_id(database: str, schema: str) -> str:
    """Compose a `schema_id` from its segments.

    Args:
        database: Database (data-source label) segment.
        schema: Schema name segment.

    Returns:
        The dotted `{database}.{schema}` composite id.
    """
    return f"{database}.{schema}"


def table_id(database: str, schema: str, table: str) -> str:
    """Compose a `table_id` from its segments.

    Args:
        database: Database (data-source label) segment.
        schema: Schema name segment.
        table: Table name segment.

    Returns:
        The dotted `{database}.{schema}.{table}` composite id.
    """
    return f"{database}.{schema}.{table}"


def column_id(table_id: str, column: str) -> str:
    """Compose a `column_id` by chaining a column onto a `table_id`.

    Chaining form (rather than the four flat segments) mirrors how the
    assembler already derives a column id from the table id it just built.

    Args:
        table_id: A composite `table_id` (from `table_id()`).
        column: Column name segment.

    Returns:
        The dotted `{table_id}.{column}` composite id.
    """
    return f"{table_id}.{column}"


def schema_prefix(dotted_id: str) -> str:
    """Return the `{database}.{schema}` prefix of a dotted id.

    The exact inverse the assemblers rely on to recover a table/column id's
    owning schema: `schema_prefix(table_id(d, sc, t)) == schema_id(d, sc)`.
    Replaces the inline `".".join(x.split(".")[:2])` prefix parsing.

    Args:
        dotted_id: A composite `table_id` (3 segments) or `column_id`
            (4 segments).

    Returns:
        The first two segments (`{database}.{schema}`) joined by `.`.
    """
    return ".".join(dotted_id.split(".")[:2])


def split_schema_id(schema_id: str) -> tuple[str, str]:
    """Decompose a `schema_id` into its `(database, schema)` segments.

    The exact inverse of `schema_id()`:
    `split_schema_id(schema_id(d, sc)) == (d, sc)`. Exists so assemblers
    recover a schema id's owning data source and schema through this one
    definition instead of splitting on positional indices at the call
    site, keeping id-segment knowledge centralized here.

    Args:
        schema_id: A composite `{database}.{schema}` id (from
            `schema_id()`).

    Returns:
        A `(database, schema)` tuple.
    """
    database, schema = schema_id.split(".", 1)
    return database, schema


def pk(row: Any, table: str) -> str | tuple[str, ...]:
    """Return the primary key of `row` for `table`.

    For single-column PKs returns the bare attribute value (a str); for
    the composite-key tables returns a tuple of values, matching how
    the corpus and DbState dicts are keyed. This is the one definition
    of each table's key, shared by `corpus_assembly.assemble_corpus` and
    `db_io.read_db_state` so the two sides cannot drift apart.

    Args:
        row: A row dataclass instance for `table`.
        table: Main-table name; a key of `PRIMARY_KEY_COLUMNS`.

    Returns:
        The bare value for single-column PKs, or a tuple of values for
        the composite-key tables (`deployment_tables`,
        `table_relationships`, `column_mappings`).

    Raises:
        KeyError: If `table` is not a known main-table name.
    """
    cols = PRIMARY_KEY_COLUMNS[table]
    if len(cols) == 1:
        return getattr(row, cols[0])
    return tuple(getattr(row, c) for c in cols)
