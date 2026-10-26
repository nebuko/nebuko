"""Capability-driven comment rendering for the platform port (issue #1164).

Engine renderers must not emit markup a platform cannot render. They build a
:class:`CommentRenderer` from the adapter's :class:`~nebuko.adapters.base.PosterCapabilities`
once, then route the assembled body through :meth:`CommentRenderer.render`.
"""

from __future__ import annotations

from nebuko.adapters.base import PosterCapabilities

__all__ = ["CommentRenderer"]


# @stub-protected
class CommentRenderer:
    """Degrade a comment body to what the platform can render."""

    # @stub-protected
    def __init__(self, capabilities: PosterCapabilities) -> None:
        raise NotImplementedError("CommentRenderer is a stub")

    # @stub-protected
    def render(self, body: str) -> str:
        raise NotImplementedError("CommentRenderer is a stub")
