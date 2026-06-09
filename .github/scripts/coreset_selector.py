#!/usr/bin/env python3
"""
Coreset Selector — select challenging past tasks for RHO rollout.

Reads trajectory archives, scores each task by difficulty (error rate,
fix requests, tool call count, runtime, tokens), and selects a diverse
subset of the most challenging tasks.

Difficulty scoring uses configurable weights from rho_config.yaml.

Usage:
    from coreset_selector import select_coreset, load_rho_config

    config = load_rho_config()
    coreset = select_coreset(archive_dir=".rho/trajectories", config=config)
"""

import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from trajectory_archive import Trajectory, load_archive


DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "rho_config.yaml")


def load_rho_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load RHO configuration from YAML file.

    Args:
        config_path: Path to rho_config.yaml. Defaults to scripts directory.

    Returns:
        Dict with RHO configuration.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    if not os.path.isfile(path):
        return get_default_config()
    with open(path) as f:
        return yaml.safe_load(f)


def get_default_config() -> Dict[str, Any]:
    """Return default RHO configuration."""
    return {
        "coreset": {
            "size": 5,
            "min_issues": 10,
            "max_issues": 100,
            "difficulty_weights": {
                "error_rate": 0.30,
                "review_fix_requests": 0.25,
                "tool_call_count": 0.20,
                "runtime": 0.15,
                "token_count": 0.10,
            },
        },
        "variants": {"num_variants": 3},
        "selection": {
            "min_improvement_percent": 5.0,
            "min_metrics_improved": 2,
        },
    }


@dataclass
class DifficultyScore:
    """Per-trajectory difficulty score with normalized component breakdown."""

    issue_number: int
    total_score: float
    error_rate_norm: float = 0.0
    fix_requests_norm: float = 0.0
    tool_calls_norm: float = 0.0
    runtime_norm: float = 0.0
    token_norm: float = 0.0


def normalize(values: List[float]) -> List[float]:
    """Min-max normalize a list of values to [0, 1].

    If all values are identical, return [0.0] for all.
    """
    if not values:
        return []
    min_val = min(values)
    max_val = max(values)
    if max_val == min_val:
        return [0.0] * len(values)
    return [(v - min_val) / (max_val - min_val) for v in values]


def compute_difficulty_scores(trajectories: List[Trajectory],
                               weights: Dict[str, float]) -> List[DifficultyScore]:
    """Compute weighted difficulty scores for all trajectories.

    Args:
        trajectories: List of Trajectory objects.
        weights: Dict mapping metric name to weight (e.g. error_rate: 0.3).

    Returns:
        List of DifficultyScore sorted by total_score descending.
    """
    # Filter to trajectories that have metrics
    valid = [t for t in trajectories if t.metrics]
    if not valid:
        return []

    # Extract raw metric vectors
    error_rates = [t.avg_error_rate for t in valid]
    fix_requests = [float(t.total_fix_requests) for t in valid]
    tool_calls = [float(t.total_tool_calls) for t in valid]
    runtimes = [t.avg_runtime_ms for t in valid]
    token_counts = [float(t.total_tokens) for t in valid]

    # Normalize each metric to [0, 1]
    err_norm = normalize(error_rates)
    fix_norm = normalize(fix_requests)
    tool_norm = normalize(tool_calls)
    runtime_norm = normalize(runtimes)
    token_norm = normalize(token_counts)

    # Weighted combination
    w = weights
    scores = []
    for i, traj in enumerate(valid):
        total = (
            w.get("error_rate", 0) * err_norm[i]
            + w.get("review_fix_requests", 0) * fix_norm[i]
            + w.get("tool_call_count", 0) * tool_norm[i]
            + w.get("runtime", 0) * runtime_norm[i]
            + w.get("token_count", 0) * token_norm[i]
        )
        scores.append(DifficultyScore(
            issue_number=traj.issue_number,
            total_score=round(total, 4),
            error_rate_norm=round(err_norm[i], 4),
            fix_requests_norm=round(fix_norm[i], 4),
            tool_calls_norm=round(tool_norm[i], 4),
            runtime_norm=round(runtime_norm[i], 4),
            token_norm=round(token_norm[i], 4),
        ))

    # Sort by total score descending (most challenging first)
    scores.sort(key=lambda s: s.total_score, reverse=True)
    return scores


def select_coreset(archive_dir: str = ".rho/trajectories",
                   config_path: Optional[str] = None,
                   coreset_size: Optional[int] = None) -> List[Trajectory]:
    """Select a coreset of challenging trajectories from archive.

    Args:
        archive_dir: Path to trajectory archive directory.
        config_path: Optional path to rho_config.yaml.
        coreset_size: Override coreset size from config.

    Returns:
        List of Trajectory objects forming the coreset.
    """
    config = load_rho_config(config_path)
    coreset_cfg = config.get("coreset", {})

    size = coreset_size or coreset_cfg.get("size", 5)
    weights = coreset_cfg.get("difficulty_weights", {
        "error_rate": 0.30,
        "review_fix_requests": 0.25,
        "tool_call_count": 0.20,
        "runtime": 0.15,
        "token_count": 0.10,
    })

    trajectories = load_archive(archive_dir)

    if not trajectories:
        print("Warning: No trajectories found in archive.")
        return []

    scores = compute_difficulty_scores(trajectories, weights)

    if not scores:
        print("Warning: No trajectories with metrics found.")
        return []

    # Select top-N by difficulty
    selected_numbers = {s.issue_number for s in scores[:size]}
    coreset = [t for t in trajectories if t.issue_number in selected_numbers]

    # Preserve difficulty order
    number_order = {n: i for i, n in enumerate(selected_numbers)}
    coreset.sort(key=lambda t: number_order.get(t.issue_number, 999))

    print(f"Selected coreset of {len(coreset)} tasks from {len(trajectories)} trajectories.")
    for i, (score, traj) in enumerate(zip(scores[:size], coreset)):
        print(f"  {i + 1}. Issue #{traj.issue_number}: score={score.total_score:.3f} "
              f"(err={score.error_rate_norm:.2f} fix={score.fix_requests_norm:.2f} "
              f"tool={score.tool_calls_norm:.2f} time={score.runtime_norm:.2f})")

    return coreset


def main():
    """CLI entry point for coreset selection."""
    config_path = os.environ.get("RHO_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    archive_dir = os.environ.get("RHO_ARCHIVE_DIR", ".rho/trajectories")
    size = int(os.environ.get("RHO_CORESET_SIZE", "0")) or None

    coreset = select_coreset(archive_dir=archive_dir, config_path=config_path, coreset_size=size)

    # Output JSON summary for downstream workflow steps
    summary = {
        "coreset_size": len(coreset),
        "tasks": [
            {
                "issue_number": t.issue_number,
                "title": t.issue_title,
                "metrics_count": len(t.metrics),
                "avg_error_rate": round(t.avg_error_rate, 2),
                "total_fix_requests": t.total_fix_requests,
            }
            for t in coreset
        ],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
