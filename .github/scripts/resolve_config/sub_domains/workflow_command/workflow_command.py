"""Workflow command sub-domain.

Value object + parser for the explicit first-line command grammar::

    /workflow <action> [<resource>] [--flag value]*

Canonical actions (issue #635):
  - ``run``        resource ``workflow`` — start/switch a workflow; ``--name`` required
  - ``resume``     no resource — resume the held step; ``--feedback`` optional
  - ``force-step`` resource ``step`` — jump to a step; ``--name`` required

Malformed input (unknown action, wrong/missing resource, unknown or
duplicate flag, missing required flag) yields ``None`` so routing falls
through to the normal cascade. Old positional forms (``/workflow default``,
``/workflow force-next-step <step>``) deliberately do not parse.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

__all__ = [
    "WorkflowCommand",
    "parse_workflow_command",
    "ACTIONS",
    "RESOURCE_BY_ACTION",
    "FLAGS_BY_ACTION",
]


@dataclass(frozen=True)
class WorkflowCommand:
    """Immutable parsed /workflow <action> <resource> --flag value command."""

    action: str  # "run" | "resume" | "force-step"
    resource: Optional[str]  # "workflow" | "step" | None (resume)
    params: Dict[str, str]  # named flags, e.g. {"name": "spec-driven-impl"}


ACTIONS: Tuple[str, ...] = ("run", "resume", "force-step")

RESOURCE_BY_ACTION: Dict[str, Optional[str]] = {
    "run": "workflow",
    "force-step": "step",
    "resume": None,
}

FLAGS_BY_ACTION: Dict[str, Set[str]] = {
    "run": {"name"},
    "force-step": {"name"},
    "resume": {"feedback"},
}

#: Required flags per action (absent ⇒ malformed ⇒ None).
_REQUIRED_FLAGS: Dict[str, str] = {"run": "name", "force-step": "name"}

#: Flags whose value consumes all tokens up to the next ``--`` token or the
#: end of line (multi-word, e.g. ``--feedback fix the bug``).
_MULTIWORD_FLAGS = {"feedback"}

_WORKFLOW_CMD_RE = re.compile(r"^/workflow\s+(.*)$")


def _parse_flags(tokens: List[str], action: str) -> Optional[Dict[str, str]]:
    """Parse ``--flag value`` pairs into a dict; None on any malformed input."""
    flags = FLAGS_BY_ACTION[action]
    params: Dict[str, str] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--"):
            return None
        flag = token[2:]
        if flag not in flags or flag in params:
            return None
        if flag in _MULTIWORD_FLAGS:
            # Consume everything up to the next "--" token or end of line.
            j = i + 1
            while j < len(tokens) and not tokens[j].startswith("--"):
                j += 1
            value = " ".join(tokens[i + 1 : j])
            if not value:
                return None
            params[flag] = value
            i = j
        else:
            if i + 1 >= len(tokens):
                return None
            params[flag] = tokens[i + 1]
            i += 2
    return params


def parse_workflow_command(trigger_body: str) -> Optional[WorkflowCommand]:
    """First-line /workflow command → WorkflowCommand; None if absent/malformed.

    # @stub-protected
    """
    first_line = trigger_body.split("\n", 1)[0].strip()
    match = _WORKFLOW_CMD_RE.match(first_line)
    if not match:
        return None
    tokens = match.group(1).split()
    if not tokens:
        return None
    action = tokens[0]
    if action not in ACTIONS:
        return None
    i = 1
    expected_resource = RESOURCE_BY_ACTION[action]
    if expected_resource is not None:
        if i >= len(tokens) or tokens[i] != expected_resource:
            return None
        i += 1
    params = _parse_flags(tokens[i:], action)
    if params is None:
        return None
    required = _REQUIRED_FLAGS.get(action)
    if required is not None and required not in params:
        return None
    return WorkflowCommand(action=action, resource=expected_resource, params=params)