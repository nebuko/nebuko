#!/usr/bin/env python3
"""
Aggregate agent metrics from issue comments on issue close.

Fetches all comments from a GitHub issue, reads the per-step agent metrics
embedded in each comment's ``workflow_state`` snapshot (issue #792), aggregates
by model name and agent role, and posts a summary table.

Environment Variables:
    GITHUB_TOKEN: GitHub API token (required)
    GITHUB_REPOSITORY: Repository in format owner/repo (required)
    ISSUE_NUMBER: Issue number to analyze (required)
"""

import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

# Import the Metrics schema
sys.path.insert(0, os.path.dirname(__file__))
from github_client import (
    BOT_MARKER,
    GitHubTransport,
    fetch_issue_comments,
)
from github_client import post_comment as _github_post_comment
from metrics_schema import Metrics
from human_metrics import (
    CommentRecord,
    HumanMetricsAnalyzer,
    HumanMetricsConfig,
    HumanMetricsRenderer,
    IssueData,
)
from workflow_state.features.get_last_state.get_last_state import extract_state_from_body
from agent_script.sub_domains.metrics.report_cost_resolver import ReportCostResolver

# Agent role marker emitted at the top of every agent comment (e.g. bot-agent-dev).
BOT_AGENT_ROLE_REGEX = re.compile(r"<!--\s*bot-agent-([a-z0-9_-]+)\s*-->")
# Human workflow trigger (e.g. @@dev, @@architect) used to count human commands.
HUMAN_COMMAND_REGEX = re.compile(r"@@[a-z][a-z0-9_-]*")


def fetch_comments(repo, issue_number, token, per_page=100):
    """Fetch all comments from an issue via the rate-limit-aware transport.

    The whole thread is returned in API order (oldest first); the transport
    handles pagination, rate-limit classification and bounded retries.
    """
    result = fetch_issue_comments(repo, issue_number, token)
    if not result.ok:
        print(f"Error fetching comments: {result.detail}")
        return []
    return list(result.comments)


def fetch_issue(repo, issue_number, token):
    """Fetch a single issue's body and metadata (body, closed_at)."""
    transport = GitHubTransport(token or None)
    result = transport.call(
        ["api", f"/repos/{repo}/issues/{issue_number}"], mutative=False
    )
    if not result.ok:
        print(f"Error fetching issue: {result.detail}")
        return {}
    try:
        return json.loads(result.payload) if result.payload.strip() else {}
    except json.JSONDecodeError as e:
        print(f"Error fetching issue: {e}")
        return {}


def extract_metrics_from_comment(body):
    """Return the per-step ``Metrics`` records carried by a comment's state.

    Each ``workflow_state`` snapshot embeds one history entry per agent step,
    and every entry holds the metrics of the invocation that produced it
    (issue #792). Snapshots accumulate the full history, so callers must
    de-duplicate entries shared with earlier comments.
    """
    state = extract_state_from_body(body)
    if state is None:
        return []
    return [
        Metrics.from_dict(entry.metrics.model_dump())
        for entry in state.history
        if entry.metrics is not None
    ]


def deduplicate_metrics(metrics_list):
    """Drop duplicate step records repeated across accumulating snapshots.

    Every comment embeds the run's full history, so a step's metrics recur in
    each later comment. Records are keyed on ``run_id`` (falling back to the
    run window plus agent role when it is missing) and the first occurrence
    wins (issue #792).
    """
    seen = set()
    unique = []
    for metric in metrics_list:
        key = metric.run_id or f"{metric.start_time}|{metric.end_time}|{metric.agent_role}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(metric)
    return unique


def _copy_per_tool_dict(per_tool):
    """Copy a per-tool dict one level deep, cloning nested value dicts."""
    if not per_tool:
        return {}
    return {
        tool: dict(values) if isinstance(values, dict) else values
        for tool, values in per_tool.items()
    }


