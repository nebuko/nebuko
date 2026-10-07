#!/usr/bin/env python3
"""
Summarize agent metrics from issue comments on issue close.

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
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime

# Import the Metrics schema
sys.path.insert(0, os.path.dirname(__file__))
from github_client import BOT_MARKER, ensure_bot_marker
from metrics_schema import Metrics
from human_metrics import (
    CommentRecord,
    HumanMetricsAnalyzer,
    HumanMetricsConfig,
    HumanMetricsRenderer,
    IssueData,
)
from workflow_state.features.get_last_state.get_last_state import extract_state_from_body

# Agent role marker emitted at the top of every agent comment (e.g. bot-agent-dev).
BOT_AGENT_ROLE_REGEX = re.compile(r"<!--\s*bot-agent-([a-z0-9_-]+)\s*-->")
# Human workflow trigger (e.g. @@dev, @@genius) used to count human commands.
HUMAN_COMMAND_REGEX = re.compile(r"@@[a-z][a-z0-9_-]*")


def fetch_comments(repo, issue_number, token, per_page=100):
    """Fetch all comments from an issue, handling pagination."""
    all_comments = []
    page = 1

    while True:
        url = (
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
            f"?per_page={per_page}&page={page}"
        )
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "nebuko-metrics-summarizer")

        try:
            with urllib.request.urlopen(req) as response:
                comments = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"HTTP error fetching comments (page {page}): {e.code} - {e.reason}")
            break
        except Exception as e:
            print(f"Error fetching comments (page {page}): {e}")
            break

        if not comments:
            break

        all_comments.extend(comments)
        page += 1

    return all_comments


def fetch_issue(repo, issue_number, token):
    """Fetch a single issue's body and metadata (body, closed_at)."""
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "nebuko-metrics-summarizer")

    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTP error fetching issue: {e.code} - {e.reason}")
    except Exception as e:
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
    if not base:
        return override or {}
    if not override:
        return base
    
    merged = dict(base)
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
            merged[tool] = values
    
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


def aggregate_by_model(all_metrics):
    """Aggregate metrics by model_name."""
    aggregated = defaultdict(lambda: {
        "runs": 0,
        "total_input_tokens": 0,
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
    })

    for m in all_metrics:
        # Support both Metrics objects and legacy dicts
        if isinstance(m, Metrics):
            model = m.model_name or "unknown"
            input_tokens = m.input_tokens
            output_tokens = m.output_tokens
            cost = m.cost
            context_window = m.max_context_window
            latency = m.tool_call_latency_ms
            runtime = m.agent_runtime_ms
            tool_calls = m.nb_tool_calls
            error_rate = m.tool_call_error_rate
            fix_requests = m.review_fix_requests
            tool_calls_per_tool = m.tool_calls_per_tool
            errors_per_tool = m.errors_per_tool
            tokens_per_tool = m.tokens_per_tool
        else:
            # Legacy dict support
            model = m.get("model_name", "unknown")
            input_tokens = m.get("input_tokens", 0)
            output_tokens = m.get("output_tokens", 0)
            cost = m.get("cost", 0.0)
            context_window = m.get("max_context_window", 0)
            latency = m.get("tool_call_latency_ms", 0.0)
            runtime = m.get("agent_runtime_ms", 0.0)
            tool_calls = m.get("nb_tool_calls", 0)
            error_rate = m.get("tool_call_error_rate", 0.0)
            fix_requests = m.get("review_fix_requests", 0)
            tool_calls_per_tool = m.get("tool_calls_per_tool", {})
            errors_per_tool = m.get("errors_per_tool", {})
            tokens_per_tool = m.get("tokens_per_tool", {})

        agg = aggregated[model]
        agg["runs"] += 1
        agg["total_input_tokens"] += input_tokens
        agg["total_output_tokens"] += output_tokens
        agg["total_cost"] += cost
        agg["total_context_window"] += context_window
        agg["latency_sum"] += latency
        agg["llm_runtime_sum"] += runtime
        agg["tool_calls_sum"] += tool_calls
        agg["error_rate_sum"] += error_rate
        agg["review_fix_requests_sum"] += fix_requests
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
    "genius": "Genius",
    "ctx": "Ctx",
    "dev": "Dev",
}


def get_agent_display_name(agent_role):
    """Get display name for an agent role."""
    return AGENT_DISPLAY_NAMES.get(agent_role, agent_role.title())


