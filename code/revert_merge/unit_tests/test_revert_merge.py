"""Unit tests for revert_merge.py.

Strategy:

- Patch `run_git`, `head_sha`, `parent_shas`, and the credentialed
  helpers (`fetch_branch` / `push_branch`) on the `revert_merge`
  module's import namespace (and on `preconditions`, for the helpers it
  imports directly). No real `git` is ever invoked.
- Record every git invocation via a shared call log, then assert (a)
  the expected sequence ran for the happy path, and (b) `revert` /
  push never ran whenever a precondition fails.
- The CLEANUP_BOT_TOKEN must never appear in any captured log line, and
  nothing may write it into `.git/config`.

The CLI surface (`main`) is exercised via `monkeypatch`ing
`sys.argv` and `setup_logging` (same pattern as test_apply_ddl.py).
"""

import logging
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import preconditions
import revert_merge


SECRET_TOKEN = "SUPER_SECRET_TOKEN_DO_NOT_LOG"


@pytest.fixture
def token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set CLEANUP_BOT_TOKEN to a recognizable secret for the test."""
    monkeypatch.setenv("CLEANUP_BOT_TOKEN", SECRET_TOKEN)


@pytest.fixture
def patched_git(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace all subprocess-touching helpers with recording mocks.

    Returns a dict with:

    - `run_git_calls`: list of argv lists (first positional arg to
      run_git), in order.
    - `fetch_calls`: list of (cwd, url, branch) tuples from
      `fetch_branch`.
    - `push_calls`: list of (cwd, url, branch) tuples from
      `push_branch`.
    - `head`: settable; value returned by `head_sha`.
    - `parents`: settable; value returned by `parent_shas`.
    - `run_git_side_effect`: settable; if non-None, invoked with the
      argv list and may raise to simulate a failed git command.
    - `fetch_side_effect`: settable; if non-None, invoked with the
      (cwd, url, branch) tuple and may raise to simulate a failed fetch.
    """
    state: dict[str, Any] = {
        "run_git_calls": [],
        "fetch_calls": [],
        "push_calls": [],
        "head": "deadbeef",
        "parents": ["p1", "p2"],
        "run_git_side_effect": None,
        "fetch_side_effect": None,
    }

    def fake_run_git(
        args: list[str], cwd: Path, **kwargs: Any
    ) -> subprocess.CompletedProcess[str] | MagicMock:
        state["run_git_calls"].append(args)
        if state["run_git_side_effect"] is not None:
            state["run_git_side_effect"](args)
        return MagicMock(returncode=0, stdout="", stderr="")

    def fake_fetch(cwd: Path, url: str, branch: str) -> None:
        state["fetch_calls"].append((cwd, url, branch))
        if state["fetch_side_effect"] is not None:
            state["fetch_side_effect"]((cwd, url, branch))

    def fake_push(cwd: Path, url: str, branch: str) -> None:
        state["push_calls"].append((cwd, url, branch))

    def fake_head_sha(cwd: Path) -> str:
        return state["head"]

    def fake_parent_shas(cwd: Path, sha: str) -> list[str]:
        return state["parents"]

    # Patch on the revert_merge module — that's the import binding the
    # orchestrator actually calls.
    monkeypatch.setattr(revert_merge, "run_git", fake_run_git)
    monkeypatch.setattr(revert_merge, "fetch_branch", fake_fetch)
    monkeypatch.setattr(revert_merge, "push_branch", fake_push)
    # The precondition functions look up head_sha / parent_shas inside
    # the `preconditions` module — patch there.
    monkeypatch.setattr(preconditions, "head_sha", fake_head_sha)
    monkeypatch.setattr(preconditions, "parent_shas", fake_parent_shas)

    return state


# The git identity the revert commits under, shared across tests.
_BOT_NAME = "metadata-db cleanup bot"
_BOT_EMAIL = "cleanup-bot@example.com"

# The revert argv, including the per-invocation `-c` identity options that
# precede the `revert` subcommand.
_REVERT_ARGV = [
    "-c",
    f"user.name={_BOT_NAME}",
    "-c",
    f"user.email={_BOT_EMAIL}",
    "revert",
    "--no-edit",
    "-m",
    "1",
    "deadbeef",
]


def _config() -> dict[str, Any]:
    """Standard config dict used by the orchestration tests."""
    return {
        "remote_url_template": (
            "https://x-access-token:{token}@github.example.com/o/r.git"
        ),
        "main_branch": "main",
        "bot_name": _BOT_NAME,
        "bot_email": _BOT_EMAIL,
    }


