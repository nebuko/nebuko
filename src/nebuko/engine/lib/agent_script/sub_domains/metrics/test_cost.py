"""Tests for per-session cost accounting (issue #663)."""

from dataclasses import FrozenInstanceError

import pytest

from nebuko.engine.lib.agent_script.sub_domains.metrics.cost import (
    TOKENS_PER_PRICE_UNIT,
    CostRates,
    SessionCost,
)


class TestCostRatesFromPrices:
    """CostRates.from_prices: per-million prices → per-token rates."""

    def test_normalizes_per_million_to_per_token(self):
        rates = CostRates.from_prices(2.0, 8.0)
        assert rates is not None
        assert rates.input_per_token == pytest.approx(2.0e-6)
        assert rates.output_per_token == pytest.approx(8.0e-6)

    def test_cache_rates_fall_back_to_input_price(self):
        rates = CostRates.from_prices(2.0, 8.0)
        assert rates.cache_read_per_token == pytest.approx(2.0e-6)
        assert rates.cache_write_per_token == pytest.approx(2.0e-6)

    def test_explicit_cache_rates_are_used(self):
        rates = CostRates.from_prices(2.0, 8.0, cache_read_price=0.2, cache_write_price=3.0)
        assert rates is not None
        assert rates.cache_read_per_token == pytest.approx(0.2e-6)
        assert rates.cache_write_per_token == pytest.approx(3.0e-6)

    def test_returns_none_when_input_price_missing(self):
        assert CostRates.from_prices(None, 8.0) is None

    def test_returns_none_when_output_price_missing(self):
        assert CostRates.from_prices(2.0, None) is None

    def test_returns_none_when_both_missing(self):
        assert CostRates.from_prices(None, None) is None

    def test_rates_are_immutable(self):
        rates = CostRates.from_prices(2.0, 8.0)
        with pytest.raises(FrozenInstanceError):
            rates.input_per_token = 1.0


class TestSessionCostTotal:
    """SessionCost.total: SDK-reported cost preferred, token fallback otherwise."""

    def test_prefers_sdk_reported_cost(self):
        cost = SessionCost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            rates=CostRates.from_prices(2.0, 8.0),
            sdk_reported_cost=0.5,
        )
        assert cost.total() == 0.5

    def test_falls_back_to_tokens_times_rates(self):
        cost = SessionCost(
            input_tokens=1_000_000,
            output_tokens=500_000,
            rates=CostRates.from_prices(2.0, 8.0),
        )
        # 1M × $2/M + 0.5M × $8/M = 2.0 + 4.0
        assert cost.total() == pytest.approx(6.0)

    def test_falls_back_when_sdk_reports_zero(self):
        cost = SessionCost(
            input_tokens=1_000_000,
            output_tokens=0,
            rates=CostRates.from_prices(2.0, 8.0),
            sdk_reported_cost=0.0,
        )
        assert cost.total() == pytest.approx(2.0)

    def test_zero_when_no_rates_and_no_sdk_cost(self):
        cost = SessionCost(input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost.total() == 0.0


class TestSessionCostCacheArithmetic:
    """Cache buckets price at the cache rate; the input tier guards token shape."""

    def test_inclusive_cache_read_subtracted_from_input(self):
        # prompt_tokens include the cached subset (OpenAI shape).
        cost = SessionCost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=400_000,
            rates=CostRates.from_prices(2.0, 8.0, cache_read_price=0.5),
        )
        # (1M - 0.4M) × $2 + 0.4M × $0.5 = 1.2 + 0.2
        assert cost.total() == pytest.approx(1.4)

    def test_exclusive_cache_read_priced_on_top(self):
        # Buckets exceed the prompt count → treat as exclusive (Anthropic shape).
        cost = SessionCost(
            input_tokens=100_000,
            output_tokens=0,
            cache_read_tokens=400_000,
            rates=CostRates.from_prices(2.0, 8.0, cache_read_price=0.5),
        )
        # 0.1M × $2 + 0.4M × $0.5 = 0.2 + 0.2
        assert cost.total() == pytest.approx(0.4)

    def test_cache_write_priced_at_its_rate(self):
        cost = SessionCost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_write_tokens=200_000,
            rates=CostRates.from_prices(2.0, 8.0, cache_write_price=4.0),
        )
        # (1M - 0.2M) × $2 + 0.2M × $4 = 1.6 + 0.8
        assert cost.total() == pytest.approx(2.4)

    def test_cache_read_and_write_both_subtracted_when_inclusive(self):
        cost = SessionCost(
            input_tokens=1_000_000,
            output_tokens=250_000,
            cache_read_tokens=300_000,
            cache_write_tokens=200_000,
            rates=CostRates.from_prices(2.0, 8.0, cache_read_price=0.1, cache_write_price=4.0),
        )
        # (1M - 0.3M - 0.2M) × $2 + 0.3M × $0.1 + 0.2M × $4 + 0.25M × $8
        # = 1.0 + 0.03 + 0.8 + 2.0
        assert cost.total() == pytest.approx(3.83)

    def test_no_cache_tokens_is_the_plain_two_tier_cost(self):
        cost = SessionCost(
            input_tokens=1_000_000,
            output_tokens=500_000,
            rates=CostRates.from_prices(2.0, 8.0),
        )
        assert cost.total() == pytest.approx(6.0)

    def test_sdk_reported_cost_still_wins_over_cache_math(self):
        cost = SessionCost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=500_000,
            rates=CostRates.from_prices(2.0, 8.0),
            sdk_reported_cost=9.0,
        )
        assert cost.total() == 9.0


