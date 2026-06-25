from typing import Any

from pydantic import BaseModel

class StateMetrics(BaseModel):
    """Compact metrics embedded in workflow state (subset of Metrics schema)."""

    run_id: str = ""
    model_name: str = ""
    agent_role: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    max_context_window: int = 0
    tool_call_latency_ms: float = 0.0
    agent_runtime_ms: float = 0.0
    nb_tool_calls: int = 0
    tool_call_error_rate: float = 0.0
    review_fix_requests: int = 0

    @staticmethod
    def from_full_metrics_dict(data: dict[str, Any]) -> "StateMetrics":
        """Extract relevant fields from the full Metrics dict."""
        return StateMetrics(
            run_id=data.get("run_id", ""),
            model_name=data.get("model_name", ""),
            agent_role=data.get("agent_role", ""),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            max_context_window=data.get("max_context_window", 0),
            tool_call_latency_ms=data.get("tool_call_latency_ms", 0.0),
            agent_runtime_ms=data.get("agent_runtime_ms", 0.0),
            nb_tool_calls=data.get("nb_tool_calls", 0),
            tool_call_error_rate=data.get("tool_call_error_rate", 0.0),
            review_fix_requests=data.get("review_fix_requests", 0),
        )