"""Per-complexity agent configuration override entity."""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..llm_config.llm_config import LLMConfig


class AgentComplexityOverride(BaseModel):
    """Partial override of an agent's model/sampling/reasoning fields for one complexity tier."""

    # Reject misplaced keys: a typo'd override key (e.g. a field the model does
    # not carry) must fail at load rather than being silently dropped (#941).
    model_config = ConfigDict(extra="forbid")

    model_ref: Optional[LLMConfig] = Field(default=None, alias="model")  # @stub-protected
    temperature: Optional[float] = None  # @stub-protected
    reasoning: Optional[str] = None  # @stub-protected  ("none" | "low" | "medium" | "xhigh")
    # Per-tier thinking switch: overrides the LLM-level ``thinking_enabled``;
    # ``None`` inherits (issue #941).
    thinking_enabled: Optional[bool] = None  # @stub-protected
    # Per-tier top-level reasoning effort; ``None`` inherits (issue #941).
    reasoning_effort: Optional[str] = None  # @stub-protected
    top_p: Optional[float] = None  # @stub-protected
    top_k: Optional[int] = None  # @stub-protected
    min_p: Optional[float] = None  # @stub-protected
    presence_penalty: Optional[float] = None  # @stub-protected
    repetition_penalty: Optional[float] = None  # @stub-protected
