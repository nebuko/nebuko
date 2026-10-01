"""Filesystem-backed platform adapter (issue #590, Stage A).

Run directory ``runs/<run-id>/`` holds ``trigger.json``, ``journal.md``,
``state.json``, ``latest_answer.md`` and ``console.log``. Journal entries carry
the standard ``<!-- bot-workflow-terminal -->`` / ``<!-- bot-agent-* -->``
markers, so ``loop_guard``, ``circuit_breaker`` and state extraction run
**unchanged** against :meth:`LocalAdapter.load_history`.

Stub step: class skeleton only. Bodies are materialised by @@dev in the
implement step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from pathlib import Path

    from engine.models import CommentEntry, Trigger
    from workflow_state.workflow_state import WorkflowState

__all__ = ["LocalAdapter"]


# @stub-protected
class LocalAdapter:  # structurally satisfies adapters.base.PlatformAdapter
    """Network-free adapter backed by a single run directory."""

    # @stub-protected
    def __init__(self, run_dir: Path, *, trigger: Trigger | None = None) -> None:
        raise NotImplementedError

    # @stub-protected
    def read_trigger(self) -> Trigger:
        raise NotImplementedError

    # @stub-protected
    def load_history(self, *, limit: int | None = None) -> Sequence[CommentEntry]:
        raise NotImplementedError

    # @stub-protected
    def post(self, body: str) -> str:
        raise NotImplementedError

    # @stub-protected
    def load_state(self) -> WorkflowState | None:
        raise NotImplementedError

    # @stub-protected
    def save_state(self, state: WorkflowState) -> None:
        raise NotImplementedError

    # @stub-protected
    def create_issue(self, *, title: str, body: str, labels: Sequence[str] = ()) -> int:
        raise NotImplementedError

    # @stub-protected
    def write_artifact(self, name: str, content: str) -> None:
        raise NotImplementedError
