"""Engine tools — declarative control-plane classification and typed harvest (issue #830).

The agent tool surface has two planes:

* **work tools** (``terminal``, ``file_editor``, ``think``, ...) — the agent's
  operational capability. Their payloads are never harvested by the engine.
* **engine tools** (``finish``, ``dispatch_next``) — the control plane. The
  engine reads their *typed* arguments: ``finish.message`` is the final
  message and ``dispatch_next``'s fields carry the workflow routing decision.

Classifying the control plane is an **engine-internal invariant** applied to
every agent, not user configuration: the engine↔agent boundary is a schema, so
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
    from nebuko.engine.finalize import AgentReport, RouteDecision, RunTerminator

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

# ``final_message`` — the tool's argument is the agent's final message (finish);
# ``routing`` — the tool's arguments carry the workflow routing decision;
# ``handoff`` — the tool's arguments carry an imperative agent-to-agent
# hand-off (call_agent), surfaced under a ``function_call`` key;
# ``finalize`` — the terminal gate of a step that declares an ``output:``
# shape: the report rides in the turn text and the tool carries the route only.
PayloadKind = Literal["final_message", "routing", "handoff", "finalize"]

# Work-loop role of an engine tool: ``terminator`` ends the run (finish);
# ``hidden`` is a control signal the work loop keeps running past (dispatch_next).
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
        spec = self.tools.get(tool_name)
        if spec is None or action is None:
            return None
        fields = {
            field: _field_text(getattr(action, field, ""))
            for field in spec.payload_fields
        }
        return EngineToolPayload(
            tool_name=tool_name, payload_kind=spec.payload_kind, fields=fields
        )

    def work_loop_presence(self, tool_name: str) -> WorkLoopPresence:
        # @stub-protected: signature defined by architect — do not modify
        """The tool's role in the work loop: ``terminator`` or ``hidden``.

        ``finish`` is the explicit completion signal (its payload is ignored in
        the work loop); ``finalize`` is the terminal gate of an output step;
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

    ``handle`` delegates to the terminator's extract → validate → render → post
    gate; the first :class:`SectionExtractionError` / :class:`MarkerInjectionError`
    becomes one retryable :class:`FinalizeToolError` naming the field, and a
    second failure propagates (fail loudly). ``salvage`` harvests the completed
    sections of a truncated turn without ever raising.
    """

    def __init__(self, terminator: "RunTerminator") -> None:
        self.terminator = terminator
        self.retried = False

    def response_schema(self) -> dict[str, Any]:
        """The route-only JSON schema the SDK exposes as ``response_schema``."""
        from nebuko.engine.finalize import FinalizeSchemaBuilder

        return FinalizeSchemaBuilder().as_response_schema()

    def handle(self, route: "RouteDecision", turn_text: str) -> "AgentReport":
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
# ``finish.message`` is the canonical final message; ``dispatch_next``'s typed
# fields are the routing decision. The work loop's ``finish`` is only the
# explicit end signal — the engine-elicited report turn is what fills the
# ``message`` deterministically.
DEFAULT_ENGINE_TOOLS: Final[EngineToolRegistry] = EngineToolRegistry(
    tools={
        "finish": EngineToolSpec(
            name="finish",
            payload_kind="final_message",
            payload_fields=("message",),
        ),
        "dispatch_next": EngineToolSpec(
            name="dispatch_next",
            payload_kind="routing",
            # ``agent``/``pin`` carry the explicit cross-agent override that the
            # prose sentinel used to provide; dropping them here silently
            # reverts a hand-off to the workflow's default next step (#830).
            payload_fields=("status", "branch", "complexity", "reason", "agent", "pin"),
        ),
        # Typed replacement for the sentinel-carried ``function_call``
        # hand-off (issue #876). Its harvested fields are surfaced under a
        # ``function_call`` key; the routing decision itself stays
        # status-only (``RoutingDecision``).
        "call_agent": EngineToolSpec(
            name="call_agent",
            payload_kind="handoff",
            payload_fields=("function", "agent_name", "callback", "task", "complexity"),
        ),
        # Terminal gate of a step that declares an ``output:`` shape (issue
        # #973): the report rides in the turn text as section bodies, so the
        # tool's only argument is the typed route.
        "finalize": EngineToolSpec(
            name="finalize",
            payload_kind="finalize",
            payload_fields=("route",),
        ),
    }
)
