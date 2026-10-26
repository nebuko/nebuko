"""Run outcome model — classification of a supervised agent run (issue #489).

Pure functions + frozen dataclass: the watchdog/watch-outcome logic is kept
out of ``run_agent.py`` so it can be unit-tested without an event loop.
"""

from dataclasses import dataclass
from enum import Enum

from nebuko.engine.lib.agent_script.features.run_agent.run_failure import RunFailure


class OutcomeStatus(str, Enum):
    """High-level outcome of a single agent run."""

    SUCCESS = "success"
    GRACEFULLY_TIMED_OUT = "gracefully_timed_out"
    INTERRUPTED = "interrupted"
    FAILURE = "failure"
    AGENT_CHECKPOINT = "agent_checkpoint"


# SDK ConversationExecutionStatus values (openhands.sdk.conversation.state).
# Compared by value so this module stays decoupled from the SDK enum.
_STATUS_FINISHED = "finished"
_STATUS_ERROR = "error"
_STATUS_STUCK = "stuck"
_STATUS_PAUSED = "paused"


@dataclass(frozen=True)
class RunOutcome:
    """Immutable record of a supervised conversation run.

    Attached to the conversation instance as ``conversation.run_outcome``
    so downstream features (build_output) can classify the run.

    Fields:
        status: High-level outcome of the run.
        soft_timeout_fired: Whether the soft-timeout watchdog tripped.
        wind_down_completed: Whether the wind-down pass finished (only
            meaningful when soft_timeout_fired is True).
        dispatch_recall_count: Number of recall turns injected because the
            run ended without a dispatch_next call (issue #524).
        dispatch_recall_event_offset: Event index captured just before the
            first dispatch-recall pass (None when no recall occurred).
            Answers captured after this offset belong to engine-injected
            turns and are excluded from latest-answer selection (issue #558).
        failure: The modelled cause of a FAILURE run (None otherwise).
            Carries the user-facing summary and the resume command so
            build_output can render a helpful terminal comment (issue #668).
        publication_guard: Result of the publication guard on an approved
            write step: "" (not checked), "clean", "recalled_ok",
            "fallback_pr", "fallback_patch" (workflow-only work delivered as a
            patch, issue #1080) or "fallback_failed".
        publication_detail: What was missing (the ``check_publication``
            reasons), the fallback PR URL, or the fallback error message.
        publication_patch: Where the delivered ``.github/workflows/*`` patch
            lives — the comment URL, or the staged artifact name when the
            patch was too large to inline (issue #1080).
        publication_recall_count: Number of publication-recall turns
            injected because the approved work was never published.
        publication_recall_event_offset: Event index captured just before the
            first publication-recall pass (None when no recall occurred) —
            the same engine-injected boundary as
            ``dispatch_recall_event_offset`` (issue #558).
    """

    status: OutcomeStatus
    soft_timeout_fired: bool = False
    wind_down_completed: bool = False
    dispatch_recall_count: int = 0
    dispatch_recall_event_offset: int | None = None
    failure: RunFailure | None = None  # @stub-protected
    publication_guard: str = ""
    publication_detail: str = ""
    # @stub-protected: signature defined by architect — do not modify
    publication_patch: str = ""
    publication_recall_count: int = 0
    publication_recall_event_offset: int | None = None


def classify_run(status_value: str, soft_timeout_fired: bool) -> OutcomeStatus:
    """Classify a run from its SDK execution status and watchdog flag.

    A ``FINISHED`` status always wins over the flag: if the run completed
    just before the watchdog fired (tiny race window), it is a success and
    no wind-down is needed.

    Args:
        status_value: ``conversation.execution_status.value`` after the run.
        soft_timeout_fired: Whether our watchdog tripped the interrupt.

    Returns:
        The matching OutcomeStatus.
    """
    if status_value == _STATUS_FINISHED:
        return OutcomeStatus.SUCCESS
    if soft_timeout_fired:
        return OutcomeStatus.GRACEFULLY_TIMED_OUT
    if status_value in (_STATUS_ERROR, _STATUS_STUCK):
        return OutcomeStatus.FAILURE
    if status_value == _STATUS_PAUSED:
        return OutcomeStatus.INTERRUPTED
    # Unexpected status (idle/running after the run returned) — treat as
    # success: the run loop terminated on its own.
    return OutcomeStatus.SUCCESS


def status_value(conversation) -> str:
    """Read the SDK execution status value defensively (returns '' if absent)."""
    try:
        return conversation.execution_status.value
    except (AttributeError, ValueError):
        return ""