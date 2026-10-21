#!/usr/bin/env python3
"""
Metrics schema for agent runtime tracking.

Defines a centralized Metrics dataclass that is used throughout the codebase
for generating, parsing, and aggregating agent metrics. Provides to_dict(),
from_dict(), and to_json() methods for serialization/deserialization.

No external dependencies — uses only Python standard library (dataclasses).
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional
import json


@dataclass
class Metrics:
    """
    Centralized schema for agent metrics.

    Fields:
        run_id: UUID string identifying the conversation run.
        model_name: LLM model identifier (e.g. "default", "gpt-5.5").
        agent_role: Agent role name ("architect", "context", "dev", "review").
        input_tokens: Number of input/prompt tokens consumed.
        output_tokens: Number of output/completion tokens generated.
        max_context_window: Peak context window observed during the run (used for KV cache estimation).
        cache_hit_rate: Fraction of input tokens served from the provider's
            prompt cache, in [0.0, 1.0] (0.0 when unavailable).
        cache_hit_tokens: Cumulative input tokens served from the provider's
            prompt cache. A subset of ``input_tokens`` in the inclusive
            (OpenAI/litellm) shape; see ADR 0014 for the inclusive/exclusive
            guard.
        reasoning_tokens: Cumulative reasoning tokens generated during the run.
            A subset of ``output_tokens``.
        cost: Total LLM cost for the run in USD, as reported by the SDK
            (0.0 when the model has no pricing configured).
        complexity: Task complexity level (e.g. "medium", "high").
        tool_call_latency_ms: Average tool call latency in milliseconds.
        agent_runtime_ms: Total agent runtime in milliseconds (sum of all calls).
        agent_runtimes_per_call_ms: Individual call latencies in milliseconds.
        nb_tool_calls: Total number of tool calls made.
        tool_call_error_rate: Percentage of tool calls that resulted in errors.
        start_time: ISO 8601 datetime string when the agent started.
        end_time: ISO 8601 datetime string when the agent finished.
        tool_calls_per_tool: Mapping of tool name to call count.
        errors_per_tool: Mapping of tool name to error count.
        tokens_per_tool: Mapping of tool name to token usage breakdown.
        tool_time_per_tool: Mapping of tool name to
            ``{"total_ms": float, "count": int}`` tool execution time (issue
            #1016). The mean is derived (``total_ms / count``) and never
            persisted, so the buckets merge by plain summation across runs.
            Durations are ``ObservationEvent.timestamp - ActionEvent.timestamp``
            and therefore an upper bound (SDK and hook overhead included);
            parallel calls overlap, so sums may exceed ``wall_clock_ms``.
        tool_runtime_ms: Total tool execution time in milliseconds (sum of all
            matched per-call durations).
        wall_clock_ms: Wall-clock time in milliseconds (``end_time - start_time``).
        review_fix_requests: Number of times review asked dev to fix something.
        dispatch_recall_count: Number of recall turns injected because the agent
            ended without calling dispatch_next (issue #524).
        publication_guard: Result of the publication guard on an approved write
            step — ``clean``, ``recalled_ok``, ``fallback_pr`` or
            ``fallback_failed``. None when the guard did not run (non-write
            role, non-approved verdict, no git remote).
        tokens_per_second: Derived generation throughput in tokens/second, or
            None when it could not be observed (issue #722).
        time_to_first_token: Derived seconds between request start and first
            token, or None when it could not be observed (issue #722).
        error: Optional error message if metrics collection failed.
    """

    run_id: str = ""
    model_name: str = ""
    agent_role: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    max_context_window: int = 0
    cache_hit_rate: float = 0.0
    cache_hit_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0
    complexity: str = "N/A"
    tool_call_latency_ms: float = 0.0
    agent_runtime_ms: float = 0.0
    agent_runtimes_per_call_ms: List[float] = field(default_factory=list)
    nb_tool_calls: int = 0
    tool_call_error_rate: float = 0.0
    start_time: str = ""
    end_time: str = ""
    tool_calls_per_tool: Dict[str, int] = field(default_factory=dict)
    errors_per_tool: Dict[str, int] = field(default_factory=dict)
    tokens_per_tool: Dict[str, Dict[str, int]] = field(default_factory=dict)
    tool_time_per_tool: Dict[str, Dict[str, float]] = field(default_factory=dict)
    tool_runtime_ms: float = 0.0
    wall_clock_ms: float = 0.0
    review_fix_requests: int = 0
    dispatch_recall_count: int = 0
    publication_guard: Optional[str] = None
    tokens_per_second: float | None = None
    time_to_first_token: float | None = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert Metrics to a plain dictionary, excluding None values."""
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    def to_json(self, compact: bool = True) -> str:
        """
        Serialize Metrics to a JSON string.

        Args:
            compact: If True, use compact separators (no spaces).
                     If False, use pretty-printed format with indentation.

        Returns:
            JSON string representation of the metrics.
        """
        if compact:
            return json.dumps(self.to_dict(), separators=(",", ":"))
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Metrics":
        """
        Create a Metrics instance from a dictionary.

        Only fields defined in the dataclass are extracted. Extra keys
        in the dictionary are silently ignored. Missing fields use their
        default values.

        Args:
            data: Dictionary containing metrics data.

        Returns:
            A new Metrics instance.
        """
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def from_json(cls, json_str: str) -> "Metrics":
        """
        Deserialize a Metrics instance from a JSON string.

        Args:
            json_str: JSON string representation of metrics.

        Returns:
            A new Metrics instance.
        """
        data = json.loads(json_str)
        return cls.from_dict(data)
