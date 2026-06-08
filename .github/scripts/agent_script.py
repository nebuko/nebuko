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

Note: Provide either prompt_location argument OR PROMPT_STRING env variable, not both.

For setup instructions, usage examples, and GitHub Actions integration,
see README.md in this directory.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import urlopen

from openhands.sdk import Agent, Conversation, LLM, get_logger
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.observation import ObservationEvent
from openhands.sdk.conversation import get_agent_final_response
from openhands.tools.preset.default import get_default_condenser
from openhands.sdk.tool.spec import Tool
from metrics_schema import Metrics


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
        tokens_per_tool[tool_name] = {
            'input': getattr(usage, 'prompt_tokens', 0) or 0,
            'output': getattr(usage, 'completion_tokens', 0) or 0,
            'cache_hit': getattr(usage, 'cache_hit_tokens', 0) or 0,
            'reasoning': getattr(usage, 'reasoning_tokens', 0) or 0,
        }
    
    return tokens_per_tool


def collect_llm_metrics(conversation):
    """
    Collect LLM metrics from a conversation after it has completed.
    
    Args:
        conversation: The OpenHands Conversation object
        
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
        
        # Get token usage
        input_tokens = 0
        output_tokens = 0
        if combined_metrics.accumulated_token_usage:
            input_tokens = combined_metrics.accumulated_token_usage.prompt_tokens
            output_tokens = combined_metrics.accumulated_token_usage.completion_tokens
        
        # Compute per-tool statistics
        tool_calls_per_tool = _compute_tool_calls_per_tool(conversation)
        errors_per_tool = _compute_errors_per_tool(conversation)
        tokens_per_tool = _compute_tokens_per_tool(combined_metrics)
        
        # Get start time (first event timestamp or current time)
        start_time = datetime.now(timezone.utc).isoformat()
        if conversation.state.events:
            first_event = conversation.state.events[0]
            if hasattr(first_event, 'timestamp'):
                start_time = first_event.timestamp
        
        # Build Metrics object
        role_name = os.environ.get("ROLE_NAME", "unknown")
        metrics = Metrics(
            run_id=str(conversation.id),
            model_name=combined_metrics.model_name,
            agent_role=role_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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


def save_metrics_json(metrics):
    """
    Save metrics as JSON file in output/ directory.

    Args:
        metrics: Metrics object containing metrics data
    """
    try:
        # Create output directory if it doesn't exist
        output_dir = "output"
        os.makedirs(output_dir, exist_ok=True)

        # Generate filename
        run_id = metrics.run_id if hasattr(metrics, 'run_id') else "unknown"
        filename = f"{output_dir}/metrics-{run_id}.json"

        # Write pretty-printed JSON file
        with open(filename, "w") as f:
            if isinstance(metrics, Metrics):
                f.write(metrics.to_json(compact=False))
            else:
                json.dump(metrics, f, indent=2)

        logger.info(f"Saved LLM metrics to {filename}")

    except Exception as e:
        logger.error(f"Failed to save metrics JSON: {e}")


def split_message(message: str, max_length: int = 10000) -> list[str]:
    """
    Split a long message into parts, each under max_length.

    Splits preferentially at paragraph breaks, then line breaks,
    then sentence boundaries, falling back to hard splits.

    Args:
        message: The full message to split.
        max_length: Maximum characters per part.

    Returns:
        List of raw message parts, each <= max_length characters.
    """
    if not message or len(message) <= max_length:
        return [message] if message else []

    parts: list[str] = []
    remaining = message

    while remaining:
        if len(remaining) <= max_length:
            parts.append(remaining)
            break

        # Strategy 1: Split at paragraph boundaries (\n\n)
        part = _split_at_boundaries(remaining, max_length, "\n\n")
        if part is not None:
            parts.append(part)
            remaining = remaining[len(part):].lstrip("\n")
            continue

        # Strategy 2: Split at line boundaries (\n)
        part = _split_at_boundaries(remaining, max_length, "\n")
        if part is not None:
            parts.append(part)
            remaining = remaining[len(part):].lstrip("\n")
            continue

        # Strategy 3: Split at sentence boundaries (. ! ?)
        part = _split_at_sentence_boundaries(remaining, max_length)
        if part is not None:
            parts.append(part)
            remaining = remaining[len(part):].lstrip(" ")
            continue

        # Strategy 4: Hard split at max_length
        parts.append(remaining[:max_length])
        remaining = remaining[max_length:]

    return parts


def _split_at_boundaries(text: str, max_length: int, delimiter: str) -> str | None:
    """
    Try to split text at delimiter boundaries, keeping each part <= max_length.

    Uses greedy packing: accumulate segments until adding the next would
    exceed max_length, then emit the current part.

    Returns the first part (without the delimiter), or None if text is
    already <= max_length or splitting is not possible.
    """
    if len(text) <= max_length:
        return None

    segments = text.split(delimiter)
    current_parts: list[str] = []
    current_len = 0

    for segment in segments:
        # Calculate length if we add this segment
        separator_len = len(delimiter) if current_parts else 0
        new_len = current_len + separator_len + len(segment)

        if new_len > max_length and current_parts:
            # Current part is full, emit it
            part = delimiter.join(current_parts)
            return part
        elif new_len > max_length and not current_parts:
            # Even the first segment is too long — can't use this strategy
            return None
        else:
            current_parts.append(segment)
            current_len = new_len

    # All segments fit in one part
    return None


def _split_at_sentence_boundaries(text: str, max_length: int) -> str | None:
    """
    Try to split text at sentence boundaries (. ! ?) followed by space.

    Returns the first part (including the sentence-ending punctuation),
    or None if splitting is not possible.
    """
    if len(text) <= max_length:
        return None

    # Find the last sentence boundary before max_length
    cutoff = text[:max_length]
    # Look for ". ", "! ", or "? " patterns
    last_break = -1
    for marker in [". ", "! ", "? "]:
        pos = cutoff.rfind(marker)
        if pos > last_break:
            last_break = pos

    if last_break > max_length * 0.3:  # Only use if we're past 30% of the limit
        return cutoff[:last_break + 1]  # Include the punctuation

    return None


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

    # Configure LLM — prefer layered config file, fall back to env vars
    config_path = os.getenv("AGENT_CONFIG_PATH", "agent-config.yaml")
    role_name = os.environ.get("ROLE_NAME", "unknown")

    api_key = None
    model = None
    base_url = None
    temperature = None
    agent_cfg = None

    if os.path.isfile(config_path):
        try:
            from config import load_config

            cfg = load_config(config_path)
            agent_cfg = cfg.agents.get(role_name)
            if agent_cfg:
                model = agent_cfg.model_ref.model_path
                provider = agent_cfg.model_ref.provider_ref
                api_key = os.environ.get(provider.api_token_env)
                base_url = provider.api_url
                temperature = agent_cfg.temperature
                logger.info(
                    f"Loaded config for agent '{role_name}' from {config_path}: "
                    f"model={model}, provider={provider.api_url}"
                )
            else:
                logger.warning(
                    f"Agent role '{role_name}' not found in config; "
                    "falling back to env vars"
                )
        except Exception as e:
            logger.warning(
                f"Failed to load config from {config_path}: {e}; "
                "falling back to env vars"
            )

    # Backward-compatible fallback: read from env vars
    if api_key is None:
        api_key = os.getenv("LLM_API_KEY")
    if model is None:
        model = os.getenv("LLM_MODEL", "gpt-5.5")
    if base_url is None:
        base_url = os.getenv("LLM_BASE_URL")
    if temperature is None and os.getenv("LLM_TEMPERATURE"):
        temperature = float(os.getenv("LLM_TEMPERATURE"))

    # Per-invocation overrides from agentRunConfig (CONFIG_MODEL / CONFIG_TEMPERATURE)
    # These allow a caller to override the config file values for a single invocation.
    config_model = os.getenv("CONFIG_MODEL")
    if config_model:
        model = config_model
        logger.info(f"CONFIG_MODEL override: {model}")
    config_temperature = os.getenv("CONFIG_TEMPERATURE")
    if config_temperature:
        temperature = float(config_temperature)
        logger.info(f"CONFIG_TEMPERATURE override: {temperature}")

    if not api_key:
        logger.error("LLM_API_KEY environment variable is not set.")
        sys.exit(1)

    llm_config = {
        "model": model,
        "api_key": api_key,
        "usage_id": "agent_script",
        "drop_params": True,
    }

    if base_url:
        llm_config["base_url"] = base_url

    if temperature is not None:
        llm_config["temperature"] = temperature

    # Reasoning level support (optional — model-dependent)
    # reasoning_effort is a flat field on the OpenHands SDK LLM class.
    # On vLLM backends, setting it to "low"/"medium"/"high" automatically
    # injects enable_thinking=true into the chat template.
    llm_reasoning = os.getenv("LLM_REASONING", "none")
    if llm_reasoning and llm_reasoning != "none":
        llm_config["reasoning_effort"] = llm_reasoning
        logger.info(f"Reasoning enabled: effort={llm_reasoning}")

    # Build litellm_extra_body from LLM config thinking parameters.
    # These are passed through to vLLM/Qwen3 for thinking mode control.
    litellm_extra_body = {}
    chat_template_kwargs = {}

    if agent_cfg and agent_cfg.model_ref.thinking_enabled is not None:
        chat_template_kwargs["enable_thinking"] = agent_cfg.model_ref.thinking_enabled
    if agent_cfg and agent_cfg.model_ref.preserve_thinking is not None:
        chat_template_kwargs["preserve_thinking"] = agent_cfg.model_ref.preserve_thinking

    if chat_template_kwargs:
        litellm_extra_body["chat_template_kwargs"] = chat_template_kwargs

    # thinking_token_budget is a sampling parameter (top-level, not in chat_template_kwargs)
    if agent_cfg and agent_cfg.model_ref.thinking_token_budget is not None:
        litellm_extra_body["thinking_token_budget"] = agent_cfg.model_ref.thinking_token_budget

    if litellm_extra_body:
        llm_config["litellm_extra_body"] = litellm_extra_body
        logger.info(f"Thinking parameters: {litellm_extra_body}")

    # max_output_tokens (recommended 32768 for most queries, 81920 for complex problems)
    if agent_cfg and agent_cfg.model_ref.max_output_tokens is not None:
        llm_config["max_output_tokens"] = agent_cfg.model_ref.max_output_tokens

    llm = LLM(**llm_config)

    # Get the current working directory as workspace
    cwd = os.getcwd()

    # YAML-driven tool resolution: read tools from agent config
    # If agent_cfg is None (config not loaded or role not found), use full defaults
    default_tool_list = ["terminal", "file_editor", "task_tracker", "think", "finish"]
    agent_tools = agent_cfg.tools if agent_cfg is not None else default_tool_list

    # Separate SDK tools (terminal, file_editor, task_tracker) from built-in
    # defaults (think → ThinkTool, finish → FinishTool)
    sdk_tool_names = {"terminal", "file_editor", "task_tracker"}
    builtin_tool_map = {"think": "ThinkTool", "finish": "FinishTool"}

    sdk_tools = []
    include_default_tools = []

    for tool_name in agent_tools:
        if tool_name in sdk_tool_names:
            sdk_tools.append(Tool(name=tool_name))
        elif tool_name in builtin_tool_map:
            include_default_tools.append(builtin_tool_map[tool_name])
        else:
            logger.warning(f"Unknown tool '{tool_name}' for agent '{role_name}' — skipping")

    logger.info(
        f"Creating {role_name} agent with tools: sdk={[t.name for t in sdk_tools]}, "
        f"builtins={include_default_tools}"
    )

    # For the ctx agent, disable cli_mode to prevent the SDK from injecting
    # problem-solving instructions that conflict with ctx's read-only role.
    system_prompt_kwargs = (
        {} if role_name == "ctx" else {"cli_mode": True}
    )

    agent = Agent(
        llm=llm,
        tools=sdk_tools,
        include_default_tools=include_default_tools,
        system_prompt_kwargs=system_prompt_kwargs,
        condenser=get_default_condenser(llm=llm),
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

    # Extract and save final agent message with embedded metrics
    final_message = get_agent_final_response(conversation.state.events)

    metrics = collect_llm_metrics(conversation)
    # Count review fix requests (genius asking dev to make changes)
    review_fix_requests = final_message.count("@@dev fix this") if final_message else 0
    metrics.review_fix_requests = review_fix_requests

    # Save metrics JSON with the new field
    save_metrics_json(metrics)

    # Split long messages into multiple parts, each under the safe limit.
    # GitHub's comment API allows 65,536 chars, but we use 10,000 as a
    # comfortable limit leaving room for bot markers and metrics comments.
    MAX_MESSAGE_LENGTH = 10000
    message_parts = split_message(final_message, MAX_MESSAGE_LENGTH) if final_message else []

    if not message_parts:
        logger.warning("No final agent message found in events")
        metrics_json = metrics.to_json(compact=True)
        sentinel = f"__NO_FINAL_MESSAGE__\n<!-- agent-metrics: {metrics_json} -->"
        with open("final_message.txt", "w", encoding="utf-8") as f:
            f.write(sentinel)
        logger.info("Saved sentinel + metrics comment (no final message)")
    else:
        # Append metrics as hidden HTML comment for later aggregation on issue close
        metrics_json = metrics.to_json(compact=True)
        metrics_comment = f"\n\n<!-- agent-metrics: {metrics_json} -->"

        # Add part indicators for multi-part messages
        if len(message_parts) > 1:
            total = len(message_parts)
            for i in range(total):
                message_parts[i] = f"*(Part {i + 1}/{total})*\n\n{message_parts[i]}"

        # Write part 1 to the standard file (backward compatible)
        with open("final_message.txt", "w", encoding="utf-8") as f:
            f.write(message_parts[0])
            if len(message_parts) == 1:  # Only single-part messages get metrics here
                f.write(metrics_comment)
        logger.info(f"Saved first message part ({len(message_parts[0])} characters)")

        # Write additional parts if the message was split
        if len(message_parts) > 1:
            # Write part count for the workflow to read
            with open("final_message_parts_count.txt", "w", encoding="utf-8") as f:
                f.write(str(len(message_parts)))

            for i, part in enumerate(message_parts[1:], start=2):
                filename = f"final_message_part_{i}.txt"
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(part)
                    # Only the last part gets the metrics comment
                    if i == len(message_parts):
                        f.write(metrics_comment)
                logger.info(f"Saved message part {i} ({len(part)} characters)")

            logger.info(f"Message split into {len(message_parts)} parts (was {len(final_message)} characters)")
        else:
            logger.info(f"Final agent message saved ({len(message_parts[0])} characters, no splitting needed)")

    logger.info("Task completed successfully")


if __name__ == "__main__":
    main()
