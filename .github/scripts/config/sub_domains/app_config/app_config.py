"""App configuration entity."""

from pydantic import BaseModel, Field

from ..agent_config.agent_config import AgentConfig
from ..llm_config.llm_config import LLMConfig
from ..metrics.cost import MetricsCostConfig
from ..provider_config.provider_config import ProviderConfig


class AppConfig(BaseModel):
    """Top-level application config: providers → llms → agents."""

    providers: dict[str, ProviderConfig]
    llms: dict[str, LLMConfig]
    agents: dict[str, AgentConfig]
    metrics_cost: MetricsCostConfig = Field(default_factory=MetricsCostConfig)
