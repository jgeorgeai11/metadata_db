"""corpus_validation.py — corpus validation rules.

`validate_corpus` enforces the loader-contract checks in
`MAINTAINING.md#ci--loader` step 4: uniqueness, FK
reference existence (including concept `related_object_ids` link
resolution), identifier syntax, within-row consistency, the
`cardinality` enum, deployment residency rules, and SQL expression
parsability. Validity is venue-free: a relationship is runnable where its
two endpoint tables are co-deployed, and a mapping's referenced tables
must likewise share a venue — both derived from the `deployment_tables`
table rather than a stored system column. Issues are accumulated across
*all* rules and surfaced together via `ValidationError` so authors see
every problem per run.

`validate_update_reason` enforces the discipline that fresh inserts
must have `update_reason: null` and updates must have non-null
`update_reason` (applied to the eight authored-row tables;
`deployment_tables` rows are derived and carry no `update_reason` at
all, so they are exempt — see the function docstring).

Parsed SQL trees are memoized so the orchestrator can reuse them when
computing `column_mappings.target_tables_referenced` without
re-parsing.
"""

import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import sqlglot.expressions as exp

# The vendored logging package (code/lib/logconfig) is resolved from
# this file's own location, so imports work from any working directory
# (not just the repo root) and CI never depends on untracked .claude/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import get_logger
from sql_parsing import (
    compute_target_tables_referenced,
    contains_statement_or_navigation,
    extract_column_refs,
    find_volatile_functions,
    is_boolean_predicate,
    parse_expression,
)
from yaml_discovery import validate_identifier_segment
from data_model import ColumnMappingKey, Corpus

if TYPE_CHECKING:
    from corpus_diff import Diff

logger = get_logger(__name__)

# cardinality values the DB CHECK constraint accepts (case-sensitive), so
# the loader can reject a bad value pre-merge instead of at write time.
# NULL (None) is allowed — cardinality is optional, never guessed.
_VALID_CARDINALITIES: frozenset[str] = frozenset(
    {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}
)


class ValidationError(Exception):
    """Aggregated corpus-validation failure.

    Carries the full list of issue strings discovered during a single
    `validate_corpus` call, not just the first one. The exception's
    string representation joins them with newlines for log readability.

    Attributes:
        issues: List of human-readable issue strings.
        summary: The one-line stage-naming summary (issue count included,
            issues themselves excluded) — a stable attribute so callers
            never have to parse the string representation.
    """

    # Stage name leading the summary line. A subclass overrides it so
    # the exception itself names its stage (see
    # `corpus_assembly.AssemblyError`) while inheriting the aggregation
    # contract — the orchestrator logs any ValidationError the same way.
    _SUMMARY_PREFIX = "Corpus validation failed"

    def __init__(self, issues: list[str]) -> None:
        self.issues = list(issues)
        self.summary = (
            f"{self._SUMMARY_PREFIX} with {len(self.issues)} issue(s):"
        )
        joined = "\n  - ".join(self.issues)
        super().__init__(f"{self.summary}\n  - {joined}")


def validate_corpus(
    corpus: Corpus,
) -> dict[ColumnMappingKey, exp.Expression | None]:
    """Run all corpus-validation rules and aggregate any issues.

    Args:
        corpus: The YAML-derived corpus.

    Returns:
        A memo dict mapping each `column_mappings` PK
        (`(source_column_id, mapping_name)`) to its parsed
        `target_expression` tree (or None if the expression was None).
        The orchestrator reuses this dict to derive
        `target_tables_referenced` without re-parsing.

    Raises:
        ValidationError: If any rule failed. All issues are reported.
    """
    issues: list[str] = []
    expression_memo: dict[ColumnMappingKey, exp.Expression | None] = {}

    # --- Uniqueness ---
    # PK uniqueness is detected upstream in
    # `corpus_assembly.assemble_corpus`; the physical-address uniqueness
    # of deployment_tables is checked in `_check_deployment_tables` below.

    # --- Reference existence ---
    _check_references(corpus, issues)
    # Columns' optional domain pointer: every non-null ref_table_id must
    # resolve to a documented table (cross-source references expected —
    # no co-deployment or linkability rule applies).
    _check_ref_tables(corpus, issues)
    # Concepts' one referential rule: every related_object_ids entry must
    # resolve to a corpus PK across the five id-keyed data spaces.
    _check_concept_related_objects(corpus, issues)
    # Every concept's anchor prefix (the labels before the reserved
    # `concept` segment) must resolve to an existing data source or schema —
    # the one referential hole a plain FK cannot express (variable-depth
    # anchor), so a concepts.yaml in a phantom folder is caught here.
    _check_concept_anchors(corpus, issues)

    # --- Identifier syntax (dotted names + physical names) ---
    _check_identifier_syntax(corpus, issues)

    # --- Deployment residency rules (physical-address uniqueness;
    #     physical-name case is a wave-1 assembly check, and system
    #     existence is covered by _check_references) ---
    _check_deployment_tables(corpus, issues)

    # --- Within-row consistency ---
    _check_within_row(corpus, issues)

    # --- cardinality enum (per-row) ---
    _check_cardinality(corpus, issues)

    # --- SQL expression checks (parsability + resolution for
    #     join_condition and target_expression) ---
    _check_sql_expressions(corpus, issues, expression_memo)

    # --- Whole-corpus grouping / derived-validity checks ---
    # Venue sets per table (from deployments) drive the "runnable
    # somewhere" checks for relationships and mappings.
    venues = _deployment_venues(corpus)
    _check_relationship_codeployment(corpus, issues, venues)
    _check_mapping_codeployment(corpus, issues, venues, expression_memo)
    # Orientation-duplicate + use_when disambiguation share the
    # unordered-pair grouping; the mapping analogue mirrors it.
    _check_relationship_pairs(corpus, issues)
    _check_mapping_disambiguation(corpus, issues)
    # Multi-table linkability reads the parse trees memoized above.
    _check_mapping_linkability(corpus, issues, expression_memo)

    if issues:
        raise ValidationError(issues)
    logger.info("Corpus validation passed (no issues)")
    return expression_memo


