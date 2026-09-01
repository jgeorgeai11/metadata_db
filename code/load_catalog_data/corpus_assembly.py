"""corpus_assembly.py — read YAML files and assemble a `Corpus`.

Each YAML file body holds *fields*; its identity (the data-source label,
schema, ...) is carried by the `PathIdentity` decoded in
`yaml_discovery.py`. This module's job is to combine the two — read each
YAML, build the right row dataclass, and accumulate everything into a
`Corpus`. Loader-managed fields (`insert_ts`, `update_ts`,
`column_mappings.target_tables_referenced`) are NOT populated here —
those live in `db_io.py` and the orchestrator.

Assembly is multi-file by construction: rows from every file of a type
union into one keyed dict with cross-file duplicate-PK detection naming
both files (originally built for `mappings/{name}.yaml`). The shard
folders (`tables/`, `columns/`, `table_relationships/`, `concepts/` —
the folder form of the four row-list types) therefore need no special
assembly handling: rows from multiple shards of one type union exactly
like rows from any other set of files, and per-row assemblers,
recognized-keys checks, and concept-id derivation are form-agnostic
(identity comes from the `PathIdentity`, never the shard filename).
The single-file and folder forms are mutually exclusive per (type,
scope), enforced here as a wave-1 rule (see `_shard_form_conflicts`).

Ids are venue-free. `data_catalog/systems.yaml` is a registry of venues; each
data source declares its residency in a sibling `deployments.yaml` that
is authored sparse (a bare venue entry means all schemas/tables under
their documented names) and expanded here into explicit table-grain
`deployment_tables` rows — expansion runs after tables assemble because
it needs the documented table inventory. (The file keeps the
`deployments` name and discovery label — it describes deployments
generally; only the assembled corpus attribute and DB table carry the
grain-named `deployment_tables`.)

Authoring errors are aggregated corpus-wide, the way validation already
aggregates its rules: `assemble_corpus` collects every discovery- and
assembly-stage issue (misplaced files, unparsable YAML, wrong document
shapes, bad rows, duplicate PKs, deployment-expansion issues, a data
source that deploys nowhere, a label colliding with a system name)
across the whole walk and raises them together in one `AssemblyError` —
a `ValidationError` subclass, so the orchestrator's existing handler
logs each issue unchanged. Granularity: a structurally broken file
(parse failure, wrong document shape) contributes one issue and its rows
are skipped; a well-formed file with bad rows contributes one issue per
bad row while good siblings still assemble. A complete `Corpus` is
returned only when no issue was found.
"""

import sys
from collections.abc import Hashable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# The vendored logging package (code/lib/logconfig) is resolved from
# this file's own location, so imports work from any working directory
# (not just the repo root) and CI never depends on untracked .claude/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import get_logger
from corpus_validation import ValidationError
from yaml_discovery import (
    RESERVED_CONCEPT_SEGMENT,
    PathIdentity,
    validate_identifier_segment,
)
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
    column_id,
    data_source_id,
    empty_corpus,
    pk,
    schema_id,
    schema_prefix,
    split_schema_id,
    table_id,
)

logger = get_logger(__name__)


class AssemblyError(ValidationError):
    """Aggregated discovery + assembly failure.

    Carries every path-classification and row-shape issue collected
    across the whole corpus walk, not just the first one. Subclassing
    `ValidationError` means the orchestrator's existing
    `except ValidationError` arm and per-issue logging work unchanged,
    while the inherited summary line names this stage (via
    `_SUMMARY_PREFIX`) so an assembly failure never reads as a
    validation failure. No import cycle: `corpus_validation` does not
    import `corpus_assembly`.

    Attributes:
        issues: List of human-readable issue strings.
    """

    _SUMMARY_PREFIX = "Corpus assembly failed"


# Recognized keys per file type. Each set is exactly the keys the
# matching `_assemble_*` reads from the body. A key outside its set is
# rejected instead of silently ignored, so a typo (`join_conditon:`)
# surfaces as an error rather than a null field. Ids are venue-free, so
# no file body carries a path-agreement `system` key any longer — the
# only `system` keys are body-derived values: the venue label on a
# `systems` registry entry and the target venue on a `deployments`
# entry. `columns` includes `is_primary_key`.
_RECOGNIZED_KEYS: dict[str, frozenset[str]] = {
    # Venue registry: one entry per queryable platform. `system` is the
    # body-derived venue label (charset-validated in `_assemble_system_row`).
    "systems": frozenset({"system", "description", "notes", "update_reason"}),
    "data_source": frozenset(
        {"owner", "description", "notes", "update_reason"}
    ),
    # Deployments: one entry per venue this data source is hosted in.
    # `database_name` renames the physical database; `schemas` is an
    # exhaustive subset/rename map (see `_assemble_deployment_entry`).
    # There are no `notes`/`update_reason` keys: venue entries carry only
    # residency facts (expanded `deployment_tables` rows are pure
    # physical facts — caveats go through concepts, rationale through
    # git via load_audit), so an entry still carrying them fails as an
    # unrecognized key.
    "deployments": frozenset({"system", "database_name", "schemas"}),
    "schema": frozenset({"description", "notes", "update_reason"}),
    "tables": frozenset(
        {"table_name", "description", "notes", "update_reason"}
    ),
    # `ref_table` is the optional domain pointer: the documented table
    # (a 3-segment dotted table id) that enumerates the column's value
    # domain. Shape-checked here; resolution is a wave-2 rule.
    "columns": frozenset(
        {
            "table_name",
            "column_name",
            "data_type",
            "is_nullable",
            "is_primary_key",
            "ref_table",
            "description",
            "notes",
            "update_reason",
        }
    ),
    # `join_type` is deliberately absent: it was removed in favor of
    # `cardinality`, so a YAML row still carrying `join_type:` fails
    # loudly as an unrecognized key instead of silently dropping the
    # field. There is no `system` key: venue validity is derived from
    # the endpoints' deployments.
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
    # There is no `target_system` key: the target dataset is identified
    # by the expression's own column references.
    "column_mappings": frozenset(
        {
            "source_column_id",
            "mapping_name",
            "target_expression",
            "use_when",
            "notes",
            "validated",
            "update_reason",
        }
    ),
    # concepts anchor to a data-source/schema path: the body `name` is
    # the id relative to that path, carrying the reserved `concept`
    # segment itself (`concept_id` = path prefix + `.` + `name`).
    "concepts": frozenset(
        {
            "name",
            "label",
            "definition",
            "notes",
            "related_object_ids",
            "update_reason",
        }
    ),
}

# Recognized keys inside one schema entry's mapping form within a
# `deployments.yaml` (`{schema}: {name:, tables:}`).
_DEPLOYMENT_SCHEMA_KEYS: frozenset[str] = frozenset({"name", "tables"})


def _check_recognized_keys(
    raw: dict[Any, Any], file_type: str, ident: PathIdentity
) -> None:
    """Reject any body key not recognized for this file type.

    Args:
        raw: The parsed body mapping (a whole-file body or one list row).
            Keys are typed `Any` because YAML does not require string
            keys (`true:`/`1:` parse as bool/int) — such keys are exactly
            what this function rejects.
        file_type: A key of `_RECOGNIZED_KEYS`.
        ident: The file's path identity, for the error message.

    Raises:
        ValueError: If `raw` carries a key outside the recognized set —
            a typo'd or misplaced key that would otherwise be dropped.
    """
    allowed = _RECOGNIZED_KEYS[file_type]
    unknown = set(raw) - allowed
    if unknown:
        # Sort by repr: YAML keys need not be strings (`true:`/`1:` parse
        # as bool/int), and a mixed-type `sorted(unknown)` would raise an
        # uncaught TypeError instead of recording this issue.
        raise ValueError(
            f"Unrecognized key(s) {sorted(unknown, key=repr)} in "
            f"{ident.path} "
            f"(file type {file_type!r}); recognized keys are "
            f"{sorted(allowed)}"
        )


def _check_optional_string_fields(
    raw: dict[Any, Any], ident: PathIdentity, *keys: str
) -> None:
    """Require each optional freeform field to be null or non-blank text.

    YAML happily types an unquoted freeform value (`update_reason:
    2024-01-01` parses as a date, `notes: true` as a bool). Such a value
    used to pass every wave of validation and fail only inside the
    post-merge write transaction — breaking dry-run parity — so it is
    rejected here, in the same wave-1 report as every other
    authoring-shape issue. Values are never coerced or trimmed: a
    non-null value must already be a string.

    A whitespace-only value (`update_reason: ""`, `notes: "   "`) is also
    rejected. It is an ambiguous second spelling of "absent" — the stored
    catalog gains a single spelling (NULL) — and, for `update_reason`,
    closes the hole where `""` would satisfy the update_reason
    discipline's (CONTRIBUTING.md wave 3) "an update names a reason"
    while carrying no reason. Authored non-blank values still load
    exactly as written (never trimmed).

    Args:
        raw: The parsed row mapping being assembled.
        ident: The file's path identity, for the error message.
        *keys: The optional freeform keys to check (`notes`,
            `update_reason`, `use_when`, `label`).

    Raises:
        ValueError: If any key holds a non-null, non-string value or a
            whitespace-only string; the message names the file, the key,
            and the row.
    """
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(
                f"`{key}` must be a string or null, got "
                f"{type(value).__name__} {value!r} — quote freeform "
                f"values so YAML does not retype them (in {ident.path}: "
                f"{raw!r})"
            )
        if not value.strip():
            raise ValueError(
                f"`{key}` must be null or carry non-whitespace content, "
                f"got {value!r} — a whitespace-only value is an ambiguous "
                f"second spelling of absent; omit the key or use null "
                f"(in {ident.path}: {raw!r})"
            )


