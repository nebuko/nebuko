"""The orchestration engine: the 15-step bash ``run-task`` job, typed once.

Nine phases, in order, each emitting one ``[phase:<name>]`` log marker:
``normalize -> resolve_config -> guards -> prompt -> agent -> validate ->
publish -> state -> route``. The engine orchestrates legacy outputs by
reference to their existing types and contains no platform-mode branching.

Stub step: class skeletons only. Bodies are materialised by @@dev in the
implement step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from pathlib import Path

    from adapters.base import PlatformAdapter
    from engine.models import CommentEntry, RoutingDecision, RunOutcome, Trigger
    from workflow_state.workflow_state import WorkflowState

__all__ = ["PHASES", "Engine", "main"]


# @stub-protected
PHASES: tuple[str, ...] = (
    "normalize",
    "resolve_config",
    "guards",
    "prompt",
    "agent",
    "validate",
    "publish",
    "state",
    "route",
)


# @stub-protected
@dataclass(frozen=True)
class Engine:
    """Platform-free orchestration driver over a :class:`PlatformAdapter`."""

    adapter: PlatformAdapter
    repo_root: Path
    env: Mapping[str, str] = field(default_factory=dict)
    history_limit: int | None = None

    # @stub-protected
    def run(self) -> RunOutcome:
        """End-to-end pipeline; always returns an outcome."""
        raise NotImplementedError

    # @stub-protected
    def normalize(self) -> Trigger:
        """Return the adapter's normalized trigger."""
        raise NotImplementedError

    # @stub-protected
    def resolve(self, trigger: Trigger) -> Any:
        """Delegate to the legacy ``resolve_config`` result."""
        raise NotImplementedError

    # @stub-protected
    def guard(self, trigger: Trigger, history: Sequence[CommentEntry]) -> Any:
        """Run the loop guard + circuit breaker against the injected history."""
        raise NotImplementedError

    # @stub-protected
    def build_prompt(self, resolved: Any, history: Sequence[CommentEntry]) -> str:
        """Build the agent prompt from the resolved config and history."""
        raise NotImplementedError

    # @stub-protected
    def invoke_agent(self, prompt: str) -> Any:
        """Delegate to the legacy ``agent_script.arun`` result."""
        raise NotImplementedError

    # @stub-protected
    def validate(self, agent_result: Any) -> None:
        """Delegate to ``validate_output``."""
        raise NotImplementedError

    # @stub-protected
    def publish(self, agent_result: Any) -> str:
        """Delegate to ``select_posted_body`` then ``adapter.post``."""
        raise NotImplementedError

    # @stub-protected
    def persist(self, agent_result: Any) -> WorkflowState | None:
        """Persist the next workflow state through the adapter."""
        raise NotImplementedError

    # @stub-protected
    def route(self, agent_result: Any) -> RoutingDecision:
        """Map the agent's dispatch payload to a :class:`RoutingDecision`."""
        raise NotImplementedError


# @stub-protected
def main(argv: Sequence[str] | None = None) -> int:
    """Select the adapter, run once, map ``RunOutcome.exit_code`` to the process code."""
    raise NotImplementedError
