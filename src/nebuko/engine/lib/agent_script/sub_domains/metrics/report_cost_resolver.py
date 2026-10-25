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
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import yaml  # noqa: F401 — used by the stub bodies once implemented

from metrics_schema import Metrics

from agent_script.sub_domains.metrics.cost import CostRates, SessionCost
from default_paths import agent_config_file
from pricing.features.load_price_table.load_price_table import load_price_table
from pricing.features.resolve_model_rates.resolve_model_rates import (
    resolve_model_rates,
)
from pricing.sub_domains.price_table.price_table import (
    ModelPrice,
    PriceTable,
    normalize_model_id,
)

#: The SDK's placeholder for an unidentified model (see ``metrics_collector``).
PLACEHOLDER_MODEL = "default"


def load_config_prices(  # @stub-protected
    config_path: Optional[str] = None,
) -> "dict[str, ModelPrice]":
    """Agent-config per-1M prices keyed by each llm entry's ``model_path``.

    Only entries setting **both** ``input_price`` and ``output_price`` are
    loadable — a partial bundle cannot produce a trustworthy session cost.
    Fail-open: a missing file, a parse error or no priced entries all yield
    ``{}`` (issue #1144).
    """
    path = config_path or agent_config_file()
    raise NotImplementedError  # @stub-protected


def _field(metric: "Metrics | Mapping[str, Any]", name: str, default: Any) -> Any:
    """Read ``name`` from a ``Metrics`` object or a legacy mapping."""
    if isinstance(metric, Mapping):
        return metric.get(name, default)
    return getattr(metric, name, default)


@dataclass(frozen=True)
class _ModelRef:
    """Model reference for :func:`resolve_model_rates`.

    The report-time job supplies the agent config's per-1M prices for the
    recorded identity (issue #1144), so the merge still resolves when the
    price table has no entry for the model.
    """

    model_path: str
    input_price: Optional[float] = None  # @stub-protected
    output_price: Optional[float] = None  # @stub-protected
    cache_read_price: Optional[float] = None  # @stub-protected


@dataclass(frozen=True)
class ReportCostResolver:  # @stub-protected
    """Effective per-entry cost for reporting from recorded identity + tokens."""

    table: Optional[PriceTable]
    config_prices: "dict[str, ModelPrice]" = field(default_factory=dict)  # @stub-protected

    @classmethod
    def build(  # @stub-protected
        cls,
        *,
        fetcher: Optional[Callable[..., Any]] = None,
        config_prices: Optional["dict[str, ModelPrice]"] = None,
    ) -> "ReportCostResolver":
        """Load the default price table, fail-open (``table=None`` on failure)."""
        try:
            table = load_price_table(fetcher=fetcher)
        except Exception:  # noqa: BLE001 — pricing is a report bonus, never fatal
            table = None
        return cls(table=table, config_prices=config_prices if config_prices is not None else {})

    def model_key(self, metric: "Metrics | Mapping[str, Any]") -> str:  # @stub-protected
        """The aggregation row key: the recorded model id, or the placeholder."""
        name = _field(metric, "model_name", "") or ""
        if name and name != PLACEHOLDER_MODEL:
            return name
        return PLACEHOLDER_MODEL

    def model_price(  # @stub-protected
        self, metric: "Metrics | Mapping[str, Any]"
    ) -> Optional[ModelPrice]:
        """The per-1M price the estimate used for ``metric``, or ``None``.

        ``None`` when the provider reported the cost, when the model identity is
        the placeholder, or when neither the config nor the table resolves a
        complete price — i.e. exactly the cases where ``effective_cost`` did not
        price tokens (issue #1048).
        """
        if (_field(metric, "cost", 0.0) or 0.0) > 0:
            return None

        model_path = self.model_key(metric)
        if model_path == PLACEHOLDER_MODEL:
            return None

        config_entry = self._config_price(model_path)
        if config_entry is None and self.table is None:
            return None

        raise NotImplementedError  # @stub-protected

    def _config_price(self, model_path: str) -> Optional[ModelPrice]:  # @stub-protected
        """The agent config's price for ``model_path``, or ``None``.

        Exact key first, then each candidate id ``normalize_model_id`` derives
        (route prefixes stripped, vendor aliases), so a config entry keyed by
        the provider-published path still matches a prefixed recorded id.
        """
        raise NotImplementedError  # @stub-protected

    def effective_cost(self, metric: "Metrics | Mapping[str, Any]") -> float:  # @stub-protected
        """The entry's USD cost for reporting; never mutates ``metric``."""
        reported = _field(metric, "cost", 0.0) or 0.0
        if reported > 0:
            return float(reported)

        price = self.model_price(metric)
        if price is None:
            return 0.0

        return SessionCost(
            input_tokens=int(_field(metric, "input_tokens", 0) or 0),
            output_tokens=int(_field(metric, "output_tokens", 0) or 0),
            cache_read_tokens=int(_field(metric, "cache_hit_tokens", 0) or 0),
            rates=self._rates(price),
        ).total()

    def is_estimated(self, metric: "Metrics | Mapping[str, Any]") -> bool:  # @stub-protected
        """True when ``effective_cost`` came from the price table, not the provider.

        Mirrors ``effective_cost``'s branches exactly so the report's estimate
        marker never lies: a reported cost, an unresolved identity or a missing
        rate is *not* an estimate, and priced tokens are (issue #1019).
        """
        return self.model_price(metric) is not None

    def _rates(self, price: ModelPrice) -> Optional[CostRates]:
        """Per-token rates for a resolved per-1M ``price``, or ``None``."""
        return CostRates.from_prices(
            price.input_price,
            price.output_price,
            price.cache_read_price,
            price.cache_write_price,
        )
