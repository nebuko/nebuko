"""Nested step-path selector entity (issue #822).

A ``StepPath`` is the dotted selector a human passes to
``/nebuko run workflow --step`` or ``/nebuko force-step --name`` to address a
step *inside* a mounted sub-workflow graph, e.g. ``sub_impl.step_3`` or
``sub_impl.step_3.review``. It carries the ordered descent chain — outermost
mount first — resolved hop-by-hop against the workflow definitions the mounts
reference.

Dots are reserved as separators, so a step name containing a dot is
unaddressable and fails loudly (segment charset is ``[A-Za-z0-9_-]+``). The
storage form stays the flat ``WorkflowPosition`` (``wf/step|wf/step|...``);
this entity only addresses where a *fresh* run enters, it is not part of the
persisted state.
"""

import re
from typing import Optional, Tuple

from pydantic import BaseModel

__all__ = ["StepPath", "SEGMENT_PATTERN"]

#: Charset a single path segment may use. Kept in sync with the safe
#: workflow-name rule so a selector cannot smuggle a traversal token.
SEGMENT_PATTERN = r"[A-Za-z0-9_-]+"

_SEGMENT_RE = re.compile(rf"^{SEGMENT_PATTERN}$")


class StepPath(BaseModel):
    """A validated, dot-separated step selector (issue #822).

    Attributes:
        raw: The original selector string.
        segments: Non-empty path segments, outermost mount first; each matches
            :data:`SEGMENT_PATTERN`.
    """

    raw: str
    segments: Tuple[str, ...]

    @classmethod
    def parse(cls, raw: str) -> Optional["StepPath"]:
        """Parse ``raw`` into a :class:`StepPath`, or ``None`` when malformed.

        Splits on ``.`` and requires every segment to be non-empty and match
        :data:`SEGMENT_PATTERN`. An empty selector, a leading/trailing/double
        dot, whitespace, or a slash all yield ``None`` — the caller fails
        loudly rather than silently falling back to the first step.

        # @stub-protected
        """
        if not raw:
            return None
        segments = tuple(raw.split("."))
        if not all(_SEGMENT_RE.match(segment) for segment in segments):
            return None
        return cls(raw=raw, segments=segments)

    @property
    def is_nested(self) -> bool:
        """True when the selector descends past the top-level workflow.

        # @stub-protected
        """
        return len(self.segments) > 1

    @property
    def depth(self) -> int:
        """Number of segments (descent levels) in the selector.

        # @stub-protected
        """
        return len(self.segments)

    def leaf_step_name(self) -> str:
        """The innermost (final) segment — the named target step.

        # @stub-protected
        """
        return self.segments[-1]

    def mount_prefix(self) -> Tuple[str, ...]:
        """The segments strictly above the leaf — the mount chain to walk.

        # @stub-protected
        """
        return self.segments[:-1]
