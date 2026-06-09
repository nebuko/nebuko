#!/usr/bin/env python3
"""
Coreset Selector — score and rank historical trajectories by difficulty.

Reads archived trajectories, computes normalized difficulty scores using
configurable weights, and selects a diverse subset of the most challenging
tasks for RHO group rollout.

Difficulty dimensions (each normalized to [0, 1]):
    - error_rate: Higher tool call error rates indicate harder tasks.
    - review_fix_requests: More fix requests mean the solution was less satisfactory.
    - tool_calls: Excessive tool calls may signal a confusing or poorly guided task.
    - runtime: Longer execution time correlates with task complexity.
    - tokens: High token consumption indicates verbose or iterative resolution.

Usage:
    python coreset_selector.py [--coreset-size N] [--config path/to/rho_config.yaml]

Environment Variables:
    None (reads from archived trajectory files locally).
"""

import os
import sys
from typing import Any, Dict, List, Tuple

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from trajectory_archive import load_archived_trajectories

DEFAULT_CONFIG = "rho_config.yaml"


def load_rho_config(config_path: str) -> Dict[str, Any]:
    """Load RHO configuration from YAML file.

    Args:
        config_path: Path to rho_config.yaml.

    Returns:
        Configuration dictionary.
    """
    with open(config_path) as f:
        return yaml.safe_load(f)


def normalize(value: float, min_val: float, max_val: float) -> float:
    """Normalize a value to [0, 1] range. Returns 0 if min == max.

    Args:
        value: The raw value to normalize.
        min_val: Minimum of the population.
        max_val: Maximum of the population.

    Returns:
        Normalized value in [0, 1].
    """
    if max_val == min_val:
        return 0.0
    return (value - min_val) / (max_val - min_val)


def compute_dimension_values(trajectories: List[Dict[str, Any]]) -> Dict[str, List[float]]:
    """Extract raw difficulty dimension values from trajectories.

    Args:
        trajectories: List of trajectory dictionaries.

    Returns:
        Dict mapping dimension name to list of raw values (one per trajectory).
    """
    error_rates: List[float] = []
    fix_requests: List[float] = []
    tool_calls: List[float] = []
    runtimes: List[float] = []
    tokens: List[float] = []

    for traj in trajectories:
        qd = traj.get("quick_difficulty", {})
        error_rates.append(qd.get("max_error_rate", 0.0))
        fix_requests.append(qd.get("total_fix_requests", 0))
        tool_calls.append(qd.get("total_tool_calls", 0))
        runtimes.append(qd.get("avg_runtime_ms", 0.0))

        # Sum total tokens across all metrics in the trajectory
        total_tokens = sum(m.get("input_tokens", 0) + m.get("output_tokens", 0)
                           for m in traj.get("metrics", []))
        tokens.append(total_tokens)

    return {
        "error_rate": error_rates,
        "review_fix_requests": fix_requests,
        "tool_calls": tool_calls,
        "runtime": runtimes,
        "tokens": tokens,
    }


def score_trajectories(trajectories: List[Dict[str, Any]],
                       weights: Dict[str, float]) -> List[Tuple[float, Dict[str, Any]]]:
    """Compute weighted difficulty scores for all trajectories.

    Normalizes each dimension to [0, 1] independently, then computes
    a weighted sum based on the provided weights.

    Args:
        trajectories: List of trajectory dictionaries.
        weights: Dict mapping dimension name to weight (should sum to ~1.0).

    Returns:
        List of (score, trajectory) tuples sorted descending by score.
    """
    if not trajectories:
        return []

    dim_values = compute_dimension_values(trajectories)
    n = len(trajectories)

    # Compute min/max per dimension
    bounds = {}
    for dim, vals in dim_values.items():
        bounds[dim] = (min(vals), max(vals))

    scored: List[Tuple[float, Dict[str, Any]]] = []

    for i, traj in enumerate(trajectories):
        score = 0.0
        for dim, weight in weights.items():
            raw = dim_values[dim][i]
            norm = normalize(raw, bounds[dim][0], bounds[dim][1])
            score += weight * norm

        scored.append((score, traj))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def select_coreset(scored: List[Tuple[float, Dict[str, Any]]],
                   coreset_size: int) -> List[Dict[str, Any]]:
    """Select a coreset of challenging trajectories.

    Takes the top-N trajectories by difficulty score, ensuring diversity
    by avoiding duplicate issue titles (deduplicates by taking first
    occurrence of each unique label combination).

    Args:
        scored: List of (score, trajectory) tuples sorted by score descending.
        coreset_size: Maximum number of trajectories to select.

    Returns:
        List of selected trajectory dictionaries.
    """
    if not scored:
        return []

    coreset: List[Dict[str, Any]] = []
    seen_labels: set = set()

    for score, traj in scored:
        if len(coreset) >= coreset_size:
            break

        # Diversity: skip if we already have an issue with identical labels
        label_key = tuple(sorted(traj.get("labels", [])))
        if label_key in seen_labels:
            continue

        seen_labels.add(label_key)
        traj_copy = dict(traj)
        traj_copy["_difficulty_score"] = score
        coreset.append(traj_copy)

    return coreset


def select_coreset_from_archive(archive_dir: str = None,
                                config_path: str = DEFAULT_CONFIG,
                                coreset_size: int = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Main entry point: load archived trajectories, score them, select coreset.

    Args:
        archive_dir: Override path to archive directory.
        config_path: Path to rho_config.yaml.
        coreset_size: Override coreset size from config.

    Returns:
        Tuple of (selected coreset, rho_config dict).
    """
    config = load_rho_config(config_path)
    if coreset_size is None:
        coreset_size = config.get("coreset_size", 5)

    weights = config.get("difficulty_weights", {
        "error_rate": 0.30,
        "review_fix_requests": 0.25,
        "tool_calls": 0.20,
        "runtime": 0.15,
        "tokens": 0.10,
    })

    # Filter: only trajectories with metrics
    all_trajs = load_archived_trajectories()
    viable = [t for t in all_trajs if t.get("metrics_count", 0) > 0]

    if not viable:
        print("No trajectories with metrics found in archive.")
        return [], config

    scored = score_trajectories(viable, weights)
    coreset = select_coreset(scored, coreset_size)

    return coreset, config


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Select a coreset of challenging trajectories")
    parser.add_argument("--coreset-size", type=int, default=None, help="Number of tasks to select")
    parser.add_argument("--config", type=str, default=DEFAULT_CONFIG, help="Path to rho_config.yaml")
    args = parser.parse_args()

    coreset, config = select_coreset_from_archive(config_path=args.config, coreset_size=args.coreset_size)

    print(f"Selected coreset of {len(coreset)} tasks:")
    for i, traj in enumerate(coreset):
        num = traj["issue_number"]
        score = traj.get("_difficulty_score", 0.0)
        title = traj.get("title", "(no title)")
        metrics_count = traj.get("metrics_count", 0)
        print(f"  {i + 1}. Issue #{num} (score={score:.3f}) [{metrics_count} metrics]: {title}")


if __name__ == "__main__":
    main()
