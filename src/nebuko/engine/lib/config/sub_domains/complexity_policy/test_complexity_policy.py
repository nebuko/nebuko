"""Tests for the layered complexity policy domain (issue #869)."""

import sys
from pathlib import Path

import pytest


from nebuko.engine.lib.config.sub_domains.complexity_policy.complexity_policy import (
    BUILTIN_LAYER,
    ComplexityBand,
    ComplexityPolicy,
    ComplexityPolicyLayer,
)
from nebuko.engine.lib.complexity_governor.evidence import (
    ContextSignals,
    EvidenceBundle,
    HumanSignals,
)


def _builtin_policy() -> ComplexityPolicy:
    return ComplexityPolicy.resolve([BUILTIN_LAYER])


def _bundle(**kwargs) -> EvidenceBundle:
    return EvidenceBundle(**kwargs)


class TestResolve:
    def test_builtin_only(self):
        policy = _builtin_policy()
        assert set(policy.bands) == {"low", "medium", "high"}
        assert policy.target_distribution == {"low": 0.35, "medium": 0.50, "high": 0.15}
        assert policy.max_auto_delta == 1

    def test_repo_layer_overrides_only_declared_sections(self):
        repo = ComplexityPolicyLayer(
            scope="repo",
            target_distribution={"low": 0.70, "medium": 0.25, "high": 0.05},
        )
        policy = ComplexityPolicy.resolve([BUILTIN_LAYER, repo])
        # Retuned distribution...
        assert policy.target_distribution["high"] == 0.05
        # ...while bands and rules are inherited from the builtin layer.
        assert set(policy.bands) == {"low", "medium", "high"}
        assert policy.signal_rules == BUILTIN_LAYER.signal_rules

    def test_repo_layer_can_replace_bands(self):
        repo = ComplexityPolicyLayer(
            scope="repo",
            bands={
                tier: ComplexityBand(
                    min_score=1, guidance=f"repo {tier} band"
                )
                for tier in ("low", "medium", "high")
            },
        )
        policy = ComplexityPolicy.resolve([BUILTIN_LAYER, repo])
        assert policy.bands["high"].guidance == "repo high band"

    def test_rejects_empty_layers(self):
        with pytest.raises(ValueError):
            ComplexityPolicy.resolve([])

    def test_rejects_first_layer_not_builtin(self):
        repo = ComplexityPolicyLayer(scope="repo", target_distribution={"low": 1.0})
        with pytest.raises(ValueError):
            ComplexityPolicy.resolve([repo])


class TestDeriveSignals:
    def test_two_linked_issues_fire_multi_issue_linkage(self):
        policy = _builtin_policy()
        signals = policy.derive_signals(
            _bundle(context=ContextSignals(linked_issues=2))
        )
        assert "multi-issue-linkage" in signals

    def test_single_linked_issue_does_not_fire(self):
        policy = _builtin_policy()
        signals = policy.derive_signals(
            _bundle(context=ContextSignals(linked_issues=1))
        )
        assert "multi-issue-linkage" not in signals

    def test_human_friction_fires_at_threshold(self):
        policy = _builtin_policy()
        assert "human-friction" in policy.derive_signals(
            _bundle(human=HumanSignals(changes_requested=2))
        )
        assert "human-friction" not in policy.derive_signals(
            _bundle(human=HumanSignals(changes_requested=1))
        )


class TestClassify:
    def test_three_linkage_classifies_high(self):
        policy = _builtin_policy()
        assert (
            policy.classify(_bundle(context=ContextSignals(linked_issues=3)))
            == "high"
        )

    def test_thin_evidence_biases_low(self):
        policy = _builtin_policy()
        assert policy.classify(_bundle()) == "low"

    def test_single_context_signal_stays_below_high(self):
        policy = _builtin_policy()
        # One marginal signal (score 1) cannot clear the HIGH gate (min_score 3).
        assert policy.classify(
            _bundle(context=ContextSignals(linked_issues=2))
        ) in ("low", "medium")
