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
from typing import Any, Dict, List, Mapping, Optional, Tuple

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
        "estimated_cost_sum": 0.0,
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
        # Per-1M USD prices of the estimated runs only (issue #1048); sets so a
        # multi-model agent lists each distinct price once.
        "input_prices": set(),
        "output_prices": set(),
        "cache_read_prices": set(),
        # Per-1M prices keyed by the recorded model identity; the price block
        # is keyed by model so the user sees which model's price they tune.
        "prices_by_model": {},
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
        if resolver is not None and resolver.is_estimated(m):
            agg["estimated_cost_sum"] += cost
            price = resolver.model_price(m)
            if price is not None:
                _accumulate_prices(agg, price)
                agg["prices_by_model"][resolver.model_key(m)] = price
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


def format_price(value):
    """Format a USD-per-1M-tokens model price (mirrors ``format_cost``)."""
    return f"${value:,.4f}"


def _accumulate_prices(agg, price):
    """Record a resolved ``ModelPrice``'s three estimation tiers on a bucket.

    Only the tiers the report-time math consumes are kept: cache-write tokens
    are never priced at report time, so the all-in-one input default for
    ``cache_write_price`` would be noise. The cache-read tier uses the price's
    effective rate (input fallback), like the cost math does — a table entry
    without a reported cache-read price still bills cache hits.
    """
    for key, value in (
        ("input_prices", price.input_price),
        ("output_prices", price.output_price),
        ("cache_read_prices", price.effective_cache_read_price()),
    ):
        if value is not None:
            agg[key].add(float(value))


#: Appended to a cost cell priced from the price table rather than the provider.
ESTIMATE_MARKER = "*"
#: Cell of the pricing rows for an agent whose cost was not estimated.
NO_PRICE_CELL = "—"
#: Rendered under the table whenever at least one cost cell carries the marker.
ESTIMATE_FOOTNOTE = (
    "\\* estimated from the price table; the provider reports no cost. "
    "Prices shown in USD per 1M tokens."
)
#: The title of the collapsible block holding diagnostic (non-summary) metrics.
DETAIL_SUMMARY = "Per-tool breakdown & provider health"
#: The title of the collapsible block holding the prices behind an estimate.
PRICE_SECTION_SUMMARY = "Price details used for the estimation"


def _format_minutes(ms_value):
    """Milliseconds as minutes with one decimal: '0.0 min', '41.0 min'."""
    return f"{ms_value / 60000.0:.1f} min"


def _format_error_rate(errors, tool_calls):
    """Errors as a share of tool calls: '3%'. Zero calls render '0%'."""
    if not tool_calls:
        return "0%"
    return f"{errors / tool_calls * 100:.0f}%"


