"""Provider-health indicators derived from raw LLM call timings (issue #722).

The OpenHands SDK reports per-call ``response_latencies`` (full round-trip) and
``token_usages`` only — never time-to-first-token nor throughput. This module is
the single home for deriving those two indicators from the raw timestamps and
token counts observed around a single LLM call, so the derivation stays pure and
unit-testable in isolation.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenTelemetry:
    """Raw timing + token counts for one LLM call, and the indicators derived from them.

    Fields:
        request_started_at: Timestamp when the request was sent.
        first_token_at: Timestamp when the first streamed token arrived, or
            ``None`` when the call was not streamed / not observable.
        request_finished_at: Timestamp when the final token arrived.
        prompt_tokens: Prompt (input) tokens reported for the call.
        completion_tokens: Completion (output) tokens reported for the call.
    """

    request_started_at: float
    first_token_at: float | None
    request_finished_at: float
    prompt_tokens: int
    completion_tokens: int

    # @stub-protected: signature defined by architect — do not modify
    def time_to_first_token(self) -> float | None:
        """Seconds between request start and first token, or ``None`` when unobservable."""
        if self.first_token_at is None:
            return None
        return self.first_token_at - self.request_started_at

    # @stub-protected: signature defined by architect — do not modify
    def tokens_per_second(self) -> float | None:
        """Generation throughput in tokens/second, or ``None`` when unobservable.

        Throughput is measured over the generation window only (first token →
        final token); input prefill time is deliberately excluded so the value
        reflects decode speed rather than a prefill-diluted average.
        """
        if self.first_token_at is None or self.completion_tokens <= 0:
            return None
        generation_seconds = self.request_finished_at - self.first_token_at
        if generation_seconds <= 0:
            return None
        return self.completion_tokens / generation_seconds