# ---------------------------------------------------------------------------
# run — happy path
# ---------------------------------------------------------------------------


def test_run_happy_path_executes_full_sequence_in_order(
    patched_git: dict[str, Any],
    token_set: None,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    patched_git["head"] = "deadbeef"
    patched_git["parents"] = ["p1", "p2"]

    revert_merge.run(_config(), commit_sha="deadbeef", cwd=tmp_path)

    # fetch_branch ran exactly once with the composed authenticated URL
    # (per-command credential injection — never persisted via set-url).
    assert patched_git["fetch_calls"] == [
        (
            tmp_path,
            f"https://x-access-token:{SECRET_TOKEN}@github.example.com/o/r.git",
            "main",
        )
    ]

    # checkout -B forces local main to origin/main's tip (robust on a
    # reused checkout with a stale local main), then the revert.
    assert patched_git["run_git_calls"] == [
        ["checkout", "-B", "main", "origin/main"],
        _REVERT_ARGV,
    ]

    # push_branch ran exactly once, again by authenticated URL.
    assert patched_git["push_calls"] == [
        (
            tmp_path,
            f"https://x-access-token:{SECRET_TOKEN}@github.example.com/o/r.git",
            "main",
        )
    ]

    # No command ever wrote the remote URL to .git/config.
    assert not any(
        "set-url" in args or "config" in args
        for args in patched_git["run_git_calls"]
    )

    # Token must never appear in any log line.
    assert SECRET_TOKEN not in caplog.text


def test_run_stale_local_main_resolves_to_origin_tip(
    patched_git: dict[str, Any], token_set: None, tmp_path: Path
) -> None:
    # A reused runner checkout can hold a stale local `main`; the
    # sequence must reset it to the just-fetched origin/main tip
    # (checkout -B) BEFORE the HEAD precondition, so a legitimate revert
    # is not refused over leftover local state.
    revert_merge.run(_config(), commit_sha="deadbeef", cwd=tmp_path)

    checkout = next(
        args for args in patched_git["run_git_calls"] if args[0] == "checkout"
    )
    assert checkout == ["checkout", "-B", "main", "origin/main"]


# ---------------------------------------------------------------------------
# run — refusal paths
# ---------------------------------------------------------------------------


def test_run_head_mismatch_refuses_before_revert_or_push(
    patched_git: dict[str, Any], token_set: None, tmp_path: Path
) -> None:
    patched_git["head"] = "other_sha"

    with pytest.raises(preconditions.PreconditionError, match="refusing"):
        revert_merge.run(_config(), commit_sha="deadbeef", cwd=tmp_path)

    # fetch + checkout ran (those happen before the precondition check),
    # but revert and push must NOT have run. `revert` can appear after the
    # leading `-c` identity options, so scan the whole argv, not just [0].
    assert not any(
        "revert" in args for args in patched_git["run_git_calls"]
    )
    assert patched_git["push_calls"] == []


@pytest.mark.parametrize("parents", [[], ["solo"], ["a", "b", "c"]])
def test_run_wrong_parent_count_refuses_before_revert_or_push(
    patched_git: dict[str, Any],
    token_set: None,
    tmp_path: Path,
    parents: list[str],
) -> None:
    patched_git["parents"] = parents

    with pytest.raises(preconditions.PreconditionError, match="refusing"):
        revert_merge.run(_config(), commit_sha="deadbeef", cwd=tmp_path)

    assert not any(
        "revert" in args for args in patched_git["run_git_calls"]
    )
    assert patched_git["push_calls"] == []


def test_run_missing_token_refuses_with_no_git_calls(
    patched_git: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CLEANUP_BOT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="CLEANUP_BOT_TOKEN"):
        revert_merge.run(_config(), commit_sha="deadbeef", cwd=tmp_path)

    assert patched_git["run_git_calls"] == []
    assert patched_git["fetch_calls"] == []
    assert patched_git["push_calls"] == []


@pytest.mark.parametrize(
    "token_value",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace"),
        pytest.param("\n", id="newline"),
    ],
)
def test_run_blank_token_refuses_with_no_git_calls(
    patched_git: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    token_value: str,
) -> None:
    # A secret stored with stray whitespace (or a lone newline) must be
    # refused up front, not formatted into the URL to fail later with an
    # opaque git auth error mid-incident.
    monkeypatch.setenv("CLEANUP_BOT_TOKEN", token_value)

    with pytest.raises(RuntimeError, match="CLEANUP_BOT_TOKEN"):
        revert_merge.run(_config(), commit_sha="deadbeef", cwd=tmp_path)

    assert patched_git["run_git_calls"] == []


