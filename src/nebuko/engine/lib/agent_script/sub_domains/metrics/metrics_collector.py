"""LLM metrics collection — pure functions for extracting metrics from conversations."""

from dataclasses import dataclass
from datetime import datetime, timezone

from openhands.sdk import get_logger
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.observation import ObservationEvent

import os

from metrics_schema import Metrics

from pricing.features.resolve_model_rates.resolve_model_rates import (
    resolve_model_rates,
)

from agent_script.sub_domains.metrics.cost import CostRates, SessionCost
from agent_script.sub_domains.metrics.token_telemetry import TokenTelemetry
from agent_script.sub_domains.metrics.token_telemetry_collector import (
    get_active_collector,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class _CapturedTelemetry:
    """One drain of the capture seam: provider-health means + cache totals."""

    tokens_per_second: float | None
    time_to_first_token: float | None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def _drain_captured_telemetry() -> "_CapturedTelemetry":
    """Drain the capture collector once, returning health means + cache totals.

    Drains the active capture collector (issue #829) so it can feed both the
    provider-health indicators and the cache-token tiers cost accounting needs.
    Returns an all-empty record when no collector was installed or nothing was
    observable.
    """
    collector = get_active_collector()
    if collector is None:
        return _CapturedTelemetry(None, None, 0, 0)
    observations = collector.drain()
    mean_ttft, mean_tps = TokenTelemetry.aggregate(observations)
    return _CapturedTelemetry(
        tokens_per_second=mean_tps,
        time_to_first_token=mean_ttft,
        cache_read_tokens=sum(obs.cache_read_tokens for obs in observations),
        cache_write_tokens=sum(obs.cache_write_tokens for obs in observations),
    )


def _cache_token_counts(accumulated_usage, captured: "_CapturedTelemetry") -> tuple[int, int]:
    """Cache-read/cache-write counts, preferring whichever source saw more.

    The SDK's accumulated ``cache_write_tokens`` is empty for OpenAI-shaped
    responses: it reads ``cache_creation_tokens``, a field litellm's
    ``PromptTokensDetails`` does not define (it carries ``cache_write_tokens``).
    The capture seam reads that field correctly, so it supplies the cache-write
    tier when the SDK is silent (issue #714 review).
    """
    sdk_read = int(getattr(accumulated_usage, "cache_read_tokens", 0) or 0)
    sdk_write = int(getattr(accumulated_usage, "cache_write_tokens", 0) or 0)
    return (
        max(sdk_read, captured.cache_read_tokens),
        max(sdk_write, captured.cache_write_tokens),
    )


def _effective_model_ref(agent_cfg):
    """The LLMConfig whose prices apply, honoring the active complexity tier."""
    if agent_cfg is None:
        return None
    complexity = os.environ.get("CONFIG_COMPLEXITY", "").lower()
    if complexity in ("low", "medium", "high"):
        overrides = getattr(agent_cfg, "complexity_overrides", None) or {}
        tier = overrides.get(complexity)
        if tier is not None and tier.model_ref is not None:
            return tier.model_ref
    return agent_cfg.model_ref


def _resolve_price(agent_cfg, price_table):
    """Merge configured prices with the default price table for the session model."""
    model_ref = _effective_model_ref(agent_cfg)
    if model_ref is None:
        return None
    return resolve_model_rates(model_ref, price_table)


# @stub-protected: signature defined by architect — do not modify
def _session_cost(
    combined_metrics,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    model_price,
) -> float:
    """Cost for the session: SDK-reported, else tokens × resolved model rates."""
    rates = None
    if model_price is not None:
        rates = CostRates.from_prices(
            model_price.input_price,
            model_price.output_price,
            model_price.cache_read_price,
            model_price.cache_write_price,
        )
    sdk_reported = float(getattr(combined_metrics, "accumulated_cost", 0.0) or 0.0)
    return SessionCost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        rates=rates,
        sdk_reported_cost=sdk_reported,
    ).total()


def _compute_tool_calls_per_tool(conversation):
    """
    Group ActionEvents by tool_name to produce per-tool call counts.

    Args:
        conversation: The OpenHands Conversation object

    Returns:
        dict: Mapping of tool_name to call count
    """
    action_events = [e for e in conversation.state.events if isinstance(e, ActionEvent)]
    agent_tool_calls = [e for e in action_events if e.source == "agent"]

    tool_counts = {}
    for event in agent_tool_calls:
        tool_name = event.tool_name if hasattr(event, 'tool_name') else "unknown"
        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

    return tool_counts


