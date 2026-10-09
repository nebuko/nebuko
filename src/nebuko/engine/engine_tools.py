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

from typing import Final, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DEFAULT_ENGINE_TOOLS",
    "EngineToolPayload",
    "EngineToolRegistry",
    "EngineToolSpec",
    "PayloadKind",
    "WorkLoopPresence",
]

# ``final_message`` — the tool's argument is the agent's final message (finish);
# ``routing`` — the tool's arguments carry the workflow routing decision.
PayloadKind = Literal["final_message", "routing"]

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
        the work loop); ``dispatch_next`` and any unknown tool are ``hidden``.
        """
        spec = self.tools.get(tool_name)
        if spec is not None and spec.payload_kind == "final_message":
            return "terminator"
        return "hidden"


def _field_text(value: object) -> str:
    """Coerce a typed action field to its stripped string form (``""`` if unset)."""
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
            payload_fields=("status", "branch", "complexity", "reason"),
        ),
    }
)
