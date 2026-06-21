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
            "<!-- bot-agent-genius -->\n<agentRunConfig>{\"task\":\"dev\"}</agentRunConfig>",
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


class TestRetryCallBypass:
    def test_retry_call_bypasses_same_agent_loop(self):
        """When retry_call=True, same-agent loop detection is skipped."""
        result = check_loop_guard(
            "<!-- bot-agent-dev -->\n@@dev <!-- auto-retry: 1 -->",
            "dev",
            retry_call=True,
        )
        assert result["SKIP_AGENT"] == "false"

    def test_normal_same_agent_still_blocked_without_flag(self):
        """Without retry_call flag, same-agent loops are still blocked."""
        result = check_loop_guard(
            "<!-- bot-agent-dev -->\nContent",
            "dev",
            retry_call=False,
        )
        assert result["SKIP_AGENT"] == "true"

    def test_retry_call_default_is_false(self):
        """Default behavior should NOT bypass (backward compat)."""
        result = check_loop_guard(
            "<!-- bot-agent-dev -->\nContent",
            "dev",
        )
        assert result["SKIP_AGENT"] == "true"
