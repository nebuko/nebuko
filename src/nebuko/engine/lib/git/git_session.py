"""Engine-owned git session guard: fail closed on credentials and leftovers.

Two silent failures let a credentials-less run finish green (issue #1089, run
39625740695): git auth lived only in the workflow, so every ``git push`` could
fail while ``gh`` (``GH_TOKEN``) succeeded, and nothing guaranteed that a
session ended with everything committed and pushed.

:class:`GitSessionGuard` closes both, engine-side, so external callers get the
same guarantee without declaring anything:

* :meth:`GitSessionGuard.preflight` runs before the agent phase — it writes the
  repo-local commit identity, then *proves* auth with ``git ls-remote``. The
  caller's own auth (SSH, ``credential.helper``, a checkout extraheader, or an
  external app token) is left untouched: a token is installed only when the
  probe fails on a github.com HTTPS remote.
* :meth:`GitSessionGuard.finalize` runs after the agent phase — it commits and
  pushes anything still pending, then verifies the tree is clean *and* the
  branch is fully pushed. Before pushing it re-proves auth and installs the
  env token when the probe fails, so it never depends on ambient credentials
  surviving the session (issue #1134).

Both entry points never raise: a failure is returned as data, and the engine
turns it into an ``ExitCode.ERROR`` run.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:  # openhands ships with the ``runtime`` extra only.
    from openhands.sdk import get_logger
except ImportError:
    # The lint-config job installs base deps (no openhands) yet must still
    # import this module through the engine package (issue #1129).
    get_logger = logging.getLogger

logger = get_logger(__name__)

# @stub-protected
GIT_IDENTITY_NAME: str = "nebuko"
# @stub-protected
GIT_IDENTITY_EMAIL: str = "nebuko@nebuko.dev"

#: Skip reasons a guard cannot act on. They mirror the ``check_publication``
#: taxonomy: no remote or a detached HEAD means there is nothing to publish.
# @stub-protected
BENIGN_SKIPS: tuple[str, ...] = ("no git remote", "detached HEAD")

#: Env vars carrying push credentials, in precedence order.
_TOKEN_ENV_VARS: tuple[str, ...] = ("NEBUKO_GIT_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")

_GIT_TIMEOUT_S = 30
_PUSH_TIMEOUT_S = 120
#: Prefixes of the remotes a token extraheader can authenticate.
_TOKEN_HOSTS: tuple[str, ...] = ("https://github.com/",)


def _dedent(text: str) -> str:
    """Collapse a multi-line git error into one log-friendly line."""
    return " ".join(text.split())


@dataclass(frozen=True)
class GuardResult:
    """Outcome of one guard pass.

    ``ok`` is the pass/fail verdict; ``skipped`` marks the benign case where
    there was nothing to guard (``ok`` is then True and ``detail`` explains
    why); ``detail`` is actionable prose — it names the env vars to set or the
    remote to configure, never a token.
    """

    ok: bool
    skipped: bool = False
    detail: str = ""


#: The engine's dirty-tree auto-commit message: recognizable as an engine-made
#: commit, and recorded as a ``dirty`` history entry (issue #1153).
DIRTY_COMMIT_MESSAGE = "chore: engine: commit changes forgotten by agent"


class GitSessionGuard:
    """Prove git auth before a session and a clean, pushed tree after it.

    Args:
        repo_root: The real checkout (``GFA_REPO_ROOT`` / the engine's
            ``repo_root``) — the agent host may run under a temporary cwd.
        env: The run's environment, read for the token and the optional
            identity overrides.
        remote: The remote every git command targets.
    """

    # @stub-protected
    def __init__(
        self,
        repo_root: Path,
        env: Mapping[str, str],
        remote: str = "origin",
    ) -> None:
        self.repo_root = Path(repo_root)
        self.env = env
        self.remote = remote
        #: Set when this guard installed the token extraheader itself.
        self._token_installed = False

    @property
    def repo(self) -> str:
        """This checkout, for error details."""
        return str(self.repo_root)

    # ── Entry points ──────────────────────────────────────────────

    # @stub-protected
    def preflight(self) -> GuardResult:
        """Identity, then prove auth; install a token only if the probe fails.

        Returns:
            ``ok=True`` when the remote is reachable (the caller's auth is
            untouched), a benign skip when there is no remote or a detached
            HEAD, and ``ok=False`` with actionable detail otherwise.
        """
        if not self._is_git_checkout():
            return GuardResult(ok=False, detail=f"{self.repo} is not a git checkout")
        remote_url = self._remote_url()
        if remote_url is None:
            return GuardResult(ok=True, skipped=True, detail="no git remote")
        if self._current_branch() in ("", "HEAD"):
            return GuardResult(ok=True, skipped=True, detail="detached HEAD")

        self._configure_identity()

        probe_failure = self._ensure_push_auth()
        if probe_failure is not None:
            return GuardResult(
                ok=False, detail=self._auth_failure_detail(remote_url, probe_failure)
            )
        if self._token_installed:
            return GuardResult(
                ok=True, detail=f"{self.remote} authenticated with a token"
            )
        return GuardResult(ok=True, detail=f"{self.remote} authenticated")

    # @stub-protected
    def finalize(self) -> GuardResult:
        """Commit and push any leftovers, then prove the session fully published.

        Auth is re-proven first and the env token installed when the probe
        fails on a github.com HTTPS remote (issue #1134): finalize never
        depends on ambient credentials surviving the session. On a token host
        a failed probe is fatal; elsewhere the push is still attempted, since
        the probe may lack the caller's own credentials.

        Returns:
            ``ok=True`` when the tree is clean and the branch SHA matches the
            remote's, a benign skip for no remote/detached HEAD, and
            ``ok=False`` with actionable detail otherwise.
        """
        if self._remote_url() is None:
            return GuardResult(ok=True, skipped=True, detail="no git remote")
        branch = self._current_branch()
        if branch in ("", "HEAD"):
            return GuardResult(ok=True, skipped=True, detail="detached HEAD")

        self._configure_identity()

        auth_failure = self._ensure_push_auth()
        if auth_failure is not None:
            remote_url = self._remote_url()
            if remote_url is not None and self._is_token_remote(remote_url):
                return GuardResult(
                    ok=False,
                    detail=self._auth_failure_detail(remote_url, auth_failure),
                )
            logger.warning(
                "git session: could not verify %s auth before the push: %s",
                self.remote,
                auth_failure,
            )

        commit_problem = self._commit_all_pending()

        problems: list[str] = []
        if commit_problem:
            problems.append(commit_problem)
        if self._pending_paths():
            problems.append(
                "the working tree still holds uncommitted changes after the "
                "automatic commit (see `git status`)"
            )
        if not problems:
            push_problem = self._push_branch(branch)
            if push_problem:
                problems.append(push_problem)
            else:
                push_problem = self._is_fully_pushed(branch)
                if push_problem:
                    problems.append(push_problem)
        if problems:
            return GuardResult(
                ok=False,
                detail="; ".join(problems)
                + self._token_hint(),
            )
        return GuardResult(ok=True, detail=f"{branch} is clean and pushed")

    # ── Identity and credentials ──────────────────────────────────

    # @stub-protected
    def _configure_identity(self) -> None:
        """Write the repo-local commit identity (``nebuko``).

        Repo-local so a bare checkout with no global identity still commits;
        the env overrides keep the two constants the single source of truth.
        Never raises: a failure surfaces at commit time with git's own message.
        """
        name = self.env.get("NEBUKO_GIT_AUTHOR_NAME") or GIT_IDENTITY_NAME
        email = self.env.get("NEBUKO_GIT_AUTHOR_EMAIL") or GIT_IDENTITY_EMAIL
        try:
            self._run_git(["config", "user.name", name])
            self._run_git(["config", "user.email", email])
        except RuntimeError as exc:
            logger.warning("git session: could not write the git identity: %s", exc)

    # @stub-protected
    def _resolve_token(self) -> Optional[str]:
        """The first non-empty push token in the environment, if any."""
        for name in _TOKEN_ENV_VARS:
            value = (self.env.get(name) or "").strip()
            if value:
                return value
        return None

    # @stub-protected
    def _install_credentials(self) -> bool:
        """Install the token as a repo-local ``http`` extraheader.

        Same encoding the workflow uses (basic ``x-access-token:<token>``).
        Returns False when no token is available or the write fails. The token
        is never logged.
        """
        token = self._resolve_token()
        if token is None:
            return False
        encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        try:
            self._run_git(
                [
                    "config",
                    f"http.https://github.com/.extraheader",
                    f"AUTHORIZATION: basic {encoded}",
                ]
            )
        except RuntimeError as exc:
            logger.warning("git session: could not install the push token: %s", exc)
            return False
        return True

    # @stub-protected
    def _verify_remote(self) -> Optional[str]:
        """Probe the remote; return the failure detail, or ``None`` on success."""
        try:
            self._run_git(["ls-remote", "--heads", self.remote])
        except RuntimeError as exc:
            return _dedent(str(exc))
        return None

    # @stub-protected
    def _ensure_push_auth(self) -> Optional[str]:
        """Prove push auth, installing the env token when the probe fails.

        Shared by preflight and finalize so neither depends on ambient
        credentials surviving the session (issue #1134). Returns the probe
        failure detail, or ``None`` when the remote authenticates. A token is
        installed only for a github.com HTTPS remote, and only when the probe
        failed — the caller's own auth is left untouched otherwise.
        """
        probe_failure = self._verify_remote()
        if probe_failure is None:
            return None
        remote_url = self._remote_url()
        if remote_url is None or not self._is_token_remote(remote_url):
            return probe_failure
        if not self._install_credentials():
            return probe_failure
        probe_failure = self._verify_remote()
        if probe_failure is not None:
            return probe_failure
        self._token_installed = True
        logger.info("git session: installed the push token for %s", self.remote)
        return None

    # ── Publication ───────────────────────────────────────────────

    # @stub-protected
    def _commit_all_pending(self) -> str:
        """Commit everything git reports once (``git add -A``). Returns a problem.

        ``.gitignore`` still applies, so generated files never ride along. An
        already-clean tree is not a problem; a commit that could not be made
        (no identity, a hook refusal) is returned as actionable prose. A made
        commit's short sha is returned so the engine can record it (issue
        #1153); an empty string means nothing was committed.
        """
        if not self._pending_paths():
            return ""
        try:
            self._run_git(["add", "-A"])
            self._run_git(["commit", "-m", DIRTY_COMMIT_MESSAGE])
        except RuntimeError as exc:
            return f"automatic session commit failed: {_dedent(str(exc))}"
        return self._short_sha()

    def _short_sha(self) -> str:
        """The current HEAD's short sha, or ``""`` when it cannot be read."""
        try:
            return self._run_git(["rev-parse", "--short", "HEAD"])
        except RuntimeError:
            return ""

    # @stub-protected
    def _is_fully_pushed(self, branch: str) -> Optional[str]:
        """True when the remote branch SHA equals local HEAD. Returns a problem.

        Compiled against ``git ls-remote`` rather than the upstream ref, because
        the publication ``push`` never sets one.
        """
        try:
            head = self._run_git(["rev-parse", "HEAD"]).strip()
            out = self._run_git(
                ["ls-remote", self.remote, f"refs/heads/{branch}"]
            )
        except RuntimeError as exc:
            return f"could not verify the push of {branch}: {_dedent(str(exc))}"
        remote_sha = out.split()[0] if out.split() else ""
        if remote_sha != head:
            return (
                f"branch {branch} is not fully pushed: remote "
                f"{remote_sha[:12] or '<missing>'} != local {head[:12]}"
            )
        return None

    # ── Plumbing ─────────────────────────────────────────────────

    # @stub-protected
    def _run_git(self, args: list[str], timeout: int = _GIT_TIMEOUT_S) -> str:
        """Run a git command in ``repo_root``; return stdout, raise on failure."""
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(self.repo_root),
            # Never block on a credentials prompt: fail fast instead (no TTY
            # in CI, so a prompt would hang until the timeout).
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed ({result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return result.stdout.strip()

    def _is_git_checkout(self) -> bool:
        try:
            self._run_git(["rev-parse", "--is-inside-work-tree"])
        except (RuntimeError, OSError):
            return False
        return True

    def _remote_url(self) -> Optional[str]:
        """The configured URL of ``self.remote``, or ``None`` when it is absent."""
        try:
            out = self._run_git(["remote", "get-url", self.remote]).strip()
        except (RuntimeError, OSError):
            return None
        return out or None

    def _current_branch(self) -> str:
        try:
            return self._run_git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        except (RuntimeError, OSError):
            return ""

    def _pending_paths(self) -> tuple[str, ...]:
        try:
            out = self._run_git(["status", "--porcelain"])
        except (RuntimeError, OSError):
            # An uninspectable tree is reported as pending: the caller turns it
            # into a failure instead of reading it as "clean".
            return ("<uninspectable>",)
        return tuple(line for line in out.splitlines() if line.strip())

    def _push_branch(self, branch: str) -> str:
        """Push ``HEAD`` to ``refs/heads/<branch>``. Returns a problem or ``""``.

        ``HEAD:refs/heads/<branch>`` needs no upstream, so it works on the first
        push of a fresh branch as well.
        """
        try:
            self._run_git(
                ["push", self.remote, f"HEAD:refs/heads/{branch}"],
                timeout=_PUSH_TIMEOUT_S,
            )
        except RuntimeError as exc:
            return f"could not push {branch}: {_dedent(str(exc))}"
        return ""

    def _is_token_remote(self, remote_url: str) -> bool:
        return remote_url.startswith(_TOKEN_HOSTS)

    def _token_hint(self) -> str:
        if self._resolve_token() is not None:
            return ""
        return (
            " — set NEBUKO_GIT_TOKEN (or GH_TOKEN / GITHUB_TOKEN) with repo write "
            "access"
        )

    def _auth_failure_detail(self, remote_url: str, probe_failure: str) -> str:
        """Actionable prose naming the fix, never the credentials themselves."""
        target = _strip_credentials(remote_url)
        if self._is_token_remote(remote_url):
            what = (
                "no usable push token: set NEBUKO_GIT_TOKEN (preferred), "
                "GH_TOKEN or GITHUB_TOKEN with contents write access"
            )
        else:
            what = f"configure credentials for {target} (ssh key or credential.helper)"
        return f"cannot authenticate to {target} — {what} ({probe_failure})"


def _strip_credentials(remote_url: str) -> str:
    """Drop ``user:token@`` from an HTTPS remote before it reaches a log line."""
    if "://" not in remote_url:
        return remote_url
    scheme, _, rest = remote_url.partition("://")
    if "@" in rest:
        _, _, rest = rest.partition("@")
    return f"{scheme}://{rest}"
