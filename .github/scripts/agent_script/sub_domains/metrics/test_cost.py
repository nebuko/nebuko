"""Tests for per-session cost accounting (issue #663)."""

from dataclasses import FrozenInstanceError

import pytest

from agent_script.sub_domains.metrics.cost import (
    TOKENS_PER_PRICE_UNIT,
    CostRates,
    LitellmCostMapInjector,
    ModelRate,
    SessionCost,
    UnpricedModelWarner,
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


class TestModelRateFromPer1m:
    """ModelRate.from_per_1m: per-million prices → per-token rates."""

    def test_normalizes_per_million_to_per_token(self):
        rate = ModelRate.from_per_1m("openai/deepseek-v4.1-flash", 0.12, 0.18)
        assert rate.model == "openai/deepseek-v4.1-flash"
        assert rate.input_cost_per_token == pytest.approx(0.12e-6)
        assert rate.output_cost_per_token == pytest.approx(0.18e-6)

    def test_rate_is_immutable(self):
        rate = ModelRate.from_per_1m("m", 1.0, 2.0)
        with pytest.raises(FrozenInstanceError):
            rate.model = "other"


class TestLitellmCostMapInjector:
    """LitellmCostMapInjector.inject: declared rates land in litellm's cost map."""

    MODEL = "openai/issue721-injector-model"

    def setup_method(self):
        import litellm

        litellm.model_cost.pop(self.MODEL, None)

    def test_inject_makes_litellm_price_the_model(self):
        import litellm

        LitellmCostMapInjector(
            rates=(ModelRate.from_per_1m(self.MODEL, 3.0, 15.0),)
        ).inject()
        # 1M prompt + 1M completion tokens at $3/M and $15/M.
        prompt, completion = litellm.cost_per_token(
            model=self.MODEL,
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )
        assert prompt == pytest.approx(3.0)
        assert completion == pytest.approx(15.0)

    def test_inject_is_idempotent(self):
        import litellm

        injector = LitellmCostMapInjector(
            rates=(ModelRate.from_per_1m(self.MODEL, 3.0, 15.0),)
        )
        injector.inject()
        injector.inject()
        entry = litellm.model_cost[self.MODEL]
        assert entry["input_cost_per_token"] == pytest.approx(3.0e-6)
        assert entry["output_cost_per_token"] == pytest.approx(15.0e-6)


class TestLitellmCostMapInjectorPrecedence:
    """inject: litellm's own non-zero entries stay authoritative (issue #721)."""

    MODEL = "openai/issue721-precedence-model"

    def setup_method(self):
        import litellm

        litellm.model_cost.pop(self.MODEL, None)

    def test_declared_rate_does_not_clobber_litellm_entry(self):
        import litellm

        litellm.model_cost[self.MODEL] = {
            "input_cost_per_token": 1.0e-6,
            "output_cost_per_token": 2.0e-6,
        }
        LitellmCostMapInjector(
            rates=(ModelRate.from_per_1m(self.MODEL, 3.0, 15.0),)
        ).inject()
        entry = litellm.model_cost[self.MODEL]
        assert entry["input_cost_per_token"] == pytest.approx(1.0e-6)
        assert entry["output_cost_per_token"] == pytest.approx(2.0e-6)

    def test_incomplete_pair_does_not_price_the_model(self):
        import litellm

        incomplete = ModelRate(
            model=self.MODEL,
            input_cost_per_token=3.0e-6,
            output_cost_per_token=None,
        )
        LitellmCostMapInjector(rates=(incomplete,)).inject()
        assert self.MODEL not in litellm.model_cost


class TestUnpricedModelWarner:
    """UnpricedModelWarner.warn_once: one warning per model, process lifetime."""

    LOGGER = "agent_script.sub_domains.metrics.cost"

    def test_warns_once_per_model(self, caplog):
        import logging

        warner = UnpricedModelWarner()
        with caplog.at_level(logging.WARNING, logger=self.LOGGER):
            first = warner.warn_once("openai/unpriced-a")
            second = warner.warn_once("openai/unpriced-a")
            other = warner.warn_once("openai/unpriced-b")
        assert (first, second, other) == (True, False, True)
        messages = [record.getMessage() for record in caplog.records]
        assert sum("openai/unpriced-a" in message for message in messages) == 1
        assert sum("openai/unpriced-b" in message for message in messages) == 1


class TestDeclaredPricingEndToEnd:
    """Declared prices make litellm report a non-zero cost (issue #721).

    The existing accounting path reads litellm's figure unchanged, so a
    CommandCode-style model with declared prices yields a non-zero
    ``total_cost_usd`` instead of the historic silent ``0``.
    """

    MODEL = "openai/issue721-e2e-model"

    def setup_method(self):
        import litellm

        litellm.model_cost.pop(self.MODEL, None)

    def test_declared_prices_yield_non_zero_session_cost(self):
        import litellm

        from agent_script.sub_domains.metrics.metrics_collector import _session_cost

        LitellmCostMapInjector(
            rates=(ModelRate.from_per_1m(self.MODEL, 3.0, 15.0),)
        ).inject()
        # The SDK telemetry reports litellm's response_cost for these tokens.
        prompt, completion = litellm.cost_per_token(
            model=self.MODEL,
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )
        cost = _session_cost(
            _FakeCombinedMetrics(prompt + completion),
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            agent_cfg=None,
        )
        assert cost == pytest.approx(18.0)
        assert cost > 0.0
