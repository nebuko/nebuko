"""Tests for circuit_breaker.py."""

import pytest

from circuit_breaker import count_bot_markers, check_circuit_breaker


class TestCountBotMarkers:
    """Test bot marker counting in comment lists."""

    def test_no_comments(self):
        bot, summary = count_bot_markers([])
        assert bot == 0
        assert summary == 0

    def test_normal_comments_only(self):
        comments = [
            {"body": "This is a regular comment"},
            {"body": "Another normal comment"},
        ]
        bot, summary = count_bot_markers(comments)
        assert bot == 0
        assert summary == 0

    def test_dev_agent_marker(self):
        comments = [{"body": "<!-- bot-agent-dev -->\nFix applied"}]
        bot, summary = count_bot_markers(comments)
        assert bot == 1
        assert summary == 0

    def test_summary_marker_counted_correctly(self):
        comments = [{"body": "<!-- bot-agent-summary -->\nSummary here"}]
        bot, summary = count_bot_markers(comments)
        assert bot == 1
        assert summary == 1

    def test_multiple_markers_in_one_comment(self):
        body = "<!-- bot-agent-dev --><!-- bot-agent-genius -->"
        comments = [{"body": body}]
        bot, summary = count_bot_markers(comments)
        assert bot == 2
        assert summary == 0

    def test_mixed_comments(self):
        comments = [
            {"body": "Human comment"},
            {"body": "<!-- bot-agent-dev -->\nResponse"},
            {"body": "<!-- bot-agent-ctx -->\nContext"},
            {"body": "<!-- bot-agent-summary -->\nSummary"},
        ]
        bot, summary = count_bot_markers(comments)
        assert bot == 3
        assert summary == 1

    def test_multiple_markers_per_comment_body(self):
        body = "<!-- bot-agent-dev -->\nSome text\n<!-- bot-agent-dev -->"
        comments = [{"body": body}]
        bot, summary = count_bot_markers(comments)
        assert bot == 2
        assert summary == 0

    def test_comments_with_empty_body(self):
        comments = [{"body": ""}, {"body": None}]
        # Should not crash - body defaults to empty string
        bot, summary = count_bot_markers([{"body": ""}, {}])
        assert bot == 0
        assert summary == 0


class TestCircuitBreakerLogic:
    """Test agent call calculation logic."""

    def _calc_calls(self, comments):
        """Helper to calculate agent calls from comments."""
        bot, summary = count_bot_markers(comments)
        return max(0, (bot - summary) // 2), bot, summary

    def test_no_calls_when_no_markers(self):
        calls, _, _ = self._calc_calls([{"body": "Normal comment"}])
        assert calls == 0

    def test_two_markers_equals_one_call(self):
        """Each invocation produces 2 markers (trigger + response)."""
        comments = [
            {"body": "<!-- bot-agent-dev -->\nTrigger"},
            {"body": "<!-- bot-agent-dev -->\nResponse"},
        ]
        calls, _, _ = self._calc_calls(comments)
        assert calls == 1

    def test_four_markers_equals_two_calls(self):
        comments = [
            {"body": "<!-- bot-agent-genius -->\nT1"},
            {"body": "<!-- bot-agent-genius -->\nR1"},
            {"body": "<!-- bot-agent-ctx -->\nT2"},
            {"body": "<!-- bot-agent-ctx -->\nR2"},
        ]
        calls, _, _ = self._calc_calls(comments)
        assert calls == 2

    def test_summary_markers_excluded_from_call_count(self):
        comments = [
            {"body": "<!-- bot-agent-dev -->\nT"},
            {"body": "<!-- bot-agent-dev -->\nR"},
            {"body": "<!-- bot-agent-summary -->\nSummary"},
        ]
        calls, bot, summary = self._calc_calls(comments)
        assert calls == 1
        assert bot == 3
        assert summary == 1

    def test_odd_number_of_markers_rounds_down(self):
        comments = [
            {"body": "<!-- bot-agent-dev -->\nOnly one marker"},
        ]
        calls, _, _ = self._calc_calls(comments)
        assert calls == 0

    def test_negative_never_happens(self):
        """If somehow summary > bot, result should be 0 not negative."""
        comments = [
            {"body": "<!-- bot-agent-summary -->\nS1"},
            {"body": "<!-- bot-agent-summary -->\nS2"},
        ]
        calls, _, _ = self._calc_calls(comments)
        assert calls == 0


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


class TestCheckCircuitBreakerHistoryProvider:
    """Test the injected history-provider seam (no network)."""

    _BOTS = ["<!-- bot-agent-dev --> ran"] * 10

    def test_provider_counts_markers_without_client(self, mocker):
        spy = mocker.patch("circuit_breaker.get_issue_comments")
        post_spy = mocker.patch("circuit_breaker.post_comment")
        result = check_circuit_breaker(
            issue_number=42,
            threshold=5,
            history_provider=lambda: self._BOTS,
        )
        assert result["CIRCUIT_BREAKER_TRIPPED"] == "true"
        assert result["AGENT_CALLS"] == "5"
        spy.assert_not_called()

    def test_provider_path_does_not_post_warning(self, mocker):
        """Provider path is pure decision-making: caller owns posting."""
        post_spy = mocker.patch("circuit_breaker.post_comment")
        check_circuit_breaker(
            issue_number=42,
            threshold=5,
            history_provider=lambda: self._BOTS,
        )
        post_spy.assert_not_called()

    def test_provider_below_threshold_not_tripped(self, mocker):
        spy = mocker.patch("circuit_breaker.get_issue_comments")
        result = check_circuit_breaker(
            issue_number=42,
            threshold=10,
            history_provider=lambda: self._BOTS,
        )
        assert result["CIRCUIT_BREAKER_TRIPPED"] == "false"
        assert result["AGENT_CALLS"] == "5"
        spy.assert_not_called()

    def test_provider_summary_markers_excluded(self):
        bodies = [
            "<!-- bot-agent-summary -->",
            "<!-- bot-agent-dev --> a",
            "<!-- bot-agent-dev --> b",
            "<!-- bot-agent-dev --> c",
            "<!-- bot-agent-dev --> d",
        ]
        # 4 dev markers, 1 summary marker -> (4-1)//2 = 2 calls
        result = check_circuit_breaker(
            issue_number=42,
            threshold=1,
            history_provider=lambda: bodies,
        )
        assert result["AGENT_CALLS"] == "2"
        assert result["CIRCUIT_BREAKER_TRIPPED"] == "true"

    def test_empty_provider_not_tripped(self):
        result = check_circuit_breaker(
            issue_number=42,
            threshold=1,
            history_provider=lambda: [],
        )
        assert result["CIRCUIT_BREAKER_TRIPPED"] == "false"
        assert result["AGENT_CALLS"] == "0"
