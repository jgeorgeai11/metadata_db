"""sql_parsing.py — parse SQL expressions and extract column refs.

Two SQL fields participate in validation:
  - `column_mappings.target_expression` — a portable scalar SQL
    expression in the target system.
  - `table_relationships.join_condition` — an `ON`-style boolean
    expression in the relationship's system.

Both must (a) parse as Postgres SQL and (b) reference columns by their
fully-qualified 4-segment path (`database.schema.table.column`). Ids are
venue-free — there is no leading system segment — so the four dotted
segments map directly onto sqlglot's `Column.catalog/db/table/name`
(`database` -> catalog, `schema` -> db, `table` -> table, `column` ->
name).
"""

import sys
from pathlib import Path

import sqlglot
import sqlglot.expressions as exp

# The vendored logging package (code/lib/logconfig) is resolved from
# this file's own location, so imports work from any working directory
# (not just the repo root) and CI never depends on untracked .claude/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import get_logger
from data_model import ColumnRef

logger = get_logger(__name__)


def parse_expression(
    sql_text: str, dialect: str = "postgres"
) -> exp.Expression:
    """Parse a SQL expression and return the sqlglot tree.

    Args:
        sql_text: The SQL to parse — a scalar expression or a boolean
            `ON`-style clause.
        dialect: sqlglot dialect; defaults to `postgres` since that's
            the metadata_db's host dialect.

    Returns:
        The parsed `sqlglot.Expression`.

    Raises:
        ValueError: If tokenizing or parsing fails, or parsing returns
            nothing. The offending text is included in the message.
    """
    try:
        tree = sqlglot.parse_one(sql_text, dialect=dialect)
    # TokenError is a sibling of ParseError (both subclass SqlglotError),
    # raised for lexing failures such as an unterminated string literal
    # (e.g. a stray apostrophe in prose). Catch both so every malformed
    # expression surfaces as the same ValueError contract.
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as e:
        # Log at debug with the library-side detail the caller lacks (the
        # dialect used); the raised ValueError already carries sql_text and
        # the original error via `from e`. Differentiating here avoids
        # logging the same message text twice once the caller logs the caught
        # exception (logging skill: avoid duplicate logging).
        logger.debug(
            f"sqlglot failed to parse SQL under dialect {dialect!r}: {e}"
        )
        raise ValueError(
            f"Failed to parse SQL expression {sql_text!r}: {e}"
        ) from e
    if tree is None:  # pragma: no cover - sqlglot raises before this
        raise ValueError(
            f"SQL expression {sql_text!r} parsed to an empty tree"
        )
    return tree


def extract_column_refs(expr: exp.Expression) -> list[ColumnRef]:
    """Walk the tree and return every fully-qualified column reference.

    Each column must be written with all four dotted segments —
    `database.schema.table.column`. A reference with fewer segments is
    ambiguous (the data source can't be determined); a reference with
    more means a stray system prefix survived the venue-free migration.
    Both raise. Segment recovery is delegated to `_collect_segments`,
    which handles how sqlglot splits an over-long identifier across a
    `Column` node and its `Dot` parents.

    Args:
        expr: A parsed sqlglot expression tree.

    Returns:
        A `ColumnRef` for every column reference found, in tree order.
        Duplicates are preserved; the caller may dedupe.

    Raises:
        ValueError: If any column reference does not have exactly four
            segments.
    """
    refs: list[ColumnRef] = []
    for col in expr.find_all(exp.Column):
        segments = _collect_segments(col)
        if len(segments) != 4:
            raise ValueError(
                f"Column reference {col.sql()!r} is not fully qualified "
                f"(expected 4 dotted segments "
                f"`database.schema.table.column`, got "
                f"{len(segments)})"
            )
        database, schema, table, column = segments
        refs.append(
            ColumnRef(
                database=database,
                schema=schema,
                table=table,
                column=column,
            )
        )
    return refs


