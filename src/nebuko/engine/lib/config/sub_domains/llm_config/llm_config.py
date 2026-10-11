"""LLM configuration entity."""

from typing import Optional

from pydantic import BaseModel, Field

from ..provider_config.provider_config import ProviderConfig


class LLMConfig(BaseModel):
    """Model path, provider reference, optional pricing, reasoning, and sampling parameters."""

    model_path: str
    provider_ref: ProviderConfig = Field(alias="provider")
    default_temperature: float = 0.0
    input_price: Optional[float] = None
    output_price: Optional[float] = None
    # Cache-tier prices (USD per 1M tokens). Unset falls back to the input
    # price for cost accounting; see ``pricing`` and ``metrics/cost.py``.
    cache_read_price: Optional[float] = None
    cache_write_price: Optional[float] = None
    # Reasoning / thinking parameters (Qwen3, vLLM, etc.)
    thinking_enabled: Optional[bool] = None
    preserve_thinking: Optional[bool] = None
    thinking_token_budget: Optional[int] = None
    max_output_tokens: Optional[int] = None
    # Sampling parameters for instruction-following control
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    min_p: Optional[float] = None
    presence_penalty: Optional[float] = None
    repetition_penalty: Optional[float] = None
    stop_sequences: Optional[list[str]] = None
