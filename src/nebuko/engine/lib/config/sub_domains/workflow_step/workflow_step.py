"""Workflow step entity."""

from typing import Literal, Optional

from pydantic import BaseModel, model_validator

from ..workflow_condition.workflow_condition import WorkflowCondition
from .script_step_spec import ScriptStepSpec


class WorkflowStep(BaseModel):
    """A single step in a workflow: agent + routing.

    A step can be:
    - An agent invocation (agent)
    - A local sub-workflow reference (workflow, optionally with path)
    - A remote git repo workflow (repo + ref + optional workflow_name)
    - A deterministic script run (script) — object name is the kind
    """

    name: str
    agent: Optional[str] = None
    workflow: Optional[str] = None
    path: Optional[str] = None
    # Git-based remote workflow source
    repo: Optional[str] = None
    ref: Optional[str] = None
    workflow_name: Optional[str] = None
    # Script step (issue #541): deterministic, non-agent step that runs a
    # repo-local script directly and posts its output as an issue comment.
    # The ``script`` key is itself the step source; its configuration nests
    # under it.
    # @stub-protected: script-variant fields defined by architect — do not modify
    script: Optional[ScriptStepSpec] = None
    next: Optional[str] = None
    conditions: Optional[list[WorkflowCondition]] = None
    skill: Optional[list[str]] = None
    # Standing instruction injected into this step's agent context (issue #551).
    prompt: Optional[str] = None
    # When True and ``prompt`` is set, this step's agent context is the step
    # prompt only: the role prompt (e.g. ``dev.md``) and its generic flow are
    # suppressed, structurally scoping the step to its declared instruction
    # (issue #784). Ignored when ``prompt`` is unset.
    prompt_replace: bool = False
    # When True, this step is an explicit human validation gate.
    # The workflow MUST pause (dispatch.status == "hold") until a human
    # responds. The approval agent evaluates human feedback and sets
    # dispatch.branch to "approved" or "rejected" accordingly.
    human_gate: bool = False
    # Declared complexity tier for this step's agent invocation (issue #787):
    # selects agents.<name>.complexity_overrides.<tier> and the
    # complexity->temperature floor. None = inherit the dispatch
    # complexity, else "medium".
    complexity: Optional[Literal["low", "medium", "high"]] = None  # @stub-protected

    @model_validator(mode="after")
    def _validate_step_source(self) -> "WorkflowStep":
        # Script step (issue #541): a distinct, deterministic, non-agent source
        # keyed by the nested ``script`` object itself. It must be the sole
        # source. Path/timeout rules are enforced by ``ScriptStepSpec``.
        if self.script is not None:
            if self.agent or self.workflow or self.repo:
                raise ValueError(
                    "A 'script' step cannot also declare 'agent', 'workflow', or 'repo'"
                )
            if self.path is not None:
                raise ValueError("A 'script' step cannot declare 'path'")
            return self

        # Note: `path` is an optional companion to `workflow`, not a standalone source.
        # `repo` is mutually exclusive with `agent`, `workflow`, and `path`.
        if not self.agent and not self.workflow and not self.repo:
            raise ValueError(
                "Step must have exactly one of 'agent', 'workflow', 'repo', or 'script'"
            )
        if self.agent and self.workflow:
            raise ValueError(
                "Step cannot have both 'agent' and 'workflow'"
            )
        if self.agent and self.repo:
            raise ValueError(
                "Step cannot have both 'agent' and 'repo'"
            )
        if self.workflow and self.repo:
            raise ValueError(
                "Step cannot have both 'workflow' and 'repo'"
            )
        if self.repo and self.path:
            raise ValueError(
                "Step cannot have both 'repo' and 'path'"
            )

        if self.repo:
            if not self.ref:
                raise ValueError("'repo' requires 'ref' (commit SHA, tag, or branch)")
            if not self.repo.startswith("https://"):
                raise ValueError("'repo' URL must use HTTPS scheme")
            if self.workflow_name:
                if ".." in self.workflow_name or self.workflow_name.startswith("/"):
                    raise ValueError(
                        "'workflow_name' must not contain '..' or start with '/'"
                    )
        return self
