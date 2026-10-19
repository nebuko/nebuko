"""Report-time cost resolution for the summarize metrics job (issue #928).

The run-time collector records the *real* model identity plus token counts but
no price (a subscription provider reports no cost), so the ``agent-metrics``
job sums zeros. This module is the report-time authority that fills those gaps:
for a stored metric it returns an *effective* USD cost — the authoritative
reported cost when present, otherwise the token counts priced against the
OpenRouter-backed default price table by the metric's recorded model identity.

Nothing in workflow state is mutated; the job recomputes on every run. The
whole seam is fail-open: an unknown model or a missing table degrades to the
documented ``0.0`` outcome rather than raising.

Base-deps-safe by construction: imports only ``cost``/``pricing``/
``metrics_schema`` and the standard library, never ``openhands``/``litellm``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Optional

from metrics_schema import Metrics

from agent_script.sub_domains.metrics.cost import CostRates, SessionCost
from pricing.features.load_price_table.load_price_table import load_price_table
from pricing.features.resolve_model_rates.resolve_model_rates import (
    resolve_model_rates,
)
from pricing.sub_domains.price_table.price_table import PriceTable

#: The SDK's placeholder for an unidentified model (see ``metrics_collector``).
PLACEHOLDER_MODEL = "default"


def _field(metric: "Metrics | Mapping[str, Any]", name: str, default: Any) -> Any:
    """Read ``name`` from a ``Metrics`` object or a legacy mapping."""
    if isinstance(metric, Mapping):
        return metric.get(name, default)
    return getattr(metric, name, default)


@dataclass(frozen=True)
class _ModelRef:
    """Path-only model reference for :func:`resolve_model_rates`.

    The report-time job has no agent config, so no configured ``*_price`` is
    supplied; the merge then resolves the table's per-field prices.
    """

    model_path: str


@dataclass(frozen=True)
class ReportCostResolver:  # @stub-protected
    """Effective per-entry cost for reporting from recorded identity + tokens."""

    table: Optional[PriceTable]

    @classmethod
    def build(  # @stub-protected
        cls, *, fetcher: Optional[Callable[..., Any]] = None
    ) -> "ReportCostResolver":
        """Load the default price table, fail-open (``table=None`` on failure)."""
        try:
            table = load_price_table(fetcher=fetcher)
        except Exception:  # noqa: BLE001 — pricing is a report bonus, never fatal
            table = None
        return cls(table=table)

    def model_key(self, metric: "Metrics | Mapping[str, Any]") -> str:  # @stub-protected
        """The aggregation row key: the recorded model id, or the placeholder."""
        name = _field(metric, "model_name", "") or ""
        if name and name != PLACEHOLDER_MODEL:
            return name
        return PLACEHOLDER_MODEL

    def effective_cost(self, metric: "Metrics | Mapping[str, Any]") -> float:  # @stub-protected
        """The entry's USD cost for reporting; never mutates ``metric``."""
        reported = _field(metric, "cost", 0.0) or 0.0
        if reported > 0:
            return float(reported)

        model_path = self.model_key(metric)
        if model_path == PLACEHOLDER_MODEL:
            return 0.0

        rates = self._rates(model_path)
        if rates is None:
            return 0.0

        return SessionCost(
            input_tokens=int(_field(metric, "input_tokens", 0) or 0),
            output_tokens=int(_field(metric, "output_tokens", 0) or 0),
            cache_read_tokens=int(_field(metric, "cache_hit_tokens", 0) or 0),
            rates=rates,
        ).total()

    def is_estimated(self, metric: "Metrics | Mapping[str, Any]") -> bool:  # @stub-protected
        """True when ``effective_cost`` came from the price table, not the provider.

        Mirrors ``effective_cost``'s branches exactly so the report's estimate
        marker never lies: a reported cost, an unresolved identity or a missing
        rate is *not* an estimate, and priced tokens are (issue #1019).
        """
        reported = _field(metric, "cost", 0.0) or 0.0
        if reported > 0:
            return False

        model_path = self.model_key(metric)
        if model_path == PLACEHOLDER_MODEL:
            return False

        return self._rates(model_path) is not None

    def _rates(self, model_path: str) -> Optional[CostRates]:
        """Per-token rates for ``model_path`` from the default table, or ``None``."""
        if self.table is None:
            return None
        price = resolve_model_rates(_ModelRef(model_path=model_path), self.table)
        if price is None:
            return None
        return CostRates.from_prices(
            price.input_price,
            price.output_price,
            price.cache_read_price,
            price.cache_write_price,
        )
