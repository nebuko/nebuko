from typing import Optional

from pydantic import BaseModel


class ModelExecutionConfig(BaseModel):
    """Effective model execution parameters captured at run time, for auditability and replay.

    Records what was actually sent to the provider — not what the role config
    declares — so a run can be replayed against another configuration and bugs
    caused by a wrong configuration can be spotted (issue #820).
    """

    model_path: str = ""
    provider: str = ""
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    min_p: Optional[float] = None
    presence_penalty: Optional[float] = None
    repetition_penalty: Optional[float] = None
    stop_sequences: Optional[list[str]] = None
    thinking_enabled: Optional[bool] = None
    reasoning_effort: Optional[str] = None
    max_output_tokens: Optional[int] = None
    # Active complexity tier ("low"/"medium"/"high"), "" when none was applied.
    complexity_tier: str = ""
    # "base" when the role's own model ran, "complexity_override" when a tier
    # override selected the model.
    model_source: str = "base"
