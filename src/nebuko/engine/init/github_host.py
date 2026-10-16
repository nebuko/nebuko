"""The ``gh`` CLI adapter for ``nebuko init`` (issue #991).

Everything the command does on the client's behalf goes through the client's own
``gh`` authentication: no Nebuko server, no GitHub App, no credential custody.
Secret values are piped to ``gh secret set`` on **stdin** and never appear in an
argument list, in the output, or in a committed file.
"""

from __future__ import annotations

import json
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


def _run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=False, **kwargs)


class GitHubHost(SetupHost):
    """``SetupHost`` over the ``gh`` CLI."""

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess] | None = None) -> None:
        self._runner = runner or _run

    # ── preflight ────────────────────────────────────────────────────────────

    def preflight(self, target: SetupTarget) -> PreflightReport:
        status = self._run(["gh", "auth", "status", "--json", "hosts"], check=False)
        if status.returncode != 0:
            return PreflightReport(ok=False, missing_scopes=(), remediation="gh auth login")
        scopes = _scopes_from_status(status.stdout, target.host)
        if scopes is None:
            scopes = self._scopes_from_headers()
        if scopes is None:
            # Fine-grained PATs and app tokens carry no scope list: there is
            # nothing to verify, so do not block the client on a guess.
            return PreflightReport(ok=True, missing_scopes=(), remediation="")
        missing = tuple(name for name in REQUIRED_SCOPES if name not in scopes)
        if missing:
            return PreflightReport(ok=False, missing_scopes=missing, remediation=REMEDIATION)
        return PreflightReport(ok=True, missing_scopes=(), remediation="")

    def _scopes_from_headers(self) -> set[str] | None:
        result = self._run(["gh", "api", "-i", "user"], check=False)
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            name, _, value = line.partition(":")
            if name.strip().lower() == "x-oauth-scopes":
                return {scope.strip() for scope in value.split(",") if scope.strip()}
        return None

    # ── repository state ─────────────────────────────────────────────────────

    def app_installed(self, target: SetupTarget) -> bool:
        """Always ``False`` until the Nebuko GitHub App ships (PAT mode today)."""
        return False

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


def _scopes_from_status(stdout: str, host: str) -> set[str] | None:
    """The active account's scopes for ``host``, or ``None`` when unreported."""
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return None
    accounts = (payload.get("hosts") or {}).get(host) or []
    for account in accounts:
        if not account.get("active"):
            continue
        raw = account.get("scopes")
        if raw:
            return {scope.strip() for scope in str(raw).split(",") if scope.strip()}
        return None
    return None