def _compute_errors_per_tool(conversation):
    """
    Count observation errors per tool_name.

    Args:
        conversation: The OpenHands Conversation object

    Returns:
        dict: Mapping of tool_name to error count
    """
    observation_events = [e for e in conversation.state.events if isinstance(e, ObservationEvent)]

    errors_per_tool = {}
    for event in observation_events:
        # Get the tool_name from the related action event
        tool_name = "unknown"
        if hasattr(event, 'action_event') and event.action_event:
            action = event.action_event
            if hasattr(action, 'tool_name'):
                tool_name = action.tool_name
        # Check if this observation is an error
        if hasattr(event, 'observation') and hasattr(event.observation, 'is_error') and event.observation.is_error:
            errors_per_tool[tool_name] = errors_per_tool.get(tool_name, 0) + 1

    return errors_per_tool


def _compute_tokens_per_tool(combined_metrics):
    """
    Compute token usage per tool from combined metrics.

    Args:
        combined_metrics: The combined metrics object from conversation

    Returns:
        dict: Mapping of tool_name to token usage dict
    """
    if not combined_metrics.token_usages:
        return {}

    tokens_per_tool = {}
    for usage in combined_metrics.token_usages:
        tool_name = getattr(usage, 'tool_name', 'unknown') or 'unknown'
        bucket = tokens_per_tool.setdefault(tool_name, {
            'input': 0, 'output': 0, 'cache_hit': 0, 'reasoning': 0,
        })
        bucket['input'] += getattr(usage, 'prompt_tokens', 0) or 0
        bucket['output'] += getattr(usage, 'completion_tokens', 0) or 0
        bucket['cache_hit'] += getattr(usage, 'cache_read_tokens', 0) or 0
        bucket['reasoning'] += getattr(usage, 'reasoning_tokens', 0) or 0

    return tokens_per_tool


def _cache_hit_rate(combined_metrics):
    """
    Read the SDK's cache-hit ratio as a fraction in [0.0, 1.0].

    ``combined_metrics.cache_hit_rate`` is already a derived ratio (issue #888);
    it is None when no input tokens were measurable, which maps to 0.0.

    Args:
        combined_metrics: The combined metrics object from conversation

    Returns:
        float: Cache-hit fraction, or 0.0 when unavailable
    """
    rate = getattr(combined_metrics, 'cache_hit_rate', None)
    return float(rate) if rate is not None else 0.0


def _reasoning_tokens(combined_metrics):
    """
    Sum reasoning tokens across all per-call usages.

    Args:
        combined_metrics: The combined metrics object from conversation

    Returns:
        int: Cumulative reasoning tokens (0 when none reported)
    """
    return sum(
        getattr(usage, 'reasoning_tokens', 0) or 0
        for usage in (combined_metrics.token_usages or [])
    )


