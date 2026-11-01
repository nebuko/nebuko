"""Tests for the verify-before-commit claims loop (issue #1250)."""

import pytest
from pydantic import ValidationError

from nebuko.engine.verify import ClaimsValidator, ClaimTaskRenderer, VerificationClaim


def _claim(id: str = "C1", kind: str = "support", **kwargs) -> VerificationClaim:
    defaults = {
        "id": id,
        "claim": "tenacity is maintained and provides exponential backoff",
        "kind": kind,
        "category": "build_vs_buy",
        "if_wrong": "fall back to a hand-rolled retry helper in the plan",
    }
    defaults.update(kwargs)
    return VerificationClaim(**defaults)


class TestVerificationClaim:
    def test_rejects_if_wrong_shorter_than_ten_chars(self):
        with pytest.raises(ValidationError):
            _claim(if_wrong="retry")

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValidationError):
            _claim(kind="confirm")


class TestClaimsValidator:
    def test_rejects_duplicate_ids(self):
        validator = ClaimsValidator()
        result = validator.validate("context", [_claim(), _claim()], "low")
        assert not result.ok
        assert any("duplicate claim id" in e for e in result.errors)

    def test_rejects_claims_on_non_context_callee(self):
        validator = ClaimsValidator()
        result = validator.validate("architect", [_claim()], "low")
        assert not result.ok

    def test_rejects_too_many_claims_for_complexity(self):
        validator = ClaimsValidator()
        claims = [_claim(id=f"C{i}") for i in range(1, 5)]
        result = validator.validate("context", claims, "low")  # cap 3 at low
        assert not result.ok

    def test_rejects_refute_ratio_below_one_third(self):
        validator = ClaimsValidator()
        claims = [_claim(), _claim(), _claim()]
        result = validator.validate("context", claims, "low")
        assert not result.ok

    def test_accepts_valid_claim_set(self):
        validator = ClaimsValidator()
        claims = [_claim(), _claim(id="C2", kind="refute")]
        result = validator.validate("context", claims, "low")
        assert result.ok


class TestClaimTaskRenderer:
    def test_renders_fixed_block_with_numbered_claims(self):
        renderer = ClaimTaskRenderer()
        claims = [_claim(), _claim(id="C2", kind="refute")]
        out = renderer.render("check the plan", claims)
        assert out.startswith("check the plan")
        assert ClaimTaskRenderer.HEADER in out
        assert "C1" in out
        assert "C2" in out