def _merge_per_tool_dict(base, override):
    """
    Merge two per-tool dicts: base and override.

    Values from override are added to base. Handles both simple numeric
    values and nested token dicts (input/output/cache_hit/reasoning).

    Args:
        base: First per-tool dict (may be None or empty)
        override: Second per-tool dict (may be None or empty)

    Returns:
        Merged per-tool dict
    """
    # Never alias a source metrics dict: nested buckets are summed in place.
    if not base:
        return _copy_per_tool_dict(override)
    if not override:
        return _copy_per_tool_dict(base)

    merged = _copy_per_tool_dict(base)
    for tool, values in override.items():
        if tool in merged:
            existing = merged[tool]
            if isinstance(values, dict) and isinstance(existing, dict):
                # Check if this is a token usage dict
                if all(k in existing for k in ('input', 'output', 'cache_hit', 'reasoning')):
                    merged[tool] = {
                        'input': existing['input'] + values.get('input', 0),
                        'output': existing['output'] + values.get('output', 0),
                        'cache_hit': existing['cache_hit'] + values.get('cache_hit', 0),
                        'reasoning': existing['reasoning'] + values.get('reasoning', 0),
                    }
                else:
                    # Generic dict merge: sum numeric values
                    for k, v in values.items():
                        existing[k] = existing.get(k, 0) + v
            else:
                # Simple numeric values: sum them
                merged[tool] = existing + values
        else:
            merged[tool] = dict(values) if isinstance(values, dict) else values

    return merged


def _format_tool_dict(tool_dict):
    """
    Format a per-tool dict for display.
    
    Args:
        tool_dict: Per-tool dict (may be None, empty, or populated)
        
    Returns:
        Formatted string or "N/A" if empty
    """
    if not tool_dict:
        return "N/A"
    # Sort alphabetically for consistent display
    parts = [f"{tool}: {count}" for tool, count in sorted(tool_dict.items())]
    return ", ".join(parts)


def _accumulate_provider_health(agg, tokens_per_second, time_to_first_token):
    """Fold one run's provider-health values into the aggregate sums.

    Each metric has its own count so a run missing one value still contributes
    the other (issue #829).
    """
    if tokens_per_second is not None:
        agg["tps_sum"] += tokens_per_second
        agg["tps_count"] += 1
    if time_to_first_token is not None:
        agg["ttft_sum"] += time_to_first_token
        agg["ttft_count"] += 1


def _format_provider_metric(total, count, decimals):
    """Mean of a provider-health metric over observable runs, or 'n/a'."""
    if not count:
        return "n/a"
    return f"{total / count:.{decimals}f}"


def _accumulate_cache_and_reasoning(
    agg, reasoning_tokens, cache_hit_rate, input_tokens, cache_hit_tokens=0
):
    """Fold one run's cache/reasoning counters into the aggregate.

    Reasoning tokens and cache-hit tokens are additive and simply summed. The
    cache-hit rate is a non-additive ratio, so it is accumulated as an
    input-token-weighted mean: each run contributes ``rate * input_tokens``
    together with its ``input_tokens`` as weight (issue #888).
    """
    agg["total_reasoning_tokens"] += reasoning_tokens
    agg["total_cache_hit_tokens"] += cache_hit_tokens
    agg["cache_weighted_sum"] += cache_hit_rate * input_tokens
    agg["cache_weight"] += input_tokens


def format_rate(rate):
    """Render a [0.0, 1.0] cache-hit fraction as e.g. '48.19%'.

    Zero or missing rates render as 'n/a' — with no cache-hit signal there is
    no meaningful ratio to show, matching the provider-health formatter's
    no-data convention (issue #888).
    """
    if not rate:
        return "n/a"
    return f"{rate * 100:.2f}%"


