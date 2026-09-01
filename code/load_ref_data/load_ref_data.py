"""load_ref_data.py — validate and load the curated ref-schema code sets.

For each `data_ref/<schema>/<table>.csv` (the git-versioned source of
truth, one CSV per ref table inside its documented schema's folder,
header = the table's columns), the loader validates the CSV against the
live table and the corpus docs, then truncates and reloads every table
inside one transaction. The loader is deliberately dumb — validate,
truncate, reload — because git/MR review of the CSVs is the change
discipline: MR review replaces `update_reason`, and git history
replaces `_hstry`. There is no diffing and no row history.

Discovery is structural: CSVs live at exactly
`data_ref/<documented_schema>/<table>.csv`. A CSV directly under
`data_ref/`, a CSV nested deeper than one folder, or a folder that does
not name a documented schema of the ref source is an error, and two
CSVs sharing a stem across folders is a hard error — the folders map
into one physical namespace, so last-one-wins is never acceptable.

Validations (per CSV, all accumulated before any write):
  * the filename stem resolves to a table in the configured ref schema;
  * the header equals the live table's columns (ordered, per
    information_schema ordinal position) — with the corpus docs gate below,
    this makes docs == CSV == DDL mechanically guaranteed for ref;
  * the table has a primary key (a curated code set without one is
    always a mistake) and primary-key values are unique across rows;
  * every value parses to its column's type (an empty cell means NULL and
    is only legal on a nullable column);
  * the row count stays within the configured `max_rows_per_table`
    guardrail (a config knob — curated sets only; open-ended domains like
    NDC/ICD are data, documented the ordinary way);
  * the documented corpus columns for the table (from the ref source's
    `columns.yaml` files under `docs_dir`) equal the CSV header — ref is
    the one source where docs-vs-reality drift is mechanically
    preventable, so it fails loudly here.

Drift detection is BIDIRECTIONAL (load, `--dry-run`, and `--check`): beyond
the per-CSV checks above, every ref-schema table (infra tables excluded)
with no matching `data_ref/<schema>/<table>.csv`, and every documented
ref-source table with no CSV, is a loud issue — so docs == CSV == DDL
holds in both directions, not just for the tables that happen to have
CSVs.

CSV bytes are read ONCE per file: the SHA-256 recorded in `ref_load_audit`
is computed from the exact bytes that were validated and loaded (no
second read, no TOCTOU), hashing the raw bytes as-is (line endings and
all — the hash describes what was loaded, not a normalization of it).
Files are decoded as `utf-8-sig` so an Excel-saved BOM cannot corrupt the
first header cell into a cryptic header mismatch.

Modes:
  * default — validate everything, then truncate-and-reload every table in
    one transaction, appending one `ref_load_audit` row per table (CSV
    content hash, row count) inside the same transaction.
  * `--dry-run` — run every validation and stop before the truncate;
    read-only, runnable under the RO role (what the pre-merge
    `validate_ref_data` CI job invokes so a malformed CSV cannot merge).
    Three repeatable, dry-run-only escape hatches cover the pre-merge
    window where the DB legitimately lags the MR (CI computes each from
    the MR's own diff; all are rejected outside `--dry-run` — a real
    load or `--check` must never skip a drift or missing-table error):
      - `--allow-missing-table <name>` (new-table MRs): the table's
        missing-from-DB error downgrades to a warning and its CSV
        validates against the documented shape instead of the live
        columns (its ref migration applies post-merge);
      - `--allow-reshaped-table <name>` (column-altering MRs): the named
        EXISTING table validates against the documented shape instead of
        the live columns (its reshape migration applies post-merge);
      - `--allow-dropped-table <name>` (table-retiring MRs): the named
        table's DB-table-without-CSV drift issue downgrades to a warning
        (its drop migration applies post-merge).
    Docs-shape validation is full-strength, not a shallow fallback:
    header equals the documented columns, values parse per the
    documented `data_type` (which must come from the loader's parser
    vocabulary — see DOCS_TYPE_VOCABULARY), empty cells only on
    documented nullable columns, and PK uniqueness over the documented
    key columns.
  * `--check` — freshness drift detection (mirrors `apply_ddl --check`):
    compare each CSV's content hash against the latest `ref_load_audit`
    row (ordered by loaded_ts with the audit_id PK as tiebreaker) and
    exit non-zero naming any table that is stale, never loaded, missing
    from the DB, or CSV-less (bidirectional drift); read-only, writes
    nothing.

There is deliberately no concurrency lock: concurrent truncate-and-reload
runs self-serialize through TRUNCATE's ACCESS EXCLUSIVE table locks and
are idempotent for identical inputs — the catalog loader's read-then-write
race does not exist here.

Connection host/port/user/password come from `.env` (`POSTGRES_*`,
maintainer credentials for a real load; the RO role suffices for
`--dry-run`/`--check`); the database/schema and directory knobs live in
`code/load_ref_data/config/load_ref_data.toml`.
"""

import argparse
import csv
import hashlib
import io
import sys
import tomllib
from datetime import date, datetime
from pathlib import Path
from typing import Any, NamedTuple

import psycopg2
from psycopg2 import sql
import yaml

# The vendored packages under code/lib (logconfig, pgconn) are resolved
# from this file's own location, so imports work from any working
# directory (not just the repo root) and CI never depends on untracked
# .claude/. Sharing pgconn (rather than keeping a local copy for
# self-containment) is fine here: code/lib is the sanctioned shared
# layer, not a peer tool's module. ENV_VARS is re-exported for the unit
# tests' env setup.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import setup_logging, get_logger
from pgconn import ENV_VARS, connection_kwargs  # noqa: F401 (re-export)

logger = get_logger(__name__)


# Default for the curated-set guardrail when the config omits the knob.
# Never used as a hardcoded comparison — the effective value always flows
# from the config (see `run`).
DEFAULT_MAX_ROWS_PER_TABLE = 1000

# The one lowercase extension ref CSVs may use; a case-variant (`.CSV`)
# is a naming mistake, not a silent skip (mirrors apply_ddl's migration
# extension guard).
CSV_SUFFIX = ".csv"

# Tables in the reference schema that are loader infrastructure, not
# code sets: no CSV backs them and the docs gate skips them.
INFRA_TABLES = frozenset({"ref_load_audit", "ddl_versions"})

# information_schema data_type -> parser proving a CSV cell is loadable.
# Values are inserted as text and cast by Postgres; these parsers give the
# pre-write validation parity with what the INSERT would accept. On the
# live path, types not listed fall back to "any text is fine"; on the
# docs-shape path the documented type must be in DOCS_TYPE_VOCABULARY.
_TYPE_PARSERS: dict[str, Any] = {
    "integer": int,
    "bigint": int,
    "smallint": int,
    "numeric": float,
    "real": float,
    "double precision": float,
    "date": date.fromisoformat,
    "timestamp with time zone": datetime.fromisoformat,
    "timestamp without time zone": datetime.fromisoformat,
    "boolean": lambda v: {
        "true": True, "false": False, "t": True, "f": False,
        "1": True, "0": False,
    }[v.lower()],
}