class _UniqueKeyLoader(yaml.SafeLoader):
    """`SafeLoader` that rejects duplicate keys within one mapping.

    PyYAML's default mapping constructor silently keeps the last value
    when a mapping repeats a key (e.g. two `description:` lines in one
    row). Every other silent-drop channel in this module is closed
    deliberately (unrecognized keys, duplicate PKs), so a repeated key
    raises a `ConstructorError` — a `YAMLError`, which `load_yaml` wraps
    into the existing one-issue-per-file `ValueError` path.

    YAML merge keys (`<<:`) are rejected with a clear message rather
    than the cryptic "could not determine a constructor for the tag
    'tag:yaml.org,2002:merge'" they would otherwise produce: supporting
    them would require flattening before duplicate-key detection and
    reasoning about merged-row provenance in error messages, and the
    corpus convention is explicit fields.
    """

    def construct_mapping(
        self, node: yaml.nodes.MappingNode, deep: bool = False
    ) -> dict[Hashable, Any]:
        """Build the mapping, raising on any duplicated or merge key."""
        seen: set[Hashable] = set()
        for key_node, _value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found a YAML merge key ('<<:'), which is "
                    "unsupported — spell out each field explicitly",
                    key_node.start_mark,
                )
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, Hashable):
                # Fall through: super() raises PyYAML's standard
                # "found unhashable key" ConstructorError.
                continue
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep)


def load_yaml(path: Path) -> Any:
    """Parse a YAML file with a duplicate-key-rejecting safe loader.

    Args:
        path: YAML file on disk.

    Returns:
        Whatever safe loading returns — usually a `dict` or `list`,
        or `None` for an empty file.

    Raises:
        ValueError: If reading or parsing fails (including a mapping
            that repeats a key). The original `OSError` or `YAMLError`
            is chained via `from e` so the traceback is preserved.
    """
    try:
        with open(path, "rb") as f:
            return yaml.load(f, Loader=_UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(
            f"Failed to read or parse YAML at {path}: {e}"
        ) from e


def _require_mapping(doc: Any, ident: PathIdentity) -> dict[Any, Any]:
    """Coerce `doc` to a mapping or raise with the offending path.

    Args:
        doc: Parsed YAML document to validate.
        ident: Path identity used to report the offending file.

    Returns:
        The document unchanged, once confirmed to be a `dict`. Its keys
        are typed `Any`: YAML does not require string keys, and the
        non-string ones are rejected downstream by
        `_check_recognized_keys`.

    Raises:
        ValueError: If `doc` is not a mapping.
    """
    if not isinstance(doc, dict):
        raise ValueError(
            f"Expected a YAML mapping in {ident.path}, got "
            f"{type(doc).__name__}"
        )
    return doc


def _require_list(doc: Any, ident: PathIdentity) -> list[Any]:
    """Coerce `doc` to a list or raise with the offending path.

    Args:
        doc: Parsed YAML document to validate.
        ident: Path identity used to report the offending file.

    Returns:
        The document as a `list`; a `None` document (an empty
        list-form YAML file) is coerced to an empty list `[]`.

    Raises:
        ValueError: If `doc` is neither `None` nor a list.
    """
    if doc is None:
        # An empty list-form YAML file (`[]` or just the file header
        # comments) parses to None — treat as empty for convenience.
        return []
    if not isinstance(doc, list):
        raise ValueError(
            f"Expected a YAML list in {ident.path}, got "
            f"{type(doc).__name__}"
        )
    return doc


def _assemble_system_row(raw: Any, ident: PathIdentity) -> SystemRow:
    """Build one `SystemRow` from a `data_catalog/systems.yaml` list item.

    The venue registry is a single list file, so unlike the other
    single-row files a system is one entry among many. `system` is a
    body-derived label (not path-derived here), so its charset is
    validated where every other identifier segment is. `description` is
    required non-blank prose — an undescribed venue tells a consumer
    nothing (NOT NULL in the DDL is the backstop).

    Raises:
        ValueError: On any row-shape failure (non-mapping item,
            unrecognized key, missing/non-string/mis-charset `system`,
            missing/blank `description`).
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"Expected a mapping per system in {ident.path}, got "
            f"{type(raw).__name__}"
        )
    _check_recognized_keys(raw, "systems", ident)
    _check_optional_string_fields(raw, ident, "notes", "update_reason")
    system = raw.get("system")
    if not isinstance(system, str):
        raise ValueError(
            f"Missing or non-string `system` in {ident.path}: {raw!r}"
        )
    try:
        validate_identifier_segment(system, "system")
    except ValueError as e:
        raise ValueError(f"{e} (in {ident.path}: {raw!r})") from e
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            f"Missing or blank `description` — every system documents "
            f"what it is (in {ident.path}: {raw!r})"
        )
    return SystemRow(
        system=system,
        description=description,
        notes=raw.get("notes"),
        update_reason=raw.get("update_reason"),
    )


def _assemble_systems(
    doc: Any, ident: PathIdentity, issues: list[str]
) -> list[SystemRow]:
    """Build a `SystemRow` list from `data_catalog/systems.yaml`.

    A row failing a shape check records one issue in `issues` and is
    skipped; sibling rows still assemble.
    """
    items = _require_list(doc, ident)
    rows: list[SystemRow] = []
    for raw in items:
        try:
            rows.append(_assemble_system_row(raw, ident))
        except ValueError as e:
            issues.append(str(e))
    return rows


def _assemble_data_source(doc: Any, ident: PathIdentity) -> DataSourceRow:
    """Build the single `DataSourceRow` from a `data_source.yaml`.

    `owner` and `description` are both required (a missing or blank value
    is an issue): `owner` is the team accountable for the documentation
    and `description` is the prose the catalog is documentation for, so
    both match the `required in YAML? yes` legend in the shipped
    `data_source.yaml` headers. `data_source_id` is the path-derived
    label.

    Raises:
        ValueError: On a non-mapping body, an unrecognized key, or a
            missing/blank `owner` or `description`.
    """
    body = _require_mapping(doc, ident)
    _check_recognized_keys(body, "data_source", ident)
    _check_optional_string_fields(body, ident, "notes", "update_reason")
    assert ident.database_name is not None
    # Both required fields are checked before raising so a file missing
    # both is fixed in one round-trip, not two.
    problems: list[str] = []
    owner = body.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        problems.append(
            "Missing or blank `owner` — every data source names the team "
            "accountable for its documentation"
        )
    description = body.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append(
            "Missing or blank `description` — every data source documents "
            "what it is"
        )
    if problems:
        raise ValueError(
            f"{'; '.join(problems)} (in {ident.path}: {body!r})"
        )
    ds_id = data_source_id(ident.database_name)
    return DataSourceRow(
        data_source_id=ds_id,
        owner=owner,
        description=description,
        notes=body.get("notes"),
        update_reason=body.get("update_reason"),
    )


def _assemble_schema(doc: Any, ident: PathIdentity) -> SchemaRow:
    """Build the single `SchemaRow` from a `schema.yaml`.

    `description` is required non-blank prose (the catalog is
    documentation; NOT NULL in the DDL is the backstop).

    Raises:
        ValueError: On a non-mapping body, an unrecognized key, or a
            missing/blank `description`.
    """
    body = _require_mapping(doc, ident)
    _check_recognized_keys(body, "schema", ident)
    _check_optional_string_fields(body, ident, "notes", "update_reason")
    assert ident.database_name is not None
    assert ident.schema_name is not None
    description = body.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            f"Missing or blank `description` — every schema documents "
            f"what it is (in {ident.path}: {body!r})"
        )
    sch_id = schema_id(ident.database_name, ident.schema_name)
    ds_id = data_source_id(ident.database_name)
    return SchemaRow(
        schema_id=sch_id,
        data_source_id=ds_id,
        schema_name=ident.schema_name,
        description=description,
        notes=body.get("notes"),
        update_reason=body.get("update_reason"),
    )


def _assemble_table_row(raw: Any, ident: PathIdentity) -> TableRow:
    """Build one `TableRow` from a `tables.yaml` list item.

    Raises:
        ValueError: On any row-shape failure (non-mapping item,
            unrecognized key, missing/mis-typed or reserved
            `table_name`, missing/blank `description`); the message
            names the file and the row.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"Expected a mapping per table in {ident.path}, got "
            f"{type(raw).__name__}"
        )
    _check_recognized_keys(raw, "tables", ident)
    _check_optional_string_fields(raw, ident, "notes", "update_reason")
    table_name = raw.get("table_name")
    if not isinstance(table_name, str):
        raise ValueError(
            f"Missing or non-string `table_name` in {ident.path}: {raw!r}"
        )
    try:
        validate_identifier_segment(table_name, "table_name")
    except ValueError as e:
        raise ValueError(f"{e} (in {ident.path}: {raw!r})") from e
    # A table literally named `concept` would collide with the
    # reserved segment in a schema-level concept_id
    # ({database}.{schema}.concept.{name} vs. a column_id
    # {database}.{schema}.concept.{column}), so it is rejected.
    if table_name == RESERVED_CONCEPT_SEGMENT:
        raise ValueError(
            f"table_name {table_name!r} is reserved: it is the literal "
            f"segment used in a concept_id and would shadow the "
            f"concepts namespace in {ident.path}: {raw!r}"
        )
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            f"Missing or blank `description` — every table documents "
            f"what it is (in {ident.path}: {raw!r})"
        )
    assert ident.database_name is not None
    assert ident.schema_name is not None
    tbl_id = table_id(
        ident.database_name, ident.schema_name, table_name
    )
    sch_id = schema_id(ident.database_name, ident.schema_name)
    return TableRow(
        table_id=tbl_id,
        schema_id=sch_id,
        table_name=table_name,
        description=description,
        notes=raw.get("notes"),
        update_reason=raw.get("update_reason"),
    )