def test_run_unfilled_placeholder_refuses_without_touching_git(
    patched_git: dict[str, Any], token_set: None, tmp_path: Path
) -> None:
    # A config carrying an unfilled `<...>` placeholder (e.g. an
    # uncustomized `<org>`) must be refused BEFORE any git operation.
    # Derived from `_config()` so the URL is the only thing that varies:
    # a hand-built config omitting `bot_name` / `bot_email` would raise
    # KeyError instead if the step-0 checks were ever reordered.
    config = _config()
    config["remote_url_template"] = (
        "https://x-access-token:{token}@github.example.com/<org>/metadata_db.git"
    )

    with pytest.raises(RuntimeError, match="unfilled placeholder"):
        revert_merge.run(config, commit_sha="deadbeef", cwd=tmp_path)

    assert patched_git["run_git_calls"] == []
    assert patched_git["fetch_calls"] == []
    assert patched_git["push_calls"] == []


def test_run_template_missing_token_refuses_without_touching_git(
    patched_git: dict[str, Any], token_set: None, tmp_path: Path
) -> None:
    # A template without `{token}` formats silently to an
    # unauthenticated URL; the sanity check must refuse BEFORE any git
    # operation instead of failing later with a git auth error.
    config = _config()
    config["remote_url_template"] = (
        "https://x-access-token@github.example.com/o/metadata_db.git"
    )

    with pytest.raises(
        RuntimeError, match=r"lacks the `\{token\}` placeholder"
    ):
        revert_merge.run(config, commit_sha="deadbeef", cwd=tmp_path)

    assert patched_git["run_git_calls"] == []
    assert patched_git["fetch_calls"] == []
    assert patched_git["push_calls"] == []


# ---------------------------------------------------------------------------
# run — subprocess failure paths
# ---------------------------------------------------------------------------


def test_run_fetch_failure_halts_before_later_commands(
    patched_git: dict[str, Any], token_set: None, tmp_path: Path
) -> None:
    def boom(call: tuple[Path, str, str]) -> None:
        raise subprocess.CalledProcessError(
            1, ["git", "fetch"], stderr="network down"
        )

    patched_git["fetch_side_effect"] = boom

    with pytest.raises(subprocess.CalledProcessError):
        revert_merge.run(_config(), commit_sha="deadbeef", cwd=tmp_path)

    # fetch was attempted; nothing after it.
    assert len(patched_git["fetch_calls"]) == 1
    assert patched_git["run_git_calls"] == []
    assert patched_git["push_calls"] == []


def test_run_revert_failure_does_not_invoke_push(
    patched_git: dict[str, Any], token_set: None, tmp_path: Path
) -> None:
    def boom(args: list[str]) -> None:
        if "revert" in args:
            raise subprocess.CalledProcessError(
                1, ["git", *args], stderr="conflict"
            )

    patched_git["run_git_side_effect"] = boom

    with pytest.raises(subprocess.CalledProcessError):
        revert_merge.run(_config(), commit_sha="deadbeef", cwd=tmp_path)

    assert patched_git["push_calls"] == []
    # The revert call did get attempted before failing.
    assert _REVERT_ARGV in patched_git["run_git_calls"]


# ---------------------------------------------------------------------------
# run — config errors
# ---------------------------------------------------------------------------


def test_run_missing_config_field_raises_keyerror(
    patched_git: dict[str, Any], token_set: None, tmp_path: Path
) -> None:
    config = {"remote_url_template": "https://x-access-token:{token}@x/y.git"}
    # `main_branch` is absent.

    with pytest.raises(KeyError, match="main_branch"):
        revert_merge.run(config, commit_sha="deadbeef", cwd=tmp_path)


# ---------------------------------------------------------------------------
# run — cleanup-bot git identity (Task 19)
# ---------------------------------------------------------------------------


def test_run_revert_argv_carries_git_identity_flags(
    patched_git: dict[str, Any], token_set: None, tmp_path: Path
) -> None:
    # `git revert` creates a commit, so the revert argv must carry both `-c`
    # identity options with the configured values, ahead of the subcommand.
    patched_git["head"] = "deadbeef"
    patched_git["parents"] = ["p1", "p2"]

    revert_merge.run(_config(), commit_sha="deadbeef", cwd=tmp_path)

    revert_calls = [a for a in patched_git["run_git_calls"] if "revert" in a]
    assert len(revert_calls) == 1
    argv = revert_calls[0]
    assert argv[:4] == [
        "-c",
        f"user.name={_BOT_NAME}",
        "-c",
        f"user.email={_BOT_EMAIL}",
    ]
    # The `-c` options precede the `revert` subcommand (git requires this).
    assert argv.index("-c") < argv.index("revert")


