"""Unit tests for sql_parsing.py.

Ids are venue-free, so every fully-qualified reference is exactly four
dotted segments (`database.schema.table.column`) — no leading system.
"""

import pytest

import sql_parsing
from data_model import ColumnRef


def test_parse_expression_simple_identifier() -> None:
    tree = sql_parsing.parse_expression("ocs.general.bene.bene_id")
    refs = sql_parsing.extract_column_refs(tree)
    assert refs == [
        ColumnRef(
            database="ocs",
            schema="general",
            table="bene",
            column="bene_id",
        )
    ]


def test_parse_expression_coalesce_returns_all_refs() -> None:
    sql_text = (
        "COALESCE("
        "edw_prd.claims_vw.bene.bene_extl_id, "
        "edw_prd.claims_vw.bene.bene_lgcy_id"
        ")"
    )
    tree = sql_parsing.parse_expression(sql_text)
    refs = sql_parsing.extract_column_refs(tree)
    assert len(refs) == 2
    assert all(r.database == "edw_prd" for r in refs)
    assert {r.column for r in refs} == {"bene_extl_id", "bene_lgcy_id"}


def test_parse_expression_join_condition_two_refs() -> None:
    sql_text = "ocs.general.bene.bene_id = ocs.general.claim.bene_id"
    tree = sql_parsing.parse_expression(sql_text)
    refs = sql_parsing.extract_column_refs(tree)
    assert len(refs) == 2
    assert {r.table for r in refs} == {"bene", "claim"}


def test_parse_expression_unparsable_raises() -> None:
    with pytest.raises(ValueError, match="Failed to parse SQL expression"):
        sql_parsing.parse_expression("SELECT FROM WHERE GROUP BY")


def test_parse_expression_empty_raises() -> None:
    # An empty string fails to parse in sqlglot (ParseError); parse_expression
    # re-raises it as ValueError.
    with pytest.raises(ValueError, match="Failed to parse SQL expression"):
        sql_parsing.parse_expression("")


def test_parse_expression_unterminated_string_raises_value_error() -> None:
    # A stray apostrophe (e.g. prose pasted into a join_condition) leaves an
    # unterminated string literal, which fails in sqlglot's tokenizer with
    # TokenError — a sibling of ParseError, not a subclass. parse_expression
    # must wrap it in the same ValueError so corpus validation records it as
    # an issue instead of crashing.
    sql_text = "conceptual: when a period's visit count < 3"
    with pytest.raises(ValueError, match="Failed to parse SQL expression"):
        sql_parsing.parse_expression(sql_text)


def test_extract_column_refs_three_part_raises() -> None:
    # Fewer than 4 segments is ambiguous (the data source can't be
    # determined).
    tree = sql_parsing.parse_expression("b.c.d = b.c.d")
    with pytest.raises(ValueError, match="not fully qualified"):
        sql_parsing.extract_column_refs(tree)


def test_extract_column_refs_five_part_raises() -> None:
    # More than 4 segments means a stray system prefix survived the
    # venue-free migration — reject it.
    tree = sql_parsing.parse_expression("sys.db.s.t.col = sys.db.s.t.col")
    with pytest.raises(ValueError, match="not fully qualified"):
        sql_parsing.extract_column_refs(tree)


def test_extract_column_refs_no_columns() -> None:
    tree = sql_parsing.parse_expression("1 + 1")
    assert sql_parsing.extract_column_refs(tree) == []


def test_compute_target_tables_referenced_sorted_deduped() -> None:
    sql_text = "COALESCE(db.s.t1.a, db.s.t2.b, db.s.t1.c)"
    tree = sql_parsing.parse_expression(sql_text)
    out = sql_parsing.compute_target_tables_referenced(tree)
    assert out == ["db.s.t1", "db.s.t2"]


def test_compute_target_tables_referenced_returns_all_tables() -> None:
    # There is no target_system to filter by — every referenced table is
    # returned (ids are venue-free).
    sql_text = "db.s.t.a + other.s2.u.b"
    tree = sql_parsing.parse_expression(sql_text)
    out = sql_parsing.compute_target_tables_referenced(tree)
    assert out == ["db.s.t", "other.s2.u"]


def test_compute_target_tables_referenced_none_returns_empty() -> None:
    assert sql_parsing.compute_target_tables_referenced(None) == []