def aggregate_by_agent(all_metrics):
    """Aggregate metrics by agent_role."""
    aggregated = defaultdict(lambda: {
        "runs": 0,
        "total_input_tokens": 0,
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
    })

    for m in all_metrics:
        # Support both Metrics objects and legacy dicts
        if isinstance(m, Metrics):
            agent = m.agent_role or "unknown"
            input_tokens = m.input_tokens
            output_tokens = m.output_tokens
            cost = m.cost
            context_window = m.max_context_window
            latency = m.tool_call_latency_ms
            runtime = m.agent_runtime_ms
            tool_calls = m.nb_tool_calls
            error_rate = m.tool_call_error_rate
            fix_requests = m.review_fix_requests
            tool_calls_per_tool = m.tool_calls_per_tool
            errors_per_tool = m.errors_per_tool
            tokens_per_tool = m.tokens_per_tool
        else:
            # Legacy dict support
            agent = m.get("agent_role", "unknown")
            input_tokens = m.get("input_tokens", 0)
            output_tokens = m.get("output_tokens", 0)
            cost = m.get("cost", 0.0)
            context_window = m.get("max_context_window", 0)
            latency = m.get("tool_call_latency_ms", 0.0)
            runtime = m.get("agent_runtime_ms", 0.0)
            tool_calls = m.get("nb_tool_calls", 0)
            error_rate = m.get("tool_call_error_rate", 0.0)
            fix_requests = m.get("review_fix_requests", 0)
            tool_calls_per_tool = m.get("tool_calls_per_tool", {})
            errors_per_tool = m.get("errors_per_tool", {})
            tokens_per_tool = m.get("tokens_per_tool", {})

        agg = aggregated[agent]
        agg["runs"] += 1
        agg["total_input_tokens"] += input_tokens
        agg["total_output_tokens"] += output_tokens
        agg["total_cost"] += cost
        agg["total_context_window"] += context_window
        agg["latency_sum"] += latency
        agg["llm_runtime_sum"] += runtime
        agg["tool_calls_sum"] += tool_calls
        agg["error_rate_sum"] += error_rate
        agg["review_fix_requests_sum"] += fix_requests
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


def format_number(n):
    """Format a number with commas."""
    return f"{n:,}"


def format_cost(value):
    """Format a USD cost with 4 decimals (sub-cent precision)."""
    return f"${value:,.4f}"


def generate_summary_table(aggregated):
    """Generate a markdown table from aggregated metrics."""
    if not aggregated:
        return "No agent metrics were found for this issue."

    lines = []
    lines.append("## 📊 Agent Statistics Summary")
    lines.append("")
    lines.append("| Model | Runs | Input Tokens | Output Tokens | Cost (USD) | Avg Max Context Window | Avg Latency (ms) | Avg LLM Runtime (ms) | Avg Tool Calls | Avg Error Rate |")
    lines.append("|-------|------|-------------|---------------|-----------|-------------------|------------------|----------------------|----------------|----------------|")

    grand_runs = 0
    grand_input = 0
    grand_output = 0
    grand_cost = 0.0

    for model in sorted(aggregated.keys()):
        agg = aggregated[model]
        runs = agg["runs"]
        input_tokens = agg["total_input_tokens"]
        output_tokens = agg["total_output_tokens"]
        cost = agg.get("total_cost", 0.0)
        avg_context_window = agg.get("total_context_window", 0) // runs if runs else 0
        avg_latency = agg["latency_sum"] / runs if runs else 0
        avg_llm_runtime = agg["llm_runtime_sum"] / runs if runs else 0
        avg_tool_calls = agg["tool_calls_sum"] / runs if runs else 0
        avg_error_rate = agg["error_rate_sum"] / runs if runs else 0

        lines.append(
            f"| {model} "
            f"| {runs} "
            f"| {format_number(input_tokens)} "
            f"| {format_number(output_tokens)} "
            f"| {format_cost(cost)} "
            f"| {format_number(avg_context_window)} "
            f"| {avg_latency:.0f} "
            f"| {avg_llm_runtime:.0f} "
            f"| {avg_tool_calls:.1f} "
            f"| {avg_error_rate:.1f}% |"
        )

        grand_runs += runs
        grand_input += input_tokens
        grand_output += output_tokens
        grand_cost += cost

    lines.append("")
    lines.append(
        f"**Totals:** {grand_runs} run{'s' if grand_runs != 1 else ''} "
        f"· {format_number(grand_input)} input tokens "
        f"· {format_number(grand_output)} output tokens "
        f"· {format_cost(grand_cost)}"
    )
    lines.append("")
    lines.append(
        "> _This comment was auto-generated by the metrics summary agent on issue close._"
    )

    return "\n".join(lines)


