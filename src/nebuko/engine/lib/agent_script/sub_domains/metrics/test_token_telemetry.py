"""Tests for provider-health telemetry derivation (issue #722)."""

from dataclasses import FrozenInstanceError

import pytest

from nebuko.engine.lib.agent_script.sub_domains.metrics.token_telemetry import (
    RetryEvent,
    TokenTelemetry,
)
from nebuko.engine.lib.metrics_schema import Metrics


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

    def test_ttft_is_none_when_offset_is_not_positive(self):
        telemetry = TokenTelemetry(
            request_started_at=2.5,
            first_token_at=2.5,
            request_finished_at=3.0,
            prompt_tokens=100,
            completion_tokens=200,
        )
        assert telemetry.time_to_first_token() is None

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

    def test_duration_is_the_full_call_window(self):
        telemetry = TokenTelemetry(
            request_started_at=10.0,
            first_token_at=11.0,
            request_finished_at=14.5,
            prompt_tokens=1,
            completion_tokens=1,
        )
        assert telemetry.duration_s() == pytest.approx(4.5)

    def test_duration_is_none_when_unordered(self):
        telemetry = TokenTelemetry(
            request_started_at=5.0,
            first_token_at=None,
            request_finished_at=5.0,
            prompt_tokens=1,
            completion_tokens=1,
        )
        assert telemetry.duration_s() is None


class TestCallHealth:
    """Stall and retry indicators derived from a run's calls (issue #1352)."""

    @staticmethod
    def _observation(duration, retry_count=0):
        return TokenTelemetry(
            request_started_at=0.0,
            first_token_at=None,
            request_finished_at=duration,
            prompt_tokens=1,
            completion_tokens=1,
            retry_count=retry_count,
        )

    def test_empty_run_reports_no_latency(self):
        health = TokenTelemetry.call_health([])
        assert health.max_call_latency_ms is None
        assert health.stall_count == 0
        assert health.retry_count == 0
        assert health.call_retry_counts == ()

    def test_reports_max_latency_in_milliseconds(self):
        health = TokenTelemetry.call_health(
            [self._observation(2.0), self._observation(12.5)]
        )
        assert health.max_call_latency_ms == pytest.approx(12500.0)

    def test_counts_calls_over_the_stall_threshold(self):
        health = TokenTelemetry.call_health(
            [self._observation(59.0), self._observation(61.0), self._observation(300.0)]
        )
        assert health.stall_count == 2

    def test_threshold_is_configurable(self):
        health = TokenTelemetry.call_health(
            [self._observation(30.0)], stall_threshold_s=10.0
        )
        assert health.stall_count == 1

    def test_counts_every_retry_event_even_when_the_attempt_failed(self):
        health = TokenTelemetry.call_health(
            [self._observation(2.0, retry_count=1)],
            retries=[RetryEvent(1, 1, "Timeout"), RetryEvent(2, 1, "Timeout")],
        )
        assert health.retry_count == 2
        assert health.call_retry_counts == (1,)


class TestTokenTelemetryAggregate:
    """TokenTelemetry.aggregate: mean of each metric over observable calls."""

    @staticmethod
    def _observation(ttft, span, completion_tokens):
        """Build an observation whose TTFT/throughput are the given values."""
        trace = 0.0
        first = trace + ttft
        return TokenTelemetry(
            request_started_at=trace,
            first_token_at=first,
            request_finished_at=first + span,
            prompt_tokens=1,
            completion_tokens=completion_tokens,
        )

    def test_returns_none_pair_for_no_observations(self):
        assert TokenTelemetry.aggregate([]) == (None, None)

    def test_means_each_metric_over_its_observable_calls(self):
        # TTFTs: 0.4, 0.8 → mean 0.6; throughputs: 100, 300 → mean 200.
        observations = [
            self._observation(ttft=0.4, span=1.0, completion_tokens=100),
            self._observation(ttft=0.8, span=1.0, completion_tokens=300),
        ]
        mean_ttft, mean_tps = TokenTelemetry.aggregate(observations)
        assert mean_ttft == pytest.approx(0.6)
        assert mean_tps == pytest.approx(200.0)

    def test_a_metric_is_skipped_when_unobservable_on_some_calls(self):
        # Second call has no completion tokens → no throughput, but its TTFT counts.
        observations = [
            self._observation(ttft=0.2, span=1.0, completion_tokens=50),
            self._observation(ttft=0.6, span=1.0, completion_tokens=0),
        ]
        mean_ttft, mean_tps = TokenTelemetry.aggregate(observations)
        assert mean_ttft == pytest.approx(0.4)
        assert mean_tps == pytest.approx(50.0)

    def test_returns_none_metric_when_no_call_was_observable(self):
        unobservable = TokenTelemetry(
            request_started_at=0.0,
            first_token_at=None,
            request_finished_at=1.0,
            prompt_tokens=5,
            completion_tokens=0,
        )
        assert TokenTelemetry.aggregate([unobservable]) == (None, None)


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


class TestMetricsStallAndRetryFields:
    """Metrics: stall/retry counters stay backward compatible (issue #1352)."""

    def test_defaults_are_empty_and_omitted_when_unset(self):
        metrics = Metrics(run_id="run-1")

        assert metrics.llm_retry_count == 0
        assert metrics.llm_max_call_latency_ms is None
        assert metrics.llm_stall_count == 0
        assert metrics.llm_call_retry_counts == []
        serialized = metrics.to_dict()
        assert "llm_max_call_latency_ms" not in serialized

    def test_loads_legacy_payload_without_the_new_fields(self):
        metrics = Metrics.from_json('{"run_id": "run-1", "input_tokens": 10}')

        assert metrics.llm_retry_count == 0
        assert metrics.llm_stall_count == 0
        assert metrics.llm_call_retry_counts == []

    def test_round_trips_the_new_fields(self):
        metrics = Metrics(
            run_id="run-1",
            llm_retry_count=2,
            llm_max_call_latency_ms=315000.0,
            llm_stall_count=1,
            llm_call_retry_counts=[1, 0, 1],
        )

        decoded = Metrics.from_json(metrics.to_json())

        assert decoded.llm_retry_count == 2
        assert decoded.llm_max_call_latency_ms == pytest.approx(315000.0)
        assert decoded.llm_stall_count == 1
        assert decoded.llm_call_retry_counts == [1, 0, 1]
