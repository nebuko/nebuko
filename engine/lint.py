"""Offline, graph-level linter for workflow YAML (issue #751).

The Pydantic loader (``.github/scripts/config/features/load_workflow``) enforces
the *field-level* shape of a workflow — types, required keys, enums. It cannot
see the *graph-level* defects that only emerge once the whole step graph is
assembled: a dangling ``next`` target, a step unreachable from the entry, a
stale condition key, an unresumable ``human_gate``, or a graph with no terminal
step. Those defects surface today only at run time, mid-workflow.

This module owns the offline pass that reports them all at once. It is a pure,
read-only consumer of the loaded models and reuses the runtime traversal
semantics from ``.github/scripts/resolve_workflow.py`` (``match_condition``,
``branch_key_for``, ``is_terminal_step``) so a lint rule can never drift from the
engine's actual routing behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from pathlib import Path

__all__ = ["LintIssue", "WorkflowLinter"]


# @stub-protected: signature defined by architect — do not modify
@dataclass(frozen=True)
class LintIssue:
    """One violated graph invariant found while linting a workflow definition.

    ``rule`` is the stable rule identifier (e.g. ``"L1-dangling-next"``); the
    ``severity`` is ``"error"`` or ``"warning"``; ``step`` names the offending
    step when the violation is step-scoped; ``location`` is ``file:line`` when a
    YAML source mapping is available; ``hint`` is an optional remediation hint.
    """

    rule: str  # @stub-protected
    severity: str  # @stub-protected  "error" | "warning"
    message: str  # @stub-protected
    step: str | None  # @stub-protected
    location: str | None  # @stub-protected
    hint: str | None  # @stub-protected


# @stub-protected: signature defined by architect — do not modify
class WorkflowLinter:
    """Check a workflow definition against the graph-level grammar invariants.

    The linter loads the YAML through the existing schema loader, builds the
    step graph, and runs every rule against the runtime traversal semantics. It
    never mutates, loads, or resolves anything at run time. ``strict`` promotes
    warnings to errors so a CI consumer can gate on them (the Dify
    ``validate_dsl.py --strict`` pattern).
    """

    # @stub-protected: signature defined by architect — do not modify
    def __init__(self, strict: bool = False) -> None:
        """Store the strict flag (warnings become errors when set)."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def lint(self, path: Path) -> list[LintIssue]:
        """Load ``path`` and return every violated invariant, in rule order."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def lint_all(self, paths: list[Path]) -> list[LintIssue]:
        """Run :meth:`lint` over every path and concatenate the issues."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def _check_dangling_next(self, graph) -> list[LintIssue]:
        """L1: ``next``/``conditions[].next`` must name an existing step."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def _check_reachability(self, graph) -> list[LintIssue]:
        """L2: every step must be reachable from the entry step."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def _check_duplicate_ids(self, graph) -> list[LintIssue]:
        """L3: step names (and branch keys) must be unique."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def _check_condition_keys(self, graph) -> list[LintIssue]:
        """L4: condition keys must be producible (no dead/duplicate ``on_result``)."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def _check_terminal_consistency(self, graph) -> list[LintIssue]:
        """L5: terminal and non-terminal steps must declare consistent routing."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def _check_human_gates(self, graph) -> list[LintIssue]:
        """L6: a ``human_gate`` step must have a route that can resume it."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def _check_has_terminal(self, graph) -> list[LintIssue]:
        """L7: the graph must contain at least one terminal step."""
        raise NotImplementedError
