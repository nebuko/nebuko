"""Harness port tests (issue #1368).

The port is the engine's one seam over agent runtimes: a registry, a frozen
capability declaration, and a closing channel both harnesses implement.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from nebuko.engine.agent_runner import EngineAgentUnavailable
from nebuko.engine.finalize import OutputContract, TurnOutcomePayload
from nebuko.engine.harness import (
    BuiltAgent,
    ClosingResult,
    HarnessCapabilities,
    HarnessSession,
    register_harness,
    registered_harnesses,
    resolve_harness,
)
from nebuko.engine.harness.claude_code import ClaudeCodeHarness
from nebuko.engine.harness.openhands import OpenHandsHarness


class FakeTextSession:
    """A minimal session: records messages, replays one scripted result."""

    def __init__(self, result: ClosingResult) -> None:
        self.result = result
        self.messages: list[str] = []
        self.events: list[Any] = []
        self.closed = False

    def send_message(self, text: str) -> None:
        self.messages.append(text)

    def run(self, timeout_s: float | None = None) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def on_event(self, callback: Any) -> None:
        self.events.append(callback)


class FakeTextChannel:
    def __init__(self, result: ClosingResult) -> None:
        self._result = result

    def elicit(self, session: Any, contract: OutputContract | None) -> ClosingResult:
        return self._result


class FakeTextHarness:
    """A second implementation proving the protocol is not OpenHands-shaped."""

    name = "fake-text"

    def capabilities(self) -> HarnessCapabilities:
        return HarnessCapabilities(backend=self.name, closing_channel="text")

    def build_agent(self, *, role_name, agent_cfg, prompt, agent_context) -> BuiltAgent:
        return BuiltAgent(session=FakeTextSession(_RESULT), prompt=prompt)

    def make_closing_channel(self) -> FakeTextChannel:
        return FakeTextChannel(_RESULT)


_RESULT = ClosingResult(
    report="## Report\n\n- done",
    outcome=TurnOutcomePayload(appreciation="approved"),
)


def test_registry_defaults_to_openhands_and_claude_code():
    assert set(registered_harnesses()) >= {"openhands", "claude-code"}


def test_resolve_none_returns_the_openhands_adapter():
    assert isinstance(resolve_harness(None), OpenHandsHarness)


def test_unknown_backend_names_the_registered_set():
    with pytest.raises(EngineAgentUnavailable) as exc:
        resolve_harness("no-such-backend")
    assert "openhands" in str(exc.value)
    assert "claude-code" in str(exc.value)


def test_register_harness_extends_registry_without_schema_change():
    register_harness("fake-text", FakeTextHarness)
    try:
        assert resolve_harness("fake-text").capabilities().closing_channel == "text"
    finally:
        from nebuko.engine.harness import base

        base._REGISTRY.pop("fake-text", None)


def test_harness_capabilities_is_frozen():
    capabilities = HarnessCapabilities(backend="x")
    with pytest.raises(ValidationError):
        capabilities.backend = "y"


def test_claude_code_capabilities_declares_mcp_engine_tools():
    capabilities = ClaudeCodeHarness().capabilities()
    assert capabilities.engine_tools == "mcp"
    assert capabilities.closing_channel == "tool"
    assert capabilities.native_tools is False
    assert capabilities.response_schema is True
    assert capabilities.tool_narrowing is True


def test_fake_text_harness_returns_same_outcome_payload():
    harness = FakeTextHarness()
    built = harness.build_agent(
        role_name="dev", agent_cfg=None, prompt="p", agent_context=None
    )
    assert isinstance(built.session, HarnessSession)
    result = harness.make_closing_channel().elicit(built.session, None)
    assert result.report == _RESULT.report
    assert result.outcome is not None
    assert result.outcome.appreciation == "approved"
