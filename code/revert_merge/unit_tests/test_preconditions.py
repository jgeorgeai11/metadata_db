"""Unit tests for preconditions.py.

These tests patch `head_sha` / `parent_shas` directly — they don't
touch subprocess at all. The point is to exercise the refusal logic in
isolation; the subprocess behavior of the helpers is covered in
test_git_ops.py.
"""

from pathlib import Path

import pytest

import preconditions


# ---------------------------------------------------------------------------
# verify_head_is
# ---------------------------------------------------------------------------


def test_verify_head_is_match_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(preconditions, "head_sha", lambda cwd: "abc123")

    # Should not raise.
    preconditions.verify_head_is("abc123", tmp_path)


def test_verify_head_is_mismatch_raises_with_both_shas(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(preconditions, "head_sha", lambda cwd: "actual_sha")

    with pytest.raises(preconditions.PreconditionError) as exc:
        preconditions.verify_head_is("expected_sha", tmp_path)

    msg = str(exc.value)
    assert "actual_sha" in msg
    assert "expected_sha" in msg
    assert "refusing to push" in msg


# ---------------------------------------------------------------------------
# verify_is_merge_commit
# ---------------------------------------------------------------------------


def test_verify_is_merge_commit_two_parents_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        preconditions, "parent_shas", lambda cwd, sha: ["p1", "p2"]
    )

    # Should not raise.
    preconditions.verify_is_merge_commit("merge_sha", tmp_path)


@pytest.mark.parametrize("parents", [[], ["solo"], ["a", "b", "c"]])
def test_verify_is_merge_commit_wrong_parent_count_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, parents: list[str]
) -> None:
    monkeypatch.setattr(
        preconditions, "parent_shas", lambda cwd, sha: parents
    )

    with pytest.raises(preconditions.PreconditionError) as exc:
        preconditions.verify_is_merge_commit("not_a_merge", tmp_path)

    msg = str(exc.value)
    assert "not_a_merge" in msg
    assert f"{len(parents)} parent" in msg
    assert "refusing to push" in msg
