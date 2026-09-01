"""Shared fixtures for revert_merge unit tests.

The revert_merge modules live one directory up from unit_tests/. Put
that directory on sys.path so `import git_ops`, `import preconditions`,
and `import revert_merge` resolve regardless of pytest's rootdir
handling — mirrors the pattern from apply_ddl / load_catalog_data.
"""

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

REVERT_MERGE_DIR = Path(__file__).resolve().parent.parent
if str(REVERT_MERGE_DIR) not in sys.path:
    sys.path.insert(0, str(REVERT_MERGE_DIR))


def _make_completed(
    args: list[str], returncode: int, stdout: str, stderr: str
) -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess matching subprocess.run's text-mode shape."""
    return subprocess.CompletedProcess(
        args=args, returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture
def fake_subprocess_run(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Patch `subprocess.run` inside `git_ops` and record calls.

    Returns a dict with three keys:

    - `calls`: list of (args, kwargs) tuples for every invocation.
    - `set_result(returncode, stdout, stderr)`: helper to control what
      the next (and subsequent) calls return.
    - `state`: the fixture's internal mutable dict holding the current
      `returncode`, `stdout`, `stderr`, and `calls`.

    Defaults to returncode=0, empty stdout/stderr.
    """
    import git_ops

    state = {
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "calls": [],
    }

    def fake_run(
        args: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        state["calls"].append((args, kwargs))
        return _make_completed(
            args, state["returncode"], state["stdout"], state["stderr"]
        )

    def set_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        state["returncode"] = returncode
        state["stdout"] = stdout
        state["stderr"] = stderr

    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)
    return {"calls": state["calls"], "set_result": set_result, "state": state}
