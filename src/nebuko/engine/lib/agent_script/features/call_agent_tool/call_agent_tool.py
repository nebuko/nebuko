"""call_agent tool — typed agent-to-agent hand-off for the control plane.

The imperative ``function_call`` section of the legacy
``<!-- AGENT_RUN_CONFIG ... -->`` sentinel named the exact agent to run next
(call_agent). This tool gives that hand-off a *typed* equivalent: the agent
calls ``call_agent`` before ending, and the runtime reads the target agent
off structured data instead of scraping prose (issue #876, PR-2).

The tool records the hand-off in a run-scoped registry, mirroring
:mod:`dispatch_next_tool`; all routing stays in the workflow engine
(``resolve_config``). Recording happens on the tool thread, so the registry
is guarded by a lock.
"""

from collections.abc import Sequence
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal, Optional, Self

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

from nebuko.engine.lib.workflow_state import FunctionCall

if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)


class CallAgentAction(Action):
    """Action for recording an imperative agent-to-agent hand-off."""

    function: Literal["call_agent"] = Field(
        default="call_agent",
        description="The registered function name. Always 'call_agent'.",
    )
    agent_name: str = Field(
        description="The agent to run next (e.g. 'context', 'dev', 'review').",
    )
    task: Optional[str] = Field(
        default=None,
        description="Optional short note describing the work delegated.",
    )
    callback: Optional[str] = Field(
        default=None,
        description=(
            "Optional return address: the agent to run when the callee "
            "finishes without an explicit decision of its own."
        ),
    )
    complexity: Optional[str] = Field(
        default=None,
        description=(
            "'low', 'medium', or 'high'. Omit to inherit the called step's "
            "declared complexity (falling back to 'medium')."
        ),
    )


class CallAgentObservation(Observation):
    """Confirmation returned after the hand-off was recorded."""

    recorded: bool = Field(
        default=False,
        description="Whether the hand-off was accepted and recorded.",
    )
    function_call: Optional[FunctionCall] = Field(
        default=None,
        description="The recorded FunctionCall, when accepted.",
    )

    @property
    def visualize(self) -> Text:
        return Text("Hand-off recorded.", style="bold green")


CALL_AGENT_DESCRIPTION = """Record an imperative agent-to-agent hand-off — you are naming the exact agent to run next.

Parameters:
- function: always 'call_agent' (the only registered function today).
- agent_name: the agent to run next (e.g. 'context', 'dev', 'review'). Required.
- task: optional short note describing the work delegated.
- callback: optional return address — the agent to run when the callee finishes without an explicit decision of its own.
- complexity: 'low' | 'medium' | 'high' for the called agent; omit to inherit the called step's declared complexity (falling back to 'medium').

Use this instead of encoding a hand-off in prose or in an HTML comment: the
engine reads the recorded `function_call` structurally. The hand-off beats
plain workflow continuation and is consumed once by the next run's resolver."""  # noqa: E501


# ── Run-scoped function-call registry ─────────────────────────────────────
# Keys are opaque run ids (conversation id, or the literal "default" key for
# single-run processes and tests). The latest hand-off wins.

_registry_lock = Lock()
_function_call_registry: dict[str, FunctionCall] = {}


def record_function_call(conversation_id: str, call: FunctionCall) -> None:
    """Store the most recent hand-off for a run (thread-safe)."""
    with _registry_lock:
        _function_call_registry[str(conversation_id)] = call
    logger.info(
        f"call_agent recorded for run {conversation_id}: "
        f"agent_name={call.agent_name}, complexity={call.complexity}, "
        f"callback={call.callback}"
    )


def get_recorded_function_call(conversation_id: str) -> Optional[FunctionCall]:
    """Return the latest recorded hand-off for a run, or None if absent."""
    with _registry_lock:
        return _function_call_registry.get(str(conversation_id))


def clear_run(conversation_id: str) -> None:
    """Drop all recorded state for a run (cleanup after output build)."""
    with _registry_lock:
        _function_call_registry.pop(str(conversation_id), None)


def reset_registry() -> None:
    """Clear every run (intended for tests)."""
    with _registry_lock:
        _function_call_registry.clear()


def action_to_function_call(action: CallAgentAction) -> Optional[FunctionCall]:
    """Map a CallAgentAction onto the canonical FunctionCall model.

    Returns None (and logs) if the payload fails FunctionCall validation, so a
    malformed tool call can never poison downstream routing.
    """
    try:
        return FunctionCall(
            function=action.function,
            agent_name=action.agent_name,
            complexity=action.complexity or "",
            callback=action.callback,
        )
    except Exception as e:  # pydantic ValidationError or similar
        logger.warning(f"call_agent payload failed FunctionCall validation: {e}")
        return None


class CallAgentExecutor(ToolExecutor):
    def __init__(self, conversation_id: str = "default") -> None:
        self._conversation_id = conversation_id

    def __call__(
        self,
        action: CallAgentAction,
        conversation: "BaseConversation | None" = None,
    ) -> CallAgentObservation:
        call = action_to_function_call(action)
        if call is None:
            return CallAgentObservation.from_text(
                text=(
                    "Hand-off rejected: invalid parameters. Re-call call_agent "
                    "with a non-empty agent_name."
                ),
                is_error=True,
            )
        record_function_call(self._conversation_id, call)
        return CallAgentObservation.from_text(
            text=(
                f"Hand-off recorded: agent_name={call.agent_name}, "
                f"complexity={call.complexity or '-'}. "
                "You may now finish."
            ),
            recorded=True,
            function_call=call,
        )


class CallAgentTool(ToolDefinition[CallAgentAction, CallAgentObservation]):
    """Typed agent-to-agent hand-off tool (issue #876)."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        conversation_id: str = "default",
        **params: Any,
    ) -> Sequence[Self]:
        """Create a CallAgentTool bound to a run-scoped conversation id.

        Args:
            conv_state: Optional conversation state (unused).
            conversation_id: Registry key for recorded hand-offs. Falls back
                to the live conversation id when available.
            **params: Additional parameters (none supported).
        """
        if params:
            raise ValueError("CallAgentTool doesn't accept parameters")
        if conv_state is not None:
            # ConversationState exposes its id as ``id`` (ConversationID);
            # no ``conversation_id`` attribute exists on it.
            try:
                conversation_id = str(conv_state.id)
            except Exception:
                pass
        return [
            cls(
                description=CALL_AGENT_DESCRIPTION,
                action_type=CallAgentAction,
                observation_type=CallAgentObservation,
                executor=CallAgentExecutor(conversation_id=conversation_id),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
