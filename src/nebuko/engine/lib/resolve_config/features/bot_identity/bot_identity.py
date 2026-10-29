"""Bot identity resolution: the single source of the engine's bot identity.

Spec (plan #1218 step "spec"): centralize the bot identity — login,
display name, and noreply email — in one config-resolvable object so the
trigger parser, commit authors, and docs read a single value instead of
hardcoded "@nebuko" strings.
"""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class BotIdentity:
    """Immutable bot identity resolved from config (or defaults)."""

    login: str
    display_name: str
    email: str

    def is_app_bot(self) -> bool:
        """True when the login is a GitHub App bot (``<app>[bot]``)."""
        raise NotImplementedError

    def mention_token(self) -> str:
        """Return the mentionable token, e.g. ``@nebuko[bot]``."""
        raise NotImplementedError

    def commit_author(self) -> tuple[str, str]:
        """Return the ``(name, email)`` pair used for git commits."""
        raise NotImplementedError


def default_identity() -> BotIdentity:
    """Return the default identity: the GitHub App bot ``nebuko[bot]``."""
    raise NotImplementedError


def from_config(config: Mapping[str, Any]) -> BotIdentity:
    """Resolve the bot identity from the engine's ``bot:`` config block.

    Falls back to :func:`default_identity` when the block or its keys are
    missing.
    """
    raise NotImplementedError
