"""Collect metrics feature — combines metrics collection with JSON persistence."""

import json
import os

from openhands.sdk import get_logger

from nebuko.engine.lib.metrics_schema import Metrics
from nebuko.engine.lib.agent_script.sub_domains.metrics.metrics_collector import collect_llm_metrics
from nebuko.engine.lib.pricing.sub_domains.price_table.price_table import PriceTable

logger = get_logger(__name__)


#: Subdirectory of ``LOG_DIR`` the staged copy goes to. Mirrors
#: ``nebuko.engine.artifacts.METRICS_DIR_NAME``, which bundles it into the
#: ``metrics.jsonl`` run artifact.
STAGED_METRICS_DIR = "metrics"


def _write_metrics(metrics: Metrics, filename: str) -> None:
    with open(filename, "w") as f:
        if isinstance(metrics, Metrics):
            f.write(metrics.to_json(compact=False))
        else:
            json.dump(metrics, f, indent=2)


def save_metrics_json(metrics: Metrics) -> None:
    """
    Save metrics as JSON file in output/ directory.

    The agent runs in a throwaway cwd, so ``output/`` is deleted with it. When
    ``LOG_DIR`` is set (the host's staging dir) a second copy is staged under
    ``$LOG_DIR/metrics/`` so it can be uploaded as a run artifact.

    Args:
        metrics: Metrics object containing metrics data
    """
    run_id = metrics.run_id if hasattr(metrics, "run_id") else "unknown"
    try:
        output_dir = "output"
        os.makedirs(output_dir, exist_ok=True)
        filename = f"{output_dir}/metrics-{run_id}.json"
        _write_metrics(metrics, filename)
        logger.info(f"Saved LLM metrics to {filename}")
    except Exception as e:
        logger.error(f"Failed to save metrics JSON: {e}")

    log_dir = os.environ.get("LOG_DIR")
    if not log_dir:
        return
    try:
        staged_dir = os.path.join(log_dir, STAGED_METRICS_DIR)
        os.makedirs(staged_dir, exist_ok=True)
        _write_metrics(metrics, f"{staged_dir}/metrics-{run_id}.json")
    except Exception as e:
        logger.error(f"Failed to stage metrics JSON for upload: {e}")


# @stub-protected: signature defined by architect — do not modify
def collect_and_save_metrics(
    conversation, agent_cfg=None, price_table: "PriceTable | None" = None
) -> Metrics:
    """
    Collect LLM metrics from a conversation and persist them as JSON.

    Entry point that orchestrates both the pure collection logic
    (from sub-domain) and the I/O operation (save to disk).

    Args:
        conversation: The OpenHands Conversation object
        agent_cfg: Optional AgentConfig supplying prices for the cost fallback.
        price_table: Optional default price table filling unset config prices.

    Returns:
        Metrics: The collected metrics object
    """
    metrics = collect_llm_metrics(conversation, agent_cfg, price_table)
    run_outcome = getattr(conversation, "run_outcome", None)
    if run_outcome is not None and hasattr(run_outcome, "dispatch_recall_count"):
        metrics.dispatch_recall_count = int(run_outcome.dispatch_recall_count or 0)
    save_metrics_json(metrics)
    return metrics
