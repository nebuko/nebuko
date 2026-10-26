"""Tests for per-field config-over-table rate resolution (issue #714)."""

import pytest

from nebuko.engine.lib.pricing.features.resolve_model_rates.resolve_model_rates import (
    resolve_model_rates,
)
from nebuko.engine.lib.pricing.sub_domains.price_table.price_table import ModelPrice, PriceTable


class _ModelRef:
    def __init__(
        self,
        model_path="openai/deepseek/deepseek-v4.1-flash",
        input_price=None,
        output_price=None,
        cache_read_price=None,
        cache_write_price=None,
    ):
        self.model_path = model_path
        self.input_price = input_price
        self.output_price = output_price
        self.cache_read_price = cache_read_price
        self.cache_write_price = cache_write_price


def _table() -> PriceTable:
    return PriceTable(
        source="openrouter",
        fetched_at=1.0,
        entries={
            "deepseek/deepseek-v4.1-flash": ModelPrice(
                input_price=0.3,
                output_price=1.2,
                cache_read_price=0.006,
            )
        },
    )


class TestResolveModelRates:
    def test_config_only_without_a_table(self):
        merged = resolve_model_rates(
            _ModelRef(input_price=2.0, output_price=8.0), table=None
        )
        assert merged is not None
        assert merged.input_price == 2.0
        assert merged.output_price == 8.0

    def test_table_only_when_config_has_no_prices(self):
        merged = resolve_model_rates(_ModelRef(), _table())
        assert merged is not None
        assert merged.input_price == pytest.approx(0.3)
        assert merged.output_price == pytest.approx(1.2)
        assert merged.cache_read_price == pytest.approx(0.006)

    def test_per_field_config_overrides_table(self):
        merged = resolve_model_rates(
            _ModelRef(input_price=9.0), _table()
        )
        assert merged.input_price == 9.0            # config wins
        assert merged.output_price == pytest.approx(1.2)  # table fills

    def test_partial_config_completed_by_table(self):
        merged = resolve_model_rates(
            _ModelRef(input_price=9.0, output_price=9.5), _table()
        )
        assert merged.input_price == 9.0
        assert merged.output_price == 9.5

    def test_cache_price_from_config_wins(self):
        merged = resolve_model_rates(
            _ModelRef(
                input_price=2.0,
                output_price=8.0,
                cache_read_price=0.05,
            ),
            _table(),
        )
        assert merged.cache_read_price == 0.05

    def test_incomplete_bundle_returns_none(self):
        # Only an input price (config and table both lack/can't complete output).
        assert resolve_model_rates(_ModelRef(input_price=2.0), table=None) is None

    def test_nothing_priced_returns_none(self):
        assert resolve_model_rates(_ModelRef(), table=None) is None

    def test_unmatched_table_plus_partial_config_returns_none(self):
        unmatched = _ModelRef(model_path="openai/unknown/model", input_price=2.0)
        assert resolve_model_rates(unmatched, _table()) is None

    def test_unmatched_table_fully_configured_still_resolves(self):
        ref = _ModelRef(
            model_path="openai/unknown/model", input_price=2.0, output_price=8.0
        )
        merged = resolve_model_rates(ref, _table())
        assert merged is not None
        assert merged.input_price == 2.0