def _bucket_cache_and_reasoning(agg):
    """Display strings for a bucket's cache-hit rate and token counters.

    The bucket rate is the input-token-weighted mean of its runs' rates; with
    no observed input tokens it falls back to 0.0 (rendered 'n/a').
    """
    weight = agg.get("cache_weight", 0)
    rate = agg["cache_weighted_sum"] / weight if weight else 0.0
    cache_hit = format_number(agg.get("total_cache_hit_tokens", 0))
    reasoning = format_number(agg.get("total_reasoning_tokens", 0))
    return format_rate(rate), cache_hit, reasoning


def aggregate_by_model(all_metrics, resolver=None):  # @stub-protected
    """Aggregate metrics by model name.

    When ``resolver`` is given, entries are grouped by
    ``resolver.model_key(...)`` and costed via ``resolver.effective_cost(...)``
    (issue #928); ``None`` preserves the raw ``model_name``/``cost`` behaviour.
    """
    aggregated = defaultdict(lambda: {
        "runs": 0,
        "total_input_tokens": 0,
        "total_uncached_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost": 0.0,
        "total_context_window": 0,
        "latency_sum": 0.0,
        "llm_runtime_sum": 0.0,
        "tool_calls_sum": 0,
        "error_rate_sum": 0.0,
        "review_fix_requests_sum": 0,
        "tool_calls_per_tool": {},
        "errors_per_tool": {},
        "tokens_per_tool": {},
        "tps_sum": 0.0,
        "tps_count": 0,
        "ttft_sum": 0.0,
        "ttft_count": 0,
        "total_reasoning_tokens": 0,
        "total_cache_hit_tokens": 0,
        "cache_weighted_sum": 0.0,
        "cache_weight": 0,
    })

    for m in all_metrics:
        # Support both Metrics objects and legacy dicts
        if isinstance(m, Metrics):
            model = resolver.model_key(m) if resolver else (m.model_name or "unknown")
            input_tokens = m.input_tokens
            output_tokens = m.output_tokens
            cost = resolver.effective_cost(m) if resolver else m.cost
            context_window = m.max_context_window
            latency = m.tool_call_latency_ms
            runtime = m.agent_runtime_ms
            tool_calls = m.nb_tool_calls
            error_rate = m.tool_call_error_rate
            fix_requests = m.review_fix_requests
            tool_calls_per_tool = m.tool_calls_per_tool
            errors_per_tool = m.errors_per_tool
            tokens_per_tool = m.tokens_per_tool
            tokens_per_second = m.tokens_per_second
            time_to_first_token = m.time_to_first_token
            reasoning_tokens = m.reasoning_tokens
            cache_hit_rate = m.cache_hit_rate
            cache_hit_tokens = m.cache_hit_tokens
        else:
            # Legacy dict support
            model = resolver.model_key(m) if resolver else m.get("model_name", "unknown")
            input_tokens = m.get("input_tokens", 0)
            output_tokens = m.get("output_tokens", 0)
            cost = resolver.effective_cost(m) if resolver else m.get("cost", 0.0)
            context_window = m.get("max_context_window", 0)
            latency = m.get("tool_call_latency_ms", 0.0)
            runtime = m.get("agent_runtime_ms", 0.0)
            tool_calls = m.get("nb_tool_calls", 0)
            error_rate = m.get("tool_call_error_rate", 0.0)
            fix_requests = m.get("review_fix_requests", 0)
            tool_calls_per_tool = m.get("tool_calls_per_tool", {})
            errors_per_tool = m.get("errors_per_tool", {})
            tokens_per_tool = m.get("tokens_per_tool", {})
            tokens_per_second = m.get("tokens_per_second")
            time_to_first_token = m.get("time_to_first_token")
            reasoning_tokens = m.get("reasoning_tokens", 0)
            cache_hit_rate = m.get("cache_hit_rate", 0.0)
            cache_hit_tokens = m.get("cache_hit_tokens", 0)

        agg = aggregated[model]
        agg["runs"] += 1
        agg["total_input_tokens"] += input_tokens
        agg["total_uncached_input_tokens"] += InputTokenSplit(
            input_tokens=input_tokens, cache_read_tokens=cache_hit_tokens
        ).uncached_input_tokens
        agg["total_output_tokens"] += output_tokens
        agg["total_cost"] += cost
        agg["total_context_window"] += context_window
        agg["latency_sum"] += latency
        agg["llm_runtime_sum"] += runtime
        agg["tool_calls_sum"] += tool_calls
        agg["error_rate_sum"] += error_rate
        agg["review_fix_requests_sum"] += fix_requests
        _accumulate_provider_health(agg, tokens_per_second, time_to_first_token)
        _accumulate_cache_and_reasoning(
            agg, reasoning_tokens, cache_hit_rate, input_tokens, cache_hit_tokens
        )
        # Merge per-tool data
        agg["tool_calls_per_tool"] = _merge_per_tool_dict(
            agg["tool_calls_per_tool"], tool_calls_per_tool
        )
        agg["errors_per_tool"] = _merge_per_tool_dict(
            agg["errors_per_tool"], errors_per_tool
        )
        agg["tokens_per_tool"] = _merge_per_tool_dict(
            agg["tokens_per_tool"], tokens_per_tool
        )

    return aggregated


