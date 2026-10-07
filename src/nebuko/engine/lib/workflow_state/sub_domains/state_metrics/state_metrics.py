from typing import Any, Optional

from pydantic import BaseModel, Field

# ``agent_runtimes_per_call_ms`` grows one entry per tool call; cap the tail we
# retain so an unbounded run cannot inflate the embedded state JSON.
MAX_CALL_RUNTIMES = 50


class StateMetrics(BaseModel):
    """Per-step agent metrics embedded in a workflow-state history entry.

    Mirrors the full ``metrics_schema.Metrics`` payload; ``from_full_metrics_dict``
    is the single mapping point between the two representations.
    """

    run_id: str = ""
    model_name: str = ""
    agent_role: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    max_context_window: int = 0
    cost: float = 0.0
    tool_call_latency_ms: float = 0.0
    agent_runtime_ms: float = 0.0
    nb_tool_calls: int = 0
    tool_call_error_rate: float = 0.0
    review_fix_requests: int = 0
    complexity: str = "N/A"
    start_time: str = ""
    end_time: str = ""
    agent_runtimes_per_call_ms: list[float] = Field(default_factory=list)
    tool_calls_per_tool: dict[str, int] = Field(default_factory=dict)
    errors_per_tool: dict[str, int] = Field(default_factory=dict)
    tokens_per_tool: dict[str, dict[str, int]] = Field(default_factory=dict)
    dispatch_recall_count: int = 0
    tokens_per_second: Optional[float] = None
    time_to_first_token: Optional[float] = None
    error: Optional[str] = None

    @staticmethod
    def from_full_metrics_dict(data: dict[str, Any]) -> "StateMetrics":  # @stub-protected
        """Map the full Metrics dict onto the state metrics record."""
        runtimes = list(data.get("agent_runtimes_per_call_ms") or [])
        return StateMetrics(
            run_id=data.get("run_id", ""),
            model_name=data.get("model_name", ""),
            agent_role=data.get("agent_role", ""),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            max_context_window=data.get("max_context_window", 0),
            cost=data.get("cost", 0.0),
            tool_call_latency_ms=data.get("tool_call_latency_ms", 0.0),
            agent_runtime_ms=data.get("agent_runtime_ms", 0.0),
            nb_tool_calls=data.get("nb_tool_calls", 0),
            tool_call_error_rate=data.get("tool_call_error_rate", 0.0),
            review_fix_requests=data.get("review_fix_requests", 0),
            complexity=data.get("complexity", "N/A"),
            start_time=data.get("start_time", ""),
            end_time=data.get("end_time", ""),
            agent_runtimes_per_call_ms=runtimes[-MAX_CALL_RUNTIMES:],
            tool_calls_per_tool=dict(data.get("tool_calls_per_tool") or {}),
            errors_per_tool=dict(data.get("errors_per_tool") or {}),
            tokens_per_tool=dict(data.get("tokens_per_tool") or {}),
            dispatch_recall_count=data.get("dispatch_recall_count", 0),
            tokens_per_second=data.get("tokens_per_second"),
            time_to_first_token=data.get("time_to_first_token"),
            error=data.get("error"),
        )