def _check_references(corpus: Corpus, issues: list[str]) -> None:
    """FK reference checks for the tables that carry outgoing FKs."""
    for sc_id, sc in corpus.schemas.items():
        if sc.data_source_id not in corpus.data_sources:
            issues.append(
                f"schemas[{sc_id}].data_source_id={sc.data_source_id!r} "
                f"not defined in data_sources"
            )
    for t_id, t in corpus.tables.items():
        if t.schema_id not in corpus.schemas:
            issues.append(
                f"tables[{t_id}].schema_id={t.schema_id!r} not defined "
                f"in schemas"
            )
    for c_id, c in corpus.columns.items():
        if c.table_id not in corpus.tables:
            issues.append(
                f"columns[{c_id}].table_id={c.table_id!r} not defined "
                f"in tables"
            )
    # deployment_tables FK columns: table_id -> tables, system -> systems,
    # data_source_id -> data_sources. Loader-expanded rows normally
    # resolve, but this guards a corpus assembled by any other route.
    for key, dep in corpus.deployment_tables.items():
        if dep.table_id not in corpus.tables:
            issues.append(
                f"deployment_tables[{key}].table_id={dep.table_id!r} not "
                f"defined in tables"
            )
        if dep.system not in corpus.systems:
            issues.append(
                f"deployment_tables[{key}].system={dep.system!r} not "
                f"defined in systems"
            )
        if dep.data_source_id not in corpus.data_sources:
            issues.append(
                f"deployment_tables[{key}].data_source_id="
                f"{dep.data_source_id!r} not defined in data_sources"
            )
        # data_source_id is redundant with table_id's leading segment (the
        # {database} label); they must agree. Loader-expanded rows satisfy
        # this by construction, but a corpus assembled by any other route
        # could disagree — this mirrors the DB CHECK
        # (data_source_id = subltree(table_id, 0, 1)).
        table_database = dep.table_id.split(".", 1)[0]
        if dep.data_source_id != table_database:
            issues.append(
                f"deployment_tables[{key}].data_source_id="
                f"{dep.data_source_id!r} does not equal table_id's leading "
                f"segment {table_database!r} — the two must agree "
                f"(data_source_id is the redundant copy of table_id's "
                f"{{database}} label)"
            )
    # table_a_id / table_b_id / source_column_id are *authored*
    # references (not composed from the file's path), so a mis-cased
    # paste from a source-system console is a realistic failure — and
    # with the lowercase mandate, corpus PKs are all-lowercase, making
    # any uppercase reference guaranteed not to resolve. Append the
    # case-mismatch hint so the message names the fix.
    for key, rel in corpus.table_relationships.items():
        if rel.table_a_id not in corpus.tables:
            issues.append(
                f"table_relationships[{key}].table_a_id={rel.table_a_id!r} "
                f"not defined in tables"
                + _case_hint(rel.table_a_id, corpus.tables)
            )
        if rel.table_b_id not in corpus.tables:
            issues.append(
                f"table_relationships[{key}].table_b_id={rel.table_b_id!r} "
                f"not defined in tables"
                + _case_hint(rel.table_b_id, corpus.tables)
            )
    for key, cm in corpus.column_mappings.items():
        # No target_system check: there is no target_system column. The
        # source system/data source is the leading label of
        # source_column_id, and its existence is guaranteed by the
        # source_column_id -> columns reference below.
        if cm.source_column_id not in corpus.columns:
            issues.append(
                f"column_mappings[{key}].source_column_id="
                f"{cm.source_column_id!r} not defined in columns"
                + _case_hint(cm.source_column_id, corpus.columns)
            )


