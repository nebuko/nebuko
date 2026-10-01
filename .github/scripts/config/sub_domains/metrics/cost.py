"""Cost-policy configuration entities.

A ``ProviderCostPolicy`` describes how to price one provider locally, for the
cases where the upstream does not propagate a usable cost — e.g. a
subscription-backed aggregator whose model ids are absent from LiteLLM's price
map, so it reports no cost at all. ``MetricsCostConfig`` groups those policies
and exposes the switch that enables the local-pricing fallback.
"""

from pydantic import BaseModel


class ProviderCostPolicy(BaseModel):
    """Local per-token pricing for a single provider."""

    provider: str
    input_cost_per_token: float
    output_cost_per_token: float
    subscription_backed: bool = False


class MetricsCostConfig(BaseModel):
    """Cost-resolution configuration: per-provider policies + fallback switch."""

    provider_policies: dict[str, ProviderCostPolicy] = {}
    fallback_to_local_pricing: bool = True