def _format_share(share):
    """A [0, 1] fraction of a row total as '29%'."""
    return f"{share * 100:.0f}%"


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

    Supplies the ``Total`` column of the transposed agent statistics table
    (issue #1019), replacing the removed 17-cell ``render_row`` summary
    (issue #942). ``as_bucket`` exposes the totals in the aggregation-bucket
    shape so the Total column reuses the per-agent cell formatters verbatim.
    """

    runs: int = 0
    # Sum of per-run uncached input (InputTokenSplit); rendered under "Input / output tokens".
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    # #888 weighted-mean machinery: rate = cache_weighted_sum / cache_weight.
    cache_weighted_sum: float = 0.0
    cache_weight: float = 0.0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    # Price-table-priced share of cost_usd; drives the "*" estimate marker.
    estimated_cost_usd: float = 0.0
    llm_runtime_ms: float = 0.0
    tool_calls: int = 0
    tool_errors: int = 0
    latency_sum: float = 0.0
    tps_sum: float = 0.0
    tps_count: int = 0
    ttft_sum: float = 0.0
    ttft_count: int = 0

    # @stub-protected
    @classmethod
    def from_aggregated(
        cls, aggregated: Mapping[str, Mapping[str, Any]]
    ) -> "StatsTotals":
        """Fold every bucket of ``aggregated`` into one totals instance.

        Input tokens fold each bucket's per-run uncached sum
        (``total_uncached_input_tokens``), never a post-hoc subtraction of the
        raw column totals (issue #948). Tool errors fold each bucket's
        per-tool error counts.
        """
        runs = 0
        input_tokens = 0
        output_tokens = 0
        cache_hit_tokens = 0
        cache_weighted_sum = 0.0
        cache_weight = 0.0
        reasoning_tokens = 0
        cost_usd = 0.0
        estimated_cost_usd = 0.0
        llm_runtime_ms = 0.0
        tool_calls = 0
        tool_errors = 0
        latency_sum = 0.0
        tps_sum = 0.0
        tps_count = 0
        ttft_sum = 0.0
        ttft_count = 0
        for agg in aggregated.values():
            runs += agg.get("runs", 0)
            input_tokens += agg.get("total_uncached_input_tokens", 0)
            output_tokens += agg.get("total_output_tokens", 0)
            cache_hit_tokens += agg.get("total_cache_hit_tokens", 0)
            cache_weighted_sum += agg.get("cache_weighted_sum", 0.0)
            cache_weight += agg.get("cache_weight", 0)
            reasoning_tokens += agg.get("total_reasoning_tokens", 0)
            cost_usd += agg.get("total_cost", 0.0)
            estimated_cost_usd += agg.get("estimated_cost_sum", 0.0)
            llm_runtime_ms += agg.get("llm_runtime_sum", 0.0)
            tool_calls += agg.get("tool_calls_sum", 0)
            tool_errors += sum((agg.get("errors_per_tool") or {}).values())
            latency_sum += agg.get("latency_sum", 0.0)
            tps_sum += agg.get("tps_sum", 0.0)
            tps_count += agg.get("tps_count", 0)
            ttft_sum += agg.get("ttft_sum", 0.0)
            ttft_count += agg.get("ttft_count", 0)
        return cls(
            runs=runs,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_weighted_sum=cache_weighted_sum,
            cache_weight=cache_weight,
            reasoning_tokens=reasoning_tokens,
            cost_usd=cost_usd,
            estimated_cost_usd=estimated_cost_usd,
            llm_runtime_ms=llm_runtime_ms,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            latency_sum=latency_sum,
            tps_sum=tps_sum,
            tps_count=tps_count,
            ttft_sum=ttft_sum,
            ttft_count=ttft_count,
        )

    # @stub-protected
    def as_bucket(self) -> Dict[str, Any]:
        """Bucket-keyed view of the totals, for reuse of the per-agent formatters."""
        return {
            "runs": self.runs,
            "total_uncached_input_tokens": self.input_tokens,
            "total_output_tokens": self.output_tokens,
            "total_cache_hit_tokens": self.cache_hit_tokens,
            "cache_weighted_sum": self.cache_weighted_sum,
            "cache_weight": self.cache_weight,
            "total_reasoning_tokens": self.reasoning_tokens,
            "total_cost": self.cost_usd,
            "estimated_cost_sum": self.estimated_cost_usd,
            "llm_runtime_sum": self.llm_runtime_ms,
            "tool_calls_sum": self.tool_calls,
            "tool_errors_sum": self.tool_errors,
            "latency_sum": self.latency_sum,
            "tps_sum": self.tps_sum,
            "tps_count": self.tps_count,
            "ttft_sum": self.ttft_sum,
            "ttft_count": self.ttft_count,
            # The price rows are per-agent only: the Total cell is always "—".
            "input_prices": set(),
            "output_prices": set(),
            "cache_read_prices": set(),
        }

    # @stub-protected
    def render_headline(self) -> str:
        """One-line summary above the table, e.g.
        ``Total: 7 runs · 41.0 min LLM time · 78 tool calls (3% failed) · $1.2340* estimated``.
        """
        minutes = _format_minutes(self.llm_runtime_ms)
        parts = [f"Total: {self.runs} runs", f"{minutes} LLM time"]
        if self.tool_calls:
            failed = f" ({_format_error_rate(self.tool_errors, self.tool_calls)} failed)"
            parts.append(f"{format_number(self.tool_calls)} tool calls{failed}")
        else:
            parts.append(f"{format_number(self.tool_calls)} tool calls")
        suffix = f"{ESTIMATE_MARKER} estimated" if self.estimated_cost_usd else ""
        parts.append(f"{format_cost(self.cost_usd)}{suffix}")
        return " · ".join(parts)


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


@dataclass(frozen=True)
class ShareCell:
    """One table cell: an absolute value, optionally with its share of the row total."""

    value_text: str
    #: Fraction of the row total (0..1); ``None`` omits the "(NN%)" suffix.
    share: Optional[float] = None
    #: True when the amount includes price-table-estimated money → marker "*".
    estimated: bool = False

    # @stub-protected
    def render(self) -> str:
        """'$0.4000 (33%)*' | '12.0 min (29%)' | '14'."""
        text = self.value_text
        if self.share is not None:
            text = f"{text} ({_format_share(self.share)})"
        if self.estimated:
            text = f"{text}{ESTIMATE_MARKER}"
        return text


@dataclass(frozen=True)
class PriceCell:
    """Agent-level cell of the pricing rows: the per-1M USD price(s) the estimate used.

    ``prices`` is empty when the agent has no price-table-estimated run; the
    cell then renders :data:`NO_PRICE_CELL` instead of implying a price
    (issue #1048). Values are sorted and paired with their model names by
    :func:`_columns_for_prices`.
    """

    prices: Tuple[float, ...] = ()

    # @stub-protected
    def render(self) -> str:
        """'$0.3000' | '$0.3000 / $0.5000' (sorted) | '—'."""
        if not self.prices:
            return NO_PRICE_CELL
        return " / ".join(format_price(value) for value in sorted(self.prices))


@dataclass(frozen=True)
class ModelPriceRow:
    """One pricing row keyed by the model a price was resolved for.

    The estimation is controlled per model, so the price block names the model
    rather than the agent role that used it (issue #1048): two roles on the
    same model collapse into one column, and a multi-model role spans two.
    """

    label: str
    #: One rendered cell per model column, aligned with the header's columns.
    cells: Tuple[str, ...]

    # @stub-protected
    def render(self) -> str:
        """'| Input price ($/1M) | $0.3000 | $0.5000 |'."""
        return "| " + self.label + " |" + "".join(f" {cell} |" for cell in self.cells)


@dataclass(frozen=True)
class MetricRow:
    """One transposed row: metric label, the Total cell, one rendered cell per agent.

    The Total leads the agent columns — it is the most valuable cell to read
    (issue #1019 follow-up); ``render`` bolds it.
    """

    label: str
    #: The Total-column cell.
    total: str
    #: One rendered cell per agent column, in column order.
    cells: Tuple[str, ...]

    # @stub-protected
    def render(self) -> str:
        """'| LLM time | **41.0 min** | 12.0 min (29%) | … |'."""
        body = "".join(f" {cell} |" for cell in self.cells)
        return f"| {self.label} | **{self.total}** |{body}"


def _summary_share(value, total):
    """``value / total`` as a 0..1 fraction, or ``None`` when there is no total."""
    if not total:
        return None
    return value / total


def _build_summary_row(aggregated, totals, columns, label, cell_builder):
    """One summary row: ``cell_builder(bucket)`` per agent plus the totals bucket.

    The Total cell drops the share (it *is* the row total) and the estimate
    marker (the marker belongs to the per-agent cells, whose costs it labels).
    It leads the agent cells in ``MetricRow.render``.
    """
    cells = tuple(cell_builder(aggregated[column]) for column in columns)
    return MetricRow(
        label=label,
        cells=cells,
        total=_strip_share_and_marker(cell_builder(totals.as_bucket())),
    )


def _strip_share_and_marker(cell_text):
    """Drop a trailing '(NN%)' and '*' from a Total-column cell."""
    return re.sub(r" \(\d+%\)$", "", cell_text.rstrip("*"))


def _summary_rows(aggregated, totals, columns):
    """The seven summary rows, plus the pricing rows when a cost is estimated."""

    def runs(b):
        return str(b.get("runs", 0))

    def llm_time(b):
        return ShareCell(
            value_text=_format_minutes(b.get("llm_runtime_sum", 0.0)),
            share=_summary_share(b.get("llm_runtime_sum", 0.0), totals.llm_runtime_ms),
        ).render()

    def cost(b):
        return ShareCell(
            value_text=format_cost(b.get("total_cost", 0.0)),
            share=_summary_share(b.get("total_cost", 0.0), totals.cost_usd),
            estimated=bool(b.get("estimated_cost_sum", 0.0)),
        ).render()

    def tokens(b):
        return (
            f"{format_number(b.get('total_uncached_input_tokens', 0))} / "
            f"{format_number(b.get('total_output_tokens', 0))}"
        )

    def cache(b):
        rate, cache_hit_tokens, _ = _bucket_cache_and_reasoning(b)
        if rate == "n/a":
            return cache_hit_tokens
        return f"{cache_hit_tokens} ({rate.rstrip('%')}%)"

    def tool_calls(b):
        return format_number(b.get("tool_calls_sum", 0))

    def error_rate(b):
        # Per-agent buckets carry the per-tool breakdown; the totals bucket only
        # exposes the folded count under "tool_errors_sum".
        errors = b.get("tool_errors_sum")
        if errors is None:
            errors = sum((b.get("errors_per_tool") or {}).values())
        return _format_error_rate(errors, b.get("tool_calls_sum", 0))

    rows = [
        _build_summary_row(aggregated, totals, columns, "Runs", runs),
        _build_summary_row(aggregated, totals, columns, "LLM time", llm_time),
        _build_summary_row(aggregated, totals, columns, "Cost", cost),
    ]
    rows.extend(
        (
            _build_summary_row(
                aggregated, totals, columns, "Input / output tokens", tokens
            ),
            _build_summary_row(aggregated, totals, columns, "Cache hit tokens", cache),
            _build_summary_row(aggregated, totals, columns, "Tool calls", tool_calls),
            _build_summary_row(
                aggregated, totals, columns, "Tool error rate", error_rate
            ),
        )
    )
    return tuple(rows)


#: (``ModelPrice`` attribute, row label) of the pricing rows, in display order.
PRICE_ROWS = (
    ("input_price", "Input price ($/1M)"),
    ("output_price", "Output price ($/1M)"),
    ("cache_read_price", "Cache hit price ($/1M)"),
)


def _price_models(aggregated):
    """Every model an estimate was priced with, sorted for a stable header.

    Unions the buckets of all agents: the block's columns are models, not
    roles, because the price a user tunes is configured per model (issue #1048).
    """
    return tuple(
        sorted(
            {
                model
                for bucket in aggregated.values()
                for model in (bucket.get("prices_by_model") or {})
            }
        )
    )


def _price_rows(aggregated, models):
    """One row per estimation tier: the per-1M USD price of each model.

    Lives in the folded pricing block rather than the summary table so the
    estimate can still be audited (issue #1048). A model is listed only when
    some agent was estimated with it; the block has no Total column because
    prices are per model, not additive.
    """
    priced = {
        model: price
        for bucket in aggregated.values()
        for model, price in (bucket.get("prices_by_model") or {}).items()
    }
    rows = []
    for key, label in PRICE_ROWS:
        cells = []
        for model in models:
            price = priced.get(model)
            value = None
            if price is not None:
                # The cache-hit row shows the effective rate, as the estimate bills it.
                value = (
                    price.effective_cache_read_price()
                    if key == "cache_read_price"
                    else getattr(price, key)
                )
            prices = () if value is None else (float(value),)
            cells.append(PriceCell(prices=prices).render())
        rows.append(ModelPriceRow(label=label, cells=tuple(cells)))
    return rows


def _detail_rows(aggregated, totals, columns):
    """Diagnostic rows kept out of the summary table (provider health, per-tool)."""
    rows = []

    def average_ms(key):
        def cell(b):
            return SecondsCell.from_average(b.get(key, 0.0), b.get("runs", 0)).render()

        return cell

    def provider_metric(key, count_key, decimals):
        def cell(b):
            return _format_provider_metric(b.get(key, 0.0), b.get(count_key, 0), decimals)

        return cell

    rows.append(
        _build_summary_row(
            aggregated, totals, columns, "Avg LLM call latency (s)", average_ms("latency_sum")
        )
    )
    rows.append(
        _build_summary_row(
            aggregated, totals, columns, "Avg tokens/sec", provider_metric("tps_sum", "tps_count", 1)
        )
    )
    rows.append(
        _build_summary_row(
            aggregated, totals, columns, "Avg TTFT (s)", provider_metric("ttft_sum", "ttft_count", 2)
        )
    )
    rows.append(
        _build_summary_row(
            aggregated,
            totals,
            columns,
            "Reasoning tokens",
            lambda b: format_number(b.get("total_reasoning_tokens", 0)),
        )
    )

    def per_tool_cell(tool):
        def cell(b):
            calls = (b.get("tool_calls_per_tool") or {}).get(tool, 0)
            errors = (b.get("errors_per_tool") or {}).get(tool, 0)
            if not calls:
                return "—"
            if errors:
                return f"{format_number(calls)} ({format_number(errors)} err)"
            return format_number(calls)

        return cell

    tool_names = sorted(
        {tool for agg in aggregated.values() for tool in (agg.get("tool_calls_per_tool") or {})}
    )
    for tool in tool_names:
        calls = sum(
            (agg.get("tool_calls_per_tool") or {}).get(tool, 0)
            for agg in aggregated.values()
        )
        errors = sum(
            (agg.get("errors_per_tool") or {}).get(tool, 0)
            for agg in aggregated.values()
        )
        if errors:
            total = f"{format_number(calls)} ({format_number(errors)} err)"
        else:
            total = format_number(calls)
        rows.append(
            MetricRow(
                label=f"  ↳ {tool}",
                cells=tuple(per_tool_cell(tool)(aggregated[column]) for column in columns),
                total=total,
            )
        )
    if not tool_names:
        rows.append(
            MetricRow(
                label="Per-tool calls",
                total="N/A",
                cells=tuple("N/A" for _ in columns),
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class AgentSummaryTable:
    """Transposed agent statistics table: metrics as rows, Total + agents as columns."""

    #: Agent display names in sort order (architect, context, dev, …).
    columns: Tuple[str, ...]
    headline: str
    #: The seven summary rows.
    rows: Tuple[MetricRow, ...]
    #: The ``*`` footnote, when at least one cost cell is starred.
    footnote: str = ""

    # @stub-protected
    @classmethod
    def from_aggregated(
        cls, aggregated: Mapping[str, Mapping[str, Any]], totals: "StatsTotals"
    ) -> "AgentSummaryTable":
        """Build headline and rows; shares are computed against totals."""
        # Cell order must follow the sorted header order, not the caller's
        # (insertion-ordered) mapping.
        column_keys = tuple(sorted(aggregated.keys(), key=_agent_sort_key))
        columns = tuple(get_agent_display_name(agent) for agent in column_keys)
        rows = _summary_rows(aggregated, totals, column_keys)
        return cls(
            columns=columns,
            headline=totals.render_headline(),
            rows=rows,
            footnote=ESTIMATE_FOOTNOTE if _has_estimate(rows) else "",
        )

    # @stub-protected
    def render(self) -> List[str]:
        """Heading, headline and the summary table."""
        header = "| | **Total** | " + " | ".join(self.columns) + " |"
        separator = "|" + "---:|" * (len(self.columns) + 2)
        lines = [
            "## 📊 Agent Statistics Summary",
            "",
            self.headline,
            "",
            header,
            separator,
        ]
        lines.extend(row.render() for row in self.rows)
        return lines


def _has_estimate(rows) -> bool:
    """Whether any rendered cell carries :data:`ESTIMATE_MARKER`."""
    return any(ESTIMATE_MARKER in cell for row in rows for cell in row.cells)


@dataclass(frozen=True)
class PriceSection:
    """Folded ``<details>`` block of the per-1M prices behind an estimated cost.

    The footnote is the block's caption, so the ``*`` marker points at the
    prices it explains (issue #1048). Renders nothing when no cost is estimated.
    Columns are the model identities the estimates were priced with, not the
    agent roles: the per-1M price is a model setting, so the model name is what
    a user needs to control the estimation.
    """

    columns: Tuple[str, ...]
    rows: Tuple[ModelPriceRow, ...]
    footnote: str = ESTIMATE_FOOTNOTE

    # @stub-protected
    @classmethod
    def from_aggregated(
        cls, aggregated: Mapping[str, Mapping[str, Any]], totals: "StatsTotals"
    ) -> "PriceSection":
        """Price rows per priced model; omitted when no cost cell is starred."""
        column_keys = tuple(sorted(aggregated.keys(), key=_agent_sort_key))
        summary_rows = _summary_rows(aggregated, totals, column_keys)
        models = _price_models(aggregated) if _has_estimate(summary_rows) else ()
        rows = _price_rows(aggregated, models) if models else ()
        return cls(columns=models, rows=tuple(rows))

    @property
    def is_empty(self) -> bool:
        return not self.rows

    # @stub-protected
    def render(self) -> List[str]:
        """'<details><summary>…</summary>' + price table + footnote + '</details>'."""
        if self.is_empty:
            return []
        header = "| | " + " | ".join(self.columns) + " |"
        separator = "|---:" * (len(self.columns) + 1) + "|"
        lines = ["", "<details>", f"<summary>{PRICE_SECTION_SUMMARY}</summary>", "", header, separator]
        lines.extend(row.render() for row in self.rows)
        lines.extend(["", f"> _{self.footnote}_", "", "</details>"])
        return lines


@dataclass(frozen=True)
class DetailSection:
    """Collapsible ``<details>`` block of extra metric rows under the summary table."""

    summary: str
    columns: Tuple[str, ...]
    rows: Tuple[MetricRow, ...]

    # @stub-protected
    @classmethod
    def from_aggregated(
        cls, aggregated: Mapping[str, Mapping[str, Any]], totals: "StatsTotals"
    ) -> "DetailSection":
        """Per-tool rows (calls, `calls (N err)` when errors exist) and provider health."""
        column_keys = tuple(sorted(aggregated.keys(), key=_agent_sort_key))
        columns = tuple(get_agent_display_name(agent) for agent in column_keys)
        return cls(
            summary=DETAIL_SUMMARY,
            columns=columns,
            rows=_detail_rows(aggregated, totals, column_keys),
        )

    # @stub-protected
    def render(self) -> List[str]:
        """'<details><summary>…</summary>' + transposed mini-table + '</details>'."""
        header = "| | **Total** | " + " | ".join(self.columns) + " |"
        separator = "|" + "---:|" * (len(self.columns) + 2)
        lines = ["", "<details>", f"<summary>{self.summary}</summary>", "", header, separator]
        lines.extend(row.render() for row in self.rows)
        lines.extend(["", "</details>"])
        return lines


#: Stable sort order for known agents; unknown roles sort last, alphabetically.
AGENT_SORT_ORDER = {"architect": 0, "context": 1, "dev": 2}


def _agent_sort_key(agent_role):
    """Sort key placing the known agents first, then unknown roles by name."""
    return (AGENT_SORT_ORDER.get(agent_role, 99), agent_role)


def generate_agent_summary_table(aggregated):
    """Render the issue-close agent statistics section.

    Returns the whole section as markdown lines: headline, the transposed
    table (metrics as rows, agents as columns plus a ``Total`` column), the
    conditional folded price block (issue #1048) and a collapsible detail
    block (issue #1019).
    """
    if not aggregated:
        return ""

    totals = StatsTotals.from_aggregated(aggregated)
    lines = AgentSummaryTable.from_aggregated(aggregated, totals).render()
    lines.extend(PriceSection.from_aggregated(aggregated, totals).render())
    lines.extend(DetailSection.from_aggregated(aggregated, totals).render())
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
