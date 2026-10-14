"""App configuration entity."""

from typing import Optional, Self

from pydantic import BaseModel, Field, model_validator

from ..agent_config.agent_config import AgentConfig
from ..complexity_policy.complexity_policy import ComplexityPolicyLayer
from ..llm_config.llm_config import LLMConfig
from ..mcp_server_config.mcp_server_config import MCPServerConfig
from ..provider_config.provider_config import ProviderConfig


class AppConfig(BaseModel):
    """Top-level application config: providers → llms → agents, plus mcp_servers.

    ``mcp_servers`` is the optional root *catalog* of reusable MCP server specs
    (issue #932), keyed by server name. An agent opts in by referencing an entry
    (``- "${mcp_servers.<name>}"``), which the ``${ref}`` resolver replaces with
    the entry dict before validation — so ``AgentConfig.mcp_servers`` still holds
    plain :class:`MCPServerConfig` instances at run time.

    ``complexity_policy`` is the optional *repo* layer of the complexity policy
    (issue #869). It is read from the same YAML as ``agents.<name>`` — the
    source that already carries ``complexity_overrides`` — and merged on top of
    the builtin layer at run start.
    """

    providers: dict[str, ProviderConfig]
    llms: dict[str, LLMConfig]
    agents: dict[str, AgentConfig]
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)  # @stub-protected
    complexity_policy: Optional[ComplexityPolicyLayer] = None

    @model_validator(mode="after")
    def _validate_mcp_catalog_keys(self) -> Self:  # @stub-protected
        """Each catalog key must equal its entry's ``name``.

        The ref path (``${mcp_servers.<key>}``) and the SDK handle
        (``mcp_config`` dict key, taken from ``MCPServerConfig.name``) then agree
        by construction, so a ref can never point at a server under one name that
        attaches under another.
        """
        for key, entry in self.mcp_servers.items():
            if entry.name != key:
                raise ValueError(
                    f"mcp_servers catalog key '{key}' must equal the server's "
                    f"name '{entry.name}'"
                )
        return self
