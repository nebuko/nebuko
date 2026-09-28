"""Spec tests for the human metrics of the issue-close resume (issue #678, spec v3).

Covers the scenarios S1-S9 of the frozen spec:

- S1 human comment count (any human comment counts, incl. gate approvals)
- S2 human intervention count after the first implementation turn
- S3 human command count vs. agent tool invocations
- S4 reading-time estimate (configurable wpm; scope = body + all comments)
- S5 wall-clock duration (first agent start -> close)
- S6 agent time spent (benefit proxy, no counterfactual baseline)
- S7 human response latency (agent turn -> next human reply)
- S8 first-pass acceptance
- S9 missing/partial data -> ``None`` + a reason in ``unavailable`` (never ``0``)

Against the stubs these tests are expected to FAIL (``NotImplementedError``);
they lock the contract for the implement step.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(
    0,
    os.path.dirname(os.path.abspath(__file__)),
)

from human_metrics import (  # noqa: E402
    CommentRecord,
    HumanMetrics,
    HumanMetricsAnalyzer,
    HumanMetricsConfig,
    HumanMetricsRenderer,
    IssueData,
)

T0 = datetime(2026, 9, 1, 10, 0, 0)


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _human(login: str, body: str = "", minutes: float = 0, command: bool = False):
    return CommentRecord(
        author_login=login,
        author_is_bot=False,
        body=body,
        created_at=_at(minutes),
        is_human_command=command,
    )


def _agent(login: str, body: str = "", minutes: float = 0, role: str = "dev",
           impl: bool = False):
    return CommentRecord(
        author_login=login,
        author_is_bot=True,
        body=body,
        created_at=_at(minutes),
        agent_role=role,
        is_implementation_turn=impl,
    )


def _issue(comments, *, body="", first_agent_start=T0, closed_at=None,
           tool_invocations=None, agent_time_seconds=None):
    return IssueData(
        body=body,
        comments=tuple(comments),
        first_agent_start=first_agent_start,
        closed_at=closed_at,
        agent_tool_invocations=tool_invocations,
        agent_time_spent_seconds=agent_time_seconds,
    )


class TestConfigDefaults(unittest.TestCase):
    def test_reading_speed_defaults_to_200_wpm(self):
        self.assertEqual(HumanMetricsConfig().reading_speed_wpm, 200)

    def test_bot_suffix_and_implementation_roles_defaults(self):
        config = HumanMetricsConfig()
        self.assertEqual(config.bot_login_suffixes, ("[bot]",))
        self.assertEqual(config.implementation_agent_roles, ("dev", "stub"))


class TestHumanCommentCount(unittest.TestCase):
    """S1: human comment count, incl. short gate/approval replies."""

    def test_counts_only_non_bot_comments(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue(
            [
                _human("alice", "please change direction"),
                _agent("openhands", "done", minutes=1),
                _human("alice", "continue", minutes=2),
            ]
        )
        self.assertEqual(analyzer.analyze(issue).human_comment_count, 2)

    def test_counts_gate_approval_comment(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue([_human("alice", "approved! continue")])
        self.assertEqual(analyzer.analyze(issue).human_comment_count, 1)

    def test_allowlist_restricts_human_comments(self):
        analyzer = HumanMetricsAnalyzer(
            HumanMetricsConfig(human_logins=frozenset({"alice"}))
        )
        issue = _issue([_human("alice", "a"), _human("bob", "b")])
        self.assertEqual(analyzer.analyze(issue).human_comment_count, 1)


class TestHumanInterventionCount(unittest.TestCase):
    """S2: human comments emitted after the first implementation turn."""

    def test_counts_human_comments_after_implementation(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue(
            [
                _human("alice", "spec ok"),
                _agent("openhands", "implementing", minutes=1, impl=True),
                _human("alice", "please fix the output", minutes=2),
                _agent("openhands", "fixed", minutes=3),
                _human("alice", "more clarity needed", minutes=4),
            ]
        )
        self.assertEqual(analyzer.analyze(issue).human_intervention_count, 2)

    def test_human_comment_before_implementation_is_not_an_intervention(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue(
            [
                _human("alice", "go ahead"),
                _agent("openhands", "implementing", minutes=1, impl=True),
            ]
        )
        self.assertEqual(analyzer.analyze(issue).human_intervention_count, 0)


class TestCommandCounts(unittest.TestCase):
    """S3: human workflow commands vs. agent tool invocations."""

    def test_counts_human_commands_and_agent_tool_invocations(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue(
            [
                _human("alice", "@@dev continue", command=True),
                _human("alice", "nice work"),
                _agent("openhands", "working", minutes=1),
            ],
            tool_invocations=7,
        )
        metrics = analyzer.analyze(issue)
        self.assertEqual(metrics.human_command_count, 1)
        self.assertEqual(metrics.agent_tool_invocation_count, 7)


class TestReadingTime(unittest.TestCase):
    """S4: reading time = words(body + all comments) / reading_speed_wpm."""

    def test_reading_time_uses_configured_wpm_over_body_and_comments(self):
        analyzer = HumanMetricsAnalyzer(
            HumanMetricsConfig(reading_speed_wpm=200)
        )
        issue = _issue(
            [_human("alice", "one two three"), _agent("openhands", "four five", minutes=1)],
            body="hello world",
        )
        # 2 (body) + 3 + 2 = 7 words / 200 wpm * 60
        self.assertAlmostEqual(
            analyzer.analyze(issue).reading_time_seconds, 7 / 200 * 60
        )


class TestWallClock(unittest.TestCase):
    """S5: closed_at - first_agent_start."""

    def test_wall_clock_is_close_minus_first_agent_start(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue([], first_agent_start=T0, closed_at=_at(60))
        self.assertAlmostEqual(
            analyzer.analyze(issue).wall_clock_seconds, 3600.0
        )


class TestAgentEffortProxy(unittest.TestCase):
    """S6: agent time spent, forwarded as-is (no baseline asserted)."""

    def test_agent_time_spent_is_forwarded(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue([], agent_time_seconds=123.4)
        self.assertAlmostEqual(
            analyzer.analyze(issue).agent_time_spent_seconds, 123.4
        )


class TestResponseLatency(unittest.TestCase):
    """S7: mean of (next human reply - agent turn) for adjacent pairs."""

    def test_mean_latency_over_agent_then_human_pairs(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue(
            [
                _agent("openhands", "first", minutes=0),
                _human("alice", "reply", minutes=10),
                _agent("openhands", "second", minutes=20),
                _human("alice", "reply", minutes=25),
            ]
        )
        # mean(10 min, 5 min) = 7.5 min = 450 s
        self.assertAlmostEqual(
            analyzer.analyze(issue).human_response_latency_seconds, 450.0
        )


class TestFirstPassAcceptance(unittest.TestCase):
    """S8: accepted iff no human comment after the first implementation turn."""

    def test_first_pass_accepted_when_no_human_intervention(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue(
            [
                _human("alice", "go"),
                _agent("openhands", "impl", minutes=1, impl=True),
            ]
        )
        self.assertIs(analyzer.analyze(issue).first_pass_accepted, True)

    def test_not_accepted_when_human_intervenes(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue(
            [
                _agent("openhands", "impl", minutes=1, impl=True),
                _human("alice", "please change this", minutes=2),
            ]
        )
        self.assertIs(analyzer.analyze(issue).first_pass_accepted, False)

    def test_unavailable_when_no_implementation_turn(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue([_human("alice", "hello")])
        metrics = analyzer.analyze(issue)
        self.assertIsNone(metrics.first_pass_accepted)
        self.assertIn("first_pass", " ".join(metrics.unavailable))


class TestUnavailableData(unittest.TestCase):
    """S9: missing inputs -> None + reason, never a misleading 0."""

    def test_missing_first_agent_start_marks_wall_clock_unavailable(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig())
        issue = _issue([], first_agent_start=None, closed_at=_at(60))
        metrics = analyzer.analyze(issue)
        self.assertIsNone(metrics.wall_clock_seconds)
        self.assertIn("wall_clock", " ".join(metrics.unavailable))

    def test_non_positive_reading_speed_marks_reading_time_unavailable(self):
        analyzer = HumanMetricsAnalyzer(HumanMetricsConfig(reading_speed_wpm=0))
        issue = _issue([_human("alice", "hello world")])
        metrics = analyzer.analyze(issue)
        self.assertIsNone(metrics.reading_time_seconds)
        self.assertIn("reading", " ".join(metrics.unavailable))

    def test_render_prints_reason_instead_of_zero(self):
        renderer = HumanMetricsRenderer(HumanMetricsConfig())
        metrics = HumanMetrics(
            wall_clock_seconds=None,
            unavailable={"wall_clock_seconds": "first agent start timestamp missing"},
        )
        rendered = renderer.render(metrics)
        self.assertIn("first agent start timestamp missing", rendered)


class TestRenderer(unittest.TestCase):
    """The renderer emits a Markdown section labelled as an estimate/proxy."""

    def test_render_produces_markdown_section(self):
        renderer = HumanMetricsRenderer(HumanMetricsConfig())
        metrics = HumanMetrics(
            human_comment_count=3,
            wall_clock_seconds=3600.0,
            human_response_latency_seconds=450.0,
            first_pass_accepted=True,
        )
        rendered = renderer.render(metrics)
        self.assertIn("Human metrics", rendered)
        self.assertIn("3", rendered)
        self.assertIn("estimate", rendered.lower())


if __name__ == "__main__":
    unittest.main()