def _check_ref_tables(corpus: Corpus, issues: list[str]) -> None:
    """Resolve every column's `ref_table_id` against the documented tables.

    The pointer is authored (`ref_table` in `columns.yaml`), so the
    loader verifies it: a non-null value must name a documented table.
    Cross-source references are expected and correct — a ocs column may
    point at a ref-source code table — so, deliberately, no
    co-deployment or linkability rule applies: the pointer serves
    context retrieval (what a value means), never a join path. An
    unresolved pointer gets the same near-match "did you mean …?" hint
    as the other authored references (`_case_hint`): the pointer is
    typed by hand, so a mis-cased paste is the realistic failure.
    """
    for c_id, c in corpus.columns.items():
        if c.ref_table_id is None:
            continue
        if c.ref_table_id not in corpus.tables:
            issues.append(
                f"columns[{c_id}].ref_table_id={c.ref_table_id!r} does "
                f"not resolve to a documented table"
                + _case_hint(c.ref_table_id, corpus.tables)
            )


def _check_identifier_syntax(corpus: Corpus, issues: list[str]) -> None:
    """Identifier-segment syntax (no `.` or whitespace per segment).

    Covers body-derived names (table_name, column_name,
    relationship_name, mapping_name) plus each dotted segment of the
    path-derived `concept_id`. Physical names are validated in wave 1
    where they are authored (`corpus_assembly`) — they are text values,
    not ltree segments.
    """
    for t_id, t in corpus.tables.items():
        try:
            validate_identifier_segment(t.table_name, "table_name")
        except ValueError as e:
            issues.append(f"tables[{t_id}]: {e}")
    for c_id, c in corpus.columns.items():
        try:
            validate_identifier_segment(c.column_name, "column_name")
        except ValueError as e:
            issues.append(f"columns[{c_id}]: {e}")
    for key, rel in corpus.table_relationships.items():
        try:
            validate_identifier_segment(
                rel.relationship_name, "relationship_name"
            )
        except ValueError as e:
            issues.append(f"table_relationships[{key}]: {e}")
    for key, cm in corpus.column_mappings.items():
        try:
            validate_identifier_segment(cm.mapping_name, "mapping_name")
        except ValueError as e:
            issues.append(f"column_mappings[{key}]: {e}")
    # concept_id is a path-derived, dotted ltree id (prefix + reserved
    # `concept` segment + `name`); validate each segment. Defense-in-depth:
    # the assembler already validates the `name` leaf, so a corpus reaching
    # here is normally clean — this guards a concept constructed by any
    # other route.
    for concept_id, concept in corpus.concepts.items():
        for segment in concept.concept_id.split("."):
            try:
                validate_identifier_segment(segment, "concept_id")
            except ValueError as e:
                issues.append(f"concepts[{concept_id}]: {e}")


def _check_deployment_tables(corpus: Corpus, issues: list[str]) -> None:
    """Deployment residency rule: physical-address uniqueness.

    Physical names are plain text (not ltree segments), but authored
    lowercase like every catalog name — the same `[a-z0-9_-]` charset,
    enforced in wave 1 where the names are authored
    (`corpus_assembly._expand_schema_entry` and
    `_assemble_deployment_entry`, per the deployment file rules —
    CONTRIBUTING.md wave 1) — so the
    physical-address uniqueness check is plain equality. Each physical
    address (`system` + the three physical names) must be claimed by at
    most one documented table: one physical object, one documented
    identity (the DDL's UNIQUE constraint is the backstop; this is the
    pre-merge gate).
    """
    seen_addresses: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    for key, dep in corpus.deployment_tables.items():
        address = (
            dep.system,
            dep.physical_database_name,
            dep.physical_schema_name,
            dep.physical_table_name,
        )
        if address in seen_addresses:
            issues.append(
                f"deployment_tables[{key}]: physical address {address} is "
                f"already claimed by "
                f"deployment_tables[{seen_addresses[address]}] — one "
                f"physical object may have only one documented identity"
            )
        else:
            seen_addresses[address] = key


def _check_within_row(corpus: Corpus, issues: list[str]) -> None:
    """Within-row consistency rules.

    Relationships no longer carry a `system` column, so the only
    within-row rule is the mappings intentional-drop rationale: a null
    `target_expression` must be accompanied by non-blank `notes`.
    Whitespace-only notes are treated as missing — they carry no
    rationale (assembly guarantees `notes` is a string or null, so the
    stripped-string test is sufficient).
    """
    for key, cm in corpus.column_mappings.items():
        if cm.target_expression is None and not (
            cm.notes and cm.notes.strip()
        ):
            issues.append(
                f"column_mappings[{key}]: target_expression is null and "
                f"notes is empty or blank — intentional drops require "
                f"rationale"
            )


