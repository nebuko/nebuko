"""Unit tests for the thinking intent → request parameter mapping (issue #937)."""

from openhands.sdk.llm.llm import LLM

from agent_script.sub_domains.thinking_request.thinking_request import (
    REASONING_EFFORT_OFF,
    ThinkingRequest,
    normalize_reasoning_effort,
)


def _resolve(**overrides):
    params = {
        "configured_thinking": None,
        "preserve_thinking": None,
        "explicit_effort": None,
        "llm_reasoning": None,
    }
    params.update(overrides)
    return ThinkingRequest.resolve(**params)


def _llm(**overrides) -> LLM:
    cfg = {
        "model": "openai/deepseek-v4.1-flash",
        "api_key": "test-key",
        "usage_id": "agent_script",
    }
    cfg.update(overrides)
    return LLM(**cfg)


class TestResolve:
    def test_unset_thinking_omits_the_key_and_keeps_thinking_on(self):
        request = _resolve()
        assert request.enable_thinking is None
        assert request.thinking_on is True

    def test_thinking_off_suppresses_effort(self):
        request = _resolve(configured_thinking=False, llm_reasoning="high")
        assert request.enable_thinking is False
        assert request.thinking_on is False
        assert request.chat_template_effort is None

    def test_high_is_normalized_to_xhigh_while_thinking_on(self):
        request = _resolve(configured_thinking=True, llm_reasoning="high")
        assert request.chat_template_effort == "xhigh"

    def test_force_thinking_matches_configured_thinking_true(self):
        forced = _resolve(configured_thinking=False, llm_reasoning="medium",
                          force_thinking=True)
        configured = _resolve(configured_thinking=True, llm_reasoning="medium")
        assert forced == configured

    def test_explicit_effort_carries_to_the_top_level(self):
        request = _resolve(configured_thinking=True, explicit_effort="xhigh")
        assert request.top_level_effort == "xhigh"
        assert request.top_level_effort_default is False

    def test_preserve_thinking_is_carried_through(self):
        assert _resolve(preserve_thinking=True).preserve_thinking is True


class TestMergedChatTemplateKwargs:
    def test_merge_preserves_existing_preserve_thinking(self):
        existing = {"enable_thinking": False, "preserve_thinking": True}
        merged = _resolve(force_thinking=True).merged_chat_template_kwargs(existing)
        assert merged == {"enable_thinking": True, "preserve_thinking": True}

    def test_merge_from_none(self):
        merged = _resolve(configured_thinking=False).merged_chat_template_kwargs(
            None
        )
        assert merged == {"enable_thinking": False}

    def test_stale_chat_template_effort_is_dropped(self):
        merged = _resolve(configured_thinking=True).merged_chat_template_kwargs(
            {"reasoning_effort": "low"}
        )
        assert "reasoning_effort" not in merged


class TestApplyTo:
    def test_enable_writes_both_carriers(self):
        llm = _llm(litellm_extra_body={
            "chat_template_kwargs": {"preserve_thinking": True}
        })
        _resolve(force_thinking=True, llm_reasoning="medium").apply_to(llm)
        assert llm.litellm_extra_body == {
            "chat_template_kwargs": {
                "enable_thinking": True,
                "preserve_thinking": True,
                "reasoning_effort": "medium",
            }
        }
        assert llm.reasoning_effort is None

    def test_thinking_off_pins_the_kill_switch(self):
        """Issue #941: the SDK default ``"high"`` keeps reasoning on."""
        llm = _llm()
        assert llm.reasoning_effort == "high"  # SDK default
        _resolve(configured_thinking=False).apply_to(llm)
        assert llm.reasoning_effort == REASONING_EFFORT_OFF
        assert llm.litellm_extra_body == {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    def test_apply_emits_top_level_effort_when_not_default(self):
        llm = _llm()
        _resolve(configured_thinking=True, explicit_effort="xhigh").apply_to(llm)
        assert llm.reasoning_effort == "xhigh"


class TestNormalizeReasoningEffort:
    def test_vocabulary(self):
        assert normalize_reasoning_effort("high") == "xhigh"
        assert normalize_reasoning_effort("medium") == "medium"
        assert normalize_reasoning_effort("none") is None
        assert normalize_reasoning_effort(None) is None
