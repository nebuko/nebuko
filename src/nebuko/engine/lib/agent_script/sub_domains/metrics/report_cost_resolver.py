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

from metrics_schema import Metrics

from agent_script.sub_domains.metrics.cost import CostRates, SessionCost
from pricing.features.load_price_table.load_price_table import load_price_table
from pricing.features.resolve_model_rates.resolve_model_rates import (
    resolve_model_rates,
)
from pricing.sub_domains.price_table.price_table import ModelPrice, PriceTable

#: The SDK's placeholder for an unidentified model (see ``metrics_collector``).
PLACEHOLDER_MODEL = "default"


def _field(metric: "Metrics | Mapping[str, Any]", name: str, default: Any) -> Any:
    """Read ``name`` from a ``Metrics`` object or a legacy mapping."""
    if isinstance(metric, Mapping):
        return metric.get(name, default)
    return getattr(metric, name, default)


@dataclass(frozen=True)
class _ModelRef:
    """Model reference for :func:`resolve_model_rates`.

    Carries the operator's configured per-1M prices when the recorded model
    identity matches an ``llms`` entry (issue #1144): the merge then resolves
    config-over-table per field, so a subscription-provider path the table
    cannot match is still priced from the config alone.
    """

    model_path: str
    input_price: Optional[float] = None
    output_price: Optional[float] = None
    cache_read_price: Optional[float] = None
    cache_write_price: Optional[float] = None


def load_config_prices(config_path: Optional[str] = None) -> dict[str, ModelPrice]:
    """Per-1M prices keyed by the agent config's ``model_path`` (issue #1144).

    Reads the layered agent config (``default_paths.agent_config_file()``) and
    collects the priced ``llms`` entries. Fail-open: an unreadable or absent
    config yields ``{}`` — pricing is a report bonus, never fatal.
    """
    try:
        import default_paths
        from config import load_config

        path = config_path or default_paths.agent_config_file()
        cfg = load_config(path)
        return {
            llm.model_path: ModelPrice(
                input_price=llm.input_price,
                output_price=llm.output_price,
                cache_read_price=llm.cache_read_price,
                cache_write_price=llm.cache_write_price,
            )
            for llm in (cfg.llms or {}).values()
            if llm.input_price is not None or llm.output_price is not None
        }
    except Exception:  # noqa: BLE001 — pricing is a report bonus, never fatal
        return {}


@dataclass(frozen=True)
class ReportCostResolver:  # @stub-protected
    """Effective per-entry cost for reporting from recorded identity + tokens."""

    table: Optional[PriceTable]
    #: Per-1M prices from the agent config's ``llms``, keyed by ``model_path``
    #: (issue #1144); they win per field over the table's match.
    config_prices: dict[str, ModelPrice] = field(default_factory=dict)

    @classmethod
    def build(  # @stub-protected
        cls, *, fetcher: Optional[Callable[..., Any]] = None
    ) -> "ReportCostResolver":
        """Load the default price table, fail-open (``table=None`` on failure)."""
        try:
            table = load_price_table(fetcher=fetcher)
        except Exception:  # noqa: BLE001 — pricing is a report bonus, never fatal
            table = None
        return cls(table=table, config_prices=load_config_prices())

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
        the placeholder, or when the table has no complete price — i.e. exactly
        the cases where ``effective_cost`` did not price tokens (issue #1048).
        """
        if (_field(metric, "cost", 0.0) or 0.0) > 0:
            return None

        model_path = self.model_key(metric)
        if model_path == PLACEHOLDER_MODEL or self.table is None:
            return None

        ref = self.config_prices.get(model_path)
        return resolve_model_rates(
            _ModelRef(
                model_path=model_path,
                input_price=ref.input_price if ref else None,
                output_price=ref.output_price if ref else None,
                cache_read_price=ref.cache_read_price if ref else None,
                cache_write_price=ref.cache_write_price if ref else None,
            ),
            self.table,
        )

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
