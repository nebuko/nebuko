"""Human metrics for the issue-close resume (issue #678, spec v3).

Pure, side-effect-free models and computation for the human-facing metrics
appended to the metrics summary that is posted when an issue closes.

All I/O stays in ``summarize_metrics.py``: it builds ``IssueData`` from the
comments/agent-metrics payloads it already fetches, then calls
``HumanMetricsAnalyzer.analyze`` and ``HumanMetricsRenderer.render``.

Stub step: data models, class/function skeletons and config only. Method
bodies are materialised by @@dev in the implement step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


# @stub-protected
@dataclass(frozen=True)
class HumanMetricsConfig:
    """All tunable inputs for the human-metrics computation, in one place."""

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
        raise NotImplementedError

    # @stub-protected
    def analyze(self, issue: IssueData) -> HumanMetrics:
        raise NotImplementedError


# @stub-protected
class HumanMetricsRenderer:
    """Renders a HumanMetrics result as a Markdown resume section."""

    # @stub-protected
    def __init__(self, config: HumanMetricsConfig) -> None:
        raise NotImplementedError

    # @stub-protected
    def render(self, metrics: HumanMetrics) -> str:
        raise NotImplementedError