def generate_agent_summary_table(aggregated):
    """Generate a markdown table from per-agent aggregated metrics."""
    if not aggregated:
        return ""

    lines = []
    lines.append("## 📊 Agent Statistics Summary")
    lines.append("")
    lines.append("| Agent | Runs | Input Tokens | Output Tokens | Cost (USD) | Avg Max Context Window | Avg Latency (ms) | Avg LLM Runtime (ms) | Avg Tool Calls | Tool Errors | Fix Requests | Per-Tool Calls |")
    lines.append("|-------|------|-------------|---------------|-----------|-------------------|------------------|----------------------|----------------|-------------|--------------|----------------|")

    grand_runs = 0
    grand_input = 0
    grand_output = 0
    grand_cost = 0.0

    # Define a stable sort order for known agents
    agent_order = {"genius": 0, "ctx": 1, "dev": 2}

    for agent in sorted(aggregated.keys(), key=lambda a: agent_order.get(a, 99)):
        agg = aggregated[agent]
        runs = agg["runs"]
        input_tokens = agg["total_input_tokens"]
        output_tokens = agg["total_output_tokens"]
        cost = agg.get("total_cost", 0.0)
        avg_context_window = agg["total_context_window"] // runs if runs else 0
        avg_latency = agg["latency_sum"] / runs if runs else 0
        avg_llm_runtime = agg["llm_runtime_sum"] / runs if runs else 0
        avg_tool_calls = agg["tool_calls_sum"] / runs if runs else 0

        # Calculate total tool errors across all runs
        total_errors = sum(agg["errors_per_tool"].values()) if agg["errors_per_tool"] else 0

        # Get total fix requests across all runs
        fix_requests = agg.get("review_fix_requests_sum", 0)

        display_name = get_agent_display_name(agent)

        lines.append(
            f"| {display_name} "
            f"| {runs} "
            f"| {format_number(input_tokens)} "
            f"| {format_number(output_tokens)} "
            f"| {format_cost(cost)} "
            f"| {format_number(avg_context_window)} "
            f"| {avg_latency:.0f} "
            f"| {avg_llm_runtime:.0f} "
            f"| {avg_tool_calls:.1f} "
            f"| {total_errors} "
            f"| {fix_requests} "
            f"| {_format_tool_dict(agg['tool_calls_per_tool'])} |"
        )

        grand_runs += runs
        grand_input += input_tokens
        grand_output += output_tokens
        grand_cost += cost

    lines.append("")
    lines.append(
        f"**Totals:** {grand_runs} run{'s' if grand_runs != 1 else ''} "
        f"· {format_number(grand_input)} input tokens "
        f"· {format_number(grand_output)} output tokens "
        f"· {format_cost(grand_cost)}"
    )

    return lines


def generate_model_summary_table(aggregated):
    """Generate a markdown table from per-model aggregated metrics."""
    if not aggregated:
        return ""

    lines = []
    lines.append("## 📊 Model Statistics Summary")
    lines.append("")
    lines.append("| Model | Runs | Input Tokens | Output Tokens | Cost (USD) | Avg Max Context Window | Avg Latency (ms) | Avg LLM Runtime (ms) | Avg Tool Calls | Tool Errors | Fix Requests | Per-Tool Calls |")
    lines.append("|-------|------|-------------|---------------|-----------|-------------------|------------------|----------------------|----------------|-------------|--------------|----------------|")

    for model in sorted(aggregated.keys()):
        agg = aggregated[model]
        runs = agg["runs"]
        input_tokens = agg["total_input_tokens"]
        output_tokens = agg["total_output_tokens"]
        cost = agg.get("total_cost", 0.0)
        avg_context_window = agg["total_context_window"] // runs if runs else 0
        avg_latency = agg["latency_sum"] / runs if runs else 0
        avg_llm_runtime = agg["llm_runtime_sum"] / runs if runs else 0
        avg_tool_calls = agg["tool_calls_sum"] / runs if runs else 0

        # Calculate total tool errors across all runs
        total_errors = sum(agg["errors_per_tool"].values()) if agg["errors_per_tool"] else 0

        # Get total fix requests across all runs
        fix_requests = agg.get("review_fix_requests_sum", 0)

        lines.append(
            f"| {model} "
            f"| {runs} "
            f"| {format_number(input_tokens)} "
            f"| {format_number(output_tokens)} "
            f"| {format_cost(cost)} "
            f"| {format_number(avg_context_window)} "
            f"| {avg_latency:.0f} "
            f"| {avg_llm_runtime:.0f} "
            f"| {avg_tool_calls:.1f} "
            f"| {total_errors} "
            f"| {fix_requests} "
            f"| {_format_tool_dict(agg['tool_calls_per_tool'])} |"
        )

    return lines


def post_comment(repo, issue_number, token, body):
    """Post a comment to a GitHub issue."""
    body = ensure_bot_marker(body)
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    payload = json.dumps({"body": body}).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "nebuko-metrics-summarizer")

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            print(f"Comment posted successfully: {result.get('html_url', 'URL not available')}")
    except urllib.error.HTTPError as e:
        print(f"HTTP error posting comment: {e.code} - {e.reason}")
        raise
    except Exception as e:
        print(f"Error posting comment: {e}")
        raise


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

    Reading speed (200 wpm) and the empty allowlist ("any non-bot author is
    human") are hardcoded defaults for now, per product decision; no workflow
    variable is wired.
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
        agent_aggregated = aggregate_by_agent(all_metrics)
        model_aggregated = aggregate_by_model(all_metrics)

        sections = []
        agent_table = generate_agent_summary_table(agent_aggregated)
        if agent_table:
            sections.append("\n".join(agent_table))

        model_table = generate_model_summary_table(model_aggregated)
        if model_table:
            sections.append("\n".join(model_table))

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
