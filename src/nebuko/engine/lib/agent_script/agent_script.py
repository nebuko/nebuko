#!/usr/bin/env python3
"""Slim entry point — orchestrates the agent_script features via the facade."""

import argparse
import os
import sys

from openhands.sdk import get_logger
from agent_script import (
    load_prompt,
    collect_and_save_metrics,
    resolve_llm_config,
    run_agent,
    build_output,
    load_prev_state_or_recover,
)
from pricing.features.load_price_table.load_price_table import load_price_table

logger = get_logger(__name__)


def main():
    """Run the task with the provided prompt."""
    # Apply debug mode if requested — must happen before logger initialization
    # for downstream logging (LITELLM_LOG, Python root logger).
    config_debug = os.environ.get("CONFIG_DEBUG", "")
    if config_debug.lower() == "true":
        os.environ["LITELLM_LOG"] = "DEBUG"
        os.environ["DEBUG"] = "1"

    # 1. Parse args
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

    # 2. Resolve prompt source
    prompt_string = os.getenv("PROMPT_STRING")
    prompt_location = args.prompt_location

    if prompt_string and prompt_location:
        logger.error("Error: Both PROMPT_STRING and prompt_location provided.")
        sys.exit(1)
    if not prompt_string and not prompt_location:
        logger.error("Error: Neither PROMPT_STRING nor prompt_location provided.")
        sys.exit(1)

    try:
        prompt = prompt_string if prompt_string else load_prompt(prompt_location)
        logger.info(f"Loaded prompt ({len(prompt)} characters)")
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    # 3. The agent's static system prompt is installed in run_agent (issue #943);
    #    the role prompt below travels as the first *user* message.

    # 4. Resolve LLM config
    llm_config, prompt, agent_cfg = resolve_llm_config(args, prompt)

    # 5. Run agent
    conversation = run_agent(llm_config, prompt, args, agent_cfg=agent_cfg)

    # 6. Collect metrics — the default price table prices models the config
    #    leaves unpriced (subscription providers); load is fail-open (#714).
    metrics = collect_and_save_metrics(
        conversation, agent_cfg, price_table=load_price_table()
    )

    # 7. Build output
    #    Recover the previous state from issue comments when the per-run disk
    #    hand-off is missing (issue #671, anomaly B of #648); disk-only reads
    #    stay inside build_output so the builder remains unit-testable.
    output = build_output(
        conversation,
        metrics,
        args,
        prev_state=load_prev_state_or_recover(),
    )

    logger.info("Task completed successfully")


if __name__ == "__main__":
    main()
