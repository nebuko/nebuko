"""Repository-variable writer for ``nebuko init`` (issue #1025).

``RepoVariableWriter`` upserts a repository variable through the GitHub
repo-variables API, so re-running ``init`` never duplicates or clobbers
unrelated variables. ``client`` is the ``gh``-backed transport the rest of the
package wraps (``github_client.GitHubTransport``).
"""

from __future__ import annotations

from typing import Any


class RepoVariableWriter:
    """Reads and upserts one repository's Actions variables."""

    def __init__(self, client: Any, owner: str, repo_name: str) -> None:
        self.client = client
        self.owner = owner
        self.repo_name = repo_name

    # @stub-protected: signature defined by architect — do not modify
    def get(self, name: str) -> str | None:
        """The variable's current value, or ``None`` when it does not exist."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def set(self, name: str, value: str) -> None:
        """Upsert ``name``: create it when absent, update it when present."""
        raise NotImplementedError