def _assemble_tables(
    doc: Any,
    ident: PathIdentity,
    issues: list[str],
    rejected_names: set[str],
) -> list[TableRow]:
    """Build a `TableRow` list from a `tables.yaml`.

    A row failing a shape check records one issue in `issues` and is
    skipped; sibling rows still assemble. When the rejected row's
    `table_name` is readable it is added to `rejected_names` so
    deployment expansion can suppress the phantom cascade a reduced
    inventory would otherwise produce (see `_WaveOneRejections`).
    """
    items = _require_list(doc, ident)
    rows: list[TableRow] = []
    for raw in items:
        try:
            rows.append(_assemble_table_row(raw, ident))
        except ValueError as e:
            issues.append(str(e))
            name = raw.get("table_name") if isinstance(raw, dict) else None
            if isinstance(name, str):
                rejected_names.add(name)
    return rows


def _assemble_column_row(raw: Any, ident: PathIdentity) -> ColumnRow:
    """Build one `ColumnRow` from a `columns.yaml` list item.

    Raises:
        ValueError: On any row-shape failure (non-mapping item,
            unrecognized key, missing/mis-typed
            `table_name`/`column_name`/`is_nullable`/`is_primary_key`,
            missing/blank `data_type`, missing/blank `description`, a
            non-string/blank/mis-shaped `ref_table`); the message names
            the file and the row.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"Expected a mapping per column in {ident.path}, got "
            f"{type(raw).__name__}"
        )
    _check_recognized_keys(raw, "columns", ident)
    _check_optional_string_fields(raw, ident, "notes", "update_reason")
    table_name = raw.get("table_name")
    column_name = raw.get("column_name")
    if not isinstance(table_name, str) or not isinstance(column_name, str):
        raise ValueError(
            f"`table_name` and `column_name` required in {ident.path}: "
            f"{raw!r}"
        )
    try:
        validate_identifier_segment(table_name, "table_name")
        validate_identifier_segment(column_name, "column_name")
    except ValueError as e:
        raise ValueError(f"{e} (in {ident.path}: {raw!r})") from e
    # A column literally named `concept` would collide with the reserved
    # segment in a table-level concept_id
    # ({database}.{schema}.{table}.concept.{name} vs. a column_id
    # {database}.{schema}.{table}.concept), so it is rejected — matching
    # the table-name guard in `_assemble_table_row`.
    if column_name == RESERVED_CONCEPT_SEGMENT:
        raise ValueError(
            f"column_name {column_name!r} is reserved: it is the literal "
            f"segment used in a concept_id and would shadow the "
            f"concepts namespace in {ident.path}: {raw!r}"
        )
    data_type = raw.get("data_type")
    is_nullable = raw.get("is_nullable")
    # data_type is strip-checked like description: `data_type: ""` would
    # otherwise load a column documented with no type at all.
    if not isinstance(data_type, str) or not data_type.strip():
        raise ValueError(
            f"Missing or blank `data_type` — every column documents its "
            f"type (in {ident.path}: {raw!r})"
        )
    if not isinstance(is_nullable, bool):
        raise ValueError(
            f"`is_nullable` (bool) required in {ident.path}: {raw!r}"
        )
    # Optional grain flag; defaults to False when omitted. Must be a
    # genuine bool (YAML `true`/`false`), not a truthy string.
    is_primary_key = raw.get("is_primary_key", False)
    if not isinstance(is_primary_key, bool):
        raise ValueError(
            f"`is_primary_key` must be a boolean (true/false) in "
            f"{ident.path}: {raw!r}"
        )
    # Optional domain pointer; null/absent means the column enumerates
    # nothing. Per the freeform-field conventions it must be a string or
    # null (never coerced), and a non-null value must be a 3-segment
    # dotted table id whose segments pass the identifier charset —
    # shape only; whether it names a documented table is a wave-2 rule
    # (corpus_validation._check_ref_tables).
    ref_table = raw.get("ref_table")
    if ref_table is not None:
        if not isinstance(ref_table, str):
            raise ValueError(
                f"`ref_table` must be a string or null, got "
                f"{type(ref_table).__name__} {ref_table!r} — quote the "
                f"value so YAML does not retype it (in {ident.path}: "
                f"{raw!r})"
            )
        segments = ref_table.split(".")
        if len(segments) != 3:
            raise ValueError(
                f"`ref_table` must be a 3-segment dotted table id "
                f"(database.schema.table), got {ref_table!r} "
                f"(in {ident.path}: {raw!r})"
            )
        for segment in segments:
            try:
                validate_identifier_segment(segment, "ref_table")
            except ValueError as e:
                raise ValueError(f"{e} (in {ident.path}: {raw!r})") from e
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            f"Missing or blank `description` — every column documents "
            f"what it is (in {ident.path}: {raw!r})"
        )
    assert ident.database_name is not None
    assert ident.schema_name is not None
    tbl_id = table_id(
        ident.database_name, ident.schema_name, table_name
    )
    col_id = column_id(tbl_id, column_name)
    return ColumnRow(
        column_id=col_id,
        table_id=tbl_id,
        column_name=column_name,
        data_type=data_type,
        is_nullable=is_nullable,
        is_primary_key=is_primary_key,
        description=description,
        notes=raw.get("notes"),
        update_reason=raw.get("update_reason"),
        ref_table_id=ref_table,
    )


def _assemble_columns(
    doc: Any, ident: PathIdentity, issues: list[str]
) -> list[ColumnRow]:
    """Build a `ColumnRow` list from a `columns.yaml`.

    A row failing a shape check records one issue in `issues` and is
    skipped; sibling rows still assemble.
    """
    items = _require_list(doc, ident)
    rows: list[ColumnRow] = []
    for raw in items:
        try:
            rows.append(_assemble_column_row(raw, ident))
        except ValueError as e:
            issues.append(str(e))
    return rows


def _assemble_table_relationship_row(
    raw: Any, ident: PathIdentity
) -> TableRelationshipRow:
    """Build one `TableRelationshipRow` from a list item.

    There is no `system` field: venue validity is derived from the
    endpoints' deployments (see
    `corpus_validation._check_relationship_codeployment`).

    Raises:
        ValueError: On any row-shape failure (non-mapping item,
            unrecognized key, missing/mis-typed required field,
            `table_a_id` outside the file's schema); the message names
            the file and the row.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"Expected a mapping per relationship in {ident.path}, got "
            f"{type(raw).__name__}"
        )
    _check_recognized_keys(raw, "table_relationships", ident)
    _check_optional_string_fields(
        raw, ident, "use_when", "notes", "update_reason"
    )
    for required in (
        "table_a_id",
        "table_b_id",
        "relationship_name",
        "join_condition",
    ):
        if not isinstance(raw.get(required), str):
            raise ValueError(
                f"Missing or non-string `{required}` in "
                f"{ident.path}: {raw!r}"
            )
    try:
        validate_identifier_segment(
            raw["relationship_name"], "relationship_name"
        )
    except ValueError as e:
        raise ValueError(f"{e} (in {ident.path}: {raw!r})") from e
    # table_a_id anchors the file — its {database}.{schema} prefix must
    # equal the file's schema, giving the relationship a single canonical
    # home. table_b_id may reach across schemas/data sources (its venue
    # co-deployment with table_a is checked in corpus_validation).
    assert ident.database_name is not None
    assert ident.schema_name is not None
    file_schema_id = schema_id(ident.database_name, ident.schema_name)
    table_a_schema = schema_prefix(raw["table_a_id"])
    if table_a_schema != file_schema_id:
        raise ValueError(
            f"relationship table_a_id {raw['table_a_id']!r} is not in "
            f"this file's schema {file_schema_id!r} in {ident.path} — a "
            f"relationship is authored from table_a's side"
        )
    # cardinality is optional (None = not yet recorded — never guessed
    # via a default); enum membership is validated in
    # corpus_validation._check_cardinality, but the shape is checked
    # here so a non-string surfaces with the file and row at hand.
    cardinality = raw.get("cardinality")
    if cardinality is not None and not isinstance(cardinality, str):
        raise ValueError(
            f"`cardinality` must be a string or null in "
            f"{ident.path}: {raw!r}"
        )
    validated = raw.get("validated", False)
    if not isinstance(validated, bool):
        raise ValueError(
            f"`validated` must be a boolean (true/false) in "
            f"{ident.path}: {raw!r}"
        )
    return TableRelationshipRow(
        table_a_id=raw["table_a_id"],
        table_b_id=raw["table_b_id"],
        relationship_name=raw["relationship_name"],
        join_condition=raw["join_condition"],
        cardinality=cardinality,
        use_when=raw.get("use_when"),
        notes=raw.get("notes"),
        validated=validated,
        update_reason=raw.get("update_reason"),
    )


def _assemble_table_relationships(
    doc: Any, ident: PathIdentity, issues: list[str]
) -> list[TableRelationshipRow]:
    """Build a `TableRelationshipRow` list from a `table_relationships.yaml`.

    A row failing a shape check records one issue in `issues` and is
    skipped; sibling rows still assemble.
    """
    items = _require_list(doc, ident)
    rows: list[TableRelationshipRow] = []
    for raw in items:
        try:
            rows.append(_assemble_table_relationship_row(raw, ident))
        except ValueError as e:
            issues.append(str(e))
    return rows


def _assemble_column_mapping_row(
    raw: Any, ident: PathIdentity, expected_prefix: str
) -> ColumnMappingRow:
    """Build one `ColumnMappingRow` from a `mappings/{name}.yaml` item.

    Args:
        raw: The list item (should be a mapping).
        ident: The file's path identity.
        expected_prefix: The file's `{database}.{schema}` folder prefix
            every `source_column_id` must live under.

    Raises:
        ValueError: On an unexpected item shape; a missing/invalid
            `source_column_id`, `mapping_name`, or `target_expression`
            key; or a `source_column_id` whose `{database}.{schema}`
            prefix does not match the file's folder path.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"Expected a mapping per column_mapping in {ident.path}, got "
            f"{type(raw).__name__}"
        )
    _check_recognized_keys(raw, "column_mappings", ident)
    _check_optional_string_fields(
        raw, ident, "use_when", "notes", "update_reason"
    )
    source_column_id = raw.get("source_column_id")
    if not isinstance(source_column_id, str):
        raise ValueError(
            f"Missing or non-string `source_column_id` in "
            f"{ident.path}: {raw!r}"
        )
    # Path-agreement: a mapping's source column must belong to the
    # schema folder the file lives in. The folder is authoritative and
    # the source schema is not stored separately, so a mismatch would
    # silently place a mapping under the wrong owner.
    source_prefix = schema_prefix(source_column_id)
    if source_prefix != expected_prefix:
        raise ValueError(
            f"source_column_id {source_column_id!r} is not under the "
            f"file's folder path {expected_prefix!r} — a mappings file "
            f"may only reference source columns from its own schema in "
            f"{ident.path}: {raw!r}"
        )
    mapping_name = raw.get("mapping_name")
    if not isinstance(mapping_name, str):
        raise ValueError(
            f"Missing or non-string `mapping_name` in "
            f"{ident.path}: {raw!r}"
        )
    try:
        validate_identifier_segment(mapping_name, "mapping_name")
    except ValueError as e:
        raise ValueError(f"{e} (in {ident.path}: {raw!r})") from e
    # `target_expression` may be explicitly null (intentional drop)
    # but the key itself must appear so authors don't silently omit.
    if "target_expression" not in raw:
        raise ValueError(
            f"`target_expression` key required (may be null) in "
            f"{ident.path}: {raw!r}"
        )
    target_expression = raw["target_expression"]
    if target_expression is not None and not isinstance(
        target_expression, str
    ):
        raise ValueError(
            f"`target_expression` must be a string or null in "
            f"{ident.path}: {raw!r}"
        )
    validated = raw.get("validated", False)
    if not isinstance(validated, bool):
        raise ValueError(
            f"`validated` must be a boolean (true/false) in "
            f"{ident.path}: {raw!r}"
        )
    return ColumnMappingRow(
        source_column_id=source_column_id,
        mapping_name=mapping_name,
        target_tables_referenced=(),
        target_expression=target_expression,
        use_when=raw.get("use_when"),
        notes=raw.get("notes"),
        validated=validated,
        update_reason=raw.get("update_reason"),
    )


def _assemble_column_mappings(
    doc: Any, ident: PathIdentity, issues: list[str]
) -> list[ColumnMappingRow]:
    """Build a `ColumnMappingRow` list from a `mappings/{name}.yaml`.

    Each row carries a required `mapping_name` (the PK discriminator, so a
    source column can map more than once) and an optional `use_when`
    (which mapping to prefer). There is no `target_system` — the target
    dataset is identified by the expression's own references. The source
    is fully identified by `source_column_id`, whose `{database}.{schema}`
    prefix must match the file's folder path so the folder location is
    authoritative. `target_tables_referenced` is left empty here — it's
    computed in the orchestrator after expression parsing.

    A row failing a shape check records one issue in `issues` and is
    skipped; sibling rows still assemble.
    """
    items = _require_list(doc, ident)
    rows: list[ColumnMappingRow] = []
    assert ident.database_name is not None
    assert ident.schema_name is not None
    expected_prefix = schema_id(ident.database_name, ident.schema_name)
    for raw in items:
        try:
            rows.append(
                _assemble_column_mapping_row(raw, ident, expected_prefix)
            )
        except ValueError as e:
            issues.append(str(e))
    return rows


# The deepest anchor a concept_id may compose: data source, schema,
# table, column. The catalog addresses nothing below a column, so a
# deeper anchor is rejected at authoring time with a named rule instead
# of dying as a raw CHECK violation at write time. The anchor is the
# labels before the authored `concept` segment: the file's path prefix
# plus the name's own anchor segments.
_MAX_CONCEPT_ANCHOR_LABELS = 4


def _validate_concept_name(
    name: str, raw: dict[str, Any], ident: PathIdentity, prefix: str
) -> None:
    """Validate a concept `name` against the authored-segment grammar.

    A `name` is the concept's id *relative to the file's anchor*, byte
    for byte: `[{table}[.{column}].]concept.{leaf}`. The author writes
    the reserved `concept` segment (the loader inserts nothing), so the
    name must carry exactly one `concept` segment, second-to-last —
    segments before it deepen the path-derived anchor (schema folder ->
    table -> column) and the final segment is the concept leaf.
    `v_clm.clm_type_cd.concept.claim_type_code` in a schema-scoped
    file anchors the concept `claim_type_code` at the `clm_type_cd`
    column; `concept.{leaf}` anchors at the file's own level.

    Args:
        name: The body `name` value (already known to be a string).
        raw: The list item, for error messages.
        ident: The file's path identity.
        prefix: The path-derived dotted anchor prefix the name's anchor
            segments extend.

    Raises:
        ValueError: On any name-rule failure — a leading, trailing, or
            doubled dot; a segment failing the identifier rules (the
            message names the failing segment and quotes the whole
            `name`); a missing reserved `concept` segment; a `concept`
            segment anywhere but second-to-last (as the leaf, at an
            anchor position, or duplicated — the message names the
            offending position); anchor segments in a
            data-source-scoped file; or a composed anchor deeper than
            `_MAX_CONCEPT_ANCHOR_LABELS` labels.
    """
    # Dot shape first: a stray dot yields empty segments, and "empty
    # segment" is a worse message than naming the actual defect.
    if name.startswith("."):
        raise ValueError(
            f"concept `name` {name!r} has a leading dot — a name is "
            f"dot-separated identifier segments with no leading, "
            f"trailing, or doubled dot (in {ident.path}: {raw!r})"
        )
    if name.endswith("."):
        raise ValueError(
            f"concept `name` {name!r} has a trailing dot — a name is "
            f"dot-separated identifier segments with no leading, "
            f"trailing, or doubled dot (in {ident.path}: {raw!r})"
        )
    if ".." in name:
        raise ValueError(
            f"concept `name` {name!r} has a doubled dot — a name is "
            f"dot-separated identifier segments with no leading, "
            f"trailing, or doubled dot (in {ident.path}: {raw!r})"
        )
    segments = name.split(".")
    for segment in segments:
        # Each segment is a single ltree label: charset, case, and
        # length are validated where every other identifier segment is,
        # surfacing a bad label rather than an ltree cast failure at
        # write time. The wrap names the failing segment (the inner
        # message) and quotes the whole `name`. The literal `concept`
        # is itself a legal label, so the reserved segment passes here
        # and its position is checked next.
        try:
            validate_identifier_segment(segment, "name")
        except ValueError as e:
            raise ValueError(
                f"{e} (in concept `name` {name!r} in {ident.path}: "
                f"{raw!r})"
            ) from e
    # Reserved-segment invariant: the author writes `concept` exactly
    # once, second-to-last, so the name separates its anchor segments
    # from its leaf visibly and `concept_id` = prefix + `.` + `name`
    # with nothing inserted. Positions are reported 1-based. The
    # schema/table/column shadow cases are guarded in
    # `yaml_discovery.decode_path`, `_assemble_tables`, and
    # `_assemble_column_row`.
    positions = [
        i
        for i, segment in enumerate(segments)
        if segment == RESERVED_CONCEPT_SEGMENT
    ]
    if not positions:
        raise ValueError(
            f"concept `name` {name!r} has no reserved "
            f"{RESERVED_CONCEPT_SEGMENT!r} segment — a name is the "
            f"concept's id relative to the file's folder, of the form "
            f"[{{table}}[.{{column}}].]concept.{{leaf}}, with "
            f"{RESERVED_CONCEPT_SEGMENT!r} written second-to-last "
            f"(in {ident.path}: {raw!r})"
        )
    if len(positions) > 1:
        raise ValueError(
            f"concept `name` {name!r} carries the reserved "
            f"{RESERVED_CONCEPT_SEGMENT!r} segment more than once "
            f"(segments {', '.join(str(p + 1) for p in positions)} of "
            f"{len(segments)}) — it appears exactly once, "
            f"second-to-last (in {ident.path}: {raw!r})"
        )
    (position,) = positions
    if position == len(segments) - 1:
        raise ValueError(
            f"concept `name` {name!r} has the reserved "
            f"{RESERVED_CONCEPT_SEGMENT!r} segment as its final "
            f"segment (the leaf position) — it belongs second-to-last, "
            f"before the leaf: [{{table}}[.{{column}}].]concept."
            f"{{leaf}} (in {ident.path}: {raw!r})"
        )
    if position != len(segments) - 2:
        raise ValueError(
            f"concept `name` {name!r} has the reserved "
            f"{RESERVED_CONCEPT_SEGMENT!r} segment at segment "
            f"{position + 1} of {len(segments)} (an anchor position) — "
            f"it belongs second-to-last, before the leaf: "
            f"[{{table}}[.{{column}}].]concept.{{leaf}} "
            f"(in {ident.path}: {raw!r})"
        )
    if len(segments) > 2 and ident.schema_name is None:
        # A data-source-scoped file could otherwise reach any object
        # under the source, making a granular concept legally authorable
        # in two places; a schema-scoped file is safe by construction
        # (its fixed prefix reaches only its own tables and columns).
        raise ValueError(
            f"concept `name` {name!r} carries anchor segments, but "
            f"this is a data-source-scoped concepts file — author "
            f"table- and column-anchored concepts in the schema's "
            f"folder ({{label}}/{{schema}}/concepts.yaml or a "
            f"{{label}}/{{schema}}/concepts/ shard) (in {ident.path}: "
            f"{raw!r})"
        )
    # Composed anchor depth: the path prefix's labels plus the name's
    # anchor segments (everything before `concept`). The catalog
    # addresses data source (1), schema (2), table (3), and column (4)
    # — nothing deeper exists to anchor to.
    anchor_labels = prefix.count(".") + 1 + len(segments) - 2
    if anchor_labels > _MAX_CONCEPT_ANCHOR_LABELS:
        raise ValueError(
            f"concept `name` {name!r} composes an anchor of "
            f"{anchor_labels} labels; the deepest anchor is a column "
            f"({_MAX_CONCEPT_ANCHOR_LABELS} labels: data source, "
            f"schema, table, column) (in {ident.path}: {raw!r})"
        )


def _assemble_concept_row(
    raw: Any, ident: PathIdentity, prefix: str
) -> ConceptRow:
    """Build one `ConceptRow` from an anchored `concepts.yaml` list item.

    Args:
        raw: The list item (should be a mapping).
        ident: The file's path identity.
        prefix: The path-derived dotted prefix (`{database}` or
            `{database}.{schema}`) the `concept_id` is composed from.

    Raises:
        ValueError: On any row-shape failure (see `_assemble_concepts`);
            the message names the file and the row.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"Expected a mapping per concept in {ident.path}, got "
            f"{type(raw).__name__}"
        )
    _check_recognized_keys(raw, "concepts", ident)
    _check_optional_string_fields(
        raw, ident, "label", "notes", "update_reason"
    )
    name = raw.get("name")
    if not isinstance(name, str):
        raise ValueError(
            f"Missing or non-string `name` in {ident.path}: {raw!r}"
        )
    # `name` is the concept's id relative to the file's anchor
    # (`[{table}[.{column}].]concept.{leaf}` — the author writes the
    # reserved `concept` segment), so `concept_id` is the path prefix
    # plus the literal name, byte for byte, with nothing inserted.
    _validate_concept_name(name, raw, ident, prefix)
    concept_id = f"{prefix}.{name}"
    # Required non-blank prose: a definition-less concept is a glossary
    # entry with nothing to look up (NOT NULL in the DDL is the backstop).
    definition = raw.get("definition")
    if not isinstance(definition, str) or not definition.strip():
        raise ValueError(
            f"Missing or blank `definition` — a concept is its "
            f"definition (in {ident.path}: {raw!r})"
        )
    # Optional authored links: null (absent) -> (), else a list of
    # strings coerced to a tuple preserving author order. Resolution
    # against the corpus happens in corpus_validation; the row-local
    # shape/duplicate/self-reference checks live here where the file
    # and row context are at hand.
    raw_related = raw.get("related_object_ids")
    if raw_related is None:
        related_object_ids: tuple[str, ...] = ()
    else:
        if not isinstance(raw_related, list):
            raise ValueError(
                f"`related_object_ids` must be a list of strings or "
                f"null in {ident.path}: {raw!r}"
            )
        seen_related: set[str] = set()
        for entry in raw_related:
            if not isinstance(entry, str):
                raise ValueError(
                    f"`related_object_ids` entries must be strings in "
                    f"{ident.path}: {raw!r}"
                )
            if entry == concept_id:
                raise ValueError(
                    f"`related_object_ids` entry {entry!r} references "
                    f"the row's own concept_id (self-reference) in "
                    f"{ident.path}: {raw!r}"
                )
            if entry in seen_related:
                raise ValueError(
                    f"duplicate `related_object_ids` entry {entry!r} "
                    f"in {ident.path}: {raw!r}"
                )
            seen_related.add(entry)
        related_object_ids = tuple(raw_related)
    return ConceptRow(
        concept_id=concept_id,
        label=raw.get("label"),
        definition=definition,
        notes=raw.get("notes"),
        related_object_ids=related_object_ids,
        update_reason=raw.get("update_reason"),
    )


