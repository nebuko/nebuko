"""Platform-free vocabulary for the orchestration engine (issue #590, Stage A).

Leaf module of the ``engine`` package: the only external type it names is the
reused ``workflow_state.workflow_state.WorkflowState`` — never re-declared here.

Stub step: class skeletons only. Bodies are materialised by @@dev in the
implement step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from workflow_state.workflow_state import WorkflowState

__all__ = [
    "Trigger",
    "CommentEntry",
    "RoutingDecision",
    "ExitCode",
    "RunOutcome",
]


# @stub-protected
@dataclass(frozen=True)
class Trigger:
    """Normalized platform event that starts one engine run."""

    author: str
    body: str
    # "issue" | "issue_comment" | "workflow_dispatch" | "pull_request_review_comment"
    type: str
    number: int
    # raw event, for legacy resolvers that still read event JSON
    payload: Mapping[str, Any] = field(default_factory=dict)


# @stub-protected
@dataclass(frozen=True)
class CommentEntry:
    """One entry of the platform comment history."""

    author: str
    body: str
    created_at: str  # ISO-8601 UTC, as emitted by the GitHub API


# @stub-protected
@dataclass(frozen=True)
class RoutingDecision:
    """The engine's normalized view of the ``dispatch_next`` tool payload."""

    status: str  # "advance" | "loop" | "hold"
    agent: str | None = None
    complexity: str = "medium"
    branch: str | None = None
    reason: str | None = None


# @stub-protected
class ExitCode(IntEnum):
    """Process exit codes distinguishing the three terminal outcomes."""

    ADVANCE = 0  # next step proceeds
    ERROR = 1  # uncaught failure / contract violation
    HOLD = 10  # human gate: stop, human resumes
    TERMINAL = 20  # terminal journal marker written


# @stub-protected
@dataclass(frozen=True)
class RunOutcome:
    """Everything one engine run produced, including the routing decision."""

    exit_code: ExitCode
    routing: RoutingDecision
    prompt: str | None
    posted_body: str | None
    state: WorkflowState | None
    phases: tuple[str, ...]  # ordered "[phase:*]" markers actually emitted
