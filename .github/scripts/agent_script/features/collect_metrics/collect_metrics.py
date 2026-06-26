"""Collect metrics feature — combines metrics collection with JSON persistence."""

import json
import os

from openhands.sdk import get_logger

from metrics_schema import Metrics
from agent_script.sub_domains.metrics.metrics_collector import collect_llm_metrics

logger = get_logger(__name__)


def save_metrics_json(metrics: Metrics) -> None:
    """
    Save metrics as JSON file in output/ directory.

    Args:
        metrics: Metrics object containing metrics data
    """
    try:
        output_dir = "output"
        os.makedirs(output_dir, exist_ok=True)

        run_id = metrics.run_id if hasattr(metrics, "run_id") else "unknown"
        filename = f"{output_dir}/metrics-{run_id}.json"

        with open(filename, "w") as f:
            if isinstance(metrics, Metrics):
                f.write(metrics.to_json(compact=False))
            else:
                json.dump(metrics, f, indent=2)

        logger.info(f"Saved LLM metrics to {filename}")

    except Exception as e:
        logger.error(f"Failed to save metrics JSON: {e}")


def collect_and_save_metrics(conversation) -> Metrics:
    """
    Collect LLM metrics from a conversation and persist them as JSON.

    Entry point that orchestrates both the pure collection logic
    (from sub-domain) and the I/O operation (save to disk).

    Args:
        conversation: The OpenHands Conversation object

    Returns:
        Metrics: The collected metrics object
    """
    metrics = collect_llm_metrics(conversation)
    save_metrics_json(metrics)
    return metrics
