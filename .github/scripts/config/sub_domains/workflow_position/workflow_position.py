"""Flat workflow position entity (issue #638, replaces the v2 recursive leaf).

A ``WorkflowPosition`` is the canonical address of a run: the ordered flat
step path — ``list[StepRef]``, outermost first, last ref = the step actually
executing. Sub-workflow entry appends a ref; return-to-parent truncates the
path below the parent mount. There is no recursion: depth is the list length.

Serialization (issue #638):
- v3 state block: ``{"version": 3, "refs": [{"workflow": ..., "step": ...}, ...]}``
  plus flat leaf aliases (``workflow``/``step``) so v1 readers keep working.
- v2 (issue #618) recursive blocks and v1 (absent) input are converted to the
  flat path by :meth:`from_state_dict` — one reader, three input shapes.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from ..step_ref.step_ref import StepRef

POSITION_STATE_VERSION = 3

# @stub-protected: signature defined by architect — do not modify
@dataclass
class WorkflowPosition:
    """Canonical workflow run address: ordered flat step path (issue #638).

    ``refs`` is outermost-first; the last ref is the step the run executes.
    An empty path is the "top-level / no position" value (renders as empty
    strings on every channel).
    """

    version: int = POSITION_STATE_VERSION
    refs: list[StepRef] = field(default_factory=list)

    # @stub-protected: signature defined by architect — do not modify
    def leaf(self) -> Optional[StepRef]:
        """The innermost (agent-bearing) ref — where the run executes."""
        return self.refs[-1] if self.refs else None

    def leaf_workflow(self) -> str:
        leaf = self.leaf()
        return leaf.workflow if leaf else ""

    def leaf_step(self) -> str:
        leaf = self.leaf()
        return leaf.step if leaf else ""

    # @stub-protected: signature defined by architect — do not modify
    def path_str(self) -> str:
        """Outermost-first path string: ``wf/step|wf/step|...`` (empty path -> '')."""
        return "|".join(f"{r.workflow}/{r.step}" for r in self.refs)

    def with_appended(self, ref: StepRef) -> "WorkflowPosition":
        """A new position with ``ref`` appended (sub-workflow entry)."""
        return WorkflowPosition(version=self.version, refs=[*self.refs, ref])

    def parent(self) -> Optional["WorkflowPosition"]:
        """A new position with the last ref dropped (return-to-parent).

        ``None`` when the path is empty (nothing above to return to).
        """
        if not self.refs:
            return None
        return WorkflowPosition(version=self.version, refs=self.refs[:-1])

    # @stub-protected: signature defined by architect — do not modify
    def to_state_dict(self) -> dict:
        """v3 state block with flat leaf aliases (v1 readers unchanged)."""
        result: dict = {"version": POSITION_STATE_VERSION}
        if self.refs:
            result["workflow"] = self.leaf_workflow()
            result["step"] = self.leaf_step()
            result["refs"] = [r.model_dump() for r in self.refs]
        return result

    # @stub-protected: signature defined by architect — do not modify
    @classmethod
    def bare(cls, workflow: str, step: str) -> "WorkflowPosition":
        """Single-ref position for a top-level (non-mount) run (issue #646).

        Position is universal and mandatory on write: every run — top-level
        or sub-workflow — carries a non-empty refs list, so the bare ref is
        the only value a top-level step emits.
        """
        return cls(refs=[StepRef(workflow=workflow, step=step)])

    # @stub-protected: signature defined by architect — do not modify
    @classmethod
    def from_state_dict(cls, data) -> Optional["WorkflowPosition"]:
        """Parse v1 (absent), v2 (recursive), or v3 (flat) input into the
        flat path. ``None`` for ``None``/empty input.

        Accepted shapes:
        - v3: ``{"version": 3, "workflow": ..., "step": ..., "refs": [...]}``
          — ``refs`` wins when present (leaf aliases would be lossy).
        - v2 (issue #618): recursive blocks where a non-leaf node's
          ``step`` key is the *next* block; each node's
          ``workflow_name``/``step_name`` is a real ref (mount nodes carry
          their mount step name).
        - v1 / bare leaf alias: a single ``workflow``/``step`` pair.
        """
        if not data or not isinstance(data, dict):
            return None
        raw_refs = data.get("refs")
        if isinstance(raw_refs, list):
            refs = [
                StepRef(workflow=str(r.get("workflow", "")), step=str(r.get("step", "")))
                for r in raw_refs
                if isinstance(r, dict)
            ]
            return cls(version=POSITION_STATE_VERSION, refs=refs) if refs else None
        refs: list[StepRef] = []
        node = data
        while isinstance(node, dict):
            name = node.get("workflow_name") or node.get("workflow") or ""
            step = node.get("step_name")
            if not isinstance(step, str):
                step = node.get("step")
                step = step if isinstance(step, str) else ""
            if name or step:
                refs.append(StepRef(workflow=str(name), step=str(step)))
            nxt = node.get("step")
            if not isinstance(nxt, dict):
                break
            node = nxt
        return cls(version=POSITION_STATE_VERSION, refs=refs) if refs else None

    # @stub-protected: signature defined by architect — do not modify
    @classmethod
    def from_env(cls, raw: str) -> Optional["WorkflowPosition"]:
        """Parse the WORKFLOW_POSITION env value (single-line JSON) into the
        flat position. ``None`` when unset/empty/malformed, so callers keep
        passing ``None`` for v1 position-less runs and state validation
        falls back to one-shot re-derivation.
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
        return cls.from_state_dict(parsed if isinstance(parsed, dict) else None)

    def is_self_call(self, step_agent: str, running_agent: str) -> bool:
        """Pure identity check: True iff ``step_agent`` is non-empty and
        equals ``running_agent``. Callers pass the *leaf* step's owning agent
        so the check answers "does the leaf re-execute the caller"
        (issue #618)."""
        return step_agent != "" and step_agent == running_agent


def parse_position_env(raw: str) -> Optional[dict]:
    """Parse the WORKFLOW_POSITION env value for state persistence.

    Returns the v3 state block (``WorkflowPosition.to_state_dict`` shape)
    from any accepted input shape (v1 env absent -> ``None``; legacy v2 JSON
    converted; v3 JSON verbatim), or ``None`` when empty/malformed — the
    same contract the agent path (``build_output``) and the script path
    (``run_script``) rely on.
    """
    position = WorkflowPosition.from_env(raw)
    return position.to_state_dict() if position is not None else None


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