# The documented `data_type` vocabulary for the docs-shape path: the
# parser types plus `text` (the pass-through type — any string loads).
# Ref docs are a machine contract, unlike the freeform `data_type` prose
# acceptable elsewhere in the corpus: a documented type outside this set
# cannot be validated pre-merge, so it is an explicit issue rather than
# a silent anything-goes fallback.
DOCS_TYPE_VOCABULARY = frozenset(_TYPE_PARSERS) | {"text"}


class DocColumn(NamedTuple):
    """One documented ref column — the docs-shape contract for a table.

    Attributes:
        name: The documented `column_name`.
        data_type: The documented `data_type` (must be in
            `DOCS_TYPE_VOCABULARY` for docs-shape validation to run).
        is_nullable: The documented `is_nullable` (an empty CSV cell is
            only legal when True).
        is_primary_key: The documented `is_primary_key` (False when the
            docs omit the key).
    """

    name: str
    data_type: str
    is_nullable: bool
    is_primary_key: bool


class TableDocs(NamedTuple):
    """A ref table's documented shape plus its documented schema.

    Attributes:
        schema: The documented schema folder the table's docs live under
            (spells the table's `data_ref/<schema>/<table>.csv` path in
            drift messages).
        columns: The documented columns in author order.
    """

    schema: str
    columns: list[DocColumn]


def compute_csv_sha256(data: bytes) -> str:
    """Return the SHA-256 of a ref CSV's exact bytes.

    Hashes the raw bytes as-is (no newline normalization): the recorded
    hash describes the exact file content that was validated and loaded,
    matching the load semantics — the CSV parser, not the hash, is what
    interprets line endings. Callers hash the same bytes they parse
    (single read), so the audit hash can never describe a different file
    state than the one loaded.

    Args:
        data: The CSV file's raw bytes.

    Returns:
        Hex-encoded SHA-256 of `data`.
    """
    return hashlib.sha256(data).hexdigest()


def list_csv_files(csv_dir: Path, documented_schemas: set[str]) -> list[Path]:
    """Discover the ref CSVs at `data_ref/<documented_schema>/<table>.csv`.

    The structure is enforced at the schema level, strictly: every CSV
    lives exactly one folder deep, and the folder must name a documented
    schema of the ref source — the folder level is semantic (the tree is
    self-describing, and it anchors a future where different documented
    schemas target different physical schemas). Stems are globally
    unique across folders because they all map into one physical
    namespace.

    Args:
        csv_dir: The ref data directory (`data_ref/`).
        documented_schemas: Valid schema-folder names (from
            `documented_schemas`, the ref source's docs folders).

    Returns:
        Sorted list of CSV paths.

    Raises:
        FileNotFoundError: If `csv_dir` doesn't exist.
        ValueError: If a CSV sits directly under `csv_dir` or nested
            deeper than one folder, a folder doesn't name a documented
            schema, two CSVs share a stem, a file uses a case-variant of
            the `.csv` extension (it would silently be skipped on a
            case-sensitive filesystem), or no CSVs exist at all (a wrong
            `csv_dir` must not read as "nothing to load").
    """
    logger.debug(f"Scanning {csv_dir} for ref CSVs")
    if not csv_dir.is_dir():
        raise FileNotFoundError(f"CSV directory not found: {csv_dir}")
    files: list[Path] = []
    seen_stems: dict[str, Path] = {}
    for entry in sorted(csv_dir.iterdir()):
        if entry.is_file():
            if entry.suffix.lower() == CSV_SUFFIX:
                raise ValueError(
                    f"Ref CSV directly under {csv_dir}: {entry.name} — "
                    f"CSVs live at exactly "
                    f"data_ref/<documented_schema>/<table>.csv (move it "
                    f"into its schema folder)"
                )
            continue  # non-CSV stray files are ignored, as before
        if entry.name not in documented_schemas:
            raise ValueError(
                f"{entry} does not name a documented schema of the ref "
                f"source — every data_ref/ folder must match a schema "
                f"folder under the ref docs (valid schema folders: "
                f"{sorted(documented_schemas)})"
            )
        for path in sorted(entry.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix
            if suffix != CSV_SUFFIX and suffix.lower() == CSV_SUFFIX:
                raise ValueError(
                    f"Ref CSV has a non-lowercase extension: {path.name}. "
                    f"Rename it to use the lowercase '{CSV_SUFFIX}' "
                    f"extension."
                )
            if suffix != CSV_SUFFIX:
                continue
            if path.parent != entry:
                raise ValueError(
                    f"Ref CSV nested deeper than one schema folder: "
                    f"{path} — CSVs live at exactly "
                    f"data_ref/<documented_schema>/<table>.csv"
                )
            first = seen_stems.get(path.stem)
            if first is not None:
                raise ValueError(
                    f"Duplicate ref CSV stem {path.stem!r}: {first} and "
                    f"{path} — stems are globally unique across schema "
                    f"folders (they map into one physical namespace, so "
                    f"last-one-wins is never acceptable)"
                )
            seen_stems[path.stem] = path
            files.append(path)
    if not files:
        raise ValueError(
            f"No {CSV_SUFFIX} files found in {csv_dir} — check the "
            f"csv_dir config knob"
        )
    logger.info(f"Found {len(files)} ref CSV(s) in {csv_dir}")
    return sorted(files)


def read_csv(path: Path) -> tuple[list[str], list[list[str]], str]:
    """Read a ref CSV once: header, data rows, and the bytes' SHA-256.

    Reads the file's bytes exactly once and both parses and hashes that
    one read, so the hash recorded in `ref_load_audit` can never describe
    different content than what was validated/loaded (no second-read
    TOCTOU). Decodes as `utf-8-sig` so an Excel-saved BOM is stripped
    instead of corrupting the first header cell.

    Args:
        path: The CSV file.

    Returns:
        (header, rows, csv_sha256) — the header cells, every data row as
        lists of strings exactly as authored (empty cell = the empty
        string; the loader's NULL convention maps it to NULL at insert
        time), and the SHA-256 of the exact bytes read.

    Raises:
        ValueError: If the file is empty (no header row), unreadable, or
            not valid UTF-8.
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ValueError(f"Failed to read ref CSV {path}: {e}") from e
    csv_sha256 = compute_csv_sha256(raw)
    try:
        # newline="" hands line-ending interpretation to the csv module
        # (quoted fields may embed newlines); utf-8-sig strips a BOM.
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError(
                f"Ref CSV {path} is empty (no header row)"
            ) from None
        rows = [row for row in reader]
    except (UnicodeDecodeError, csv.Error) as e:
        raise ValueError(f"Failed to parse ref CSV {path}: {e}") from e
    logger.debug(f"Read {path.name}: {len(rows)} data row(s)")
    return header, rows, csv_sha256


# ---------------------------------------------------------------------------
# Live-table introspection
# ---------------------------------------------------------------------------


def fetch_ref_tables(
    conn: psycopg2.extensions.connection, schema: str
) -> set[str]:
    """Return the code-set tables of the reference schema (infra excluded).

    Args:
        conn: Active psycopg2 connection.
        schema: The ref schema name.

    Returns:
        Set of table names, minus `ref_load_audit`/`ddl_versions`.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables "
            "where table_schema = %s and table_type = 'BASE TABLE'",
            (schema,),
        )
        tables = {row[0] for row in cur.fetchall()}
    return tables - INFRA_TABLES


