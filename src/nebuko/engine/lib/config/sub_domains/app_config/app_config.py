"""App configuration entity."""

from typing import Optional

from pydantic import BaseModel

from ..agent_config.agent_config import AgentConfig
from ..complexity_policy.complexity_policy import ComplexityPolicyLayer
from ..llm_config.llm_config import LLMConfig
from ..provider_config.provider_config import ProviderConfig


class AppConfig(BaseModel):
    """Top-level application config: providers → llms → agents.

    ``complexity_policy`` is the optional *repo* layer of the complexity policy
    (issue #882). It is read from the same YAML as ``agents.<name>`` — the
    source that already carries ``complexity_overrides`` — and merged on top of
    the builtin layer at run start.
    """

    providers: dict[str, ProviderConfig]
    llms: dict[str, LLMConfig]
    agents: dict[str, AgentConfig]
    complexity_policy: Optional[ComplexityPolicyLayer] = None
