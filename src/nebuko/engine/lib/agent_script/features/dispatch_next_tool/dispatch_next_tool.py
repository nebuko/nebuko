"""dispatch_next tool — structured factual turn outcome (issues #524, #964).

The agent reports *what happened* (its appreciation of the turn); the engine
derives the route from the workflow topology. There is no navigation intent
("advance"/"loop"/"terminate") in the payload — that vocabulary is gone.

Recording happens on the tool thread, so the run-scoped registry is guarded by
a lock.
"""

from collections.abc import Sequence
from threading import Lock
from typing import TYPE_CHECKING, Any, Optional, Self

from openhands.sdk import get_logger
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)
from pydantic import Field
from rich.text import Text

from workflow_state import RunPrefs, TurnOutcome

if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)


class TurnOutcomeAction(Action):
    """Action recording the agent's factual verdict on the turn it just ended."""

    appreciation: str = Field(
        description=(
            "Your factual verdict on this turn: 'approved' (the work meets "
            "the step's goal), 'rejected' (it does not), or 'hold' (you cannot "
            "decide — the turn needs a human)."
        )
    )
    hold_kind: Optional[str] = Field(
        default=None,
        description=(
            "Only with appreciation='hold': 'awaiting_approval' (a human must "
            "approve before the workflow continues) or 'awaiting_input' (you "
            "need information from the human)."
        ),
    )
    required_agent: Optional[str] = Field(
        default=None,
        description=(
            "Optional agent name that must run at the routed step "
            "(e.g. 'dev'). It never selects the step itself."
        ),
    )
    round: Optional[int] = Field(
        default=None,
        description="Optional review/iteration round number this turn belongs to.",
    )
    note: Optional[str] = Field(
        default=None,
        description="One-sentence justification of the verdict.",
    )
    complexity: Optional[str] = Field(
        default=None,
        description=(
            "'low', 'medium', or 'high' for the next agent. Omit to inherit "
            "the workflow step's declared complexity (defaults to 'medium')."
        ),
    )
    pin: bool = Field(
        default=False,
        description=(
            "When true, pin this step's output comment(s) so their full body "
            "is injected verbatim into downstream agents' context."
        ),
    )


class TurnOutcomeObservation(Observation):
    """Confirmation returned after the turn outcome was recorded."""

    @property
    def visualize(self) -> Text:
        return Text("Turn outcome recorded.", style="bold green")


DISPATCH_NEXT_DESCRIPTION = """Record the factual outcome of this turn. You MUST call this tool as one of your FINAL actions, before finishing.

Parameters:
- appreciation: REQUIRED. Your verdict on this turn — 'approved' (the work meets the step's goal), 'rejected' (it does not), or 'hold' (you cannot decide; a human must).
- hold_kind: only with appreciation='hold' — 'awaiting_approval' or 'awaiting_input'.
- required_agent: optional agent name that must run at the routed step; it never selects the step.
- round: optional review/iteration round number this turn belongs to.
- note: one-sentence justification.
- complexity: 'low' | 'medium' | 'high' for the next agent; omit to inherit the workflow step's declared complexity (defaults to 'medium').
- pin: set true to pin this step's output comment(s) for verbatim injection into downstream context; omit to leave unpinned.

Where the workflow goes next is engine topology, never your choice: the engine
maps your appreciation onto the step's declared ``on_result`` edge.
Do NOT encode the verdict in prose or HTML comments instead of calling this tool.
Deliverable note: the free-form text you write alongside this call (the turn's text)
is captured by the posting pipeline as the agent's answer and posted to the issue —
so include the full run report there if you have not already posted it as a text
answer. The parameters above are factual metadata only; never stuff the report into
`note`."""  # noqa: E501


# ── Run-scoped turn-outcome registry ──────────────────────────────────────
# Keys are opaque run ids (conversation id, or the literal "default" key for
# single-run processes and tests). The latest outcome wins.

_registry_lock = Lock()
_outcome_registry: dict[str, TurnOutcome] = {}


