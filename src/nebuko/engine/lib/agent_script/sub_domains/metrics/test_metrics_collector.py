"""Tests for wiring captured token telemetry into the run Metrics (issue #829)."""

import time
from unittest.mock import MagicMock

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
    def __init__(
        self,
        reasoning_tokens=0,
        prompt_tokens=0,
        completion_tokens=0,
        cache_read_tokens=0,
    ):
        self.reasoning_tokens = reasoning_tokens
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cache_read_tokens = cache_read_tokens


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

    def test_populates_root_cache_hit_tokens_from_accumulated_usage(self):
        conversation = _conversation_with_usage(
            _AccumulatedUsage(prompt_tokens=10000, cache_read_tokens=4820)
        )

        metrics = collect_llm_metrics(conversation)

        assert metrics.input_tokens == 10000
        assert metrics.cache_hit_tokens == 4820

    def test_root_cache_hit_tokens_default_to_zero(self):
        metrics = collect_llm_metrics(_Conversation())

        assert metrics.cache_hit_tokens == 0

    def test_tokens_per_tool_accumulates_every_call(self):
        """Each usage adds into its bucket instead of overwriting the previous."""
        conversation = _conversation_with(
            0.0,
            [
                _TokenUsage(
                    prompt_tokens=1000, completion_tokens=100,
                    cache_read_tokens=400, reasoning_tokens=10,
                ),
                _TokenUsage(
                    prompt_tokens=2000, completion_tokens=200,
                    cache_read_tokens=800, reasoning_tokens=30,
                ),
            ],
        )

        metrics = collect_llm_metrics(conversation)

        # No tool attribution yet, so both calls land in the "unknown" bucket —
        # but the bucket must now hold the SUM of both calls, not just the last.
        assert metrics.tokens_per_tool == {
            "unknown": {
                "input": 3000,
                "output": 300,
                "cache_hit": 1200,
                "reasoning": 40,
            }
        }


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


class TestEffectiveModelNameRecorded:
    """The run records the effective model identity, not the SDK placeholder.

    The summarize job prices zero-cost entries by this identity, so a run with
    a resolvable model_ref must persist the real model path instead of
    ``"default"`` (issue #928).
    """

    def _cfg(self, model_path="openai/deepseek/deepseek-v4.1-flash"):
        from types import SimpleNamespace

        ref = SimpleNamespace(model_path=model_path)
        return SimpleNamespace(model_ref=ref, complexity_overrides={})

    def test_records_the_config_model_path(self):
        import os

        usage = _AccumulatedUsage(prompt_tokens=10, completion_tokens=5)
        os.environ.pop("CONFIG_COMPLEXITY", None)
        metrics = collect_llm_metrics(_conversation_with_usage(usage), self._cfg())
        assert metrics.model_name == "openai/deepseek/deepseek-v4.1-flash"

    def test_falls_back_to_the_sdk_value_without_config(self):
        import os

        usage = _AccumulatedUsage(prompt_tokens=10, completion_tokens=5)
        os.environ.pop("CONFIG_COMPLEXITY", None)
        metrics = collect_llm_metrics(_conversation_with_usage(usage), None)
        assert metrics.model_name == "test-model"


def _make_action(action_id, tool_name, timestamp, source="agent"):
    from openhands.sdk.event.llm_convertible.action import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall

    return ActionEvent(
        id=action_id,
        source=source,
        tool_name=tool_name,
        tool_call_id=f"call_{action_id}",
        thought=[],
        timestamp=timestamp,
        tool_call=MessageToolCall(
            id=f"call_{action_id}", name=tool_name, arguments="{}", origin="completion"
        ),
        llm_response_id=f"resp_{action_id}",
        summary="",
    )


def _make_observation(obs_id, action_id, tool_name, timestamp, is_error=False):
    from openhands.sdk.event.llm_convertible.observation import ObservationEvent

    # model_construct bypasses the discriminated-union validation of observation.
    return ObservationEvent.model_construct(
        id=obs_id,
        source="environment",
        tool_name=tool_name,
        tool_call_id=f"call_{action_id}",
        action_id=action_id,
        timestamp=timestamp,
        observation=MagicMock(is_error=is_error) if is_error else None,
    )


def _conversation_with_events(events):
    class _State:
        pass

    class _Conversation:
        id = "run-1"
        conversation_stats = _Stats()
        state = _State()

    _Conversation.state.events = events
    return _Conversation()


