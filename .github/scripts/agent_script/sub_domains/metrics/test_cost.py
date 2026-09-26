"""Tests for per-session cost accounting (issue #663)."""

from dataclasses import FrozenInstanceError

import pytest

from agent_script.sub_domains.metrics.cost import (
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


def test_tokens_per_price_unit_matches_config_convention():
    assert TOKENS_PER_PRICE_UNIT == 1_000_000


class _FakeCombinedMetrics:
    def __init__(self, accumulated_cost=0.0):
        self.accumulated_cost = accumulated_cost


class _FakeModelRef:
    def __init__(self, input_price, output_price):
        self.input_price = input_price
        self.output_price = output_price


class _FakeAgentConfig:
    def __init__(self, input_price, output_price):
        self.model_ref = _FakeModelRef(input_price, output_price)


class TestSessionCostHelper:
    """metrics_collector._session_cost: SDK-first with configured-rate fallback."""

    def test_uses_sdk_cost_when_present(self):
        from agent_script.sub_domains.metrics.metrics_collector import _session_cost

        cost = _session_cost(
            _FakeCombinedMetrics(0.25),
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            agent_cfg=_FakeAgentConfig(2.0, 8.0),
        )
        assert cost == pytest.approx(0.25)

    def test_falls_back_to_config_rates_for_unpriced_model(self):
        from agent_script.sub_domains.metrics.metrics_collector import _session_cost

        cost = _session_cost(
            _FakeCombinedMetrics(0.0),
            input_tokens=1_000_000,
            output_tokens=500_000,
            agent_cfg=_FakeAgentConfig(2.0, 8.0),
        )
        assert cost == pytest.approx(6.0)

    def test_zero_without_sdk_cost_or_config(self):
        from agent_script.sub_domains.metrics.metrics_collector import _session_cost

        cost = _session_cost(
            _FakeCombinedMetrics(0.0),
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            agent_cfg=None,
        )
        assert cost == 0.0
