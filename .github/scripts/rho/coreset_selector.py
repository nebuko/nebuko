#!/usr/bin/env python3
"""
Coreset selector — score trajectories by difficulty and select challenging tasks.

Reads archived trajectories, computes a weighted difficulty score for each,
and returns the top-N most challenging tasks for RHO group rollout.

Scoring dimensions (weights configurable via rho_config.yaml):
    - error_rate: Higher tool call error rate = harder task
    - review_fix_requests: More fix requests = harder task
    - tool_calls: More tool calls = harder task
    - runtime: Longer runtime = harder task
    - token_usage: Higher token consumption = harder task
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(__file__) + "/..")

from metrics_schema import Metrics
from rho.trajectory_archive import Trajectory, list_trajectories


# ── Data Models ───────────────────────────────────────────────────────

@dataclass
class ScoredTrajectory:
    """A trajectory with its computed difficulty score."""

    trajectory: Trajectory
    score: float
    component_scores: Dict[str, float]


# ── Scoring Logic ─────────────────────────────────────────────────────

def _normalize(values: List[float]) -> List[float]:
    """Min-max normalize a list of values to [0, 1]."""
    if not values:
        return []
    min_v = min(values)
    max_v = max(values)
    if max_v == min_v:
        return [0.5] * len(values)
    return [(v - min_v) / (max_v - min_v) for v in values]


def _compute_metric_for_trajectory(trajectory: Trajectory, metric: str) -> float:
    """
    Extract an aggregated metric value from a trajectory.

    Returns the average across all metric entries in the trajectory.
    """
    if not trajectory.metrics:
        return 0.0

    values = []
    for m in trajectory.metrics:
        if metric == "error_rate":
            values.append(m.tool_call_error_rate)
        elif metric == "review_fix_requests":
            values.append(m.review_fix_requests)
        elif metric == "tool_calls":
            values.append(m.nb_tool_calls)
        elif metric == "runtime":
            values.append(m.agent_runtime_ms)
        elif metric == "token_usage":
            values.append(m.input_tokens + m.output_tokens)
        else:
            values.append(0.0)

    return sum(values) / len(values) if values else 0.0


def _load_weights(rho_config_path: str) -> Dict[str, float]:
    """Load scoring weights from rho_config.yaml."""
    if not os.path.exists(rho_config_path):
        # Default weights
        return {
            "error_rate": 0.30,
            "review_fix_requests": 0.25,
            "tool_calls": 0.20,
            "runtime": 0.15,
            "token_usage": 0.10,
        }

    with open(rho_config_path) as f:
        config = yaml.safe_load(f)

    if config and "coreset" in config and "weights" in config["coreset"]:
        return config["coreset"]["weights"]
    return _load_weights("")  # Fallback to defaults


def score_trajectories(
    trajectories: List[Trajectory],
    weights: Optional[Dict[str, float]] = None,
) -> List[ScoredTrajectory]:
    """
    Score all trajectories by difficulty.

    Args:
        trajectories: List of trajectories to score.
        weights: Optional override weights (from config by default).

    Returns:
        List of ScoredTrajectory sorted by score descending.
    """
    if not trajectories:
        return []

    if weights is None:
        weights = _load_weights("")

    # Compute raw metric values
    metrics_map = {
        "error_rate": [],
        "review_fix_requests": [],
        "tool_calls": [],
        "runtime": [],
        "token_usage": [],
    }

    for traj in trajectories:
        for metric in metrics_map:
            metrics_map[metric].append(_compute_metric_for_trajectory(traj, metric))

    # Normalize each metric to [0, 1]
    normalized = {k: _normalize(v) for k, v in metrics_map.items()}

    # Compute weighted scores
    scored: list[ScoredTrajectory] = []
    for i, traj in enumerate(trajectories):
        component_scores = {k: normalized[k][i] for k in normalized}
        score = sum(component_scores[k] * weights.get(k, 0) for k in normalized)

        scored.append(ScoredTrajectory(
            trajectory=traj,
            score=score,
            component_scores=component_scores,
        ))

    # Sort by score descending (hardest first)
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored


def select_coreset(
    trajectories: List[Trajectory],
    coreset_size: int = 5,
    weights: Optional[Dict[str, float]] = None,
) -> List[ScoredTrajectory]:
    """
    Select the top-N most challenging trajectories as the coreset.

    Args:
        trajectories: Pool of candidate trajectories.
        coreset_size: Number of trajectories to select.
        weights: Optional scoring weight override.

    Returns:
        Top coreset_size ScoredTrajectory objects.
    """
    scored = score_trajectories(trajectories, weights=weights)
    return scored[:coreset_size]


def select_coreset_from_archive(
    archive_dir: str,
    coreset_size: int = 5,
    rho_config_path: Optional[str] = None,
) -> List[ScoredTrajectory]:
    """
    Load trajectories from archive and select coreset.

    Convenience function that combines loading + scoring + selection.
    """
    trajectories = list_trajectories(archive_dir)

    weights = None
    if rho_config_path:
        weights = _load_weights(rho_config_path)

    return select_coreset(trajectories, coreset_size=coreset_size, weights=weights)


def main():
    """CLI entry point for coreset selection."""
    archive_dir = os.environ.get("RHO_ARCHIVE_DIR", ".rho/trajectories")
    rho_config_path = os.environ.get("RHO_CONFIG_PATH", ".github/scripts/rho/rho_config.yaml")

    # Load config
    with open(rho_config_path) as f:
        config = yaml.safe_load(f)

    coreset_size = config.get("coreset", {}).get("size", 5)

    scored = select_coreset_from_archive(
        archive_dir=archive_dir,
        coreset_size=coreset_size,
        rho_config_path=rho_config_path,
    )

    print(f"Selected {len(scored)} trajectories for coreset:")
    for st in scored:
        traj = st.trajectory
        print(f"  #{traj.issue_number} ({st.score:.3f}): {traj.issue_title[:60]}...")
        print(f"    metrics: {len(traj.metrics)} runs")


if __name__ == "__main__":
    main()