def record_turn_outcome(conversation_id: str, outcome: TurnOutcome) -> None:
    """Store the most recent turn outcome for a run (thread-safe)."""
    with _registry_lock:
        _outcome_registry[str(conversation_id)] = outcome
    logger.info(
        f"dispatch_next recorded for run {conversation_id}: "
        f"status={outcome.status.value}, appreciation={outcome.appreciation}, "
        f"hold_kind={outcome.hold_kind}"
    )


def get_recorded_turn_outcome(conversation_id: str) -> Optional[TurnOutcome]:
    """Return the latest recorded turn outcome for a run, or None if absent."""
    with _registry_lock:
        return _outcome_registry.get(str(conversation_id))


def clear_run(conversation_id: str) -> None:
    """Drop the recorded outcome for a run (cleanup after output build)."""
    with _registry_lock:
        _outcome_registry.pop(str(conversation_id), None)


def reset_registry() -> None:
    """Clear every run (intended for tests)."""
    with _registry_lock:
        _outcome_registry.clear()


def action_to_payload(action: TurnOutcomeAction) -> dict[str, Any]:
    """Map the action onto the agent-supplied half of a turn-outcome payload.

    Only *agent-supplied* facts are returned; the engine-measured ``status``
    axis is added by ``build_output`` (via ``TurnOutcome.from_agent_payload``),
    so the agent can never claim completion on its own.
    """
    payload: dict[str, Any] = {"appreciation": action.appreciation}
    if action.hold_kind:
        payload["hold_kind"] = action.hold_kind
    if action.required_agent:
        payload["required_agent"] = action.required_agent
    if action.round is not None:
        payload["round"] = action.round
    if action.note:
        payload["note"] = action.note
    return payload


def action_to_prefs(action: TurnOutcomeAction) -> RunPrefs:
    """Map the non-routing knobs of the action onto :class:`RunPrefs`."""
    return RunPrefs(complexity=action.complexity or "", pin=action.pin)


class DispatchNextExecutor(ToolExecutor):
    def __init__(self, conversation_id: str = "default") -> None:
        self._conversation_id = conversation_id

    def __call__(
        self,
        action: TurnOutcomeAction,
        conversation: "BaseConversation | None" = None,
    ) -> TurnOutcomeObservation:
        try:
            outcome = TurnOutcome.from_agent_payload(
                action_to_payload(action), observed=None
            )
        except Exception as e:
            # Loud, actionable failure (issue #864): a silent drop here is
            # indistinguishable from the agent never having called the tool.
            logger.error(
                f"dispatch_next rejected for run {self._conversation_id}: {e}"
            )
            return TurnOutcomeObservation.from_text(text=str(e), is_error=True)
        record_turn_outcome(self._conversation_id, outcome)
        return TurnOutcomeObservation.from_text(
            text=(
                f"Turn outcome recorded: appreciation={outcome.appreciation}, "
                f"hold_kind={outcome.hold_kind}. "
                "You may now finish."
            )
        )


class DispatchNextTool(ToolDefinition[TurnOutcomeAction, TurnOutcomeObservation]):
    """Structured workflow turn-outcome tool (issues #524, #964)."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        conversation_id: str = "default",
        **params: Any,
    ) -> Sequence[Self]:
        """Create a DispatchNextTool bound to a run-scoped conversation id.

        Args:
            conv_state: Optional conversation state (unused).
            conversation_id: Registry key for recorded outcomes. Falls back
                to the live conversation id when available.
            **params: Additional parameters (none supported).
        """
        if params:
            raise ValueError("DispatchNextTool doesn't accept parameters")
        if conv_state is not None:
            # ConversationState exposes its id as ``id`` (ConversationID);
            # no ``conversation_id`` attribute exists on it.
            try:
                conversation_id = str(conv_state.id)
            except Exception:
                pass
        return [
            cls(
                description=DISPATCH_NEXT_DESCRIPTION,
                action_type=TurnOutcomeAction,
                observation_type=TurnOutcomeObservation,
                executor=DispatchNextExecutor(conversation_id=conversation_id),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
