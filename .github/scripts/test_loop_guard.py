"""Tests for loop_guard.py."""

import pytest

from loop_guard import (
    detect_same_agent_loop,
    detect_human_plus_agent_ping,
    check_loop_guard,
)


class TestDetectSameAgentLoop:
    """Test same-agent loop detection."""

    def test_same_agent_detected(self):
        body = "<!-- bot-agent-dev -->\n@@dev please help"
        assert detect_same_agent_loop(body, "dev") is True

    def test_different_agent_not_detected(self):
        body = "<!-- bot-agent-genius -->\n@@ctx please analyze"
        assert detect_same_agent_loop(body, "ctx") is False

    def test_no_bot_marker_returns_false(self):
        body = "Regular human comment @@dev"
        assert detect_same_agent_loop(body, "dev") is False

    def test_issue_reviewer_agent_name(self):
        body = "<!-- bot-agent-issue-reviewer -->\n@@issue-reviewer check"
        assert detect_same_agent_loop(body, "issue-reviewer") is True

    def test_all_agents(self):
        for agent in ("genius", "ctx", "dev", "review", "issue", "issue-reviewer"):
            body = f"<!-- bot-agent-{agent} -->"
            assert detect_same_agent_loop(body, agent) is True
            assert detect_same_agent_loop(body, "other") is False


class TestDetectHumanPlusAgentPing:
    """Test human + agent ping detection."""

    def test_human_and_agent_ping(self):
        body = "@alice @@dev please fix this"
        assert detect_human_plus_agent_ping(body) is True

    def test_agent_only_ping(self):
        body = "@@dev please fix this"
        assert detect_human_plus_agent_ping(body) is False

    def test_human_only_ping(self):
        body = "@alice @bob let's discuss"
        assert detect_human_plus_agent_ping(body) is False

    def test_no_mentions(self):
        body = "Just a regular comment"
        assert detect_human_plus_agent_ping(body) is False

    def test_multiple_agents_one_human(self):
        body = "@charlie @@genius @@dev let's go"
        assert detect_human_plus_agent_ping(body) is True

    def test_empty_body(self):
        assert detect_human_plus_agent_ping("") is False


class TestCheckLoopGuard:
    """Test full loop guard logic."""

    def test_same_agent_loop_skips(self):
        result = check_loop_guard(
            "<!-- bot-agent-dev -->\n@@dev redo",
            "dev",
        )
        assert result["SKIP_AGENT"] == "true"
        assert "loop detected" in result.get("REASON", "").lower()

    def test_cross_agent_allowed(self):
        result = check_loop_guard(
            "<!-- bot-agent-genius -->\n<!-- AGENT_RUN_CONFIG {\"dispatch\": {\"task_hint\": \"dev\"}} -->",
            "dev",
        )
        assert result["SKIP_AGENT"] == "false"

    def test_human_plus_agent_skips(self):
        result = check_loop_guard(
            "@alice @@dev please implement",
            "dev",
        )
        assert result["SKIP_AGENT"] == "true"

    def test_normal_human_request_passes(self):
        result = check_loop_guard(
            "@@dev please build this feature",
            "dev",
        )
        assert result["SKIP_AGENT"] == "false"

    def test_bot_without_matching_agent_passes(self):
        result = check_loop_guard(
            "<!-- bot-agent-ctx -->\nContent here",
            "dev",
        )
        assert result["SKIP_AGENT"] == "false"


def _state_block(version: int, step: str, position=None) -> str:
    """Build a posted WORKFLOW_STATE comment block for guard tests."""
    import json
    state = {
        "version": version,
        "workflow": "dev-review-loop",
        "step": step,
        "agent": "dev",
    }
    if position is not None:
        state["position"] = position
    return (
        f"<!-- WORKFLOW_STATE_START -->\n{json.dumps(state)}\n"
        f"<!-- WORKFLOW_STATE_END -->"
    )


def _nested_position(levels: list[tuple[str, str]]) -> dict:
    """v2 nested position block (outermost first), ``to_state_dict`` shape."""
    out: dict = {}
    node = out
    for i, (wf, step) in enumerate(levels):
        node["workflow_name"] = wf
        node["step_name"] = step
        if i < len(levels) - 1:
            node["step"] = {}
            node = node["step"]
    return out


class TestCheckSameAgentAdvancePositionAware:
    """v2 full-chain comparison (issue #618, review fix for defect 4)."""

    def test_same_agent_different_position_allows(self):
        """step_1 -> step_2 re-enters the same leaf; only the full chain
        distinguishes a legitimate advance from a self loop."""
        from loop_guard import check_same_agent_advance
        body = _state_block(2, "implement", _nested_position([
            ("tmp-impl", "step_1"), ("dev-review-loop", "implement"),
        ]))
        result = check_same_agent_advance(
            body, "tmp-impl/step_2|dev-review-loop/implement", "implement"
        )
        assert result["SKIP_AGENT"] == "false"
        assert "new position" in result["REASON"]

    def test_same_agent_same_position_skips(self):
        from loop_guard import check_same_agent_advance
        body = _state_block(2, "implement", _nested_position([
            ("tmp-impl", "step_1"), ("dev-review-loop", "implement"),
        ]))
        result = check_same_agent_advance(
            body, "tmp-impl/step_1|dev-review-loop/implement", "implement"
        )
        assert result["SKIP_AGENT"] == "true"
        assert "same v2 position" in result["REASON"]

    def test_legacy_trigger_falls_back_to_flat_step(self):
        """v1 trigger (no position) + v2 resolved: flat-step fallback —
        different step allows."""
        from loop_guard import check_same_agent_advance
        body = _state_block(1, "scaffold")
        result = check_same_agent_advance(
            body, "tmp-impl/step_1|dev-review-loop/implement", "implement"
        )
        assert result["SKIP_AGENT"] == "false"
        assert "new step" in result["REASON"]

    def test_legacy_trigger_same_flat_step_skips(self):
        from loop_guard import check_same_agent_advance
        body = _state_block(1, "implement")
        result = check_same_agent_advance(body, "", "implement")
        assert result["SKIP_AGENT"] == "true"

    def test_no_trigger_state_and_no_resolved_path_skips(self):
        from loop_guard import check_same_agent_advance
        result = check_same_agent_advance("", "", "implement")
        assert result["SKIP_AGENT"] == "true"


class TestTriggerPositionPath:
    """Flat-path extraction from posted v2 states (issue #618)."""

    def test_extracts_full_chain_outermost_first(self):
        from loop_guard import trigger_position_path
        body = _state_block(2, "implement", _nested_position([
            ("plan-orchestration", "sub_impl"),
            ("tmp-impl", "step_1"),
            ("dev-review-loop", "implement"),
        ]))
        assert trigger_position_path(body) == (
            "plan-orchestration/sub_impl|tmp-impl/step_1|dev-review-loop/implement"
        )

    def test_last_block_wins(self):
        from loop_guard import trigger_position_path
        body = (
            _state_block(2, "implement", _nested_position([
                ("tmp-impl", "step_1"), ("dev-review-loop", "implement"),
            ]))
            + "\n"
            + _state_block(2, "implement", _nested_position([
                ("tmp-impl", "step_2"), ("dev-review-loop", "implement"),
            ]))
        )
        assert trigger_position_path(body) == (
            "tmp-impl/step_2|dev-review-loop/implement"
        )

    def test_v1_state_returns_empty(self):
        from loop_guard import trigger_position_path
        assert trigger_position_path(_state_block(1, "scaffold")) == ""