# ---------------------------------------------------------------------------
# Characterization corpus — pins segment recovery against sqlglot changes.
# ---------------------------------------------------------------------------


def test_extract_column_refs_preserves_case() -> None:
    # ltree/identifier matching is case-sensitive; segment recovery must
    # preserve the original case exactly (no folding). Corpus ids are
    # all-lowercase (yaml_discovery rejects uppercase segments), so an
    # uppercase SQL reference reads as unknown and corpus_validation's
    # _case_hint appends a "did you mean ...? (case mismatch)" hint —
    # which only works if the parser hands it the unfolded original.
    tree = sql_parsing.parse_expression(
        "EDW_PRD.CLAIMS_VW.bene.bene_MBI_id"
    )
    assert sql_parsing.extract_column_refs(tree) == [
        ColumnRef(
            database="EDW_PRD",
            schema="CLAIMS_VW",
            table="bene",
            column="bene_MBI_id",
        )
    ]


def test_extract_column_refs_quoted_reserved_word_column() -> None:
    # A column named for a reserved word must be quoted; the recovered
    # segment is the unquoted name.
    tree = sql_parsing.parse_expression('ocs.general.bene."order"')
    refs = sql_parsing.extract_column_refs(tree)
    assert len(refs) == 1
    assert refs[0].column == "order"
    assert refs[0].table_id == "ocs.general.bene"


def test_extract_column_refs_inside_case_expression() -> None:
    sql_text = (
        "CASE WHEN ocs.general.bene.flag > 0 "
        "THEN ocs.general.bene.a "
        "ELSE ocs.general.claim.b END"
    )
    refs = sql_parsing.extract_column_refs(sql_parsing.parse_expression(sql_text))
    assert len(refs) == 3
    assert {r.column for r in refs} == {"flag", "a", "b"}


def test_extract_column_refs_inside_cast() -> None:
    # The CAST target type (TEXT) is not a column reference.
    tree = sql_parsing.parse_expression("CAST(ocs.general.bene.bene_id AS TEXT)")
    refs = sql_parsing.extract_column_refs(tree)
    assert len(refs) == 1
    assert refs[0].column == "bene_id"


def test_extract_column_refs_multiple_in_arithmetic() -> None:
    sql_text = "ocs.general.bene.a + ocs.general.bene.b * ocs.general.claim.c"
    refs = sql_parsing.extract_column_refs(sql_parsing.parse_expression(sql_text))
    assert len(refs) == 3
    assert {r.column for r in refs} == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# contains_statement_or_navigation (M1)
# ---------------------------------------------------------------------------


# Canary corpus: one shape per navigation entry of
# `sql_parsing._NAVIGATION_NODES`, paired with the exact label the check
# reports. Asserting the label (not merely "rejected") pins the
# class-to-label pairing that reaches the author-facing validation
# message, so a sqlglot version bump that reshuffles node classes — or a
# mis-paired table entry — fails here instead of silently degrading the
# message or the guard.
_NAVIGATION_CANARY_CASES: list[tuple[str, str]] = [
    ("(SELECT max(d.s.t.a) FROM d.s.t)", "subquery"),
    ("SELECT d.s.t.a FROM d.s.t", "SELECT statement"),
    ("SELECT d.s.t.a UNION SELECT d.s.u.b", "UNION set-operation"),
    ("SELECT d.s.t.a INTERSECT SELECT d.s.u.b", "INTERSECT set-operation"),
    ("SELECT d.s.t.a EXCEPT SELECT d.s.u.b", "EXCEPT set-operation"),
    ("d.s.t.a; DROP TABLE d.s.t", "multiple statements"),
    # sqlglot's generic fallback for a statement it does not model with a
    # dedicated class (it logs "falling back to parsing as a 'Command'").
    ("VACUUM d.s.t", "SQL command/statement"),
]


# Each is a full DML/DDL statement carrying qualified column references —
# the shape that previously cleared every guard (no navigation node seen,
# real columns extracted) and stored verbatim.
_STATEMENT_CANARY_CASES: list[tuple[str, str]] = [
    ("UPDATE d.s.t SET d.s.t.a = e.f.g.h", "UPDATE statement"),
    ("DELETE FROM d.s.t WHERE d.s.t.a = e.f.g.h", "DELETE statement"),
    ("INSERT INTO d.s.t VALUES (e.f.g.h)", "INSERT statement"),
    (
        "MERGE INTO d.s.t USING e.f.g ON d.s.t.a = e.f.g.h "
        "WHEN MATCHED THEN UPDATE SET d.s.t.b = 1",
        "MERGE statement",
    ),
    ("CREATE TABLE d.s.t (a int)", "CREATE statement"),
    ("DROP TABLE d.s.t", "DROP statement"),
    ("ALTER TABLE d.s.t ADD COLUMN a int", "ALTER statement"),
    ("GRANT SELECT ON d.s.t TO some_role", "GRANT statement"),
    ("TRUNCATE TABLE d.s.t", "TRUNCATE statement"),
]


