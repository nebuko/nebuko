"""Run failure model — human-readable cause + recovery hint (issue #668).

When the configured LLM endpoint is unreachable, ``conversation.arun()``
raises a transport error that the supervised run loop currently discards:
the run is classified as ``OutcomeStatus.FAILURE`` but the exception itself
never reaches the output layer, so the terminal comment is opaque and gives
the human no recovery path.

This module models that discarded error as an immutable ``RunFailure``:
it names the cause for humans and carries the command that resumes the
workflow once the provider is reachable again.

Provider failures (connectivity, 5xx/429 once the SDK's retries are exhausted,
and 401/403/404 configuration rejections) are terminal for the run (issue
#1322): the agent layer raises :class:`ProviderFailure` instead of synthesizing
a comment, so the engine posts the real cause once and never advances the
workflow. Request-specific errors (context too long, content rejected) keep
their existing handling.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from openhands.sdk.llm.exceptions import (
    LLMContentPolicyViolationError,
    LLMContextWindowExceedError,
    LLMContextWindowTooSmallError,
    LLMError,
    LLMServiceUnavailableError,
)

try:
    import litellm
except ImportError:  # pragma: no cover - litellm ships with the SDK
    litellm = None

#: Single source of truth for the recovery command (issue #668).
RESUME_COMMAND = "/nebuko resume"  # @stub-protected

#: Raw-text fragments that identify an unreachable endpoint when the typed
#: exception is hidden behind provider wrapping (litellm / vLLM).
_CONNECTIVITY_TOKENS = (
    "cannot connect",
    "connection refused",
    "connect call failed",
    "connection error",
    "name or service not known",
    "timed out",
    "timeout",
    "service unavailable",
    "rate limit",
    "too many requests",
)

#: Raw-text fragments that identify a refused *configuration* (bad key, bad
#: URL or unknown model). Retrying cannot help until it is fixed (issue #1322).
#: Deliberately spelled out: a bare ``404`` would also match a port number.
_CONFIG_TOKENS = (
    "error code: 401",
    "error code: 403",
    "error code: 404",
    "401 unauthorized",
    "403 forbidden",
    "404 not found",
    "notfounderror",
    "authenticationerror",
    "permissiondeniederror",
    "invalid api key",
    "incorrect api key",
    "model_not_found",
)

_UNREACHABLE_SUMMARY = (
    "> \u26a0\ufe0f **The agent could not reach the LLM API provider.** The "
    "configured endpoint did not accept a connection, so the run could not "
    "start. This is usually a transient provider outage or a "
    "stopped/misconfigured endpoint."
)
_CONFIG_SUMMARY = (
    "> \u26a0\ufe0f **The LLM provider rejected the agent's request.** The "
    "configured API key, base URL or model was refused (HTTP 401/403/404). "
    "Retrying won't help until the provider configuration is fixed."
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


def _litellm_types(*names: str) -> tuple[type, ...]:
    """The litellm exception classes named in ``names`` that this build has."""
    if litellm is None:
        return ()
    candidates = (getattr(litellm, name, None) for name in names)
    return tuple(cls for cls in candidates if isinstance(cls, type))


_LITELLM_UNREACHABLE_TYPES = _litellm_types(
    "APIConnectionError",
    "InternalServerError",
    "ServiceUnavailableError",
    "RateLimitError",
    "Timeout",
)
_LITELLM_CONFIG_TYPES = _litellm_types(
    "AuthenticationError", "PermissionDeniedError", "NotFoundError"
)
#: Deterministic request errors keep their existing handling (issue #1322).
_SDK_REQUEST_TYPES: tuple[type, ...] = (
    LLMContextWindowExceedError,
    LLMContextWindowTooSmallError,
    LLMContentPolicyViolationError,
)
_LITELLM_REQUEST_TYPES = _litellm_types(
    "ContextWindowExceededError", "ContentPolicyViolationError"
)


class RunFailureKind(str, Enum):  # @stub-protected
    """High-level cause of a failed run."""

    LLM_UNREACHABLE = "llm_unreachable"
    LLM_CONFIG_ERROR = "llm_config_error"
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
        requires_config_fix: The provider refused the configuration, so a
            retry cannot help until it is fixed (issue #1322).
        provider: Provider base URL, when known.
        model: Model identifier, when known.
    """

    kind: RunFailureKind
    summary: str
    detail: str
    resume_command: str = RESUME_COMMAND
    requires_config_fix: bool = False
    provider: str = ""
    model: str = ""


class ProviderFailure(Exception):  # @stub-protected
    """A model call failed at the provider, so the run must stop (issue #1322).

    Raised by the agent layer at the single classification site, so the engine
    posts the true cause once and ends the run as failed instead of
    synthesizing a comment and carrying on with ``validate``/``publish``/``route``.
    """

    def __init__(
        self,
        provider_error: str,
        *,
        provider: str = "",
        model: str = "",
        agent_phase: str = "agent",
        requires_config_fix: bool = False,
    ) -> None:
        super().__init__(provider_error)
        self.provider_error = provider_error
        self.provider = provider
        self.model = model
        self.agent_phase = agent_phase
        self.requires_config_fix = requires_config_fix


def _has_token(detail: str, tokens: Iterable[str]) -> bool:
    """Whether raw text carries any of ``tokens`` (wrapper-proof)."""
    lowered = detail.lower()
    return any(token in lowered for token in tokens)


def _is_unreachable_by_type(error: BaseException) -> bool:
    """Whether the exception type itself means the endpoint is unreachable."""
    if isinstance(error, LLMServiceUnavailableError):
        return True
    return isinstance(error, _LITELLM_UNREACHABLE_TYPES)


def _unwrap(error: BaseException) -> tuple[BaseException, ...]:
    """The error plus every provider error it wraps (issue #1322).

    The SDK raises ``ConversationRunError`` around the provider exception
    (``original_exception``), and exceptions chained with ``raise ... from``
    hide the real cause in ``__cause__``. The 404 of run 42049492853 arrived
    this way and was classified ``UNKNOWN``.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        inner = getattr(current, "original_exception", None)
        if not isinstance(inner, BaseException):
            inner = current.__cause__
        current = inner if isinstance(inner, BaseException) else None
    return tuple(chain)


def classify_failure(error: Exception) -> RunFailure:  # @stub-protected
    """Classify a run error into a ``RunFailure`` with a human-readable summary.

    Detection unwraps the SDK's ``ConversationRunError`` first, then prefers the
    SDK's typed exceptions, falling back to litellm types and to status/text
    tokens, so provider wrapping and version differences never silently degrade
    the message.

    Args:
        error: The exception captured from the supervised run.

    Returns:
        A ``RunFailure`` describing the cause and the resume command.
    """
    chain = _unwrap(error)
    detail = str(error)
    combined = "\n".join(str(link) for link in chain)

    # Deterministic request errors (context too long, content rejected) keep
    # today's handling: they are not provider outages (issue #1322).
    if any(
        isinstance(link, _SDK_REQUEST_TYPES + _LITELLM_REQUEST_TYPES)
        for link in chain
    ):
        return RunFailure(
            kind=RunFailureKind.LLM_ERROR,
            summary=_LLM_ERROR_SUMMARY,
            detail=detail,
        )
    if any(isinstance(link, _LITELLM_CONFIG_TYPES) for link in chain) or _has_token(
        combined, _CONFIG_TOKENS
    ):
        return RunFailure(
            kind=RunFailureKind.LLM_CONFIG_ERROR,
            summary=_CONFIG_SUMMARY,
            detail=detail,
            requires_config_fix=True,
        )
    if any(_is_unreachable_by_type(link) for link in chain) or _has_token(
        combined, _CONNECTIVITY_TOKENS
    ):
        return RunFailure(
            kind=RunFailureKind.LLM_UNREACHABLE,
            summary=_UNREACHABLE_SUMMARY,
            detail=detail,
        )
    if any(isinstance(link, LLMError) for link in chain):
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


def is_provider_failure(failure: RunFailure) -> bool:  # @stub-protected
    """Whether ``failure`` is a provider failure that must stop the run."""
    return failure.kind in (
        RunFailureKind.LLM_UNREACHABLE,
        RunFailureKind.LLM_CONFIG_ERROR,
    )


def redact_secrets(text: str, secrets: Iterable[str]) -> str:  # @stub-protected
    """Remove exact occurrences of the configured secrets from ``text`` (#1322).

    Only the configured values are scrubbed (no pattern matching): a provider
    error may echo the API key, and the failure comment is public.
    """
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted
