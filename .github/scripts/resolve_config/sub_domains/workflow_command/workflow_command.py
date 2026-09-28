"""Workflow command sub-domain.

Value object + parser for the explicit first-line command grammar::

    /workflow <action> [<resource>] [--flag value]*

Canonical actions (issue #635):
  - ``run``        resource ``workflow`` — start/switch a workflow; ``--name`` required
  - ``resume``     no resource — resume the held step; ``--feedback`` optional
  - ``force-step`` resource ``step`` — jump to a step; ``--name`` required
  - ``delete``     resource ``comments`` — batch-delete issue comments (issue #644);
                   ``--issue`` and ``--from`` optional, both numeric
  - ``clean``      resource ``comments`` — local maintenance command (issue #679);
                   runs its script and starts no agent

Only the ``/workflow``-prefixed form is recognized as a command; a bare
``clean comments`` line is not one (issue #679).

Malformed input (unknown action, wrong/missing resource, unknown or
duplicate flag, missing required flag) yields ``None`` so routing falls
through to the normal cascade. Old positional forms (``/workflow default``,
``/workflow force-next-step <step>``) deliberately do not parse.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Set, Tuple

__all__ = [
    "WorkflowCommand",
    "parse_workflow_command",
    "ACTIONS",
    "RESOURCE_BY_ACTION",
    "FLAGS_BY_ACTION",
    "COMMAND_REGISTRY",
    "CommentIntent",
    "CommentKind",
    "CommandClass",
    "classify_comment",
]


@dataclass(frozen=True)
class WorkflowCommand:
    """Immutable parsed /workflow <action> <resource> --flag value command."""

    action: str  # "run" | "resume" | "force-step" | "delete" | "clean"
    resource: Optional[str]  # "workflow" | "step" | "comments" | None (resume)
    params: Dict[str, str]  # named flags, e.g. {"name": "spec-driven-impl"}


ACTIONS: Tuple[str, ...] = ("run", "resume", "force-step", "delete")

RESOURCE_BY_ACTION: Dict[str, Optional[str]] = {
    "run": "workflow",
    "force-step": "step",
    "resume": None,
    "delete": "comments",
}

FLAGS_BY_ACTION: Dict[str, Set[str]] = {
    "run": {"name"},
    "force-step": {"name"},
    "resume": {"feedback"},
    "delete": {"issue"},
}

#: Classification outcome for a single issue comment (issue #679).
CommentKind = Literal["command", "agent_trigger", "none"]

#: Command class: ``"workflow"`` commands bootstrap the agent pipeline,
#: ``"local"`` commands run only their deterministic script (issue #679).
CommandClass = Literal["workflow", "local"]

#: Single source of truth for recognized commands (issue #679). Maps a command
#: name to its command class. Only the ``/workflow``-prefixed form is
#: recognized as a command — the real-world trigger payload is
#: ``/workflow clean comments`` (issue #590), never a bare ``clean comments``.
#: Any name listed here is a first-class short-circuit: it classifies as a
#: command and can never fall through to the agent-trigger path. A new command
#: only needs to be registered here.
COMMAND_REGISTRY: Dict[str, CommandClass] = {
    "run": "workflow",
    "resume": "workflow",
    "force-step": "workflow",
    "delete": "local",
    "clean": "local",
}

#: Required flags per action (absent ⇒ malformed ⇒ None).
_REQUIRED_FLAGS: Dict[str, str] = {"run": "name", "force-step": "name"}

#: Flags whose value consumes all tokens up to the next ``--`` token or the
#: end of line (multi-word, e.g. ``--feedback fix the bug``).
_MULTIWORD_FLAGS = {"feedback"}

#: Per-action flags whose value must be all digits (issue #644). A
#: non-numeric value makes the command malformed (⇒ None).
_NUMERIC_FLAGS_BY_ACTION: Dict[str, Set[str]] = {
    "delete": {"issue"},
}

#: Registry-local commands that reuse a core action's grammar (issue #679).
#: ``clean`` is a registry alias of ``delete comments`` — same resource and
#: flags — so its invocation is validated exactly like ``delete``. Without this
#: a malformed ``/workflow clean`` prefix would over-match and be treated as a
#: recognized command.
_GRAMMAR_ALIASES: Dict[str, str] = {"clean": "delete"}

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
    # Registry-local commands (``clean``) reuse a core action's grammar, so
    # their resource/flag validation is identical to that action's (issue #679).
    grammar_action = _GRAMMAR_ALIASES.get(action, action)
    if grammar_action not in ACTIONS:
        return None
    i = 1
    expected_resource = RESOURCE_BY_ACTION[grammar_action]
    if expected_resource is not None:
        if i >= len(tokens) or tokens[i] != expected_resource:
            return None
        i += 1
    params = _parse_flags(tokens[i:], grammar_action)
    if params is None:
        return None
    if not _numeric_flags_valid(params, grammar_action):
        return None
    required = _REQUIRED_FLAGS.get(grammar_action)
    if required is not None and required not in params:
        return None
    return WorkflowCommand(action=action, resource=expected_resource, params=params)


def _is_ascii_digits(value: str) -> bool:
    """True for non-empty ASCII-digit strings.

    ``str.isdigit`` also accepts non-ASCII digits (e.g. ``'²'``, ``'١٢٣'``)
    which ``int()`` then rejects; genuine numeric flags must be ASCII so the
    downstream ``int()`` conversion can never crash (issue #644 review).
    """
    return value.isascii() and value.isdigit()


def _numeric_flags_valid(params: Dict[str, str], action: str) -> bool:
    """True unless a numeric-flag value for ``action`` is not all digits."""
    numeric_flags = _NUMERIC_FLAGS_BY_ACTION.get(action, set())
    return all(
        _is_ascii_digits(value)
        for flag, value in params.items()
        if flag in numeric_flags
    )


# @stub-protected: signature defined by architect — do not modify
@dataclass(frozen=True)
class CommentIntent:
    """Immutable outcome of classifying a single issue comment (issue #679).

    Downstream routing consumes this instead of re-deriving intent from the
    raw text, so a recognized command is a first-class short-circuit that can
    never reach the agent-trigger path.

    Attributes:
        kind: ``"command"`` for a recognized command; ``"agent_trigger"``
            when the comment carries an explicit agent trigger (human
            ``@@mention`` or CI machine token); ``"none"`` otherwise.
        command: Normalised command name when ``kind == "command"``
            (e.g. ``"clean"``), else ``None``.
        raw: Original comment body, kept for logging/diagnostics.
    """

    kind: CommentKind  # @stub-protected
    command: Optional[str]  # @stub-protected
    raw: str  # @stub-protected


#: Human ``@@mention`` agent triggers. Mirrors the marker grammar owned by
#: ``features/parse_trigger``; kept local so this domain module has no
#: dependency on the application layer (issue #679).
_MENTION_RE = re.compile(
    r"@@(?:issue-reviewer|genius|ctx|dev|review|issue|summarize)"
)

#: CI machine-trigger tokens (mirrors ``features/parse_trigger``).
_MACHINE_TRIGGER_RE = re.compile(
    r"<!--\s*bot-(?:subtask-complete|summarizer-trigger|trigger)\b"
)


def _has_agent_trigger(body: str) -> bool:
    """True when ``body`` carries an explicit agent trigger (issue #679)."""
    return bool(_MENTION_RE.search(body) or _MACHINE_TRIGGER_RE.search(body))


# @stub-protected: signature defined by architect — do not modify
def classify_comment(body: str) -> CommentIntent:
    """Classify an issue comment into command / agent-trigger / none.

    Delegates to :func:`parse_workflow_command`, so a comment is a ``"command"``
    only when it is a **well-formed** registered command (including
    ``/workflow clean comments``): malformed invocations — unknown action,
    wrong/missing resource, unknown flag — parse to ``None`` and fall through to
    ``"none"`` rather than over-matching. A recognized command must
    short-circuit the agent-trigger path. Otherwise a body carrying an explicit
    agent trigger (human ``@@mention`` or CI machine token) is an
    ``"agent_trigger"``; anything else is ``"none"``.

    Only the ``/workflow``-prefixed form is recognized as a command — the
    real-world trigger payload for issue #679 is ``/workflow clean comments``
    (issue #590), not a bare ``clean comments``. Matching is on the command
    form at the start of the (trimmed) first line, never a substring search,
    so prose that merely mentions "clean comments" is not misclassified.

    Args:
        body: Raw comment body.

    Returns:
        A frozen :class:`CommentIntent`.
    """
    wf_cmd = parse_workflow_command(body)
    if wf_cmd is not None and wf_cmd.action in COMMAND_REGISTRY:
        return CommentIntent(kind="command", command=wf_cmd.action, raw=body)
    if _has_agent_trigger(body):
        return CommentIntent(kind="agent_trigger", command=None, raw=body)
    return CommentIntent(kind="none", command=None, raw=body)