def _check_sql_expressions(
    corpus: Corpus,
    issues: list[str],
    expression_memo: dict[ColumnMappingKey, exp.Expression | None],
) -> None:
    """Parse every join_condition and target_expression; resolve refs.

    For each `join_condition` this enforces a one-shot boolean-predicate
    check on the root, the same expression-only (no
    statement/navigation) and determinism (no volatile function) rules
    applied to `target_expression`, requires at least one column
    reference (a constant predicate relates no endpoints), and then per
    reference: column existence and A/B-table membership, plus a
    coverage check that both endpoints are referenced. For each
    `target_expression` it enforces the expression-only and determinism
    construct rules on the tree,
    requires at least one column reference (a constant expression
    identifies no target dataset), then per reference: column existence,
    and that no reference points back to the source column's own table (a
    value trivially equivalent to itself is not a translation). Every
    failure mode is accumulated; the parse tree is memoized for the
    orchestrator and the linkability check.
    """
    for key, rel in corpus.table_relationships.items():
        try:
            tree = parse_expression(rel.join_condition)
        except ValueError as e:
            issues.append(f"table_relationships[{key}]: {e}")
            continue
        # An ON-clause must be a boolean predicate, not a bare column or
        # scalar. Checked on the root of the tree already built. The
        # operator families named here are kept in agreement with
        # sql_parsing._BOOLEAN_ROOT_TYPES / is_boolean_predicate.
        if not is_boolean_predicate(tree):
            issues.append(
                f"table_relationships[{key}]: join_condition is not a "
                f"boolean predicate — its root must be a comparison "
                f"(=, <>, <, IS [NOT] DISTINCT FROM), logical "
                f"(AND, OR, NOT), membership/range (IN, BETWEEN), null test "
                f"(IS), or pattern-match (LIKE, ILIKE, SIMILAR TO, ~, ~*) "
                f"operator, not a bare column or scalar expression"
            )
        # A join_condition is held to the same bar as a target_expression:
        # it may not navigate/relate rows itself (a relationship already
        # records the pairwise join), and it must be deterministic. An
        # explicit AT TIME ZONE is deterministic and is not flagged.
        nav = contains_statement_or_navigation(tree)
        if nav is not None:
            issues.append(
                f"table_relationships[{key}]: join_condition contains a "
                f"{nav} — it must be a single boolean predicate over the two "
                f"endpoints' columns, not a statement, subquery, or embedded "
                f"join"
            )
        volatile = find_volatile_functions(tree)
        if volatile:
            issues.append(
                f"table_relationships[{key}]: join_condition uses "
                f"volatile/context-dependent function(s) {volatile} — a join "
                f"predicate must be deterministic (same inputs → same result)"
            )
        try:
            refs = extract_column_refs(tree)
        except ValueError as e:
            issues.append(f"table_relationships[{key}]: {e}")
            continue
        # Mirror the target_expression minimum: a join_condition must
        # reference at least one column. Without this a self-relationship's
        # constant predicate (`1 = 1`) passes, because the both-endpoints
        # coverage check below is gated on the endpoints differing.
        if not refs:
            issues.append(
                f"table_relationships[{key}]: join_condition references no "
                f"columns — a join predicate must relate the endpoints' "
                f"columns, not a constant expression"
            )
        # Every ref must resolve and belong to table_a_id/table_b_id
        # (subset); when the endpoints differ, both must be referenced
        # (coverage). Existence and membership are two modes of one loop.
        endpoints = {rel.table_a_id, rel.table_b_id}
        referenced_endpoints: set[str] = set()
        for ref in refs:
            if ref.column_id not in corpus.columns:
                issues.append(
                    f"table_relationships[{key}]: join_condition references "
                    f"unknown column {ref.column_id!r}"
                    + _case_hint(ref.column_id, corpus.columns)
                )
            if ref.table_id in endpoints:
                referenced_endpoints.add(ref.table_id)
            else:
                issues.append(
                    f"table_relationships[{key}]: join_condition references "
                    f"column {ref.column_id!r} in table {ref.table_id!r}, "
                    f"which is neither table_a nor table_b — a relationship "
                    f"is a pairwise join and may not route through a third "
                    f"table"
                )
        if rel.table_a_id != rel.table_b_id:
            missing = endpoints - referenced_endpoints
            if missing:
                issues.append(
                    f"table_relationships[{key}]: join_condition does not "
                    f"reference both endpoints; missing {sorted(missing)} — "
                    f"the condition must link table_a to table_b"
                )

    for key, cm in corpus.column_mappings.items():
        if cm.target_expression is None:
            expression_memo[key] = None
            continue
        try:
            tree = parse_expression(cm.target_expression)
        except ValueError as e:
            issues.append(f"column_mappings[{key}]: {e}")
            continue
        # A target_expression is a single value-producing expression —
        # no SELECT/FROM/JOIN/subquery/CTE/set-op/trailing statement
        # (relating rows is table_relationships' job), so
        # aggregates/windows/casts pass.
        nav = contains_statement_or_navigation(tree)
        if nav is not None:
            issues.append(
                f"column_mappings[{key}]: target_expression contains a "
                f"{nav} — it must be a single value-producing expression, "
                f"not a statement or join (relating rows is "
                f"table_relationships' job)"
            )
        # No volatile/context-dependent functions — an equivalence must
        # be deterministic. Explicit AT TIME ZONE is not flagged.
        volatile = find_volatile_functions(tree)
        if volatile:
            issues.append(
                f"column_mappings[{key}]: target_expression uses "
                f"volatile/context-dependent function(s) {volatile} — an "
                f"equivalence must be deterministic (same inputs → same "
                f"result)"
            )
        try:
            refs = extract_column_refs(tree)
        except ValueError as e:
            issues.append(f"column_mappings[{key}]: {e}")
            continue
        # A non-null target_expression must reference at least one column:
        # a constant expression identifies no target dataset, so it would
        # escape the co-deployment and linkability checks (which key off
        # referenced tables). The documented route for "no target
        # equivalent by design" is target_expression: null plus notes.
        if not refs:
            issues.append(
                f"column_mappings[{key}]: target_expression references no "
                f"columns — a constant expression identifies no target "
                f"dataset; reference at least one target column, or use "
                f"target_expression: null with notes for an intentional drop"
            )
        # source_column_id is db.schema.table.column; its table is the
        # first three segments. Every ref must resolve to a known column,
        # and none may point back at the source column's own table.
        source_table_id = cm.source_column_id.rsplit(".", 1)[0]
        for ref in refs:
            if ref.column_id not in corpus.columns:
                issues.append(
                    f"column_mappings[{key}]: target_expression references "
                    f"unknown column {ref.column_id!r}"
                    + _case_hint(ref.column_id, corpus.columns)
                )
            elif ref.table_id == source_table_id:
                issues.append(
                    f"column_mappings[{key}]: target_expression references "
                    f"column {ref.column_id!r} in the source column's own "
                    f"table {source_table_id!r} — a value trivially "
                    f"equivalent to itself is not a translation; map toward "
                    f"another dataset's columns"
                )
        expression_memo[key] = tree


