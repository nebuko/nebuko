"""Tests for the token-telemetry capture seam (issue #829)."""

import sys
import time
import types

import litellm
import pytest

from nebuko.engine.lib.agent_script.sub_domains.metrics import token_telemetry_collector as seam
from nebuko.engine.lib.agent_script.sub_domains.metrics.token_telemetry_collector import (
    CallStamps,
    TokenTelemetryCollector,
    install,
    maybe_register_langfuse,
)


class _PromptDetails:
    def __init__(self, cached_tokens=None, cache_write_tokens=None):
        self.cached_tokens = cached_tokens
        self.cache_write_tokens = cache_write_tokens


class _Usage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, prompt_tokens_details=None):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.prompt_tokens_details = prompt_tokens_details


class _Chunk:
    def __init__(self, usage=None):
        self.usage = usage


class _Response:
    def __init__(self, usage=None):
        self.usage = usage


def _start(collector, call_id):
    collector.log_pre_api_call(model="m", messages=[], kwargs={"litellm_call_id": call_id})


class TestTokenTelemetryCollector:
    """Collector: bracket one call, emit one observation from the stream."""

    def test_stream_chunks_yield_one_observation(self):
        collector = TokenTelemetryCollector()
        _start(collector, "call-1")
        time.sleep(0.001)  # separate the request-start and first-token stamps
        collector.on_token(_Chunk())
        time.sleep(0.001)
        collector.on_token(_Chunk(usage=_Usage(prompt_tokens=10, completion_tokens=4)))

        observations = collector.drain()
        assert len(observations) == 1
        obs = observations[0]
        assert obs.prompt_tokens == 10
        assert obs.completion_tokens == 4
        assert obs.time_to_first_token() is not None
        assert obs.tokens_per_second() is not None

    def test_first_chunk_stamps_first_token_and_final_chunk_closes(self):
        collector = TokenTelemetryCollector()
        _start(collector, "call-1")
        time.sleep(0.001)
        collector.on_token(_Chunk())
        time.sleep(0.001)
        # A usage-less chunk must not close the observation early.
        collector.on_token(_Chunk())
        assert collector.drain() == []
        assert len(collector._in_flight) == 1  # noqa: SLF001 — bracket still open

    def test_drain_returns_and_clears(self):
        collector = TokenTelemetryCollector()
        _start(collector, "call-1")
        collector.on_token(_Chunk(usage=_Usage(completion_tokens=1)))

        assert len(collector.drain()) == 1
        assert collector.drain() == []

    def test_failure_drops_the_in_flight_call(self):
        collector = TokenTelemetryCollector()
        _start(collector, "call-1")
        collector.on_token(_Chunk())  # first token observed, but the call then fails
        collector.log_failure_event(
            kwargs={"litellm_call_id": "call-1"}, response_obj=None, start_time=0, end_time=0
        )

        assert collector.drain() == []

    def test_success_event_records_a_non_streamed_call(self):
        collector = TokenTelemetryCollector()
        _start(collector, "call-1")
        collector.log_success_event(
            kwargs={"litellm_call_id": "call-1"},
            response_obj=_Response(usage=_Usage(prompt_tokens=3, completion_tokens=7)),
            start_time=0,
            end_time=1,
        )

        observations = collector.drain()
        assert len(observations) == 1
        assert observations[0].completion_tokens == 7
        assert observations[0].first_token_at is None
        assert observations[0].tokens_per_second() is None

    def test_success_event_does_not_double_count_a_streamed_call(self):
        collector = TokenTelemetryCollector()
        _start(collector, "call-1")
        collector.on_token(_Chunk(usage=_Usage(completion_tokens=2)))
        collector.log_success_event(
            kwargs={"litellm_call_id": "call-1"},
            response_obj=_Response(usage=_Usage(completion_tokens=2)),
            start_time=0,
            end_time=1,
        )

        assert len(collector.drain()) == 1

    def test_chunks_without_a_bracket_are_ignored(self):
        collector = TokenTelemetryCollector()
        collector.on_token(_Chunk(usage=_Usage(completion_tokens=2)))
        assert collector.drain() == []


class TestCallStamps:
    def test_first_token_defaults_to_none(self):
        assert CallStamps(request_started_at=1.0).first_token_at is None


class TestInstall:
    def test_install_sets_active_collector_and_subscribes(self, monkeypatch):
        previous_active = seam.get_active_collector()
        previous_callbacks = list(litellm.callbacks)
        try:
            collector = TokenTelemetryCollector()
            install(collector)
            assert seam.get_active_collector() is collector
            assert collector in litellm.callbacks
            install(collector)  # idempotent — no duplicate subscription
            assert litellm.callbacks.count(collector) == 1
        finally:
            seam._active_collector = previous_active  # noqa: SLF001
            litellm.callbacks = previous_callbacks


class TestMaybeRegisterLangfuse:
    """Registration is default-off and pure configuration."""

    def test_noop_without_env(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        before = list(litellm.success_callback)
        maybe_register_langfuse()
        assert litellm.success_callback == before

    def test_noop_when_keys_set_but_package_absent(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        monkeypatch.delitem(sys.modules, "langfuse", raising=False)
        before = list(litellm.success_callback)
        maybe_register_langfuse()
        assert "langfuse" not in litellm.success_callback
        assert litellm.success_callback == before

    def test_registers_when_keys_and_package_are_present(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        # langfuse is an optional extra and may be absent in CI; a stub module
        # lets us exercise the gate itself rather than the third-party package.
        monkeypatch.setitem(sys.modules, "langfuse", types.ModuleType("langfuse"))
        before = list(litellm.success_callback)
        try:
            maybe_register_langfuse()
            assert "langfuse" in litellm.success_callback
            maybe_register_langfuse()  # idempotent
            assert litellm.success_callback.count("langfuse") == 1
        finally:
            if "langfuse" in litellm.success_callback:
                litellm.success_callback.remove("langfuse")
            assert litellm.success_callback == before


class TestCacheTokenCapture:
    """The capture seam carries the provider's cache buckets (issue #714)."""

    def test_cache_read_and_write_are_captured(self):
        collector = TokenTelemetryCollector()
        _start(collector, "call-1")
        collector.on_token(
            _Chunk(
                usage=_Usage(
                    prompt_tokens=100,
                    completion_tokens=5,
                    prompt_tokens_details=_PromptDetails(
                        cached_tokens=40, cache_write_tokens=10
                    ),
                )
            )
        )

        obs = collector.drain()[0]
        assert obs.cache_read_tokens == 40
        assert obs.cache_write_tokens == 10

    def test_absent_cache_details_default_to_zero(self):
        collector = TokenTelemetryCollector()
        _start(collector, "call-1")
        collector.on_token(_Chunk(usage=_Usage(prompt_tokens=10, completion_tokens=1)))

        obs = collector.drain()[0]
        assert obs.cache_read_tokens == 0
        assert obs.cache_write_tokens == 0

    def test_private_cache_attrs_are_a_fallback(self):
        class _PrivateUsage:
            prompt_tokens = 50
            completion_tokens = 2
            prompt_tokens_details = None
            _cache_read_input_tokens = 20
            _cache_creation_input_tokens = 7

        collector = TokenTelemetryCollector()
        _start(collector, "call-1")
        collector.on_token(_Chunk(usage=_PrivateUsage()))

        obs = collector.drain()[0]
        assert obs.cache_read_tokens == 20
        assert obs.cache_write_tokens == 7