AGENT_DISPLAY_NAMES = {
    "architect": "Architect",
    "context": "Context",
    "dev": "Dev",
}


def get_agent_display_name(agent_role):
    """Get display name for an agent role."""
    return AGENT_DISPLAY_NAMES.get(agent_role, agent_role.title())


def aggregate_by_agent(all_metrics, resolver=None):  # @stub-protected
    """Aggregate metrics by agent_role.

    When ``resolver`` is given, per-role cost is ``resolver.effective_cost(...)``
    (issue #928); ``None`` preserves the raw ``cost`` behaviour.
    """
    aggregated = defaultdict(lambda: {
        "runs": 0,
        "total_input_tokens": 0,
        "total_uncached_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost": 0.0,
        "total_context_window": 0,
        "latency_sum": 0.0,
        "llm_runtime_sum": 0.0,
        "tool_runtime_sum": 0.0,
        "wall_clock_sum": 0.0,
        "tool_calls_sum": 0,
        "error_rate_sum": 0.0,
        "review_fix_requests_sum": 0,
        "tool_calls_per_tool": {},
        "errors_per_tool": {},
        "tokens_per_tool": {},
        "tool_time_per_tool": {},
        "tps_sum": 0.0,
        "tps_count": 0,
        "ttft_sum": 0.0,
        "ttft_count": 0,
        "total_reasoning_tokens": 0,
        "total_cache_hit_tokens": 0,
        "cache_weighted_sum": 0.0,
        "cache_weight": 0,
    })

    for m in all_metrics:
        # Support both Metrics objects and legacy dicts
        if isinstance(m, Metrics):
            agent = m.agent_role or "unknown"
            input_tokens = m.input_tokens
            output_tokens = m.output_tokens
            cost = resolver.effective_cost(m) if resolver else m.cost
            context_window = m.max_context_window
            latency = m.tool_call_latency_ms
            runtime = m.agent_runtime_ms
            tool_calls = m.nb_tool_calls
            error_rate = m.tool_call_error_rate
            fix_requests = m.review_fix_requests
            tool_calls_per_tool = m.tool_calls_per_tool
            errors_per_tool = m.errors_per_tool
            tokens_per_tool = m.tokens_per_tool
            tokens_per_second = m.tokens_per_second
            time_to_first_token = m.time_to_first_token
            reasoning_tokens = m.reasoning_tokens
            cache_hit_rate = m.cache_hit_rate
            cache_hit_tokens = m.cache_hit_tokens
            tool_time_per_tool = m.tool_time_per_tool
            tool_runtime = m.tool_runtime_ms
            wall_clock = m.wall_clock_ms
        else:
            # Legacy dict support
            agent = m.get("agent_role", "unknown")
            input_tokens = m.get("input_tokens", 0)
            output_tokens = m.get("output_tokens", 0)
            cost = resolver.effective_cost(m) if resolver else m.get("cost", 0.0)
            context_window = m.get("max_context_window", 0)
            latency = m.get("tool_call_latency_ms", 0.0)
            runtime = m.get("agent_runtime_ms", 0.0)
            tool_calls = m.get("nb_tool_calls", 0)
            error_rate = m.get("tool_call_error_rate", 0.0)
            fix_requests = m.get("review_fix_requests", 0)
            tool_calls_per_tool = m.get("tool_calls_per_tool", {})
            errors_per_tool = m.get("errors_per_tool", {})
            tokens_per_tool = m.get("tokens_per_tool", {})
            tokens_per_second = m.get("tokens_per_second")
            time_to_first_token = m.get("time_to_first_token")
            reasoning_tokens = m.get("reasoning_tokens", 0)
            cache_hit_rate = m.get("cache_hit_rate", 0.0)
            cache_hit_tokens = m.get("cache_hit_tokens", 0)
            tool_time_per_tool = m.get("tool_time_per_tool", {})
            tool_runtime = m.get("tool_runtime_ms", 0.0)
            wall_clock = m.get("wall_clock_ms", 0.0)

        agg = aggregated[agent]
        agg["runs"] += 1
        agg["total_input_tokens"] += input_tokens
        agg["total_uncached_input_tokens"] += InputTokenSplit(
            input_tokens=input_tokens, cache_read_tokens=cache_hit_tokens
        ).uncached_input_tokens
        agg["total_output_tokens"] += output_tokens
        agg["total_cost"] += cost
        agg["total_context_window"] += context_window
        agg["latency_sum"] += latency
        agg["llm_runtime_sum"] += runtime
        agg["tool_runtime_sum"] += tool_runtime
        agg["wall_clock_sum"] += wall_clock
        agg["tool_calls_sum"] += tool_calls
        agg["error_rate_sum"] += error_rate
        agg["review_fix_requests_sum"] += fix_requests
        _accumulate_provider_health(agg, tokens_per_second, time_to_first_token)
        _accumulate_cache_and_reasoning(
            agg, reasoning_tokens, cache_hit_rate, input_tokens, cache_hit_tokens
        )
        # Merge per-tool data
        agg["tool_calls_per_tool"] = _merge_per_tool_dict(
            agg["tool_calls_per_tool"], tool_calls_per_tool
        )
        agg["errors_per_tool"] = _merge_per_tool_dict(
            agg["errors_per_tool"], errors_per_tool
        )
        agg["tokens_per_tool"] = _merge_per_tool_dict(
            agg["tokens_per_tool"], tokens_per_tool
        )
        agg["tool_time_per_tool"] = _merge_per_tool_dict(
            agg["tool_time_per_tool"], tool_time_per_tool
        )

    return aggregated


