#!/usr/bin/env python3
"""
Coreset selector for Retrospective Harness Optimization (RHO).

Selects a diverse subset of challenging tasks from historical trajectories
based on weighted difficulty scoring (error rate, fix requests, tool calls,
runtime, token usage).

Input: List of Trajectory objects from trajectory_archive.py
Output: List of selected trajectories ranked by difficulty
"""

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
from metrics_schema import Metrics
from trajectory_archive import Trajectory, TrajectoryRepository


# ── Scoring Helpers ──────────────────────────────────────────────────


def _normalize(values: List[float]) -> List[float]:
    """
    Min-max normalize a list of values to [0, 1].

    If all values are identical, returns [0.0] * len(values).
    """
    if not values:
        return []
    min_v = min(values)
    max_v = max(values)
    if max_v == min_v:
        return [0.0] * len(values)
    return [(v - min_v) / (max_v - min_v) for v in values]


def compute_trajectory_metrics(trajectory: Trajectory) -> Dict[str, float]:
    """
    Compute aggregate difficulty metrics for a single trajectory.

    Args:
        trajectory: A trajectory with metrics from one issue.

    Returns:
        Dict mapping metric name to aggregated value.
    """
    metrics_list = trajectory.metrics
    if not metrics_list:
        return {
            "error_rate": 0.0,
            "review_fix_requests": 0,
            "tool_calls": 0,
            "runtime_ms": 0.0,
            "total_tokens": 0,
            "num_runs": 0,
        }

    total_error_rate = sum(m.tool_call_error_rate for m in metrics_list)
    total_fix_requests = sum(m.review_fix_requests for m in metrics_list)
    total_tool_calls = sum(m.nb_tool_calls for m in metrics_list)
    total_runtime = sum(m.agent_runtime_ms for m in metrics_list)
    total_tokens = sum(m.input_tokens + m.output_tokens for m in metrics_list)

    return {
        "error_rate": total_error_rate,
        "review_fix_requests": total_fix_requests,
        "tool_calls": total_tool_calls,
        "runtime_ms": total_runtime,
        "total_tokens": total_tokens,
        "num_runs": len(metrics_list),
    }


@dataclass
class ScoredTrajectory:
    """A trajectory with its computed difficulty score."""

    trajectory: Trajectory
    score: float
    metrics: Dict[str, float]
    normalized: Dict[str, float] = field(default_factory=dict)


# ── Core Selection Logic ─────────────────────────────────────────────


def score_trajectories(
    trajectories: List[Trajectory],
    weights: Optional[Dict[str, float]] = None,
) -> List[ScoredTrajectory]:
    """
    Score all trajectories by weighted difficulty.

    Args:
        trajectories: List of trajectories to score.
        weights: Optional dict of metric weights. Defaults to rho_config.yaml values.

    Returns:
        List of ScoredTrajectory sorted by score (descending).
    """
    if weights is None:
        weights = {
            "error_rate": 0.30,
            "review_fix_requests": 0.25,
            "tool_calls": 0.20,
            "runtime": 0.15,
            "token_usage": 0.10,
        }

    # Compute raw metrics for each trajectory
    raw_metrics = [compute_trajectory_metrics(t) for t in trajectories]

    # Extract value lists for normalization
    error_rates = [m["error_rate"] for m in raw_metrics]
    fix_requests = [float(m["review_fix_requests"]) for m in raw_metrics]
    tool_calls = [float(m["tool_calls"]) for m in raw_metrics]
    runtimes = [m["runtime_ms"] for m in raw_metrics]
    tokens = [float(m["total_tokens"]) for m in raw_metrics]

    # Normalize each metric independently
    norm_error = _normalize(error_rates)
    norm_fix = _normalize(fix_requests)
    norm_tools = _normalize(tool_calls)
    norm_runtime = _normalize(runtimes)
    norm_tokens = _normalize(tokens)

    # Compute weighted scores
    scored = []
    for i, trajectory in enumerate(trajectories):
        raw = raw_metrics[i]
        score = (
            weights.get("error_rate", 0.30) * norm_error[i]
            + weights.get("review_fix_requests", 0.25) * norm_fix[i]
            + weights.get("tool_calls", 0.20) * norm_tools[i]
            + weights.get("runtime", 0.15) * norm_runtime[i]
            + weights.get("token_usage", 0.10) * norm_tokens[i]
        )
        normalized = {
            "error_rate": norm_error[i],
            "review_fix_requests": norm_fix[i],
            "tool_calls": norm_tools[i],
            "runtime": norm_runtime[i],
            "token_usage": norm_tokens[i],
        }
        scored.append(ScoredTrajectory(
            trajectory=trajectory,
            score=score,
            metrics=raw,
            normalized=normalized,
        ))

    scored.sort(key=lambda s: s.score, reverse=True)
    return scored


