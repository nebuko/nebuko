"""The ``gh`` CLI adapter for ``nebuko init`` (issue #991).

Everything the command does on the client's behalf goes through the client's own
``gh`` authentication: no Nebuko server, no GitHub App, no credential custody.
Secret values are piped to ``gh secret set`` on **stdin** and never appear in an
argument list, in the output, or in a committed file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Callable, Sequence

from nebuko.engine.init.ports import (
    GeneratedFiles,
    PreflightReport,
    SetupError,
    SetupHost,
    SetupTarget,
)

REQUIRED_SCOPES = ("workflow",)
REMEDIATION = "gh auth refresh -s workflow"


class _NoActiveAccount:
    """Sentinel: ``gh`` reports no active account for the host (logged out)."""


_NO_ACTIVE_ACCOUNT = _NoActiveAccount()


class _Unknown:
    """Sentinel: the probe could not determine the token's scopes."""


_UNKNOWN = _Unknown()


def _run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=False, **kwargs)


class GitHubHost(SetupHost):
    """``SetupHost`` over the ``gh`` CLI."""

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess] | None = None) -> None:
        self._runner = runner or _run

    # ── preflight ────────────────────────────────────────────────────────────

    # @stub-protected
    def gh_available(self) -> bool:
        """Whether the ``gh`` CLI is on ``PATH`` at all."""
        return shutil.which("gh") is not None

    # @stub-protected
    def preflight(self, target: SetupTarget) -> PreflightReport:
        status = self._run(["gh", "auth", "status", "--json", "hosts"], check=False)
        scopes = (
            _scopes_from_status(status.stdout, target.host) if status.returncode == 0 else _UNKNOWN
        )
        if scopes is _UNKNOWN:
            # `gh auth status --json hosts` needs the `hosts` field, which gh
            # 2.77-2.79 reject ("Unknown JSON field"); probe the API instead of
            # misreporting a logged-in client as logged out.
            scopes = self._scopes_from_headers()
            if scopes is _UNKNOWN:
                return PreflightReport(ok=False, missing_scopes=(), remediation="gh auth login")
        elif scopes is _NO_ACTIVE_ACCOUNT:
            # `gh auth status --json hosts` exits 0 with an empty host map when
            # no token is present, so this is the logged-out case, not "the
            # token reports no scopes".
            return PreflightReport(ok=False, missing_scopes=(), remediation="gh auth login")
        elif scopes is None:
            # An active account that reports no scope list: consult the API,
            # but a failed lookup here must not be read as "logged out".
            scopes = self._scopes_from_headers()
            if scopes is _UNKNOWN:
                scopes = None
        return self._report_for(scopes)

    def _report_for(self, scopes: set[str] | None) -> PreflightReport:
        if scopes is None:
            # Fine-grained PATs and app tokens carry no scope list: there is
            # nothing to verify, so do not block the client on a guess.
            return PreflightReport(ok=True, missing_scopes=(), remediation="")
        missing = tuple(name for name in REQUIRED_SCOPES if name not in scopes)
        if missing:
            return PreflightReport(ok=False, missing_scopes=missing, remediation=REMEDIATION)
        return PreflightReport(ok=True, missing_scopes=(), remediation="")

    def _scopes_from_headers(self) -> set[str] | None | _Unknown:
        result = self._run(["gh", "api", "-i", "user"], check=False)
        if result.returncode != 0:
            return _UNKNOWN
        return _parse_scopes_header(result.stdout)

    # ── repository state ─────────────────────────────────────────────────────

    # @stub-protected
    def app_installed(self, target: SetupTarget) -> bool:
        """Always ``False`` until the Nebuko GitHub App ships (PAT mode today)."""
        return False

    # @stub-protected
    def existing_paths(self, target: SetupTarget, paths: Sequence[str]) -> set[str]:
        found: set[str] = set()
        for path in paths:
            result = self._run(
                ["gh", "api", f"repos/{target.slug}/contents/{path}", "--jq", ".path"],
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                found.add(path)
        return found

    # ── pull request ─────────────────────────────────────────────────────────

    # @stub-protected
    def open_pr(
        self,
        target: SetupTarget,
        branch: str,
        title: str,
        body: str,
        files: GeneratedFiles,
    ) -> str:
        default_branch = self._api(target, f"repos/{target.slug}", jq=".default_branch")
        head = self._api(
            target, f"repos/{target.slug}/git/ref/heads/{default_branch}", jq=".object.sha"
        )
        base_tree = self._api(
            target, f"repos/{target.slug}/git/commits/{head}", jq=".tree.sha"
        )
        entries = []
        for path, content in (
            (files.agent_config_path, files.agent_config),
            (files.workflow_path, files.workflow),
        ):
            blob = self._api_json(
                target,
                f"repos/{target.slug}/git/blobs",
                {"content": content, "encoding": "utf-8"},
                jq=".sha",
            )
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob})
        tree = self._api_json(
            target,
            f"repos/{target.slug}/git/trees",
            {"base_tree": base_tree, "tree": entries},
            jq=".sha",
        )
        commit = self._api_json(
            target,
            f"repos/{target.slug}/git/commits",
            {"message": title, "tree": tree, "parents": [head]},
            jq=".sha",
        )
        self._put_branch(target, branch, commit)
        return self._create_or_view_pr(target, branch, default_branch, title, body)

    def _put_branch(self, target: SetupTarget, branch: str, commit: str) -> None:
        """Create the branch, or move it when a previous run already made it."""
        existing = self._run(
            ["gh", "api", f"repos/{target.slug}/git/ref/heads/{branch}"], check=False
        )
        if existing.returncode == 0:
            self._api_json(
                target,
                f"repos/{target.slug}/git/refs/heads/{branch}",
                {"sha": commit, "force": True},
                method="PATCH",
                jq=".object.sha",
            )
            return
        self._api_json(
            target,
            f"repos/{target.slug}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": commit},
            jq=".object.sha",
        )

    def _create_or_view_pr(
        self, target: SetupTarget, branch: str, base: str, title: str, body: str
    ) -> str:
        created = self._run(
            [
                "gh", "pr", "create",
                "--repo", target.slug,
                "--head", branch,
                "--base", base,
                "--title", title,
                "--body", body,
            ],
            check=False,
        )
        if created.returncode == 0 and created.stdout.strip():
            return created.stdout.strip()
        existing = self._run(
            ["gh", "pr", "view", branch, "--repo", target.slug, "--json", "url", "--jq", ".url"],
            check=False,
        )
        if existing.returncode == 0 and existing.stdout.strip():
            return existing.stdout.strip()
        raise SetupError(f"gh pr create failed: {(created.stderr or created.stdout).strip()}")

    # ── secrets ──────────────────────────────────────────────────────────────

    # @stub-protected
    def existing_secrets(self, target: SetupTarget) -> frozenset[str]:
        # A listing failure must surface: "unknown" is never "nothing exists",
        # or an existing secret would be silently overwritten.
        args = ["gh", "secret", "list"]
        if target.org:
            args += ["--org", target.org]
        else:
            args += ["--repo", target.slug]
        args += ["--json", "name", "--jq", ".[].name"]
        out = self._run(args, check=True).stdout
        return frozenset(line.strip() for line in out.splitlines() if line.strip())

    # @stub-protected
    def set_secret(self, target: SetupTarget, name: str, value: str) -> None:
        args = ["gh", "secret", "set", name]
        if target.org:
            args += ["--org", target.org]
        else:
            args += ["--repo", target.slug]
        # The value travels on stdin: never in argv, never in the process list.
        self._run(args, input=value, check=True)

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _api(self, target: SetupTarget, endpoint: str, *, jq: str | None = None) -> str:
        args = ["gh", "api", endpoint]
        if jq:
            args += ["--jq", jq]
        return self._run(args, check=True).stdout.strip()

    def _api_json(
        self,
        target: SetupTarget,
        endpoint: str,
        payload: dict[str, Any],
        *,
        method: str = "POST",
        jq: str | None = None,
    ) -> str:
        args = ["gh", "api", "--method", method, endpoint, "--input", "-"]
        if jq:
            args += ["--jq", jq]
        result = self._run(args, input=json.dumps(payload), check=True)
        return result.stdout.strip()

    def _run(self, args: list[str], *, input: str | None = None, check: bool = True):
        result = self._runner(args, capture_output=True, text=True, input=input)
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise SetupError(f"{' '.join(args)} failed: {detail}")
        return result


