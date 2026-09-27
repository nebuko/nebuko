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

from openhands.sdk.llm.exceptions import LLMError, LLMServiceUnavailableError

try:
    import litellm
except ImportError:  # pragma: no cover - litellm ships with the SDK
    litellm = None

#: Single source of truth for the recovery command (issue #668).
RESUME_COMMAND = "/workflow resume"  # @stub-protected

#: Raw-text fragments that identify an unreachable endpoint when the typed
#: exception is hidden behind provider wrapping (litellm / vLLM).
_CONNECTIVITY_TOKENS = (
    "cannot connect",
    "connection refused",
    "connect call failed",
    "connection error",
    "name or service not known",
)

_UNREACHABLE_SUMMARY = (
    "> \u26a0\ufe0f **The agent could not reach the LLM API provider.** The "
    "configured endpoint did not accept a connection, so the run could not "
    "start. This is usually a transient provider outage or a "
    "stopped/misconfigured endpoint."
)
_LLM_ERROR_SUMMARY = (
    "> \u26a0\ufe0f **The agent hit an error communicating with the LLM API.** "
    "The provider returned an error response, so the run could not complete."
)
_UNKNOWN_SUMMARY = (
    "> \u26a0\ufe0f **The agent run failed before producing any output.** The "
    "failure was not an LLM connectivity problem \u2014 check the full logs in "
    "the workflow run for details."
)


def _litellm_unreachable_types() -> tuple[type, ...]:
    """litellm exception classes that mean the endpoint could not be reached."""
    if litellm is None:
        return ()
    names = ("APIConnectionError", "InternalServerError")
    candidates = (getattr(litellm, name, None) for name in names)
    return tuple(cls for cls in candidates if isinstance(cls, type))


_LITELLM_UNREACHABLE_TYPES = _litellm_unreachable_types()


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


def _has_connectivity_token(detail: str) -> bool:
    """Whether raw text names a connectivity failure (wrapper-proof)."""
    lowered = detail.lower()
    return any(token in lowered for token in _CONNECTIVITY_TOKENS)


def _is_unreachable_by_type(error: Exception) -> bool:
    """Whether the exception type itself means the endpoint is unreachable."""
    if isinstance(error, LLMServiceUnavailableError):
        return True
    return isinstance(error, _LITELLM_UNREACHABLE_TYPES)


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
    detail = str(error)
    if _is_unreachable_by_type(error) or _has_connectivity_token(detail):
        return RunFailure(
            kind=RunFailureKind.LLM_UNREACHABLE,
            summary=_UNREACHABLE_SUMMARY,
            detail=detail,
        )
    if isinstance(error, LLMError):
        return RunFailure(
            kind=RunFailureKind.LLM_ERROR,
            summary=_LLM_ERROR_SUMMARY,
            detail=detail,
        )
    return RunFailure(
        kind=RunFailureKind.UNKNOWN,
        summary=_UNKNOWN_SUMMARY,
        detail=detail,
    )