def _check_cardinality(corpus: Corpus, issues: list[str]) -> None:
    """Validate `cardinality` against the DB CHECK's allowed set.

    Gives the pre-merge dry-run parity with the constraint so an invalid
    value (`1_to_many`, uppercase `MANY_TO_ONE`) fails cleanly before
    merge instead of tripping a raw CHECK violation inside the load
    transaction. None passes — cardinality is optional (not yet
    recorded), matching the nullable column.
    """
    for key, rel in corpus.table_relationships.items():
        if (
            rel.cardinality is not None
            and rel.cardinality not in _VALID_CARDINALITIES
        ):
            issues.append(
                f"table_relationships[{key}]: cardinality "
                f"{rel.cardinality!r} is not one of "
                f"{sorted(_VALID_CARDINALITIES)} (case-sensitive)"
            )


def _check_concept_related_objects(corpus: Corpus, issues: list[str]) -> None:
    """Resolve every concept's `related_object_ids` against the corpus.

    A concept's links are authored, not derived, so the loader verifies
    them: each entry must resolve to a PK defined in the corpus across
    the five id-keyed data spaces (`data_sources`, `schemas`, `tables`,
    `columns`, `concepts`). `systems` is deliberately excluded —
    concepts are about data, and the venue registry is infrastructure; a
    definition that needs to mention a platform names it in prose. An
    unresolved entry is an issue naming the concept and the offending id,
    with a case-mismatch "did you mean …?" hint. Relationship / mapping /
    deployment rows (composite tuple PKs) are not linkable targets.
    """
    known_ids = (
        corpus.data_sources.keys()
        | corpus.schemas.keys()
        | corpus.tables.keys()
        | corpus.columns.keys()
        | corpus.concepts.keys()
    )
    for concept_id, concept in corpus.concepts.items():
        for entry in concept.related_object_ids:
            if entry not in known_ids:
                issues.append(
                    f"concepts[{concept_id}]: related_object_ids entry "
                    f"{entry!r} does not resolve to any data_sources / "
                    f"schemas / tables / columns / concepts id in the corpus"
                    + _case_hint(entry, known_ids)
                )


