"""Tests for the metrics cost-policy config entities (issue #714)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))

from config import (  # noqa: E402
    AppConfig,
    MetricsCostConfig,
    ProviderCostPolicy,
    load_config,
)

COMMANDCODE_CONFIG = Path(__file__).parents[5] / "agent-config-commandcode.yaml"


class TestProviderCostPolicy:
    """ProviderCostPolicy: per-token rates + subscription flag."""

    def test_parses_all_fields(self):
        policy = ProviderCostPolicy(
            provider="commandcode",
            input_cost_per_token=1.2e-07,
            output_cost_per_token=1.8e-07,
            subscription_backed=True,
        )
        assert policy.provider == "commandcode"
        assert policy.input_cost_per_token == pytest.approx(1.2e-07)
        assert policy.output_cost_per_token == pytest.approx(1.8e-07)
        assert policy.subscription_backed is True

    def test_subscription_backed_defaults_to_false(self):
        policy = ProviderCostPolicy(
            provider="openrouter",
            input_cost_per_token=1.0e-06,
            output_cost_per_token=2.0e-06,
        )
        assert policy.subscription_backed is False


class TestMetricsCostConfig:
    """MetricsCostConfig: policies keyed by provider + fallback switch."""

    def test_defaults_to_fallback_enabled_with_no_policies(self):
        config = MetricsCostConfig()
        assert config.provider_policies == {}
        assert config.fallback_to_local_pricing is True

    def test_indexes_policies_by_provider_name(self):
        config = MetricsCostConfig(
            provider_policies={
                "commandcode": ProviderCostPolicy(
                    provider="commandcode",
                    input_cost_per_token=1.2e-07,
                    output_cost_per_token=1.8e-07,
                    subscription_backed=True,
                )
            }
        )
        assert config.provider_policies["commandcode"].subscription_backed is True


class TestAppConfigMetricsCost:
    """AppConfig: the metrics_cost block defaults and parses from YAML."""

    def test_defaults_when_block_absent(self):
        config = AppConfig(providers={}, llms={}, agents={})
        assert config.metrics_cost.fallback_to_local_pricing is True
        assert config.metrics_cost.provider_policies == {}

    def test_parses_metrics_cost_block_from_yaml(self, tmp_path):
        config_file = tmp_path / "agent-config.yaml"
        config_file.write_text(
            "providers: {}\nllms: {}\nagents: {}\n"
            "metrics_cost:\n"
            "  fallback_to_local_pricing: true\n"
            "  provider_policies:\n"
            "    commandcode:\n"
            "      provider: commandcode\n"
            "      input_cost_per_token: 1.2e-07\n"
            "      output_cost_per_token: 1.8e-07\n"
            "      subscription_backed: true\n"
        )
        config = load_config(str(config_file))
        policy = config.metrics_cost.provider_policies["commandcode"]
        assert policy.subscription_backed is True
        assert policy.input_cost_per_token == pytest.approx(1.2e-07)


class TestCommandCodeConfigFile:
    """The shipped CommandCode config must declare its local pricing."""

    def test_declares_subscription_backed_policy(self):
        config = load_config(str(COMMANDCODE_CONFIG))
        assert config.metrics_cost.fallback_to_local_pricing is True
        policy = config.metrics_cost.provider_policies["commandcode"]
        assert policy.subscription_backed is True
        assert policy.input_cost_per_token > 0
        assert policy.output_cost_per_token > 0