def collect_llm_metrics(conversation, agent_cfg=None, price_table=None):
    """
    Collect LLM metrics from a conversation after it has completed.

    Args:
        conversation: The OpenHands Conversation object
        agent_cfg: Optional AgentConfig whose ``model_ref`` seeds the
            deterministic cost fallback (its configured prices win per field).
        price_table: Optional default :class:`PriceTable` supplying prices the
            config does not set, so subscription-provider models are costed.

    Returns:
        Metrics: Metrics object containing all collected metrics
    """
    try:
        # Get combined metrics across all LLMs
        combined_metrics = conversation.conversation_stats.get_combined_metrics()

        # Count tool calls
        action_events = [e for e in conversation.state.events if isinstance(e, ActionEvent)]
        agent_tool_calls = [e for e in action_events if e.source == "agent"]
        nb_tool_calls = len(agent_tool_calls)

        # Count tool call errors
        observation_events = [e for e in conversation.state.events if isinstance(e, ObservationEvent)]
        total_observations = len(observation_events)
        error_observations = len([e for e in observation_events if hasattr(e, 'observation') and hasattr(e.observation, 'is_error') and e.observation.is_error])

        # Calculate error rate (avoid division by zero)
        tool_call_error_rate = 0.0
        if total_observations > 0:
            tool_call_error_rate = (error_observations / total_observations) * 100

        # Calculate average latency (latency is in seconds, convert to milliseconds)
        tool_call_latency_ms = 0.0
        if combined_metrics.response_latencies:
            latencies = [lat.latency for lat in combined_metrics.response_latencies]
            total_latency = sum(latencies)
            # Convert from seconds to milliseconds
            tool_call_latency_ms = (total_latency / len(latencies)) * 1000
            # Total LLM runtime (sum of all call latencies in ms)
            agent_runtime_ms = round(total_latency * 1000, 2)
            # Individual call latencies in ms
            agent_runtimes_per_call_ms = [round(l * 1000, 2) for l in latencies]
        else:
            agent_runtime_ms = 0.0
            agent_runtimes_per_call_ms = []

        # Get token usage (including the SDK's cache buckets)
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_write_tokens = 0
        max_context_window = 0

        # Drain the capture seam once — it feeds both the provider-health means
        # and, when the SDK's cache-write bucket is empty (litellm field-name
        # mismatch), the cache-write tier cost accounting needs.
        captured = _drain_captured_telemetry()

        if combined_metrics.accumulated_token_usage:
            input_tokens = combined_metrics.accumulated_token_usage.prompt_tokens
            output_tokens = combined_metrics.accumulated_token_usage.completion_tokens
            cache_read_tokens, cache_write_tokens = _cache_token_counts(
                combined_metrics.accumulated_token_usage, captured
            )
            max_context_window = combined_metrics.accumulated_token_usage.context_window

        # Session cost: the SDK's accumulated_cost when it priced the model,
        # otherwise tokens × resolved rates (config over the default price
        # table; 0.0 only when neither source knows the model).
        model_price = _resolve_price(agent_cfg, price_table)
        cost = _session_cost(
            combined_metrics,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            model_price,
        )

        # Compute per-tool statistics
        tool_calls_per_tool = _compute_tool_calls_per_tool(conversation)
        errors_per_tool = _compute_errors_per_tool(conversation)
        tokens_per_tool = _compute_tokens_per_tool(combined_metrics)

        # Provider cache/reasoning signals (issue #888).
        cache_hit_rate = _cache_hit_rate(combined_metrics)
        reasoning_tokens = _reasoning_tokens(combined_metrics)

        # Get start time (first event timestamp or current time)
        start_time = datetime.now(timezone.utc).isoformat()
        if conversation.state.events:
            first_event = conversation.state.events[0]
            if hasattr(first_event, 'timestamp'):
                start_time = first_event.timestamp

        # Provider-health indicators captured over the run's LLM calls (#829).
        tokens_per_second = captured.tokens_per_second
        time_to_first_token = captured.time_to_first_token

        # Build Metrics object
        role_name = os.environ.get("ROLE_NAME", "unknown")
        metrics = Metrics(
            run_id=str(conversation.id),
            model_name=combined_metrics.model_name,
            agent_role=role_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            max_context_window=max_context_window,
            cache_hit_rate=cache_hit_rate,
            cache_hit_tokens=cache_read_tokens,
            reasoning_tokens=reasoning_tokens,
            cost=cost,
            complexity=os.environ.get("COMPLEXITY", "N/A"),
            tool_call_latency_ms=tool_call_latency_ms,
            agent_runtime_ms=agent_runtime_ms,
            agent_runtimes_per_call_ms=agent_runtimes_per_call_ms,
            nb_tool_calls=nb_tool_calls,
            tool_call_error_rate=tool_call_error_rate,
            start_time=start_time,
            end_time=datetime.now(timezone.utc).isoformat(),
            tool_calls_per_tool=tool_calls_per_tool,
            errors_per_tool=errors_per_tool,
            tokens_per_tool=tokens_per_tool,
            tokens_per_second=tokens_per_second,
            time_to_first_token=time_to_first_token,
        )

        logger.info(f"Collected LLM metrics for run {metrics.run_id}")
        return metrics

    except Exception as e:
        logger.error(f"Failed to collect LLM metrics: {e}")
        # Return minimal metrics on error
        return Metrics(
            run_id=str(conversation.id) if conversation else "unknown",
            model_name="unknown",
            agent_role=os.environ.get("ROLE_NAME", "unknown"),
            input_tokens=0,
            output_tokens=0,
            complexity=os.environ.get("COMPLEXITY", "N/A"),
            tool_call_latency_ms=0.0,
            nb_tool_calls=0,
            tool_call_error_rate=0.0,
            start_time=datetime.now(timezone.utc).isoformat(),
            end_time=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )
