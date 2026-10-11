"""Provider-health indicators derived from raw LLM call timings (issue #722).

The OpenHands SDK reports per-call ``response_latencies`` (full round-trip) and
``token_usages`` only — never time-to-first-token nor throughput. This module is
the single home for deriving those two indicators from the raw timestamps and
token counts observed around a single LLM call, so the derivation stays pure and
unit-testable in isolation.

It is also the anti-corruption boundary of the metrics wire: every capture
source (the litellm callback collector today) emits ``TokenTelemetry`` and the
rest of the pipeline (aggregation into ``Metrics``, resume rendering) consumes
it — so the capture mechanism can be swapped without touching downstream code.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class TokenTelemetry:
    """Raw timing + token counts for one LLM call, and the indicators derived from them.

    Fields:
        request_started_at: Monotonic timestamp when the request was sent.
        first_token_at: Monotonic timestamp when the first streamed token
            arrived, or ``None`` when the call was not streamed / not observable.
        request_finished_at: Monotonic timestamp when the final token arrived.
        prompt_tokens: Prompt (input) tokens billed for the call.
        completion_tokens: Completion (output) tokens billed for the call.
        cache_read_tokens: Prompt tokens served from the provider's cache.
        cache_write_tokens: Prompt tokens written to the provider's cache.
    """

    request_started_at: float
    first_token_at: float | None
    request_finished_at: float
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def time_to_first_token(self) -> float | None:
        """Seconds between request start and first token, or ``None`` when unobservable.

        Returns ``None`` when the first token was never observed and when the
        recorded timestamps are not ordered (a non-positive offset cannot be a
        real time-to-first-token).
        """
        if self.first_token_at is None:
            return None
        ttft = self.first_token_at - self.request_started_at
        return ttft if ttft > 0 else None

    def tokens_per_second(self) -> float | None:
        """Generation throughput in tokens/second, or ``None`` when unobservable.

        Throughput is measured over the generation window only (first token →
        final token); input prefill time is deliberately excluded so the value
        reflects decode speed rather than a prefill-diluted average. Returns
        ``None`` — never ``inf`` — when the first token is unobserved, the
        generation window is not positive, or no completion tokens were billed.
        """
        if self.first_token_at is None or self.completion_tokens <= 0:
            return None
        generation_seconds = self.request_finished_at - self.first_token_at
        if generation_seconds <= 0:
            return None
        return self.completion_tokens / generation_seconds

    @staticmethod
    def aggregate(
        observations: Sequence["TokenTelemetry"],
    ) -> tuple[float | None, float | None]:
        """Mean TTFT and mean throughput across a run's calls.

        Each mean is taken over that metric's own non-``None`` observations, so a
        call that yielded a timestamp but no usable throughput still contributes
        to the TTFT mean (and vice versa). Returns ``(None, None)`` when neither
        metric was observable.
        """
        ttfts = [
            ttft
            for ttft in (obs.time_to_first_token() for obs in observations)
            if ttft is not None
        ]
        throughputs = [
            tps
            for tps in (obs.tokens_per_second() for obs in observations)
            if tps is not None
        ]
        mean_ttft = sum(ttfts) / len(ttfts) if ttfts else None
        mean_tps = sum(throughputs) / len(throughputs) if throughputs else None
        return mean_ttft, mean_tps