def _assemble_concepts(
    doc: Any, ident: PathIdentity, issues: list[str]
) -> list[ConceptRow]:
    """Build a `ConceptRow` list from an anchored `concepts.yaml` file.

    A concept's identity is path-derived like every other file type: the
    body supplies a `name` that is the concept's id *relative to the
    file's anchor* — `[{table}[.{column}].]concept.{leaf}`, with the
    reserved `concept` segment written by the author — and `concept_id`
    is the file's `PathIdentity` prefix + `.` + `name`, byte for byte,
    yielding `{database}[.{schema}[.{table}[.{column}]]].concept.{leaf}`.
    `concept.{leaf}` anchors at the file's own level
    (`{database}.concept.{leaf}` for a data-source-level file,
    `{database}.{schema}.concept.{leaf}` for a schema-level file), while
    anchor segments before `concept` in a schema-level file anchor the
    concept at a table or column of that schema. Every segment is
    validated as a single ltree label, the reserved `concept` segment
    must appear exactly once, second-to-last, anchor segments are
    rejected in a data-source-scoped file (deeper-than-source anchors
    are authored in the schema's folder), and the composed anchor may
    not exceed 4 labels (see `_validate_concept_name`). `definition` is
    required non-blank prose — a concept is its definition.

    Each row may carry an optional `related_object_ids` — a list of
    dotted ids of the catalog objects the concept is about (retrieval
    anchors, resolved against the corpus in
    `corpus_validation._check_concept_related_objects`). It is coerced
    to a tuple preserving author order; duplicates within a row and a
    self-reference (an entry equal to the row's own `concept_id`) are
    rejected here where the row context is at hand.

    A row failing any of these shape checks records one issue in
    `issues` and is skipped; sibling rows still assemble.

    Args:
        doc: The parsed YAML document (a list of concept mappings).
        ident: The file's path identity (`database_name`, plus
            `schema_name` for a schema-level file).
        issues: Corpus-wide issue accumulator the row-shape failures are
            recorded into.

    Returns:
        The list of `ConceptRow` carried by the file (bad rows skipped).
    """
    items = _require_list(doc, ident)
    rows: list[ConceptRow] = []
    assert ident.database_name is not None
    # Data-source-level (schema_name is None) vs. schema-level prefix.
    if ident.schema_name is None:
        prefix = data_source_id(ident.database_name)
    else:
        prefix = schema_id(ident.database_name, ident.schema_name)
    for raw in items:
        try:
            rows.append(_assemble_concept_row(raw, ident, prefix))
        except ValueError as e:
            issues.append(str(e))
    return rows


# ---------------------------------------------------------------------------
# Deployments — authored sparse, stored expanded
# ---------------------------------------------------------------------------

# ds_id -> schema_name -> {table_name: table_id}. The documented table
# inventory each deployment entry expands against.
_TablesByDataSource = dict[str, dict[str, dict[str, str]]]


@dataclass
class _WaveOneRejections:
    """Schema/table rows of one data source rejected earlier in wave 1.

    A rejected `schema.yaml` or `tables.yaml` row shrinks the documented
    inventory that deployment expansion runs against, so without this
    record every deployment entry referencing the rejected object would
    also fail ("unknown table/schema", "expands to zero rows", "deploys
    nowhere") — a phantom cascade. The expansion consults this to keep
    the report at one defect, one issue: a cascade whose sole cause is
    the reduced inventory is suppressed (the original row issue already
    names the fix).

    Attributes:
        schemas: Schema names whose `schema.yaml` failed to assemble.
        tables: Rejected table names per schema (rows whose shape
            checks failed but whose `table_name` was readable).
        broken_table_files: Schemas whose whole `tables.yaml` — or any
            shard of a `tables/` folder — failed (parse error / wrong
            document shape), so part of the schema's table inventory is
            unknowable.
    """

    schemas: set[str] = field(default_factory=set)
    tables: dict[str, set[str]] = field(default_factory=dict)
    broken_table_files: set[str] = field(default_factory=set)

    def tables_reduced(self, schema_name: str) -> bool:
        """True when the schema's table inventory lost rejected rows."""
        return schema_name in self.broken_table_files or bool(
            self.tables.get(schema_name)
        )

    def schema_affected(self, schema_name: str) -> bool:
        """True when any rejection could make this schema look unknown."""
        return schema_name in self.schemas or self.tables_reduced(
            schema_name
        )

    def table_rejected(self, schema_name: str, table_name: str) -> bool:
        """True when this table could be unknown because of a rejection."""
        return schema_name in self.broken_table_files or (
            table_name in self.tables.get(schema_name, set())
        )

    def any_affected(self) -> bool:
        """True when any rejection reduced this data source's inventory."""
        return bool(
            self.schemas
            or self.broken_table_files
            or any(self.tables.values())
        )


def _index_tables_by_data_source(corpus: Corpus) -> _TablesByDataSource:
    """Group the assembled tables by data source and schema.

    Includes every documented schema (even one with no tables), so a bare
    deployment entry deploys the documented inventory exactly and an
    exhaustive `schemas:` key can be validated against the real schema set.
    """
    index: _TablesByDataSource = {}
    for sch in corpus.schemas.values():
        index.setdefault(sch.data_source_id, {}).setdefault(
            sch.schema_name, {}
        )
    for t in corpus.tables.values():
        database, schema_name = split_schema_id(t.schema_id)
        ds = data_source_id(database)
        index.setdefault(ds, {}).setdefault(schema_name, {})[
            t.table_name
        ] = t.table_id
    return index


