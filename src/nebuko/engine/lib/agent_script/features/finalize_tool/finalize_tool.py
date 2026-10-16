"""finalize tool — the terminal gate of a step that declares an ``output:`` shape.

Issue #973 ends a run with exactly two artifacts: the report (the turn's
markdown text, whose section bodies the engine renders into the posted comment)
and the routing decision. This tool carries the second one: a tiny, fixed
``route`` argument whose ``response_schema`` is the route only, so a long
markdown payload can never truncate the routing decision.

The tool records the route in the run-scoped turn-outcome registry (the same
one ``dispatch_next`` uses), so the engine's existing routing/state machinery
reads it unchanged. Recording happens on the tool thread, so the registry is
guarded by a lock.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Self

from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)
from rich.text import Text

from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
    record_turn_outcome,
)
from agent_script.features.run_agent.run_outcome import OutcomeStatus
from nebuko.engine.finalize import (
    FinalizeSchemaBuilder,
    RouteDecision,
    route_from_action,
)
from workflow_state import RunPrefs, TurnOutcome

if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState

FINALIZE_TOOL_NAME = "finalize"


class FinalizeAction(Action):
    """Action for the terminal finalize gate.

    The report rides in the turn text, so the action declares no field of its
    own: the ``route`` arrives through the tool's ``response_schema`` and lands
    in ``action.structured_output``.
    """


class FinalizeObservation(Observation):
    """Confirmation returned after the route was recorded."""

    @property
    def visualize(self) -> Text:
        return Text("Finalized.", style="bold green")


FINALIZE_DESCRIPTION = """End this step: record the routing decision for the workflow.

The report itself is the markdown text of this turn — write the declared
section bodies between the exact markers the prompt shows, then call this tool
with your routing decision. Do not put the report in the tool arguments.

Parameters:
- route.appreciation: REQUIRED. Your verdict on this turn — 'approved' (the
  work meets the step's goal), 'rejected' (it does not), or 'hold' (you cannot
  decide; a human must).
- route.hold_kind: only with appreciation='hold' — 'awaiting_approval' or
  'awaiting_input'.
- route.required_agent: optional agent name that must run at the routed step;
  it never selects the step.
- route.round: optional review/iteration round number this turn belongs to.
- route.note: one-sentence justification of the verdict.
- route.complexity: 'low' | 'medium' | 'high' for the next agent; omit to
  inherit the workflow step's declared complexity (defaults to 'medium').
- route.pin: set true to pin this step's output comment(s) for verbatim
  injection into downstream context; omit to leave unpinned.

Where the workflow goes next is engine topology, never your choice: the engine
maps your appreciation onto the step's declared ``on_result`` edge."""  # noqa: E501


def route_to_turn_outcome(route: RouteDecision) -> TurnOutcome:
    """Map a :class:`RouteDecision` onto the factual ``TurnOutcome`` model.

    The finalize turn is engine-elicited and only runs after a completed agent
    turn, so the engine-measured status is ``success`` (issue #964).
    """
    return TurnOutcome.from_agent_payload(
        {
            "appreciation": route.appreciation,
            "hold_kind": route.hold_kind,
            "required_agent": route.required_agent,
            "round": route.round,
            "note": route.note,
        },
        observed=OutcomeStatus.SUCCESS,
    )


def route_to_prefs(route: RouteDecision) -> RunPrefs:
    """Map the non-routing knobs of the route onto :class:`RunPrefs`.

    Mirrors ``dispatch_next``'s ``action_to_prefs``: the tier and the pin are
    run preferences, not routing facts, so they travel beside the outcome.
    """
    return RunPrefs(complexity=route.complexity or "", pin=route.pin)


class FinalizeExecutor(ToolExecutor):
    def __init__(self, conversation_id: str = "default") -> None:
        self._conversation_id = conversation_id

    def __call__(
        self,
        action: FinalizeAction,
        conversation: "BaseConversation | None" = None,
    ) -> FinalizeObservation:
        route = route_from_action(action)
        if route is None:
            return FinalizeObservation.from_text(
                text=(
                    "finalize rejected: the 'route' argument is missing or "
                    "invalid. Re-call finalize with a valid route."
                ),
                is_error=True,
            )
        record_turn_outcome(
            self._conversation_id,
            route_to_turn_outcome(route),
            route_to_prefs(route),
        )
        return FinalizeObservation.from_text(
            text=(
                f"Route recorded: appreciation={route.appreciation}, "
                f"hold_kind={route.hold_kind or '-'}. You may now finish."
            )
        )


class FinalizeTool(ToolDefinition[FinalizeAction, FinalizeObservation]):
    """Terminal finalize gate for a step that declares an ``output:`` shape."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        conversation_id: str = "default",
        **params: Any,
    ) -> Sequence[Self]:
        """Create a FinalizeTool bound to a run-scoped conversation id.

        Args:
            conv_state: Optional conversation state (unused).
            conversation_id: Registry key for the recorded route. Falls back to
                the live conversation id when available.
            **params: Additional parameters (none supported).
        """
        if params:
            raise ValueError("FinalizeTool doesn't accept parameters")
        if conv_state is not None:
            try:
                conversation_id = str(conv_state.id)
            except Exception:
                pass
        tool = cls(
            description=FINALIZE_DESCRIPTION,
            action_type=FinalizeAction,
            observation_type=FinalizeObservation,
            executor=FinalizeExecutor(conversation_id=conversation_id),
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        return [tool.set_response_schema(FinalizeSchemaBuilder().as_response_schema())]
