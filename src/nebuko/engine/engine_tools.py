"""Engine tools - declarative control-plane classification and typed harvest (issue #830).

The agent tool surface has two planes:

* **work tools** (``terminal``, ``file_editor``, ``think``, ...) - the agent's
  operational capability. Their payloads are never harvested by the engine.
* **engine tools** (``finish``, ``dispatch_next``) - the control plane. The
  engine reads their *typed* arguments: ``finish.message`` is the final
  message and ``dispatch_next``'s fields carry the workflow routing decision.

Classifying the control plane is an **engine-internal invariant** applied to
every agent, not user configuration: the engineтЖФagent boundary is a schema, so
the answer can never depend on where an agent happened to place its prose
(the #822 truncation bug). :data:`DEFAULT_ENGINE_TOOLS` is the single frozen
instance; :meth:`EngineToolRegistry.harvest` performs the structural typed-field
read that replaces the former free-text/``Thought:`` heuristics.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from nebuko.engine.finalize import AgentReport, RunTerminator

__all__ = [
    "DEFAULT_ENGINE_TOOLS",
    "EngineToolPayload",
    "EngineToolRegistry",
    "EngineToolSpec",
    "FinalizeToolError",
    "FinalizeToolHandler",
    "PayloadKind",
    "WorkLoopPresence",
]

# ``final_message`` - the tool's argument is the agent's final message (finish);
# ``routing`` / ``handoff`` - payload shapes of the removed ``dispatch_next`` /
# ``call_agent`` tools (read-only legacy compatibility, #1033);
# ``finalize`` - the terminal tool of every step: the report rides in the turn
# text and the tool carries the typed ``turn_outcome`` only.
PayloadKind = Literal["final_message", "routing", "handoff", "finalize"]

# Work-loop role of an engine tool: ``terminator`` ends the run (finish);
# ``hidden`` is a control signal the work loop keeps running past.
WorkLoopPresence = Literal["terminator", "hidden"]


class EngineToolSpec(BaseModel):
    """Declarative description of one control-plane tool."""

    model_config = ConfigDict(frozen=True)

    name: str
    payload_kind: PayloadKind
    # The typed argument names the engine harvests, in declaration order.
    payload_fields: tuple[str, ...]


class EngineToolPayload(BaseModel):
    """A schema-validated harvest of one engine-tool action.

    ``fields`` holds the declared ``payload_fields`` read off the action as
    strings; an absent/``None`` argument becomes ``""`` so a partially-filled
    call still harvests deterministically.
    """

    model_config = ConfigDict(frozen=True)

    tool_name: str
    payload_kind: PayloadKind
    fields: Mapping[str, str]


class EngineToolRegistry(BaseModel):
    """Engine-owned classification of the control plane and its typed payloads.

    One frozen instance is applied uniformly to every agent
    (:data:`DEFAULT_ENGINE_TOOLS`). The registry is injectable so a future
    control plane can be supplied without touching the business logic, but it
    is deliberately *not* per-role configuration: a run must never guess which
    slot holds the deliverable.
    """

    model_config = ConfigDict(frozen=True)

    tools: Mapping[str, EngineToolSpec]

    def harvest(self, tool_name: str, action: object) -> Optional[EngineToolPayload]:
        # @stub-protected: signature defined by architect — do not modify
        """Structurally read an engine-tool action's typed payload.

        Returns ``None`` for a non-engine tool or a missing action, so callers
        fall back deterministically rather than reading arbitrary attributes.
        """
        spec = self.tools.get(tool_name) or _LEGACY_TOOL_SPECS.get(tool_name)
        if spec is None or action is None:
            return None
        fields = {
            field: _field_text(getattr(action, field, ""))
            for field in spec.payload_fields
        }
        return EngineToolPayload(
            tool_name=tool_name, payload_kind=spec.payload_kind, fields=fields
        )

    def finalize_payload(self, action: object) -> Mapping[str, Any]:
        # @stub-protected: signature defined by architect — do not modify
        """The agent's ``turn_outcome`` from a ``finalize`` action (issue #1033).

        The typed ``turn_outcome`` is the canonical slot. A worker that has not
        been migrated yet still sends the flat ``route`` action; its vocabulary
        is the same, so it is read as a fallback rather than dropped.
        """
        if action is None:
            return {}
        sources = [getattr(action, "structured_output", None)]
        state = getattr(action, "__dict__", None)
        if isinstance(state, Mapping):
            sources.append(state)
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            for key in ("turn_outcome", "route", "appreciation"):
                # ``turn_outcome`` first (the canonical typed argument), then
                # the pre-#1033 flat ``route``/top-level shapes a not-yet
                # migrated worker may still send.
                if key == "appreciation":
                    if key in source:
                        return dict(source)
                    break
                nested = source.get(key)
                if isinstance(nested, Mapping):
                    return dict(nested)
                if nested is not None and not isinstance(nested, Mapping):
                    dumped = _as_mapping(nested)
                    if dumped is not None:
                        return dumped
        return {}

    def work_loop_presence(self, tool_name: str) -> WorkLoopPresence:
        # @stub-protected: signature defined by architect — do not modify
        """The tool's role in the work loop: ``terminator`` or ``hidden``.

        ``finish`` is the explicit completion signal (its payload is ignored in
        the work loop); ``finalize`` is the terminal tool of every step;
        ``dispatch_next`` and any unknown tool are ``hidden``.
        """
        spec = self.tools.get(tool_name)
        if spec is not None and spec.payload_kind in ("final_message", "finalize"):
            return "terminator"
        return "hidden"


class FinalizeToolError(Exception):
    """Tool-layer finalize failure; retryable exactly once (fix 7, #973).

    The tool layer surfaces the first extraction/validation failure as one
    retryable error naming the offending field, so the agent can repair its
    answer; a second failure is re-raised unchanged and fails the run loudly.
    """

    field: str = ""
    retryable: bool = True

    def __init__(
        self, message: str, *, field: str = "", retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.field = field
        self.retryable = retryable


class FinalizeToolHandler:
    """Adapts :class:`RunTerminator` to the engine tool layer (issue #973).

    ``handle`` delegates to the terminator's extract тЖТ validate тЖТ render тЖТ post
    gate; the first :class:`SectionExtractionError` / :class:`MarkerInjectionError`
    becomes one retryable :class:`FinalizeToolError` naming the field, and a
    second failure propagates (fail loudly). ``salvage`` harvests the completed
    sections of a truncated turn without ever raising.
    """

    def __init__(self, terminator: "RunTerminator") -> None:
        self.terminator = terminator
        self.retried = False

    def response_schema(self) -> dict[str, Any]:
        """The turn-outcome JSON schema the SDK exposes as ``response_schema``."""
        from nebuko.engine.finalize import FinalizeSchemaBuilder

        return FinalizeSchemaBuilder().as_response_schema()

    def handle(self, route: object, turn_text: str) -> "AgentReport":
        """Gate the terminal emission; retry once, then fail loudly."""
        from nebuko.engine.finalize import (
            MarkerInjectionError,
            SectionExtractionError,
        )

        try:
            return self.terminator.finalize(route, turn_text)
        except (SectionExtractionError, MarkerInjectionError) as exc:
            if self.retried:
                raise
            self.retried = True
            raise FinalizeToolError(
                str(exc), field=_named_field(str(exc)), retryable=True
            ) from exc

    def salvage(self, turn_text: str) -> "AgentReport":
        """Harvest the completed sections of a truncated turn; never raises."""
        return self.terminator.salvage(turn_text)


def _named_field(message: str) -> str:
    """The field name quoted in a section error message (``""`` when absent)."""
    match = re.search(r"'([^']+)'", message)
    return match.group(1) if match else ""


def _as_mapping(value: object) -> Optional[dict[str, Any]]:
    """A mapping view of a pydantic model (or another duck-typed outcome).

    A plain object with the right attributes (a scripted test double, a
    not-yet-migrated worker's namespace) exposes the same fields through
    ``vars``; unset attributes are dropped so the read mirrors
    ``model_dump(exclude_none=True)``.
    """
    if isinstance(value, BaseModel):
        try:
            return value.model_dump(exclude_none=True)
        except Exception:  # noqa: BLE001 - an unreadable action must not crash
            return None
    if isinstance(value, Mapping):
        return dict(value)
    namespace = getattr(value, "__dict__", None)
    if isinstance(namespace, Mapping) and not callable(value):
        return {key: item for key, item in namespace.items() if item is not None}
    return None


def _field_text(value: object) -> str:
    """Coerce a typed action field to its stripped string form (``""`` if unset).

    Boolean flags render as ``"true"`` only when set: an unset flag is
    indistinguishable from ``False``, so a falsy flag stays ``""`` and never
    masks a fallback value (issue #830).
    """
    if isinstance(value, bool):
        return "true" if value else ""
    if value is None:
        return ""
    return str(value).strip()


# The control plane, frozen for every agent in the engine (issue #830).
# ``finalize`` is the *only* terminal tool (issues #1033, #1071): it carries the
# typed ``turn_outcome`` (the verdict plus the optional ``agent_call`` hand-off)
# and the report is its ``report`` argument. ``finish`` is retired as a tool; a
# legacy spec is kept below so recorded pre-migration runs stay readable.
DEFAULT_ENGINE_TOOLS: Final[EngineToolRegistry] = EngineToolRegistry(
    tools={
        # Terminal tool of every step, ``output:`` or not: the typed turn outcome
        # is the payload; the report is the action's own ``report`` argument.
        "finalize": EngineToolSpec(
            name="finalize",
            payload_kind="finalize",
            payload_fields=("turn_outcome",),
        ),
    }
)

#: Payload shapes of the tools removed in #1033. They are not registered (no
#: agent is offered them); the specs only keep a pre-migration run's recorded
#: action readable.
_LEGACY_TOOL_SPECS: Final[Mapping[str, EngineToolSpec]] = {
    # ``finish`` was removed as a tool in issue #1071 (finalize is the only
    # terminal tool); the spec keeps a pre-migration run's recorded action
    # readable, as was done for dispatch_next in #1033.
    "finish": EngineToolSpec(
        name="finish",
        payload_kind="final_message",
        payload_fields=("message",),
    ),
    "dispatch_next": EngineToolSpec(
        name="dispatch_next",
        payload_kind="routing",
        payload_fields=(
            "appreciation",
            "hold_kind",
            "required_agent",
            "round",
            "note",
            "complexity",
            "pin",
        ),
    ),
    "call_agent": EngineToolSpec(
        name="call_agent",
        payload_kind="handoff",
        payload_fields=("function", "agent_name", "callback", "task", "complexity"),
    ),
}
