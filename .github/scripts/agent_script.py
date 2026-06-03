#!/usr/bin/env python3
"""
Example: Task Runner

This script runs OpenHands agent for an arbitrary task. It accepts a
prompt either as a string or from a file/URL and executes the task.
Designed for use with GitHub Actions workflows.

Usage:
    python agent_script.py [prompt_location]

Arguments:
    prompt_location: (Optional) Path to a local file or URL containing the prompt
                     If not provided, PROMPT_STRING env variable must be set

Environment Variables:
    PROMPT_STRING: Direct prompt text (alternative to prompt_location)
    LLM_API_KEY: API key for the LLM (required)
    LLM_MODEL: Language model to use (default: gpt-5.5)
    LLM_BASE_URL: Optional base URL for LLM API
    LLM_TEMPERATURE: Optional sampling temperature (0.0 = deterministic, default: provider default)
    LLM_SEED: Optional seed for reproducible outputs (default: not set)

Note: Provide either prompt_location argument OR PROMPT_STRING env variable, not both.

For setup instructions, usage examples, and GitHub Actions integration,
see README.md in this directory.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import urlopen

from openhands.sdk import LLM, Conversation, get_logger
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.observation import ObservationEvent
from openhands.sdk.conversation import get_agent_final_response
from openhands.tools.preset.default import get_default_agent


logger = get_logger(__name__)


def is_url(path: str) -> bool:
    """Check if the given path is a URL."""
    try:
        result = urlparse(path)
        return all([result.scheme, result.netloc])
    except Exception:
        return False


def load_prompt(prompt_location: str) -> str:
    """
    Load prompt from a file or URL.

    Args:
        prompt_location: Path to a local file or URL containing the prompt

    Returns:
        The prompt content as a string

    Raises:
        ValueError: If the prompt cannot be loaded
    """
    try:
        if is_url(prompt_location):
            logger.info(f"Downloading prompt from URL: {prompt_location}")
            with urlopen(prompt_location) as response:
                return response.read().decode("utf-8")
        else:
            logger.info(f"Loading prompt from file: {prompt_location}")
            with open(prompt_location) as f:
                return f.read()
    except Exception as e:
        raise ValueError(f"Failed to load prompt from {prompt_location}: {e}")


def _compute_tool_calls_per_tool(agent_tool_calls):
    """Count tool calls grouped by tool_name."""
    counts = {}
    for event in agent_tool_calls:
        tool_name = getattr(event, "tool_name", None)
        if tool_name:
            counts[tool_name] = counts.get(tool_name, 0) + 1
    return counts


def _compute_errors_per_tool(observation_events):
    """Count tool call errors grouped by tool_name using observation.is_error."""
    errors = {}
    for event in observation_events:
        tool_name = getattr(event, "tool_name", None)
        if not tool_name:
            continue
        obs = getattr(event, "observation", None)
        is_error = getattr(obs, "is_error", False) if obs else False
        if is_error:
            errors[tool_name] = errors.get(tool_name, 0) + 1
    return errors


def _compute_tokens_per_tool(agent_tool_calls, combined_metrics):
    """
    Distribute token usage per tool call.
    
    A single LLM response can trigger multiple parallel tool calls.
    Token costs are evenly divided among tools in the same response.
    """
    # Guard against None/empty token_usages
    if not combined_metrics.token_usages:
        return {}

    # Build lookup: response_id -> TokenUsage
    token_by_response = {}
    for tu in combined_metrics.token_usages:
        rid = getattr(tu, "response_id", None)
        if rid:
            token_by_response[rid] = tu

    # Group tool calls by llm_response_id
    calls_by_response = defaultdict(list)
    for event in agent_tool_calls:
        rid = getattr(event, "llm_response_id", None)
        if rid:
            calls_by_response[rid].append(event)

    # Initialize per-tool token accumulators
    tokens_per_tool = {}

    for rid, tu in token_by_response.items():
        calls = calls_by_response.get(rid, [])
        if not calls:
            continue
        # Divide tokens evenly among calls in this response
        n = len(calls)
        input_tok = getattr(tu, "prompt_tokens", 0) / n
        output_tok = getattr(tu, "completion_tokens", 0) / n
        cache_hit = getattr(tu, "cache_read_tokens", 0) / n
        reasoning = getattr(tu, "reasoning_tokens", 0) / n

        for event in calls:
            tool_name = getattr(event, "tool_name", None)
            if not tool_name:
                continue
            if tool_name not in tokens_per_tool:
                tokens_per_tool[tool_name] = {
                    "input": 0,
                    "output": 0,
                    "cache_hit": 0,
                    "reasoning": 0,
                }
            tokens_per_tool[tool_name]["input"] += input_tok
            tokens_per_tool[tool_name]["output"] += output_tok
            tokens_per_tool[tool_name]["cache_hit"] += cache_hit
            tokens_per_tool[tool_name]["reasoning"] += reasoning

    # Round to integers for cleaner output
    for tool_name in tokens_per_tool:
        for key in tokens_per_tool[tool_name]:
            tokens_per_tool[tool_name][key] = round(tokens_per_tool[tool_name][key])

    return tokens_per_tool


def collect_llm_metrics(conversation):
    """
    Collect LLM metrics from a conversation after it has completed.
    
    Args:
        conversation: The OpenHands Conversation object
        
    Returns:
        dict: Dictionary containing all collected metrics
    """
    try:
        # Get combined metrics across all LLMs
        combined_metrics = conversation.conversation_stats.get_combined_metrics()
        
        # Count tool calls
        action_events = [e for e in conversation.state.events if isinstance(e, ActionEvent)]
        agent_tool_calls = [e for e in action_events if e.source == "agent"]
        nb_tool_calls = len(agent_tool_calls)
        
        # Count tool call errors (fixed: use observation.is_error instead of exit_code)
        observation_events = [e for e in conversation.state.events if isinstance(e, ObservationEvent)]
        total_observations = len(observation_events)
        error_observations = len([
            e for e in observation_events
            if getattr(getattr(e, "observation", None), "is_error", False)
        ])
        
        # Calculate error rate (avoid division by zero)
        tool_call_error_rate = 0.0
        if total_observations > 0:
            tool_call_error_rate = (error_observations / total_observations) * 100
        
        # Calculate average latency (latency is in seconds, convert to milliseconds)
        tool_call_latency_ms = 0.0
        if combined_metrics.response_latencies:
            total_latency = sum(lat.latency for lat in combined_metrics.response_latencies)
            # Convert from seconds to milliseconds
            tool_call_latency_ms = (total_latency / len(combined_metrics.response_latencies)) * 1000
        
        # Get token usage
        input_tokens = 0
        output_tokens = 0
        if combined_metrics.accumulated_token_usage:
            input_tokens = combined_metrics.accumulated_token_usage.prompt_tokens
            output_tokens = combined_metrics.accumulated_token_usage.completion_tokens
        
        # Per-tool statistics
        tool_calls_per_tool = _compute_tool_calls_per_tool(agent_tool_calls)
        errors_per_tool = _compute_errors_per_tool(observation_events)
        tokens_per_tool = _compute_tokens_per_tool(agent_tool_calls, combined_metrics)
        
        # Get start time (first event timestamp or current time)
        start_time = datetime.now(timezone.utc).isoformat()
        if conversation.state.events:
            first_event = conversation.state.events[0]
            if hasattr(first_event, 'timestamp'):
                start_time = first_event.timestamp
        
        # Build metrics dictionary
        role_name = os.environ.get("ROLE_NAME", "unknown")
        metrics = {
            "run_id": str(conversation.id),
            "model_name": combined_metrics.model_name,
            "agent_role": role_name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "complexity": os.environ.get("COMPLEXITY", "N/A"),
            "tool_call_latency_ms": tool_call_latency_ms,
            "nb_tool_calls": nb_tool_calls,
            "tool_calls_per_tool": tool_calls_per_tool,
            "errors_per_tool": errors_per_tool,
            "tokens_per_tool": tokens_per_tool,
            "tool_call_error_rate": tool_call_error_rate,
            "start_time": start_time,
            "end_time": datetime.now(timezone.utc).isoformat()
        }
        
        logger.info(f"Collected LLM metrics for run {metrics['run_id']}")
        return metrics
        
    except Exception as e:
        logger.error(f"Failed to collect LLM metrics: {e}")
        # Return minimal metrics on error
        return {
            "run_id": str(conversation.id) if conversation else "unknown",
            "model_name": "unknown",
            "agent_role": os.environ.get("ROLE_NAME", "unknown"),
            "input_tokens": 0,
            "output_tokens": 0,
            "complexity": os.environ.get("COMPLEXITY", "N/A"),
            "tool_call_latency_ms": 0.0,
            "nb_tool_calls": 0,
            "tool_calls_per_tool": {},
            "errors_per_tool": {},
            "tokens_per_tool": {},
            "tool_call_error_rate": 0.0,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": datetime.now(timezone.utc).isoformat(),
            "error": str(e)
        }


def save_metrics_json(metrics_dict):
    """
    Save metrics dictionary as JSON file in output/ directory.
    
    Args:
        metrics_dict: Dictionary containing metrics data
    """
    try:
        # Create output directory if it doesn't exist
        output_dir = "output"
        os.makedirs(output_dir, exist_ok=True)
        
        # Generate filename
        run_id = metrics_dict.get("run_id", "unknown")
        filename = f"{output_dir}/metrics-{run_id}.json"
        
        # Write JSON file
        with open(filename, "w") as f:
            json.dump(metrics_dict, f, indent=2)
        
        logger.info(f"Saved LLM metrics to {filename}")
        
    except Exception as e:
        logger.error(f"Failed to save metrics JSON: {e}")


def main():
    """Run the task with the provided prompt."""
    parser = argparse.ArgumentParser(
        description="Run OpenHands agent for arbitrary tasks"
    )
    parser.add_argument(
        "prompt_location",
        nargs="?",
        help=(
            "Path to a local file or URL containing the prompt "
            "(optional if PROMPT_STRING is set)"
        ),
    )
    args = parser.parse_args()

    # Get prompt from either location or string
    prompt_string = os.getenv("PROMPT_STRING")
    prompt_location = args.prompt_location

    # Validate that exactly one is provided
    if prompt_string and prompt_location:
        logger.error(
            "Error: Both PROMPT_STRING and prompt_location provided. "
            "Please provide only one."
        )
        sys.exit(1)

    if not prompt_string and not prompt_location:
        logger.error(
            "Error: Neither PROMPT_STRING nor prompt_location provided. "
            "Please provide one."
        )
        sys.exit(1)

    # Load the prompt
    try:
        if prompt_string:
            logger.info("Using prompt from PROMPT_STRING environment variable")
            prompt = prompt_string
        else:
            prompt = load_prompt(prompt_location)
        logger.info(f"Loaded prompt ({len(prompt)} characters)")
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    # Configure LLM
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        logger.error("LLM_API_KEY environment variable is not set.")
        sys.exit(1)

    model = os.getenv("LLM_MODEL", "gpt-5.5")
    base_url = os.getenv("LLM_BASE_URL")

    llm_config = {
        "model": model,
        "api_key": api_key,
        "usage_id": "agent_script",
        "drop_params": True,
    }

    if base_url:
        llm_config["base_url"] = base_url

    if os.getenv("LLM_TEMPERATURE"):
        llm_config["temperature"] = float(os.getenv("LLM_TEMPERATURE"))
    if os.getenv("LLM_SEED"):
        llm_config["seed"] = int(os.getenv("LLM_SEED"))

    llm = LLM(**llm_config)

    # Get the current working directory as workspace
    cwd = os.getcwd()

    # Create agent with default tools
    agent = get_default_agent(
        llm=llm,
        cli_mode=True,
    )

    # Create conversation
    conversation = Conversation(
        agent=agent,
        workspace=cwd,
    )

    logger.info("Starting task execution...")
    logger.info(f"Prompt: {prompt[:200]}...")

    # Send the prompt and run the agent
    conversation.send_message(prompt)
    conversation.run()

    # Collect and save LLM metrics
    metrics = collect_llm_metrics(conversation)
    save_metrics_json(metrics)

    # Extract and save final agent message with embedded metrics
    final_message = get_agent_final_response(conversation.state.events)
    
    # Smart truncation: enforce a hard limit at the Python level to prevent
    # GitHub comment API failures (65,536 char limit). We use 10,000 chars
    # as a safe limit, leaving room for bot markers and metrics comments.
    MAX_MESSAGE_LENGTH = 10000
    if final_message and len(final_message) > MAX_MESSAGE_LENGTH:
        original_len = len(final_message)
        # Truncate at paragraph boundary for readability
        truncated = final_message[:MAX_MESSAGE_LENGTH]
        # Try to cut at the last paragraph break rather than mid-sentence
        last_paragraph_break = truncated.rfind("\n\n")
        if last_paragraph_break > MAX_MESSAGE_LENGTH * 0.5:
            truncated = truncated[:last_paragraph_break]
        final_message = truncated + "\n\n... *(truncated due to length)*"
        logger.warning(f"Final message truncated from {original_len} to {len(final_message)} characters")
    
    if final_message:
        # Append metrics as hidden HTML comment for later aggregation on issue close
        metrics_json = json.dumps(metrics, separators=(",", ":"))
        metrics_comment = f"\n\n<!-- agent-metrics: {metrics_json} -->"
        with open("final_message.txt", "w", encoding="utf-8") as f:
            f.write(final_message)
            f.write(metrics_comment)
        logger.info(f"Saved final agent message ({len(final_message)} characters) with embedded metrics")
    else:
        logger.warning("No final agent message found in events")
        # Mark that there's no final message so the workflow can handle the fallback properly
        # Write a sentinel + metrics comment
        metrics_json = json.dumps(metrics, separators=(",", ":"))
        sentinel = f"__NO_FINAL_MESSAGE__\n<!-- agent-metrics: {metrics_json} -->"
        with open("final_message.txt", "w", encoding="utf-8") as f:
            f.write(sentinel)
        logger.info("Saved sentinel + metrics comment (no final message)")

    logger.info("Task completed successfully")


if __name__ == "__main__":
    main()
