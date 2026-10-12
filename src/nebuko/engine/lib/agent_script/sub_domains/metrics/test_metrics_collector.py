"""Tests for wiring captured token telemetry into the run Metrics (issue #829)."""

import time

import pytest

from agent_script.sub_domains.metrics import token_telemetry_collector as seam
from agent_script.sub_domains.metrics.metrics_collector import (
    _cache_token_counts,
    _CapturedTelemetry,
    _drain_captured_telemetry,
    collect_llm_metrics,
)
from agent_script.sub_domains.metrics.token_telemetry_collector import (
    TokenTelemetryCollector,
    install,
)


class _Usage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, prompt_tokens_details=None):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.prompt_tokens_details = prompt_tokens_details


class _PromptDetails:
    def __init__(self, cached_tokens=None, cache_write_tokens=None):
        self.cached_tokens = cached_tokens
        self.cache_write_tokens = cache_write_tokens


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


def _record_one_call(collector, usage=None):
    collector.log_pre_api_call(model="m", messages=[], kwargs={"litellm_call_id": "c1"})
    time.sleep(0.001)
    collector.on_token(_Chunk())  # first token
    time.sleep(0.001)
    if usage is None:
        usage = _Usage(prompt_tokens=3, completion_tokens=9)
    collector.on_token(_Chunk(usage=usage))


class TestDrainCapturedTelemetry:
    def test_empty_without_a_collector(self):
        seam._active_collector = None  # noqa: SLF001
        captured = _drain_captured_telemetry()
        assert captured.tokens_per_second is None
        assert captured.time_to_first_token is None
        assert captured.cache_write_tokens == 0

    def test_carries_means_and_cache_totals(self):
        collector = TokenTelemetryCollector()
        install(collector)
        _record_one_call(
            collector,
            _Usage(
                prompt_tokens=100,
                completion_tokens=5,
                prompt_tokens_details=_PromptDetails(
                    cached_tokens=40, cache_write_tokens=10
                ),
            ),
        )

        captured = _drain_captured_telemetry()
        assert captured.tokens_per_second is not None and captured.tokens_per_second > 0
        assert captured.time_to_first_token is not None
        assert captured.cache_read_tokens == 40
        assert captured.cache_write_tokens == 10


class TestCacheTokenCounts:
    """The SDK's silent cache-write bucket is filled from the capture seam."""

    class _Accumulated:
        def __init__(self, cache_read_tokens=0, cache_write_tokens=0):
            self.cache_read_tokens = cache_read_tokens
            self.cache_write_tokens = cache_write_tokens

    def test_sdk_count_wins_when_it_saw_more(self):
        read, write = _cache_token_counts(
            self._Accumulated(cache_read_tokens=90, cache_write_tokens=50),
            _CapturedTelemetry(None, None, cache_read_tokens=40, cache_write_tokens=10),
        )
        assert (read, write) == (90, 50)

    def test_capture_fills_the_sdk_silent_cache_write(self):
        # The SDK's cache_write_tokens is 0 for OpenAI-shaped responses; the
        # capture seam reads litellm's cache_write_tokens correctly.
        read, write = _cache_token_counts(
            self._Accumulated(cache_read_tokens=40, cache_write_tokens=0),
            _CapturedTelemetry(None, None, cache_read_tokens=0, cache_write_tokens=10),
        )
        assert (read, write) == (40, 10)

    def test_no_capture_leaves_sdk_counts(self):
        read, write = _cache_token_counts(self._Accumulated(7, 3), _CapturedTelemetry(None, None))
        assert (read, write) == (7, 3)


class TestDeriveProviderHealth:
    def test_none_pair_without_a_collector(self):
        seam._active_collector = None  # noqa: SLF001
        captured = _drain_captured_telemetry()
        assert (captured.tokens_per_second, captured.time_to_first_token) == (None, None)

    def test_none_pair_when_nothing_was_observed(self):
        install(TokenTelemetryCollector())
        captured = _drain_captured_telemetry()
        assert (captured.tokens_per_second, captured.time_to_first_token) == (None, None)

    def test_returns_means_from_captured_calls(self):
        collector = TokenTelemetryCollector()
        install(collector)
        _record_one_call(collector)

        captured = _drain_captured_telemetry()
        assert captured.tokens_per_second is not None and captured.tokens_per_second > 0
        assert captured.time_to_first_token is not None and captured.time_to_first_token > 0
        # Drain is destructive: the second read sees nothing.
        second = _drain_captured_telemetry()
        assert (second.tokens_per_second, second.time_to_first_token) == (None, None)


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


class _AccumulatedUsage:
    context_window = 0

    def __init__(
        self,
        prompt_tokens=0,
        completion_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    ):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_write_tokens = cache_write_tokens


def _conversation_with_usage(usage):
    combined = _Combined()
    combined.accumulated_token_usage = usage

    class _Stats:
        def get_combined_metrics(self):
            return combined

    class _Conversation:
        id = "run-1"
        conversation_stats = _Stats()
        state = _State()

    return _Conversation()


class TestCollectLlmMetricsCacheWriteFromCapture:
    """End-to-end: the capture seam prices the SDK's silent cache-write tier."""

    def _table_and_ref(self):
        from types import SimpleNamespace

        from pricing.sub_domains.price_table.price_table import PriceTable

        table = PriceTable.from_openrouter(
            {
                "data": [
                    {
                        "id": "deepseek/deepseek-v4.1-flash",
                        "pricing": {
                            "prompt": "0.000001",  # $1 / 1M input
                            "completion": "0.000008",  # $8 / 1M output
                            "input_cache_write": "0.000004",  # $4 / 1M write
                        },
                    }
                ]
            }
        )
        ref = SimpleNamespace(model_path="openai/deepseek/deepseek-v4.1-flash")
        cfg = SimpleNamespace(model_ref=ref, complexity_overrides={})
        return table, cfg

    def test_capture_cache_write_is_priced_when_the_sdk_reports_zero(self):
        import os

        table, cfg = self._table_and_ref()
        # SDK accumulation is cache-blind for writes (litellm field mismatch).
        usage = _AccumulatedUsage(prompt_tokens=1_000_000, cache_write_tokens=0)
        collector = TokenTelemetryCollector()
        install(collector)
        _record_one_call(
            collector,
            _Usage(
                prompt_tokens=1_000_000,
                prompt_tokens_details=_PromptDetails(cache_write_tokens=200_000),
            ),
        )

        os.environ.pop("CONFIG_COMPLEXITY", None)
        metrics = collect_llm_metrics(_conversation_with_usage(usage), cfg, table)

        assert metrics.error is None
        # 800k regular input @ $1/1M + 200k cache-write @ $4/1M = $1.60
        assert metrics.cost == pytest.approx(1.6)