@pytest.mark.parametrize(
    "override",
    [
        pytest.param({"bot_name": "   "}, id="blank_name"),
        pytest.param({"bot_email": ""}, id="empty_email"),
        pytest.param(
            {"bot_name": "<cleanup-bot-name>"}, id="placeholder_name"
        ),
        pytest.param(
            {"bot_email": "<cleanup-bot-email>"}, id="placeholder_email"
        ),
    ],
)
def test_run_bad_identity_refuses_without_touching_git(
    patched_git: dict[str, Any],
    token_set: None,
    tmp_path: Path,
    override: dict[str, str],
) -> None:
    # A blank or still-unfilled `<...>` identity is refused in the step-0
    # sanity block, before any git command runs.
    config = _config()
    config.update(override)

    with pytest.raises(RuntimeError, match="bot_"):
        revert_merge.run(config, commit_sha="deadbeef", cwd=tmp_path)

    assert patched_git["run_git_calls"] == []
    assert patched_git["fetch_calls"] == []
    assert patched_git["push_calls"] == []


@pytest.mark.parametrize("missing", ["bot_name", "bot_email"])
def test_run_missing_identity_key_raises_keyerror(
    patched_git: dict[str, Any],
    token_set: None,
    tmp_path: Path,
    missing: str,
) -> None:
    config = _config()
    del config[missing]

    with pytest.raises(KeyError, match=missing):
        revert_merge.run(config, commit_sha="deadbeef", cwd=tmp_path)

    assert patched_git["run_git_calls"] == []


# ---------------------------------------------------------------------------
# main — exit codes per error type
# ---------------------------------------------------------------------------


def _write_config(
    tmp_path: Path,
    remote_url_template: str = "https://x-access-token:{token}@x/y.git",
) -> Path:
    """Write a complete TOML config to `tmp_path`, returning its path.

    Carries every key `run` reads, so a test overriding one value (via
    `remote_url_template`) still exercises the check it names rather
    than tripping a KeyError on an unrelated missing key.
    """
    config_file = tmp_path / "cfg.toml"
    config_file.write_text(
        f'remote_url_template = "{remote_url_template}"\n'
        'main_branch = "main"\n'
        f'bot_name = "{_BOT_NAME}"\n'
        f'bot_email = "{_BOT_EMAIL}"\n'
    )
    return config_file


def _stub_setup_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(revert_merge, "setup_logging", lambda *a, **k: None)


def test_main_happy_path_does_not_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_setup_logging(monkeypatch)
    config_file = _write_config(tmp_path)
    monkeypatch.setattr(revert_merge, "run", lambda *a, **k: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "revert_merge.py",
            "--config",
            str(config_file),
            "--commit-sha",
            "abc",
        ],
    )

    # Should complete without raising SystemExit.
    revert_merge.main()