# Three table entries can never be the *reported* label: their node
# always sits below a node the walk reaches first (expr.walk() is BFS,
# root-first, so the wrapping construct wins) — a JOIN and a FROM clause
# under their `Select`, a CTE under the `Select`/`Subquery` that owns the
# `WITH`. They are canaried by class instead: the shape must still
# contain a node of the class the table pairs with that label, and must
# still be rejected. Each tuple is (sql_text, label the entry carries).
_SHADOWED_NAVIGATION_CASES: list[tuple[str, str]] = [
    ("SELECT d.s.t.a FROM d.s.t JOIN d.s.u ON d.s.t.k = d.s.u.k", "JOIN"),
    ("WITH c AS (SELECT 1) SELECT * FROM c", "CTE"),
    ("SELECT d.s.t.a FROM d.s.t", "FROM clause"),
]


@pytest.mark.parametrize(
    ("sql_text", "expected_label"),
    _NAVIGATION_CANARY_CASES + _STATEMENT_CANARY_CASES,
)
def test_contains_statement_or_navigation_flags_navigation(
    sql_text: str, expected_label: str
) -> None:
    tree = sql_parsing.parse_expression(sql_text)
    assert sql_parsing.contains_statement_or_navigation(tree) == expected_label


@pytest.mark.parametrize(("sql_text", "label"), _SHADOWED_NAVIGATION_CASES)
def test_contains_statement_or_navigation_flags_shadowed_nodes(
    sql_text: str, label: str
) -> None:
    # The shape is rejected (by the wrapping construct), and the class the
    # table pairs with `label` is really the class sqlglot emits for it —
    # the part a label assertion cannot reach for these three entries.
    tree = sql_parsing.parse_expression(sql_text)
    assert sql_parsing.contains_statement_or_navigation(tree) is not None
    node_types = tuple(
        node_type
        for node_type, entry_label in sql_parsing._NAVIGATION_NODES
        if entry_label == label
    )
    assert any(isinstance(node, node_types) for node in tree.walk())


def test_contains_statement_or_navigation_canary_covers_node_table() -> None:
    # Every entry in the navigation table must have a canary case above;
    # an entry added to the table without one would otherwise dodge the
    # version-bump canary and lose its guard silently.
    covered = {label for _, label in _NAVIGATION_CANARY_CASES}
    covered |= {label for _, label in _STATEMENT_CANARY_CASES}
    covered |= {label for _, label in _SHADOWED_NAVIGATION_CASES}
    assert covered == {label for _, label in sql_parsing._NAVIGATION_NODES}


@pytest.mark.parametrize(
    "sql_text",
    [
        "d.s.t.a",  # bare column
        "d.s.t.a + d.s.t.b",  # scalar arithmetic
        "CASE WHEN d.s.t.a > 0 THEN 1 ELSE 0 END",  # conditional
        "SUM(d.s.t.a)",  # aggregate
        "ROW_NUMBER() OVER (PARTITION BY d.s.t.a ORDER BY d.s.t.b)",  # window
    ],
)
def test_contains_statement_or_navigation_passes_expressions(sql_text: str) -> None:
    tree = sql_parsing.parse_expression(sql_text)
    assert sql_parsing.contains_statement_or_navigation(tree) is None


# ---------------------------------------------------------------------------
# find_volatile_functions (M3)
# ---------------------------------------------------------------------------