def _collect_segments(col: exp.Column) -> list[str]:
    """Return the dotted segments of a `Column` reference, left-to-right.

    A standard SQL identifier has at most four parts, which sqlglot
    stores on the `Column` as `catalog`, `db`, `table`, and `name` —
    exactly the four segments of a venue-free
    `database.schema.table.column` reference. An over-long reference
    (e.g. a stray leading `system` segment that survived the migration)
    parses as a four-part `Column` nested as the left-hand side of a
    `Dot` node holding the trailing segment(s).

    This recovers the full list by reading the qualifier parts present
    on the `Column`, then walking up the chain of `Dot` parents — while
    the running node stays the Dot's left-hand side — appending each
    right-hand identifier. The caller checks the result is exactly four
    segments.
    """
    # Qualifier parts carried directly on the Column (catalog/db/table),
    # then the column's own name.
    segments: list[str] = [
        col.args[attr].name
        for attr in ("catalog", "db", "table")
        if col.args.get(attr)
    ]
    segments.append(col.name)

    parent = col.parent
    # Walk up Dot chain accumulating right-hand identifiers. Stop as
    # soon as we leave the Dot chain (or there's no parent).
    cursor: exp.Expression | None = parent
    child: exp.Expression = col
    while isinstance(cursor, exp.Dot) and cursor.this is child:
        rhs = cursor.expression
        if isinstance(rhs, exp.Identifier):
            segments.append(rhs.name)
        else:  # pragma: no cover - sqlglot always uses Identifier here
            break
        child = cursor
        cursor = cursor.parent
    return segments


# Navigation / statement nodes that make a fragment more than a single
# value-producing expression. Order here is cosmetic — every class is
# distinct, so results follow expr.walk() (BFS, root-first), which reports
# a wrapping construct before what it wraps (a subquery before its inner
# SELECT). See `contains_statement_or_navigation`.
#
# The DML/DDL statement classes are included because sqlglot parses a full
# statement into its own root node (e.g. `update t set a.b.c.d = e.f.g.h`
# parses to an `exp.Update`), which would otherwise clear every guard —
# `contains_statement_or_navigation` saw no navigation node and
# `extract_column_refs` found qualified columns — and store verbatim as a
# `target_expression`. Stored expressions are never executed by this
# codebase; rejecting statements protects the "single value-producing
# expression" contract and any downstream consumer that templates the stored
# text into runnable SQL. `Command` is sqlglot's generic fallback for a
# statement it does not model with a dedicated class, so it backstops
# anything the explicit classes miss. Class names verified against the
# pinned sqlglot (30.x) — the canary tests in unit_tests/test_sql_parsing.py
# exercise each so a version bump that renames a class fails loudly.
_NAVIGATION_NODES: tuple[tuple[type[exp.Expression], str], ...] = (
    (exp.Subquery, "subquery"),
    (exp.CTE, "CTE"),
    (exp.With, "CTE"),
    (exp.Intersect, "INTERSECT set-operation"),
    (exp.Except, "EXCEPT set-operation"),
    (exp.Union, "UNION set-operation"),
    (exp.Join, "JOIN"),
    (exp.Select, "SELECT statement"),
    (exp.From, "FROM clause"),
    (exp.Block, "multiple statements"),
    # DML / DDL statements — a stored expression is never one of these.
    (exp.Update, "UPDATE statement"),
    (exp.Insert, "INSERT statement"),
    (exp.Delete, "DELETE statement"),
    (exp.Merge, "MERGE statement"),
    (exp.Create, "CREATE statement"),
    (exp.Drop, "DROP statement"),
    (exp.Alter, "ALTER statement"),
    (exp.Grant, "GRANT statement"),
    (exp.TruncateTable, "TRUNCATE statement"),
    # Generic fallback for any statement sqlglot does not model above.
    (exp.Command, "SQL command/statement"),
)


# Case-insensitive names of volatile / context-dependent functions an
# equivalence must never depend on (a stable equivalence yields the same
# result for the same inputs). Every function is listed by name here even
# when sqlglot parses it into a dedicated node class (matched via
# _VOLATILE_NODE_TYPES below) — so if a future sqlglot version demotes a
# function back to an `Anonymous` call, the name match still catches it.
# `now` and `current_timestamp` are the same node; both names are listed
# so either spelling is recognizable. An explicit `AT TIME ZONE '<zone>'`
# is deterministic and is NOT here.
VOLATILE_FUNCTION_DENYLIST: frozenset[str] = frozenset(
    {
        # Wall-clock / transaction time.
        "now",
        "current_timestamp",
        "current_date",
        "current_time",
        "localtime",
        "localtimestamp",
        "clock_timestamp",
        "statement_timestamp",
        "transaction_timestamp",
        "timeofday",
        # Single-argument age(ts) is now()-dependent (age from the current
        # date). The name-based denylist also rejects the immutable
        # two-argument age(ts, ts) form — an accepted over-rejection (same
        # trade-off as `version`; argument-arity analysis costs more than
        # the constraint is worth). Author the two-argument intent as an
        # explicit subtraction instead.
        "age",
        # Randomness.
        "random",
        "gen_random_uuid",
        "uuid_generate_v4",
        "setseed",
        # Sequences.
        "nextval",
        "currval",
        "lastval",
        "setval",
        # Session / connection context.
        "current_user",
        "session_user",
        "current_role",
        "current_schema",
        "current_database",
        "current_catalog",
        "current_setting",
        "version",
        "txid_current",
        "pg_backend_pid",
        "pg_current_xact_id",
        "inet_client_addr",
        # Timing side effects.
        "pg_sleep",
    }
)


