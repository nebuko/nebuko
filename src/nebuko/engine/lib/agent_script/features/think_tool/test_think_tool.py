"""Payload regressions for the ``think`` tool wiring (issue #937).

Verification seam per ``AGENTS.md``: ``select_chat_options(llm, {}, has_tools=True)``
is what the SDK actually sends, so these tests assert on the *payload*, not on
the ``LLM`` attributes the executor mutates.
"""

import dataclasses
from types import SimpleNamespace

import pytest
from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.options.chat_options import select_chat_options
from openhands.sdk.tool.builtins.think import ThinkAction

from agent_script.features.think_tool.think_tool import (
    ThinkEnableExecutor,
    ThinkEnableTool,
)

MODEL = "openai/deepseek-v4.1-flash"


def _llm(**overrides) -> LLM:
    cfg = {"model": MODEL, "api_key": "test-key", "usage_id": "agent_script"}
    cfg.update(overrides)
    return LLM(**cfg)


def _thinking_off_llm() -> LLM:
    """The payload ``dev``/``context`` resolve to today (thinking disabled)."""
    return _llm(
        reasoning_effort="none",
        litellm_extra_body={
            "chat_template_kwargs": {
                "enable_thinking": False,
                "preserve_thinking": True,
            }
        },
    )


def _kwargs(payload: dict) -> dict:
    return (payload.get("extra_body") or {}).get("chat_template_kwargs") or {}


def _conversation(llm: LLM):
    return SimpleNamespace(agent=SimpleNamespace(llm=llm))


@pytest.fixture(autouse=True)
def _clear_reasoning_env(monkeypatch):
    monkeypatch.delenv("LLM_REASONING", raising=False)


class TestToolDefinition:
    def test_exposed_name_is_think(self):
        """The default derivation would expose ``think_enable`` (ruling 7)."""
        tool = ThinkEnableTool.create()[0]
        assert tool.name == "think"

    def test_create_rejects_params(self):
        with pytest.raises(ValueError):
            ThinkEnableTool.create(conv_state=None, unexpected=True)


class TestPayloadRegressions:
    def test_default_off_payload_is_unchanged(self):
        """No ``think`` call ⇒ exactly the pre-change payload."""
        payload = select_chat_options(_thinking_off_llm(), {}, has_tools=True)
        assert payload["reasoning_effort"] == "none"
        assert _kwargs(payload) == {
            "enable_thinking": False,
            "preserve_thinking": True,
        }

    def test_think_enables_thinking_in_the_next_payload(self):
        llm = _thinking_off_llm()
        observation = ThinkEnableExecutor()(
            ThinkAction(thought="let me reason"), _conversation(llm)
        )
        assert observation.is_error is False

        payload = select_chat_options(llm, {}, has_tools=True)
        kwargs = _kwargs(payload)
        assert kwargs["enable_thinking"] is True
        assert kwargs["preserve_thinking"] is True

    def test_think_effort_rule_applies(self, monkeypatch):
        monkeypatch.setenv("LLM_REASONING", "high")
        llm = _thinking_off_llm()
        ThinkEnableExecutor()(ThinkAction(thought="think"), _conversation(llm))
        assert _kwargs(select_chat_options(llm, {}, has_tools=True))[
            "reasoning_effort"
        ] == "xhigh"

    def test_think_is_idempotent(self):
        llm = _thinking_off_llm()
        executor = ThinkEnableExecutor()
        for _ in range(2):
            assert executor(ThinkAction(thought="think"), _conversation(llm)).is_error is False
        assert _kwargs(select_chat_options(llm, {}, has_tools=True)) == {
            "enable_thinking": True,
            "preserve_thinking": True,
        }


class TestFailurePaths:
    def test_missing_conversation_is_a_loud_error(self, caplog):
        with caplog.at_level("ERROR"):
            observation = ThinkEnableExecutor()(ThinkAction(thought="think"), None)
        assert observation.is_error is True
        assert "thinking was NOT enabled" in caplog.text

    def test_missing_llm_is_a_loud_error(self):
        conversation = SimpleNamespace(agent=SimpleNamespace())
        observation = ThinkEnableExecutor()(
            ThinkAction(thought="think"), conversation
        )
        assert observation.is_error is True

    def test_unobservable_carrier_is_a_loud_error(self, monkeypatch):
        """A payload with no reasoning carrier must not silently claim success.

        The H4 case: the parameters are set on the ``LLM`` but nothing that
        enables reasoning actually reaches the request the SDK builds.
        """
        import agent_script.features.think_tool.think_tool as think_tool

        monkeypatch.setattr(
            think_tool, "select_chat_options", lambda *a, **kw: {}
        )
        llm = _thinking_off_llm()
        observation = ThinkEnableExecutor()(
            ThinkAction(thought="think"), _conversation(llm)
        )
        assert observation.is_error is True
