"""Tests for the complexity governor + boundary integration (issue #869)."""

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))  # src/nebuko/engine/lib

from config.sub_domains.complexity_policy.complexity_policy import (  # noqa: E402
    build_builtin_policy,
)
from complexity_governor import (  # noqa: E402
    ComplexityAdjustment,
    ComplexityGovernor,
    ComplexityReviewRequest,
    ContextSignals,
    EvidenceBundle,
    HumanSignals,
    collect_evidence,
    compute_boundary_adjustment,
    distribution_report,
    load_repo_policy_layer,
)
from workflow_state import WorkflowState, to_json  # noqa: E402
from workflow_state.features.build_state.build_state import build_state  # noqa: E402


def _policy():
    return build_builtin_policy()


def _entry(
    step,
    *,
    complexity,
    review_fix_requests=0,
    review_request=None,
    nb_tool_calls=3,
    input_tokens=1000,
):
    return build_state(
        None,
        workflow="wf",
        step=step,
        agent="dev",
        metrics_dict={
            "complexity": complexity,
            "review_fix_requests": review_fix_requests,
            "input_tokens": input_tokens,
            "nb_tool_calls": nb_tool_calls,
        },
        complexity_review_request=review_request,
    )


class TestEvaluateBoundary:
    def test_no_drift_returns_none(self):
        policy = _policy()
        history = list(_entry("s1", complexity="low").history)
        evidence = collect_evidence(history)
        assert ComplexityGovernor().evaluate_boundary(history, evidence, policy) is None

    def test_auto_adjust_disabled_returns_none(self):
        policy = _policy().model_copy(update={"auto_adjust": False})
        history = list(_entry("s1", complexity="low", review_fix_requests=5).history)
        evidence = EvidenceBundle(
            human=HumanSignals(changes_requested=5),
            context=ContextSignals(linked_issues=3),
        )
        assert ComplexityGovernor().evaluate_boundary(history, evidence, policy) is None

    def test_human_drift_shifts_up_by_one(self):
        policy = _policy()
        history = list(_entry("s1", complexity="low", review_fix_requests=3).history)
        evidence = collect_evidence(history, human=HumanSignals(changes_requested=3))
        adjustment = ComplexityGovernor().evaluate_boundary(history, evidence, policy)
        assert adjustment is not None
        assert adjustment.delta == 1
        assert adjustment.trigger == "evidence-drift"

    def test_delta_clamped_to_max_auto_delta(self):
        policy = _policy()
        history = list(_entry("s1", complexity="low").history)
        evidence = EvidenceBundle(
            context=ContextSignals(linked_issues=50),
            llm={"input_tokens": 10_000_000.0, "nb_tool_calls": 999.0},
        )
        adjustment = ComplexityGovernor().evaluate_boundary(history, evidence, policy)
        assert adjustment is not None
        assert adjustment.delta == policy.max_auto_delta


class TestAdjudicate:
    def test_rejects_empty_evidence(self):
        policy = _policy()
        request = ComplexityReviewRequest(evidence=EvidenceBundle(), feeling="hmm")
        with pytest.raises(ValueError):
            ComplexityGovernor().adjudicate(request, EvidenceBundle(), policy)

    def test_rejects_blank_feeling(self):
        policy = _policy()
        request = ComplexityReviewRequest(
            evidence=EvidenceBundle(human=HumanSignals(changes_requested=5)),
            feeling="   ",
        )
        with pytest.raises(ValueError):
            ComplexityGovernor().adjudicate(request, EvidenceBundle(), policy)

    def test_agent_request_uses_suggested_tier(self):
        policy = _policy()
        request = ComplexityReviewRequest(
            evidence=EvidenceBundle(human=HumanSignals(changes_requested=5)),
            feeling="design got weird",
            suggested_tier="high",
        )
        adjustment = ComplexityGovernor().adjudicate(request, EvidenceBundle(), policy)
        assert adjustment.trigger == "agent-request"
        assert adjustment.feeling == "design got weird"
        assert adjustment.delta == 1  # clamped to max_auto_delta


class TestApply:
    @pytest.mark.parametrize("base", ["low", "medium", "high"])
    def test_never_escapes_range(self, base):
        for delta in range(-5, 6):
            adj = ComplexityAdjustment(
                delta=delta, trigger="evidence-drift", evidence=EvidenceBundle()
            )
            assert ComplexityGovernor.apply(base, adj) in ("low", "medium", "high")

    def test_none_adjustment_is_identity(self):
        assert ComplexityGovernor.apply("medium", None) == "medium"

    def test_high_plus_one_stays_high(self):
        adj = ComplexityAdjustment(
            delta=1, trigger="evidence-drift", evidence=EvidenceBundle()
        )
        assert ComplexityGovernor.apply("high", adj) == "high"


