"""revert_merge.py — cleanup-bot script: revert a failed-load merge.

Invoked by the `revert_failed_load` job in
`.github/workflows/post_merge.yml` when a post-merge `load_catalog_data`
run fails. The merge commit landed on `main` but the DB didn't move, so
to restore the invariant this script pushes a `git revert -m 1 <sha>`
directly to `main`, authenticating as the cleanup bot via
`CLEANUP_BOT_TOKEN`.

Refusal contract (from MAINTAINING.md, "The revert
script — Refusal"): if any precondition fails — HEAD is not the
expected commit, the commit is not a merge — the script exits non-zero
WITHOUT pushing. The failed pipeline surfaces the inconsistency for
human review.

Usage:
    uv run code/revert_merge/revert_merge.py \\
        --config code/revert_merge/config/revert_merge.toml \\
        --commit-sha "$GITHUB_SHA"
"""

import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

# The vendored logging package (code/lib/logconfig) is resolved from
# this file's own location, so imports work from any working directory
# (not just the repo root) and CI never depends on untracked .claude/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import setup_logging, get_logger

from git_ops import build_authenticated_url, fetch_branch, push_branch, run_git
from preconditions import (
    PreconditionError,
    verify_head_is,
    verify_is_merge_commit,
)

logger = get_logger(__name__)

TOKEN_ENV_VAR = "CLEANUP_BOT_TOKEN"

# Angle-bracket placeholders (e.g. `<org>`) mark template values the
# operator must fill in before the config is usable. `{token}` is the
# one runtime placeholder and uses str.format braces instead.
_UNFILLED_PLACEHOLDER_RE = re.compile(r"<[^>]*>")


def run(config: dict[str, Any], commit_sha: str, cwd: Path) -> None:
    """Execute the full revert-and-push flow.

    The git sequence (fetch → checkout → verify → revert → push) runs in
    a strict order: every precondition is checked BEFORE `git revert` or
    `git push` is invoked, so a refusal never leaves the remote in a
    half-finished state. Credentials are injected per command
    (`fetch_branch` / `push_branch` pass the authenticated URL on each
    git command line) — the token is never written to `.git/config`, so
    it cannot outlive the job on a reused runner workspace.

    Args:
        config: Parsed TOML config. Expected keys: `remote_url_template`,
            `main_branch`, `bot_name`, `bot_email` (the last two supply the
            git identity `git revert` commits under).
        commit_sha: The merge commit SHA to revert. Comes from
            `--commit-sha` on the command line (CI passes
            `$GITHUB_SHA`).
        cwd: Working directory of the git repo. The CI runner's
            checkout in production; a temp dir under tests.

    Raises:
        RuntimeError: If `CLEANUP_BOT_TOKEN` is missing, empty, or
            whitespace-only, the
            configured remote URL still carries an unfilled placeholder
            such as `<org>` (a never-customized template) or lacks the
            `{token}` placeholder (the push would silently run
            unauthenticated), or the bot identity (`bot_name` / `bot_email`)
            is blank or still an unfilled `<...>` placeholder.
        KeyError: If a required config field is missing, or if the remote
            URL template names a placeholder other than `{token}`.
        PreconditionError: If HEAD is not the expected commit, or the
            commit is not a 2-parent merge.
        subprocess.CalledProcessError: If any git invocation fails.
        OSError: If the git executable cannot be found or invoked.
    """
    token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if not token:
        # Unset, empty, and whitespace-only are all rejected — a secret
        # stored with a stray newline would otherwise be formatted into
        # the remote URL and fail mid-incident with an opaque git auth
        # error. Stripping on read also drops a trailing newline from an
        # otherwise-valid secret. Never log the token itself — only the
        # fact that it's missing.
        raise RuntimeError(
            f"{TOKEN_ENV_VAR} is not set; refusing to push without "
            f"cleanup-bot credentials."
        )

    main_branch = config["main_branch"]
    remote_url_template = config["remote_url_template"]

    # 0. Template sanity: refuse BEFORE any git operation if the URL
    # still carries an unfilled `<...>` placeholder (the shipped
    # config's URL is filled in, but a re-pointed fork may not be; the
    # bot identity below ships unfilled). Without this, git would fail
    # later with a confusing DNS/404 error — or worse, push somewhere
    # unintended.
    unfilled = _UNFILLED_PLACEHOLDER_RE.search(remote_url_template)
    if unfilled:
        raise RuntimeError(
            f"remote_url_template still contains the unfilled placeholder "
            f"{unfilled.group(0)!r} — fill in the real value in the TOML "
            f"config before running; refusing to touch git."
        )
    # A template without `{token}` would format to an unauthenticated
    # URL and fail only later, with a git auth error mid-incident —
    # refuse up front with the config-level cause instead.
    if "{token}" not in remote_url_template:
        raise RuntimeError(
            "remote_url_template lacks the `{token}` placeholder — the "
            "push would run unauthenticated; add `{token}` (e.g. "
            "`https://x-access-token:{token}@...`) in the TOML config; "
            "refusing to touch git."
        )
    # Bot identity sanity: `git revert` creates a commit, so it needs an
    # author/committer identity. None is configured on a stock CI container,
    # so without this the very first real incident would fail at the revert
    # step with `main` left ahead of the DB. Refuse a blank or still-unfilled
    # (`<...>`) identity here, alongside the URL checks, before any git op.
    bot_name = config["bot_name"]
    bot_email = config["bot_email"]
    for key, value in (("bot_name", bot_name), ("bot_email", bot_email)):
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(
                f"{key} is blank in the config — set the cleanup bot's git "
                f"identity before running; refusing to touch git."
            )
        unfilled_id = _UNFILLED_PLACEHOLDER_RE.search(value)
        if unfilled_id:
            raise RuntimeError(
                f"{key} still contains the unfilled placeholder "
                f"{unfilled_id.group(0)!r} — fill in the cleanup bot's git "
                f"identity in the TOML config before running; refusing to "
                f"touch git."
            )

    # 1. Compose the authenticated URL for per-command use. It never
    # reaches the log and is never persisted in .git/config.
    url = build_authenticated_url(remote_url_template, token)

    # 2. Fetch the branch tip into origin/<branch> (credential injected
    # on this command only).
    fetch_branch(cwd, url, main_branch)

    # 3. Force the local branch to the just-fetched origin tip.
    # `checkout -B` (not plain `checkout`) makes this robust on reused
    # runner checkouts: a stale local `main` left by a previous job
    # would otherwise make the HEAD precondition below refuse a
    # legitimate revert.
    run_git(
        ["checkout", "-B", main_branch, f"origin/{main_branch}"], cwd=cwd
    )

    # 4. Refuse before doing anything destructive if preconditions fail.
    verify_head_is(commit_sha, cwd=cwd)
    verify_is_merge_commit(commit_sha, cwd=cwd)

    # 5. Produce and push the revert (credential injected per command).
    # `git revert` creates a commit, so pass the cleanup bot's identity as
    # per-invocation `-c user.name=… -c user.email=…` options (these must
    # precede the `revert` subcommand). Per-command `-c` rather than
    # `git config` writes keeps nothing identity-related in `.git/config`
    # on a reused runner workspace, mirroring the token hygiene.
    run_git(
        [
            "-c",
            f"user.name={bot_name}",
            "-c",
            f"user.email={bot_email}",
            "revert",
            "--no-edit",
            "-m",
            "1",
            commit_sha,
        ],
        cwd=cwd,
    )
    push_branch(cwd, url, main_branch)

    logger.info(f"Pushed revert of {commit_sha} to origin/{main_branch}")


