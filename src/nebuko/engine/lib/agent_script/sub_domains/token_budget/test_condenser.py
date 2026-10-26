"""Tests for the policy-driven condenser builder (issue #851)."""

from types import SimpleNamespace

from openhands.sdk import LLM

from nebuko.engine.lib.agent_script.sub_domains.token_budget.condenser import (
    CONDENSER_USAGE_ID,
    DEFAULT_EFFECTIVE_INPUT_LIMIT,
    build_condenser,
    resolve_effective_input_limit,
    resolve_token_budget_policy,
)
from nebuko.engine.lib.config import TokenBudgetPolicy


def _llm() -> LLM:
    return LLM(model="openai/gpt-4o", api_key="test-key")


class TestResolveTokenBudgetPolicy:
    def test_none_config_yields_documented_defaults(self):
        policy = resolve_token_budget_policy(None)
        assert policy == TokenBudgetPolicy()

    def test_role_policy_is_used(self):
        configured = TokenBudgetPolicy(prompt_profile="lean")
        cfg = SimpleNamespace(token_budget=configured)
        assert resolve_token_budget_policy(cfg) is configured

    def test_missing_budget_falls_back_to_defaults(self):
        assert resolve_token_budget_policy(SimpleNamespace()) == TokenBudgetPolicy()


class TestResolveEffectiveInputLimit:
    def test_uses_llm_route_limit_when_known(self):
        llm = _llm()
        assert resolve_effective_input_limit(llm) == llm.effective_max_input_tokens

    def test_falls_back_when_route_exposes_no_limit(self):
        llm = SimpleNamespace(effective_max_input_tokens=None)
        assert resolve_effective_input_limit(llm) == DEFAULT_EFFECTIVE_INPUT_LIMIT


class TestBuildCondenser:
    def test_default_policy_is_a_late_cache_aligned_valve(self):
        llm = _llm()
        condenser = build_condenser(llm, TokenBudgetPolicy())
        assert condenser.max_size == 240
        assert condenser.keep_first == 4
        assert condenser.max_tokens == int(
            0.8 * resolve_effective_input_limit(llm)
        )

    def test_summarizer_runs_on_role_model_with_metering_tag(self):
        llm = _llm()
        summarizer = build_condenser(llm, TokenBudgetPolicy()).llm
        assert summarizer.model == llm.model
        assert summarizer.usage_id == CONDENSER_USAGE_ID
        # Cache sharing requires the routing credentials to be copied verbatim.
        assert summarizer.api_key == llm.api_key

    def test_explicit_policy_fields_are_honoured(self):
        llm = _llm()
        policy = TokenBudgetPolicy(
            compaction_max_size=500,
            compaction_max_tokens=4096,
            compaction_keep_first=6,
            compaction_model="openai/gpt-4o-mini",
        )
        condenser = build_condenser(llm, policy)
        assert condenser.max_size == 500
        assert condenser.max_tokens == 4096
        assert condenser.keep_first == 6
        assert condenser.llm.model == "openai/gpt-4o-mini"

    def test_token_valve_never_resolves_empty(self):
        condenser = build_condenser(_llm(), TokenBudgetPolicy())
        assert condenser.max_tokens is not None
        assert condenser.max_tokens > 0
