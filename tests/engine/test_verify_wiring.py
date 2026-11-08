"""Engine wiring tests for issue #1250: validation, rendering, coverage, metrics."""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from nebuko.engine.verify import (
    ClaimPlanCoverage,
    ClaimTaskRenderer,
    ClaimsValidator,
    VerificationClaim,
    claim_metrics,
)


def _claim(**overrides):
    fields = {
        "id": "C1",
        "claim": "tenacity provides exponential backoff",
        "kind": "support",
        "category": "build_vs_buy",
        "if_wrong": "the plan switches to a hand-rolled helper",
    }
    fields.update(overrides)
    return VerificationClaim(**fields)


class TestAgentCallVerify:
    def test_rejected_on_non_context_handoff(self):
        from nebuko.engine.finalize import AgentCall

        call = AgentCall(agent_name="dev", task="t", verify=[_claim()])
        with pytest.raises(ValueError, match="context"):
            call.validate_runtime()

    def test_accepted_on_context_handoff(self):
        from nebuko.engine.finalize import AgentCall

        call = AgentCall(
            agent_name="context",
            task="t",
            verify=[_claim(), _claim(id="C2", kind="refute")],
        )
        assert len(call.verify) == 2


class TestTaskRendering:
    def test_render_task_prepends_claims_and_keeps_task(self):
        rendered = ClaimTaskRenderer().render_task("collect docs", [_claim()])
        assert "collect docs" in rendered
        assert "### CLAIM VERIFICATION" in rendered
        assert "| C1" in rendered

    def test_draft_plan_never_leaks_into_task(self):
        """The renderer emits only the task and the claims table."""
        rendered = ClaimTaskRenderer().render_task("t", [_claim()])
        assert "draft" not in rendered
        assert rendered.count("\n| C") == 1


class TestPlanCoverage:
    def test_missing_ids_flagged(self):
        claims = [_claim(), _claim(id="C2", kind="refute")]
        plan_text = "## Context\nC1 confirmed.\n"
        assert ClaimPlanCoverage().missing_ids(claims, plan_text) == ["C2"]

    def test_all_addressed(self):
        assert ClaimPlanCoverage().missing_ids([_claim()], "C1 confirmed") == []


class TestMetrics:
    def test_claim_metrics_counts_and_verdicts(self):
        claims = [_claim(), _claim(id="C2", kind="refute")]
        report = "| C1 | confirmed | url | excerpt |\n| C2 | refuted | url | x |"
        m = claim_metrics(claims, report)
        assert m["claims"] == 2
        assert m["verdicts"] == {"confirmed": 1, "refuted": 1}
        assert m["refuted_changed_plan"] is True

    def test_validator_enforces_caps(self):
        claims = [_claim(id=f"C{i}") for i in range(1, 6)]
        assert ClaimsValidator().validate(
            claims, agent_name="context", complexity="low"
        )
