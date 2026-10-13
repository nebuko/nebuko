"""Comment-size policy sub-domain (issue #913)."""

from .comment_size import (
    DEFAULT_MAX_COMMENT_CHARS,
    DEFAULT_MAX_PARTS,
    CommentSizePolicy,
)

__all__ = [
    "DEFAULT_MAX_COMMENT_CHARS",
    "DEFAULT_MAX_PARTS",
    "CommentSizePolicy",
]
