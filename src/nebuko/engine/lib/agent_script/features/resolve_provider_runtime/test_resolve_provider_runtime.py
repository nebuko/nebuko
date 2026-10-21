"""Tests for resolve_provider_runtime — the per-call provider HTTP decoration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from config import ProviderConfig

from agent_script.features.resolve_provider_runtime.resolve_provider_runtime import (
    SESSION_ID_ENV,
    IssueCacheKeyRuntime,
    IssueSessionRuntime,
    StaticProviderRuntime,
    build_provider_runtime,
)


def _provider(**overrides) -> ProviderConfig:
    base = {"api_url": "https://opencode.ai/zen/go/v1", "api_token_env": "KEY"}
    base.update(overrides)
    return ProviderConfig(**base)


class TestStaticProviderRuntime:
    def test_forwards_user_agent_and_static_headers_verbatim(self):
        runtime = StaticProviderRuntime(
            _provider(user_agent="nebuko/1.0", extra_headers={"X-Static": "1"})
        )
        assert runtime.extra_headers() == {
            "User-Agent": "nebuko/1.0",
            "X-Static": "1",
        }

    def test_omits_user_agent_when_unset(self):
        assert StaticProviderRuntime(_provider()).extra_headers() == {}

    def test_contributes_no_body_params(self):
        assert StaticProviderRuntime(_provider()).extra_body() == {}


class TestIssueSessionRuntime:
    def test_stamps_the_declared_header_with_the_engine_id(self):
        runtime = IssueSessionRuntime("x-opencode-session", "854")
        assert runtime.extra_headers() == {"x-opencode-session": "854"}

    def test_contributes_no_body_params(self):
        assert IssueSessionRuntime("x-opencode-session", "854").extra_body() == {}


class TestIssueCacheKeyRuntime:
    def test_stamps_the_declared_body_param_with_the_engine_id(self):
        runtime = IssueCacheKeyRuntime("prompt_cache_key", "1068")
        assert runtime.extra_body() == {"prompt_cache_key": "1068"}

    def test_contributes_no_headers(self):
        assert IssueCacheKeyRuntime("prompt_cache_key", "1068").extra_headers() == {}


class TestStampedRequestDecoration:
    def test_stamped_env_flows_into_the_provider_headers(self):
        provider = _provider(
            user_agent="nebuko/1.0", session_header="x-opencode-session"
        )
        headers: dict[str, str] = {}
        for runtime in build_provider_runtime(provider, {SESSION_ID_ENV: "854"}):
            headers.update(runtime.extra_headers())
        assert headers["x-opencode-session"] == "854"
        assert headers["User-Agent"] == "nebuko/1.0"


class TestBuildProviderRuntime:
    """The four cases: (name, env) → exactly the expected header source list."""

    def test_no_name_no_env_yields_static_only(self):
        runtimes = build_provider_runtime(_provider(), {})
        assert [type(r) for r in runtimes] == [StaticProviderRuntime]

    def test_env_without_a_declared_name_sends_nothing(self):
        runtimes = build_provider_runtime(_provider(), {SESSION_ID_ENV: "854"})
        assert [type(r) for r in runtimes] == [StaticProviderRuntime]

    def test_declared_name_without_an_env_id_sends_nothing(self):
        runtimes = build_provider_runtime(
            _provider(session_header="x-opencode-session"), {}
        )
        assert [type(r) for r in runtimes] == [StaticProviderRuntime]

    def test_name_and_env_append_the_session_runtime_last(self):
        runtimes = build_provider_runtime(
            _provider(session_header="x-opencode-session"),
            {SESSION_ID_ENV: "854"},
        )
        assert [type(r) for r in runtimes] == [
            StaticProviderRuntime,
            IssueSessionRuntime,
        ]


class TestBuildProviderRuntimeCacheKey:
    """Body-param gating mirrors the header gating: name and env both required."""

    def test_env_without_a_declared_name_sends_nothing(self):
        runtimes = build_provider_runtime(_provider(), {SESSION_ID_ENV: "1068"})
        assert [type(r) for r in runtimes] == [StaticProviderRuntime]

    def test_declared_name_without_an_env_id_sends_nothing(self):
        runtimes = build_provider_runtime(
            _provider(cache_key_param="prompt_cache_key"), {}
        )
        assert [type(r) for r in runtimes] == [StaticProviderRuntime]

    def test_name_and_env_append_the_cache_key_runtime(self):
        runtimes = build_provider_runtime(
            _provider(cache_key_param="prompt_cache_key"), {SESSION_ID_ENV: "1068"}
        )
        assert [type(r) for r in runtimes] == [
            StaticProviderRuntime,
            IssueCacheKeyRuntime,
        ]

    def test_session_runtime_precedes_the_cache_key_runtime(self):
        provider = _provider(
            session_header="x-opencode-session", cache_key_param="prompt_cache_key"
        )
        runtimes = build_provider_runtime(provider, {SESSION_ID_ENV: "1068"})
        assert [type(r) for r in runtimes] == [
            StaticProviderRuntime,
            IssueSessionRuntime,
            IssueCacheKeyRuntime,
        ]

    def test_stamped_env_flows_into_the_request_body(self):
        provider = _provider(cache_key_param="prompt_cache_key")
        body: dict = {}
        for runtime in build_provider_runtime(provider, {SESSION_ID_ENV: "1068"}):
            body.update(runtime.extra_body())
        assert body == {"prompt_cache_key": "1068"}
