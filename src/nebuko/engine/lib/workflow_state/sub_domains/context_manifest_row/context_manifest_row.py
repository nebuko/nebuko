"""Persisted context-manifest row (issue #1346).

One body-free row of the ordered context plan an agent run was built from: the
position, the section kind, its provenance and the size/hash of the text. The
full text lives in the run artifacts (``context_plan.json`` / ``prompt.md``);
this compact row is what the workflow state carries so replay can show the
composition and order offline.
"""

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ContextManifestRow"]


class ContextManifestRow(BaseModel):  # @stub-protected
    """Immutable record of one context section (no body text)."""

    model_config = ConfigDict(frozen=True)

    order: int
    kind: str
    source: str = ""
    comment_ids: list[str] = Field(default_factory=list)
    chars: int = 0
    sha256: str = ""