# sqlglot parses several volatile functions into dedicated node classes
# rather than an `Anonymous` call, so a name match on the denylist alone
# would miss them. This maps each such class to the denylist name it
# represents; denylist entries without a class here (nextval,
# clock_timestamp, current_setting, …) parse as `Anonymous` and are
# matched by name. Which functions get a dedicated class can change
# between sqlglot versions — the canary cases in
# unit_tests/test_sql_parsing.py exercise every denylisted function, so a
# version bump that reshuffles node classes fails tests instead of
# silently losing coverage.
_VOLATILE_NODE_TYPES: tuple[tuple[type[exp.Expression], str], ...] = (
    (exp.CurrentTimestamp, "current_timestamp"),  # also now()
    (exp.CurrentDate, "current_date"),
    (exp.CurrentTime, "current_time"),
    (exp.Localtime, "localtime"),
    (exp.Localtimestamp, "localtimestamp"),
    (exp.CurrentUser, "current_user"),
    (exp.SessionUser, "session_user"),
    (exp.CurrentSchema, "current_schema"),
    (exp.CurrentDatabase, "current_database"),
    (exp.CurrentCatalog, "current_catalog"),
    (exp.CurrentRole, "current_role"),
    (exp.CurrentVersion, "version"),
    (exp.Rand, "random"),
    (exp.Uuid, "gen_random_uuid"),
)


# Parenthesis-less SQL context keywords. Depending on the sqlglot
# version and dialect, some parse to a dedicated node class and some to
# a bare, unqualified `Column` (e.g. `current_role` under the postgres
# dialect in sqlglot 30.x) — a form neither the node-class table nor the
# `Anonymous` name match sees. `find_volatile_functions` therefore also
# checks unqualified single-name `Column` nodes against this set. A
# fully qualified reference (`db.schema.table.current_role`) is a
# genuine column and is never flagged.
_CONTEXT_KEYWORD_NAMES: frozenset[str] = frozenset(
    {
        "current_timestamp",
        "current_date",
        "current_time",
        "localtime",
        "localtimestamp",
        "current_user",
        "session_user",
        "current_role",
        "current_schema",
        "current_catalog",
        "current_database",
    }
)


# Root node types whose value is a boolean predicate — comparison
# (incl. null-safe and pattern-matching), logical, and membership
# operators. A bare column/literal/scalar arithmetic root is not among
# these. See `is_boolean_predicate`. The null-safe comparisons
# (`IS [NOT] DISTINCT FROM`), the Postgres regex operators (`~` / `~*`),
# and `SIMILAR TO` each parse to a dedicated node class that is not a
# subclass of the plain comparison nodes above, so every family is listed
# explicitly.
_BOOLEAN_ROOT_TYPES: tuple[type[exp.Expression], ...] = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.And,
    exp.Or,
    exp.Not,
    exp.In,
    exp.Is,
    exp.Like,
    exp.ILike,
    exp.Between,
    # Null-safe comparisons: `IS DISTINCT FROM` / `IS NOT DISTINCT FROM`.
    exp.NullSafeEQ,
    exp.NullSafeNEQ,
    # Postgres regex-match operators: `~` (RegexpLike) / `~*` (RegexpILike).
    exp.RegexpLike,
    exp.RegexpILike,
    # SQL `SIMILAR TO` pattern match.
    exp.SimilarTo,
)