def _check_concept_anchors(corpus: Corpus, issues: list[str]) -> None:
    """Every concept's anchor prefix must resolve to a real catalog object.

    A concept_id is `<database>.concept.<name>` (data-source level),
    `<database>.<schema>.concept.<name>` (schema level),
    `<database>.<schema>.<table>.concept.<name>` (table level), or
    `<database>.<schema>.<table>.<column>.concept.<name>` (column level).
    The labels before the reserved `concept` segment are the anchor — the
    folder the concepts.yaml was authored under plus any relative `name`
    segments. That anchor must exist: a concepts.yaml in a typo'd or
    phantom folder (or a `name` reaching a phantom table/column) otherwise
    loads under a nonexistent anchor with a fully green pipeline. Concepts
    carry no FK columns, so this is the one asymmetric hole in the
    referential net — every other file type in a phantom folder is caught
    by an FK check; a plain FK cannot express this because the anchor is a
    variable-depth prefix (1 to 4 labels).

    The anchor's depth determines the required object kind: resolution is
    an exact id lookup in that depth's space (data_sources / schemas /
    tables / columns), so a 3-label anchor that is not a table fails, and
    a 4-label anchor that is not a column fails — a column name that
    exists only on a different table does not resolve.

    Malformed-shape ids (wrong label count, missing reserved segment) are a
    wave-1 / DB-CHECK concern and are skipped here rather than double-reported
    (the id-syntax check and the concept_id shape CHECK own that).

    Args:
        corpus: The YAML-derived corpus.
        issues: Issue accumulator.
    """
    for concept_id, concept in corpus.concepts.items():
        segments = concept.concept_id.split(".")
        # Anchor = labels before the reserved `concept` segment, which the
        # shape places second-to-last. Skip malformed ids (owned elsewhere).
        if len(segments) < 3 or segments[-2] != "concept":
            continue
        anchor_labels = segments[:-2]
        anchor = ".".join(anchor_labels)
        if len(anchor_labels) == 1:
            if anchor not in corpus.data_sources:
                issues.append(
                    f"concepts[{concept_id}]: anchor {anchor!r} does not "
                    f"resolve to a data_sources id — a data-source-level "
                    f"concept must live under an existing data source"
                    + _case_hint(anchor, corpus.data_sources)
                )
        elif len(anchor_labels) == 2:
            if anchor not in corpus.schemas:
                issues.append(
                    f"concepts[{concept_id}]: anchor {anchor!r} does not "
                    f"resolve to a schemas id — a schema-level concept must "
                    f"live under an existing schema"
                    + _case_hint(anchor, corpus.schemas)
                )
        elif len(anchor_labels) == 3:
            if anchor not in corpus.tables:
                issues.append(
                    f"concepts[{concept_id}]: anchor {anchor!r} does not "
                    f"resolve to a tables id — a table-level concept must "
                    f"live at an existing table"
                    + _case_hint(anchor, corpus.tables)
                )
        elif len(anchor_labels) == 4:
            if anchor not in corpus.columns:
                issues.append(
                    f"concepts[{concept_id}]: anchor {anchor!r} does not "
                    f"resolve to a columns id — a column-level concept must "
                    f"live at an existing column"
                    + _case_hint(anchor, corpus.columns)
                )
        else:
            issues.append(
                f"concepts[{concept_id}]: anchor {anchor!r} has "
                f"{len(anchor_labels)} labels; a concept anchors to a data "
                f"source (1 label), a schema (2 labels), a table (3 "
                f"labels), or a column (4 labels)"
            )


def _deployment_venues(corpus: Corpus) -> dict[str, set[str]]:
    """Map each documented `table_id` to the set of venues it deploys in.

    Read straight off the expanded `deployment_tables` rows — the
    derived "where does this run?" answer the relationship / mapping
    co-deployment checks consume.
    """
    venues: dict[str, set[str]] = {}
    for dep in corpus.deployment_tables.values():
        venues.setdefault(dep.table_id, set()).add(dep.system)
    return venues


def _check_relationship_codeployment(
    corpus: Corpus, issues: list[str], venues: dict[str, set[str]]
) -> None:
    """Require a relationship's two endpoints to share at least one venue.

    A relationship records join logic; where it can run is derived — the
    intersection of the endpoint tables' deployment sets. An empty
    intersection means the join is runnable nowhere and is rejected. The
    message names both endpoints and their venue sets. Endpoints that do
    not resolve to a documented table are skipped here (already reported
    by `_check_references`).
    """
    for key, rel in corpus.table_relationships.items():
        if (
            rel.table_a_id not in corpus.tables
            or rel.table_b_id not in corpus.tables
        ):
            continue
        a_venues = venues.get(rel.table_a_id, set())
        b_venues = venues.get(rel.table_b_id, set())
        if not (a_venues & b_venues):
            issues.append(
                f"table_relationships[{key}]: endpoints are not co-deployed "
                f"in any venue — {rel.table_a_id!r} deploys in "
                f"{sorted(a_venues)} and {rel.table_b_id!r} in "
                f"{sorted(b_venues)}; a relationship must be runnable "
                f"somewhere"
            )


