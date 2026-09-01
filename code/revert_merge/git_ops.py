"""git_ops.py — subprocess wrappers for the git commands revert_merge uses.

All `subprocess.run(["git", ...])` calls in this package go through
`run_git` so that:

1. Tests can patch a single seam to assert command argv without invoking
   real `git`.
2. The orchestrator never has to remember the boilerplate
   (`capture_output=True`, `text=True`, `cwd=...`).
3. The token-bearing remote URL is only built inside
   `build_authenticated_url` and is never logged.

Credential hygiene: the token is injected per command — `fetch_branch`
and `push_branch` pass the authenticated URL directly on the git
command line instead of writing it to `origin` via
`git remote set-url`. Nothing token-bearing is ever persisted in
`.git/config`, so on a reused runner workspace the credential cannot
outlive the job.

Every helper takes an explicit `cwd: Path` rather than relying on the
process working directory — the CI job runs inside the repo checkout,
but unit tests point the helpers at `tmp_path`.
"""

import subprocess
import sys
from pathlib import Path

# The vendored logging package (code/lib/logconfig) is resolved from
# this file's own location, so imports work from any working directory
# (not just the repo root) and CI never depends on untracked .claude/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "code" / "lib"))
from logconfig import get_logger

logger = get_logger(__name__)


def run_git(
    args: list[str],
    cwd: Path,
    check: bool = True,
    log_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `git` with the given arguments inside `cwd`.

    Thin wrapper over `subprocess.run` that captures stdout/stderr as
    text. Logs the command (with `git` prefixed) on entry, and the
    non-zero exit code plus stderr on failure.

    Args:
        args: Arguments to pass to `git` (without the leading "git").
        cwd: Working directory in which to run git.
        check: If True (default), raise `CalledProcessError` on
            non-zero exit. Pass `check=False` to inspect a
            possibly-failing command's exit code instead; the failure
            is then logged at WARNING rather than ERROR.
        log_args: If provided, used in place of `args` when forming log
            messages. `fetch_branch` / `push_branch` pass a redacted form
            so the cleanup-bot token never appears in the logs.

    Returns:
        The completed process object (stdout/stderr available as `.stdout` /
        `.stderr`, both `str`).

    Raises:
        subprocess.CalledProcessError: If `check` is True and git exits
            non-zero.
        OSError: If the `git` executable cannot be found or invoked.
    """
    display_args = log_args if log_args is not None else args
    # Note: stderr is logged verbatim on failure below. That is safe for
    # credentialed operations only because git itself anonymizes URL
    # credentials in its error output (e.g. "https://***@host/...");
    # argv redaction (log_args) stays this module's own responsibility.
    logger.debug(f"Running git {display_args} in {cwd}")
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = (
            f"git {display_args} exited {result.returncode}: "
            f"{result.stderr.strip()}"
        )
        if check:
            # This exit stops execution (we raise below), so it is a
            # genuine ERROR-level failure.
            logger.error(message)
            # Use display_args in the raised exception so any uncaught
            # traceback also avoids leaking the token.
            raise subprocess.CalledProcessError(
                result.returncode,
                ["git", *display_args],
                output=result.stdout,
                stderr=result.stderr,
            )
        # The caller passed check=False to deliberately inspect a
        # possibly-failing command, so a non-zero exit is expected here,
        # not a failure — log at WARNING to avoid misleading noise.
        logger.warning(message)
    return result


def head_sha(cwd: Path) -> str:
    """Return the SHA of HEAD in the repo at `cwd`.

    Args:
        cwd: Working directory of the git repo.

    Returns:
        The 40-character HEAD SHA, with trailing newline stripped.

    Raises:
        subprocess.CalledProcessError: If `git rev-parse HEAD` fails.
    """
    result = run_git(["rev-parse", "HEAD"], cwd=cwd)
    return result.stdout.strip()


def parent_shas(cwd: Path, sha: str) -> list[str]:
    """Return the parent SHAs of the given commit.

    Uses `git rev-list --parents -n 1 <sha>`, which prints one line of
    space-separated tokens: the commit itself followed by each parent.

    Args:
        cwd: Working directory of the git repo.
        sha: Commit SHA to inspect.

    Returns:
        List of parent SHAs. Empty for a root commit, one entry for a
        normal commit, two entries for a standard merge commit.

    Raises:
        subprocess.CalledProcessError: If `git rev-list` fails (e.g.
            the SHA does not exist in the repo).
    """
    result = run_git(["rev-list", "--parents", "-n", "1", sha], cwd=cwd)
    # Output is one line: "<sha> <parent1> <parent2> ..."; the first
    # token is the commit itself, the rest are parents.
    tokens = result.stdout.strip().split()
    return tokens[1:]


def build_authenticated_url(remote_url_template: str, token: str) -> str:
    """Compose the token-authenticated GitHub URL for per-command use.

    Composes the URL via `remote_url_template.format(token=token)`. The
    result is passed straight to `fetch_branch` / `push_branch` on each
    git command line — it is never written to `.git/config` (no
    `git remote set-url`), so the credential cannot outlive the job on
    a reused runner workspace. Callers (and the test suite) rely on the
    guarantee that the composed URL is never logged.

    Format semantics: a template that simply LACKS the `{token}`
    placeholder formats silently to an unauthenticated URL — no
    exception is raised here. Only a template naming a *different*
    placeholder (e.g. `{tokn}`) raises `KeyError` from `str.format`.
    Both misconfigurations are refused up front by `revert_merge.run`'s
    template sanity checks (unfilled `<...>` placeholder, missing
    `{token}`) before any git operation runs.

    Args:
        remote_url_template: A `str.format`-style template containing a
            `{token}` placeholder, e.g.
            `https://x-access-token:{token}@github.example.com/org/repo.git`.
        token: The cleanup-bot token (a GitHub App installation token or
            fine-grained PAT) to substitute in.

    Returns:
        The composed URL. Contains the token — never log it.

    Raises:
        KeyError: If the template names a placeholder other than
            `{token}`.
    """
    return remote_url_template.format(token=token)


def fetch_branch(cwd: Path, authenticated_url: str, branch: str) -> None:
    """Fetch `branch` from the authenticated URL into `origin/<branch>`.

    Fetching by explicit URL (not the `origin` remote) injects the
    credential per command, and the explicit refspec still updates the
    `refs/remotes/origin/<branch>` tracking ref that the subsequent
    `checkout -B <branch> origin/<branch>` resets to. The URL is
    redacted from all log output via `log_args`.

    Args:
        cwd: Working directory of the git repo.
        authenticated_url: Token-bearing URL from
            `build_authenticated_url` (never logged).
        branch: The branch to fetch (e.g. `main`).

    Raises:
        subprocess.CalledProcessError: If `git fetch` fails.
    """
    refspec = f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
    run_git(
        ["fetch", authenticated_url, refspec],
        cwd=cwd,
        log_args=["fetch", "<redacted-url>", refspec],
    )


def push_branch(cwd: Path, authenticated_url: str, branch: str) -> None:
    """Push HEAD to `branch` at the authenticated URL.

    Pushing by explicit URL keeps the credential per-command (nothing
    persisted in `.git/config`). The URL is redacted from all log
    output via `log_args`.

    Args:
        cwd: Working directory of the git repo.
        authenticated_url: Token-bearing URL from
            `build_authenticated_url` (never logged).
        branch: The branch to push to (e.g. `main`).

    Raises:
        subprocess.CalledProcessError: If `git push` fails.
    """
    refspec = f"HEAD:refs/heads/{branch}"
    run_git(
        ["push", authenticated_url, refspec],
        cwd=cwd,
        log_args=["push", "<redacted-url>", refspec],
    )
