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
    """Per-token USD rates derived from configured per-million-token prices."""

    input_per_token: float
    output_per_token: float

    # @stub-protected: signature defined by architect — do not modify
    @staticmethod
    def from_prices(
        input_price: Optional[float], output_price: Optional[float]
    ) -> Optional["CostRates"]:
        """Build per-token rates from per-million prices.

        Returns ``None`` unless *both* prices are set — the SDK only applies
        custom pricing when both ``input_cost_per_token`` and
        ``output_cost_per_token`` are present, so a half-configured pricing
        pair must not be treated as usable.
        """
        if input_price is None or output_price is None:
            return None
        return CostRates(
            input_per_token=input_price / TOKENS_PER_PRICE_UNIT,
            output_per_token=output_price / TOKENS_PER_PRICE_UNIT,
        )


@dataclass(frozen=True)
class SessionCost:
    """Cost accounting for one agent session.

    Prefers the SDK's own ``accumulated_cost`` (which also prices cache and
    reasoning tokens); falls back to ``tokens × rates`` for models the SDK
    cannot price, so unpriced/self-hosted runs do not silently report $0.
    """

    input_tokens: int
    output_tokens: int
    rates: Optional[CostRates] = None
    sdk_reported_cost: float = 0.0

    # @stub-protected: signature defined by architect — do not modify
    def total(self) -> float:
        """Session cost in USD."""
        if self.sdk_reported_cost > 0:
            return self.sdk_reported_cost
        if self.rates is None:
            return 0.0
        return (
            self.input_tokens * self.rates.input_per_token
            + self.output_tokens * self.rates.output_per_token
        )


@dataclass(frozen=True)
class ModelRate:
    """Per-token USD rates for one model, derived from declared per-million prices.

    litellm cannot price a model absent from its cost map (e.g. a CommandCode
    model name), so the operator declares the per-million-token prices in the
    model config and this value object carries the normalized per-token view.
    """

    model: str
    input_cost_per_token: float
    output_cost_per_token: float

    # @stub-protected: signature defined by architect — do not modify
    @classmethod
    def from_per_1m(
        cls,
        model: str,
        input_per_1m_tokens: float,
        output_per_1m_tokens: float,
    ) -> "ModelRate":
        """Build a per-token rate triple from a declared per-million price pair."""
        raise NotImplementedError


@dataclass(frozen=True)
class LitellmCostMapInjector:
    """Register operator-declared rates into litellm's supported custom cost map.

    The single place declared prices enter litellm: each ``ModelRate`` is
    registered so litellm itself prices the model and reports a non-zero
    ``response_cost`` — accounting keeps reading litellm's figure unchanged.
    Injection is idempotent, so it is safe to call more than once.
    """

    rates: tuple[ModelRate, ...]

    # @stub-protected: signature defined by architect — do not modify
    def inject(self) -> None:
        """Register every rate into litellm's custom cost map."""
        raise NotImplementedError
