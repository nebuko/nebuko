"""Author identity sub-domain package."""

from .author_identity import (
    BOT_LOGIN_DENYLIST,
    BOT_LOGIN_SUFFIXES,
    AuthorIdentity,
    build_author_identity,
)

__all__ = [
    "AuthorIdentity",
    "build_author_identity",
    "BOT_LOGIN_DENYLIST",
    "BOT_LOGIN_SUFFIXES",
]
