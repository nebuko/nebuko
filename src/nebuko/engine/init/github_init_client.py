"""The ``gh``-backed ``GitHubInitClient`` adapter (issue #1272).

Every operation the client-facing init flow performs on GitHub goes through
the client's own ``gh`` CLI: branch upsert, pull request, and secret writes.
``gh`` cannot seal secrets, so ``put_secret`` accepts only a plaintext
``SecretValue`` (the web/Worker path seals its own values).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Callable

from nebuko.engine.init.core import SecretTarget, SecretValue
from nebuko.engine.init.ports import SetupError


def _run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=False, **kwargs)


class GhInitClient:
    """``GitHubInitClient`` over the ``gh`` CLI."""

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess] | None = None) -> None:
        self._runner = runner or _run

    # @stub-protected
    def get_existing_files(self, target: SecretTarget, paths: list[str]) -> dict[str, bool]:
        """Whether each path already exists on the default branch."""
        found: dict[str, bool] = {}
        for path in paths:
            result = self._gh(
                ["api", f"repos/{target.repo}/contents/{path}", "--jq", ".path"],
                check=False,
            )
            found[path] = result.returncode == 0 and bool(result.stdout.strip())
        return found

    # @stub-protected
    def existing_secrets(self, target: SecretTarget) -> frozenset[str]:
        """The secret names stored at the target scope; raises on failure."""
        args = ["secret", "list"]
        if target.scope == "org":
            args += ["--org", target.org or ""]
        else:
            args += ["--repo", target.repo]
        args += ["--json", "name", "--jq", ".[].name"]
        out = self._gh(args, check=True).stdout
        return frozenset(line.strip() for line in out.splitlines() if line.strip())

    # @stub-protected
    def upsert_branch_and_commit(self, target: SecretTarget, branch: str, files: dict[str, str]) -> None:
        """Force-update ``branch`` with a commit carrying ``files``."""
        default_branch = self._api(target, f"repos/{target.repo}", jq=".default_branch")
        head = self._api(
            target, f"repos/{target.repo}/git/ref/heads/{default_branch}", jq=".object.sha"
        )
        base_tree = self._api(target, f"repos/{target.repo}/git/commits/{head}", jq=".tree.sha")
        entries = []
        for path in sorted(files):
            blob = self._api_json(
                target,
                f"repos/{target.repo}/git/blobs",
                {"content": files[path], "encoding": "utf-8"},
                jq=".sha",
            )
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob})
        tree = self._api_json(
            target,
            f"repos/{target.repo}/git/trees",
            {"base_tree": base_tree, "tree": entries},
            jq=".sha",
        )
        commit = self._api_json(
            target,
            f"repos/{target.repo}/git/commits",
            {"message": f"nebuko init: {', '.join(sorted(files))}", "tree": tree, "parents": [head]},
            jq=".sha",
        )
        existing = self._gh(["api", f"repos/{target.repo}/git/ref/heads/{branch}"], check=False)
        if existing.returncode == 0:
            self._api_json(
                target,
                f"repos/{target.repo}/git/refs/heads/{branch}",
                {"sha": commit, "force": True},
                method="PATCH",
                jq=".object.sha",
            )
            return
        self._api_json(
            target,
            f"repos/{target.repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": commit},
            jq=".object.sha",
        )

    # @stub-protected
    def get_or_open_pull_request(self, target: SecretTarget, branch: str, title: str, body: str) -> str:
        """The open PR for ``branch``, or a newly opened one."""
        existing = self._gh(
            ["pr", "view", branch, "--repo", target.repo, "--json", "url", "--jq", ".url"],
            check=False,
        )
        if existing.returncode == 0 and existing.stdout.strip():
            return existing.stdout.strip()
        default_branch = self._api(target, f"repos/{target.repo}", jq=".default_branch")
        created = self._gh(
            [
                "pr", "create",
                "--repo", target.repo,
                "--head", branch,
                "--base", default_branch,
                "--title", title,
                "--body", body,
            ],
            check=False,
        )
        if created.returncode == 0 and created.stdout.strip():
            return created.stdout.strip()
        raise SetupError(f"gh pr create failed: {(created.stderr or created.stdout).strip()}")

    # @stub-protected
    def put_secret(self, target: SecretTarget, name: str, value: SecretValue) -> None:
        """Store ``name``; the value travels on stdin, never in argv."""
        if not isinstance(value, str):
            raise SetupError("gh cannot store a sealed secret; provide the plaintext value")
        args = ["secret", "set", name]
        if target.scope == "org":
            args += ["--org", target.org or ""]
        else:
            args += ["--repo", target.repo]
        self._gh(args, input=value, check=True)

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _gh(self, args: list[str], *, input: str | None = None, check: bool = True):
        result = self._runner(["gh", *args], capture_output=True, text=True, input=input)
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise SetupError(f"gh {' '.join(args)} failed: {detail}")
        return result

    def _api(self, target: SecretTarget, endpoint: str, *, jq: str | None = None) -> str:
        args = ["api", endpoint]
        if jq:
            args += ["--jq", jq]
        return self._gh(args, check=True).stdout.strip()

    def _api_json(
        self,
        target: SecretTarget,
        endpoint: str,
        payload: dict[str, Any],
        *,
        method: str = "POST",
        jq: str | None = None,
    ) -> str:
        args = ["api", "--method", method, endpoint, "--input", "-"]
        if jq:
            args += ["--jq", jq]
        result = self._gh(args, input=json.dumps(payload), check=True)
        return result.stdout.strip()
