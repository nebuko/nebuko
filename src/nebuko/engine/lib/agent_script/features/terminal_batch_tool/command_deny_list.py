"""Command deny-list: engine-owned statement blocking for agent terminals.

The engine owns the publish pipeline (see ``docs/git-flow.md``): agents commit,
the engine pushes and opens the pull request. This module holds the ordered
deny rules consulted by ``terminal_batch_tool`` before each statement is
replayed into the SDK ``TerminalExecutor`` — a matched statement is never
executed; the agent receives the rule's explanation instead.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class DenyRule:
    """One deny rule: a compiled pattern and the message it explains."""

    name: str  # e.g. "git-push"
    pattern: re.Pattern[str]  # compiled, matches the whole statement
    message: str  # returned to the agent verbatim


@dataclass(frozen=True)
class DenyResult:
    """What a matched rule reports back to the agent."""

    rule_name: str
    statement: str  # the matched statement, verbatim
    message: str


class CommandDenyList:
    """Ordered statement deny rules; first match wins."""

    def __init__(self, rules: Sequence[DenyRule]) -> None:
        self.rules = tuple(rules)

    # @stub-protected: signature defined by architect — do not modify
    def check(self, statement: str) -> DenyResult | None:
        """Return the first rule matching ``statement``, else ``None``."""
        raise NotImplementedError


# @stub-protected: signature defined by architect — do not modify
def push_deny_list() -> CommandDenyList:
    """The built-in deny list: one rule, ``git-push``."""
    raise NotImplementedError
