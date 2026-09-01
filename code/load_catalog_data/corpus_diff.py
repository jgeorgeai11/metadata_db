"""corpus_diff.py — compute a `Diff` between a `Corpus` and a `DbState`.

Per-PK classification:
  - in corpus only → insert
  - in DB only     → delete
  - in both, content fields differ → update
  - in both, content fields identical → no-op (preserves idempotency)

PK changes are surfaced as one delete + one insert (never as an update)
because PKs are derived from YAML paths and an in-place identity
change would falsely preserve `insert_ts` and skip the `_hstry` write.

Because deletes are derived by absence, `check_mass_delete` guards a
computed diff against accidental mass deletion (wrong `data_root`,
half-finished folder rename, botched merge) before it is applied.
"""

import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

# The vendored logging package (code/lib/logconfig) is resolved from
# this file's own location, so imports work from any working directory
# (not just the repo root) and CI never depends on untracked .claude/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import get_logger
from data_model import (
    CONTENT_COLUMNS,
    ColumnMappingKey,
    Corpus,
    DbState,
    DeploymentKey,
    TABLE_ORDER,
    TableRelationshipKey,
)

logger = get_logger(__name__)


class MassDeleteError(RuntimeError):
    """Raised when a diff would delete a suspiciously large share of the DB.

    See `check_mass_delete`. Subclasses RuntimeError so the entry point's
    existing error handling surfaces it as a clean exit-1 with message.
    """


# Mass-delete guard defaults, used when the loader config does not set the
# `mass_delete_fraction` / `mass_delete_min_count` knobs. The fraction is
# the maximum tolerated share of current DB rows a single run may delete;
# the min count is the absolute floor below which the guard never engages
# (a percentage alone misbehaves on small corpora — 1 delete out of 3
# rows is 33% but not "mass").
DEFAULT_MASS_DELETE_FRACTION = 0.25
DEFAULT_MASS_DELETE_MIN_COUNT = 20


@dataclass(frozen=True)
class RowChange:
    """One row-level change in the diff.

    Attributes:
        table: Main-table name (e.g., `"systems"`).
        key: The PK — a string for single-column PKs or a tuple for the
            composite-key tables (`deployment_tables`,
            `table_relationships`, `column_mappings`).
        old: The current-DB row, or None for inserts.
        new: The corpus row, or None for deletes.
    """

    table: str
    key: str | DeploymentKey | TableRelationshipKey | ColumnMappingKey
    old: Any
    new: Any


@dataclass
class Diff:
    """A computed set of row changes across all 9 tables.

    Attributes:
        inserts: Rows present in corpus but not in DB.
        updates: Rows whose content differs between corpus and DB.
        deletes: Rows present in DB but not in corpus.
    """

    inserts: list[RowChange] = field(default_factory=list)
    updates: list[RowChange] = field(default_factory=list)
    deletes: list[RowChange] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True iff there are no inserts, updates, or deletes."""
        return not (self.inserts or self.updates or self.deletes)

    def summary(self) -> str:
        """Compact human-readable summary for the dry-run log."""
        return (
            f"Diff: {len(self.inserts)} insert(s), "
            f"{len(self.updates)} update(s), "
            f"{len(self.deletes)} delete(s)"
        )


def _content_signature(row: Any, table: str) -> tuple[Any, ...]:
    """Build a tuple of the row's content-column values for equality.

    Excludes loader-managed timestamps, matching CONTENT_COLUMNS. The
    tuple is ordered by the row's dataclass field declaration so two
    rows produce comparable tuples deterministically. The rule is
    identical for all 9 tables — `deployment_tables` rows are pure
    facts (no `update_reason` column), so no table needs a special
    exclusion.
    """
    cols = CONTENT_COLUMNS[table]
    return tuple(
        getattr(row, f.name) for f in fields(row) if f.name in cols
    )


def compute_diff(corpus: Corpus, db_state: DbState) -> Diff:
    """Compute the per-table diff between `corpus` and `db_state`.

    Args:
        corpus: The YAML-derived corpus.
        db_state: Current DB state.

    Returns:
        A populated `Diff`. `inserts`/`updates`/`deletes` are populated
        in `TABLE_ORDER`, but consumers should not rely on intra-list
        ordering for correctness — `db_io.apply_diff` reorders deletes to
        reverse FK order regardless.
    """
    diff = Diff()
    for table in TABLE_ORDER:
        corpus_rows: dict[Any, Any] = getattr(corpus, table)
        db_rows: dict[Any, Any] = getattr(db_state, table)
        corpus_keys = set(corpus_rows.keys())
        db_keys = set(db_rows.keys())

        for key in corpus_keys - db_keys:
            diff.inserts.append(
                RowChange(
                    table=table, key=key, old=None, new=corpus_rows[key]
                )
            )
        for key in db_keys - corpus_keys:
            diff.deletes.append(
                RowChange(table=table, key=key, old=db_rows[key], new=None)
            )
        for key in corpus_keys & db_keys:
            new_row = corpus_rows[key]
            old_row = db_rows[key]
            if _content_signature(new_row, table) != _content_signature(
                old_row, table
            ):
                diff.updates.append(
                    RowChange(table=table, key=key, old=old_row, new=new_row)
                )
            # else: content-equal — no-op, preserves idempotency.

    logger.info(diff.summary())
    return diff


def check_mass_delete(
    diff: Diff,
    db_state: DbState,
    fraction: float,
    min_count: int,
) -> None:
    """Refuse a diff that would delete a suspiciously large share of the DB.

    Deletes are derived by absence — any DB row whose PK is missing from
    the assembled corpus is scheduled for deletion — so a wrong
    `data_root`, a half-finished folder rename, or a botched merge
    silently converts into mass deletion. This guard turns that into a
    hard failure before any write: it engages once at least `min_count`
    rows would be deleted, and raises when the delete count exceeds
    `fraction` of all current DB rows. A legitimate mass removal (e.g.
    decommissioning a system) bypasses it explicitly — `--allow-mass-delete`
    on a manual run, or raising the `mass_delete_*` config knobs in the
    same reviewed MR.

    Args:
        diff: The computed corpus-vs-DB diff.
        db_state: The DB state the diff was computed against.
        fraction: Maximum tolerated `deletes / total current rows` ratio;
            strictly exceeding it (not merely reaching it) trips the guard.
        min_count: Absolute floor below which the guard never engages.

    Raises:
        MassDeleteError: If at least `min_count` rows would be deleted and
            the count exceeds `fraction` of current DB rows.
    """
    delete_count = len(diff.deletes)
    if delete_count < min_count:
        return
    total_rows = sum(len(getattr(db_state, t)) for t in TABLE_ORDER)
    if delete_count > fraction * total_rows:
        pct = 100.0 * delete_count / total_rows
        raise MassDeleteError(
            f"mass-delete guard: this run would delete {delete_count} of "
            f"{total_rows} current DB rows ({pct:.0f}%), exceeding the "
            f"{fraction:.0%} threshold (guard engages at >= {min_count} "
            f"deletes). Deletes are computed by absence from YAML — check "
            f"data_root and for missing or renamed files/folders under "
            f"data_catalog/sources/. If this mass removal is intended, re-run with "
            f"--allow-mass-delete (requires METADATA_DB_ALLOW_MASS_DELETE=1) "
            f"or adjust mass_delete_fraction / mass_delete_min_count in the "
            f"loader config."
        )
