"""Nebuko App installation detection for ``nebuko init`` (issue #1025).

``AppInstallationResolver`` answers one question — is the Nebuko GitHub App
installed on ``owner/repo_name``? — so ``init`` can skip the PAT prompt in App
mode. ``client`` is the ``gh``-backed transport the rest of the package wraps
(``github_client.GitHubTransport``); it is typed loosely here so the resolver
stays unit-testable with a fake.
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_APP_SLUG = "nebuko"


class AppInstallationResolver:
    """Whether the Nebuko App is installed on one repository."""

    def __init__(self, client: Any, owner: str, repo_name: str, app_slug: str) -> None:
        self.client = client
        self.owner = owner
        self.repo_name = repo_name
        self.app_slug = app_slug

    # @stub-protected: signature defined by architect — do not modify
    def is_installed(self) -> bool:
        """True iff the App is installed on ``owner/repo_name``."""
        return self._probe().ok

    # @stub-protected: signature defined by architect — do not modify
    def installation_id(self) -> int | None:
        """The App's installation id for this repository, or ``None``."""
        result = self._probe()
        if not result.ok:
            return None
        try:
            payload = json.loads(result.payload or "{}")
        except ValueError:
            return None
        identifier = payload.get("id") if isinstance(payload, dict) else None
        return identifier if isinstance(identifier, int) else None

    def _probe(self) -> Any:
        """GET the repository installation endpoint (200 installed, 404 not)."""
        return self.client.call(
            ["api", f"repos/{self.owner}/{self.repo_name}/installation"],
            mutative=False,
        )
