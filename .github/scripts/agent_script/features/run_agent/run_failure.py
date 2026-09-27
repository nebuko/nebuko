"""Run failure model — human-readable cause + recovery hint (issue #668).

When the configured LLM endpoint is unreachable, ``conversation.arun()``
raises a transport error that the supervised run loop currently discards:
the run is classified as ``OutcomeStatus.FAILURE`` but the exception itself
never reaches the output layer, so the terminal comment is opaque and gives
the human no recovery path.

This module models that discarded error as an immutable ``RunFailure``:
it names the cause for humans and carries the command that resumes the
workflow once the provider is reachable again.
"""

from dataclasses import dataclass
from enum import Enum

#: Single source of truth for the recovery command (issue #668).
RESUME_COMMAND = "/workflow resume"  # @stub-protected


class RunFailureKind(str, Enum):  # @stub-protected
    """High-level cause of a failed run."""

    LLM_UNREACHABLE = "llm_unreachable"
    LLM_ERROR = "llm_error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RunFailure:  # @stub-protected
    """Immutable description of why a run failed and how to resume it.

    Fields:
        kind: High-level cause classification.
        summary: User-facing Markdown explaining the failure.
        detail: Raw exception text, rendered collapsed in the comment.
        resume_command: Command the human posts to resume the workflow.
    """

    kind: RunFailureKind
    summary: str
    detail: str
    resume_command: str = RESUME_COMMAND


def classify_failure(error: Exception) -> RunFailure:  # @stub-protected
    """Classify a run error into a ``RunFailure`` with a human-readable summary.

    Detection prefers a type match on the SDK-normalized
    ``LLMServiceUnavailableError`` (connectivity / 5xx), falling back to a
    connectivity-token heuristic on the raw text, so litellm wrapping and
    version differences never silently degrade the message.

    Args:
        error: The exception captured from the supervised run.

    Returns:
        A ``RunFailure`` describing the cause and the resume command.
    """
    raise NotImplementedError
