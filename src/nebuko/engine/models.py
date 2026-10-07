"""Platform-free vocabulary for the orchestration engine (issue #590, Stage A).

Leaf module of the ``engine`` package: the only external type it names is the
reused ``workflow_state.workflow_state.WorkflowState`` — never re-declared here.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from workflow_state.workflow_state import WorkflowState

__all__ = [
    "Trigger",
    "CommentEntry",
    "RoutingDecision",
    "ExitCode",
    "RunOutcome",
    "DeterministicStep",
    "StepResult",
    "NebukoManifest",
    "to_routing_decision",
]


# @stub-protected
class NebukoManifest(BaseModel):
    """Parsed ``.nebuko/nebuko.yaml``: optional per-repo path overrides.

    Paths are resolved relative to the repo root. Both fields are optional, so an
    absent or empty manifest keeps the fixed ``<kind>/`` subfolders.
    """

    model_config = ConfigDict(extra="forbid")

    skills_dir: str | None = None
    default_workflow: str | None = None


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
    # body of the issue this event belongs to ("" when unavailable)
    issue_body: str = ""


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


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() == "true"


# @stub-protected
@dataclass(frozen=True)
class StepResult:
    """Immutable record of one executed deterministic step."""

    step_kind: str
    ok: bool
    detail: str  # human-readable outcome (e.g. "deleted 42 comments on issue 590")
    affected: int  # comments deleted / script exit code


# @stub-protected
@dataclass(frozen=True)
class DeterministicStep:
    """An agent-free step the resolver hands to the engine to execute directly."""

    kind: Literal["delete_comments", "run_script"]
    issue: str = ""  # delete_comments: target issue
    from_comment: str = ""  # delete_comments: --from boundary ("" = all)
    script_path: str = ""  # run_script
    script_args: str = ""
    timeout_minutes: int | None = None

    # @stub-protected
    @classmethod
    def from_resolved(cls, resolved: Mapping[str, str]) -> DeterministicStep | None:
        """Typed view over the resolver env contract; None when no step is present.

        ``DELETE_COMMENTS`` wins over ``RUN_SCRIPT`` on a pathological overlap;
        a pure ``SKIP_AGENT=true`` carrying neither flag resolves to ``None``,
        which keeps the guards' skip verdict (``ExitCode.TERMINAL``) intact.
        """
        if not isinstance(resolved, Mapping):
            return None
        if _is_true(resolved.get("DELETE_COMMENTS")):
            return cls(
                kind="delete_comments",
                issue=str(resolved.get("DELETE_COMMENTS_ISSUE") or ""),
                from_comment=str(resolved.get("DELETE_COMMENTS_FROM") or ""),
            )
        if _is_true(resolved.get("RUN_SCRIPT")):
            timeout = str(resolved.get("SCRIPT_TIMEOUT_MINUTES") or "").strip()
            return cls(
                kind="run_script",
                script_path=str(resolved.get("SCRIPT_PATH") or ""),
                script_args=str(resolved.get("SCRIPT_ARGS") or ""),
                timeout_minutes=int(timeout) if timeout.isdigit() else None,
            )
        return None

    # @stub-protected
    def execute(self, adapter: Any) -> StepResult:
        """Run the step through the adapter/runner; never raises for step failure."""
        try:
            if self.kind == "delete_comments":
                deleted = int(
                    adapter.delete_issue_comments(self.issue, self.from_comment)
                )
                return StepResult(
                    step_kind=self.kind,
                    ok=True,
                    detail=f"deleted {deleted} comments on issue {self.issue}",
                    affected=deleted,
                )
            return self._execute_script()
        except Exception as exc:  # noqa: BLE001 - a step failure is data, not a crash
            return StepResult(step_kind=self.kind, ok=False, detail=str(exc), affected=0)

    def _execute_script(self) -> StepResult:
        timeout = self.timeout_minutes * 60 if self.timeout_minutes else None
        completed = subprocess.run(
            [self.script_path, *shlex.split(self.script_args)], timeout=timeout
        )
        return StepResult(
            step_kind=self.kind,
            ok=completed.returncode == 0,
            detail=f"script exited with code {completed.returncode}",
            affected=completed.returncode,
        )


def to_routing_decision(args: Mapping[str, Any]) -> RoutingDecision:
    """Normalize a ``dispatch_next``/``AGENT_RUN_CONFIG`` payload.

    Tolerant of missing fields: an unknown/absent status is treated as a plain
    ``advance`` so a hand-off is never silently dropped.
    """
    status = str(args.get("status") or "advance")
    complexity = args.get("complexity") or "medium"
    return RoutingDecision(
        status=status,
        agent=args.get("agent"),
        complexity=str(complexity),
        branch=args.get("branch"),
        reason=args.get("reason"),
    )