def select_coreset(
    scored: List[ScoredTrajectory],
    size: int = 5,
) -> List[ScoredTrajectory]:
    """
    Select top-N trajectories as the coreset.

    Args:
        scored: List of scored trajectories (sorted by score descending).
        size: Number of trajectories to select.

    Returns:
        The top-N scored trajectories.
    """
    return scored[:size]


# ── CLI Entry Point ──────────────────────────────────────────────────


def load_rho_config(path: str = "rho_config.yaml") -> Dict:
    """
    Load RHO configuration from YAML file.

    Uses yaml.safe_load. Falls back to defaults if file not found.
    """
    import yaml

    if not os.path.isfile(path):
        return {}

    with open(path) as f:
        return yaml.safe_load(f) or {}


def main():
    """CLI: Load trajectories, score them, print coreset selection."""
    config = load_rho_config()

    repo_dir = os.environ.get("RHO_REPOSITORY", ".rho/trajectories")
    coreset_size = int(
        os.environ.get("RHO_CORESET_SIZE",
                        config.get("coreset", {}).get("size", 5))
    )

    weights_config = config.get("coreset", {}).get("weights", {})
    weights = {
        "error_rate": weights_config.get("error_rate", 0.30),
        "review_fix_requests": weights_config.get("review_fix_requests", 0.25),
        "tool_calls": weights_config.get("tool_calls", 0.20),
        "runtime": weights_config.get("runtime", 0.15),
        "token_usage": weights_config.get("token_usage", 0.10),
    }

    print(f"Loading trajectories from {repo_dir}...")
    repository = TrajectoryRepository(repo_dir)
    trajectories = repository.list_all()

    if not trajectories:
        print("No trajectories found. Run trajectory_archive.py first.")
        sys.exit(1)

    print(f"Found {len(trajectories)} trajectories. Scoring...")
    scored = score_trajectories(trajectories, weights=weights)

    coreset = select_coreset(scored, size=coreset_size)
    print(f"\nSelected coreset (top {len(coreset)} by difficulty):")
    print("-" * 70)

    for i, item in enumerate(coreset):
        t = item.trajectory
        issue = t.issue
        print(f"\n  #{issue.issue_number}: {issue.title}")
        print(f"    Score: {item.score:.3f}")
        print(f"    Runs: {item.metrics['num_runs']}")
        print(f"    Error rate: {item.metrics['error_rate']:.1f}%")
        print(f"    Fix requests: {item.metrics['review_fix_requests']}")
        print(f"    Tool calls: {item.metrics['tool_calls']}")
        print(f"    Runtime: {item.metrics['runtime_ms']:.0f}ms")
        print(f"    Tokens: {item.metrics['total_tokens']}")

    # Output as JSON for pipeline consumption
    output = {
        "coreset_size": len(coreset),
        "total_trajectories": len(trajectories),
        "selected": [
            {
                "issue_number": item.trajectory.issue.issue_number,
                "title": item.trajectory.issue.title,
                "score": round(item.score, 4),
                "metrics": item.metrics,
            }
            for item in coreset
        ],
    }

    output_path = ".rho/coreset.json"
    os.makedirs(".rho", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nCoreset saved to {output_path}")


if __name__ == "__main__":
    main()
