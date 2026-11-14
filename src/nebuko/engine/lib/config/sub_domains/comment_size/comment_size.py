"""Comment-size policy for agent answer comments (issue #913).

Single source of truth for how much answer content may be posted on the thread
and across how many comments. Previously the bounds were implicit and drifted:
``prepare_comment.MAX_COMMENT_LENGTH`` truncated the whole body at 60k, while
``build_output`` split at a role-conditional ``60000 if role == "context" else
10000`` — and its part files had no reader, so anything past the first comment
was silently dropped. Owning both knobs here lets the publish seam split a long
answer across comments instead of throwing the tail away, which is what lets the
context agent's ``RELEVANT SNIPPETS`` section carry more data in one pass.

Defaults follow the approved plan: 60,000 chars per comment (a safe margin under
GitHub's 65,536-character hard limit, which no adapter guards) and a soft
ceiling of 4 comments per answer (~240k chars total, 4x today's budget). The
ceiling is a *soft* max: content under it is never padded up to it, and it only
bounds comment noise when an answer genuinely overflows.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

#: Per-comment budget, under GitHub's 65,536-character hard comment limit.
DEFAULT_MAX_COMMENT_CHARS = 60_000
#: Soft ceiling on posted comments per answer (~4 x the 60k per-comment budget).
DEFAULT_MAX_PARTS = 4


class CommentSizePolicy(BaseModel):
    """Tunable contract for how much answer content is posted, and in how many comments."""

    max_comment_chars: int = DEFAULT_MAX_COMMENT_CHARS
    max_parts: int = DEFAULT_MAX_PARTS

    model_config = ConfigDict(frozen=True)

    def resolved_max_comment_chars(self) -> int:
        """A usable per-comment budget; a non-positive value falls back to the default.

        Guarantees every caller receives a positive bound, so a mis-declared
        policy can never configure the splitter to emit zero-length parts.
        """
        if self.max_comment_chars > 0:
            return self.max_comment_chars
        return DEFAULT_MAX_COMMENT_CHARS

    def resolved_max_parts(self) -> int:
        """A usable comment-count ceiling; anything below 1 falls back to the default."""
        if self.max_parts >= 1:
            return self.max_parts
        return DEFAULT_MAX_PARTS
