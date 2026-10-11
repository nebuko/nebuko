"""Platform-free vocabulary for the orchestration engine (issue #590, Stage A).

Leaf module of the ``engine`` package: the only external type it names is the
reused ``workflow_state.workflow_state.WorkflowState`` — never re-declared here.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
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
    "SkipKind",
    "SkipOutcome",
    "DeterministicStep",
    "StepResult",
    "WorkflowCallFrame",
    "NebukoManifest",
    "to_routing_decision",
    "circuit_breaker_update_notice",
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
    # Platform comment id (issue #756): lets a step's posted comment be located
    # by name for step-output resolution. ``None`` for a hand-built entry.
    comment_id: str | None = None


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


# @stub-protected
class SkipKind(Enum):
    """Why a run stopped without dispatching an agent."""

    NO_NEXT_AGENT = "no_next_agent"  # benign — nothing left to dispatch
    TERMINAL = "terminal"  # benign — terminal marker / workflow exhausted
    CONFIG_ERROR = "config_error"  # failure — workflow not found / bad config


# @stub-protected
@dataclass(frozen=True)
class SkipOutcome:
    """The typed verdict for a run that stops without dispatching an agent.

    Single source of truth for *why* the guards phase skipped the agent and the
    exit code that follows: a benign verdict (``NO_NEXT_AGENT`` / ``TERMINAL``)
    is a deliberate no-op and must not fail the host run, while a
    ``CONFIG_ERROR`` keeps the existing terminal failure exit. Resolver verdicts
    may omit the kind — an unclassified reason defaults to ``CONFIG_ERROR`` so
    behaviour is never softened by accident.
    """

    kind: SkipKind
    reason: str

    # @stub-protected
    @property
    def is_benign(self) -> bool:
        return self.kind in (SkipKind.NO_NEXT_AGENT, SkipKind.TERMINAL)

    # @stub-protected
    @property
    def exit_code(self) -> ExitCode:
        return ExitCode.ADVANCE if self.is_benign else ExitCode.TERMINAL

    @classmethod
    def from_verdict(cls, verdict: Mapping[str, Any]) -> "SkipOutcome":
        """Build the verdict from a guards-phase ``SKIP_AGENT`` dict.

        Unknown/missing ``SKIP_KIND`` falls back to ``CONFIG_ERROR`` (the
        fail-safe), and the human-readable ``REASON`` is preserved verbatim.
        """
        raw_kind = str(verdict.get("SKIP_KIND") or "").strip()
        try:
            kind = SkipKind(raw_kind) if raw_kind else SkipKind.CONFIG_ERROR
        except ValueError:
            kind = SkipKind.CONFIG_ERROR
        return cls(kind=kind, reason=str(verdict.get("REASON") or ""))


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _as_text(value: bytes | str | None) -> str:
    """Normalize a possibly-bytes captured stream to ``str``."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


# @stub-protected
@dataclass(frozen=True)
class StepResult:
    """Immutable record of one executed deterministic step."""

    step_kind: str
    ok: bool
    detail: str  # human-readable outcome (e.g. "deleted 42 comments on issue 590")
    affected: int  # comments deleted / script exit code
    output: str = ""  # captured stdout/stderr (run_script; issue #885)
    duration_seconds: float = 0.0  # wall-clock execution time (run_script)


