"""dispatch_next tool — structured control-plane signal for workflow routing.

Replaces reliance on the free-text ``<!-- AGENT_RUN_CONFIG ... -->`` HTML
sentinel (issue #524). Agents call this tool *before* ending; the runtime can
then verify programmatically whether a dispatch decision was made — a boolean
on structured data, immune to long-context format drops.

The tool only records the decision in a run-scoped registry; all routing
stays in the workflow engine (``resolve_config``). Recording happens on the
tool thread, so the registry is guarded by a lock.
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

from workflow_state import Dispatch

if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)


class DispatchNextAction(Action):
    """Action for recording the workflow's next-step decision."""

    status: str = Field(
        description=(
            "Workflow outcome: 'advance' (proceed to next step/agent), "
            "'loop' (re-run the current step), or 'hold' (wait for a human)."
        )
    )
    agent: Optional[str] = Field(
        default=None,
        description=(
            "Optional explicit target agent name (e.g. 'ctx', 'dev'). "
            "If omitted, the workflow engine resolves the next agent from "
            "the workflow definition."
        ),
    )
    complexity: str = Field(
        default="medium",
        description="'low', 'medium', or 'high'. Defaults to 'medium'.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Free-text justification of the dispatch decision.",
    )


class DispatchNextObservation(Observation):
    """Confirmation returned after the dispatch decision was recorded."""

    @property
    def visualize(self) -> Text:
        return Text("Dispatch recorded.", style="bold green")


DISPATCH_NEXT_DESCRIPTION = """Record the workflow's next-step decision. You MUST call this tool as one of your FINAL actions, before finishing.

Parameters:
- status: 'advance' (route to the next step/agent), 'loop' (repeat the current step), or 'hold' (wait for human input).
- agent: optional explicit target agent name; omit to let the workflow engine decide from the workflow definition.
- complexity: 'low' | 'medium' | 'high' for the next agent.
- reason: one-sentence justification.

Do NOT encode the dispatch decision in prose or HTML comments instead of calling this tool.
Deliverable note: the free-form text you write alongside this call (the turn's text)
is captured by the posting pipeline as the agent's answer and posted to the issue —
so include the full run report there if you have not already posted it as a text
answer. The parameters above are routing metadata only; never stuff the report into
`reason`."""  # noqa: E501


# ── Run-scoped dispatch registry ──────────────────────────────────────────
# Keys are opaque run ids (conversation id, or the literal "default" key for
# single-run processes and tests). The latest decision wins.

_registry_lock = Lock()
_dispatch_registry: dict[str, Dispatch] = {}


def record_dispatch(conversation_id: str, dispatch: Dispatch) -> None:
    """Store the most recent dispatch decision for a run (thread-safe)."""
    with _registry_lock:
        _dispatch_registry[str(conversation_id)] = dispatch
    logger.info(
        f"dispatch_next recorded for run {conversation_id}: "
        f"status={dispatch.status}, task_hint={dispatch.task_hint}, "
        f"complexity={dispatch.complexity}"
    )


def get_recorded_dispatch(conversation_id: str) -> Optional[Dispatch]:
    """Return the latest recorded dispatch for a run, or None if absent."""
    with _registry_lock:
        return _dispatch_registry.get(str(conversation_id))


def clear_run(conversation_id: str) -> None:
    """Drop all recorded state for a run (cleanup after output build)."""
    with _registry_lock:
        _dispatch_registry.pop(str(conversation_id), None)


def reset_registry() -> None:
    """Clear every run (intended for tests)."""
    with _registry_lock:
        _dispatch_registry.clear()


def action_to_dispatch(action: DispatchNextAction) -> Optional[Dispatch]:
    """Map a DispatchNextAction onto the canonical Dispatch model.

    Returns None (and logs) if the payload fails Dispatch validation, so a
    malformed tool call can never poison downstream routing.
    """
    try:
        return Dispatch(
            status=action.status,
            task_hint=action.agent,
            complexity=action.complexity,
            reason=action.reason,
        )
    except Exception as e:  # pydantic ValidationError or similar
        logger.warning(f"dispatch_next payload failed Dispatch validation: {e}")
        return None


class DispatchNextExecutor(ToolExecutor):
    def __init__(self, conversation_id: str = "default") -> None:
        self._conversation_id = conversation_id

    def __call__(
        self,
        action: DispatchNextAction,
        conversation: "BaseConversation | None" = None,
    ) -> DispatchNextObservation:
        dispatch = action_to_dispatch(action)
        if dispatch is None:
            return DispatchNextObservation.from_text(
                text=(
                    "Dispatch rejected: invalid parameters. Re-call dispatch_next "
                    "with a valid status (advance|loop|hold)."
                ),
                is_error=True,
            )
        record_dispatch(self._conversation_id, dispatch)
        return DispatchNextObservation.from_text(
            text=(
                f"Dispatch recorded: status={dispatch.status}, "
                f"task_hint={dispatch.task_hint}, "
                f"complexity={dispatch.complexity}. "
                "You may now finish."
            )
        )


class DispatchNextTool(ToolDefinition[DispatchNextAction, DispatchNextObservation]):
    """Structured workflow-dispatch tool (issue #524)."""

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
            conversation_id: Registry key for recorded dispatches. Falls back
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
                action_type=DispatchNextAction,
                observation_type=DispatchNextObservation,
                executor=DispatchNextExecutor(conversation_id=conversation_id),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
