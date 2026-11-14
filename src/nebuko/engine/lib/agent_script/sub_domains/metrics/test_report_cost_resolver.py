"""Tests for report-time cost resolution (issue #928).

No network: the price table is stubbed, or loaded through an injected fetcher
pointed at a tmp cache dir.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from nebuko.engine.lib.agent_script.sub_domains.metrics import report_cost_resolver as mod
from nebuko.engine.lib.agent_script.sub_domains.metrics.report_cost_resolver import (
    PLACEHOLDER_MODEL,
    ReportCostResolver,
)
from nebuko.engine.lib.metrics_schema import Metrics
from nebuko.engine.lib.pricing.sub_domains.price_table.price_table import ModelPrice, PriceTable

# tests live at <lib>/agent_script/sub_domains/metrics/, so the importable lib
# root is parents[3] (parents[4] would be.../engine, which cannot satisfy
# `import agent_script`).
ENGINE_LIB = Path(__file__).resolve().parents[3]


def _table() -> PriceTable:
    """A table keyed like OpenRouter (org-qualified deepseek id)."""
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


def _resolver(table=None) -> ReportCostResolver:
    return ReportCostResolver(table=table if table is not None else _table())


class TestModelKey:
    def test_real_model_id_is_the_key(self):
        m = Metrics(model_name="openai/deepseek/deepseek-v4.1-flash")
        assert _resolver().model_key(m) == "openai/deepseek/deepseek-v4.1-flash"

    def test_placeholder_and_empty_collapse_to_the_placeholder_bucket(self):
        resolver = _resolver()
        assert resolver.model_key(Metrics(model_name="default")) == PLACEHOLDER_MODEL
        assert resolver.model_key(Metrics(model_name="")) == PLACEHOLDER_MODEL

    def test_accepts_a_legacy_mapping(self):
        assert _resolver().model_key({"model_name": "gpt-4"}) == "gpt-4"


class TestEffectiveCost:
    def test_zero_cost_real_model_is_priced_from_the_table(self):
        m = Metrics(
            model_name="openai/deepseek/deepseek-v4.1-flash",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cost=0.0,
        )
        # 1M × $0.30/M + 0.5M × $1.20/M = 0.30 + 0.60
        assert _resolver().effective_cost(m) == pytest.approx(0.9)

    def test_reported_cost_passes_through_untouched(self):
        m = Metrics(model_name="openai/deepseek/deepseek-v4.1-flash", cost=1.25)
        assert _resolver().effective_cost(m) == 1.25

    def test_cache_hit_tokens_bill_at_the_cache_read_rate(self):
        m = Metrics(
            model_name="openai/deepseek/deepseek-v4.1-flash",
            input_tokens=1_000_000,
            cache_hit_tokens=200_000,
            cost=0.0,
        )
        # 800k regular @ $0.30/M + 200k cache-read @ $0.006/M
        assert _resolver().effective_cost(m) == pytest.approx(0.2412)

    def test_placeholder_identity_stays_zero(self):
        m = Metrics(model_name="default", input_tokens=1_000_000, output_tokens=1_000_000)
        assert _resolver().effective_cost(m) == 0.0

    def test_unknown_model_is_zero(self):
        m = Metrics(model_name="openai/does-not-exist", input_tokens=100, output_tokens=10)
        assert _resolver().effective_cost(m) == 0.0

    def test_missing_table_is_zero(self):
        m = Metrics(
            model_name="openai/deepseek/deepseek-v4.1-flash",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        assert ReportCostResolver(table=None).effective_cost(m) == 0.0

    def test_zero_tokens_yield_zero(self):
        m = Metrics(model_name="openai/deepseek/deepseek-v4.1-flash")
        assert _resolver().effective_cost(m) == 0.0

    def test_accepts_a_legacy_mapping(self):
        metric = {
            "model_name": "openai/deepseek/deepseek-v4.1-flash",
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cost": 0.0,
        }
        assert _resolver().effective_cost(metric) == pytest.approx(0.3)

    def test_does_not_mutate_the_metric(self):
        m = Metrics(
            model_name="openai/deepseek/deepseek-v4.1-flash",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cost=0.0,
        )
        _resolver().effective_cost(m)
        assert m.cost == 0.0


class TestIsEstimated:
    """The report's cost-provenance flag (issue #1019) mirrors effective_cost."""

    def test_table_priced_entry_is_an_estimate(self):
        m = Metrics(
            model_name="openai/deepseek/deepseek-v4.1-flash",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cost=0.0,
        )
        assert _resolver().is_estimated(m) is True

    def test_reported_cost_is_not_an_estimate(self):
        m = Metrics(model_name="openai/deepseek/deepseek-v4.1-flash", cost=1.25)
        assert _resolver().is_estimated(m) is False

    def test_placeholder_identity_is_not_an_estimate(self):
        m = Metrics(model_name="default", input_tokens=1_000_000)
        assert _resolver().is_estimated(m) is False

    def test_unknown_model_is_not_an_estimate(self):
        m = Metrics(model_name="openai/does-not-exist", input_tokens=100)
        assert _resolver().is_estimated(m) is False

    def test_missing_table_is_not_an_estimate(self):
        m = Metrics(model_name="openai/deepseek/deepseek-v4.1-flash", input_tokens=1_000_000)
        assert ReportCostResolver(table=None).is_estimated(m) is False

    def test_accepts_a_legacy_mapping(self):
        metric = {
            "model_name": "openai/deepseek/deepseek-v4.1-flash",
            "input_tokens": 1_000_000,
            "cost": 0.0,
        }
        assert _resolver().is_estimated(metric) is True

    def test_never_contradicts_effective_cost(self):
        """A starred cell always carries price-table money, and vice versa."""
        priced = Metrics(
            model_name="openai/deepseek/deepseek-v4.1-flash", input_tokens=1_000_000
        )
        reported = Metrics(model_name="openai/deepseek/deepseek-v4.1-flash", cost=1.0)
        resolver = _resolver()
        assert resolver.effective_cost(priced) > 0 and resolver.is_estimated(priced)
        assert resolver.effective_cost(reported) > 0 and not resolver.is_estimated(reported)


