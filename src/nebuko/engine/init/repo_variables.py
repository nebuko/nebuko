"""Repository-variable writer for ``nebuko init`` (issue #1025).

``RepoVariableWriter`` upserts a repository variable through the GitHub
repo-variables API, so re-running ``init`` never duplicates or clobbers
unrelated variables. ``client`` is the ``gh``-backed transport the rest of the
package wraps (``github_client.GitHubTransport``).
"""

from __future__ import annotations

import json
from typing import Any

from nebuko.engine.init.ports import SetupError


class RepoVariableWriter:
    """Reads and upserts one repository's Actions variables."""

    def __init__(self, client: Any, owner: str, repo_name: str) -> None:
        self.client = client
        self.owner = owner
        self.repo_name = repo_name

    # @stub-protected: signature defined by architect — do not modify
    def get(self, name: str) -> str | None:
        """The variable's current value, or ``None`` when it does not exist."""
        result = self.client.call(["api", f"{self._base()}/{name}"], mutative=False)
        if not result.ok:
            return None
        try:
            payload = json.loads(result.payload or "{}")
        except ValueError:
            return None
        value = payload.get("value") if isinstance(payload, dict) else None
        return value if isinstance(value, str) else None

    # @stub-protected: signature defined by architect — do not modify
    def set(self, name: str, value: str) -> None:
        """Upsert ``name``: create it when absent, update it when present."""
        body = json.dumps({"name": name, "value": value})
        if self.get(name) is None:
            args = ["api", "--method", "POST", self._base()]
        else:
            args = ["api", "--method", "PATCH", f"{self._base()}/{name}"]
        result = self.client.call(args, mutative=True, input_data=body)
        if not result.ok:
            raise SetupError(
                f"cannot set repository variable {name} on "
                f"{self.owner}/{self.repo_name}: {result.detail or result.status}"
            )

    def _base(self) -> str:
        return f"repos/{self.owner}/{self.repo_name}/actions/variables"
