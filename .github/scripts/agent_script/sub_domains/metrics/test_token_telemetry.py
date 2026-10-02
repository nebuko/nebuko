"""Tests for provider-health telemetry derivation (issue #722)."""

from dataclasses import FrozenInstanceError

import pytest

from agent_script.sub_domains.metrics.token_telemetry import TokenTelemetry
from metrics_schema import Metrics


class TestTokenTelemetryDerivation:
    """TokenTelemetry: TTFT and generation throughput from raw call timings."""

    def test_derives_both_indicators_for_a_streamed_call(self):
        telemetry = TokenTelemetry(
            request_started_at=0.0,
            first_token_at=0.5,
            request_finished_at=2.5,
            prompt_tokens=100,
            completion_tokens=200,
        )
        assert telemetry.time_to_first_token() == pytest.approx(0.5)
        assert telemetry.tokens_per_second() == pytest.approx(100.0)

    def test_returns_none_indicators_when_first_token_was_not_observed(self):
        telemetry = TokenTelemetry(
            request_started_at=0.0,
            first_token_at=None,
            request_finished_at=2.5,
            prompt_tokens=100,
            completion_tokens=200,
        )
        assert telemetry.time_to_first_token() is None
        assert telemetry.tokens_per_second() is None

    def test_throughput_is_none_when_no_completion_tokens(self):
        telemetry = TokenTelemetry(
            request_started_at=0.0,
            first_token_at=0.5,
            request_finished_at=2.5,
            prompt_tokens=100,
            completion_tokens=0,
        )
        assert telemetry.tokens_per_second() is None

    def test_throughput_is_none_when_generation_window_is_not_positive(self):
        telemetry = TokenTelemetry(
            request_started_at=0.0,
            first_token_at=2.5,
            request_finished_at=2.5,
            prompt_tokens=100,
            completion_tokens=200,
        )
        assert telemetry.tokens_per_second() is None

    def test_is_immutable(self):
        telemetry = TokenTelemetry(
            request_started_at=0.0,
            first_token_at=0.5,
            request_finished_at=2.5,
            prompt_tokens=100,
            completion_tokens=200,
        )
        with pytest.raises(FrozenInstanceError):
            telemetry.first_token_at = 1.0


class TestMetricsProviderHealthFields:
    """Metrics: the two new optional fields stay backward compatible."""

    def test_defaults_to_none_and_is_omitted_when_unset(self):
        metrics = Metrics(run_id="run-1")

        assert metrics.tokens_per_second is None
        assert metrics.time_to_first_token is None
        serialized = metrics.to_dict()
        assert "tokens_per_second" not in serialized
        assert "time_to_first_token" not in serialized

    def test_loads_legacy_payload_without_the_new_fields(self):
        metrics = Metrics.from_json('{"run_id": "run-1", "input_tokens": 10}')

        assert metrics.run_id == "run-1"
        assert metrics.tokens_per_second is None
        assert metrics.time_to_first_token is None

    def test_round_trips_the_new_fields(self):
        metrics = Metrics(
            run_id="run-1",
            tokens_per_second=42.5,
            time_to_first_token=0.75,
        )

        decoded = Metrics.from_json(metrics.to_json())

        assert decoded.tokens_per_second == pytest.approx(42.5)
        assert decoded.time_to_first_token == pytest.approx(0.75)