# Canary corpus: one invocation per denylisted function (both spellings
# where they differ). sqlglot decides per version whether each parses to
# a dedicated node class or an `Anonymous` call, and coverage depends on
# that split (_VOLATILE_NODE_TYPES vs. VOLATILE_FUNCTION_DENYLIST) — so
# exercising every function here turns a sqlglot upgrade that reshuffles
# node classes into a test failure instead of a silent coverage loss.
_VOLATILE_CANARY_CASES: list[tuple[str, str]] = [
    # Wall-clock / transaction time.
    ("now()", "current_timestamp"),
    ("current_timestamp", "current_timestamp"),
    ("current_date", "current_date"),
    ("current_time", "current_time"),
    ("localtime", "localtime"),
    ("localtimestamp", "localtimestamp"),
    ("clock_timestamp()", "clock_timestamp"),
    ("statement_timestamp()", "statement_timestamp"),
    ("transaction_timestamp()", "transaction_timestamp"),
    ("timeofday()", "timeofday"),
    # Single-argument age(ts) is now()-dependent.
    ("age(d.s.t.ts)", "age"),
    # Randomness.
    ("random()", "random"),
    ("gen_random_uuid()", "gen_random_uuid"),
    ("uuid_generate_v4()", "uuid_generate_v4"),
    # Sequences.
    ("nextval('db.s.seq')", "nextval"),
    ("currval('db.s.seq')", "currval"),
    ("lastval()", "lastval"),
    ("setval('db.s.seq', 1)", "setval"),
    # Randomness (seed mutation).
    ("setseed(0.5)", "setseed"),
    # Session / connection context.
    ("current_user", "current_user"),
    ("session_user", "session_user"),
    ("current_role", "current_role"),
    ("current_schema", "current_schema"),
    ("current_database()", "current_database"),
    ("current_catalog", "current_catalog"),
    ("current_setting('x')", "current_setting"),
    ("version()", "version"),
    ("txid_current()", "txid_current"),
    ("pg_backend_pid()", "pg_backend_pid"),
    ("pg_current_xact_id()", "pg_current_xact_id"),
    ("inet_client_addr()", "inet_client_addr"),
    # Timing side effects.
    ("pg_sleep(1)", "pg_sleep"),
]


@pytest.mark.parametrize(("sql_text", "expected"), _VOLATILE_CANARY_CASES)
def test_find_volatile_functions_flags_volatile(sql_text: str, expected: str) -> None:
    tree = sql_parsing.parse_expression(sql_text)
    assert expected in sql_parsing.find_volatile_functions(tree)


def test_find_volatile_functions_canary_covers_entire_denylist() -> None:
    # Every denylisted name must have a canary case above; a name added to
    # the denylist without one would otherwise dodge the version-bump canary.
    covered = {expected for _, expected in _VOLATILE_CANARY_CASES}
    assert covered >= sql_parsing.VOLATILE_FUNCTION_DENYLIST - {"now"}
    # `now` is the alternate spelling of current_timestamp: sqlglot folds
    # now() into the CurrentTimestamp node, so no parse can report "now".
    assert ("now()", "current_timestamp") in _VOLATILE_CANARY_CASES


# Session-state and sleep/seed denylist entries, exercised in both loader
# contexts: a value-producing expression (target_expression shape) and a
# boolean predicate (join_condition shape). Both validation paths call
# find_volatile_functions on the parsed tree, so a hit here rejects the
# function in either field.
_SESSION_STATE_CASES: list[tuple[str, str]] = [
    ("current_database()", "current_database"),
    ("current_catalog", "current_catalog"),
    ("current_role", "current_role"),
    ("pg_sleep(1)", "pg_sleep"),
    ("setseed(0.5)", "setseed"),
]


@pytest.mark.parametrize(("call", "expected"), _SESSION_STATE_CASES)
def test_find_volatile_functions_flags_session_state_in_expression_context(
    call: str, expected: str
) -> None:
    # target_expression shape: the call embedded in a scalar expression.
    tree = sql_parsing.parse_expression(f"COALESCE(d.s.t.a, {call})")
    assert expected in sql_parsing.find_volatile_functions(tree)


@pytest.mark.parametrize(("call", "expected"), _SESSION_STATE_CASES)
def test_find_volatile_functions_flags_session_state_in_predicate_context(
    call: str, expected: str
) -> None:
    # join_condition shape: the call embedded in a boolean predicate.
    tree = sql_parsing.parse_expression(f"d.s.t.a = d.s.u.b AND d.s.t.a = {call}")
    assert sql_parsing.is_boolean_predicate(tree) is True
    assert expected in sql_parsing.find_volatile_functions(tree)


