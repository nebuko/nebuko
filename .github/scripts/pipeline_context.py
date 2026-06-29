#!/usr/bin/env python3
"""
Pipeline context objects for the decoupled workflow context pipeline.

Defines data classes that flow through the phases:
  - TriggerHints: parsed hints from trigger text
  - ResolvedContext: complete agent config ready for execution
  - PipelineContext: accumulated data across all phases
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from workflow_state.sub_domains.dispatch.dispatch import Dispatch
from workflow_state.sub_domains.step_resolution.step_resolution import StepResolution
from workflow_state.workflow_state import WorkflowState


@dataclass
class TriggerHints:
    """Hints extracted from a trigger message body.

    This is a pure data structure representing what was found in the
    trigger text: explicit workflow/step/agent overrides, commands,
    and complexity indicators. No business logic here.
    """

    workflow_name: Optional[str] = None
    step_name: Optional[str] = None
    agent_name: Optional[str] = None
    force_next_step: Optional[str] = None
    complexity: str = "medium"
    reasoning: str = "none"
    model: Optional[str] = None
    temperature: Optional[float] = None
    skills: Optional[str] = None
    task_hint: Optional[str] = None
    is_bot_comment: bool = False


@dataclass
class ResolvedContext:
    """Complete resolved configuration for agent execution.

    Produced by Phase 2 (Configuration Resolution). Contains everything
    needed to run the agent: which agent, what prompt, what LLM config,
    and the workflow state context.
    """

    # Routing
    workflow: str = ""
    step: str = ""
    workflow_next: str = ""
    parent_workflow: str = ""
    parent_workflow_step: str = ""

    # Agent
    agent: str = ""
    prompt_location: str = ""
    matched_rule: str = ""

    # Skills
    agent_skills: str = ""
    step_skills: str = ""

    # LLM config
    complexity: str = "medium"
    reasoning: str = "none"
    model: str = ""
    temperature: str = ""
    top_p: str = ""
    top_k: str = ""
    repetition_penalty: str = ""

    # State
    workflow_state: Optional[WorkflowState] = None
    dispatch: Optional[Dispatch] = None
    resolution: Optional[StepResolution] = None

    # Skip flag
    skip_agent: bool = False
    skip_reason: str = ""

    def to_env_dict(self) -> dict[str, str]:
        """Convert to environment variable dict for GitHub Actions compatibility."""
        result = {
            "TASK": self.agent,
            "ROLE_NAME": self.agent,
            "PROMPT_LOCATION": self.prompt_location,
            "AGENT_SELECTED": "false" if self.skip_agent else "true",
            "COMPLEXITY": self.complexity,
            "LLM_REASONING": self.reasoning,
            "AGENT_SKILLS": self.agent_skills,
            "STEP_SKILLS": self.step_skills,
            "CONFIG_MODEL": self.model,
            "CONFIG_TEMPERATURE": self.temperature,
            "CONFIG_TOP_P": self.top_p,
            "CONFIG_TOP_K": self.top_k,
            "CONFIG_REPETITION_PENALTY": self.repetition_penalty,
            "WORKFLOW_NAME": self.workflow,
            "WORKFLOW_STEP": self.step,
            "WORKFLOW_NEXT": self.workflow_next,
            "PARENT_WORKFLOW": self.parent_workflow,
            "PARENT_WORKFLOW_STEP": self.parent_workflow_step,
            "MATCHED_RULE": self.matched_rule,
            "SKIP_AGENT": "true" if self.skip_agent else "false",
        }
        if self.skip_agent:
            result["REASON"] = self.skip_reason
        return result

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON output."""
        d: dict[str, Any] = {
            "workflow": self.workflow,
            "step": self.step,
            "workflow_next": self.workflow_next,
            "parent_workflow": self.parent_workflow,
            "parent_workflow_step": self.parent_workflow_step,
            "agent": self.agent,
            "prompt_location": self.prompt_location,
            "matched_rule": self.matched_rule,
            "agent_skills": self.agent_skills,
            "step_skills": self.step_skills,
            "complexity": self.complexity,
            "reasoning": self.reasoning,
            "model": self.model,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "repetition_penalty": self.repetition_penalty,
            "skip_agent": self.skip_agent,
            "skip_reason": self.skip_reason,
        }
        if self.dispatch:
            d["dispatch"] = self.dispatch.model_dump()
        if self.resolution:
            d["resolution"] = self.resolution.model_dump()
        return d

    @staticmethod
    def skipped(reason: str) -> ResolvedContext:
        """Create a skip result."""
        return ResolvedContext(skip_agent=True, skip_reason=reason)

    @staticmethod
    def from_env_dict(data: dict[str, str]) -> ResolvedContext:
        """Reconstruct from environment variables (backward compat)."""
        skip = data.get("SKIP_AGENT", "false") == "true"
        return ResolvedContext(
            agent=data.get("ROLE_NAME", ""),
            prompt_location=data.get("PROMPT_LOCATION", ""),
            workflow=data.get("WORKFLOW_NAME", ""),
            step=data.get("WORKFLOW_STEP", ""),
            workflow_next=data.get("WORKFLOW_NEXT", ""),
            parent_workflow=data.get("PARENT_WORKFLOW", ""),
            parent_workflow_step=data.get("PARENT_WORKFLOW_STEP", ""),
            complexity=data.get("COMPLEXITY", "medium"),
            reasoning=data.get("LLM_REASONING", "none"),
            model=data.get("CONFIG_MODEL", ""),
            temperature=data.get("CONFIG_TEMPERATURE", ""),
            top_p=data.get("CONFIG_TOP_P", ""),
            top_k=data.get("CONFIG_TOP_K", ""),
            repetition_penalty=data.get("CONFIG_REPETITION_PENALTY", ""),
            agent_skills=data.get("AGENT_SKILLS", ""),
            step_skills=data.get("STEP_SKILLS", ""),
            matched_rule=data.get("MATCHED_RULE", ""),
            skip_agent=skip,
            skip_reason=data.get("REASON", ""),
        )