def contains_statement_or_navigation(expr: exp.Expression) -> str | None:
    """Return the kind of any statement/navigation node in `expr`, else None.

    A `column_mappings.target_expression` must be a single value-producing
    expression — it computes a value, it does not navigate or relate rows.
    This flags the constructs that violate that: a `SELECT` / `FROM` /
    `JOIN` / subquery / CTE / set operation (`UNION` / `INTERSECT` /
    `EXCEPT`), or a trailing second statement (which sqlglot wraps in a
    `Block`).

    Args:
        expr: A parsed expression tree (`self` is inspected too, not only
            descendants).

    Returns:
        A short human-readable label for the first offending node found
        (e.g. `"subquery"`, `"JOIN"`), or None when the tree is a plain
        expression.
    """
    for node in expr.walk():
        for node_type, label in _NAVIGATION_NODES:
            if isinstance(node, node_type):
                return label
    return None


def find_volatile_functions(expr: exp.Expression) -> list[str]:
    """Return the denylisted volatile function names found in `expr`.

    Matches the dedicated node classes sqlglot emits for functions like
    `now()` / `current_user` (`_VOLATILE_NODE_TYPES`), `Anonymous` calls
    whose name is in `VOLATILE_FUNCTION_DENYLIST` (e.g. `nextval`,
    `pg_sleep`), and bare, unqualified column-shaped context keywords
    (`_CONTEXT_KEYWORD_NAMES`, e.g. `current_role`, which the postgres
    dialect parses as a plain `Column`). An explicit `AT TIME ZONE
    '<zone>'` parses to an `AtTimeZone` node, is not on the denylist,
    and is not flagged.

    Args:
        expr: A parsed expression tree.

    Returns:
        Sorted, de-duplicated list of the denylisted names present.
    """
    found: set[str] = set()
    for node in expr.walk():
        for node_type, name in _VOLATILE_NODE_TYPES:
            if isinstance(node, node_type):
                found.add(name)
        if isinstance(node, exp.Anonymous):
            fn_name = node.name.lower()
            if fn_name in VOLATILE_FUNCTION_DENYLIST:
                found.add(fn_name)
        # A parenthesis-less context keyword can parse as a bare Column;
        # only a completely unqualified single name matches (a 4-part
        # qualified reference is a genuine column).
        if (
            isinstance(node, exp.Column)
            and not node.args.get("table")
            and not node.args.get("db")
            and not node.args.get("catalog")
            and node.name.lower() in _CONTEXT_KEYWORD_NAMES
        ):
            found.add(node.name.lower())
    return sorted(found)


def is_boolean_predicate(expr: exp.Expression) -> bool:
    """Return True when `expr`'s root is a boolean-valued predicate.

    A `table_relationships.join_condition` must be an `ON`-style boolean
    expression, so its root must be one of the accepted predicate
    families (this list is kept in agreement with the join-condition
    error text in `corpus_validation._check_sql_expressions`):
      - comparison: `=`, `<>`, `<`, `<=`, `>`, `>=`
      - null-safe comparison: `IS [NOT] DISTINCT FROM`
      - logical: `AND`, `OR`, `NOT`
      - membership / range: `IN`, `BETWEEN`
      - null / boolean test: `IS`
      - pattern match: `LIKE`, `ILIKE`, `SIMILAR TO`, `~`, `~*`
    A bare column, a literal, or a scalar arithmetic expression (`a + b`)
    is not a predicate. A parenthesized predicate (`(a = b)`) is
    unwrapped first.

    Args:
        expr: A parsed expression tree.

    Returns:
        True if the (paren-unwrapped) root is a boolean predicate.
    """
    node: exp.Expression = expr
    while isinstance(node, exp.Paren):
        node = node.this
    return isinstance(node, _BOOLEAN_ROOT_TYPES)


def compute_target_tables_referenced(
    expr: exp.Expression | None,
) -> list[str]:
    """Return sorted, de-duped `table_id`s referenced by `expr`.

    Used to populate `column_mappings.target_tables_referenced` from a
    parsed `target_expression`. Ids are venue-free and a mapping has no
    `target_system`, so every table the expression references counts —
    there is no system to filter by (the DDL comment already describes
    this unfiltered semantics). A None expression (intentional drop)
    yields an empty list.

    Args:
        expr: A parsed expression tree, or None for an intentional drop.

    Returns:
        Sorted, de-duplicated list of `table_id` strings.
    """
    if expr is None:
        return []
    refs = extract_column_refs(expr)
    tables = {ref.table_id for ref in refs}
    return sorted(tables)