def test_find_volatile_functions_flags_age_in_expression_context() -> None:
    # target_expression shape: age(ts) buried in a scalar expression.
    tree = sql_parsing.parse_expression("COALESCE(d.s.t.a, age(d.s.t.ts))")
    assert "age" in sql_parsing.find_volatile_functions(tree)


def test_find_volatile_functions_flags_age_in_predicate_context() -> None:
    # join_condition shape: age(ts) inside a boolean predicate.
    tree = sql_parsing.parse_expression(
        "d.s.t.a = d.s.u.b AND d.s.t.a = age(d.s.t.ts)"
    )
    assert sql_parsing.is_boolean_predicate(tree) is True
    assert "age" in sql_parsing.find_volatile_functions(tree)


def test_find_volatile_functions_passes_qualified_current_role_column() -> None:
    # `current_role` parses as a bare Column in the postgres dialect, so
    # the volatile check matches unqualified names only — a genuine,
    # fully qualified column that happens to be named current_role must
    # not be flagged as volatile.
    tree = sql_parsing.parse_expression("d.s.t.current_role = d.s.u.b")
    assert sql_parsing.find_volatile_functions(tree) == []


def test_find_volatile_functions_flags_nested_volatile() -> None:
    # A volatile call buried inside an otherwise-clean expression is found.
    tree = sql_parsing.parse_expression("COALESCE(d.s.t.a, gen_random_uuid())")
    assert sql_parsing.find_volatile_functions(tree) == ["gen_random_uuid"]


@pytest.mark.parametrize(
    "sql_text",
    [
        "d.s.t.a AT TIME ZONE 'UTC'",  # explicit zone is deterministic
        "date_trunc('month', d.s.t.a)",
        "COALESCE(d.s.t.a, d.s.t.b)",
        "split_part(d.s.t.a, ' ', 1)",
    ],
)
def test_find_volatile_functions_passes_deterministic(sql_text: str) -> None:
    tree = sql_parsing.parse_expression(sql_text)
    assert sql_parsing.find_volatile_functions(tree) == []


def test_find_volatile_functions_reports_name() -> None:
    tree = sql_parsing.parse_expression("random()")
    assert sql_parsing.find_volatile_functions(tree) == ["random"]


# ---------------------------------------------------------------------------
# is_boolean_predicate (G)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql_text",
    [
        "d.s.t.a = d.s.u.b",
        # The full logical family the docstring accepts (And / Or / Not).
        "d.s.t.a AND d.s.u.b",
        "d.s.t.a OR d.s.u.b",
        "NOT d.s.t.a",
        "d.s.t.a IN (1, 2)",
        "d.s.t.a IS NOT NULL",
        "d.s.t.a <> d.s.u.b",
        # The full comparison family (documented in CONTRIBUTING.md): the
        # strict/ordered operators parse to GT/GTE/LTE roots.
        "d.s.t.a > d.s.u.b",
        "d.s.t.a >= d.s.u.b",
        "d.s.t.a <= d.s.u.b",
        "d.s.t.a < d.s.u.b",
        "d.s.t.a LIKE 'x%'",
        "d.s.t.a ILIKE 'x%'",
        "d.s.t.a BETWEEN 1 AND 2",
        "(d.s.t.a = d.s.u.b)",  # parenthesized predicate
        # Null-safe comparisons (NullSafeEQ / NullSafeNEQ).
        "d.s.t.a IS NOT DISTINCT FROM d.s.u.b",
        "d.s.t.a IS DISTINCT FROM d.s.u.b",
        # Postgres regex-match operators (RegexpLike / RegexpILike).
        "d.s.t.a ~ d.s.u.b",
        "d.s.t.a ~* d.s.u.b",
        # SQL SIMILAR TO (SimilarTo).
        "d.s.t.a SIMILAR TO d.s.u.b",
    ],
)
def test_is_boolean_predicate_true(sql_text: str) -> None:
    tree = sql_parsing.parse_expression(sql_text)
    assert sql_parsing.is_boolean_predicate(tree) is True


@pytest.mark.parametrize(
    "sql_text",
    [
        "d.s.t.a",  # bare column
        "1",  # literal
        "d.s.t.a + d.s.u.b",  # scalar arithmetic
        "SUM(d.s.t.a)",  # aggregate scalar
    ],
)
def test_is_boolean_predicate_false(sql_text: str) -> None:
    tree = sql_parsing.parse_expression(sql_text)
    assert sql_parsing.is_boolean_predicate(tree) is False