def format_number(n):
    """Format a number with commas."""
    return f"{n:,}"


def format_cost(value):
    """Format a USD cost with 4 decimals (sub-cent precision)."""
    return f"${value:,.4f}"


@dataclass(frozen=True)
class InputTokenSplit:
    """One usage record's input tokens split into new (uncached) and cache-read parts.

    Mirrors the ADR-0014 token-shape guard
    (``cost.py::SessionCost._regular_input_tokens``): the litellm/OpenAI shape
    counts cache buckets *inside* ``input_tokens`` and they are subtracted; the
    Anthropic shape counts them alongside, so ``input_tokens`` is already the
    uncached figure. Reported input is therefore comparable with harnesses that
    report only newly-processed input (issue #948).
    """

    input_tokens: int = 0
    cache_read_tokens: int = 0
    # Cache-write tokens are not persisted past collection and are 0 at report time.
    cache_write_tokens: int = 0

    # @stub-protected
    @property
    def uncached_input_tokens(self) -> int:
        """New input tokens: guarded input − cache reads − cache writes; never negative."""
        if self.input_tokens >= self.cache_read_tokens + self.cache_write_tokens:
            return self.input_tokens - self.cache_read_tokens - self.cache_write_tokens
        return self.input_tokens


@dataclass(frozen=True)
class StatsTotals:
    """Grand totals over every bucket of an aggregated metrics mapping.

    Renders as the bold summary row of the agent statistics table,
    replacing the removed ``**Totals:**`` sentence (issue #942).
    """

    runs: int = 0
    # Sum of per-run uncached input (InputTokenSplit); rendered under "Input Tokens".
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    # @stub-protected
    @classmethod
    def from_aggregated(
        cls, aggregated: Mapping[str, Mapping[str, Any]]
    ) -> "StatsTotals":
        """Fold every bucket of ``aggregated`` into one totals instance.

        Input tokens fold each bucket's per-run uncached sum
        (``total_uncached_input_tokens``), never a post-hoc subtraction of the
        raw column totals (issue #948).
        """
        runs = 0
        input_tokens = 0
        output_tokens = 0
        cost_usd = 0.0
        for agg in aggregated.values():
            runs += agg.get("runs", 0)
            input_tokens += agg.get("total_uncached_input_tokens", 0)
            output_tokens += agg.get("total_output_tokens", 0)
            cost_usd += agg.get("total_cost", 0.0)
        return cls(
            runs=runs,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    # @stub-protected
    def render_row(self) -> str:
        """One 17-cell markdown row matching the agent table's fixed column layout."""
        empty = "—"
        return (
            f"| **Totals** "
            f"| {self.runs} "
            f"| {format_number(self.input_tokens)} "
            f"| {format_number(self.output_tokens)} "
            f"| {empty} "
            f"| {empty} "
            f"| {empty} "
            f"| {format_cost(self.cost_usd)} "
            f"| {empty} "
            f"| {empty} "
            f"| {empty} "
            f"| {empty} "
            f"| {empty} "
            f"| {empty} "
            f"| {empty} "
            f"| {empty} "
            f"| {empty} |"
        )


@dataclass(frozen=True)
class SecondsCell:
    """One duration cell of the agent statistics table, rendered in seconds.

    Owns the ms -> s presentation conversion (issue #947): ``workflow_state``,
    ``metrics_schema.Metrics`` and the aggregation buckets all keep
    milliseconds; only the rendered cell text is divided by 1000.
    """

    ms_value: float = 0.0

    # @stub-protected
    @classmethod
    def from_average(cls, ms_sum: float, runs: int) -> "SecondsCell":
        """Per-run mean of a millisecond sum, wrapping 0.0 ms when runs == 0."""
        return cls(ms_value=ms_sum / runs if runs else 0.0)

    # @stub-protected
    def render(self) -> str:
        """Whole seconds, no decimals: '0', '1', '29', '688'."""
        return f"{self.ms_value / 1000.0:.0f}"


def generate_agent_summary_table(aggregated):
    """Generate a markdown table from per-agent aggregated metrics."""
    if not aggregated:
        return ""

    lines = []
    lines.append("## 📊 Agent Statistics Summary")
    lines.append("")
    lines.append("| Agent | Runs | Input Tokens | Output Tokens | Cache Hit Rate | Cache Hit Tokens | Reasoning Tokens | Cost (USD) | Avg Max Context Window | Avg Latency (s) | Avg LLM Runtime (s) | Avg Tokens/sec | Avg TTFT (s) | Avg Tool Calls | Tool Errors | Fix Requests | Per-Tool Calls |")
    lines.append("|-------|------|-------------|---------------|----------------|------------------|------------------|-----------|-------------------|------------------|----------------------|----------------|--------------|----------------|-------------|--------------|----------------|")

    # Define a stable sort order for known agents
    agent_order = {"architect": 0, "context": 1, "dev": 2}

    for agent in sorted(aggregated.keys(), key=lambda a: agent_order.get(a, 99)):
        agg = aggregated[agent]
        runs = agg["runs"]
        input_tokens = agg.get("total_uncached_input_tokens", 0)
        output_tokens = agg["total_output_tokens"]
        cost = agg.get("total_cost", 0.0)
        avg_context_window = agg["total_context_window"] // runs if runs else 0
        avg_latency = SecondsCell.from_average(agg["latency_sum"], runs)
        avg_llm_runtime = SecondsCell.from_average(agg["llm_runtime_sum"], runs)
        avg_tool_calls = agg["tool_calls_sum"] / runs if runs else 0
        avg_tps = _format_provider_metric(
            agg.get("tps_sum", 0.0), agg.get("tps_count", 0), 1
        )
        avg_ttft = _format_provider_metric(
            agg.get("ttft_sum", 0.0), agg.get("ttft_count", 0), 2
        )

        # Calculate total tool errors across all runs
        total_errors = sum(agg["errors_per_tool"].values()) if agg["errors_per_tool"] else 0

        # Get total fix requests across all runs
        fix_requests = agg.get("review_fix_requests_sum", 0)

        cache_rate, cache_hit_tokens, reasoning_tokens = _bucket_cache_and_reasoning(agg)

        display_name = get_agent_display_name(agent)

        lines.append(
            f"| {display_name} "
            f"| {runs} "
            f"| {format_number(input_tokens)} "
            f"| {format_number(output_tokens)} "
            f"| {cache_rate} "
            f"| {cache_hit_tokens} "
            f"| {reasoning_tokens} "
            f"| {format_cost(cost)} "
            f"| {format_number(avg_context_window)} "
            f"| {avg_latency.render()} "
            f"| {avg_llm_runtime.render()} "
            f"| {avg_tps} "
            f"| {avg_ttft} "
            f"| {avg_tool_calls:.1f} "
            f"| {total_errors} "
            f"| {fix_requests} "
            f"| {_format_tool_dict(agg['tool_calls_per_tool'])} |"
        )

    lines.append(StatsTotals.from_aggregated(aggregated).render_row())

    return lines


def post_comment(repo, issue_number, token, body):
    """Post a comment to a GitHub issue through the shared transport."""
    result = _github_post_comment(repo, issue_number, body, token)
    if not result.ok:
        print(f"Error posting comment: {result.detail}")
        raise RuntimeError(f"Failed to post comment: {result.detail}")
    print(f"Comment posted successfully: {result.comment_url or 'URL not available'}")


def parse_iso_datetime(value):
    """Parse a GitHub ISO-8601 timestamp (e.g. '2024-01-01T00:00:00Z')."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _metric_field(metric, name, default):
    """Read a field from a Metrics object or a legacy dict."""
    if isinstance(metric, dict):
        return metric.get(name, default)
    return getattr(metric, name, default)


def build_comment_record(comment):
    """Normalise one GitHub comment into a CommentRecord (or None if unusable).

    Bot-vs-human is decided by the universal ``BOT_MARKER`` because bots post
    through the maintainer's PAT and are therefore indistinguishable by login.
    """
    body = comment.get("body") or ""
    created_at = parse_iso_datetime(comment.get("created_at"))
    if created_at is None:
        return None
    login = (comment.get("user") or {}).get("login") or ""
    return CommentRecord(
        author_login=login,
        author_is_bot=BOT_MARKER in body,
        body=body,
        created_at=created_at,
        agent_role=parse_agent_role(body),
        is_human_command=bool(HUMAN_COMMAND_REGEX.search(body)),
    )


def parse_agent_role(body):
    """Extract the agent role from the ``bot-agent-<role>`` marker, if present."""
    match = BOT_AGENT_ROLE_REGEX.search(body or "")
    return match.group(1) if match else None


def collect_issue_data(comments, all_metrics, issue_body, issue_closed_at):
    """Build the pure ``IssueData`` input from already-fetched pipeline data."""
    records = tuple(
        record
        for record in (build_comment_record(c) for c in comments)
        if record is not None
    )

    start_times = [
        start
        for start in (
            parse_iso_datetime(_metric_field(m, "start_time", None))
            for m in all_metrics
        )
        if start is not None
    ]
    first_agent_start = min(start_times) if start_times else None

    if all_metrics:
        agent_tool_invocations = sum(
            _metric_field(m, "nb_tool_calls", 0) or 0 for m in all_metrics
        )
        agent_time_spent_seconds = (
            sum(_metric_field(m, "agent_runtime_ms", 0.0) or 0.0 for m in all_metrics)
            / 1000.0
        )
    else:
        agent_tool_invocations = None
        agent_time_spent_seconds = None

    return IssueData(
        body=issue_body or "",
        comments=records,
        first_agent_start=first_agent_start,
        closed_at=issue_closed_at,
        agent_tool_invocations=agent_tool_invocations,
        agent_time_spent_seconds=agent_time_spent_seconds,
    )


def build_human_metrics_section(comments, all_metrics, issue_body, issue_closed_at):
    """Compute and render the human-metrics section for the resume.

    Reading speed (200 wpm), the empty allowlist ("any non-bot author is
    human"), and the context-agent reading-time exclusion are hardcoded
    defaults for now, per product decision; no workflow variable is wired.
    """
    config = HumanMetricsConfig()
    issue_data = collect_issue_data(comments, all_metrics, issue_body, issue_closed_at)
    metrics = HumanMetricsAnalyzer(config).analyze(issue_data)
    return HumanMetricsRenderer(config).render(metrics)


def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    issue_number = os.environ.get("ISSUE_NUMBER")

    if not token or not repo or not issue_number:
        print("Error: GITHUB_TOKEN, GITHUB_REPOSITORY, and ISSUE_NUMBER env vars are required.")
        sys.exit(1)

    print(f"Fetching comments for {repo} issue #{issue_number}...")
    comments = fetch_comments(repo, issue_number, token)
    print(f"Fetched {len(comments)} comments.")

    issue = fetch_issue(repo, issue_number, token)
    issue_body = issue.get("body") or ""
    issue_closed_at = parse_iso_datetime(issue.get("closed_at"))

    # Extract per-step metrics from every comment's workflow_state snapshot,
    # then collapse the repeats carried forward by accumulating histories.
    all_metrics = []
    for comment in comments:
        all_metrics.extend(extract_metrics_from_comment(comment.get("body", "")))
    all_metrics = deduplicate_metrics(all_metrics)

    print(f"Extracted {len(all_metrics)} metric entries.")

    human_section = build_human_metrics_section(
        comments, all_metrics, issue_body, issue_closed_at
    )

    if not all_metrics:
        summary = "No agent metrics were found for this issue."
        if human_section:
            summary = f"{summary}\n\n{human_section}"
    else:
        # Fill zero-cost entries at report time from the OpenRouter price table
        # keyed by each entry's recorded model identity (issue #928); fail-open.
        resolver = ReportCostResolver.build()
        agent_aggregated = aggregate_by_agent(all_metrics, resolver)

        sections = []
        agent_table = generate_agent_summary_table(agent_aggregated)
        if agent_table:
            sections.append("\n".join(agent_table))

        if human_section:
            sections.append(human_section)

        if sections:
            summary = "\n\n".join(sections)
            summary += "\n\n> _This comment was auto-generated by the metrics summary agent on issue close._"
        else:
            summary = "No agent metrics were found for this issue."

    print("\n--- Summary ---")
    print(summary)
    print("--- End Summary ---\n")

    post_comment(repo, issue_number, token, summary)
    print("Done.")


if __name__ == "__main__":
    main()