def fetch_table_columns(
    conn: psycopg2.extensions.connection, schema: str, table: str
) -> list[tuple[str, str, bool]]:
    """Return a live table's columns in ordinal order.

    Args:
        conn: Active psycopg2 connection.
        schema: The ref schema name.
        table: Table name within the schema.

    Returns:
        List of (column_name, data_type, is_nullable) in ordinal order.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select column_name, data_type, is_nullable "
            "from information_schema.columns "
            "where table_schema = %s and table_name = %s "
            "order by ordinal_position",
            (schema, table),
        )
        return [
            (name, data_type, nullable == "YES")
            for name, data_type, nullable in cur.fetchall()
        ]


def fetch_pk_columns(
    conn: psycopg2.extensions.connection, schema: str, table: str
) -> tuple[str, ...]:
    """Return a live table's primary-key column names in key order.

    Args:
        conn: Active psycopg2 connection.
        schema: The ref schema name.
        table: Table name within the schema.

    Returns:
        Tuple of PK column names (empty if the table has no PK — which
        `validate_csv` reports as an issue, never a silent skip of
        duplicate detection).
    """
    with conn.cursor() as cur:
        cur.execute(
            "select kcu.column_name "
            "from information_schema.table_constraints tc "
            "inner join information_schema.key_column_usage kcu "
            "  on tc.constraint_name = kcu.constraint_name "
            " and tc.table_schema = kcu.table_schema "
            " and tc.table_name = kcu.table_name "
            "where tc.constraint_type = 'PRIMARY KEY' "
            "  and tc.table_schema = %s and tc.table_name = %s "
            "order by kcu.ordinal_position",
            (schema, table),
        )
        return tuple(row[0] for row in cur.fetchall())


# ---------------------------------------------------------------------------
# Corpus-docs consistency gate
# ---------------------------------------------------------------------------


def documented_schemas(docs_dir: Path) -> set[str]:
    """Return the ref source's documented schema folder names.

    The schema folders under `docs_dir` (e.g. `codes/`) define the valid
    `data_ref/<schema>/` folder names — the CSV tree mirrors the
    documented schemas, enforced by `list_csv_files`. A directory counts
    as a documented schema only when it contains a `schema.yaml` (every
    corpus schema folder has one) — a stray non-schema directory under
    the source folder must not become a legal data_ref/ folder name.

    Args:
        docs_dir: The ref source's corpus folder (`data_catalog/sources/ref`).

    Returns:
        Set of schema folder names.

    Raises:
        ValueError: If `docs_dir` is missing (discovery cannot know the
            valid schema folders).
    """
    if not docs_dir.is_dir():
        raise ValueError(
            f"Ref docs folder not found: {docs_dir} — the schema-folder "
            f"discovery and the consistency gate need the ref docs"
        )
    return {
        entry.name
        for entry in docs_dir.iterdir()
        if entry.is_dir() and (entry / "schema.yaml").is_file()
    }


def documented_columns(docs_dir: Path) -> dict[str, TableDocs]:
    """Collect the documented shape per table from the ref docs.

    Walks every `columns.yaml` (and `columns/*.yaml` shard) under the ref
    source's corpus folder and groups the authored columns — name,
    `data_type`, `is_nullable`, `is_primary_key` — by `table_name`,
    tagging each table with the schema folder its docs live under. Used
    by the consistency gate (documented columns must equal the CSV
    header/DDL columns) and by docs-shape validation (the docs are the
    full shape contract when the live DB cannot vouch for a table).

    Shard files are deduplicated by resolved path, so a shard literally
    named `columns/columns.yaml` — matched by both discovery globs — is
    read once, not twice.

    Args:
        docs_dir: The ref source's corpus folder (`data_catalog/sources/ref`).

    Returns:
        Mapping of table_name -> `TableDocs` (documented schema plus the
        documented columns in author order).

    Raises:
        ValueError: If `docs_dir` is missing, a columns file is
            unparsable or lives outside a schema folder, a row lacks the
            required fields (string `table_name`/`column_name`/
            `data_type`, boolean `is_nullable`, optional boolean
            `is_primary_key`), a (table, column) is documented more
            than once, or one table's docs span two schema folders — the
            gate cannot run against broken docs, and a duplicate
            documented column (or a schema-straddling table) deserves an
            explicit message naming the offender and the files/folders.
    """
    if not docs_dir.is_dir():
        raise ValueError(
            f"Ref docs folder not found: {docs_dir} — the consistency "
            f"gate needs the documented corpus columns"
        )
    # Both globs can match one file (a shard named columns/columns.yaml);
    # dedupe by resolved path so it is read exactly once.
    candidates = sorted(docs_dir.rglob("columns.yaml")) + sorted(
        p
        for p in docs_dir.rglob("columns/*.yaml")
        if p.parent.name == "columns"
    )
    columns_files: list[Path] = []
    seen_paths: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen_paths:
            seen_paths.add(resolved)
            columns_files.append(path)
    documented: dict[str, TableDocs] = {}
    sources: dict[tuple[str, str], list[Path]] = {}
    for path in columns_files:
        relative_parts = path.relative_to(docs_dir).parts
        if len(relative_parts) < 2:
            raise ValueError(
                f"Ref columns docs outside a schema folder: {path} — "
                f"docs live under docs_dir/<schema>/ so each table's "
                f"documented schema is knowable"
            )
        schema = relative_parts[0]
        try:
            with open(path, "rb") as f:
                doc = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            raise ValueError(
                f"Failed to read or parse ref docs at {path}: {e}"
            ) from e
        for raw in doc or []:
            if (
                not isinstance(raw, dict)
                or not isinstance(raw.get("table_name"), str)
                or not isinstance(raw.get("column_name"), str)
                or not isinstance(raw.get("data_type"), str)
                or not isinstance(raw.get("is_nullable"), bool)
                or not isinstance(raw.get("is_primary_key", False), bool)
            ):
                raise ValueError(
                    f"Malformed ref docs row in {path}: {raw!r} — every "
                    f"row needs string table_name/column_name/data_type, "
                    f"boolean is_nullable, and (optionally) boolean "
                    f"is_primary_key"
                )
            table_name = raw["table_name"]
            column_name = raw["column_name"]
            sources.setdefault((table_name, column_name), []).append(path)
            entry = documented.get(table_name)
            if entry is None:
                entry = TableDocs(schema=schema, columns=[])
                documented[table_name] = entry
            elif entry.schema != schema:
                raise ValueError(
                    f"Ref table {table_name!r} is documented under two "
                    f"schema folders: {entry.schema!r} and {schema!r} "
                    f"(the second in {path}) — a table's docs must live "
                    f"under exactly one schema folder"
                )
            entry.columns.append(
                DocColumn(
                    name=column_name,
                    data_type=raw["data_type"],
                    is_nullable=raw["is_nullable"],
                    is_primary_key=raw.get("is_primary_key", False),
                )
            )
    duplicates = {
        key: paths for key, paths in sources.items() if len(paths) > 1
    }
    if duplicates:
        # Without this, the docs gate's list-vs-set comparison would
        # report a useless "undocumented: [], documented but absent: []".
        details = "; ".join(
            f"{table}.{column} in {sorted({str(p) for p in paths})}"
            for (table, column), paths in sorted(duplicates.items())
        )
        raise ValueError(
            f"Duplicate documented ref column(s): {details} — each "
            f"(table, column) may be documented exactly once across the "
            f"ref columns docs"
        )
    logger.debug(
        f"Documented ref columns for {len(documented)} table(s) "
        f"from {len(columns_files)} file(s) under {docs_dir}"
    )
    return documented


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _docs_gate_issues(
    table: str, header: list[str], documented: dict[str, TableDocs]
) -> list[str]:
    """Docs consistency gate: documented corpus columns == CSV header.

    Order is deliberately not compared (sorted-set equality): docs
    column order is authoring freedom, and an ordered gate would force
    docs churn on every reshape.

    Args:
        table: The target table name.
        header: The CSV header cells.
        documented: The documented shape per table.

    Returns:
        Issues (empty when the docs agree with the header).
    """
    if table not in documented:
        return [
            f"{table}: not documented in the ref corpus docs — add its "
            f"tables.yaml/columns.yaml rows under data_catalog/sources/ref/"
        ]
    doc_cols = [col.name for col in documented[table].columns]
    if sorted(doc_cols) != sorted(header):
        issues: list[str] = []
        # A repeated header cell fails the sorted comparison while both
        # set differences come out empty, so the message below would name
        # nothing at all ("undocumented: [], documented but absent: []").
        # Name the repeat instead — every issue string here names its
        # offender.
        repeated = sorted({c for c in header if header.count(c) > 1})
        if repeated:
            issues.append(
                f"{table}: CSV header repeats column(s) {repeated} — each "
                f"column may appear exactly once in the header"
            )
        missing = sorted(set(header) - set(doc_cols))
        extra = sorted(set(doc_cols) - set(header))
        # Skip the set-difference message only when the repeat is the
        # whole story (both differences empty).
        if not repeated or missing or extra:
            issues.append(
                f"{table}: documented corpus columns disagree with the "
                f"CSV/DDL columns — undocumented: {missing}, documented "
                f"but absent: {extra}"
            )
        return issues
    return []


def _guardrail_issues(
    table: str, rows: list[list[str]], max_rows_per_table: int
) -> list[str]:
    """Curated-set row-count guardrail (config knob).

    Args:
        table: The target table name.
        rows: The CSV data rows.
        max_rows_per_table: The guardrail value (from config — never a
            hardcoded literal).

    Returns:
        Issues (empty when the row count is within the guardrail).
    """
    if len(rows) > max_rows_per_table:
        return [
            f"{table}: {len(rows)} rows exceeds max_rows_per_table "
            f"({max_rows_per_table}) — ref tables are curated code sets; "
            f"open-ended domains are data, documented the ordinary way"
        ]
    return []


def _row_issues(
    table: str,
    header: list[str],
    rows: list[list[str]],
    columns: list[tuple[str, str, bool]],
    pk_columns: tuple[str, ...],
) -> list[str]:
    """Per-row checks shared by the live and docs-shape paths.

    Checks cell counts, PK-value uniqueness, empty-cell legality
    (nullable columns only), and per-value type parsing. Positional:
    `columns` must be aligned to `header` order (the callers guarantee
    it — live by ordered header equality, docs-shape by name lookup).

    Args:
        table: The target table name.
        header: The CSV header cells.
        rows: The CSV data rows (strings; empty cell = NULL).
        columns: (name, data_type, is_nullable) aligned to header order.
        pk_columns: The key column names (live PK or documented
            `is_primary_key` columns). Empty means duplicate detection
            cannot run — the callers report that as its own issue.

    Returns:
        List of human-readable issue strings.
    """
    issues: list[str] = []
    pk_indexes = [header.index(c) for c in pk_columns if c in header]
    seen_pks: dict[tuple[str, ...], int] = {}
    for line_no, row in enumerate(rows, start=2):  # header is line 1
        if len(row) != len(header):
            issues.append(
                f"{table}: line {line_no} has {len(row)} cells, expected "
                f"{len(header)}"
            )
            continue
        if pk_indexes:
            pk_value = tuple(row[i] for i in pk_indexes)
            first = seen_pks.get(pk_value)
            if first is not None:
                issues.append(
                    f"{table}: duplicate primary key {pk_value} at line "
                    f"{line_no} (first seen at line {first})"
                )
            else:
                seen_pks[pk_value] = line_no
        for (name, data_type, nullable), value in zip(columns, row):
            if value == "":
                # Empty cell = NULL (the loader's one spelling of absent).
                if not nullable:
                    issues.append(
                        f"{table}: line {line_no} column {name!r} is "
                        f"empty but the column is NOT NULL"
                    )
                continue
            parser = _TYPE_PARSERS.get(data_type)
            if parser is None:
                continue  # text-ish column: any string loads
            try:
                parser(value)
            except (ValueError, KeyError):
                issues.append(
                    f"{table}: line {line_no} column {name!r} value "
                    f"{value!r} does not parse as {data_type}"
                )
    return issues


def validate_csv_docs_shape(
    table: str,
    header: list[str],
    rows: list[list[str]],
    documented: dict[str, TableDocs],
    max_rows_per_table: int,
) -> list[str]:
    """Validate a CSV against its documented shape (no live table).

    The escape-hatch path (`--allow-missing-table` for new tables,
    `--allow-reshaped-table` for column-altering MRs): the live DB
    cannot vouch for the table, so the docs — which author `data_type`,
    `is_nullable`, and `is_primary_key` per column — become the full
    shape contract. Validation is as strict as the live path minus live
    introspection itself: docs gate, guardrail, header == documented
    columns, values parse per documented type, empty cells only on
    nullable columns, and PK uniqueness over the documented key columns.

    The documented `data_type` is a machine contract here: a type
    outside `DOCS_TYPE_VOCABULARY` is an explicit issue (ref docs must
    use parseable type names, unlike the freeform `data_type` prose
    acceptable elsewhere in the corpus).

    Args:
        table: The target table name (the CSV filename stem).
        header: The CSV header cells.
        rows: The CSV data rows.
        documented: The documented shape per table.
        max_rows_per_table: The curated-set guardrail (from config).

    Returns:
        List of human-readable issue strings.
    """
    gate_issues = _docs_gate_issues(table, header, documented)
    issues = list(gate_issues)
    issues.extend(_guardrail_issues(table, rows, max_rows_per_table))
    if gate_issues:
        # No trustworthy shape to validate against — column-aligned
        # checks would misreport, so only the per-row cell counts run.
        for line_no, row in enumerate(rows, start=2):  # header is line 1
            if len(row) != len(header):
                issues.append(
                    f"{table}: line {line_no} has {len(row)} cells, "
                    f"expected {len(header)}"
                )
        return issues
    doc_cols = documented[table].columns
    for col in doc_cols:
        if col.data_type not in DOCS_TYPE_VOCABULARY:
            issues.append(
                f"{table}: documented data_type {col.data_type!r} on "
                f"column {col.name!r} is outside the loader's parser "
                f"vocabulary {sorted(DOCS_TYPE_VOCABULARY)} — ref docs "
                f"must use parseable type names (unlike the freeform "
                f"data_type prose elsewhere in the corpus)"
            )
    pk_columns = tuple(col.name for col in doc_cols if col.is_primary_key)
    if not pk_columns:
        issues.append(
            f"{table}: no documented column has is_primary_key: true — "
            f"duplicate-code detection cannot run, and a curated code "
            f"set without a primary key is always a mistake (document "
            f"the key column)"
        )
    # The gate passed, so every header name resolves to a documented
    # column; align the docs to header order for the positional checks.
    by_name = {col.name: col for col in doc_cols}
    aligned = [
        (col.name, col.data_type, col.is_nullable)
        for col in (by_name[name] for name in header)
    ]
    issues.extend(_row_issues(table, header, rows, aligned, pk_columns))
    return issues


def missing_csv_issues(
    ref_tables: set[str],
    documented: dict[str, TableDocs],
    csv_tables: set[str],
    allow_dropped_tables: set[str] | None = None,
) -> list[str]:
    """Bidirectional drift: tables that exist somewhere but have no CSV.

    The per-CSV checks only see tables that HAVE a CSV; these close the
    other direction of the docs == CSV == DDL guarantee — a ref-schema
    table (infra excluded; the caller passes `fetch_ref_tables`' result)
    or a documented ref-source table with no matching
    `data_ref/<schema>/<table>.csv` is loud drift, not silence.

    Args:
        ref_tables: Code-set tables live in the reference schema.
        documented: The documented shape per table.
        csv_tables: Table names that have a data_ref CSV (stems).
        allow_dropped_tables: Tables whose DB-table-without-CSV issue is
            downgraded to a warning — the `--allow-dropped-table` escape
            for an MR that retires a ref table, whose drop migration
            applies post-merge. None means no exemptions.

    Returns:
        One issue per CSV-less table, per direction.
    """
    allow_dropped = allow_dropped_tables or set()
    issues: list[str] = []
    for table in sorted(ref_tables - csv_tables):
        # The documented schema spells the expected CSV path; a table
        # absent from the docs gets the literal <schema> placeholder.
        schema = (
            documented[table].schema if table in documented else "<schema>"
        )
        if table in allow_dropped:
            logger.warning(
                f"{table}: table exists in the reference schema but has no "
                f"data_ref/{schema}/{table}.csv — downgraded to a "
                f"warning by --allow-dropped-table (its drop migration "
                f"applies post-merge)"
            )
            continue
        issues.append(
            f"{table}: table exists in the reference schema but has no "
            f"data_ref/{schema}/{table}.csv — every ref code-set table "
            f"must be CSV-backed (add the CSV, or drop the table in a "
            f"migration and pass --allow-dropped-table {table} on the "
            f"pre-merge --dry-run)"
        )
    for table in sorted(set(documented) - csv_tables):
        issues.append(
            f"{table}: documented in the ref corpus docs but has no "
            f"data_ref/{documented[table].schema}/{table}.csv — "
            f"docs == CSV == DDL must hold in both directions (add the "
            f"CSV, or remove the docs rows)"
        )
    return issues


def validate_csv(
    table: str,
    header: list[str],
    rows: list[list[str]],
    live_columns: list[tuple[str, str, bool]],
    pk_columns: tuple[str, ...],
    documented: dict[str, TableDocs],
    max_rows_per_table: int,
) -> list[str]:
    """Run every per-CSV validation; return the accumulated issues.

    Args:
        table: The target table name (the CSV filename stem).
        header: The CSV header cells.
        rows: The CSV data rows (strings; empty cell = NULL).
        live_columns: The live table's (name, data_type, is_nullable)
            in ordinal order.
        pk_columns: The live table's PK column names. Empty is itself an
            issue: a curated code set without a primary key is always a
            mistake, and duplicate detection cannot run without one.
        documented: The documented shape per table (from
            `documented_columns`).
        max_rows_per_table: The curated-set row-count guardrail (from
            config — never a hardcoded literal).

    Returns:
        List of human-readable issue strings (empty when the CSV is
        loadable).
    """
    issues: list[str] = []

    # Header must equal the live table's columns, in order — the CSV is a
    # faithful image of the table.
    live_names = [name for name, _, _ in live_columns]
    if header != live_names:
        issues.append(
            f"{table}: CSV header {header} does not equal the live "
            f"table's columns {live_names} (if this MR reshapes the "
            f"table via a ddl_ref migration, pass --allow-reshaped-table "
            f"{table} on the pre-merge --dry-run)"
        )
        # Per-value checks are positional; they would misreport against a
        # wrong header, so stop this CSV's validation here.
        return issues

    # Docs consistency gate: documented corpus columns == CSV header.
    issues.extend(_docs_gate_issues(table, header, documented))

    # Curated-set guardrail (config knob).
    issues.extend(_guardrail_issues(table, rows, max_rows_per_table))

    # PK guard: no primary key is a validation issue, never a silent
    # skip of duplicate detection.
    if not pk_columns:
        issues.append(
            f"{table}: the live table has no primary key — "
            f"duplicate-code detection cannot run, and a curated code "
            f"set without a primary key is always a mistake (add the PK "
            f"in a ref migration)"
        )

    # Per-row shape, PK uniqueness, and type parsing.
    issues.extend(_row_issues(table, header, rows, live_columns, pk_columns))
    return issues


def _warn_unused_exemptions(
    flag: str, exempted: set[str], matchable: set[str], subject: str
) -> None:
    """Log one WARNING naming exemptions that downgrade nothing.

    An exemption matching no table is silently inert — it neither errors
    nor warns on its own — so a misspelled or stale CI-computed flag
    looks like it worked. Naming it makes a broken exemption computation
    visible in the dry-run log (the guardrail `apply_ddl` already gives
    `--allow-pending`). Nothing is logged when every name matched.

    Args:
        flag: The CLI flag the names came from (for the message).
        exempted: The table names passed with that flag.
        matchable: The table names the flag could actually apply to.
        subject: What `matchable` holds, for the message (e.g.
            "ref CSV").
    """
    unused = sorted(exempted - matchable)
    if unused:
        logger.warning(
            f"{flag} matched no {subject}: {unused} — the exemption(s) "
            f"downgraded nothing (check the flag list for a typo or a "
            f"stale entry)"
        )


def validate_all(
    conn: psycopg2.extensions.connection,
    schema: str,
    csv_files: list[Path],
    docs_dir: Path,
    max_rows_per_table: int,
    allow_missing_tables: set[str] | None = None,
    allow_reshaped_tables: set[str] | None = None,
    allow_dropped_tables: set[str] | None = None,
) -> tuple[dict[str, tuple[list[str], list[list[str]], str]], list[str]]:
    """Validate every ref CSV against the live schema and the docs.

    Also runs the bidirectional drift checks (`missing_csv_issues`): a
    ref-schema or documented table with no CSV is an issue, so the
    docs == CSV == DDL guarantee holds in both directions. Each
    documented table's CSV must also sit under ITS documented schema
    folder — discovery only proves the folder names some documented
    schema, so a misfiled CSV (a valid folder, but not the table's) is
    an issue here on every path (the check needs only the docs). Any
    exemption name that downgrades nothing this run is reported as a
    WARNING (see `_warn_unused_exemptions`) — CI computes the exemptions
    from the MR's diff, and an inert one is otherwise invisible.

    Args:
        conn: Active psycopg2 connection.
        schema: The ref schema name.
        csv_files: The CSVs to validate (from `list_csv_files`).
        docs_dir: The ref source's corpus folder.
        max_rows_per_table: The curated-set guardrail (from config).
        allow_missing_tables: Table names whose missing-from-DB error is
            downgraded to a warning (docs-shape validation still runs) —
            the `--allow-missing-table` escape for new-table MRs whose
            ref migration applies post-merge. None means no exemptions.
        allow_reshaped_tables: Existing tables validated against the
            documented shape instead of the live columns — the
            `--allow-reshaped-table` escape for MRs that alter a ref
            table's columns, whose migration applies post-merge. None
            means no exemptions.
        allow_dropped_tables: Tables whose DB-table-without-CSV drift
            issue is downgraded to a warning — the
            `--allow-dropped-table` escape for table-retiring MRs whose
            drop migration applies post-merge. None means no exemptions.

    Returns:
        (loadable, issues) — `loadable` maps each table name to its
        (header, rows, csv_sha256) for the write phase (the hash is of
        the exact bytes read here — single read); `issues` accumulates
        every validation failure across all CSVs. A load may proceed
        only when `issues` is empty. Docs-shape-validated tables (the
        missing/reshaped escapes, dry-run only) are never in `loadable`.

    Raises:
        ValueError: If the ref docs are missing or malformed (from
            `documented_columns`) — broken docs abort the run instead of
            becoming an accumulated issue, because the gate cannot be
            run at all against docs that will not parse.
        psycopg2.Error: On any live-introspection failure.
    """
    allow_missing = allow_missing_tables or set()
    allow_reshaped = allow_reshaped_tables or set()
    allow_dropped = allow_dropped_tables or set()
    documented = documented_columns(docs_dir)
    ref_tables = fetch_ref_tables(conn, schema)
    loadable: dict[str, tuple[list[str], list[list[str]], str]] = {}
    issues: list[str] = []
    for path in csv_files:
        table = path.stem
        # Schema-folder alignment: discovery only proves the folder names
        # SOME documented schema; the CSV must live under THIS table's
        # documented schema folder. Needs only the docs, so it runs on
        # every path (live, --allow-missing-table, --allow-reshaped-table).
        docs_entry = documented.get(table)
        if docs_entry is not None and path.parent.name != docs_entry.schema:
            issues.append(
                f"{table}: CSV lives under the {path.parent.name!r} "
                f"folder but the table is documented under the "
                f"{docs_entry.schema!r} schema — move it to "
                f"data_ref/{docs_entry.schema}/{table}.csv"
            )
        if table not in ref_tables and table not in allow_missing:
            issues.append(
                f"{path.name}: no table named {table!r} in the "
                f"{schema!r} schema — the filename stem must resolve to "
                f"a ref table (apply the ref migration first, or pass "
                f"--allow-missing-table {table} on a pre-merge --dry-run)"
            )
            continue
        try:
            header, rows, csv_sha256 = read_csv(path)
        except ValueError as e:
            issues.append(str(e))
            continue
        if table not in ref_tables:
            # Exempted by --allow-missing-table: the table's migration
            # applies post-merge, so live introspection cannot run —
            # warn, and validate the CSV against the documented shape.
            logger.warning(
                f"{table}: not in the {schema!r} schema — downgraded to "
                f"a warning by --allow-missing-table (its ref migration "
                f"applies post-merge); validating the CSV against the "
                f"documented shape"
            )
            issues.extend(
                validate_csv_docs_shape(
                    table, header, rows, documented, max_rows_per_table
                )
            )
            continue
        if table in allow_reshaped:
            # Exempted by --allow-reshaped-table: the live columns are
            # the OLD shape (the reshape migration applies post-merge),
            # so the documented shape is the validation target instead.
            logger.warning(
                f"{table}: validating against the documented shape "
                f"instead of the live columns — downgraded by "
                f"--allow-reshaped-table (its reshape migration applies "
                f"post-merge)"
            )
            issues.extend(
                validate_csv_docs_shape(
                    table, header, rows, documented, max_rows_per_table
                )
            )
            continue
        live_columns = fetch_table_columns(conn, schema, table)
        pk_columns = fetch_pk_columns(conn, schema, table)
        issues.extend(
            validate_csv(
                table,
                header,
                rows,
                live_columns,
                pk_columns,
                documented,
                max_rows_per_table,
            )
        )
        loadable[table] = (header, rows, csv_sha256)
    # Bidirectional drift: tables with no CSV at all (DB- and docs-side).
    csv_tables = {path.stem for path in csv_files}
    issues.extend(
        missing_csv_issues(ref_tables, documented, csv_tables, allow_dropped)
    )
    # The exemptions are computed by CI from the MR's own diff, so a
    # broken computation (or a typo) shows up as a name that downgrades
    # nothing. Report it, the way apply_ddl reports an unused
    # --allow-pending: an inert exemption fails nothing on its own and is
    # otherwise invisible in the dry-run log.
    _warn_unused_exemptions(
        "--allow-missing-table", allow_missing, csv_tables, "ref CSV"
    )
    _warn_unused_exemptions(
        "--allow-reshaped-table", allow_reshaped, csv_tables, "ref CSV"
    )
    _warn_unused_exemptions(
        "--allow-dropped-table",
        allow_dropped,
        ref_tables - csv_tables,
        "CSV-less ref table",
    )
    return loadable, issues


# ---------------------------------------------------------------------------
# Write phase
# ---------------------------------------------------------------------------


def load_tables(
    conn: psycopg2.extensions.connection,
    loadable: dict[str, tuple[list[str], list[list[str]], str]],
) -> None:
    """Truncate-and-reload every validated table in one transaction.

    Appends one `ref_load_audit` row per table (CSV content hash, row
    count) inside the same transaction, so the freshness ledger can never
    disagree with what was actually loaded — the hash rides in `loadable`
    from the single `read_csv` read, so it describes the exact bytes
    loaded. Commits on success; rolls back on any failure.

    Args:
        conn: Active psycopg2 connection (maintainer credentials).
        loadable: table -> (header, rows, csv_sha256), from
            `validate_all`.

    Raises:
        psycopg2.Error: On any database failure (after rollback).
    """
    try:
        with conn.cursor() as cur:
            for table, (header, rows, csv_sha256) in sorted(loadable.items()):
                cur.execute(
                    sql.SQL("truncate table {}").format(
                        sql.Identifier(table)
                    )
                )
                insert = sql.SQL(
                    "insert into {} ({}) values ({})"
                ).format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(
                        sql.Identifier(col) for col in header
                    ),
                    sql.SQL(", ").join(
                        sql.Placeholder() for _ in header
                    ),
                )
                for row in rows:
                    # Empty cell = NULL; Postgres casts the text values to
                    # the column types (parse-validated beforehand).
                    cur.execute(
                        insert,
                        [value if value != "" else None for value in row],
                    )
                cur.execute(
                    "insert into ref_load_audit "
                    "(table_name, csv_sha256, row_count) "
                    "values (%s, %s, %s)",
                    (table, csv_sha256, len(rows)),
                )
                logger.info(f"Loaded {table}: {len(rows)} row(s)")
        conn.commit()
        logger.info(f"Committed reload of {len(loadable)} ref table(s)")
    except Exception as e:
        logger.error(f"Ref load failed; rolling back: {e}")
        # A rollback failure (e.g. a dead connection) must not mask the
        # root cause: log it and re-raise the ORIGINAL exception.
        try:
            conn.rollback()
        except Exception as rollback_error:
            logger.error(
                f"Rollback itself failed (original error preserved): "
                f"{rollback_error}"
            )
        raise


# ---------------------------------------------------------------------------
# --check (freshness)
# ---------------------------------------------------------------------------


def check_freshness(
    conn: psycopg2.extensions.connection,
    csv_files: list[Path],
    ref_tables: set[str],
) -> list[str]:
    """Compare each CSV's hash against the latest ref_load_audit row.

    The latest-row query orders by `loaded_ts desc, audit_id desc`: two
    audit rows written in one transaction share a now() timestamp, so
    the identity PK is the deterministic tiebreaker.

    Args:
        conn: Active psycopg2 connection.
        csv_files: The CSVs to check.
        ref_tables: Code-set tables live in the reference schema (from
            `fetch_ref_tables`) — distinguishes "table missing: apply
            the migration" from "table present but never loaded: run
            the loader".

    Returns:
        One issue string per missing, never-loaded, or stale table
        (empty when the DB is current with every CSV).

    Raises:
        ValueError: If a CSV cannot be read — any `OSError` surfaces as
            the documented ValueError contract, matching `read_csv`.
    """
    issues: list[str] = []
    with conn.cursor() as cur:
        for path in csv_files:
            table = path.stem
            if table not in ref_tables:
                issues.append(
                    f"{table}: no such table in the reference schema — apply "
                    f"the ref migration that creates it (apply_ddl.py "
                    f"with apply_ddl_ref.toml), then run load_ref_data.py"
                )
                continue
            try:
                raw = path.read_bytes()
            except OSError as e:
                raise ValueError(
                    f"Failed to read ref CSV {path}: {e}"
                ) from e
            csv_hash = compute_csv_sha256(raw)
            cur.execute(
                "select csv_sha256 from ref_load_audit "
                "where table_name = %s "
                "order by loaded_ts desc, audit_id desc limit 1",
                (table,),
            )
            row = cur.fetchone()
            if row is None:
                issues.append(
                    f"{table}: never loaded (no ref_load_audit row) — "
                    f"the table exists; run load_ref_data.py"
                )
            elif row[0] != csv_hash:
                issues.append(
                    f"{table}: stale — {path.name} has changed since the "
                    f"last load (run load_ref_data.py)"
                )
            else:
                logger.debug(f"{table}: current")
    return issues


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(
    config: dict[str, Any],
    check: bool,
    dry_run: bool,
    allow_missing_tables: set[str] | None = None,
    allow_reshaped_tables: set[str] | None = None,
    allow_dropped_tables: set[str] | None = None,
) -> None:
    """Execute the load (or `--check` / `--dry-run`) given a parsed config.

    Args:
        config: Parsed TOML config with keys `csv_dir`, `database`,
            `schema`, and optionally `docs_dir` (default
            `data_catalog/sources/ref`) and `max_rows_per_table` (default
            `DEFAULT_MAX_ROWS_PER_TABLE`).
        check: Freshness mode — read-only, exits non-zero on any stale,
            never-loaded, missing, or CSV-less table (bidirectional
            drift).
        dry_run: Validate-only mode — read-only, stops before the
            truncate.
        allow_missing_tables: Tables exempted from the missing-from-DB
            error (`--allow-missing-table`; only meaningful with
            `dry_run` — `main` rejects it elsewhere).
        allow_reshaped_tables: Existing tables validated against the
            documented shape instead of the live columns
            (`--allow-reshaped-table`; dry-run only, like the above).
        allow_dropped_tables: Tables whose DB-table-without-CSV drift
            issue downgrades to a warning (`--allow-dropped-table`;
            dry-run only, like the above).

    Raises:
        KeyError: If a required config field is missing.
        OSError: If `csv_dir` doesn't exist (`FileNotFoundError`), or a
            walk of the CSV/docs tree fails for another reason (e.g.
            `PermissionError` on an unreadable folder) — `main` maps the
            whole family to a logged exit 1.
        ValueError: On CSV-discovery/docs failures or an invalid schema
            name.
        RuntimeError: If env vars are missing, or validation/freshness
            issues were found (each already logged).
        psycopg2.Error: On any database failure.
    """
    csv_dir = Path(config["csv_dir"])
    docs_dir = Path(config.get("docs_dir", "data_catalog/sources/ref"))
    max_rows = int(
        config.get("max_rows_per_table", DEFAULT_MAX_ROWS_PER_TABLE)
    )
    conn_kwargs = connection_kwargs(config["database"], config["schema"])

    # Discovery needs the documented schema folders: data_ref/'s folder
    # level mirrors them (folder = documented schema, enforced).
    csv_files = list_csv_files(csv_dir, documented_schemas(docs_dir))

    conn = psycopg2.connect(**conn_kwargs)
    try:
        if check:
            ref_tables = fetch_ref_tables(conn, config["schema"])
            issues = check_freshness(conn, csv_files, ref_tables)
            # --check surfaces bidirectional drift too: a DB or docs
            # table with no CSV is exactly the forgotten-state drift
            # this mode exists to catch.
            documented = documented_columns(docs_dir)
            csv_tables = {path.stem for path in csv_files}
            issues.extend(
                missing_csv_issues(ref_tables, documented, csv_tables)
            )
            if issues:
                for issue in issues:
                    logger.error(issue)
                raise RuntimeError(
                    f"ref freshness check failed: {len(issues)} issue(s) "
                    f"(stale, never loaded, missing, or CSV-less tables)"
                )
            logger.info(
                f"All {len(csv_files)} ref table(s) current with data_ref/"
            )
            return

        loadable, issues = validate_all(
            conn,
            config["schema"],
            csv_files,
            docs_dir,
            max_rows,
            allow_missing_tables,
            allow_reshaped_tables,
            allow_dropped_tables,
        )
        if issues:
            for issue in issues:
                logger.error(issue)
            raise RuntimeError(
                f"ref CSV validation failed with {len(issues)} issue(s)"
            )
        logger.info(
            f"Validation passed for {len(loadable)} ref table(s)"
        )
        if dry_run:
            logger.info("Dry run: stopping before the truncate (no writes)")
            return

        # The audit hashes ride inside `loadable` (computed by read_csv
        # from the single read) — no re-read, no TOCTOU.
        load_tables(conn, loadable)
    finally:
        conn.close()


def main() -> None:
    """Parse args, load config, set up logging, dispatch to `run`."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate and truncate-and-reload the ref-schema code sets "
            "from data_ref/<schema>/*.csv."
        ),
    )
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to TOML configuration file.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Freshness mode: exit non-zero if any ref CSV's content hash "
            "differs from the latest ref_load_audit row (or a table was "
            "never loaded). Read-only."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate-only mode: run every validation and stop before "
            "the truncate. Read-only (runnable under the RO role)."
        ),
    )
    parser.add_argument(
        "--allow-missing-table",
        action="append",
        default=[],
        metavar="TABLE",
        help=(
            "With --dry-run only (repeatable): downgrade TABLE's "
            "missing-from-the-ref-schema error to a warning and validate "
            "its CSV against the documented shape — for MRs adding a new "
            "ref table whose migration is applied post-merge (CI computes "
            "this from the MR's own added CSVs)."
        ),
    )
    parser.add_argument(
        "--allow-reshaped-table",
        action="append",
        default=[],
        metavar="TABLE",
        help=(
            "With --dry-run only (repeatable): validate TABLE's CSV "
            "against the documented shape instead of the live columns — "
            "for MRs that alter a ref table's columns, whose migration "
            "is applied post-merge (CI computes this from the MR's own "
            "modified CSVs when the MR also touches ddl_ref/)."
        ),
    )
    parser.add_argument(
        "--allow-dropped-table",
        action="append",
        default=[],
        metavar="TABLE",
        help=(
            "With --dry-run only (repeatable): downgrade TABLE's "
            "DB-table-without-CSV drift error to a warning — for MRs "
            "that retire a ref table, whose drop migration is applied "
            "post-merge (CI computes this from the MR's own deleted "
            "CSVs)."
        ),
    )
    args = parser.parse_args()

    setup_logging(log_dir="logs/load_ref_data")
    logger.info("=" * 60)

    if args.check and args.dry_run:
        logger.error("--check and --dry-run are mutually exclusive")
        logger.info("=" * 60)
        sys.exit(1)

    # Every exemption flag exists solely for the pre-merge dry-run; a
    # real load (or --check) must never skip a drift or missing-table
    # error.
    dry_run_only_flags = {
        "--allow-missing-table": args.allow_missing_table,
        "--allow-reshaped-table": args.allow_reshaped_table,
        "--allow-dropped-table": args.allow_dropped_table,
    }
    if not args.dry_run:
        rejected = sorted(
            flag for flag, values in dry_run_only_flags.items() if values
        )
        if rejected:
            logger.error(f"{', '.join(rejected)} require(s) --dry-run")
            logger.info("=" * 60)
            sys.exit(1)

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"Config file not found: {args.config}")
        logger.info("=" * 60)
        sys.exit(1)

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.error(f"Failed to read config file: {e}")
        logger.info("=" * 60)
        sys.exit(1)

    try:
        run(
            config,
            check=args.check,
            dry_run=args.dry_run,
            allow_missing_tables=set(args.allow_missing_table),
            allow_reshaped_tables=set(args.allow_reshaped_table),
            allow_dropped_tables=set(args.allow_dropped_table),
        )
        logger.info("SUCCESS")
        logger.info("=" * 60)
    except KeyError as e:
        logger.error(f"Missing required config field: {e}")
        logger.info("=" * 60)
        sys.exit(1)
    # OSError (not just FileNotFoundError) so an unreadable data_ref/ or
    # docs folder — the directory walks in run() raise PermissionError,
    # NotADirectoryError, etc. — still ends in a logged exit 1 rather
    # than an unhandled traceback with no closing separator.
    except (OSError, ValueError, RuntimeError) as e:
        logger.error(f"Error: {e}")
        logger.info("=" * 60)
        sys.exit(1)
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        logger.info("=" * 60)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