def _deployment_row(
    tbl_id: str,
    system: str,
    ds_id: str,
    phys_db: str,
    phys_schema: str,
    phys_table: str,
) -> DeploymentRow:
    """Build one expanded table-grain `DeploymentRow`.

    Pure facts only — the identity, `data_source_id`, and the three
    physical names. There is nothing to inherit from the venue entry
    (its grammar carries only residency facts).
    """
    return DeploymentRow(
        table_id=tbl_id,
        system=system,
        data_source_id=ds_id,
        physical_database_name=phys_db,
        physical_schema_name=phys_schema,
        physical_table_name=phys_table,
    )


def _expand_schema_entry(
    schema_name: str,
    schema_val: Any,
    documented_tables: dict[str, str],
    ctx: dict[str, Any],
    issues: list[str],
) -> list[DeploymentRow]:
    """Expand one `{schema}: <value>` item of an exhaustive `schemas:` map.

    `schema_val` is either a physical-schema-name string (all tables
    under their documented names) or a mapping `{name:, tables:}` where
    `tables:` is an exhaustive table->physical-name subset. Explicit
    physical names are charset-validated here (lowercase `[a-z0-9_-]+`,
    per the deployment file rules — CONTRIBUTING.md wave 1) so they
    surface in the same wave-1 report as
    every other authoring-shape issue; defaulted names are already
    validated path/body segments. `ctx` carries the shared per-entry
    fields (`system`, `ds_id`, `phys_db`, `path`) plus the wave-1
    rejection record (`rejections`) and the suppressed-cascade
    accumulator (`suppressed_ds`). Unknown table keys, null/invalid
    physical names, and mis-typed values are recorded in `issues` (the
    offending item skipped) rather than raised, so one bad key does not
    mask siblings — except where the sole cause is a schema/table row
    already rejected in this wave, which is suppressed (one defect, one
    issue) and marked in `suppressed_ds`.
    """
    system = ctx["system"]
    rejections: _WaveOneRejections = ctx["rejections"]
    suppressed_ds: set[str] = ctx["suppressed_ds"]
    rows: list[DeploymentRow] = []

    def _row(tbl_id: str, phys_schema: str, phys_table: str) -> DeploymentRow:
        """Build one row for this entry, binding the shared venue context."""
        return _deployment_row(
            tbl_id,
            system,
            ctx["ds_id"],
            phys_db=ctx["phys_db"],
            phys_schema=phys_schema,
            phys_table=phys_table,
        )

    if schema_val is None:
        issues.append(
            f"deployment entry for system {system!r} in {ctx['path']}: "
            f"schema {schema_name!r} has a null physical name — explicit "
            f"physical names are required"
        )
        return rows
    if isinstance(schema_val, str):
        # An empty-string physical name is rejected here (with the
        # deployment-entry context) rather than deferred to a later
        # identifier-syntax validation issue, matching the null check
        # above and the mapping-form `name: ''` check below.
        if not schema_val:
            issues.append(
                f"deployment entry for system {system!r} in {ctx['path']}: "
                f"schema {schema_name!r} has an empty-string physical name — "
                f"explicit physical names are required"
            )
            return rows
        # Explicit physical names are lowercase [a-z0-9_-] like every
        # catalog name (the deployment file rules, CONTRIBUTING.md
        # wave 1) — checked in wave 1 so the issue lands in the same
        # report as the other shape checks.
        try:
            validate_identifier_segment(schema_val, "physical_schema_name")
        except ValueError as e:
            issues.append(
                f"deployment entry for system {system!r} in "
                f"{ctx['path']}: {e}"
            )
            return rows
        if not documented_tables:
            # Suppress when the empty inventory is itself a wave-1
            # casualty (this schema's table rows were rejected) — the
            # row issue already names the fix.
            if rejections.tables_reduced(schema_name):
                suppressed_ds.add(ctx["ds_id"])
                return rows
            issues.append(
                f"deployment entry for system {system!r} in {ctx['path']}: "
                f"schema {schema_name!r} has no documented tables, so it "
                f"expands to zero deployment rows — document at least one "
                f"table under this schema, or drop the schema from the entry"
            )
            return rows
        for table_name, tbl_id in documented_tables.items():
            rows.append(_row(tbl_id, schema_val, table_name))
        return rows
    if not isinstance(schema_val, dict):
        issues.append(
            f"deployment entry for system {system!r} in {ctx['path']}: "
            f"schema {schema_name!r} value must be a physical-name string "
            f"or a mapping with `name`/`tables`, got "
            f"{type(schema_val).__name__}"
        )
        return rows

    unknown = set(schema_val) - _DEPLOYMENT_SCHEMA_KEYS
    if unknown:
        issues.append(
            f"deployment entry for system {system!r} in {ctx['path']}: "
            f"schema {schema_name!r} has unrecognized key(s) "
            f"{sorted(unknown, key=repr)}; recognized keys are "
            f"{sorted(_DEPLOYMENT_SCHEMA_KEYS)}"
        )
        return rows
    if "name" in schema_val:
        phys_schema = schema_val["name"]
        if not isinstance(phys_schema, str) or not phys_schema:
            issues.append(
                f"deployment entry for system {system!r} in "
                f"{ctx['path']}: schema {schema_name!r} `name` must be a "
                f"non-empty physical-name string, got {phys_schema!r}"
            )
            return rows
        try:
            validate_identifier_segment(
                phys_schema, "physical_schema_name"
            )
        except ValueError as e:
            issues.append(
                f"deployment entry for system {system!r} in "
                f"{ctx['path']}: {e}"
            )
            return rows
    else:
        # Default the physical schema name to the documented name.
        phys_schema = schema_name

    if "tables" not in schema_val:
        # Whole schema, documented table names.
        if not documented_tables:
            # Same wave-1-casualty suppression as the string form above.
            if rejections.tables_reduced(schema_name):
                suppressed_ds.add(ctx["ds_id"])
                return rows
            issues.append(
                f"deployment entry for system {system!r} in {ctx['path']}: "
                f"schema {schema_name!r} has no documented tables, so it "
                f"expands to zero deployment rows — document at least one "
                f"table under this schema, or drop the schema from the entry"
            )
            return rows
        for table_name, tbl_id in documented_tables.items():
            rows.append(_row(tbl_id, phys_schema, table_name))
        return rows

    tables_map = schema_val["tables"]
    if not isinstance(tables_map, dict):
        issues.append(
            f"deployment entry for system {system!r} in {ctx['path']}: "
            f"schema {schema_name!r} `tables` must be a mapping of "
            f"table -> physical name"
        )
        return rows
    if not tables_map:
        # An explicit but empty `tables: {}` deploys nothing under this
        # schema — an authoring mistake, not a silent no-op.
        issues.append(
            f"deployment entry for system {system!r} in {ctx['path']}: "
            f"schema {schema_name!r} has an empty `tables` map, so it "
            f"expands to zero deployment rows — list at least one table, or "
            f"drop the schema from the entry"
        )
        return rows
    for table_key, table_val in tables_map.items():
        # Validate the explicit physical name FIRST. Its validity is
        # independent of whether table_key is a documented table, so a
        # wave-1-rejected table row must not suppress an independent invalid
        # physical name on the same deployment line — reporting both in one
        # wave saves an extra fix round-trip.
        phys_name_issue: str | None = None
        if not isinstance(table_val, str) or not table_val:
            phys_name_issue = (
                f"deployment entry for system {system!r} in "
                f"{ctx['path']}: table {table_key!r} under schema "
                f"{schema_name!r} has a null, non-string, or empty physical "
                f"name ({table_val!r}) — explicit physical names are "
                f"required"
            )
        else:
            try:
                validate_identifier_segment(table_val, "physical_table_name")
            except ValueError as e:
                phys_name_issue = (
                    f"deployment entry for system {system!r} in "
                    f"{ctx['path']}: {e}"
                )
        if phys_name_issue is not None:
            issues.append(phys_name_issue)
            continue
        if table_key not in documented_tables:
            # Suppress the phantom "unknown table" when the table's own
            # row was rejected earlier in this wave (or its whole
            # tables.yaml failed) — that issue already names the fix. The
            # physical name was validated above, so a real name defect is
            # never hidden by this suppression.
            if rejections.table_rejected(schema_name, table_key):
                suppressed_ds.add(ctx["ds_id"])
                continue
            issues.append(
                f"deployment entry for system {system!r} in "
                f"{ctx['path']}: unknown table {table_key!r} under schema "
                f"{schema_name!r} — not a documented table of this data "
                f"source"
            )
            continue
        rows.append(
            _row(documented_tables[table_key], phys_schema, table_val)
        )
    return rows


