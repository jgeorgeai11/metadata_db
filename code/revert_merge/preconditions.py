"""preconditions.py — refusal checks for revert_merge.

These functions enforce the script's safety contract from
`MAINTAINING.md` ("The revert script — Refusal"):

1. `verify_head_is`: the failed merge commit must still be at the tip
   of `main`. If anything else has landed since, this script must not
   try to "undo" by reverting an older SHA — that would not restore the
   `main`/DB invariant and would likely conflict.
2. `verify_is_merge_commit`: the commit being reverted must have
   exactly two parents. `git revert -m 1 <sha>` is only meaningful on
   a merge commit; running it on a normal commit would silently revert
   the wrong thing.

Both helpers call into `git_ops.head_sha` / `git_ops.parent_shas`
rather than `subprocess.run` directly, so tests can exercise refusal
logic without mocking subprocess.
"""

import sys
from pathlib import Path

# The vendored logging package (code/lib/logconfig) is resolved from
# this file's own location, so imports work from any working directory
# (not just the repo root) and CI never depends on untracked .claude/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import get_logger

from git_ops import head_sha, parent_shas

logger = get_logger(__name__)


class PreconditionError(Exception):
    """A precondition for pushing the revert was not met.

    Used by the entry point to discriminate "refuse to push" failures
    (a clean log message, no DB drift, human follow-up expected) from
    other unexpected failures (config, subprocess, OS).
    """


def verify_head_is(commit_sha: str, cwd: Path) -> None:
    """Refuse unless HEAD of the repo at `cwd` equals `commit_sha`.

    A mismatch means another commit landed on `main` since the failed
    merge — the script cannot safely revert just the original merge
    without touching that newer work.

    Args:
        commit_sha: The expected HEAD SHA (the failed merge commit).
        cwd: Working directory of the git repo.

    Raises:
        PreconditionError: If HEAD does not match `commit_sha`. The
            message names both SHAs for triage.
        subprocess.CalledProcessError: If `git rev-parse` itself fails.
    """
    actual = head_sha(cwd)
    if actual != commit_sha:
        raise PreconditionError(
            f"HEAD ({actual}) does not match expected commit "
            f"({commit_sha}); refusing to push."
        )
    logger.info(f"Precondition OK: HEAD matches expected commit {commit_sha}")


def verify_is_merge_commit(commit_sha: str, cwd: Path) -> None:
    """Refuse unless `commit_sha` is a merge commit (exactly 2 parents).

    `git revert -m 1 <sha>` is only meaningful for merge commits; on a
    normal commit it would revert the wrong thing and on a root commit
    it would fail outright. Either case is a "refuse to push" outcome.

    Args:
        commit_sha: The commit to inspect.
        cwd: Working directory of the git repo.

    Raises:
        PreconditionError: If the commit does not have exactly two
            parents.
        subprocess.CalledProcessError: If `git rev-list` itself fails
            (e.g. the SHA does not exist in the repo).
    """
    parents = parent_shas(cwd, commit_sha)
    if len(parents) != 2:
        raise PreconditionError(
            f"Commit {commit_sha} has {len(parents)} parent(s), expected 2 "
            f"(merge commit); refusing to push."
        )
    logger.info(
        f"Precondition OK: {commit_sha} is a merge commit with 2 parents"
    )
