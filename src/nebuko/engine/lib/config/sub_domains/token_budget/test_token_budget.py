"""Tests for TokenBudgetPolicy (issue #851)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import TokenBudgetPolicy
from config.sub_domains.token_budget.token_budget import (
    COMPACTION_TOKEN_FRACTION,
    DEFAULT_COMPACTION_MAX_SIZE,
    MIN_COMPACTION_MAX_TOKENS,
)


class TestDefaults:
    """The approved default semantics (contract invariants 1-3)."""

    def test_max_size_defaults_to_240_not_the_preset_80(self):
        policy = TokenBudgetPolicy()
        assert policy.compaction_max_size is None
        assert policy.resolved_max_size() == 240 == DEFAULT_COMPACTION_MAX_SIZE

    def test_max_tokens_always_resolves_to_a_number(self):
        policy = TokenBudgetPolicy()
        assert policy.compaction_max_tokens is None
        assert policy.resolved_max_tokens(100_000) == int(
            COMPACTION_TOKEN_FRACTION * 100_000
        )

    def test_derived_max_tokens_clamps_to_positive_minimum(self):
        policy = TokenBudgetPolicy()
        assert policy.resolved_max_tokens(0) == MIN_COMPACTION_MAX_TOKENS

    def test_explicit_zero_max_tokens_still_wins(self):
        policy = TokenBudgetPolicy(compaction_max_tokens=0)
        assert policy.resolved_max_tokens(100_000) == 0

    def test_compaction_model_defaults_to_role_model(self):
        policy = TokenBudgetPolicy()
        assert policy.resolved_compaction_model("openai/gpt-4o") == "openai/gpt-4o"

    def test_keep_first_matches_default_condenser(self):
        assert TokenBudgetPolicy().compaction_keep_first == 4

    def test_experimental_flags_default_off(self):
        policy = TokenBudgetPolicy()
        assert policy.prompt_profile == "full"
        assert policy.require_batched_recon is False


class TestResolvedOverrides:
    """Explicit values are honoured verbatim."""

    def test_explicit_max_size_wins(self):
        assert TokenBudgetPolicy(compaction_max_size=500).resolved_max_size() == 500

    def test_explicit_max_tokens_wins(self):
        policy = TokenBudgetPolicy(compaction_max_tokens=1234)
        assert policy.resolved_max_tokens(100_000) == 1234

    def test_explicit_compaction_model_wins(self):
        policy = TokenBudgetPolicy(compaction_model="openai/gpt-4o-mini")
        assert policy.resolved_compaction_model("openai/gpt-4o") == "openai/gpt-4o-mini"


class TestMergedWith:
    """``merged_with`` is a field-wise merge using exclude-unset semantics."""

    def test_none_override_returns_self(self):
        policy = TokenBudgetPolicy(compaction_max_size=300)
        assert policy.merged_with(None) is policy

    def test_only_explicitly_set_fields_win(self):
        base = TokenBudgetPolicy(
            compaction_max_size=300, compaction_keep_first=6, prompt_profile="lean"
        )
        override = TokenBudgetPolicy(prompt_profile="full")
        merged = base.merged_with(override)
        assert merged.prompt_profile == "full"
        assert merged.compaction_max_size == 300
        assert merged.compaction_keep_first == 6

    def test_empty_override_keeps_base(self):
        base = TokenBudgetPolicy(compaction_max_size=300)
        assert base.merged_with(TokenBudgetPolicy()).compaction_max_size == 300

    def test_merge_does_not_mutate_base(self):
        base = TokenBudgetPolicy(prompt_profile="full")
        base.merged_with(TokenBudgetPolicy(prompt_profile="lean"))
        assert base.prompt_profile == "full"