def _assemble_deployment_entry(
    raw: Any,
    ident: PathIdentity,
    ds_id: str,
    schemas: dict[str, dict[str, str]],
    seen_systems: set[str],
    issues: list[str],
    rejections: _WaveOneRejections,
    suppressed_ds: set[str],
) -> list[DeploymentRow]:
    """Expand one venue entry of a `deployments.yaml` into table rows.

    A bare entry (no `schemas:` key) deploys every documented schema and
    table under their documented names; `database_name` optionally renames
    the physical database (charset-checked here: explicit physical names
    are lowercase `[a-z0-9_-]+`, per the deployment file rules —
    CONTRIBUTING.md wave 1). An exhaustive
    `schemas:` map subsets and renames (see `_expand_schema_entry`). Each
    system may appear at most once per file. Unknown schema keys are
    recorded in `issues` — unless the schema was already rejected in this
    wave (its `schema.yaml`/`tables.yaml` rows failed), in which case the
    cascade is suppressed and `suppressed_ds` marks the data source (one
    defect, one issue).

    Raises:
        ValueError: On a structural failure that voids the whole entry
            (non-mapping item, unrecognized top-level key, missing/
            mis-typed/duplicate `system`, invalid `database_name`, or a
            non-mapping `schemas`).
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"Expected a mapping per deployment entry in {ident.path}, got "
            f"{type(raw).__name__}"
        )
    _check_recognized_keys(raw, "deployments", ident)
    system = raw.get("system")
    if not isinstance(system, str):
        raise ValueError(
            f"Missing or non-string `system` in {ident.path}: {raw!r}"
        )
    try:
        validate_identifier_segment(system, "system")
    except ValueError as e:
        raise ValueError(f"{e} (in {ident.path}: {raw!r})") from e
    if system in seen_systems:
        raise ValueError(
            f"system {system!r} appears more than once in {ident.path} — "
            f"a data source deploys to each venue at most once per file"
        )
    seen_systems.add(system)
    # Physical database name defaults to the documented database name
    # (the data-source label); an explicit `database_name` renames it and
    # must be a non-empty string.
    if "database_name" in raw:
        phys_db = raw["database_name"]
        if not isinstance(phys_db, str) or not phys_db:
            raise ValueError(
                f"`database_name` must be a non-empty string in "
                f"{ident.path}: {raw!r}"
            )
        # Explicit physical names are lowercase [a-z0-9_-] like every
        # catalog name (the deployment file rules, CONTRIBUTING.md
        # wave 1) — a wave-1 shape issue.
        try:
            validate_identifier_segment(phys_db, "physical_database_name")
        except ValueError as e:
            raise ValueError(f"{e} (in {ident.path}: {raw!r})") from e
    else:
        phys_db = ds_id
    ctx = {
        "system": system,
        "ds_id": ds_id,
        "phys_db": phys_db,
        "path": ident.path,
        "rejections": rejections,
        "suppressed_ds": suppressed_ds,
    }

    if "schemas" not in raw:
        # Bare (or database_name-only) entry: all documented schemas and
        # tables under their documented names.
        rows: list[DeploymentRow] = []
        for schema_name, tbls in schemas.items():
            for table_name, tbl_id in tbls.items():
                rows.append(
                    _deployment_row(
                        tbl_id,
                        system,
                        ds_id,
                        phys_db=phys_db,
                        phys_schema=schema_name,
                        phys_table=table_name,
                    )
                )
        if not rows:
            # A bare entry against a data source with no documented tables
            # (no schemas, or every schema empty) silently deploys nothing;
            # surface it with the entry context instead — unless the empty
            # inventory is itself a wave-1 casualty (schema/table rows
            # rejected earlier), which is suppressed as a cascade.
            if rejections.any_affected():
                suppressed_ds.add(ds_id)
            else:
                issues.append(
                    f"deployment entry for system {system!r} in "
                    f"{ident.path} expands to zero deployment rows — the "
                    f"data source has no documented tables to deploy; "
                    f"document schemas/tables first"
                )
        return rows

    schemas_map = raw["schemas"]
    if not isinstance(schemas_map, dict):
        raise ValueError(
            f"`schemas` must be a mapping of schema -> physical name (or "
            f"a `name`/`tables` mapping) in {ident.path}: {raw!r}"
        )
    if not schemas_map:
        # An explicit but empty `schemas: {}` map deploys nothing — an
        # authoring mistake, not a silent no-op (a bare entry, not
        # `schemas: {}`, is the way to deploy the whole documented set).
        issues.append(
            f"deployment entry for system {system!r} in {ident.path} has an "
            f"empty `schemas` map, so it expands to zero deployment rows — "
            f"omit the `schemas` key to deploy every documented schema, or "
            f"list at least one schema"
        )
        return []
    rows = []
    for schema_key, schema_val in schemas_map.items():
        if schema_key not in schemas:
            # Suppress the phantom "unknown schema" when the schema is
            # absent only because its own rows were rejected in this
            # wave — that issue already names the fix.
            if rejections.schema_affected(schema_key):
                suppressed_ds.add(ds_id)
                continue
            issues.append(
                f"deployment entry for system {system!r} in {ident.path}: "
                f"unknown schema {schema_key!r} — not a documented schema "
                f"of this data source"
            )
            continue
        rows.extend(
            _expand_schema_entry(
                schema_key, schema_val, schemas[schema_key], ctx, issues
            )
        )
    return rows


def _assemble_deployments(
    doc: Any,
    ident: PathIdentity,
    tables_by_ds: _TablesByDataSource,
    issues: list[str],
    data_sources_with_entries: set[str],
    rejections: _WaveOneRejections,
    suppressed_ds: set[str],
) -> list[DeploymentRow]:
    """Expand a data source's `deployments.yaml` into table-grain rows.

    Runs after tables assemble (it needs the documented table inventory).
    An entry failing a structural check records one issue in `issues` and
    is skipped; sibling entries still expand. Expansion failures whose
    sole cause is a schema/table row already rejected in this wave are
    suppressed (see `_WaveOneRejections`), with the data source marked in
    `suppressed_ds` so the whole-corpus "deploys nowhere" rule stays
    quiet too.

    A data source whose `deployments.yaml` carries at least one venue
    entry is recorded in `data_sources_with_entries`; combined with an
    empty documented inventory, the whole-corpus "deploys nowhere" rule
    uses it to tell "document schemas/tables first" apart from every
    other zero-deployment cause (absent/venue-less file, or entries that
    failed for their own recorded reasons).
    """
    items = _require_list(doc, ident)
    assert ident.database_name is not None
    ds_id = data_source_id(ident.database_name)
    if items:
        data_sources_with_entries.add(ds_id)
    schemas = tables_by_ds.get(ds_id, {})
    rows: list[DeploymentRow] = []
    seen_systems: set[str] = set()
    for raw in items:
        try:
            rows.extend(
                _assemble_deployment_entry(
                    raw,
                    ident,
                    ds_id,
                    schemas,
                    seen_systems,
                    issues,
                    rejections,
                    suppressed_ds,
                )
            )
        except ValueError as e:
            issues.append(str(e))
    return rows


def _shard_form_conflicts(files: list[PathIdentity]) -> list[str]:
    """Detect a (type, scope) authored in both single-file and folder form.

    The single `<type>.yaml` and the `<type>/` shard folder are mutually
    exclusive per scope (the schema level for `tables`/`columns`/
    `table_relationships`/schema-level `concepts`; the data-source level
    for data-source-level `concepts`): allowing both would invite
    split-brain authoring — some rows in the file, some in the folder,
    each form individually valid. One issue is recorded per offending
    (type, scope) pair, naming both the single file and the folder.
    File types with no folder form can never conflict (their
    `from_shard_folder` is always False).

    Args:
        files: Classified YAML files (from `discover_yaml_files`),
            assumed sorted by path so the folder attribution (the first
            shard's parent) is deterministic.

    Returns:
        One issue string per (type, scope) present in both forms, in
        sorted (file_type, database, schema) order.
    """
    # scope key -> representative path for each form. A scope has at most
    # one single-file path (the filename is fixed per scope); for the
    # folder form every shard shares one parent, so the first seen wins.
    _Scope = tuple[str, str | None, str | None]
    single_form: dict[_Scope, Path] = {}
    folder_form: dict[_Scope, Path] = {}
    for ident in files:
        scope = (ident.file_type, ident.database_name, ident.schema_name)
        if ident.from_shard_folder:
            folder_form.setdefault(scope, ident.path.parent)
        else:
            single_form.setdefault(scope, ident.path)
    issues: list[str] = []
    for scope in sorted(
        single_form.keys() & folder_form.keys(),
        key=lambda s: (s[0], s[1] or "", s[2] or ""),
    ):
        file_type, database, schema = scope
        at = (
            f"data source {database!r}"
            if schema is None
            else f"schema {database}.{schema}"
        )
        issues.append(
            f"both the single-file and folder forms of {file_type!r} are "
            f"present for {at}: {single_form[scope]} and the shard folder "
            f"{folder_form[scope]} — the two forms are mutually "
            f"exclusive; keep one (move the rows into the folder as a "
            f"shard, or fold the shards back into the single file)"
        )
    return issues


def assemble_corpus(
    files: list[PathIdentity],
    discovery_issues: list[str] | None = None,
) -> Corpus:
    """Read every YAML file and build the corpus, aggregating issues.

    Dispatches to the per-file-type assembler based on `file_type` and
    keys each row by its primary key via `data_model.pk`. A row-list
    type split across shard files (`columns/clm.yaml` +
    `columns/bene.yaml`) assembles identically to the equivalent single
    file: rows union into the same keyed dict, and a PK defined in two
    shards is a duplicate naming both files, exactly as across any other
    file pair. Instead of aborting on the first offender, authoring
    errors are collected across the whole corpus walk and raised
    together:
      - Form level: for a given (type, scope) the single `<type>.yaml`
        and the `<type>/` shard folder may not both be present — one
        issue per offending pair naming both paths (see
        `_shard_form_conflicts`).
      - File level: a YAML parse failure or a wrong document shape
        records one issue for the file and skips that file's rows;
        remaining files still process. The single-row file types
        (`data_source`, `schema`) behave like one-row lists. A broken
        shard in a `tables/` folder marks the schema's table inventory
        incomplete for deployment-expansion suppression
        (`_record_file_failure`), same as a broken single `tables.yaml`.
      - Row level: each bad row in a well-formed file records one issue
        and is skipped; sibling rows still assemble (which keeps
        cross-file duplicate-PK detection meaningful on survivors).
      - Deployments are expanded in a second pass, after all tables
        assemble, into explicit table-grain rows. Expansion failures
        whose sole cause is a schema/table row already rejected in this
        wave ("unknown table/schema", "expands to zero rows", "deploys
        nowhere") are suppressed — one defect, one issue.
      - Duplicate PKs — within a single file or across files — keep the
        first occurrence in the corpus dict; each later occurrence
        records an issue naming the key and both files.
      - Two whole-corpus rules run last: a `data_source_id` may not
        collide with a `systems` name (single-segment namespaces stay
        disjoint), and every data source must deploy somewhere.

    Args:
        files: Classified YAML files (from `discover_yaml_files`).
        discovery_issues: Path-classification issues collected by
            `discover_yaml_files`; they join the assembly issues in one
            report because a misplaced file and a malformed row are the
            same authoring class. Defaults to none.

    Returns:
        A populated `Corpus` — only when no issue was found.
        Loader-managed fields remain unset.

    Raises:
        AssemblyError: If any discovery or assembly issue was collected;
            carries every issue found across the whole walk (a
            `ValidationError` subclass).
    """
    corpus = empty_corpus()
    # Iterate in sorted path order so duplicate-PK "first occurrence"
    # attribution and the order of collected issues are deterministic across
    # machines (raw filesystem iteration order is not guaranteed).
    # discover_yaml_files already returns sorted order; sorting here makes the
    # guarantee intrinsic to assembly regardless of how `files` was built.
    files = sorted(files, key=lambda ident: ident.path)
    issues: list[str] = list(discovery_issues or [])
    # Wave-1 form rule: a (type, scope) may be authored as the single
    # file or the shard folder, never both. Checked up front from the
    # classified paths alone — the offending files still assemble below
    # so their other defects surface in the same report.
    issues.extend(_shard_form_conflicts(files))
    # Track where every key was first added so a duplicate (within a
    # single file or across files) records an issue naming both
    # occurrences instead of silently overwriting in the dict.
    first_seen: dict[str, dict[str | tuple[str, ...], Path]] = {
        t: {}
        for t in (
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
    }

    def _record(
        table: str, key: str | tuple[str, ...], source: PathIdentity
    ) -> bool:
        """Register `key`; False (recording an issue) on a duplicate."""
        first = first_seen[table].get(key)
        if first is not None:
            issues.append(
                f"Duplicate {table} PK {key!r} in {source.path} "
                f"(first occurrence in {first} kept)"
            )
            return False
        first_seen[table][key] = source.path
        return True

    # Deployments are assembled in a second pass (they need the tables),
    # so stash their (ident, doc) pairs while the first pass runs.
    deployment_files: list[tuple[PathIdentity, Any]] = []

    # Wave-1 schema/table rejections per data source — consumed by the
    # deployments pass to suppress phantom cascades (one defect, one
    # issue). See _WaveOneRejections.
    rejections_by_ds: dict[str, _WaveOneRejections] = {}

    def _rejections_for(database_name: str) -> _WaveOneRejections:
        """The (lazily created) rejection record for one data source."""
        return rejections_by_ds.setdefault(
            data_source_id(database_name), _WaveOneRejections()
        )

    def _record_file_failure(ident: PathIdentity) -> None:
        """Register a failed schema.yaml / tables file for suppression.

        A `tables`-typed failure marks the whole schema's table
        inventory incomplete whether the file is the single
        `tables.yaml` or one shard of a `tables/` folder — a broken
        shard makes the inventory just as unknowable, so
        deployment-expansion cascades are suppressed the same way.
        """
        if ident.database_name is None or ident.schema_name is None:
            return
        if ident.file_type == "schema":
            _rejections_for(ident.database_name).schemas.add(
                ident.schema_name
            )
        elif ident.file_type == "tables":
            _rejections_for(ident.database_name).broken_table_files.add(
                ident.schema_name
            )

    for ident in files:
        try:
            doc = load_yaml(ident.path)
        except ValueError as e:
            # A file that cannot parse yields no rows: one issue for the
            # file; remaining files still process.
            issues.append(str(e))
            _record_file_failure(ident)
            continue
        try:
            if ident.file_type == "systems":
                for sysrow in _assemble_systems(doc, ident, issues):
                    key = pk(sysrow, "systems")
                    if _record("systems", key, ident):
                        corpus.systems[key] = sysrow
            elif ident.file_type == "data_source":
                ds = _assemble_data_source(doc, ident)
                key = pk(ds, "data_sources")
                if _record("data_sources", key, ident):
                    corpus.data_sources[key] = ds
            elif ident.file_type == "deployments":
                deployment_files.append((ident, doc))
            elif ident.file_type == "schema":
                sc = _assemble_schema(doc, ident)
                key = pk(sc, "schemas")
                if _record("schemas", key, ident):
                    corpus.schemas[key] = sc
            elif ident.file_type == "tables":
                assert ident.database_name is not None
                assert ident.schema_name is not None
                rejected_names = (
                    _rejections_for(ident.database_name)
                    .tables.setdefault(ident.schema_name, set())
                )
                for tr in _assemble_tables(
                    doc, ident, issues, rejected_names
                ):
                    key = pk(tr, "tables")
                    if _record("tables", key, ident):
                        corpus.tables[key] = tr
            elif ident.file_type == "columns":
                for cr in _assemble_columns(doc, ident, issues):
                    key = pk(cr, "columns")
                    if _record("columns", key, ident):
                        corpus.columns[key] = cr
            elif ident.file_type == "table_relationships":
                for rel in _assemble_table_relationships(doc, ident, issues):
                    key = pk(rel, "table_relationships")
                    if _record("table_relationships", key, ident):
                        corpus.table_relationships[key] = rel
            elif ident.file_type == "column_mappings":
                for cm in _assemble_column_mappings(doc, ident, issues):
                    key = pk(cm, "column_mappings")
                    if _record("column_mappings", key, ident):
                        corpus.column_mappings[key] = cm
            elif ident.file_type == "concepts":
                for concept in _assemble_concepts(doc, ident, issues):
                    key = pk(concept, "concepts")
                    if _record("concepts", key, ident):
                        corpus.concepts[key] = concept
            else:  # pragma: no cover - exhaustive by Literal
                raise ValueError(f"Unknown file_type {ident.file_type}")
        except ValueError as e:
            # A wrong document shape (`_require_mapping`/`_require_list`)
            # or a bad single-row body: one issue for the file, and its
            # rows are skipped. Row-level failures inside list files are
            # already recorded by the per-row assemblers and do not
            # surface here.
            issues.append(str(e))
            _record_file_failure(ident)

    # Second pass: expand deployments against the assembled tables.
    tables_by_ds = _index_tables_by_data_source(corpus)
    # Data sources whose deployments.yaml carried at least one venue entry
    # — lets the "deploys nowhere" rule below name the right fix.
    data_sources_with_entries: set[str] = set()
    # Data sources for which a deployment-expansion cascade was suppressed
    # (its sole cause a wave-1 schema/table rejection): the "deploys
    # nowhere" rule stays quiet for them too.
    suppressed_ds: set[str] = set()
    for ident, doc in deployment_files:
        assert ident.database_name is not None
        try:
            for dep in _assemble_deployments(
                doc,
                ident,
                tables_by_ds,
                issues,
                data_sources_with_entries,
                _rejections_for(ident.database_name),
                suppressed_ds,
            ):
                key = pk(dep, "deployment_tables")
                if _record("deployment_tables", key, ident):
                    corpus.deployment_tables[key] = dep
        except ValueError as e:
            issues.append(str(e))

    # Whole-corpus rule: single-segment namespaces stay disjoint, so a
    # data source label may not equal a system name (both are looked up
    # in the union of id spaces by concept links).
    for ds_id in corpus.data_sources:
        if ds_id in corpus.systems:
            issues.append(
                f"data_source_id {ds_id!r} collides with a systems name — "
                f"data-source labels and system names share a single-segment "
                f"namespace and must be disjoint"
            )

    # Whole-corpus rule: every data source must deploy somewhere — a
    # missing deployments.yaml, or one that yields zero venues, documents
    # data that exists nowhere. The diagnostic names the right fix by
    # distinguishing the two causes: venue entries that expanded against
    # an empty documented inventory (fix: document schemas/tables) vs.
    # everything else — a missing/venue-less file, or entries that failed
    # for their own reasons against a populated inventory (the per-entry
    # issue already named the offending entry, so the generic "add or
    # fix" advice is the accurate one there).
    deployed = {
        dep.data_source_id for dep in corpus.deployment_tables.values()
    }
    for ds_id in corpus.data_sources:
        if ds_id not in deployed:
            if ds_id in suppressed_ds:
                # The zero-deployment state is a suppressed cascade of a
                # wave-1 schema/table rejection — that issue already
                # names the fix, so this rule stays quiet (one defect,
                # one issue).
                continue
            inventory_empty = not any(
                tables_by_ds.get(ds_id, {}).values()
            )
            if ds_id in data_sources_with_entries and inventory_empty:
                issues.append(
                    f"data source {ds_id!r} has no deployments — its "
                    f"deployments.yaml venue entries expanded against an "
                    f"empty documented inventory; document schemas/tables "
                    f"first so the entries have something to deploy"
                )
            else:
                issues.append(
                    f"data source {ds_id!r} has no deployments — every data "
                    f"source must be materialized in at least one venue "
                    f"(add or fix its deployments.yaml)"
                )

    if issues:
        raise AssemblyError(issues)
    logger.info(
        "Assembled corpus: "
        f"{len(corpus.systems)} systems, "
        f"{len(corpus.data_sources)} data_sources, "
        f"{len(corpus.schemas)} schemas, "
        f"{len(corpus.tables)} tables, "
        f"{len(corpus.columns)} columns, "
        f"{len(corpus.deployment_tables)} deployment_tables, "
        f"{len(corpus.table_relationships)} table_relationships, "
        f"{len(corpus.column_mappings)} column_mappings, "
        f"{len(corpus.concepts)} concepts"
    )
    return corpus
