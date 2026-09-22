"""Recursive workflow position entity (issue #618).

A ``WorkflowPosition`` is the canonical address of a workflow step: the
workflow that owns it, the step name, and — when the step is a
workflow-mount step (``workflow:``/``path:`` reference with no agent) —
the sub-position inside the mounted workflow. The innermost,
agent-bearing position (``leaf``) is where a run actually executes.

This entity replaces the flat ``workflow``/``step`` strings that hid
sub-workflow mounts in the posted state (the #617 dead-end: the dev
run resolved to the parent's ``scaffold`` step instead of the mounted
``dev-review-loop / implement`` leaf).
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

POSITION_STATE_VERSION = 2


def parse_position_env(raw: str) -> Optional[dict]:
    """Parse the v2 mount-chain position from the WORKFLOW_POSITION env value.

    Returns the nested dict for ``WorkflowState.position`` (the
    ``WorkflowPosition.to_state_dict`` shape), or ``None`` when the value is
    empty, malformed, or not a JSON object (legacy v1 states stay
    position-less). Pure I/O — the env read stays with the caller so the
    same helper serves the agent path (``build_output``) and the script path
    (``run_script``) without duplicating the parse.
    """
    value = (raw or "").strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        logging.getLogger(__name__).warning(
            "WORKFLOW_POSITION is not valid JSON; dropping position"
        )
        return None
    return parsed if isinstance(parsed, dict) else None


# @stub-protected: signature defined by architect — do not modify
@dataclass
class WorkflowPosition:
    """A position in the workflow mount chain (issue #618).

    Attributes:
        workflow_name: Name (or path reference) of the workflow owning
            ``step_name``.
        workflow_path: Repo-root-relative path of the workflow file
            (e.g. ``workflows/plan-orchestration.yaml``); empty when the
            path cannot be determined.
        step_name: Step name within ``workflow_name``.
        sub: Position inside the workflow mounted by ``step_name``;
            ``None`` when the step is a leaf (agent-bearing) step.
    """

    workflow_name: str
    workflow_path: str = ""
    step_name: str = ""
    sub: Optional["WorkflowPosition"] = None

    # @stub-protected: signature defined by architect — do not modify
    @property
    def leaf(self) -> "WorkflowPosition":
        """The innermost (agent-bearing) position — where the run executes."""
        return self.sub.leaf if self.sub is not None else self

    def position_path(self) -> tuple:
        """Flat chain of ``(workflow_name, step_name)`` pairs, outermost first."""
        parts = [(self.workflow_name, self.step_name)]
        if self.sub is not None:
            parts.extend(self.sub.position_path())
        return tuple(parts)

    # @stub-protected: signature defined by architect — do not modify
    def to_state_dict(self) -> dict:
        """Nested state block in the canonical ``workflow``/``step`` shape.

        Includes the flat ``leaf`` aliases (``leaf_workflow``,
        ``leaf_step``) so v1 consumers keep reading the innermost
        position from the legacy flat ``workflow``/``step`` fields.
        """
        result = {
            "version": POSITION_STATE_VERSION,
            "workflow_name": self.workflow_name,
            "workflow_path": self.workflow_path,
            "step_name": self.step_name,
            "leaf_workflow": self.leaf.workflow_name,
            "leaf_step": self.leaf.step_name,
        }
        if self.sub is not None:
            result["step"] = self.sub.to_state_dict()
        return result

    # @stub-protected: signature defined by architect — do not modify
    @classmethod
    def from_state_dict(cls, data) -> Optional["WorkflowPosition"]:
        """Inverse of :meth:`to_state_dict`; ``None`` for ``None``/empty input."""
        if not data:
            return None
        sub = data.get("step")
        return cls(
            workflow_name=data.get("workflow_name") or "",
            workflow_path=data.get("workflow_path") or "",
            step_name=data.get("step_name") or "",
            sub=cls.from_state_dict(sub) if sub else None,
        )

    def is_self_call(self, step_agent: str, running_agent: str) -> bool:
        """Pure identity check: True iff ``step_agent`` is non-empty and equals
        ``running_agent``. This does NOT inspect any position/agent field of
        self — callers must pass the *leaf* position's owning agent so the
        check answers "does the leaf re-execute the caller" (issue #618)."""
        return step_agent != "" and step_agent == running_agent


# @stub-protected: signature defined by architect — do not modify
def is_self_function_call(fc, running_agent: str) -> bool:
    """True when a stored ``function_call`` targets the running agent itself.

    Pure I/O helper used by the build path (``build_output``) and the
    resolve path (``resolve_route``) to neutralize self-hand-offs
    (issue #618 root-cause defect 2): a self ``function_call`` must
    never outrank the plain dispatch / workflow-next routing.
    """
    if fc is None or not running_agent:
        return False
    return getattr(fc, "agent_name", None) == running_agent
