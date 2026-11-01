"""Tests for VerificationClaim, ClaimsValidator and ClaimTaskRenderer (issue #1250)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nebuko.engine.verify import ClaimTaskRenderer, ClaimsValidator, VerificationClaim


def _claim(**overrides: object) -> VerificationClaim:
    fields: dict[str, object] = {
        "id": "C1",
        "claim": "tenacity provides exponential backoff",
        "kind": "support",
        "category": "build_vs_buy",
        "if_wrong": "the plan switches to a hand-rolled retry helper",
    }
    fields.update(overrides)
    return VerificationClaim(**fields)


def _validator() -> ClaimsValidator:
    return ClaimsValidator()


class TestVerificationClaim:
    def test_min_length_if_wrong(self) -> None:
        with pytest.raises(ValidationError):
            _claim(if_wrong="short")

    def test_kind_is_literal(self) -> None:
        with pytest.raises(ValidationError):
            _claim(kind="confirm")

    def test_category_is_literal(self) -> None:
        with pytest.raises(ValidationError):
            _claim(category="pricing")


class TestClaimsValidator:
    def test_rejects_non_context_callee(self) -> None:
        message = _validator().validate(
            [_claim()], agent_name="architect", complexity="high"
        )
        assert message is not None and "context" in message

    def test_enforces_complexity_cap(self) -> None:
        claims = [_claim(id=f"C{i}") for i in range(1, 5)]
        message = _validator().validate(claims, agent_name="context", complexity="low")
        assert message is not None and "cap" in message

    def test_enforces_refute_ratio(self) -> None:
        claims = [_claim(id=f"C{i}") for i in range(1, 4)]
        message = _validator().validate(claims, agent_name="context", complexity="high")
        assert message is not None and "refute" in message

    def test_rejects_duplicate_ids(self) -> None:
        claims = [_claim(id="C1"), _claim(id="C1", kind="refute")]
        message = _validator().validate(claims, agent_name="context", complexity="high")
        assert message is not None and "duplicate" in message

    def test_accepts_valid_set(self) -> None:
        claims = [_claim(), _claim(id="C2", kind="refute"), _claim(id="C3")]
        assert _validator().validate(claims, agent_name="context", complexity="high") is None


class TestClaimTaskRenderer:
    def test_renders_fixed_table_with_one_row_per_claim(self) -> None:
        rendered = ClaimTaskRenderer().render([_claim(), _claim(id="C2")])
        assert rendered.startswith("### CLAIM VERIFICATION")
        assert "| ID | Verdict |" in rendered
        assert "| C1 | | | |" in rendered
        assert "| C2 | | | |" in rendered
