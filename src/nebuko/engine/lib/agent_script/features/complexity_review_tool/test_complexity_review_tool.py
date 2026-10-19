"""Tests for the complexity_review tool wiring (issue #869)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))  # src/nebuko/engine/lib

from agent_script.features.complexity_review_tool import (  # noqa: E402
    ComplexityReviewAction,
    action_to_request,
    clear_complexity_review_run,
    get_recorded_review_request,
    record_review_request,
    reset_complexity_review_registry,
)
from agent_script.sub_domains.tool_resolution.tool_resolution import (  # noqa: E402
    resolve_toolset,
)
from complexity_governor import (  # noqa: E402
    ComplexityReviewRequest,
    EvidenceBundle,
    HumanSignals,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_complexity_review_registry()
    yield
    reset_complexity_review_registry()


def _action(**kwargs) -> ComplexityReviewAction:
    payload = {
        "evidence": EvidenceBundle(human=HumanSignals(changes_requested=3)),
        "feeling": "loop detected",
    }
    payload.update(kwargs)
    return ComplexityReviewAction(**payload)


class TestToolResolution:
    def test_complexity_review_registers(self):
        resolved = resolve_toolset(["complexity_review"], "dev")
        assert [t.name for t in resolved.sdk_tools] == ["ComplexityReviewTool"]

    def test_combined_with_control_plane_tools(self):
        resolved = resolve_toolset(
            ["think", "finish", "finalize", "complexity_review"], "architect"
        )
        names = [t.name for t in resolved.sdk_tools]
        assert "ComplexityReviewTool" in names
        assert resolved.dispatch_tool_enabled


class TestActionToRequest:
    def test_valid_action_maps(self):
        request = action_to_request(_action(suggested_tier="high"))
        assert request is not None
        assert request.feeling == "loop detected"
        assert request.suggested_tier == "high"

    def test_empty_evidence_rejected(self):
        action = _action(evidence=EvidenceBundle())
        assert action_to_request(action) is None

    def test_blank_feeling_rejected(self):
        action = _action(feeling="   ")
        assert action_to_request(action) is None


class TestRegistry:
    def test_record_and_get(self):
        request = ComplexityReviewRequest(
            evidence=EvidenceBundle(human=HumanSignals(approvals=1)),
            feeling="weird signal",
        )
        record_review_request("run-1", request)
        assert get_recorded_review_request("run-1") == request
        assert get_recorded_review_request("missing") is None

    def test_clear_run(self):
        request = ComplexityReviewRequest(
            evidence=EvidenceBundle(human=HumanSignals(approvals=1)),
            feeling="weird signal",
        )
        record_review_request("run-1", request)
        clear_complexity_review_run("run-1")
        assert get_recorded_review_request("run-1") is None
