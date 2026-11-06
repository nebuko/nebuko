"""Author identity sub-domain package."""

from .author_identity import (
    BOT_LOGIN_DENYLIST,
    BOT_LOGIN_SUFFIXES,
    AuthorIdentity,
    build_author_identity,
    is_bot_login,
)

__all__ = [
    "AuthorIdentity",
    "build_author_identity",
    "BOT_LOGIN_DENYLIST",
    "BOT_LOGIN_SUFFIXES",
    "is_bot_login",
]
