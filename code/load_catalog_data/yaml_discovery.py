"""yaml_discovery.py — classify YAML files by location.

The loader source corpus is venue-free and lives under `data_catalog/`:

  - `data_catalog/systems.yaml`                                  — venue registry
  - `data_catalog/sources/{label}/data_source.yaml`
  - `data_catalog/sources/{label}/deployments.yaml`
  - `data_catalog/sources/{label}/concepts.yaml`                 (data-source anchors)
  - `data_catalog/sources/{label}/concepts/{stem}.yaml`          (data-source anchors, sharded)
  - `data_catalog/sources/{label}/{schema}/schema.yaml`
  - `data_catalog/sources/{label}/{schema}/tables.yaml`
  - `data_catalog/sources/{label}/{schema}/tables/{stem}.yaml`   (sharded)
  - `data_catalog/sources/{label}/{schema}/columns.yaml`
  - `data_catalog/sources/{label}/{schema}/columns/{stem}.yaml`  (sharded)
  - `data_catalog/sources/{label}/{schema}/table_relationships.yaml`
  - `data_catalog/sources/{label}/{schema}/table_relationships/{stem}.yaml` (sharded)
  - `data_catalog/sources/{label}/{schema}/concepts.yaml`        (schema/table/column anchors)
  - `data_catalog/sources/{label}/{schema}/concepts/{stem}.yaml` (schema/table/column anchors, sharded)
  - `data_catalog/sources/{label}/{schema}/mappings/{name}.yaml`

A concept anchors at any of four depths — data source, schema, table,
or column — but its file placement is unchanged: concepts files live
only at the source root and in a schema's folder, each as the single
`concepts.yaml` or a `concepts/` shard folder. The body `name` is the
id relative to the file's anchor and carries the reserved `concept`
segment itself (`concept_id` = path prefix + `.` + `name`, byte for
byte): `concept.{leaf}` anchors at the file's own level, and in a
schema-scoped file `{table}.concept.{leaf}` / `{table}.{column}.concept.{leaf}`
deepen the anchor (see `corpus_assembly._assemble_concepts`) — never
deeper file paths.

The four row-list types (`tables`, `columns`, `table_relationships`,
`concepts`) may each be authored as either the single `<type>.yaml` or a
`<type>/` folder of shard files — a `<type>/` folder is a split
`<type>.yaml`, mirroring the existing `mappings/{name}.yaml` pattern.
Shard filename stems are freeform grouping labels (charset-validated,
never decoded into an identity, not stored); the two forms are mutually
exclusive per (type, scope), enforced in `corpus_assembly`. The folder
names are therefore reserved as schema path segments (see
`_validate_schema_segment`).

Each file's *location* determines which main table it loads into and
which identifier segments come from the path (vs. the file body). Ids
carry no system name: `{label}` is the single-segment data-source label,
and composite ids are `{database}.{schema}.{table}.{column}` with
`{database}` = the label. The venue registry (`data_catalog/systems.yaml`) is a
single file at the corpus root rather than a per-system folder, and each
data source states its residency in a sibling `deployments.yaml`. This
module owns that classification and the identifier-syntax checks that
protect the dotted PKs from ambiguity.

Classification errors are aggregated, not fail-fast:
`discover_yaml_files` collects every path-classification issue across
the whole walk and returns them alongside the successfully classified
identities, so `corpus_assembly.assemble_corpus` can report them
together with its own row-shape issues in one `AssemblyError`.
Environment errors (a missing `{data_root}/sources/` root) stay
fail-fast — they mean the run is misconfigured, not that a file is
misauthored.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# The vendored logging package (code/lib/logconfig) is resolved from
# this file's own location, so imports work from any working directory
# (not just the repo root) and CI never depends on untracked .claude/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import get_logger

logger = get_logger(__name__)


# The 9 recognized file types correspond 1:1 to the 9 main tables in
# metadata_db. `systems` is the single venue-registry file at the corpus
# root (`data_catalog/systems.yaml`); every other type lives under
# `data_catalog/sources/{label}/`. `deployments` is a per-data-source file the
# loader expands into table-grain rows; `concepts` is anchored at the
# data-source or schema level (see `decode_path`). See
# `MAINTAINING.md#yaml-files`.
FileType = Literal[
    "systems",
    "data_source",
    "deployments",
    "schema",
    "tables",
    "columns",
    "table_relationships",
    "column_mappings",
    "concepts",
]


# Identifier segments must be lowercase `ltree` labels: only [a-z0-9_-].
# This is stricter than merely forbidding `.`/whitespace — it also
# rejects `$ @ % #` etc. — because the composed IDs are stored as `ltree`
# (see code/apply_ddl/ddl_catalog/0001_initial_schema.sql). Enforcing the charset
# here surfaces a bad identifier as a clear validation error instead of a
# cryptic ltree cast failure at write time. `.` is excluded (it is the
# ltree separator, and would break the dotted-PK decode). Uppercase is
# excluded because the target systems (SAS, Postgres, Snowflake) resolve
# unquoted identifiers case-insensitively while ltree ids are
# case-sensitive — case variation would mint spurious distinct ids for
# the same physical object, so lowercase is the canonical form.
_LABEL_RE = re.compile(r"[a-z0-9_-]+")

# Maximum length of a single ltree label. PostgreSQL caps an ltree label at
# 255 characters; an over-long segment would otherwise pass every wave-1
# shape check and die at INSERT with a raw ltree error. Enforcing it here
# fails wave 1 with a named file and segment instead.
_MAX_LABEL_LENGTH = 255


# The literal segment separating a concept's anchor from its leaf. It
# is authored in the body `name` (never inserted by the loader), exactly
# once, second-to-last, so `concept_id` = the file's path prefix + `.` +
# `name`, byte for byte, at every anchor depth:
#   {database}.concept.{leaf}                    (data-source anchor)
#   {database}.{schema}.concept.{leaf}           (schema anchor)
#   {database}.{schema}.{table}.concept.{leaf}   (table anchor)
#   {database}.{schema}.{table}.{column}.concept.{leaf} (column anchor)
# It keeps a `concept_id` self-describing and enables subtree queries
# (`{database}.*.concept.*`). Because it is reserved, a schema, table,
# or column literally named `concept` would shadow it and is rejected
# (see `decode_path`, `corpus_assembly._assemble_tables`, and
# `corpus_assembly._assemble_column_row`).
RESERVED_CONCEPT_SEGMENT = "concept"


# The literal directory segment that groups a schema's column-mapping
# files (`{label}/{schema}/mappings/{name}.yaml`). It is reserved at the
# data-source level: a `mappings/` folder directly under `{label}/` is the
# wrong depth (mappings belong under a schema), so it is rejected with a
# dedicated error rather than decoded as a schema literally named
# `mappings` — which would otherwise surface later as a confusing FK
# failure. Mirrors the reserved `concept` segment (see `decode_path`).
RESERVED_MAPPINGS_SEGMENT = "mappings"


# The four row-list types whose single `<type>.yaml` may instead be
# authored as a `<type>/` folder of shard files (folder-name -> the
# `FileType` its shards classify to). A `<type>/` folder is a split
# `<type>.yaml`: shards union at assembly, and the shard filename stem
# is a freeform grouping label (charset-validated like a `mappings/`
# stem, never decoded into an identity). `schema.yaml`,
# `data_source.yaml`, and `deployments.yaml` are deliberately absent —
# they are single-row or single-purpose files where sharding is
# meaningless, so a `schema/` or `deployments/` folder stays an
# unrecognized location.
_SHARD_FOLDER_TYPES: dict[str, FileType] = {
    "tables": "tables",
    "columns": "columns",
    "table_relationships": "table_relationships",
    "concepts": "concepts",
}


# Shard folders valid only under a schema. `concepts/` is excluded: it
# is also valid directly under `{label}/` (data-source-level concepts),
# so only these three get the dedicated wrong-depth error there.
_SCHEMA_ONLY_SHARD_FOLDERS = ("tables", "columns", "table_relationships")


@dataclass(frozen=True)
class PathIdentity:
    """The identity components of a YAML file inferred from its path.

    Ids are venue-free, so a `PathIdentity` carries no system: the
    venue registry (`data_catalog/systems.yaml`) needs no identity segments, and
    every other file lives under `data_catalog/sources/{label}/` carrying that
    data-source label as `database_name` (the `{database}` segment of
    every id beneath it). `schema_name` is set for schema-level files
    (and schema-level `concepts`); `database_name` is None only for the
    `systems` registry file. The `mappings/{name}.yaml` filename stem is
    a grouping label (charset-validated but not stored) — `mapping_name`
    comes from the file body, not the path.

    Attributes:
        file_type: Which of the 9 recognized file types this is.
        database_name: The owning data source's label (the `{database}`
            segment); None only for the `systems` registry file.
        schema_name: The owning schema's name (`general` for schemaless
            sources); None for `systems`, `data_source`, `deployments`,
            and data-source-level `concepts` files.
        path: The path on disk this identity was decoded from.
        from_shard_folder: True when the file is a shard inside a
            `<type>/` folder (the folder form of a row-list type)
            rather than the single `<type>.yaml`. Consumed by
            `corpus_assembly`'s mutual-exclusion rule — the two forms
            may not coexist for one (type, scope). Always False for the
            types that have no folder form.
    """

    file_type: FileType
    database_name: str | None
    schema_name: str | None
    path: Path
    from_shard_folder: bool = False


def validate_identifier_segment(value: str, kind: str) -> None:
    """Require an identifier segment to be a lowercase `ltree` label.

    Segments are joined with `.` to form composite PKs
    (`{database}.{schema_name}.{table_name}.{column_name}`) that are
    stored as `ltree`, so each must match `[a-z0-9_-]+` (lowercase
    letters, digits, underscore, hyphen). This rejects `.` (the ltree
    separator), whitespace, other punctuation (`$ @ % #`, …), and
    uppercase letters (the target systems resolve unquoted identifiers
    case-insensitively while ltree ids are case-sensitive, so lowercase
    is the canonical spelling). The error message takes one of two
    forms: if the value's only offense is uppercase letters, it says
    identifiers must be lowercase and names the lowercased form to use;
    otherwise it lists the offending characters (uppercase letters
    among them).

    Args:
        value: The candidate segment text.
        kind: Human-readable label for the segment kind (e.g.,
            `"data_source"`) used only in the error message.

    Raises:
        ValueError: If `value` is empty, longer than the 255-character
            ltree label limit, or contains a character outside the
            lowercase ltree-legal set.
    """
    if not value:
        raise ValueError(f"Empty {kind} segment is not allowed")
    # Length is checked before charset so an over-long (but otherwise legal)
    # label fails with the length message, not a charset one.
    if len(value) > _MAX_LABEL_LENGTH:
        raise ValueError(
            f"Invalid {kind} segment {value!r}: {len(value)} characters "
            f"exceeds the {_MAX_LABEL_LENGTH}-character ltree label limit"
        )
    if not _LABEL_RE.fullmatch(value):
        # If lowercasing alone would make the value legal, the author
        # merely mis-cased an otherwise-fine identifier — name the exact
        # form to use instead of listing "offending characters".
        if _LABEL_RE.fullmatch(value.lower()):
            raise ValueError(
                f"Invalid {kind} segment {value!r}: identifiers must be "
                f"lowercase (target systems resolve unquoted identifiers "
                f"case-insensitively; ltree ids are case-sensitive) — "
                f"use {value.lower()!r}"
            )
        bad = sorted({c for c in value if not re.fullmatch(r"[a-z0-9_-]", c)})
        raise ValueError(
            f"Invalid {kind} segment {value!r}: only [a-z0-9_-] is "
            f"allowed (lowercase ltree-legal labels); "
            f"offending character(s): {', '.join(repr(c) for c in bad)}"
        )


def _validate_schema_segment(schema_name: str) -> None:
    """Validate a schema path segment, rejecting the reserved words.

    A schema literally named `concept` would collide with the reserved
    segment in a data-source-level `concept_id`
    (`{database}.concept.{name}` vs. a `table_id`
    `{database}.concept.{table}`), so it is rejected here where the
    schema segment is first seen. The four shard-folder names (`tables`,
    `columns`, `table_relationships`, `concepts`) are likewise reserved:
    the folder grammar makes them ambiguous at the schema-segment
    position (`{label}/concepts/{stem}.yaml` is a data-source-level
    concepts shard, not a schema named `concepts`).

    Args:
        schema_name: The candidate schema path segment.

    Raises:
        ValueError: If `schema_name` is not a legal ltree label, is the
            reserved `concept` word, or is one of the reserved
            shard-folder names.
    """
    validate_identifier_segment(schema_name, "schema_name")
    if schema_name == RESERVED_CONCEPT_SEGMENT:
        raise ValueError(
            f"schema name {schema_name!r} is reserved: it is the literal "
            f"segment used in a concept_id "
            f"({{database}}[.{{schema}}].{RESERVED_CONCEPT_SEGMENT}."
            f"{{name}}) and would shadow the concepts namespace"
        )
    if schema_name in _SHARD_FOLDER_TYPES:
        raise ValueError(
            f"schema name {schema_name!r} is reserved: it is a "
            f"shard-folder name in the path grammar "
            f"({{label}}[/{{schema}}]/{schema_name}/{{stem}}.yaml), and a "
            f"schema with this name would make those paths ambiguous"
        )


# Filenames that classify a schema-level file (4 parts under sources/).
_SCHEMA_LEVEL_TYPES: dict[str, FileType] = {
    "schema.yaml": "schema",
    "tables.yaml": "tables",
    "columns.yaml": "columns",
    "table_relationships.yaml": "table_relationships",
    # Schema-level concepts: sources/{label}/{schema}/concepts.yaml.
    "concepts.yaml": "concepts",
}


def decode_path(path: Path, data_root: Path) -> PathIdentity:
    """Classify a YAML file by its location and return its identity.

    The venue registry is the single file `{data_root}/systems.yaml`;
    every other file lives under `{data_root}/sources/`, where the number
    and pattern of path segments after `sources/` determines the type:
      - `{label}/data_source.yaml`
      - `{label}/deployments.yaml`
      - `{label}/concepts.yaml`                (data-source-level concepts)
      - `{label}/concepts/{stem}.yaml`         (data-source level, sharded)
      - `{label}/{schema}/schema.yaml`
      - `{label}/{schema}/tables.yaml`
      - `{label}/{schema}/tables/{stem}.yaml`  (sharded)
      - `{label}/{schema}/columns.yaml`
      - `{label}/{schema}/columns/{stem}.yaml` (sharded)
      - `{label}/{schema}/table_relationships.yaml`
      - `{label}/{schema}/table_relationships/{stem}.yaml` (sharded)
      - `{label}/{schema}/concepts.yaml`       (schema-level concepts)
      - `{label}/{schema}/concepts/{stem}.yaml` (schema level, sharded)
      - `{label}/{schema}/mappings/{name}.yaml`

    A `concepts.yaml` at the data-source level (`{label}/`) is
    data-source-scoped (no `schema_name`); at the schema level
    (`{label}/{schema}/`) it is schema-scoped. `concepts.yaml` is a
    reserved filename: at any other depth (under `mappings/` or a
    non-concepts shard folder) it is a path error, never silently
    reinterpreted as a shard whose stem happens to be `concepts`.
    `mappings` is likewise a reserved directory segment: it is valid
    only one level below a schema (`{label}/{schema}/mappings/`), so a
    `mappings/` folder directly under `{label}/` is a dedicated path
    error rather than a schema named `mappings`.

    The four row-list types may each be authored as a `<type>/` folder
    of shard files instead of the single `<type>.yaml`; shards classify
    to the same `FileType` and identity fields as the single-file form,
    with `from_shard_folder` set. Shard filename stems are freeform
    grouping labels (charset-validated like a `mappings/` stem, never
    decoded into an identity). `tables/`, `columns/`, and
    `table_relationships/` folders live only under a schema — directly
    under `{label}/` they get a dedicated wrong-depth error (only
    `concepts/` is valid at the data-source level) — and the four folder
    names are reserved as schema segments (see
    `_validate_schema_segment`). Each `sources/` path segment (label,
    schema, mapping or shard stem) is validated as an identifier on the
    way through.

    Args:
        path: The YAML file on disk.
        data_root: The repository's `data_catalog/` directory.

    Returns:
        A populated `PathIdentity`.

    Raises:
        ValueError: If `path` is neither `{data_root}/systems.yaml` nor
            under `{data_root}/sources/`, or is under `sources/` but does
            not match one of the recognized shapes, or if any `sources/`
            segment fails identifier-syntax validation.
    """
    resolved = path.resolve()
    root = data_root.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as e:
        raise ValueError(
            f"YAML path {path} is not under data root {root}"
        ) from e
    return _decode_parts(relative.parts, path, root)


def _decode_parts(
    parts: tuple[str, ...], path: Path, root: Path
) -> PathIdentity:
    """Classify by the already-relativized path segments.

    The filesystem-independent core of `decode_path`: `parts` is the
    path relative to the corpus root, `path`/`root` appear only in the
    returned identity and error messages. Split out so the
    wrong-extension guard in `discover_yaml_files` can ask "would this
    name classify?" about a *virtual* `.yaml` spelling without
    resolving it on disk — on a case-insensitive (Windows) filesystem,
    resolving `tables.yaml` next to an existing `tables.YAML` folds back
    to the on-disk casing and would defeat the check.

    See `decode_path` for the grammar, returns, and raises.
    """
    # The venue registry: a single file at the corpus root.
    if parts == ("systems.yaml",):
        return PathIdentity(
            file_type="systems",
            database_name=None,
            schema_name=None,
            path=path,
        )

    # Everything else must live under `sources/`.
    if not parts or parts[0] != "sources":
        raise ValueError(
            f"YAML path {path} is not the venue registry "
            f"({root / 'systems.yaml'}) nor under the sources tree "
            f"({root / 'sources'}) (relative parts: {parts})"
        )

    # `rel` is the path *within* sources/: {label}/... .
    rel = parts[1:]
    filename = parts[-1]

    # `mappings` is a reserved directory segment that lives one level below
    # a schema (`{label}/{schema}/mappings/{name}.yaml`, where it is
    # rel[2]). A `mappings/` folder directly under `{label}/` (rel[1]) is
    # the wrong depth: it would otherwise decode as a schema literally
    # named `mappings` (for a schema-level filename) or fall through to a
    # generic "unrecognized location" error, either way surfacing later as
    # a confusing FK failure. Reject it here with the correct location,
    # mirroring the reserved `concept` segment and the wrong-depth
    # `concepts.yaml` handling below.
    if len(rel) >= 2 and rel[1] == RESERVED_MAPPINGS_SEGMENT:
        raise ValueError(
            f"mappings/ folder at the data-source level: {path} — a "
            f"mappings folder must live under a schema "
            f"({{label}}/{{schema}}/mappings/{{name}}.yaml), not directly "
            f"under {{label}}/ (relative parts: {parts})"
        )

    # A `tables/`, `columns/`, or `table_relationships/` shard folder
    # directly under `{label}/` is the wrong depth: those folders live
    # under a schema. Reject with the correct location rather than
    # falling through to the generic "unrecognized location" error,
    # mirroring the wrong-depth `mappings/` handling above. (This also
    # enforces the schema-name reservation at this position: the same
    # path could otherwise read as a schema literally named `columns`.)
    if len(rel) == 3 and rel[1] in _SCHEMA_ONLY_SHARD_FOLDERS:
        raise ValueError(
            f"{rel[1]}/ folder at the data-source level: {path} — a "
            f"{rel[1]} shard folder must live under a schema "
            f"({{label}}/{{schema}}/{rel[1]}/{{stem}}.yaml); only a "
            f"concepts/ folder is valid directly under {{label}}/ "
            f"(relative parts: {parts})"
        )

    if len(rel) == 2 and filename == "data_source.yaml":
        label, _ = rel
        validate_identifier_segment(label, "data_source")
        return PathIdentity(
            file_type="data_source",
            database_name=label,
            schema_name=None,
            path=path,
        )
    if len(rel) == 2 and filename == "deployments.yaml":
        label, _ = rel
        validate_identifier_segment(label, "data_source")
        return PathIdentity(
            file_type="deployments",
            database_name=label,
            schema_name=None,
            path=path,
        )
    if len(rel) == 2 and filename == "concepts.yaml":
        # Data-source-level concepts: {label}/concepts.yaml — no schema
        # segment, so schema_name stays None.
        label, _ = rel
        validate_identifier_segment(label, "data_source")
        return PathIdentity(
            file_type="concepts",
            database_name=label,
            schema_name=None,
            path=path,
        )
    # Data-source-level concepts shard folder: {label}/concepts/{stem}.yaml
    # — the folder form of {label}/concepts.yaml. Checked before the
    # schema-level shapes so it wins the ambiguity with a schema named
    # `concepts` (a reserved schema name for exactly this reason). The
    # stem is a freeform grouping label: validated as a segment, never
    # decoded into an identity.
    if len(rel) == 3 and rel[1] == "concepts":
        label, _, shard_file = rel
        validate_identifier_segment(label, "data_source")
        validate_identifier_segment(Path(shard_file).stem, "shard_file")
        return PathIdentity(
            file_type="concepts",
            database_name=label,
            schema_name=None,
            path=path,
            from_shard_folder=True,
        )
    if len(rel) == 3 and filename in _SCHEMA_LEVEL_TYPES:
        label, schema_name, name = rel
        validate_identifier_segment(label, "data_source")
        _validate_schema_segment(schema_name)
        return PathIdentity(
            file_type=_SCHEMA_LEVEL_TYPES[name],
            database_name=label,
            schema_name=schema_name,
            path=path,
        )
    # `concepts.yaml` is a reserved filename valid only at the data-source
    # (2-part) and schema (3-part) depths — which return above — and
    # inside a `concepts/` shard folder, where both readings are concepts
    # (the schema-level shard branch below classifies it). Reaching here
    # with any other parent (under `mappings/` or a non-concepts shard
    # folder) means it sits at an unsupported depth — a path error, not a
    # shard whose stem is spelled `concepts`, so a misplaced concepts
    # file can never silently load as another type's rows.
    # (`len(rel) < 2` guards the pathological `sources/concepts.yaml`,
    # which has no parent segment to inspect and is unsupported anyway.)
    if filename == "concepts.yaml" and (
        len(rel) < 2 or rel[-2] != "concepts"
    ):
        raise ValueError(
            f"concepts.yaml at an unsupported depth: {path} — a concepts "
            f"file must live at the data-source ({{label}}/) or schema "
            f"({{label}}/{{schema}}/) level, or inside a concepts/ shard "
            f"folder (relative parts: {parts})"
        )
    # Schema-level shard folders: {label}/{schema}/<type>/{stem}.yaml —
    # the folder form of the four row-list types. Shards classify to the
    # same FileType/identity as their single-file equivalent; the stem is
    # a freeform grouping label (validated, never decoded).
    if len(rel) == 4 and rel[2] in _SHARD_FOLDER_TYPES:
        label, schema_name, folder, shard_file = rel
        validate_identifier_segment(label, "data_source")
        _validate_schema_segment(schema_name)
        validate_identifier_segment(Path(shard_file).stem, "shard_file")
        return PathIdentity(
            file_type=_SHARD_FOLDER_TYPES[folder],
            database_name=label,
            schema_name=schema_name,
            path=path,
            from_shard_folder=True,
        )
    if len(rel) == 4 and rel[2] == RESERVED_MAPPINGS_SEGMENT:
        label, schema_name, _, mapping_file = rel
        validate_identifier_segment(label, "data_source")
        _validate_schema_segment(schema_name)
        # The filename stem is a grouping label (conventionally the
        # target dataset's label). It is charset-validated like any
        # segment but is not decoded into an identity — `mapping_name`
        # comes from the file body.
        validate_identifier_segment(Path(mapping_file).stem, "mapping_file")
        return PathIdentity(
            file_type="column_mappings",
            database_name=label,
            schema_name=schema_name,
            path=path,
        )

    raise ValueError(
        f"Unrecognized YAML location under data_catalog/sources/: {path} "
        f"(relative parts: {parts})"
    )


def discover_yaml_files(
    data_root: Path,
) -> tuple[list[PathIdentity], list[str]]:
    """Walk the corpus and classify every YAML file.

    Collects the venue registry (`{data_root}/systems.yaml`, if present)
    and every `.yaml` under `{data_root}/sources/`. Non-YAML files
    (`.gitkeep`, `README.md`, `.txt`, etc.) are silently ignored.
    Any `.yaml` whose location does not match one of the recognized
    shapes (a misplaced file, a bad segment charset, a reserved schema
    word, an unsupported `concepts.yaml` depth, a shard folder at the
    wrong depth — including a case-variant folder name like `Columns/`,
    which never matches the lowercase grammar) records one issue naming
    the offending path — and the walk continues, so an author with
    several misplaced files sees them all in one run. The caller
    (`corpus_assembly.assemble_corpus`) folds these issues into its own
    aggregated `AssemblyError`.

    A wrong YAML extension is an error, never a silent skip: a file at a
    recognized corpus location whose name matches a recognized stem but
    carries `.yml` or a case-variant extension (`.YAML`, `.Yml`, …)
    records one issue requiring the lowercase `.yaml` spelling. Without
    this, a mis-extensioned `concepts.yml` would yield a green pipeline
    while delete-by-absence removed its previously loaded rows. A
    mis-extensioned file at an unrecognized location stays ignored like
    any other non-YAML file.

    Args:
        data_root: The repository's `data_catalog/` directory.

    Returns:
        A `(identities, issues)` tuple: the `PathIdentity` list for
        every successfully classified YAML file, in sorted path order
        (deterministic across machines — raw filesystem walk order is not
        guaranteed), and the issue strings collected across the whole
        walk (each naming the offending path): classification issues in
        sorted path order, followed by wrong-extension issues in sorted
        path order — deterministic across machines.

    Raises:
        FileNotFoundError: If `{data_root}/sources/` does not exist —
            an environment error (wrong `data_root`), kept fail-fast
            rather than aggregated with authoring errors.
    """
    sources_root = data_root / "sources"
    if not sources_root.is_dir():
        raise FileNotFoundError(
            f"sources root not found: {sources_root}"
        )

    identities: list[PathIdentity] = []
    issues: list[str] = []
    # Walk the whole corpus root: this finds the venue registry
    # (`data_catalog/systems.yaml`), every file in the `data_catalog/sources/` tree, and
    # any *misplaced* `.yaml` elsewhere under `data_catalog/` (e.g. a stray file
    # under an old `systems/` folder at the corpus root) — `decode_path` classifies the
    # first two and records the rest as issues. Only `.yaml` (canonical
    # extension) is loaded; a `.yml`/case-variant spelling at a
    # recognized location is an issue (see below), never silently
    # skipped.
    # Sorted so both `identities` and the classification issues come out in
    # a deterministic path order regardless of the filesystem's raw walk
    # order — the docstring's ordering guarantee is intrinsic here, so
    # callers (and duplicate-PK "first occurrence" attribution downstream)
    # need not re-sort.
    all_files = sorted(p for p in data_root.rglob("*") if p.is_file())
    for path in (p for p in all_files if p.suffix == ".yaml"):
        try:
            identities.append(decode_path(path, data_root))
        except ValueError as e:
            # Aggregate rather than abort: every decode_path message
            # already names the offending path, so collecting the string
            # is enough for the combined report. The debug log lets a
            # log reader pinpoint each offender without waiting for the
            # caller's combined AssemblyError.
            logger.debug(f"Path classification issue: {e}")
            issues.append(str(e))
    # Wrong-extension guard: a `.yml` or case-variant (`.YAML`, `.Yml`,
    # …) file that *would* classify cleanly under the `.yaml` spelling
    # is a mis-extensioned corpus file. It must fail loudly: silently
    # ignoring it turns a rename typo into delete-by-absence data loss.
    # One that would not classify is ignored like any other non-YAML
    # file (`.md`, `.txt`).
    root = data_root.resolve()
    for path in all_files:
        if path.suffix == ".yaml" or path.suffix.lower() not in (
            ".yml",
            ".yaml",
        ):
            continue
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:  # pragma: no cover - rglob stays under root
            continue
        # Classify the virtual `.yaml` spelling by parts (not by a real
        # path): resolving a candidate that differs only in extension
        # case would fold back to the on-disk name on Windows.
        candidate_parts = relative.parts[:-1] + (
            Path(relative.name).stem + ".yaml",
        )
        try:
            _decode_parts(candidate_parts, path, root)
        except ValueError:
            continue
        logger.debug(f"Wrong YAML extension: {path}")
        issues.append(
            f"Wrong YAML extension: {path} — corpus files must use the "
            f"lowercase `.yaml` extension (rename to "
            f"{path.with_suffix('.yaml').name}); until then the file is "
            f"not loaded, and delete-by-absence would remove its "
            f"previously loaded rows"
        )
    logger.info(
        f"Discovered {len(identities)} YAML file(s) under {data_root} "
        f"({len(issues)} classification issue(s))"
    )
    return identities, issues
