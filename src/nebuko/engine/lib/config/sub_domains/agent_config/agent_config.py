"""Agent configuration entity."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..agent_complexity_override.agent_complexity_override import AgentComplexityOverride
from ..llm_config.llm_config import LLMConfig
from ..mcp_server_config.mcp_server_config import MCPServerConfig
from ..skill_spec.skill_spec import SkillSpec
from ..token_budget.token_budget import TokenBudgetPolicy


class AgentConfig(BaseModel):
    """Agent role → model + prompt file + skills + tools."""

    model_ref: LLMConfig = Field(alias="model")
    temperature: float = 0.0
    prompt_file: str
    # One-line role description, injected into every agent's system prompt as
    # part of the generated "Your Team" section (issue #476).
    role: Optional[str] = None
    # Skill attachments (issue #955): each entry is `inject` (body rendered into
    # the prompt) or `discover` (summary catalog + on-demand retrieval).
    skills: list[SkillSpec] = Field(default_factory=list)
    tools: list[str] = Field(
        default=[
            "terminal", "read", "edit", "write", "task_tracker", "think",
            "finish",
        ]
    )
    # Declarative MCP servers attached to this agent's conversation at launch.
    # Each entry is either an inline MCPServerConfig spec or a full-string ref
    # (``"${mcp_servers.<name>}"``) into the root catalog, resolved at load time.
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    # Token-budget / compaction policy for this role (issue #851).
    token_budget: TokenBudgetPolicy = Field(default_factory=TokenBudgetPolicy)
    # Per-agent sampling parameter overrides
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    min_p: Optional[float] = None
    presence_penalty: Optional[float] = None
    repetition_penalty: Optional[float] = None
    stop_sequences: Optional[list[str]] = None
    # Per-complexity-tier overrides (complexity -> partial agent override)
    complexity_overrides: dict[Literal["low", "medium", "high"], AgentComplexityOverride] = Field(default_factory=dict)  # @stub-protected