@dataclass
class PipelineContext:
    """Accumulated context that flows through all pipeline phases.

    Phase 1 (State Resolution) writes workflow_state.
    Phase 2 (Config Resolution) adds resolved_context.
    Phase 3 (Query Assembly) adds query_text.
    Phase 4 (Agent Execution) adds result and advances state.
    """

    trigger: str = ""
    workflow_state: Optional[WorkflowState] = None
    resolved_context: Optional[ResolvedContext] = None
    query_text: str = ""

    # Issue metadata (populated by Phase 1 or caller)
    issue_title: str = ""
    issue_body: str = ""
    issue_author: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON artifact handoff."""
        d: dict[str, Any] = {
            "trigger": self.trigger,
            "issue_title": self.issue_title,
            "issue_body": self.issue_body,
            "issue_author": self.issue_author,
            "query_text": self.query_text,
        }
        if self.workflow_state:
            d["workflow_state"] = self.workflow_state.model_dump()
        if self.resolved_context:
            d["resolved_context"] = self.resolved_context.to_dict()
        return d

    @staticmethod
    def from_dict(data: dict[str, Any]) -> PipelineContext:
        """Reconstruct from a JSON artifact dict."""
        ctx = PipelineContext(
            trigger=data.get("trigger", ""),
            issue_title=data.get("issue_title", ""),
            issue_body=data.get("issue_body", ""),
            issue_author=data.get("issue_author", ""),
            query_text=data.get("query_text", ""),
        )
        if "workflow_state" in data and data["workflow_state"]:
            ctx.workflow_state = WorkflowState.model_validate(data["workflow_state"])
        if "resolved_context" in data and data["resolved_context"]:
            rc_data = data["resolved_context"]
            ctx.resolved_context = ResolvedContext(
                workflow=rc_data.get("workflow", ""),
                step=rc_data.get("step", ""),
                workflow_next=rc_data.get("workflow_next", ""),
                parent_workflow=rc_data.get("parent_workflow", ""),
                parent_workflow_step=rc_data.get("parent_workflow_step", ""),
                agent=rc_data.get("agent", ""),
                prompt_location=rc_data.get("prompt_location", ""),
                matched_rule=rc_data.get("matched_rule", ""),
                agent_skills=rc_data.get("agent_skills", ""),
                step_skills=rc_data.get("step_skills", ""),
                complexity=rc_data.get("complexity", "medium"),
                reasoning=rc_data.get("reasoning", "none"),
                model=rc_data.get("model", ""),
                temperature=rc_data.get("temperature", ""),
                top_p=rc_data.get("top_p", ""),
                top_k=rc_data.get("top_k", ""),
                repetition_penalty=rc_data.get("repetition_penalty", ""),
                skip_agent=rc_data.get("skip_agent", False),
                skip_reason=rc_data.get("skip_reason", ""),
            )
            if "dispatch" in rc_data and rc_data["dispatch"]:
                from workflow_state.sub_domains.dispatch.dispatch import Dispatch as D

                ctx.resolved_context.dispatch = D.model_validate(rc_data["dispatch"])
        return ctx
