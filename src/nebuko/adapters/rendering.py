"""Capability-driven comment rendering for the platform port (issue #1164).

Engine renderers must not emit markup a platform cannot render. They build a
:class:`CommentRenderer` from the adapter's :class:`~nebuko.adapters.base.PosterCapabilities`
once, then route the assembled body through :meth:`CommentRenderer.render`.
"""

from __future__ import annotations

import html
import re

from nebuko.adapters.base import PosterCapabilities

__all__ = ["CommentRenderer"]

# A `<details>` block: the whole element is captured so the summary line can be
# kept (as a heading) while the wrapper markup is dropped.
_DETAILS_RE = re.compile(
    r"<details>(?:\s*<summary>(?P<summary>.*?)</summary>)?(?P<body>.*?)</details>",
    re.DOTALL | re.IGNORECASE,
)
_TASKLIST_RE = re.compile(r"^(\s*)- \[([ xX])\] ", re.MULTILINE)


class CommentRenderer:
    """Degrade a comment body to what the platform can render."""

    def __init__(self, capabilities: PosterCapabilities) -> None:
        self._capabilities = capabilities

    def render(self, body: str) -> str:
        """Return ``body`` with every unsupported construct degraded.

        Degrades, in order: ``<details>`` blocks are unwrapped to their
        content with the ``<summary>`` kept as a heading line; ``- [ ]`` /
        ``- [x]`` task-list markers become plain ``-`` bullets; raw HTML
        (other than the engine's own markers) is escaped unless the platform
        declares ``raw_html``.
        """
        rendered = body
        if not self._capabilities.details:
            rendered = self._unwrap_details(rendered)
        if not self._capabilities.tasklist:
            rendered = _TASKLIST_RE.sub(r"\1- ", rendered)
        if not self._capabilities.raw_html:
            rendered = self._escape_raw_html(rendered)
        return rendered

    @staticmethod
    def _unwrap_details(body: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            summary = (match.group("summary") or "").strip()
            body_text = (match.group("body") or "").strip("\n")
            parts = []
            if summary:
                parts.append(f"#### {summary}")
            parts.append(body_text.strip())
            return "\n\n".join(part for part in parts if part)

        return _DETAILS_RE.sub(_replace, body)

    @staticmethod
    def _escape_raw_html(body: str) -> str:
        # Escape bare tags but leave the engine's own workflow markers alone:
        # they are the contract every adapter relies on, never presentation.
        def _replace(match: re.Match[str]) -> str:
            tag = match.group(0)
            if tag.startswith("<!--"):
                return tag
            return html.escape(tag)

        return re.sub(r"</?[a-zA-Z][^>]*>|<!--.*?-->", _replace, body, flags=re.DOTALL)
