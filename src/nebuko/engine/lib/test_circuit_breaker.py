"""Tests for circuit_breaker.py."""

import pytest

from circuit_breaker import count_bot_markers, check_circuit_breaker, circuit_breaker_warning


class TestCountBotMarkers:
    """Test bot marker counting in comment lists."""

    def test_no_comments(self):
        assert count_bot_markers([]) == 0

    def test_normal_comments_only(self):
        comments = [
            {"body": "This is a regular comment"},
            {"body": "Another normal comment"},
        ]
        assert count_bot_markers(comments) == 0

    def test_dev_agent_marker(self):
        comments = [{"body": "<!-- bot-agent-dev -->\nFix applied"}]
        assert count_bot_markers(comments) == 1

    def test_multiple_markers_in_one_comment(self):
        body = "<!-- bot-agent-dev --><!-- bot-agent-architect -->"
        assert count_bot_markers([{"body": body}]) == 2

    def test_mixed_comments(self):
        comments = [
            {"body": "Human comment"},
            {"body": "<!-- bot-agent-dev -->\nResponse"},
            {"body": "<!-- bot-agent-context -->\nContext"},
        ]
        assert count_bot_markers(comments) == 2

    def test_multiple_markers_per_comment_body(self):
        body = "<!-- bot-agent-dev -->\nSome text\n<!-- bot-agent-dev -->"
        assert count_bot_markers([{"body": body}]) == 2

    def test_comments_with_empty_body(self):
        # Should not crash - body defaults to empty string
        assert count_bot_markers([{"body": ""}, {}]) == 0


class TestCircuitBreakerLogic:
    """Test agent call calculation logic."""

    def _calc_calls(self, comments):
        """Helper to calculate agent calls from comments."""
        return max(0, count_bot_markers(comments) // 2)

    def test_no_calls_when_no_markers(self):
        assert self._calc_calls([{"body": "Normal comment"}]) == 0

    def test_two_markers_equals_one_call(self):
        """Each invocation produces 2 markers (trigger + response)."""
        comments = [
            {"body": "<!-- bot-agent-dev -->\nTrigger"},
            {"body": "<!-- bot-agent-dev -->\nResponse"},
        ]
        assert self._calc_calls(comments) == 1

    def test_four_markers_equals_two_calls(self):
        comments = [
            {"body": "<!-- bot-agent-architect -->\nT1"},
            {"body": "<!-- bot-agent-architect -->\nR1"},
            {"body": "<!-- bot-agent-context -->\nT2"},
            {"body": "<!-- bot-agent-context -->\nR2"},
        ]
        assert self._calc_calls(comments) == 2

    def test_odd_number_of_markers_rounds_down(self):
        comments = [
            {"body": "<!-- bot-agent-dev -->\nOnly one marker"},
        ]
        assert self._calc_calls(comments) == 0


class TestCheckCircuitBreakerDryRun:
    """Test circuit breaker with dry-run mode (no actual API calls)."""

    def test_dry_run_does_not_fail_without_token(self, mocker):
        """Dry run should not fail when no GitHub token is provided."""
        mocker.patch(
            "circuit_breaker.get_issue_comments",
            return_value=[],
        )
        result = check_circuit_breaker(
            issue_number=42,
            threshold=5,
            dry_run=True,
        )
        assert result["CIRCUIT_BREAKER_TRIPPED"] == "false"
        assert result["AGENT_CALLS"] == "0"


class TestCircuitBreakerWarning:
    """The trip warning names the per-issue unlock command (issue #839)."""

    def test_warning_reports_the_counts(self):
        warning = circuit_breaker_warning(agent_calls=30, threshold=25)

        assert "30/25" in warning

    def test_warning_names_the_update_command(self):
        warning = circuit_breaker_warning(agent_calls=30, threshold=25)

        assert "/nebuko update circuit-breaker --value <n>" in warning
