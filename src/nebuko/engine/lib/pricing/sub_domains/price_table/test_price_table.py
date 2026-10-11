"""Tests for the default price table entity (issue #714 / #721)."""

import pytest

from pricing.sub_domains.price_table.price_table import (
    TOKENS_PER_PRICE_UNIT,
    ModelPrice,
    PriceTable,
    normalize_model_id,
)


class TestNormalizeModelId:
    """normalize_model_id: exact id first, then leading-segment-stripped ids."""

    def test_exact_id_is_first_candidate(self):
        assert normalize_model_id("deepseek/deepseek-v4.1-flash")[0] == (
            "deepseek/deepseek-v4.1-flash"
        )

    def test_commandcode_route_prefix_is_stripped(self):
        candidates = normalize_model_id("openai/deepseek/deepseek-v4.1-flash")
        assert "deepseek/deepseek-v4.1-flash" in candidates

    def test_openrouter_route_prefix_is_stripped(self):
        candidates = normalize_model_id("openrouter/z-ai/glm-5.3-flash")
        assert "z-ai/glm-5.3-flash" in candidates

    def test_all_three_commandcode_paths_normalize(self):
        assert "xiaomi/mimo-v2.6-pro" in normalize_model_id(
            "openai/xiaomi/mimo-v2.6-pro"
        )
        assert "deepseek/deepseek-v4.1-flash" in normalize_model_id(
            "openai/deepseek/deepseek-v4.1-flash"
        )
        assert "z-ai/glm-5.3-flash" in normalize_model_id(
            "openai/z-ai/glm-5.3-flash"
        )

    def test_bare_id_has_single_candidate(self):
        assert normalize_model_id("gpt-5.5") == ["gpt-5.5"]


class TestFromOpenrouter:
    """PriceTable.from_openrouter: per-token USD strings → per-1M floats."""

    PAYLOAD = {
        "data": [
            {
                "id": "deepseek/deepseek-v4.1-flash",
                "pricing": {
                    "prompt": "0.0000003",
                    "completion": "0.0000012",
                    "input_cache_read": "0.000000006",
                },
            },
            {
                "id": "z-ai/glm-5.3-flash",
                "pricing": {
                    "prompt": "0.00000015",
                    "completion": "0.0000005",
                    "input_cache_read": "0.00000003",
                },
            },
        ]
    }

    def test_parses_per_token_pricing_to_per_million(self):
        table = PriceTable.from_openrouter(self.PAYLOAD)
        price = table.entries["deepseek/deepseek-v4.1-flash"]
        assert price.input_price == pytest.approx(0.3)
        assert price.output_price == pytest.approx(1.2)
        assert price.cache_read_price == pytest.approx(0.006)

    def test_source_is_recorded(self):
        table = PriceTable.from_openrouter(self.PAYLOAD)
        assert table.source == "openrouter"
        assert table.fetched_at > 0

    def test_missing_price_keys_become_none(self):
        table = PriceTable.from_openrouter(self.PAYLOAD)
        assert table.entries["z-ai/glm-5.3-flash"].cache_write_price is None

    def test_entries_without_id_are_skipped(self):
        table = PriceTable.from_openrouter(
            {"data": [{"pricing": {"prompt": "0.1"}}, {"id": "a/b", "pricing": {}}]}
        )
        assert list(table.entries) == ["a/b"]

    def test_empty_payload_yields_empty_table(self):
        table = PriceTable.from_openrouter({})
        assert table.entries == {}


class TestMatch:
    """PriceTable.match: routed model path resolves through normalization."""

    def _table(self):
        return PriceTable.from_openrouter(TestFromOpenrouter.PAYLOAD)

    def test_openai_route_prefix_resolves(self):
        price = self._table().match("openai/deepseek/deepseek-v4.1-flash")
        assert price is not None
        assert price.input_price == pytest.approx(0.3)

    def test_exact_id_resolves(self):
        price = self._table().match("deepseek/deepseek-v4.1-flash")
        assert price is not None

    def test_unmatched_model_returns_none(self):
        assert self._table().match("openai/unknown/model") is None


class TestModelPriceMergedOver:
    """ModelPrice.merged_over: per-field override on top of a base bundle."""

    def test_override_wins_per_field(self):
        base = ModelPrice(
            input_price=1.0, output_price=2.0, cache_read_price=0.1
        )
        override = ModelPrice(input_price=9.0)
        merged = base.merged_over(override)
        assert merged.input_price == 9.0
        assert merged.output_price == 2.0
        assert merged.cache_read_price == 0.1

    def test_none_override_returns_a_copy(self):
        base = ModelPrice(input_price=1.0)
        merged = base.merged_over(None)
        assert merged == base
        assert merged is not base

    def test_all_none_override_keeps_base(self):
        base = ModelPrice(input_price=1.0, output_price=2.0)
        merged = base.merged_over(ModelPrice())
        assert merged == base

    def test_base_none_is_filled_by_override(self):
        base = ModelPrice(input_price=1.0)
        merged = base.merged_over(ModelPrice(cache_write_price=3.0))
        assert merged.cache_write_price == 3.0
        assert merged.input_price == 1.0


def test_tokens_per_price_unit_matches_config_convention():
    assert TOKENS_PER_PRICE_UNIT == 1_000_000
