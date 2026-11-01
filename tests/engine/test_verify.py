"""Claim-verification loop (spec step): red tests against stubs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nebuko.engine.finalize import AgentCall, VerificationClaim
from nebuko.engine.verify import (
    CLAIM_CAPS,
    MIN_REFUTE_RATIO,
    VERDICT_VALUES,
    ClaimLedger,
    ClaimTaskRenderer,
    ClaimsValidator,
)


def _claim(cid: str = "C1", kind: str = "support") -> VerificationClaim:
    return VerificationClaim(
        id=cid,
        kind=kind,
        category="version",
        claim="tenacity latest release is 9.x",
        if_wrong="Pin the version the plan documents",
    )


def _refutes(n: int) -> list[VerificationClaim]:
    return [_claim(f"R{i}", kind="refute") for i in range(n)]


# ── schema: VerificationClaim / AgentCall.verify ──────────────────────────


def test_given_a_well_formed_claim_when_constructed_then_it_keeps_all_fields():
    c = _claim()
    assert (c.id, c.kind, c.category) == ("C1", "support", "version")


def test_given_an_if_wrong_shorter_than_ten_chars_when_constructed_then_validation_rejects_it():
    with pytest.raises(ValidationError):
        VerificationClaim(
            id="C1", kind="support", category="version",
            claim="x", if_wrong="short",
        )


def test_given_verify_on_a_context_call_when_constructed_then_it_is_accepted():
    call = AgentCall(agent_name="context", verify=[_claim()])
    assert call.verify[0].id == "C1"


def test_given_verify_on_a_non_context_call_when_constructed_then_validation_rejects_it():
    with pytest.raises(ValidationError):
        AgentCall(agent_name="dev", verify=[_claim()])


def test_given_an_unknown_category_when_constructed_then_validation_rejects_it():
    with pytest.raises(ValidationError):
        VerificationClaim(
            id="C1", kind="support", category="random",
            claim="x", if_wrong="something changes here",
        )


# ── ClaimsValidator ────────────────────────────────────────────────────────


def test_given_no_claims_when_validated_then_the_result_is_ok():
    result = ClaimsValidator().validate("context", [], "medium")
    assert result.ok is True and result.errors == []


def test_given_claims_within_the_medium_cap_when_validated_then_the_result_is_ok():
    claims = [_claim(f"C{i}") for i in range(3)] + _refutes(2)
    assert ClaimsValidator().validate("context", claims, "medium").ok is True


def test_given_more_claims_than_the_tier_cap_when_validated_then_the_cap_error_is_reported():
    claims = [_claim(f"C{i}") for i in range(4)] + _refutes(2)
    result = ClaimsValidator().validate("context", claims, "low")
    assert result.ok is False and any("cap" in e.lower() for e in result.errors)


def test_given_fewer_refute_claims_than_one_third_when_validated_then_the_ratio_error_is_reported():
    claims = [_claim(f"C{i}") for i in range(5)] + _refutes(1)
    result = ClaimsValidator().validate("context", claims, "high")
    assert result.ok is False and any("refute" in e.lower() for e in result.errors)


def test_given_duplicate_claim_ids_when_validated_then_the_duplicate_error_is_reported():
    result = ClaimsValidator().validate("context", [_claim("C1"), _claim("C1")], "low")
    assert result.ok is False and any("C1" in e for e in result.errors)


@pytest.mark.parametrize("tier,cap", [("low", 3), ("medium", 5), ("high", 8)])
def test_given_the_tier_table_when_read_then_the_cap_matches_the_plan(tier: str, cap: int):
    assert CLAIM_CAPS[tier] == cap and MIN_REFUTE_RATIO == pytest.approx(1 / 3)


# ── ClaimTaskRenderer ──────────────────────────────────────────────────────


def test_given_a_task_and_claims_when_rendered_then_the_fixed_header_and_claim_lines_are_appended():
    out = ClaimTaskRenderer().render("collect facts", [_claim("C1"), _claim("C2", "refute")])
    assert out.startswith("collect facts")
    assert "VERIFY THESE CLAIMS (independently; report verdict per claim)" in out
    assert "C1 [support/version] tenacity latest release is 9.x — if wrong: Pin the version the plan documents" in out
    assert "C2 [refute/version] tenacity latest release is 9.x — if wrong: Pin the version the plan documents" in out


def test_given_a_task_when_rendered_then_only_the_claims_are_added_and_no_draft_plan_leaks():
    out = ClaimTaskRenderer().render("collect facts", [_claim()])
    assert "draft" not in out.lower() and "plan" not in out.lower().replace("if wrong", "")


def test_given_no_claims_when_rendered_then_the_task_is_returned_unchanged():
    assert ClaimTaskRenderer().render("collect facts", []) == "collect facts"


# ── ClaimLedger ────────────────────────────────────────────────────────────


def test_given_claims_and_verdicts_when_recorded_then_summarize_reports_the_distribution():
    ledger = ClaimLedger()
    claims = [_claim("C1"), _claim("C2", "refute")]
    ledger.record("run-1", claims, {"C1": "confirmed", "C2": "refuted"})
    summary = ledger.summarize()
    assert summary["claims_per_run"]["run-1"] == 2
    assert summary["verdict_distribution"]["confirmed"] == 1
    assert summary["verdict_distribution"]["refuted"] == 1
    assert summary["refuted_changed_plan_share"] == 0.0


def test_given_a_refuted_claim_that_changed_the_plan_when_recorded_then_the_share_counts_the_run():
    ledger = ClaimLedger()
    ledger.record("run-1", [_claim("C1", "refute")],
                  {"C1": "refuted", "C1_plan_changed": "true"})
    assert ledger.summarize()["refuted_changed_plan_share"] == 1.0


def test_given_the_verdict_vocabulary_when_read_then_it_is_exactly_the_four_verdicts():
    assert VERDICT_VALUES == ("confirmed", "refuted", "contested", "inconclusive")
