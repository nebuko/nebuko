"""finalize tool — the single terminal tool of every step (issue #1033).

The report is the turn's markdown text; an ``output:``
declaration only decides how the engine extracts and renders it. The tool
carries the one typed ``turn_outcome``: the agent's verdict plus the optional
``agent_call`` hand-off. ``dispatch_next`` and ``call_agent`` no longer exist
as tools — their fields live here.

The tool records the outcome in the run-scoped turn-outcome registry and, when
the outcome names an ``agent_call``, the hand-off in the run-scoped
function-call registry, so the engine's existing routing/state machinery reads
them unchanged. Recording happens on the tool thread, so both registries are
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

from agent_script.features.call_agent_tool.call_agent_tool import (
    record_function_call,
)
from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
    record_turn_outcome,
)
from nebuko.engine.finalize import (
    AWAITING_INPUT,
    FinalizeSchemaBuilder,
    TurnOutcomePayload,
    turn_outcome_from_action,
)
from nebuko.engine.report_text import closing_turn_report_text
from workflow_state import RunPrefs, TurnOutcome

if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState

FINALIZE_TOOL_NAME = "finalize"

#: Refusal for a finalize call whose turn carries no report text (issue #1038).
NO_REPORT_TEXT = (
    "finalize rejected: this turn has no report text. The report is the markdown "
    "text of the turn that calls finalize, and nothing is posted without it. Write "
    "the full report as the text of this turn, then call finalize again. A "
    "'report posted' note is not a report."
)


class FinalizeAction(Action):
    """Action for the terminal ``finalize`` tool.

    The report rides in the turn text, so the action declares no field of its
    own: the ``turn_outcome`` arrives through the tool's ``response_schema``
    and lands in ``action.structured_output``.
    """


class FinalizeObservation(Observation):
    """Confirmation returned after the turn outcome was recorded."""

    @property
    def visualize(self) -> Text:
        return Text("Finalized.", style="bold green")


FINALIZE_DESCRIPTION = f"""End this step: record your turn outcome for the workflow.

The report itself is the markdown text of this turn. Write the report first
(when the prompt shows section markers, put the section bodies between the
exact markers), then call this tool with your turn outcome. Do not put the
report in the tool arguments.

Parameters:
- turn_outcome.appreciation: REQUIRED. Your verdict on this turn —
  'approved' (the work meets the step's goal), 'rejected' (it does not), or
  'hold' (you are blocked and need information from a human).
- turn_outcome.hold_kind: REQUIRED with appreciation='hold', and only then:
  '{AWAITING_INPUT}'. This asks a human for information and recalls this same
  step with the answer. You cannot request an approval gate — gates are
  engine-owned.
- turn_outcome.agent_call: optional hand-off to another agent, as
  {{agent_name, task}}. The called agent receives only 'task' (plus its
  standard system context) and does not see this conversation. Its approved
  or rejected verdict returns its full report to you in a new turn; its hold
  recalls the called agent, not you.
- turn_outcome.round: optional review/iteration round number this turn
  belongs to.
- turn_outcome.note: one-sentence justification of the verdict.
- turn_outcome.complexity: 'low' | 'medium' | 'high' for the next agent; omit
  to inherit the workflow step's declared complexity (defaults to 'medium').
- turn_outcome.pin: set true to pin this step's output comment(s) for verbatim
  injection into downstream context; omit to leave unpinned.

Where the workflow goes next is engine topology, never your choice: the engine
maps your appreciation onto the step's declared ``on_result`` edge."""  # noqa: E501


def outcome_to_turn_outcome(outcome: TurnOutcomePayload) -> TurnOutcome:
    """Map a :class:`TurnOutcomePayload` onto the factual ``TurnOutcome``.

    The finalize turn is engine-elicited and only runs after a completed agent
    turn, so the engine-measured status is ``completed`` (issue #964).
    """
    return outcome.to_turn_outcome()


def outcome_to_prefs(outcome: TurnOutcomePayload) -> RunPrefs:
    """Map the non-routing knobs of the outcome onto :class:`RunPrefs`.

    The tier and the pin are run preferences, not routing facts, so they
    travel beside the outcome.
    """
    return RunPrefs(complexity=outcome.complexity or "", pin=outcome.pin)


#: Backwards-compatible aliases (pre-#1033 names of the two mappers).
route_to_turn_outcome = outcome_to_turn_outcome
route_to_prefs = outcome_to_prefs


class FinalizeExecutor(ToolExecutor):
    def __init__(self, conversation_id: str = "default") -> None:
        self._conversation_id = conversation_id

    def __call__(
        self,
        action: FinalizeAction,
        conversation: "BaseConversation | None" = None,
    ) -> FinalizeObservation:
        try:
            outcome = turn_outcome_from_action(action)
            if outcome is None:
                raise ValueError(
                    "missing or invalid 'turn_outcome': pass appreciation "
                    "('approved' | 'rejected' | 'hold') and the optional fields"
                )
            outcome.validate_runtime()
        except Exception as exc:  # noqa: BLE001 - surface every violation to the agent
            return FinalizeObservation.from_text(
                text=(
                    f"finalize rejected: {exc}. Fix the turn_outcome and call "
                    "finalize again."
                ),
                is_error=True,
            )
        # The report is the turn's markdown text, not an argument. A call made
        # with no report text in its turn is refused here, while the agent can
        # still act on it: an accepted call would record the outcome, and the
        # agent would then acknowledge "posted" with nothing to post (#1038).
        if conversation is not None and not closing_turn_report_text(conversation):
            return FinalizeObservation.from_text(text=NO_REPORT_TEXT, is_error=True)
        record_turn_outcome(
            self._conversation_id,
            outcome_to_turn_outcome(outcome),
            outcome_to_prefs(outcome),
        )
        handoff = outcome.to_function_call()
        if handoff is not None:
            record_function_call(self._conversation_id, handoff)
        return FinalizeObservation.from_text(
            text=(
                f"Turn outcome recorded: appreciation={outcome.appreciation}, "
                f"hold_kind={outcome.hold_kind or '-'}"
                + (
                    f", agent_call={handoff.agent_name}"
                    if handoff is not None
                    else ""
                )
                + "."
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
