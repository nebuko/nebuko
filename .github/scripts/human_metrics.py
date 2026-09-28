"""Human metrics for the issue-close resume (issue #678, spec v3).

Pure, side-effect-free models and computation for the human-facing metrics
appended to the metrics summary that is posted when an issue closes.

All I/O stays in ``summarize_metrics.py``: it builds ``IssueData`` from the
comments/agent-metrics payloads it already fetches, then calls
``HumanMetricsAnalyzer.analyze`` and ``HumanMetricsRenderer.render``.

The models are immutable, the analyzer/renderer hold only their config, and any
metric whose inputs are missing degrades to ``None`` with a reason in
``unavailable`` (Scenario 9) instead of a misleading zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


# @stub-protected
@dataclass(frozen=True)
class HumanMetricsConfig:
    """All tunable inputs for the human-metrics computation, in one place.

    ``human_logins`` is an allowlist enforced by the analyzer: an empty
    allowlist means "any non-bot author counts as human". ``bot_login_suffixes``
    is used by the caller when building ``CommentRecord``s (the repo's primary
    bot signal is the comment marker, not the login suffix), so it lives here
    only to keep every knob in one validated entity.
    """

    reading_speed_wpm: int = 200
    human_logins: frozenset[str] = frozenset()
    bot_login_suffixes: tuple[str, ...] = ("[bot]",)
    implementation_agent_roles: tuple[str, ...] = ("dev", "stub")
    # @stub-protected


# @stub-protected
@dataclass(frozen=True)
class CommentRecord:
    """A single issue comment normalised for the human-metrics rules."""

    author_login: str
    author_is_bot: bool
    body: str
    created_at: datetime
    agent_role: str | None = None
    is_human_command: bool = False
    is_implementation_turn: bool = False
    # @stub-protected


# @stub-protected
@dataclass(frozen=True)
class IssueData:
    """Raw, already-collected inputs for one issue's human metrics."""

    body: str
    comments: tuple[CommentRecord, ...]
    first_agent_start: datetime | None
    closed_at: datetime | None
    agent_tool_invocations: int | None
    agent_time_spent_seconds: float | None
    # @stub-protected


# @stub-protected
@dataclass(frozen=True)
class HumanMetrics:
    """Immutable result of the human-metrics computation for one issue.

    A ``None`` value means the metric could not be computed; the matching entry
    in ``unavailable`` carries the human-readable reason (Scenario 9).
    """

    human_comment_count: int | None = None
    human_intervention_count: int | None = None
    human_command_count: int | None = None
    agent_tool_invocation_count: int | None = None
    reading_time_seconds: float | None = None
    wall_clock_seconds: float | None = None
    agent_time_spent_seconds: float | None = None
    human_response_latency_seconds: float | None = None
    first_pass_accepted: bool | None = None
    unavailable: Mapping[str, str] = field(default_factory=dict)
    # @stub-protected


