"""Per-complexity agent configuration override entity."""

from typing import Optional

from pydantic import BaseModel, Field

from ..llm_config.llm_config import LLMConfig


class AgentComplexityOverride(BaseModel):
    """Partial override of an agent's model/sampling/reasoning fields for one complexity tier."""

    model_ref: Optional[LLMConfig] = Field(default=None, alias="model")  # @stub-protected
    temperature: Optional[float] = None  # @stub-protected
    reasoning: Optional[str] = None  # @stub-protected  ("none" | "low" | "medium" | "xhigh")
    top_p: Optional[float] = None  # @stub-protected
    top_k: Optional[int] = None  # @stub-protected
    min_p: Optional[float] = None  # @stub-protected
    presence_penalty: Optional[float] = None  # @stub-protected
    repetition_penalty: Optional[float] = None  # @stub-protected
