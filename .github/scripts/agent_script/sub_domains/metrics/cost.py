"""Per-session LLM cost accounting (issues #663, #721).

Operator-configured prices (``LLMConfig.input_price`` / ``output_price`` in
``agent-config.yaml``) are expressed per million tokens, whereas the SDK's
telemetry expects a per-token cost. This module is the single home for that
normalization and for selecting between the SDK-reported cost and a
deterministic token-based fallback, so both are unit-testable in isolation.

It also owns the bridge that lets litellm price a model it does not know
(issue #721): declared per-million prices are registered into litellm's
supported custom cost map, so litellm remains the single source of truth for
cost and the accounting path above reads its figure unchanged.
"""

from dataclasses import dataclass, field
from typing import Optional

import litellm
from openhands.sdk import get_logger

logger = get_logger(__name__)

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
        return cls(
            model=model,
            input_cost_per_token=input_per_1m_tokens / TOKENS_PER_PRICE_UNIT,
            output_cost_per_token=output_per_1m_tokens / TOKENS_PER_PRICE_UNIT,
        )


def litellm_knows_model(model: str) -> bool:
    """Whether litellm has *any* cost entry for *model*.

    Any entry counts as priced — a legitimate ``$0`` (free-tier) entry is still
    known, so such a model is not misreported as unpriced. Use
    :func:`litellm_prices_model` instead when the question is whether a declared
    rate may override litellm's own figure.
    """
    return litellm.model_cost.get(model) is not None


def litellm_prices_model(model: str) -> bool:
    """Whether litellm can already price *model* with a non-zero per-token cost."""
    entry = litellm.model_cost.get(model)
    if not entry:
        return False
    return bool(entry.get("input_cost_per_token")) or bool(
        entry.get("output_cost_per_token")
    )


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
        """Register every rate into litellm's custom cost map.

        A rate is applied only when it carries a complete ``(input, output)``
        pair and litellm cannot already price the model with a non-zero cost;
        litellm's own entries stay authoritative, so a wrong config value can
        never silently corrupt pricing for a known model.
        """
        for rate in self.rates:
            if rate.input_cost_per_token is None or rate.output_cost_per_token is None:
                continue
            if litellm_prices_model(rate.model):
                continue
            litellm.register_model(
                {
                    rate.model: {
                        "input_cost_per_token": rate.input_cost_per_token,
                        "output_cost_per_token": rate.output_cost_per_token,
                    }
                }
            )


@dataclass
class UnpricedModelWarner:
    """Emit at most one "unpriced model" warning per model id.

    Not frozen: the notifier carries the ``already warned`` state that makes
    the warning one-time for the process lifetime.
    """

    _warned: set[str] = field(default_factory=set)

    # @stub-protected: signature defined by architect — do not modify
    def warn_once(self, model: str) -> bool:
        """Warn once per model; return ``True`` iff this call logged."""
        if model in self._warned:
            return False
        self._warned.add(model)
        logger.warning(
            f"Model '{model}' is priced by neither litellm nor configured "
            f"prices; cost metrics will report 0. Declare input_price / "
            f"output_price for this model in the agent config to enable "
            f"cost accounting."
        )
        return True


#: Process-wide warner so the one-time warning survives repeated resolution calls.
UNPRICED_MODEL_WARNER = UnpricedModelWarner()
