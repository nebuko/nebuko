"""Tests for report-time cost resolution (issue #928).

No network: the price table is stubbed, or loaded through an injected fetcher
pointed at a tmp cache dir.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from agent_script.sub_domains.metrics import report_cost_resolver as mod
from agent_script.sub_domains.metrics.report_cost_resolver import (
    PLACEHOLDER_MODEL,
    ReportCostResolver,
)
from metrics_schema import Metrics
from pricing.sub_domains.price_table.price_table import ModelPrice, PriceTable

REPO_LIB = Path(__file__).resolve().parents[4]


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


class TestBaseDepsImportability:
    """The job installs base deps only, so the resolver chain must not need
    ``openhands``/``litellm`` (issue #928)."""

    def test_resolver_imports_with_openhands_and_litellm_blocked(self):
        script = textwrap.dedent(
            """
            import builtins, sys
            sys.path.insert(0, {lib!r})
            real_import = builtins.__import__
            def guarded(name, *a, **k):
                if name.split('.')[0] in ('openhands', 'litellm'):
                    raise ImportError('blocked ' + name)
                return real_import(name, *a, **k)
            builtins.__import__ = guarded
            from agent_script.sub_domains.metrics.report_cost_resolver import (
                ReportCostResolver,
            )
            from pricing.features.load_price_table.load_price_table import (
                load_price_table,
            )
            print(ReportCostResolver, load_price_table)
            """
        ).format(lib=str(REPO_LIB))
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
