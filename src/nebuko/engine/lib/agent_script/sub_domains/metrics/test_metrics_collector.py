"""Tests for wiring captured token telemetry into the run Metrics (issue #829)."""

import time

import pytest

from agent_script.sub_domains.metrics import token_telemetry_collector as seam
from agent_script.sub_domains.metrics.metrics_collector import (
    _derive_tps_and_ttft,
    collect_llm_metrics,
)
from agent_script.sub_domains.metrics.token_telemetry_collector import (
    TokenTelemetryCollector,
    install,
)


class _Usage:
    def __init__(self, prompt_tokens=0, completion_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _Chunk:
    def __init__(self, usage=None):
        self.usage = usage


class _Combined:
    model_name = "test-model"
    response_latencies = []
    token_usages = []
    accumulated_token_usage = None
    accumulated_cost = 0.0


class _Stats:
    def get_combined_metrics(self):
        return _Combined()


class _State:
    events = []


class _Conversation:
    id = "run-1"
    conversation_stats = _Stats()
    state = _State()


class _TokenUsage:
    def __init__(self, reasoning_tokens=0):
        self.reasoning_tokens = reasoning_tokens


def _conversation_with(cache_hit_rate, token_usages):
    """A conversation whose combined metrics carry cache/reasoning signals."""
    combined = _Combined()
    combined.cache_hit_rate = cache_hit_rate
    combined.token_usages = token_usages

    class _Stats:
        def get_combined_metrics(self):
            return combined

    class _Conversation:
        id = "run-1"
        conversation_stats = _Stats()
        state = _State()

    return _Conversation()


@pytest.fixture(autouse=True)
def _reset_active_collector():
    previous = seam.get_active_collector()
    yield
    seam._active_collector = previous  # noqa: SLF001


def _record_one_call(collector):
    collector.log_pre_api_call(model="m", messages=[], kwargs={"litellm_call_id": "c1"})
    time.sleep(0.001)
    collector.on_token(_Chunk())  # first token
    time.sleep(0.001)
    collector.on_token(_Chunk(usage=_Usage(prompt_tokens=3, completion_tokens=9)))


class TestDeriveProviderHealth:
    def test_none_pair_without_a_collector(self):
        seam._active_collector = None  # noqa: SLF001
        assert _derive_tps_and_ttft() == (None, None)

    def test_none_pair_when_nothing_was_observed(self):
        install(TokenTelemetryCollector())
        assert _derive_tps_and_ttft() == (None, None)

    def test_returns_means_from_captured_calls(self):
        collector = TokenTelemetryCollector()
        install(collector)
        _record_one_call(collector)

        tps, ttft = _derive_tps_and_ttft()
        assert tps is not None and tps > 0
        assert ttft is not None and ttft > 0
        # Drain is destructive: the second read sees nothing.
        assert _derive_tps_and_ttft() == (None, None)


class TestCollectLlmMetricsProviderHealth:
    def test_populates_both_fields_from_the_collector(self):
        collector = TokenTelemetryCollector()
        install(collector)
        _record_one_call(collector)

        metrics = collect_llm_metrics(_Conversation())

        assert metrics.error is None
        assert metrics.tokens_per_second is not None
        assert metrics.time_to_first_token is not None

    def test_leaves_both_fields_unset_without_a_collector(self):
        seam._active_collector = None  # noqa: SLF001

        metrics = collect_llm_metrics(_Conversation())

        assert metrics.tokens_per_second is None
        assert metrics.time_to_first_token is None


class TestCollectLlmMetricsCacheAndReasoning:
    def test_populates_rate_and_reasoning_sum(self):
        conversation = _conversation_with(
            0.4819, [_TokenUsage(10000), _TokenUsage(170)]
        )

        metrics = collect_llm_metrics(conversation)

        assert metrics.error is None
        assert metrics.cache_hit_rate == pytest.approx(0.4819)
        assert metrics.reasoning_tokens == 10170

    def test_zero_when_no_signal_available(self):
        metrics = collect_llm_metrics(_Conversation())

        assert metrics.cache_hit_rate == 0.0
        assert metrics.reasoning_tokens == 0

    def test_none_rate_maps_to_zero(self):
        conversation = _conversation_with(None, [])

        metrics = collect_llm_metrics(conversation)

        assert metrics.cache_hit_rate == 0.0
        assert metrics.reasoning_tokens == 0
