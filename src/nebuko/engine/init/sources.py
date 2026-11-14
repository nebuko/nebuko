"""Template sources: the pinned ref over HTTPS, or a local checkout (issue #991).

The provider templates are fetched from ``raw.githubusercontent.com`` at the
pinned tag — the exact URL ``docs/adoption.md`` already tells clients to curl —
so no provider copy is packaged and no clone is needed.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from nebuko.engine.init.ports import SetupError, TemplateSource

DEFAULT_BASE_URL = "https://raw.githubusercontent.com/nebuko/nebuko"


def _urlopen(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (https only)
        return response.read().decode("utf-8")


class HttpTemplateSource(TemplateSource):
    """Fetches ``<base_url>/<ref>/<path>`` over HTTPS."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        opener: Callable[[str], str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._opener = opener or _urlopen

    # @stub-protected
    def fetch(self, path: str, ref: str) -> str:
        url = f"{self._base_url}/{ref}/{path.lstrip('/')}"
        try:
            return self._opener(url)
        except (urllib.error.URLError, OSError) as exc:
            raise SetupError(f"cannot fetch {url}: {exc}") from exc


class FileTemplateSource(TemplateSource):
    """Reads ``root/<path>`` from a local checkout; ``ref`` is ignored."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    # @stub-protected
    def fetch(self, path: str, ref: str) -> str:
        target = self._root / path
        try:
            return target.read_text(encoding="utf-8")
        except OSError as exc:
            raise SetupError(f"cannot read {target}: {exc}") from exc