# @stub-protected
class HumanMetricsAnalyzer:
    """Pure computation of human metrics from already-collected issue data."""

    # @stub-protected
    def __init__(self, config: HumanMetricsConfig) -> None:
        self._config = config

    def _is_human(self, comment: CommentRecord) -> bool:
        """A comment is human unless flagged as a bot or excluded by the allowlist.

        An empty ``human_logins`` allowlist means "any non-bot author is human".
        """
        if comment.author_is_bot:
            return False
        allowlist = self._config.human_logins
        return not allowlist or comment.author_login in allowlist

    def _is_implementation_turn(self, comment: CommentRecord) -> bool:
        """Implementation turns come from the explicit flag or the configured roles."""
        if comment.is_implementation_turn:
            return True
        role = comment.agent_role
        return role is not None and role in self._config.implementation_agent_roles

    def _reading_time(
        self, issue: IssueData, unavailable: dict
    ) -> float | None:
        if self._config.reading_speed_wpm <= 0:
            unavailable["reading_time_seconds"] = "reading speed must be greater than zero"
            return None
        text = issue.body + " " + " ".join(c.body for c in issue.comments)
        words = len(text.split())
        return words / self._config.reading_speed_wpm * 60

    def _wall_clock(self, issue: IssueData, unavailable: dict) -> float | None:
        if issue.first_agent_start is None or issue.closed_at is None:
            unavailable["wall_clock_seconds"] = (
                "first agent start timestamp or issue close time missing"
            )
            return None
        seconds = (issue.closed_at - issue.first_agent_start).total_seconds()
        if seconds < 0:
            unavailable["wall_clock_seconds"] = (
                "issue close time precedes first agent start"
            )
            return None
        return seconds

    def _response_latency(
        self, comments: tuple[CommentRecord, ...], unavailable: dict
    ) -> float | None:
        latencies = [
            (following.created_at - preceding.created_at).total_seconds()
            for preceding, following in zip(comments, comments[1:])
            if preceding.author_is_bot and self._is_human(following)
        ]
        if not latencies:
            unavailable["human_response_latency_seconds"] = (
                "no agent turn followed by a human reply"
            )
            return None
        return sum(latencies) / len(latencies)

    # @stub-protected
    def analyze(self, issue: IssueData) -> HumanMetrics:
        unavailable: dict[str, str] = {}
        comments = issue.comments

        human_comments = [c for c in comments if self._is_human(c)]
        human_comment_count = len(human_comments)

        implementation_indexes = [
            i for i, c in enumerate(comments) if self._is_implementation_turn(c)
        ]
        first_implementation = implementation_indexes[0] if implementation_indexes else None

        if first_implementation is None:
            human_intervention_count = None
            unavailable["human_intervention_count"] = "no implementation turn found"
            first_pass_accepted = None
            unavailable["first_pass_accepted"] = "no implementation turn found"
        else:
            human_intervention_count = sum(
                1
                for i, c in enumerate(comments)
                if i > first_implementation and self._is_human(c)
            )
            first_pass_accepted = human_intervention_count == 0

        human_command_count = sum(1 for c in human_comments if c.is_human_command)

        if issue.agent_tool_invocations is None:
            agent_tool_invocation_count = None
            unavailable["agent_tool_invocation_count"] = (
                "agent tool invocation count unavailable"
            )
        else:
            agent_tool_invocation_count = issue.agent_tool_invocations

        if issue.agent_time_spent_seconds is None:
            agent_time_spent_seconds = None
            unavailable["agent_time_spent_seconds"] = "agent runtime data unavailable"
        else:
            agent_time_spent_seconds = issue.agent_time_spent_seconds

        return HumanMetrics(
            human_comment_count=human_comment_count,
            human_intervention_count=human_intervention_count,
            human_command_count=human_command_count,
            agent_tool_invocation_count=agent_tool_invocation_count,
            reading_time_seconds=self._reading_time(issue, unavailable),
            wall_clock_seconds=self._wall_clock(issue, unavailable),
            agent_time_spent_seconds=agent_time_spent_seconds,
            human_response_latency_seconds=self._response_latency(comments, unavailable),
            first_pass_accepted=first_pass_accepted,
            unavailable=unavailable,
        )


# @stub-protected
class HumanMetricsRenderer:
    """Renders a HumanMetrics result as a Markdown resume section."""

    # @stub-protected
    def __init__(self, config: HumanMetricsConfig) -> None:
        self._config = config

    def _cell(self, value, unavailable: Mapping[str, str], key: str, formatter) -> str:
        if value is None:
            reason = unavailable.get(key)
            return f"unavailable — {reason}" if reason else "unavailable"
        return formatter(value)

    def _as_minutes(self, seconds: float) -> str:
        return f"{seconds / 60:.1f} min"

    # @stub-protected
    def render(self, metrics: HumanMetrics) -> str:
        unavailable = metrics.unavailable
        rows = [
            (
                "Human comments",
                self._cell(metrics.human_comment_count, unavailable,
                           "human_comment_count", str),
            ),
            (
                "Human interventions after implementation",
                self._cell(metrics.human_intervention_count, unavailable,
                           "human_intervention_count", str),
            ),
            (
                "Human workflow commands",
                self._cell(metrics.human_command_count, unavailable,
                           "human_command_count", str),
            ),
            (
                "Agent tool invocations",
                self._cell(metrics.agent_tool_invocation_count, unavailable,
                           "agent_tool_invocation_count", str),
            ),
            (
                "Estimated human reading time",
                self._cell(metrics.reading_time_seconds, unavailable,
                           "reading_time_seconds", self._as_minutes),
            ),
            (
                "Wall-clock duration (first agent start → close)",
                self._cell(metrics.wall_clock_seconds, unavailable,
                           "wall_clock_seconds", self._as_minutes),
            ),
            (
                "Agent time spent (benefit proxy)",
                self._cell(metrics.agent_time_spent_seconds, unavailable,
                           "agent_time_spent_seconds", self._as_minutes),
            ),
            (
                "Average human response latency",
                self._cell(metrics.human_response_latency_seconds, unavailable,
                           "human_response_latency_seconds", self._as_minutes),
            ),
            (
                "Accepted on first pass",
                self._cell(metrics.first_pass_accepted, unavailable,
                           "first_pass_accepted", lambda v: "Yes" if v else "No"),
            ),
        ]

        lines = [
            "## 🧑‍💻 Human metrics",
            "",
            "| Metric | Value |",
            "|--------|-------|",
        ]
        lines.extend(f"| {label} | {value} |" for label, value in rows)
        lines.append("")
        lines.append(
            "> _Human metrics are estimates/proxies derived from issue text and workflow "
            f"timestamps (reading speed: {self._config.reading_speed_wpm} wpm; scope: issue body "
            "+ all comments). Agent time spent is a proxy for human effort avoided, not a "
            "measured saving._"
        )
        return "\n".join(lines)
