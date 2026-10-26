"""``fetch_comment_bodies`` — recall full comment bodies by id (stubs).

Non-terminal, read-only engine tool: given a list of cached comment ids it
returns their full bodies, wrapped in the same untrusted-content framing as
``render_unverified_appendix`` (#872) so a full body reaching the prompt is
still explicitly marked as untrusted third-party text.
"""

from __future__ import annotations

from dataclasses import dataclass


# @stub-protected: signature defined by architect — do not modify
@dataclass(frozen=True)
class FetchCommentBodiesAction:
    """The comment ids the agent wants the full bodies for."""

    comment_ids: list[int]


# @stub-protected: signature defined by architect — do not modify
@dataclass(frozen=True)
class CommentBodiesObservation:
    """Bodies found in the run's cached thread, plus ids missing from the cache."""

    bodies: dict[str, str]
    not_found: list[int]


# @stub-protected: signature defined by architect — do not modify
class FetchCommentBodiesExecutor:
    """Look up full comment bodies in the per-run cached bodies map."""

    context_bodies: dict[str, str]

    def __init__(self, context_bodies: dict[str, str]) -> None:
        self.context_bodies = context_bodies

    def execute(self, action: FetchCommentBodiesAction) -> CommentBodiesObservation:
        bodies: dict[str, str] = {}
        not_found: list[int] = []
        for comment_id in action.comment_ids:
            body = self.context_bodies.get(str(comment_id))
            if body is None:
                not_found.append(comment_id)
            else:
                bodies[str(comment_id)] = body
        return CommentBodiesObservation(bodies=bodies, not_found=not_found)


# @stub-protected: signature defined by architect — do not modify
class FetchCommentBodiesTool:
    """ToolDefinition binding an executor to the run's cached comment bodies."""

    @classmethod
    def create(cls, context_bodies: dict[str, str]) -> "FetchCommentBodiesTool":
        raise NotImplementedError