def _check_mapping_codeployment(
    corpus: Corpus,
    issues: list[str],
    venues: dict[str, set[str]],
    expression_memo: dict[ColumnMappingKey, exp.Expression | None],
) -> None:
    """Require a mapping's referenced tables to share at least one venue.

    A target expression must be computable somewhere: the tables it
    references must be co-deployed in at least one venue (the intersection
    of their deployment sets is non-empty). This applies to single-table
    expressions too: a documented table excluded by every exhaustive
    deployments map deploys nowhere, so a mapping targeting it is
    rejected — deliberately stricter than the multi-table wording of the
    target-expression shape rule (CONTRIBUTING.md wave 2), because the
    equivalent is computable nowhere either way. An
    intentional drop (no expression) or a reference to an undocumented
    table is skipped here (the latter is reported by
    `_check_sql_expressions`).
    """
    for key, cm in corpus.column_mappings.items():
        tree = expression_memo.get(key)
        if tree is None:
            continue
        tables = compute_target_tables_referenced(tree)
        if not tables or any(t not in corpus.tables for t in tables):
            continue
        shared: set[str] | None = None
        for t in tables:
            tv = venues.get(t, set())
            shared = tv if shared is None else (shared & tv)
        if not shared:
            issues.append(
                f"column_mappings[{key}]: referenced tables {tables} are "
                f"not co-deployed in any single venue — the equivalent is "
                f"computable nowhere"
            )


def _check_relationship_pairs(corpus: Corpus, issues: list[str]) -> None:
    """Orientation-duplicate + use_when checks over the unordered pair.

    For a given `relationship_name`, the unordered pair must be unique — a
    reverse-orientation duplicate (`(t1,t2,default)` and
    `(t2,t1,default)`) is a conflict, while the same pair under distinct
    names stays allowed. When an unordered pair carries more than one
    relationship, every one must supply a non-blank `use_when` so a
    consumer can choose between the alternative joins (whitespace-only
    values are treated as missing — they disambiguate nothing).
    """
    # (unordered pair, name) -> the first PK seen on that key.
    by_pair_name: dict[tuple[frozenset[str], str], tuple[str, str, str]] = {}
    # unordered pair -> list of its relationship PKs.
    by_pair: dict[frozenset[str], list[tuple[str, str, str]]] = {}
    for key, rel in corpus.table_relationships.items():
        pair = frozenset({rel.table_a_id, rel.table_b_id})
        b_key = (pair, rel.relationship_name)
        if b_key in by_pair_name:
            issues.append(
                f"table_relationships[{key}]: duplicate relationship for "
                f"the unordered pair {sorted(pair)} under name "
                f"{rel.relationship_name!r} (already defined as "
                f"{by_pair_name[b_key]}) — a join documented in both "
                f"orientations under one name is a conflict; use distinct "
                f"relationship_names for genuinely directional joins"
            )
        else:
            by_pair_name[b_key] = key
        by_pair.setdefault(pair, []).append(key)

    for pair, keys in by_pair.items():
        if len(keys) > 1:
            for key in keys:
                rel = corpus.table_relationships[key]
                if not (rel.use_when and rel.use_when.strip()):
                    issues.append(
                        f"table_relationships[{key}]: the pair "
                        f"{sorted(pair)} carries multiple relationships, so "
                        f"each must set use_when to disambiguate; this one "
                        f"has none"
                    )


def _check_mapping_disambiguation(corpus: Corpus, issues: list[str]) -> None:
    """Multiple mappings per `source_column_id` each need a `use_when`.

    Mirrors relationships' use_when rule — when a source column has more
    than one mapping, every one must set a non-blank `use_when` so a
    consumer can choose among them (whitespace-only values are treated
    as missing). The grain is the source column alone
    (there is no target_system dimension): identity is
    `(source_column_id, mapping_name)`, so all mappings from one source
    column compete regardless of which dataset each targets.
    """
    by_source: dict[str, list[ColumnMappingKey]] = {}
    for key, cm in corpus.column_mappings.items():
        by_source.setdefault(cm.source_column_id, []).append(key)
    for source, keys in by_source.items():
        if len(keys) > 1:
            for key in keys:
                cm = corpus.column_mappings[key]
                if not (cm.use_when and cm.use_when.strip()):
                    issues.append(
                        f"column_mappings[{key}]: source column {source!r} "
                        f"has multiple mappings, so each must set use_when "
                        f"to disambiguate; this one has none"
                    )


def _check_mapping_linkability(
    corpus: Corpus,
    issues: list[str],
    expression_memo: dict[ColumnMappingKey, exp.Expression | None],
) -> None:
    """A multi-table target_expression's tables must be linkable.

    A multi-table equivalence is unusable unless a consumer can join the
    tables it references, so those tables must lie in one connected
    component of the `table_relationships` graph. A single-table (or
    zero-table) expression passes trivially; this is a linkability floor,
    not a grain/path proof. An expression referencing an undocumented
    table is skipped (like `_check_mapping_codeployment`) — the typo is
    already reported as an unknown column by `_check_sql_expressions`,
    and a phantom "not all linkable" issue would just echo it.
    """
    adjacency = _build_relationship_adjacency(corpus)
    for key, cm in corpus.column_mappings.items():
        tree = expression_memo.get(key)
        if tree is None:
            continue
        tables = compute_target_tables_referenced(tree)
        if len(tables) < 2:
            continue
        if any(t not in corpus.tables for t in tables):
            continue
        if not _all_connected(tables, adjacency):
            issues.append(
                f"column_mappings[{key}]: target_expression references "
                f"multiple tables {tables} that are not all linkable via "
                f"documented table_relationships — add the enabling "
                f"relationship so a consumer can join them"
            )