def main() -> None:
    """Parse args, load config, set up logging, dispatch to `run`."""
    parser = argparse.ArgumentParser(
        description=(
            "Revert a failed-load merge commit and push directly to main "
            "as the cleanup bot."
        ),
    )
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to TOML configuration file.",
    )
    parser.add_argument(
        "--commit-sha",
        type=str,
        required=True,
        help="SHA of the failed merge commit to revert (CI passes $GITHUB_SHA).",
    )
    args = parser.parse_args()

    # Defer log-file creation until after argparse so `--help` doesn't
    # spawn an empty log directory.
    setup_logging(log_dir="logs/revert_merge")
    logger.info("=" * 60)
    # State the target up front: every refusal below fires before `run`
    # reaches git, and the failed-pipeline log is the whole post-mortem
    # artifact — so the SHA and config must appear even then.
    logger.info(
        f"Reverting commit {args.commit_sha} using config {args.config}"
    )

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.error(f"Failed to read config file: {e}")
        sys.exit(1)

    # SUCCESS lives in `else` (not the `try`) so an error raised while
    # logging it — after the push already landed — is not caught below
    # and reported as a failure; the closing separator lives in
    # `finally` so every exit path is bracketed exactly once.
    try:
        run(config, commit_sha=args.commit_sha, cwd=Path.cwd())
    except PreconditionError as e:
        # Distinct arm so the operator's eye goes straight to "refusing
        # to push" in the failed-pipeline log.
        logger.error(f"Precondition failed: {e}")
        sys.exit(1)
    except KeyError as e:
        # Also catches the KeyError `build_authenticated_url` raises when
        # the template names a placeholder other than `{token}`, so the
        # message must point at both possible causes.
        logger.error(
            f"Missing required config field or malformed URL template "
            f"placeholder: {e}"
        )
        sys.exit(1)
    except RuntimeError as e:
        logger.error(f"{e}")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        logger.error(f"git command failed (exit {e.returncode}): {e.cmd}")
        sys.exit(1)
    except OSError as e:
        logger.error(f"OS error: {e}")
        sys.exit(1)
    else:
        logger.info("SUCCESS")
    finally:
        logger.info("=" * 60)


if __name__ == "__main__":  # pragma: no cover
    main()