def test_tokens_per_price_unit_matches_config_convention():
    assert TOKENS_PER_PRICE_UNIT == 1_000_000


class _FakeCombinedMetrics:
    def __init__(self, accumulated_cost=0.0):
        self.accumulated_cost = accumulated_cost


class TestSessionCostHelper:
    """metrics_collector._session_cost: SDK-first with resolved-rate fallback."""

    def _price(self, **kwargs):
        from nebuko.engine.lib.pricing.sub_domains.price_table.price_table import ModelPrice

        return ModelPrice(**kwargs)

    def test_uses_sdk_cost_when_present(self):
        from nebuko.engine.lib.agent_script.sub_domains.metrics.metrics_collector import _session_cost

        cost = _session_cost(
            _FakeCombinedMetrics(0.25),
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=0,
            cache_write_tokens=0,
            model_price=self._price(input_price=2.0, output_price=8.0),
        )
        assert cost == pytest.approx(0.25)

    def test_falls_back_to_resolved_rates_for_unpriced_model(self):
        from nebuko.engine.lib.agent_script.sub_domains.metrics.metrics_collector import _session_cost

        cost = _session_cost(
            _FakeCombinedMetrics(0.0),
            input_tokens=1_000_000,
            output_tokens=500_000,
            cache_read_tokens=0,
            cache_write_tokens=0,
            model_price=self._price(input_price=2.0, output_price=8.0),
        )
        assert cost == pytest.approx(6.0)

    def test_cache_tokens_priced_in_the_fallback(self):
        from nebuko.engine.lib.agent_script.sub_domains.metrics.metrics_collector import _session_cost

        cost = _session_cost(
            _FakeCombinedMetrics(0.0),
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=400_000,
            cache_write_tokens=0,
            model_price=self._price(
                input_price=2.0, output_price=8.0, cache_read_price=0.5
            ),
        )
        assert cost == pytest.approx(1.4)

    def test_zero_without_sdk_cost_or_price(self):
        from nebuko.engine.lib.agent_script.sub_domains.metrics.metrics_collector import _session_cost

        cost = _session_cost(
            _FakeCombinedMetrics(0.0),
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=0,
            cache_write_tokens=0,
            model_price=None,
        )
        assert cost == 0.0
