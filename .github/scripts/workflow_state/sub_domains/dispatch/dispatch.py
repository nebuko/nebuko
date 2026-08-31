from typing import Optional

from pydantic import BaseModel, field_validator

DISPATCH_STATUSES = {"advance", "loop", "hold", "terminate"}

class Dispatch(BaseModel):
    """Agent outcome and runtime preferences, consumed by the workflow engine.

    Replaces the external <!-- AGENT_RUN_CONFIG ... --> single-line HTML-comment
    sentinel with a structured field
    inside WorkflowState. Instead of agents explicitly naming the next agent,
    they express their outcome (advance, loop, hold, terminate) and optionally
    provide a task hint. The workflow engine resolves routing from this field
    plus the workflow definition.

    Fields:
        status: Outcome indicator ("advance", "loop", "hold", "terminate").
        task_hint: Optional suggested next agent (overrides workflow YAML).
        complexity: Complexity level for downstream agent.
        reasoning: Reasoning mode for downstream agent.
        model: Optional model override.
        temperature, top_p, top_k, min_p, presence_penalty, repetition_penalty:
            Sampling parameter overrides.
        workflow: Optional workflow name override.
        step: Optional step name override.
        branch: Optional branch name.
        parent_workflow, parent_workflow_step: Sub-workflow tracking.
        parent_chain: Ordered ancestor chain of reloadable workflow references
            (newest-first) carried across nested sub-workflows (issue #470).
        skills: Optional skills string for downstream agent.
        reason: Optional free-text explanation for the dispatch decision.
    """

    status: str = "advance"
    task_hint: Optional[str] = None
    complexity: str = "medium"
    reasoning: str = "none"
    model: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[float] = None
    min_p: Optional[float] = None
    presence_penalty: Optional[float] = None
    repetition_penalty: Optional[float] = None
    workflow: Optional[str] = None
    step: Optional[str] = None
    branch: Optional[str] = None
    parent_workflow: Optional[str] = None
    parent_workflow_step: Optional[str] = None
    parent_chain: Optional[list[str]] = None
    skills: Optional[str] = None
    debug: bool = False
    reason: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in DISPATCH_STATUSES:
            raise ValueError(
                f"Invalid dispatch status '{v}'. Must be one of: {DISPATCH_STATUSES}"
            )
        return v