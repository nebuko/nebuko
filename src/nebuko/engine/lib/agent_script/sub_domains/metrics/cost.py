"""Per-session LLM cost accounting (issue #663).

Operator-configured prices (``LLMConfig.input_price`` / ``output_price`` in
``agent-config.yaml``) are expressed per million tokens, whereas the SDK's
telemetry expects a per-token cost. This module is the single home for that
normalization and for selecting between the SDK-reported cost and a
deterministic token-based fallback, so both are unit-testable in isolation.
"""

from dataclasses import dataclass
from typing import Optional

#: Configured prices are quoted per million tokens (industry convention).
TOKENS_PER_PRICE_UNIT: int = 1_000_000


@dataclass(frozen=True)
class CostRates:
    """Per-token USD rates derived from configured per-million-token prices.

    Cache tiers fall back to the input rate when unpriced — conservative, and
    matching LiteLLM's custom-pricing behaviour. A rate bundle is only usable
    when the input and output tiers are priced.
    """

    input_per_token: float
    output_per_token: float
    cache_read_per_token: float
    cache_write_per_token: float

    # @stub-protected: signature defined by architect — do not modify
    @staticmethod
    def from_prices(
        input_price: Optional[float],
        output_price: Optional[float],
        cache_read_price: Optional[float] = None,
        cache_write_price: Optional[float] = None,
    ) -> Optional["CostRates"]:
        """Build per-token rates from per-million prices.

        Returns ``None`` unless *both* input and output prices are set — a rate
        bundle missing either cannot produce a trustworthy session cost. Unset
        cache prices fall back to the input price.
        """
        if input_price is None or output_price is None:
            return None
        cache_read = cache_read_price if cache_read_price is not None else input_price
        cache_write = (
            cache_write_price if cache_write_price is not None else input_price
        )
        return CostRates(
            input_per_token=input_price / TOKENS_PER_PRICE_UNIT,
            output_per_token=output_price / TOKENS_PER_PRICE_UNIT,
            cache_read_per_token=cache_read / TOKENS_PER_PRICE_UNIT,
            cache_write_per_token=cache_write / TOKENS_PER_PRICE_UNIT,
        )


@dataclass(frozen=True)
class SessionCost:
    """Cost accounting for one agent session.

    Prefers the SDK's own ``accumulated_cost`` (a genuinely billed cost); falls
    back to a four-tier ``tokens × rates`` estimate for models the SDK cannot
    price, so unpriced/self-hosted/subscription runs do not silently report $0.
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    rates: Optional[CostRates] = None
    sdk_reported_cost: float = 0.0

    # @stub-protected: signature defined by architect — do not modify
    def total(self) -> float:
        """Session cost in USD."""
        if self.sdk_reported_cost > 0:
            return self.sdk_reported_cost
        if self.rates is None:
            return 0.0
        regular_input = self._regular_input_tokens()
        return (
            regular_input * self.rates.input_per_token
            + self._cache_read() * self.rates.cache_read_per_token
            + self._cache_write() * self.rates.cache_write_per_token
            + self.output_tokens * self.rates.output_per_token
        )

    def _cache_read(self) -> int:
        return max(self.cache_read_tokens, 0)

    def _cache_write(self) -> int:
        return max(self.cache_write_tokens, 0)

    def _regular_input_tokens(self) -> int:
        """Input tokens billed at the full rate, guarding the token shape.

        OpenAI-style usage counts cache buckets *inside* ``prompt_tokens``
        (inclusive), Anthropic-style counts them alongside (exclusive). When the
        buckets fit within the prompt count they are treated as inclusive and
        subtracted; otherwise they are priced on top.
        """
        if self.input_tokens >= self._cache_read() + self._cache_write():
            return self.input_tokens - self._cache_read() - self._cache_write()
        return self.input_tokens
