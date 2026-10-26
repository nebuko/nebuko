"""Workflow command sub-domain.

Value object + parser for the explicit first-line command grammar::

    /nebuko <action> [<resource>] [--flag value]*

Canonical actions (issue #635):
  - ``run``        resource ``workflow`` — start/switch a workflow; ``--name``
                   optional (omitting it runs the repo's default workflow —
                   issue #1094), ``--step`` optional (enter at a dedicated step,
                   issue #789; a dotted ``a.b`` selector descends into mounted
                   sub-workflows, issue #822)
  - ``resume``     no resource — resume the held step; ``--feedback`` optional
  - ``force-step`` resource ``step`` — jump to a step; ``--name`` required
                   (dotted ``a.b`` selectors accepted, issue #822)
  - ``delete``     resource ``comments`` — batch-delete issue comments (issue #644);
                   ``--issue`` and ``--from`` optional, both numeric
  - ``clean``      resource ``comments`` — local maintenance command (issue #679);
                   alias of ``delete``, so ``--issue``/``--from`` apply too;
                   runs its script and starts no agent
  - ``update``     resource ``circuit-breaker`` — set the per-issue
                   circuit-breaker call limit (issue #839); ``--value`` required
                   and numeric, human-only, agent-free
  - ``approve``    resource ``gate`` — release a human gate as approved
                   (issue #648); ``--feedback`` optional
  - ``reject``     resource ``gate`` — release a human gate as rejected
                   (issue #648); ``--feedback`` optional

Only the ``/nebuko``-prefixed form is recognized as a command; a bare
``clean comments`` line is not one (issue #679).

The command line is the grammar line for every action, but a **terminal**
``--feedback`` flag additionally spans the rest of the comment body, so its
value may be written on the following lines (verbatim, newlines preserved,
issue #919). Any other flag stays single-line.

Malformed input (unknown action, wrong/missing resource, unknown or
duplicate flag, missing required flag) yields ``None`` from
:func:`parse_workflow_command`; :func:`parse_command` is the typed sibling
that keeps the failure category and a user-facing usage hint (issue #804).
A malformed ``/nebuko`` invocation is a *command attempt*: the resolver
answers it with that usage hint and never consults the workflow-continuation
cascade (issue #867), so an active dispatch can never absorb it. Old
positional forms (``/nebuko default``, ``/nebuko force-next-step <step>``)
deliberately do not parse.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Set, Tuple

from config import brand

__all__ = [
    "WorkflowCommand",
    "CommandGrammar",
    "CommandSource",
    "CommandParseResult",
    "CommandDetail",
    "ParseStatus",
    "parse_command",
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
    """Immutable parsed /nebuko <action> <resource> --flag value command."""

    action: str  # "run" | "resume" | "force-step" | "delete" | "clean" | "update"
    resource: Optional[str]  # "workflow" | "step" | "comments" | "circuit-breaker" | None
    params: Dict[str, str]  # named flags, e.g. {"name": "spec-driven-impl"}


#: Parse status: ``"absent"`` (no command intent), ``"valid"`` (a well-formed
#: command), ``"malformed"`` (a slash/command attempt that broke the grammar).
ParseStatus = Literal["absent", "valid", "malformed"]

#: Machine-stable failure category carried by a :class:`CommandParseResult`
#: (issue #804). ``"none"`` pairs with ``status == "absent"`` and ``"ok"`` with
#: ``status == "valid"``; every other token names a distinct grammar violation.
CommandDetail = Literal[
    "none",
    "ok",
    "unknown_command",
    "unknown_action",
    "unknown_resource",
    "unknown_flag",
    "duplicate_flag",
    "bare_flag_value",
    "invalid_flag_value",
    "missing_required_flag",
]


@dataclass(frozen=True)
class CommandParseResult:
    """Typed outcome of parsing a comment's first line as a /nebuko command.

    Sibling of :func:`parse_workflow_command` that never collapses the several
    malformed-invocation modes into one ``None`` (issue #804): ``detail`` keeps
    a machine-stable category and ``usage`` carries a user-facing correction so
    the engine can post actionable feedback without invoking an LLM.
    """

    status: ParseStatus  # @stub-protected
    command: Optional[WorkflowCommand]  # @stub-protected  (set iff status == "valid")
    detail: CommandDetail  # @stub-protected
    usage: Optional[str]  # @stub-protected  (correction hint, iff malformed)


ACTIONS: Tuple[str, ...] = (
    "run",
    "resume",
    "force-step",
    "delete",
    "update",
    "approve",
    "reject",
)

RESOURCE_BY_ACTION: Dict[str, Optional[str]] = {
    "run": "workflow",
    "force-step": "step",
    "resume": None,
    "delete": "comments",
    "update": "circuit-breaker",
    "approve": "gate",
    "reject": "gate",
}

FLAGS_BY_ACTION: Dict[str, Set[str]] = {
    "run": {"name", "step"},
    "force-step": {"name"},
    "resume": {"feedback"},
    "delete": {"issue", "from"},
    "update": {"value"},
    "approve": {"feedback"},
    "reject": {"feedback"},
}

#: Classification outcome for a single issue comment (issues #679/#867).
#: ``"command_attempt"`` is a malformed ``/nebuko`` invocation: recognized as
#: an intent, never as a command, and answered with a usage hint by the
#: resolver without consulting the workflow-continuation cascade.
CommentKind = Literal["command", "command_attempt", "agent_trigger", "none"]

#: Command class: ``"workflow"`` commands bootstrap the agent pipeline,
#: ``"local"`` commands run only their deterministic script (issue #679),
#: ``"engine"`` commands are handled inline by ``resolve_config`` and are
#: **never** dispatched to an agent (issue #648).
CommandClass = Literal["workflow", "local", "engine"]

#: Single source of truth for recognized commands (issue #679). Maps a command
#: name to its command class. Only the ``/nebuko``-prefixed form is
#: recognized as a command — the real-world trigger payload is
#: ``/nebuko clean comments`` (issue #590), never a bare ``clean comments``.
#: Any name listed here is a first-class short-circuit: it classifies as a
#: command and can never fall through to the agent-trigger path. A new command
#: only needs to be registered here.
COMMAND_REGISTRY: Dict[str, CommandClass] = {
    "run": "workflow",
    "resume": "workflow",
    "force-step": "workflow",
    "delete": "local",
    "clean": "local",
    "update": "local",
    "approve": "engine",
    "reject": "engine",
}

#: Required flags per action (absent ⇒ malformed ⇒ None).
_REQUIRED_FLAGS: Dict[str, str] = {
    "force-step": "name",
    "update": "value",
}

#: Flags whose value consumes all tokens up to the next ``--`` token or the
#: end of line (multi-word, e.g. ``--feedback fix the bug``).
_MULTIWORD_FLAGS = {"feedback"}

#: Per-action flags whose value must be all digits (issue #644). A
#: non-numeric value makes the command malformed (⇒ None).
_NUMERIC_FLAGS_BY_ACTION: Dict[str, Set[str]] = {
    "delete": {"issue", "from"},
    "update": {"value"},
}

#: Accepted spellings for a resource token, normalized to the canonical
#: hyphenated form (issue #839). The request's ``circuit_breaker`` spelling is
#: an alias of the canonical ``circuit-breaker`` resource.
_RESOURCE_ALIASES: Dict[str, str] = {"circuit_breaker": "circuit-breaker"}

#: Registry-local commands that reuse a core action's grammar (issue #679).
#: ``clean`` is a registry alias of ``delete comments`` — same resource and
#: flags — so its invocation is validated exactly like ``delete``. Without this
#: a malformed ``/nebuko clean`` prefix would over-match and be treated as a
#: recognized command.
_GRAMMAR_ALIASES: Dict[str, str] = {"clean": "delete"}

# @stub-protected: signature defined by architect — do not modify
@dataclass(frozen=True)
class CommandGrammar:
    """Single source of truth for the slash-command prefix (issue #749).

    Owns the prefix that opens a command line and builds the anchor regex that
    :func:`parse_workflow_command` matches against. ``primary`` is sourced from
    ``BrandIdentity.command_name`` so the command name is defined exactly once.
    """

    primary: str  # @stub-protected

    # @stub-protected: signature defined by architect — do not modify
    def pattern(self) -> re.Pattern[str]:
        """Anchor regex matching ``/{primary} <rest>`` at the start of a line."""
        return re.compile(rf"^/{self.primary}\s+(.*)$")


#: Process-wide grammar and compiled anchor, sourced from the brand
#: identity so the slash-command prefix is defined exactly once (issue #749).
#: The GitHub Actions trigger gates cannot import Python and hard-code the
#: same prefix in the workflow YAML.
COMMAND_GRAMMAR = CommandGrammar(primary=brand().command_name)

_COMMAND_RE = COMMAND_GRAMMAR.pattern()


@dataclass(frozen=True)
class CommandSource:
    """A command body split into its grammar line and its continuation.

    The command grammar is first-line-only; the terminal multiword flag
    (``--feedback``) additionally spans the comment body (issue #919). This
    value object models ``body = command_line + continuation`` in one place so
    :func:`parse_command` and :func:`_attempted_action` share a single split
    boundary and the ``--feedback`` span rule has one source of truth.

    The continuation excludes engine bookkeeping (the appended workflow-state
    ``<details>`` block and bot marker lines) so only human-authored text is
    ever captured as feedback. The command line itself is left verbatim, so a
    bot body whose first line is a marker is still not a command.

    Attributes:
        command_line: First line of the body, stripped.
        continuation: Human-authored text after the first newline with internal
            newlines preserved; stripped; ``""`` when there is none.
    """

    command_line: str
    continuation: str

    @classmethod
    def from_body(cls, body: str) -> "CommandSource":
        """Split ``body`` into its stripped grammar line and continuation."""
        command_line, _, rest = body.partition("\n")
        return cls(
            command_line=command_line.strip(),
            continuation=_strip_engine_bookkeeping(rest).strip(),
        )


def _strip_engine_bookkeeping(text: str) -> str:
    """Drop the engine-appended workflow-state block and bot markers.

    Trigger bodies carry the state ``<details>`` block and routing markers that
    the engine appended; they are not command input (issue #919).
    """
    if not text:
        return text
    from workflow_state.features.comment.to_git_comment import strip_state_section

    return strip_state_section(text)


def _parse_flags(
    tokens: List[str], action: str, continuation: str = ""
) -> Tuple[Optional[Dict[str, str]], Optional[CommandDetail]]:
    """Parse ``--flag value`` pairs into a dict.

    Returns ``(params, None)`` on success, or ``(None, detail)`` naming the
    first grammar violation so :func:`parse_command` can report it.
    """
    flags = FLAGS_BY_ACTION[action]
    params: Dict[str, str] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--"):
            return None, "unknown_flag"
        flag = token[2:]
        if flag not in flags:
            return None, "unknown_flag"
        if flag in params:
            return None, "duplicate_flag"
        if flag in _MULTIWORD_FLAGS:
            # Consume everything up to the next "--" token or end of line.
            j = i + 1
            while j < len(tokens) and not tokens[j].startswith("--"):
                j += 1
            value = " ".join(tokens[i + 1 : j])
            # A terminal multiword flag (no ``--`` token follows on the command
            # line) spans the rest of the body: append the continuation lines
            # verbatim, newlines preserved (issue #919). A non-terminal flag
            # keeps single-line semantics and ignores the continuation.
            if j >= len(tokens) and continuation:
                value = f"{value}\n{continuation}" if value else continuation
            if not value:
                return None, "bare_flag_value"
            params[flag] = value
            i = j
        else:
            if i + 1 >= len(tokens):
                return None, "bare_flag_value"
            params[flag] = tokens[i + 1]
            i += 2
    return params, None


#: Per-action usage hints surfaced to a human whose command is malformed
#: (issue #804). Keyed by the invoked action (so the ``clean`` alias keeps its
#: own spelling) with the grammar action as fallback.
_USAGE_BY_ACTION: Dict[str, str] = {
    "run": "/nebuko run workflow --name <workflow> [--step <step[.step...]>]",
    "resume": "/nebuko resume [--feedback <text>]",
    "force-step": "/nebuko force-step step --name <step[.step...]>",
    "delete": "/nebuko delete comments [--issue <number>] [--from <comment-id>]",
    "clean": "/nebuko clean comments [--issue <number>] [--from <comment-id>]",
    "update": "/nebuko update circuit-breaker --value <n>",
    "approve": "/nebuko approve gate [--feedback <text>]",
    "reject": "/nebuko reject gate [--feedback <text>]",
}

#: Usage shown when the action is missing or unrecognized.
_USAGE_UNKNOWN_ACTION = (
    "unrecognized command; expected one of: "
    "/nebuko run workflow --name <workflow>, /nebuko resume, "
    "/nebuko force-step step --name <step>, /nebuko delete comments, "
    "/nebuko update circuit-breaker --value <n>, /nebuko approve gate, "
    "/nebuko reject gate"
)

#: Usage shown for a leading ``/`` that is not a ``/nebuko`` invocation.
_USAGE_UNKNOWN_COMMAND = (
    "unrecognized / command; see the Human Commands table in docs/features.md "
    "(e.g. /nebuko run workflow --name <workflow>, /nebuko resume)"
)


def _usage_for(action: Optional[str], grammar_action: Optional[str]) -> str:
    """Best available correction hint for a malformed invocation."""
    return (
        _USAGE_BY_ACTION.get(action or "")
        or _USAGE_BY_ACTION.get(grammar_action or "")
        or _USAGE_UNKNOWN_ACTION
    )


def _malformed_result(
    detail: CommandDetail,
    action: Optional[str] = None,
    grammar_action: Optional[str] = None,
) -> CommandParseResult:
    """A ``malformed`` parse result carrying its detail + usage hint."""
    return CommandParseResult(
        status="malformed",
        command=None,
        detail=detail,
        usage=_usage_for(action, grammar_action),
    )


def parse_command(trigger_body: str) -> CommandParseResult:
    """First line → typed :class:`CommandParseResult`; never collapses failures.

    The command line is the grammar line; the terminal ``--feedback`` flag
    additionally spans the rest of the body (issue #919).

    # @stub-protected
    """
    source = CommandSource.from_body(trigger_body)
    first_line = source.command_line
    match = _COMMAND_RE.match(first_line)
    if not match:
        # A leading "/" that is not a well-formed /nebuko prefix is an unknown
        # slash intent; anything else carries no command at all (issue #804).
        if first_line == f"/{COMMAND_GRAMMAR.primary}":
            # Bare prefix with no body — a /nebuko attempt, not a foreign slash.
            return _malformed_result("unknown_action")
        if first_line.startswith("/"):
            return CommandParseResult(
                status="malformed",
                command=None,
                detail="unknown_command",
                usage=_USAGE_UNKNOWN_COMMAND,
            )
        return CommandParseResult(
            status="absent", command=None, detail="none", usage=None
        )
    tokens = match.group(1).split()
    if not tokens:
        return _malformed_result("unknown_action")
    action = tokens[0]
    # Registry-local commands (``clean``) reuse a core action's grammar, so
    # their resource/flag validation is identical to that action's (issue #679).
    grammar_action = _GRAMMAR_ALIASES.get(action, action)
    if grammar_action not in ACTIONS:
        return _malformed_result("unknown_action", action, grammar_action)
    i = 1
    expected_resource = RESOURCE_BY_ACTION[grammar_action]
    if expected_resource is not None:
        if i >= len(tokens):
            return _malformed_result("unknown_resource", action, grammar_action)
        # Normalize alias spellings (e.g. ``circuit_breaker``) to the canonical
        # resource token before validating (issue #839).
        resource_token = _RESOURCE_ALIASES.get(tokens[i], tokens[i])
        if resource_token != expected_resource:
            return _malformed_result("unknown_resource", action, grammar_action)
        i += 1
    params, detail = _parse_flags(tokens[i:], grammar_action, source.continuation)
    if params is None:
        return _malformed_result(detail or "unknown_flag", action, grammar_action)
    if not _numeric_flags_valid(params, grammar_action):
        return _malformed_result("invalid_flag_value", action, grammar_action)
    required = _REQUIRED_FLAGS.get(grammar_action)
    if required is not None and required not in params:
        return _malformed_result("missing_required_flag", action, grammar_action)
    return CommandParseResult(
        status="valid",
        command=WorkflowCommand(
            action=action, resource=expected_resource, params=params
        ),
        detail="ok",
        usage=None,
    )


def parse_workflow_command(trigger_body: str) -> Optional[WorkflowCommand]:
    """First-line /nebuko command → WorkflowCommand; None if absent/malformed.

    Compatibility wrapper over :func:`parse_command` so existing consumers
    (``classify_comment``, ``build_trigger_commands``, the resolver facade)
    keep their signature and ``None``-on-malformed contract.

    # @stub-protected
    """
    return parse_command(trigger_body).command


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
    """Immutable outcome of classifying a single issue comment (issues #679/#867).

    Downstream routing consumes this instead of re-deriving intent from the
    raw text. A recognized command is a first-class short-circuit that can
    never reach the agent-trigger path; a malformed ``/nebuko`` invocation is a
    distinct ``"command_attempt"`` so the resolver can answer it with the
    grammar's usage hint without consulting the workflow-continuation cascade.

    Attributes:
        kind: ``"command"`` for a recognized command; ``"command_attempt"``
            for a malformed ``/nebuko`` invocation (recognized intent, invalid
            grammar); ``"agent_trigger"`` when the comment carries an explicit
            agent trigger (human ``@@mention`` or CI machine token);
            ``"none"`` otherwise.
        command: Normalised command name when ``kind == "command"``
            (e.g. ``"clean"``); for ``"command_attempt"`` the recognized verb
            when the action itself was valid (e.g. ``"clean"``), else ``None``.
        raw: Original comment body, kept for logging/diagnostics.
        usage: Per-verb correction hint for ``"command_attempt"`` (aggregate
            help for an unknown action); ``None`` otherwise.
        detail: Machine-stable failure category for ``"command_attempt"``
            (mirrors :data:`CommandDetail`); ``None`` otherwise.
    """

    kind: CommentKind  # @stub-protected
    command: Optional[str]  # @stub-protected
    raw: str  # @stub-protected
    usage: Optional[str] = None  # @stub-protected
    detail: Optional[str] = None  # @stub-protected


#: Human ``@@mention`` agent triggers. Mirrors the marker grammar owned by
#: ``features/parse_trigger``; kept local so this domain module has no
#: dependency on the application layer (issue #679).
_MENTION_RE = re.compile(
    r"@@(?:architect|context|dev|review|po)"
)

#: CI machine-trigger tokens (mirrors ``features/parse_trigger``).
_MACHINE_TRIGGER_RE = re.compile(
    r"<!--\s*bot-trigger\b"
)


def _has_agent_trigger(body: str) -> bool:
    """True when ``body`` carries an explicit agent trigger (issue #679)."""
    return bool(_MENTION_RE.search(body) or _MACHINE_TRIGGER_RE.search(body))


def _attempted_action(body: str) -> Optional[str]:
    """The recognized verb of a ``/nebuko`` line, or ``None`` if absent/unknown.

    Carries the attempted action on a ``command_attempt`` intent even when the
    flags are invalid (issue #867); a malformed *action* has no verb. Matching
    mirrors :func:`parse_command`: first line, anchored prefix, first token.
    """
    first_line = CommandSource.from_body(body).command_line
    match = _COMMAND_RE.match(first_line)
    if not match:
        return None
    tokens = match.group(1).split()
    if not tokens or tokens[0] not in COMMAND_REGISTRY:
        return None
    return tokens[0]


# @stub-protected: signature defined by architect — do not modify
def classify_comment(body: str) -> CommentIntent:
    """Classify an issue comment into command / command-attempt / trigger / none.

    A comment is a ``"command"`` only when it is a **well-formed** registered
    command (including ``/nebuko clean comments``). A malformed ``/nebuko``
    invocation — unknown action, wrong/missing resource, unknown flag — is a
    ``"command_attempt"`` (issue #867): the grammar alone decides it, so the
    intent carries the usage hint and failure category for a terminal answer
    that never consults the workflow-continuation cascade. A leading ``/`` that
    is not a ``/nebuko`` attempt falls through unchanged. Otherwise a body
    carrying an explicit agent trigger (human ``@@mention`` or CI machine token)
    is an ``"agent_trigger"``; anything else is ``"none"``.

    Only the ``/nebuko``-prefixed form is recognized as a command — the
    real-world trigger payload for issue #679 is ``/nebuko clean comments``
    (issue #590), not a bare ``clean comments``. Matching is on the command
    form at the start of the (trimmed) first line, never a substring search,
    so prose that merely mentions "clean comments" is not misclassified.

    Args:
        body: Raw comment body.

    Returns:
        A frozen :class:`CommentIntent`.
    """
    parse = parse_command(body)
    if (
        parse.status == "valid"
        and parse.command is not None
        and parse.command.action in COMMAND_REGISTRY
    ):
        return CommentIntent(kind="command", command=parse.command.action, raw=body)
    if parse.status == "malformed" and parse.detail != "unknown_command":
        return CommentIntent(
            kind="command_attempt",
            command=_attempted_action(body),
            raw=body,
            usage=parse.usage,
            detail=parse.detail,
        )
    if _has_agent_trigger(body):
        return CommentIntent(kind="agent_trigger", command=None, raw=body)
    return CommentIntent(kind="none", command=None, raw=body)