def _build_relationship_adjacency(corpus: Corpus) -> dict[str, set[str]]:
    """Build an undirected table adjacency from every `table_relationships`.

    Edges connect `table_a_id` and `table_b_id`. Ids are venue-free, so a
    single global adjacency serves the linkability floor directly.
    """
    adjacency: dict[str, set[str]] = {}
    for rel in corpus.table_relationships.values():
        adjacency.setdefault(rel.table_a_id, set()).add(rel.table_b_id)
        adjacency.setdefault(rel.table_b_id, set()).add(rel.table_a_id)
    return adjacency


def _all_connected(tables: list[str], adjacency: dict[str, set[str]]) -> bool:
    """Return True if every table in `tables` is reachable from the first.

    A depth-first walk over the undirected relationship graph from
    `tables[0]`; the referenced tables are linkable iff all of them fall
    in the reached set.
    """
    start = tables[0]
    reached: set[str] = {start}
    frontier = [start]
    while frontier:
        node = frontier.pop()
        for neighbor in adjacency.get(node, ()):  # isolated node -> ()
            if neighbor not in reached:
                reached.add(neighbor)
                frontier.append(neighbor)
    return all(t in reached for t in tables)


def _case_hint(object_id: str, known_ids: Iterable[str]) -> str:
    """Suggest a known id if `object_id` differs only by case.

    Identifiers are case-sensitive (ltree does not fold case), and the
    loader mandates lowercase segments (`yaml_discovery._LABEL_RE`), so
    corpus ids are all-lowercase and any uppercase in a reference is
    guaranteed not to resolve — it reads as "unknown". This appends a
    "did you mean …?" hint when a case-insensitive match exists,
    turning the most common footgun into an actionable message.
    Generalized over any id space: the authored-FK checks pass
    `corpus.tables`/`corpus.columns`; SQL checks pass `corpus.columns`
    keys; the concept-link check passes the union of the five id-keyed
    data spaces' keys.

    Args:
        object_id: The unresolved id reference.
        known_ids: The known ids to match against.

    Returns:
        A hint string (leading with ` — `), or `""` if no case-only
        match exists.
    """
    lowered = object_id.lower()
    for known in known_ids:
        if known.lower() == lowered:
            return f" — did you mean {known!r}? (case mismatch)"
    return ""


def validate_update_reason(diff: "Diff") -> None:
    """Enforce `update_reason` discipline against the computed diff.

    Inserts (rows new to the DB) must have `update_reason is None`.
    Updates must have a non-blank `update_reason` (a whitespace-only value
    is treated as missing, matching the stripped-string rule checks).
    Deletes carry no
    constraint — the rationale lives in the git commit that removed
    the row. Applied to the eight authored-row tables;
    `deployment_tables` rows are skipped entirely: they are derived
    (expanded from sparse venue entries) and carry no `update_reason`
    attribute at all, so the rationale for any deployment change is the
    git commit, joinable via `load_audit` — the same stance the model
    already takes on deletes.

    The check reads each change's `new` row straight off the diff (not a
    fresh corpus lookup), so it sees exactly what will be written.

    Args:
        diff: The computed diff against current DB state.

    Raises:
        ValidationError: If any update or insert violates the rule.
            All issues are reported.
    """
    issues: list[str] = []
    for change in diff.inserts:
        if change.table == "deployment_tables":
            continue
        # change.new is the row to be inserted; an insert always
        # carries one.
        if getattr(change.new, "update_reason", None) is not None:
            issues.append(
                f"{change.table}[{change.key}]: insert has non-null "
                f"update_reason — must be null on fresh inserts"
            )
    for change in diff.updates:
        if change.table == "deployment_tables":
            continue
        reason = getattr(change.new, "update_reason", None)
        # Stripped-string semantics (matching the endpoint-pair /
        # multi-mapping use_when and intentional-drop checks of
        # CONTRIBUTING.md wave 2): a whitespace-only reason is treated as
        # missing. Assembly rejects authored whitespace-only freeform
        # values, so this diff-time check guards rows arriving through
        # any other path.
        if reason is None or not reason.strip():
            issues.append(
                f"{change.table}[{change.key}]: update has null or blank "
                f"update_reason — required on every update"
            )
    if issues:
        raise ValidationError(issues)
    logger.info("update_reason discipline passed (no issues)")
