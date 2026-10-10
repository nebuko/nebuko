"""Tests for resolve_provider_runtime — the per-call provider HTTP decoration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from config import ProviderConfig

from agent_script.features.resolve_provider_runtime.resolve_provider_runtime import (
    SESSION_ID_ENV,
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


class TestIssueSessionRuntime:
    def test_stamps_the_declared_header_with_the_engine_id(self):
        runtime = IssueSessionRuntime("x-opencode-session", "854")
        assert runtime.extra_headers() == {"x-opencode-session": "854"}


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
