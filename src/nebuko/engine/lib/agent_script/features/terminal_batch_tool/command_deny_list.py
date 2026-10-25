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

#: The deny message returned verbatim to the agent for a matched statement.
PUSH_DENY_MESSAGE = (
    "git push is disabled for agents: the engine commits leftovers, pushes "
    "the branch and opens the pull request after your run. Commit your work "
    "locally instead."
)

#: Matches a single ``git push`` statement (one shell segment):
#: optional leading whitespace, optional ``VAR=value`` environment
#: assignments, git's global options, then ``push`` as the subcommand —
#: git's global options all precede the subcommand, so ``push`` must be the
#: first non-option token. A mention inside another command (``echo git
#: push``, ``grep push``) or as an argument of another subcommand
#: (``git log --oneline push``) does not match.
#: Git global options that take a separate value token (e.g. ``git -C repo
#: push``): the value must be consumed with the option so the subcommand is
#: still the first non-option token.
PUSH_PATTERN = (
    r"\s*"
    r"(?:[A-Za-z_][A-Za-z_0-9]*=\S*\s+)*"
    r"git(?:\s+(?:-{1,2}\S+|-c|-C|--git-dir|--work-tree|--namespace"
    r"|--exec-path|--super-prefix)(?:\s+[^\s-]\S*)?)*\s+push(?:\s+\S+)*\s*"
)

#: Shell operators that chain statements inside one ``commands`` entry.
#: Quoted text is never split (``git commit -m 'a; git push'`` is one
#: statement whose message happens to contain a mention).
_CHAIN_OPERATORS = re.compile(r"&&|\|\||[;|&]")


def split_statements(statement: str) -> list[str]:
    """Split a ``commands`` entry on shell operators, honoring quotes."""
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in statement:
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif _CHAIN_OPERATORS.match(char):
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


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
        """Return the first rule matching any segment of ``statement``.

        A ``commands`` entry may chain statements with shell operators
        (``&&``, ``||``, ``;``, ``|``, ``&``); each segment is checked so a
        chained ``git push`` cannot slip past a deny rule.
        """
        for rule in self.rules:
            if any(
                rule.pattern.fullmatch(segment) for segment in split_statements(statement)
            ):
                return DenyResult(
                    rule_name=rule.name, statement=statement, message=rule.message
                )
        return None


# @stub-protected: signature defined by architect — do not modify
def push_deny_list() -> CommandDenyList:
    """The built-in deny list: one rule, ``git-push``."""
    return CommandDenyList(
        [
            DenyRule(
                name="git-push",
                pattern=re.compile(PUSH_PATTERN),
                message=PUSH_DENY_MESSAGE,
            )
        ]
    )