class TestBoundaryIntegration:
    def test_boundary_adjudicates_recorded_request(self):
        policy = _policy()
        review_request = ComplexityReviewRequest(
            evidence=EvidenceBundle(human=HumanSignals(changes_requested=4)),
            feeling="loop detected",
            suggested_tier="high",
        )
        state = _entry("s1", complexity="medium", review_request=review_request)
        adjustment = compute_boundary_adjustment(state, policy)
        assert adjustment is not None
        assert adjustment.trigger == "agent-request"

    def test_boundary_falls_back_to_drift(self):
        policy = _policy()
        running = _entry("s1", complexity="low")
        state = build_state(
            running,
            workflow="wf",
            step="s2",
            agent="dev",
            metrics_dict={
                "complexity": "low",
                "review_fix_requests": 3,
                "input_tokens": 1000,
                "nb_tool_calls": 3,
            },
        )
        adjustment = compute_boundary_adjustment(state, policy)
        assert adjustment is not None
        assert adjustment.trigger == "evidence-drift"
        assert adjustment.delta == 1

    def test_distribution_report_against_target(self):
        policy = _policy()
        state = _entry("s1", complexity="high")
        report = distribution_report(state, repo="nebuko/nebuko", policy=policy)
        assert report.repo == "nebuko/nebuko"
        assert report.counts["high"] == 1
        assert report.target["high"] == 0.15
        assert report.delta_from_target["high"] == pytest.approx(1.0 - 0.15)


class TestPolicySnapshotPersistence:
    def test_state_carries_policy_and_adjustment(self):
        policy = _policy()
        adj = ComplexityAdjustment(
            delta=1, trigger="evidence-drift", evidence=EvidenceBundle()
        )
        state = build_state(
            None,
            workflow="wf",
            step="s1",
            agent="dev",
            complexity_policy=policy,
            complexity_adjustment=adj,
        )
        assert state.complexity_policy is not None
        assert state.history[-1].complexity_adjustment.delta == 1

    def test_adjustment_round_trips_through_json(self):
        adj = ComplexityAdjustment(
            delta=-1,
            trigger="evidence-drift",
            evidence=EvidenceBundle(human=HumanSignals(feedback_rounds=2)),
            rationale="over-tiered",
        )
        state = build_state(
            None, workflow="wf", step="s1", agent="dev", complexity_adjustment=adj
        )
        restored = WorkflowState.model_validate_json(to_json(state))
        assert restored.history[-1].complexity_adjustment == adj


class TestEndToEndDrift:
    """A LOW step burning HIGH tokens + change-requests shifts at the boundary."""

    def test_low_step_heavy_evidence_shifts_up_with_snapshot(self):
        policy = _policy()
        # Step 1 ran at LOW but burned HIGH tokens and drew 2 change-requests.
        step1 = build_state(
            None,
            workflow="wf",
            step="s1",
            agent="dev",
            metrics_dict={
                "complexity": "low",
                "input_tokens": 200_000,
                "nb_tool_calls": 5,
                "review_fix_requests": 2,
            },
        )
        adjustment = compute_boundary_adjustment(step1, policy)
        assert adjustment is not None
        assert adjustment.delta == 1  # clamp(low -> high, +-1)

        # The next boundary applies the adjustment and records the snapshot
        # that justified it on the advancing step's history entry.
        step2 = build_state(
            step1,
            workflow="wf",
            step="s2",
            agent="dev",
            complexity_adjustment=adjustment,
            metrics_dict={"complexity": "medium"},
        )
        recorded = step2.history[-1].complexity_adjustment
        assert recorded is not None
        assert recorded.evidence.llm["input_tokens"] == 200_000.0
        assert recorded.evidence.human.changes_requested == 2
        assert ComplexityGovernor.apply("low", recorded) == "medium"