class TestModelPrice:
    """The per-1M price the report renders next to an estimated cost (issue #1048)."""

    def test_estimated_entry_exposes_the_table_price(self):
        m = Metrics(
            model_name="openai/deepseek/deepseek-v4.1-flash",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cost=0.0,
        )
        price = _resolver().model_price(m)
        assert price is not None
        assert price.input_price == pytest.approx(0.3)
        assert price.output_price == pytest.approx(1.2)
        assert price.cache_read_price == pytest.approx(0.006)

    def test_displayed_price_reproduces_the_effective_cost(self):
        m = Metrics(
            model_name="openai/deepseek/deepseek-v4.1-flash",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cache_hit_tokens=200_000,
            cost=0.0,
        )
        price = _resolver().model_price(m)
        assert price is not None
        expected = (
            800_000 * price.input_price
            + 200_000 * price.cache_read_price
            + 500_000 * price.output_price
        ) / 1_000_000
        assert _resolver().effective_cost(m) == pytest.approx(expected)

    def test_reported_cost_has_no_price(self):
        m = Metrics(model_name="openai/deepseek/deepseek-v4.1-flash", cost=1.25)
        assert _resolver().model_price(m) is None

    def test_placeholder_identity_has_no_price(self):
        m = Metrics(model_name="default", input_tokens=1_000_000)
        assert _resolver().model_price(m) is None

    def test_unknown_model_has_no_price(self):
        m = Metrics(model_name="openai/does-not-exist", input_tokens=100)
        assert _resolver().model_price(m) is None

    def test_missing_table_has_no_price(self):
        m = Metrics(model_name="openai/deepseek/deepseek-v4.1-flash", input_tokens=1_000_000)
        assert ReportCostResolver(table=None).model_price(m) is None

    def test_accepts_a_legacy_mapping(self):
        metric = {"model_name": "openai/deepseek/deepseek-v4.1-flash", "cost": 0.0}
        price = _resolver().model_price(metric)
        assert price is not None and price.input_price == pytest.approx(0.3)

    def test_effective_cache_read_price_falls_back_to_input(self):
        """An entry without a cache-read price bills cache hits at the input rate."""
        table = PriceTable(
            source="openrouter",
            fetched_at=1.0,
            entries={
                "vendor/no-cache-model": ModelPrice(input_price=2.0, output_price=8.0)
            },
        )
        m = Metrics(
            model_name="vendor/no-cache-model",
            input_tokens=500_000,
            cache_hit_tokens=500_000,
            cost=0.0,
        )
        resolver = _resolver(table)
        price = resolver.model_price(m)
        assert price is not None
        assert price.effective_cache_read_price() == pytest.approx(2.0)
        # 500k cache-hit tokens at $2.00/1M; no regular input tokens.
        assert resolver.effective_cost(m) == pytest.approx(1.0)


