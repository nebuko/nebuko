#!/usr/bin/env python3
"""
Summarize agent metrics from issue comments on issue close.

Fetches all comments from a GitHub issue, extracts embedded agent metrics
from HTML comments, aggregates by model name, and posts a summary table.

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

# Import the Metrics schema
sys.path.insert(0, os.path.dirname(__file__))
from metrics_schema import Metrics

METRICS_REGEX = re.compile(r"<!--\s*agent-metrics:\s*(.*?)\s*-->")


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
        req.add_header("User-Agent", "git-flow-ai-metrics-summarizer")

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


def extract_metrics_from_comment(body):
    """Extract all metrics objects from a comment body."""
    metrics_list = []
    for match in METRICS_REGEX.finditer(body):
        try:
            metrics = Metrics.from_json(match.group(1))
            metrics_list.append(metrics)
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse metrics JSON: {e}")
    return metrics_list


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


def generate_summary_table(aggregated):
    """Generate a markdown table from aggregated metrics."""
    if not aggregated:
        return "No agent metrics were found for this issue."

    lines = []
    lines.append("## 📊 Agent Statistics Summary")
    lines.append("")
    lines.append("| Model | Runs | Input Tokens | Output Tokens | Avg Latency (ms) | Avg LLM Runtime (ms) | Avg Tool Calls | Avg Error Rate |")
    lines.append("|-------|------|-------------|---------------|------------------|----------------------|----------------|----------------|")

    grand_runs = 0
    grand_input = 0
    grand_output = 0

    for model in sorted(aggregated.keys()):
        agg = aggregated[model]
        runs = agg["runs"]
        input_tokens = agg["total_input_tokens"]
        output_tokens = agg["total_output_tokens"]
        avg_latency = agg["latency_sum"] / runs if runs else 0
        avg_llm_runtime = agg["llm_runtime_sum"] / runs if runs else 0
        avg_tool_calls = agg["tool_calls_sum"] / runs if runs else 0
        avg_error_rate = agg["error_rate_sum"] / runs if runs else 0

        lines.append(
            f"| {model} "
            f"| {runs} "
            f"| {format_number(input_tokens)} "
            f"| {format_number(output_tokens)} "
            f"| {avg_latency:.0f} "
            f"| {avg_llm_runtime:.0f} "
            f"| {avg_tool_calls:.1f} "
            f"| {avg_error_rate:.1f}% |"
        )

        grand_runs += runs
        grand_input += input_tokens
        grand_output += output_tokens

    lines.append("")
    lines.append(
        f"**Totals:** {grand_runs} run{'s' if grand_runs != 1 else ''} "
        f"· {format_number(grand_input)} input tokens "
        f"· {format_number(grand_output)} output tokens"
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
    lines.append("| Agent | Runs | Input Tokens | Output Tokens | Avg Latency (ms) | Avg LLM Runtime (ms) | Avg Tool Calls | Tool Errors | Fix Requests | Per-Tool Calls |")
    lines.append("|-------|------|-------------|---------------|------------------|----------------------|----------------|-------------|--------------|----------------|")

    grand_runs = 0
    grand_input = 0
    grand_output = 0

    # Define a stable sort order for known agents
    agent_order = {"genius": 0, "ctx": 1, "dev": 2}

    for agent in sorted(aggregated.keys(), key=lambda a: agent_order.get(a, 99)):
        agg = aggregated[agent]
        runs = agg["runs"]
        input_tokens = agg["total_input_tokens"]
        output_tokens = agg["total_output_tokens"]
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

    lines.append("")
    lines.append(
        f"**Totals:** {grand_runs} run{'s' if grand_runs != 1 else ''} "
        f"· {format_number(grand_input)} input tokens "
        f"· {format_number(grand_output)} output tokens"
    )

    return lines


def generate_model_summary_table(aggregated):
    """Generate a markdown table from per-model aggregated metrics."""
    if not aggregated:
        return ""

    lines = []
    lines.append("## 📊 Model Statistics Summary")
    lines.append("")
    lines.append("| Model | Runs | Input Tokens | Output Tokens | Avg Latency (ms) | Avg LLM Runtime (ms) | Avg Tool Calls | Tool Errors | Fix Requests | Per-Tool Calls |")
    lines.append("|-------|------|-------------|---------------|------------------|----------------------|----------------|-------------|--------------|----------------|")

    for model in sorted(aggregated.keys()):
        agg = aggregated[model]
        runs = agg["runs"]
        input_tokens = agg["total_input_tokens"]
        output_tokens = agg["total_output_tokens"]
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
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    payload = json.dumps({"body": body}).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "git-flow-ai-metrics-summarizer")

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

    # Extract all metrics from all comments
    all_metrics = []
    for comment in comments:
        body = comment.get("body", "")
        metrics = extract_metrics_from_comment(body)
        if metrics:
            all_metrics.extend(metrics)

    print(f"Extracted {len(all_metrics)} metric entries.")

    if not all_metrics:
        summary = "No agent metrics were found for this issue."
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