# @stub-protected
@dataclass(frozen=True)
class DeterministicStep:
    """An agent-free step the resolver hands to the engine to execute directly."""

    kind: Literal["delete_comments", "run_script", "update_circuit_breaker"]
    issue: str = ""  # delete_comments: target issue
    from_comment: str = ""  # delete_comments: --from boundary ("" = all)
    script_path: str = ""  # run_script
    script_args: str = ""
    timeout_minutes: int | None = None
    step_name: str = ""  # run_script: workflow step name (from WORKFLOW_STEP)
    circuit_breaker_value: int = 0  # update_circuit_breaker: new per-issue limit

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
                step_name=str(resolved.get("WORKFLOW_STEP") or ""),
            )
        if _is_true(resolved.get("UPDATE_CIRCUIT_BREAKER")):
            raw = str(resolved.get("UPDATE_CIRCUIT_BREAKER_VALUE") or "").strip()
            return cls(
                kind="update_circuit_breaker",
                circuit_breaker_value=int(raw) if raw.isascii() and raw.isdigit() else 0,
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
            if self.kind == "update_circuit_breaker":
                return self._execute_circuit_breaker_update(adapter)
            return self._execute_script()
        except Exception as exc:  # noqa: BLE001 - a step failure is data, not a crash
            return StepResult(step_kind=self.kind, ok=False, detail=str(exc), affected=0)

    def _execute_circuit_breaker_update(self, adapter: Any) -> StepResult:
        """Persist the new per-issue breaker limit and post a confirmation.

        The override must survive into the next run, and on GitHub the only
        cross-run carrier is the comment history (the disk hand-off is
        per-runner). So the step loads the previous state, applies the limit,
        and posts it wrapped around a human confirmation — the guard of the
        next run reads it back via ``extract_state``.
        """
        from nebuko.engine.paths import ensure_import_path

        ensure_import_path()
        from circuit_breaker import count_bot_markers
        from workflow_state.features.comment.to_git_comment import (
            wrap_comment_with_state,
        )
        from workflow_state.workflow_state import WorkflowState

        prev = adapter.load_state() or WorkflowState()
        new_state = prev.model_copy(
            update={
                "circuit_breaker": prev.circuit_breaker.with_limit(
                    self.circuit_breaker_value
                )
            }
        )
        history = adapter.load_history() or []
        agent_calls = max(0, count_bot_markers(list(history)) // 2)
        detail = circuit_breaker_update_notice(self.circuit_breaker_value, agent_calls)
        adapter.post(wrap_comment_with_state(new_state, detail))
        adapter.save_state(new_state)
        return StepResult(
            step_kind=self.kind,
            ok=True,
            detail=detail,
            affected=self.circuit_breaker_value,
        )

    def _execute_script(self) -> StepResult:
        """Run the script, capturing combined output and timing it.

        A timeout is data, not a crash (issue #885): it returns ``ok=False`` with
        the partial output and no exit code, mirroring a non-zero exit. Output is
        captured merged (stderr folded into stdout) so the posted report shows
        the interleaved stream a terminal would.
        """
        timeout = self.timeout_minutes * 60 if self.timeout_minutes else None
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [self.script_path, *shlex.split(self.script_args)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return StepResult(
                step_kind=self.kind,
                ok=False,
                detail=f"script timed out after {self.timeout_minutes}m",
                affected=0,
                output=_as_text(exc.stdout),
                duration_seconds=time.monotonic() - started,
            )
        return StepResult(
            step_kind=self.kind,
            ok=completed.returncode == 0,
            detail=f"script exited with code {completed.returncode}",
            affected=completed.returncode,
            output=completed.stdout or "",
            duration_seconds=time.monotonic() - started,
        )


def circuit_breaker_update_notice(value: int, agent_calls: int) -> str:
    """Human-facing confirmation for a per-issue circuit-breaker update.

    Notes explicitly when the new limit does not clear the current usage, so a
    human does not mistake a no-op for an unlock.
    """
    if value <= agent_calls:
        return (
            f"⚙️ Circuit-breaker limit set to **{value}** for this issue.\n\n"
            f"⚠️ Current usage is {agent_calls} agent calls, so the breaker is "
            f"still tripped; raise the limit above {agent_calls} to unlock."
        )
    return (
        f"⚙️ Circuit-breaker limit set to **{value}** for this issue.\n\n"
        f"Current usage is {agent_calls} agent calls; the issue is unlocked."
    )


# @stub-protected
@dataclass(frozen=True)
class WorkflowCallFrame:
    """One live sub-workflow invocation — the function call frame (issue #813).

    Pushed when a ``workflow`` call step enters a child workflow and popped at
    the child's terminal step. Frames make workflows behave as typed functions:
    ``bindings`` are the frozen call-site parameter values, ``materialized``
    holds the values resolved just-in-time at the consuming step (an ``exec``
    binding runs there, not at the call boundary, so fresh context is injected
    immediately before the model runs), and ``outputs`` carries only the
    callee's declared return values back to the caller.

    Bindings are duck-typed: each one exposes ``resolve(scope) -> str``, so
    this leaf module never imports the config sub-domain.
    """

    callee: str
    repo_ref: str = ""
    call_step: str = ""
    bindings: Mapping[str, Any] = field(default_factory=dict)
    materialized: Mapping[str, str] = field(default_factory=dict)
    outputs: Mapping[str, str] = field(default_factory=dict)

    # @stub-protected
    def materialize(self, name: str, scope: Mapping[str, str]) -> "WorkflowCallFrame":
        """Return a new frame with ``name``'s binding resolved against ``scope``.

        A frozen frame is never mutated; the caller threads the returned frame.
        Raises whatever the binding's ``resolve`` raises (exec failure or an
        unresolved reference) so the caller can apply the ``required`` policy.
        """
        binding = self.bindings[name]
        value = binding.resolve(dict(scope))
        return replace(self, materialized={**self.materialized, name: value})

    # @stub-protected
    def pop(self) -> dict[str, str]:
        """Return the callee's declared outputs — and nothing else."""
        return dict(self.outputs)


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