class TestDriftNoRatchet:
    """The drift check must not climb a tier per boundary on persistent evidence.

    History entries record the *effective* (post-overlay) tier, so the governor
    recovers the workflow's *base* tier by subtracting the adjustment recorded on
    the last entry. Comparing fresh evidence against that stable base makes the
    correction idempotent: repeated heavy boundaries yield the same clamped delta
    rather than low -> medium -> high, and the tier decays back once evidence stops.
    """

    def test_three_consecutive_heavy_boundaries_hold_steady(self):
        policy = _policy()
        base_chain = "medium"
        state = build_state(
            None,
            workflow="wf",
            step="s1",
            agent="dev",
            metrics_dict={
                "complexity": base_chain,
                "input_tokens": 200_000,
                "nb_tool_calls": 50,
                "review_fix_requests": 2,
            },
        )
        effective_tiers = []
        for step in range(2, 5):
            adjustment = compute_boundary_adjustment(state, policy)
            assert adjustment is not None, f"heavy evidence stopped firing at s{step}"
            assert adjustment.delta == 1
            effective = ComplexityGovernor.apply(base_chain, adjustment)
            effective_tiers.append(effective)
            state = build_state(
                state,
                workflow="wf",
                step=f"s{step}",
                agent="dev",
                complexity_adjustment=adjustment,
                metrics_dict={
                    "complexity": effective,
                    "input_tokens": 200_000,
                    "nb_tool_calls": 50,
                    "review_fix_requests": 2,
                },
            )
        assert effective_tiers == ["high", "high", "high"]

    def test_evidence_stopping_reverts_to_base(self):
        policy = _policy()
        heavy = build_state(
            None,
            workflow="wf",
            step="s1",
            agent="dev",
            metrics_dict={
                "complexity": "medium",
                "input_tokens": 200_000,
                "nb_tool_calls": 50,
            },
        )
        adjustment = compute_boundary_adjustment(heavy, policy)
        assert adjustment is not None and adjustment.delta == 1
        # s2 ran at the overlaid HIGH, but the token burn has stopped.
        quiet = build_state(
            heavy,
            workflow="wf",
            step="s2",
            agent="dev",
            complexity_adjustment=adjustment,
            metrics_dict={"complexity": "high", "input_tokens": 1_000, "nb_tool_calls": 2},
        )
        revert = compute_boundary_adjustment(quiet, policy)
        assert revert is not None
        assert revert.delta == -1


class TestFromJsonStrict:
    """A corrupt snapshot must fail loudly, not coerce into a no-op decision."""

    def test_blank_is_none(self):
        assert ComplexityAdjustment.from_json("") is None
        assert ComplexityAdjustment.from_json("   ") is None

    def test_round_trip(self):
        adj = ComplexityAdjustment(
            delta=1,
            trigger="agent-request",
            evidence=EvidenceBundle(human=HumanSignals(changes_requested=3)),
            feeling="weird",
            rationale="r",
        )
        assert ComplexityAdjustment.from_json(adj.to_json()) == adj

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            "[]",
            '{"delta": 1}',  # missing trigger
            '{"delta": 1, "trigger": "bogus"}',
            '{"delta": "1", "trigger": "evidence-drift"}',  # coerced delta
            '{"delta": null, "trigger": "evidence-drift"}',
            '{"delta": 1, "trigger": "evidence-drift", "evidence": ["nope"]}',
        ],
    )
    def test_malformed_raises(self, raw):
        with pytest.raises(ValueError):
            ComplexityAdjustment.from_json(raw)


class TestHighGateSemantics:
    """Pin the HIGH gate's magnitude-weighted, ``any_of``-satisfiable behavior.

    ``_score`` weights each firing rule by its field magnitude, so one heavy
    observation (large token burn) clears ``min_score`` on its own. Pinning this
    makes the magnitude weighting an explicit contract rather than an accident.
    """

    def test_token_burn_alone_classifies_high(self):
        policy = _policy()
        evidence = EvidenceBundle(llm={"input_tokens": 150_000.0})
        assert policy.derive_signals(evidence) == {"high-token-burn"}
        assert policy.classify(evidence) == "high"

    def test_small_token_burn_stays_low(self):
        policy = _policy()
        assert policy.classify(EvidenceBundle(llm={"input_tokens": 1_000.0})) == "low"


class TestRepoPolicyLoading:
    """``load_repo_policy_layer`` reads the agent config named by AGENT_CONFIG_PATH."""

    def test_absent_config_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_CONFIG_PATH", str(tmp_path / "absent.yaml"))
        assert load_repo_policy_layer() is None

    def test_unreadable_config_is_logged(self, tmp_path, monkeypatch, caplog):
        # Valid YAML but not a valid AppConfig (no providers/llms/agents).
        bad = tmp_path / "agent-config.yaml"
        bad.write_text(
            "complexity_policy:\n"
            "  scope: repo\n"
            "  target_distribution: { low: 1.0, medium: 0.0, high: 0.0 }\n"
        )
        monkeypatch.setenv("AGENT_CONFIG_PATH", str(bad))
        with caplog.at_level(logging.WARNING):
            assert load_repo_policy_layer() is None
        assert "complexity policy" in caplog.text.lower()

    def test_unset_path_falls_back_to_layered_lookup(self, monkeypatch):
        monkeypatch.delenv("AGENT_CONFIG_PATH", raising=False)
        # The shipped default config declares no repo layer.
        assert load_repo_policy_layer() is None