class TestToolExecutionTime:
    """Per-tool execution time from action->observation deltas (issue #1016)."""

    def test_matched_pair_records_total_and_count(self):
        events = [
            _make_action("a1", "terminal", "2026-01-01T00:00:00"),
            _make_observation("o1", "a1", "terminal", "2026-01-01T00:00:00.250000"),
        ]

        metrics = collect_llm_metrics(_conversation_with_events(events))

        assert metrics.tool_time_per_tool == {"terminal": {"total_ms": 250.0, "count": 1}}
        assert metrics.tool_runtime_ms == 250.0

    def test_multiple_calls_accumulate_in_one_bucket(self):
        events = [
            _make_action("a1", "terminal", "2026-01-01T00:00:00"),
            _make_observation("o1", "a1", "terminal", "2026-01-01T00:00:00.100000"),
            _make_action("a2", "terminal", "2026-01-01T00:00:01"),
            _make_observation("o2", "a2", "terminal", "2026-01-01T00:00:01.300000"),
        ]

        metrics = collect_llm_metrics(_conversation_with_events(events))

        assert metrics.tool_time_per_tool["terminal"] == {"total_ms": 400.0, "count": 2}
        assert metrics.tool_runtime_ms == 400.0

    def test_unmatched_action_and_observation_are_skipped(self):
        events = [
            _make_action("a1", "terminal", "2026-01-01T00:00:00"),
            _make_observation("o1", "a_missing", "terminal", "2026-01-01T00:00:00.500000"),
        ]

        metrics = collect_llm_metrics(_conversation_with_events(events))

        assert metrics.tool_time_per_tool == {}
        assert metrics.tool_runtime_ms == 0.0

    def test_parallel_calls_overlap_and_sum_above_wall_clock(self):
        """Busy-time sums: each overlapping call counts in full."""
        events = [
            _make_action("a1", "terminal", "2026-01-01T00:00:00"),
            _make_action("a2", "bash", "2026-01-01T00:00:00"),
            _make_observation("o1", "a1", "terminal", "2026-01-01T00:00:00.600000"),
            _make_observation("o2", "a2", "bash", "2026-01-01T00:00:00.600000"),
        ]

        metrics = collect_llm_metrics(_conversation_with_events(events))

        assert metrics.tool_time_per_tool == {
            "terminal": {"total_ms": 600.0, "count": 1},
            "bash": {"total_ms": 600.0, "count": 1},
        }
        assert metrics.tool_runtime_ms == 1200.0
        # Both calls span the same 600 ms window, so busy time exceeds the
        # 600 ms of wall clock the two events cover.
        assert metrics.tool_runtime_ms > 600.0

    def test_user_sourced_actions_are_ignored(self):
        events = [
            _make_action("a1", "terminal", "2026-01-01T00:00:00", source="user"),
            _make_observation("o1", "a1", "terminal", "2026-01-01T00:00:00.400000"),
        ]

        metrics = collect_llm_metrics(_conversation_with_events(events))

        assert metrics.tool_time_per_tool == {}

    def test_wall_clock_spans_start_and_end(self):
        events = [
            _make_action("a1", "terminal", "2026-01-01T00:00:00"),
            _make_observation("o1", "a1", "terminal", "2026-01-01T00:00:00.100000"),
        ]

        metrics = collect_llm_metrics(_conversation_with_events(events))

        assert metrics.wall_clock_ms >= 100.0
        assert metrics.end_time

    def test_no_events_yields_defaults(self):
        metrics = collect_llm_metrics(_Conversation())

        assert metrics.tool_time_per_tool == {}
        assert metrics.tool_runtime_ms == 0.0
        # No events: start_time falls back to now, so wall clock is ~0.
        assert metrics.wall_clock_ms >= 0.0

    def test_negative_delta_clamps_to_zero(self):
        from agent_script.sub_domains.metrics.metrics_collector import _duration_ms

        assert _duration_ms("2026-01-01T00:00:05", "2026-01-01T00:00:00") == 0.0

    def test_mixed_naive_and_aware_timestamps_subtract(self):
        from agent_script.sub_domains.metrics.metrics_collector import _duration_ms

        naive = "2026-01-01T00:00:00"
        aware = "2026-01-01T00:00:01+00:00"
        # Naive is read as local time, so the delta is finite either way.
        assert _duration_ms(naive, aware) >= 0.0

    def test_unparseable_timestamps_yield_zero(self):
        from agent_script.sub_domains.metrics.metrics_collector import _duration_ms

        assert _duration_ms("not-a-date", "2026-01-01T00:00:00") == 0.0
        assert _duration_ms(None, None) == 0.0


