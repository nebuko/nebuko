#!/usr/bin/env python3
"""Slim entry point — orchestrates the agent_script features via the facade."""

import argparse
import json
import os
import sys

from openhands.sdk import get_logger
from nebuko.engine.lib.agent_script import (
    load_prompt,
    collect_and_save_metrics,
    resolve_llm_config,
    run_agent,
    build_output,
    load_prev_state_or_recover,
)
from nebuko.engine.lib.agent_script.features.run_agent.run_failure import ProviderFailure
from nebuko.engine.lib.pricing.features.load_price_table.load_price_table import load_price_table

logger = get_logger(__name__)

#: Exit status of a run that stopped on a provider failure (issue #1322). The
#: legacy seam maps it back to ``ProviderFailure`` instead of a bare
#: ``RuntimeError``, so the engine can report the real cause.
PROVIDER_FAILURE_EXIT_CODE = 3
PROVIDER_FAILURE_FILE = "provider_failure.json"


def _record_provider_failure(error: ProviderFailure) -> None:
    """Stage the provider failure for the parent seam, then exit non-zero."""
    payload = {
        "provider_error": error.provider_error,
        "provider": error.provider,
        "model": error.model,
        "agent_phase": error.agent_phase,
        "requires_config_fix": error.requires_config_fix,
    }
    try:
        with open(PROVIDER_FAILURE_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except OSError as exc:  # pragma: no cover - the stderr message still carries it
        logger.warning(f"could not stage the provider failure: {exc}")
    logger.error(f"Provider failure: {error.provider_error}")
    sys.exit(PROVIDER_FAILURE_EXIT_CODE)


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
    prev_state = load_prev_state_or_recover()

    # 5. Run agent
    try:
        conversation = run_agent(
            llm_config, prompt, args, agent_cfg=agent_cfg, prev_state=prev_state
        )
    except ProviderFailure as error:
        # A provider failure is terminal (issue #1322): stop before the metrics
        # and output steps, which would otherwise synthesize a wrong cause.
        _record_provider_failure(error)

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
        prev_state=prev_state,
    )

    logger.info("Task completed successfully")


if __name__ == "__main__":
    main()