def test_main_config_not_found_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    _stub_setup_logging(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "revert_merge.py",
            "--config",
            "/no/such/file.toml",
            "--commit-sha",
            "abc",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        revert_merge.main()

    assert exc.value.code == 1
    assert "Config file not found" in caplog.text


def test_main_bad_toml_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    _stub_setup_logging(monkeypatch)
    config_file = tmp_path / "cfg.toml"
    config_file.write_text("this = = not toml ===")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "revert_merge.py",
            "--config",
            str(config_file),
            "--commit-sha",
            "abc",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        revert_merge.main()

    assert exc.value.code == 1
    assert "Failed to read config file" in caplog.text


def test_main_precondition_error_exits_nonzero_with_refusing_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    _stub_setup_logging(monkeypatch)
    config_file = _write_config(tmp_path)

    def _refuse(*a: object, **k: object) -> None:
        raise preconditions.PreconditionError(
            "HEAD (x) does not match expected commit (y); refusing to push."
        )

    monkeypatch.setattr(revert_merge, "run", _refuse)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "revert_merge.py",
            "--config",
            str(config_file),
            "--commit-sha",
            "abc",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        revert_merge.main()

    assert exc.value.code == 1
    assert "Precondition failed" in caplog.text
    assert "refusing to push" in caplog.text


def test_main_runtime_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    _stub_setup_logging(monkeypatch)
    config_file = _write_config(tmp_path)

    def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("CLEANUP_BOT_TOKEN is not set; refusing to push.")

    monkeypatch.setattr(revert_merge, "run", _boom)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "revert_merge.py",
            "--config",
            str(config_file),
            "--commit-sha",
            "abc",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        revert_merge.main()

    assert exc.value.code == 1
    assert "CLEANUP_BOT_TOKEN" in caplog.text


def test_main_missing_config_field_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    _stub_setup_logging(monkeypatch)
    config_file = _write_config(tmp_path)

    def _missing(*a: object, **k: object) -> None:
        raise KeyError("main_branch")

    monkeypatch.setattr(revert_merge, "run", _missing)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "revert_merge.py",
            "--config",
            str(config_file),
            "--commit-sha",
            "abc",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        revert_merge.main()

    assert exc.value.code == 1
    assert "Missing required config field" in caplog.text


def test_main_called_process_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    _stub_setup_logging(monkeypatch)
    config_file = _write_config(tmp_path)

    def _git_fail(*a: object, **k: object) -> None:
        raise subprocess.CalledProcessError(
            1, ["git", "push"], stderr="rejected"
        )

    monkeypatch.setattr(revert_merge, "run", _git_fail)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "revert_merge.py",
            "--config",
            str(config_file),
            "--commit-sha",
            "abc",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        revert_merge.main()

    assert exc.value.code == 1
    assert "git command failed" in caplog.text


def test_main_os_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    _stub_setup_logging(monkeypatch)
    config_file = _write_config(tmp_path)

    def _os_fail(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(revert_merge, "run", _os_fail)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "revert_merge.py",
            "--config",
            str(config_file),
            "--commit-sha",
            "abc",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        revert_merge.main()

    assert exc.value.code == 1
    assert "OS error" in caplog.text


def test_main_unfilled_placeholder_exits_nonzero_without_git(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    patched_git: dict[str, Any],
    token_set: None,
) -> None:
    # End-to-end through main(): a config still carrying an unfilled
    # `<...>` placeholder exits 1 with a clear message, before any
    # git operation.
    caplog.set_level(logging.ERROR)
    _stub_setup_logging(monkeypatch)
    config_file = _write_config(
        tmp_path,
        remote_url_template="https://x-access-token:{token}@x/<org>/y.git",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "revert_merge.py",
            "--config",
            str(config_file),
            "--commit-sha",
            "abc",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        revert_merge.main()

    assert exc.value.code == 1
    assert "unfilled placeholder" in caplog.text
    assert patched_git["run_git_calls"] == []
    assert patched_git["fetch_calls"] == []
    assert patched_git["push_calls"] == []


# ---------------------------------------------------------------------------
# The shipped config artifact
# ---------------------------------------------------------------------------


def test_shipped_config_has_required_keys_and_token_placeholder() -> None:
    """The real `config/revert_merge.toml` carries every key `run` reads.

    Every other test synthesizes its own config, so a typo'd key or a
    `{token}`-less URL committed to the shipped artifact would surface
    only during an incident — when the revert job is the last line of
    defense and no earlier signal exists. Asserts presence and
    non-blankness, not that the `<...>` identity placeholders have been
    filled in: those are filled per-deployment by an operator, and the
    step-0 sanity block refuses them at runtime.
    """
    config_path = (
        Path(__file__).resolve().parents[1] / "config" / "revert_merge.toml"
    )

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    for key in ("remote_url_template", "main_branch", "bot_name", "bot_email"):
        assert key in config, f"{key} missing from {config_path}"
        assert isinstance(config[key], str) and config[key].strip(), (
            f"{key} is blank in {config_path}"
        )

    # Without `{token}` the URL would format to an unauthenticated remote.
    assert "{token}" in config["remote_url_template"]


# ---------------------------------------------------------------------------
# Token-redaction: an end-to-end sweep
# ---------------------------------------------------------------------------


def test_main_token_never_in_logs_across_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    patched_git: dict[str, Any],
    token_set: None,
) -> None:
    """End-to-end: happy path through main() — token must not leak."""
    caplog.set_level(logging.DEBUG)
    _stub_setup_logging(monkeypatch)
    config_file = _write_config(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "revert_merge.py",
            "--config",
            str(config_file),
            "--commit-sha",
            "deadbeef",
        ],
    )

    revert_merge.main()

    assert SECRET_TOKEN not in caplog.text
