"""Agent configuration entity."""

from typing import Optional

from pydantic import BaseModel, Field

from ..llm_config.llm_config import LLMConfig


class AgentConfig(BaseModel):
    """Agent role → model + prompt file + skills + tools."""

    model_ref: LLMConfig = Field(alias="model")
    temperature: float = 0.0
    prompt_file: str
    role: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(
        default=[
            "terminal", "file_editor", "task_tracker", "think", "finish",
        ]
    )
    # Per-agent sampling parameter overrides
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    min_p: Optional[float] = None
    presence_penalty: Optional[float] = None
    repetition_penalty: Optional[float] = None
    stop_sequences: Optional[list[str]] = None