class TestBuild:
    def test_build_loads_the_table_through_the_injected_fetcher(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setenv("NEBUKO_PRICE_CACHE_DIR", str(tmp_path))
        payload = {
            "data": [
                {
                    "id": "deepseek/deepseek-v4.1-flash",
                    "pricing": {"prompt": "0.0000003", "completion": "0.0000012"},
                }
            ]
        }

        resolver = ReportCostResolver.build(
            fetcher=lambda: json.dumps(payload).encode()
        )

        assert resolver.table is not None
        m = Metrics(
            model_name="openai/deepseek/deepseek-v4.1-flash",
            input_tokens=1_000_000,
            output_tokens=0,
        )
        assert resolver.effective_cost(m) == pytest.approx(0.3)

    def test_build_is_fail_open_when_the_loader_raises(self, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(mod, "load_price_table", _boom)
        assert ReportCostResolver.build().table is None


def _import_guard_script(lib: Path) -> str:
    """Import the base-deps-safe chain with ``openhands``/``litellm`` blocked.

    The library is an installed subpackage of ``nebuko``, so the probe needs no
    ``sys.path`` setup; the guard only proves the chain resolves without the
    runtime-only extras.
    """
    lib = str(lib)
    return (
        "import builtins;"
        "real = builtins.__import__;"
        "guard = lambda name, *a, **k: (_ for _ in ()).throw("
        "ImportError(name)) if name.split('.')[0] in "
        "('openhands', 'litellm') else real(name, *a, **k);"
        "builtins.__import__ = guard;"
        "import nebuko.engine.lib.agent_script as agent_script;"
        "import nebuko.engine.lib.pricing as pricing;"
        "from nebuko.engine.lib.agent_script.sub_domains.metrics.report_cost_resolver import"
        " ReportCostResolver;"
        "from nebuko.engine.lib.pricing.features.load_price_table.load_price_table import"
        " load_price_table;"
        f"assert agent_script.__file__.startswith({lib!r}), "
        "agent_script.__file__;"
        "print('ok')"
    )


class TestBaseDepsImportability:
    """The job installs base deps only, so the resolver chain must not need
    ``openhands``/``litellm`` (issue #928)."""

    def test_resolver_imports_with_openhands_and_litellm_blocked(self, tmp_path):
        script = _import_guard_script(ENGINE_LIB)
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=env,
        )
        assert result.returncode == 0, result.stderr

def _mistral_config_prices() -> dict:
    """Config prices keyed the way ``load_config_prices`` keys them."""
    return {
        "mistral/mistral-small-2603": ModelPrice(
            input_price=0.15, output_price=0.6, cache_read_price=0.015
        )
    }


class TestConfigPriceFallback:
    """Report-time fallback to agent-config llm prices (issue #1144)."""

    def test_model_price_falls_back_to_config_prices_when_table_has_no_match(self):
        # Given: no price table and a priced config entry.
        # When: a zero-cost metric for the config's model is priced.
        # Then: the config price is returned and tokens are priced from it.
        resolver = ReportCostResolver(table=None, config_prices=_mistral_config_prices())
        m = Metrics(
            model_name="mistral/mistral-small-2603",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cost=0.0,
        )
        assert resolver.model_price(m) == ModelPrice(
            input_price=0.15, output_price=0.6, cache_read_price=0.015
        )
        # 1M × $0.15/M + 0.5M × $0.60/M
        assert resolver.effective_cost(m) == pytest.approx(0.45)
        assert resolver.is_estimated(m)

    def test_config_fields_fill_gaps_in_partial_table_match(self):
        # Given: a table entry missing output_price, and a config entry with it.
        # When: the model is priced.
        # Then: the merge fills output from config, input from the table
        # (table wins where both are set).
        table = PriceTable(
            source="openrouter",
            fetched_at=1.0,
            entries={
                "mistral/mistral-small-2603": ModelPrice(input_price=0.9)
            },
        )
        resolver = ReportCostResolver(table=table, config_prices=_mistral_config_prices())
        m = Metrics(model_name="mistral/mistral-small-2603", cost=0.0)
        price = resolver.model_price(m)
        assert price is not None
        assert price.input_price == pytest.approx(0.9)
        assert price.output_price == pytest.approx(0.6)

    def test_model_price_returns_none_without_table_or_config_entry(self):
        # Given: no table and no matching config entry.
        # When: a zero-cost metric for an unknown model is priced.
        # Then: ``None`` — the fail-open $0 outcome is preserved.
        resolver = ReportCostResolver(table=None, config_prices=_mistral_config_prices())
        m = Metrics(model_name="mistral/does-not-exist", input_tokens=1_000, cost=0.0)
        assert resolver.model_price(m) is None
        assert resolver.effective_cost(m) == 0.0
        assert not resolver.is_estimated(m)

    def test_config_entry_matches_a_stripped_prefixed_model_id(self):
        # Given: a config entry keyed by the provider-published path and a
        # metric recording a route-prefixed id.
        # When: the model is priced.
        # Then: a candidate id from ``normalize_model_id`` resolves it.
        resolver = ReportCostResolver(table=None, config_prices=_mistral_config_prices())
        m = Metrics(model_name="openrouter/mistral/mistral-small-2603", cost=0.0)
        assert resolver.model_price(m) is not None

    def test_reported_cost_still_wins_over_any_estimation(self):
        # Given: a metric whose provider reported a cost.
        # When: it is priced with a config entry present.
        # Then: the reported cost passes through and no estimate is claimed.
        resolver = ReportCostResolver(table=None, config_prices=_mistral_config_prices())
        m = Metrics(model_name="mistral/mistral-small-2603", cost=1.25)
        assert resolver.effective_cost(m) == 1.25
        assert resolver.model_price(m) is None
        assert not resolver.is_estimated(m)


class TestLoadConfigPrices:
    """``load_config_prices`` reads the agent config's llm prices, fail-open."""

    def test_load_config_prices_reads_llm_prices_and_skips_incomplete_entries(self, tmp_path):
        config = tmp_path / "agent-config.yaml"
        config.write_text(
            "llms:\n"
            "  small:\n"
            "    model_path: mistral/mistral-small-2603\n"
            "    input_price: 0.15\n"
            "    output_price: 0.6\n"
            "    cache_read_price: 0.015\n"
            "  large:\n"
            "    model_path: mistral/mistral-large-4\n"
            "    input_price: 1.36\n"  # output_price missing → skipped
            "  glm:\n"
            "    model_path: mistral/zai-glm-5-3\n"  # no prices at all → skipped
        )
        prices = mod.load_config_prices(str(config))
        assert list(prices) == ["mistral/mistral-small-2603"]
        assert prices["mistral/mistral-small-2603"] == ModelPrice(
            input_price=0.15, output_price=0.6, cache_read_price=0.015
        )

    def test_load_config_prices_returns_empty_when_config_missing(self, tmp_path):
        assert mod.load_config_prices(str(tmp_path / "nope.yaml")) == {}

    def test_load_config_prices_is_fail_open_on_a_parse_error(self, tmp_path):
        config = tmp_path / "agent-config.yaml"
        config.write_text("llms: [broken\n")
        assert mod.load_config_prices(str(config)) == {}