class TestErrorsPerTool:
    """Error observations are attributed through action_id -> ActionEvent.id."""

    def test_error_counted_under_matching_action_tool_name(self):
        events = [
            _make_action("a1", "terminal", "2026-01-01T00:00:00"),
            _make_observation(
                "o1", "a1", "terminal", "2026-01-01T00:00:00.250000", is_error=True
            ),
        ]

        metrics = collect_llm_metrics(_conversation_with_events(events))

        assert metrics.errors_per_tool == {"terminal": 1}

    def test_orphan_error_observation_falls_back_to_unknown(self):
        events = [
            _make_observation(
                "o1", "missing", "terminal", "2026-01-01T00:00:00.250000", is_error=True
            ),
        ]

        metrics = collect_llm_metrics(_conversation_with_events(events))

        assert metrics.errors_per_tool == {"unknown": 1}


class TestModelExecutionConfigCaptured:
    """The applied model execution config is recorded for audit/replay (#820)."""

    def _cfg(self, **overrides):
        from types import SimpleNamespace

        ref = SimpleNamespace(
            model_path="openai/deepseek/deepseek-v4.1-flash",
            provider_ref=SimpleNamespace(id="openrouter"),
        )
        cfg = SimpleNamespace(
            model_ref=ref,
            complexity_overrides={},
            temperature=0.3,
            top_p=None,
            top_k=None,
            min_p=None,
            presence_penalty=None,
            repetition_penalty=None,
            stop_sequences=None,
        )
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg

    def test_resolved_llm_config_wins(self):
        import os

        os.environ.pop("CONFIG_COMPLEXITY", None)
        usage = _AccumulatedUsage(prompt_tokens=10, completion_tokens=5)
        metrics = collect_llm_metrics(
            _conversation_with_usage(usage),
            self._cfg(top_p=0.9),
            None,
            llm_config={
                "model": "openai/glm-4.6",
                "temperature": 0.7,
                "litellm_extra_body": {
                    "top_p": 0.8,
                    "top_k": 40,
                    "stop": ["</answer>"],
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                "reasoning_effort": "none",
                "max_output_tokens": 8192,
            },
        )

        config = metrics.model_execution_config
        assert config["model_path"] == "openai/glm-4.6"
        assert config["temperature"] == 0.7
        assert config["top_p"] == 0.8
        assert config["top_k"] == 40
        assert config["stop_sequences"] == ["</answer>"]
        assert config["thinking_enabled"] is False
        assert config["reasoning_effort"] == "none"
        assert config["max_output_tokens"] == 8192
        assert config["provider"] == "openrouter"

    def test_falls_back_to_agent_config(self):
        import os

        os.environ.pop("CONFIG_COMPLEXITY", None)
        usage = _AccumulatedUsage(prompt_tokens=10, completion_tokens=5)
        metrics = collect_llm_metrics(
            _conversation_with_usage(usage), self._cfg(stop_sequences=["STOP"])
        )

        config = metrics.model_execution_config
        assert config["model_path"] == "openai/deepseek/deepseek-v4.1-flash"
        assert config["temperature"] == 0.3
        assert config["stop_sequences"] == ["STOP"]
        assert config["model_source"] == "base"

    def test_records_complexity_override_source(self):
        import os
        from types import SimpleNamespace

        os.environ["CONFIG_COMPLEXITY"] = "high"
        cfg = self._cfg()
        cfg.complexity_overrides = {
            "high": SimpleNamespace(model_ref=SimpleNamespace(model_path="openai/glm-4.6"))
        }
        usage = _AccumulatedUsage(prompt_tokens=10, completion_tokens=5)

        try:
            metrics = collect_llm_metrics(_conversation_with_usage(usage), cfg)
        finally:
            os.environ.pop("CONFIG_COMPLEXITY", None)

        config = metrics.model_execution_config
        assert config["model_source"] == "complexity_override"
        assert config["complexity_tier"] == "high"
        assert config["model_path"] == "openai/glm-4.6"

    def test_none_when_no_config_available(self):
        import os

        os.environ.pop("CONFIG_COMPLEXITY", None)
        usage = _AccumulatedUsage(prompt_tokens=10, completion_tokens=5)
        metrics = collect_llm_metrics(_conversation_with_usage(usage))

        assert metrics.model_execution_config is None

