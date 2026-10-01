"""Platform adapter factory (issue #590, Stage A).

The single bootstrap location that wires a concrete adapter to the
``PlatformAdapter`` port, keeping adapter selection out of the engine.
"""

from __future__ import annotations

from typing import Any

from adapters.base import PlatformAdapter

__all__ = ["PlatformAdapter", "build_adapter"]


# @stub-protected
def build_adapter(kind: str, **kwargs: Any) -> PlatformAdapter:
    """Select the adapter for ``kind`` (``"github"`` | ``"local"``) and forward ``kwargs``."""
    if kind == "local":
        from adapters.local import LocalAdapter

        return LocalAdapter(**kwargs)
    if kind == "github":
        from adapters.github import GitHubAdapter

        return GitHubAdapter(**kwargs)
    raise ValueError(f"unknown adapter kind: {kind!r}")
