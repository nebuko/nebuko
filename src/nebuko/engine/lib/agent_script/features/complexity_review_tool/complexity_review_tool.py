"""complexity_review tool — structured complexity-tier review request.

Lets an agent escalate/de-escalate a run's complexity with *evidence* rather
than by silently editing the dispatch sentinel. The tool only records the
request in a run-scoped registry; the single decision-maker
(:class:`~complexity_governor.ComplexityGovernor`) adjudicates it at the next
invocation boundary, where the resolved policy and live evidence are available.

Recording happens on the tool thread, so the registry is guarded by a lock.
The canonical :class:`ComplexityReviewRequest`/``EvidenceBundle`` models live in
``complexity_governor`` (SDK-free) so ``workflow_state`` can persist requests
without importing the agent runtime.
"""

from collections.abc import Sequence
from threading import Lock
from typing import TYPE_CHECKING, Any, Optional, Self

from complexity_governor.complexity_governor import ComplexityReviewRequest
from complexity_governor.evidence import EvidenceBundle
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

if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)


class ComplexityReviewAction(Action):
    """Action for requesting a complexity-tier review."""

    evidence: EvidenceBundle = Field(
        description=(
            "Collected evidence justifying the review. Must populate at least "
            "one family: llm (metrics), human (feedback/change-requests), or "
            "context (issue linkage/reopen)."
        )
    )
    feeling: str = Field(
        description=(
            "Mandatory non-blank description of the observed signal: a loop, a "
            "weird design, or any strange signal that suggests the tier is wrong."
        )
    )
    suggested_tier: Optional[str] = Field(
        default=None,
        description=(
            "Advisory only: 'low', 'medium', or 'high'. The governor decides the "
            "final delta; this is an input, not a command."
        ),
    )


class ComplexityReviewObservation(Observation):
    """Confirmation returned after the review request was recorded."""

    @property
    def visualize(self) -> Text:
        return Text("Complexity review recorded.", style="bold green")


COMPLEXITY_REVIEW_DESCRIPTION = """Request a review of the run's complexity tier, backed by evidence.

Call this when the observed work no longer matches the tier you are running at —
the task turned out markedly heavier (or lighter) than the tier assumed.

Parameters:
- evidence: mandatory. Populate at least one family: llm (metric fields), human (feedback_rounds / changes_requested / approvals), context (linked_issues / linked_recent_issues / reopened).
- feeling: mandatory, non-blank. Describe the concrete signal (loop, weird design, strange signal).
- suggested_tier: optional advisory hint ('low' | 'medium' | 'high'); it is an input, not a command.

The governor adjudicates your request against the resolved policy and live evidence at the next boundary; the final tier is clamped by the policy's max_auto_delta."""  # noqa: E501


# ── Run-scoped review-request registry ────────────────────────────────────
# Keys are opaque run ids (conversation id, or the literal "default" key for
# single-run processes and tests). The latest request wins.

_registry_lock = Lock()
_review_registry: dict[str, ComplexityReviewRequest] = {}


def record_review_request(
    conversation_id: str, request: ComplexityReviewRequest
) -> None:
    """Store the most recent review request for a run (thread-safe)."""
    with _registry_lock:
        _review_registry[str(conversation_id)] = request
    logger.info(
        f"complexity_review recorded for run {conversation_id}: "
        f"suggested_tier={request.suggested_tier}"
    )


def get_recorded_review_request(
    conversation_id: str,
) -> Optional[ComplexityReviewRequest]:
    """Return the latest recorded review request for a run, or None."""
    with _registry_lock:
        return _review_registry.get(str(conversation_id))


def clear_complexity_review_run(conversation_id: str) -> None:
    """Drop all recorded state for a run (cleanup after output build)."""
    with _registry_lock:
        _review_registry.pop(str(conversation_id), None)


def reset_complexity_review_registry() -> None:
    """Clear every run (intended for tests)."""
    with _registry_lock:
        _review_registry.clear()


def action_to_request(action: "ComplexityReviewAction") -> Optional[ComplexityReviewRequest]:
    """Map a ComplexityReviewAction onto the canonical request model.

    Returns None (and logs) when the payload is invalid — an unpopulated
    evidence bundle or a blank ``feeling`` — so a malformed tool call can never
    poison downstream adjudication.
    """
    from validate_output import validate_complexity_review

    verdict = validate_complexity_review(action)
    if not verdict.is_valid:
        logger.warning(f"complexity_review payload failed validation: {verdict.errors}")
        return None
    return ComplexityReviewRequest(
        evidence=action.evidence,
        feeling=action.feeling,
        suggested_tier=action.suggested_tier,
    )


class ComplexityReviewExecutor(ToolExecutor):
    def __init__(self, conversation_id: str = "default") -> None:
        self._conversation_id = conversation_id

    def __call__(
        self,
        action: "ComplexityReviewAction",
        conversation: "BaseConversation | None" = None,
    ) -> ComplexityReviewObservation:
        request = action_to_request(action)
        if request is None:
            return ComplexityReviewObservation.from_text(
                text=(
                    "Complexity review rejected: invalid payload. Provide a valid "
                    "evidence bundle and a non-blank feeling, then re-call."
                ),
                is_error=True,
            )
        record_review_request(self._conversation_id, request)
        return ComplexityReviewObservation.from_text(
            text=(
                "Complexity review recorded. The governor will adjudicate it at "
                "the next boundary; you may now finish."
            )
        )


class ComplexityReviewTool(
    ToolDefinition[ComplexityReviewAction, ComplexityReviewObservation]
):
    """Structured complexity-tier review tool (issue #869)."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        conversation_id: str = "default",
        **params: Any,
    ) -> Sequence[Self]:
        """Create a ComplexityReviewTool bound to a run-scoped conversation id."""
        if params:
            raise ValueError("ComplexityReviewTool doesn't accept parameters")
        if conv_state is not None:
            try:
                conversation_id = str(conv_state.id)
            except Exception:
                pass
        return [
            cls(
                description=COMPLEXITY_REVIEW_DESCRIPTION,
                action_type=ComplexityReviewAction,
                observation_type=ComplexityReviewObservation,
                executor=ComplexityReviewExecutor(conversation_id=conversation_id),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