def _parse_scopes_header(text: str) -> set[str] | None:
    """The ``X-OAuth-Scopes`` value of a ``gh api -i`` response, if present."""
    for line in text.splitlines():
        name, _, value = line.partition(":")
        if name.strip().lower() == "x-oauth-scopes":
            return {scope.strip() for scope in value.split(",") if scope.strip()}
    return None


def _scopes_from_status(
    stdout: str, host: str
) -> set[str] | None | _NoActiveAccount | _Unknown:
    """The active account's scopes for ``host``.

    ``None`` means an active account exists but reports no scope list
    (fine-grained PAT / app token); ``_NO_ACTIVE_ACCOUNT`` means ``gh`` has no
    active account for this host at all; ``_UNKNOWN`` means the payload is not
    the shape this gh release emits (older releases reject ``--json hosts``).
    """
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return _UNKNOWN
    if not isinstance(payload, dict):
        return _UNKNOWN
    hosts = payload.get("hosts")
    if not isinstance(hosts, dict):
        return _UNKNOWN
    accounts = hosts.get(host) or []
    for account in accounts:
        if not account.get("active"):
            continue
        raw = account.get("scopes")
        if raw:
            return {scope.strip() for scope in str(raw).split(",") if scope.strip()}
        return None
    return _NO_ACTIVE_ACCOUNT
