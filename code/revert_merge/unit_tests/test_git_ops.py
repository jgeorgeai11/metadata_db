"""Unit tests for git_ops.py.

`subprocess.run` is patched at the `git_ops` module boundary via the
`fake_subprocess_run` fixture from conftest. No real `git` is invoked.

Each test asserts (a) the argv passed to `git` matches the spec and
(b) the cleanup-bot token never appears in the captured log output —
`build_authenticated_url` composes the token-bearing URL, and
`fetch_branch` / `push_branch` pass it per command (never persisting it
in `.git/config`) while redacting it from every log line.
"""

import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

import git_ops


# ---------------------------------------------------------------------------
# run_git
# ---------------------------------------------------------------------------


def test_run_git_invokes_git_with_prefixed_args(
    fake_subprocess_run: dict[str, Any], tmp_path: Path
) -> None:
    fake_subprocess_run["set_result"](returncode=0, stdout="ok\n")

    result = git_ops.run_git(["status"], cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout == "ok\n"
    args, kwargs = fake_subprocess_run["calls"][0]
    assert args == ["git", "status"]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    # run_git owns the raise so it can log/redact before raising
    assert kwargs["check"] is False


def test_run_git_nonzero_raises_when_check_true(
    fake_subprocess_run: dict[str, Any], tmp_path: Path
) -> None:
    fake_subprocess_run["set_result"](returncode=1, stderr="boom")

    with pytest.raises(subprocess.CalledProcessError) as exc:
        git_ops.run_git(["fetch"], cwd=tmp_path, check=True)

    assert exc.value.returncode == 1
    assert exc.value.stderr == "boom"
    assert exc.value.cmd == ["git", "fetch"]


def test_run_git_nonzero_returns_when_check_false(
    fake_subprocess_run: dict[str, Any],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_subprocess_run["set_result"](returncode=2, stderr="warn")
    caplog.set_level(logging.DEBUG, logger="git_ops")

    result = git_ops.run_git(["fetch"], cwd=tmp_path, check=False)

    assert result.returncode == 2
    # An expected non-zero exit is logged at WARNING, not ERROR.
    assert [r.levelname for r in caplog.records if "exited 2" in r.message] == [
        "WARNING"
    ]


def test_run_git_nonzero_logs_error(
    fake_subprocess_run: dict[str, Any],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_subprocess_run["set_result"](returncode=1, stderr="nope")
    caplog.set_level(logging.ERROR, logger="git_ops")

    with pytest.raises(subprocess.CalledProcessError):
        git_ops.run_git(["fetch"], cwd=tmp_path)

    assert "exited 1" in caplog.text
    assert "nope" in caplog.text


def test_run_git_log_args_overrides_log_only_not_argv(
    fake_subprocess_run: dict[str, Any],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """log_args controls log text; the real argv still goes to git."""
    caplog.set_level(logging.DEBUG, logger="git_ops")

    git_ops.run_git(
        ["fetch", "https://x-access-token:T@x/y.git", "refs/heads/main"],
        cwd=tmp_path,
        log_args=["fetch", "<redacted>", "refs/heads/main"],
    )

    # The real subprocess.run received the actual URL...
    args, _ = fake_subprocess_run["calls"][0]
    assert args == [
        "git",
        "fetch",
        "https://x-access-token:T@x/y.git",
        "refs/heads/main",
    ]
    # ...but the log shows only the redacted form.
    assert "<redacted>" in caplog.text
    assert "x-access-token:T@" not in caplog.text


# ---------------------------------------------------------------------------
# head_sha
# ---------------------------------------------------------------------------


def test_head_sha_strips_trailing_newline(
    fake_subprocess_run: dict[str, Any], tmp_path: Path
) -> None:
    fake_subprocess_run["set_result"](stdout="abc123def\n")

    assert git_ops.head_sha(tmp_path) == "abc123def"

    args, _ = fake_subprocess_run["calls"][0]
    assert args == ["git", "rev-parse", "HEAD"]


# ---------------------------------------------------------------------------
# parent_shas
# ---------------------------------------------------------------------------


def test_parent_shas_root_commit_returns_empty(
    fake_subprocess_run: dict[str, Any], tmp_path: Path
) -> None:
    # rev-list --parents prints just "<sha>" for a root commit.
    fake_subprocess_run["set_result"](stdout="aaaa\n")

    assert git_ops.parent_shas(tmp_path, "aaaa") == []

    args, _ = fake_subprocess_run["calls"][0]
    assert args == ["git", "rev-list", "--parents", "-n", "1", "aaaa"]


def test_parent_shas_normal_commit_returns_one_parent(
    fake_subprocess_run: dict[str, Any], tmp_path: Path
) -> None:
    fake_subprocess_run["set_result"](stdout="bbbb pppp\n")

    assert git_ops.parent_shas(tmp_path, "bbbb") == ["pppp"]


def test_parent_shas_merge_commit_returns_two_parents(
    fake_subprocess_run: dict[str, Any], tmp_path: Path
) -> None:
    fake_subprocess_run["set_result"](stdout="cccc p1 p2\n")

    assert git_ops.parent_shas(tmp_path, "cccc") == ["p1", "p2"]


# ---------------------------------------------------------------------------
# build_authenticated_url
# ---------------------------------------------------------------------------


def test_build_authenticated_url_substitutes_token() -> None:
    template = "https://x-access-token:{token}@github.example.com/o/r.git"
    url = git_ops.build_authenticated_url(template, "SECRET123")
    assert url == "https://x-access-token:SECRET123@github.example.com/o/r.git"


def test_build_authenticated_url_absent_placeholder_is_silent() -> None:
    # A template that merely LACKS `{token}` formats silently to an
    # unauthenticated URL — str.format raises nothing for unused kwargs.
    # (The unfilled-placeholder sanity check in revert_merge.run is the
    # guard against a never-customized template.)
    template = "https://github.example.com/o/r.git"
    url = git_ops.build_authenticated_url(template, "SECRET123")
    assert url == template
    assert "SECRET123" not in url


def test_build_authenticated_url_wrong_placeholder_raises_keyerror() -> None:
    # Only a template naming a *different* placeholder raises.
    template = "https://x-access-token:{tokn}@github.example.com/o/r.git"
    with pytest.raises(KeyError):
        git_ops.build_authenticated_url(template, "SECRET123")


# ---------------------------------------------------------------------------
# fetch_branch / push_branch — per-command credential injection
# ---------------------------------------------------------------------------

_AUTH_URL = "https://x-access-token:SECRET123@github.example.com/o/r.git"


def test_fetch_branch_uses_url_and_tracking_refspec(
    fake_subprocess_run: dict[str, Any], tmp_path: Path
) -> None:
    git_ops.fetch_branch(tmp_path, _AUTH_URL, "main")

    args, _ = fake_subprocess_run["calls"][0]
    assert args == [
        "git",
        "fetch",
        _AUTH_URL,
        "+refs/heads/main:refs/remotes/origin/main",
    ]


def test_push_branch_uses_url_and_head_refspec(
    fake_subprocess_run: dict[str, Any], tmp_path: Path
) -> None:
    git_ops.push_branch(tmp_path, _AUTH_URL, "main")

    args, _ = fake_subprocess_run["calls"][0]
    assert args == ["git", "push", _AUTH_URL, "HEAD:refs/heads/main"]


def test_fetch_and_push_never_touch_git_config(
    fake_subprocess_run: dict[str, Any], tmp_path: Path
) -> None:
    # Credential hygiene: the token is injected per command; nothing may
    # write it (or any remote URL) into .git/config on a reused runner.
    git_ops.fetch_branch(tmp_path, _AUTH_URL, "main")
    git_ops.push_branch(tmp_path, _AUTH_URL, "main")

    for args, _ in fake_subprocess_run["calls"]:
        assert "set-url" not in args
        assert "config" not in args


@pytest.mark.parametrize("helper", ["fetch_branch", "push_branch"])
def test_credentialed_helpers_do_not_log_token(
    helper: str,
    fake_subprocess_run: dict[str, Any],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="git_ops")

    getattr(git_ops, helper)(tmp_path, _AUTH_URL, "main")

    assert "SECRET123" not in caplog.text
    assert "<redacted-url>" in caplog.text


@pytest.mark.parametrize("helper", ["fetch_branch", "push_branch"])
def test_credentialed_helpers_do_not_log_token_on_failure(
    helper: str,
    fake_subprocess_run: dict[str, Any],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing credentialed command must not leak the token
    through argv or the raised exception's cmd."""
    fake_subprocess_run["set_result"](returncode=1, stderr="rejected")
    caplog.set_level(logging.DEBUG, logger="git_ops")

    with pytest.raises(subprocess.CalledProcessError) as exc:
        getattr(git_ops, helper)(tmp_path, _AUTH_URL, "main")

    # Neither the log output nor the exception's recorded cmd should
    # contain the token.
    assert "SECRET123" not in caplog.text
    assert "SECRET123" not in " ".join(exc.value.cmd)
