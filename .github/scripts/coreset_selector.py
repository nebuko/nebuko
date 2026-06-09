#!/usr/bin/env python3
"""
Coreset Selector — Difficulty-based trajectory selection for RHO.

Reads archived trajectory records, scores each task by weighted difficulty
(error rate, fix requests, tool calls, runtime, token count), and selects
a diverse subset of the most challenging past tasks.

Difficulty scoring uses configurable weights from rho_config.yaml.

Usage:
    python coreset_selector.py [--min-score FLOAT] [--size INT]
"""

import json
import math
import sys
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from trajectory_archive import TrajectoryRecord, load_all_trajectories, get_archive_dir


@dataclass
class DifficultyScore:
    """Breakdown of difficulty dimensions for one trajectory."""

    issue_number: int = 0
    overall: float = 0.0
    error_rate: float = 0.0
    fix_requests: float = 0.0
    tool_calls: float = 0.0
    runtime: float = 0.0
    token_count: float = 0.0


@dataclass
class CoresetSelection:
    """Result of coreset selection."""

    selected: List[DifficultyScore] = field(default_factory=list)
    pool_size: int = 0
    target_size: int = 0


# ── Normalization Helpers ────────────────────────────────────────────


def normalize(value: float, vmin: float, vmax: float, eps: float = 1e-9) -> float:
    """Min-max normalize to [0, 1].

    Args:
        value: Raw value to normalize.
        vmin: Minimum of normalization range.
        vmax: Maximum of normalization range.
        eps: Epsilon to avoid division by zero.

    Returns:
        Clamped normalized value in [0, 1].
    """
    if vmax - vmin < eps:
        return 0.0
    return max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))


def percentile(values: List[float], pct: float) -> float:
    """Approximate percentile calculation.

    Args:
        values: Sorted or unsorted list of floats.
        pct: Percentile (0.0–1.0).

    Returns:
        Value at the given percentile.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(pct * (len(sorted_vals) - 1))
    return sorted_vals[idx]


# ── Per-Dimension Scorers ────────────────────────────────────────────


def _metric_val(metric_obj_or_dict, key: str, default: Any = 0) -> Any:
    """Extract a metric field from either a Metrics dataclass or a dict.

    Args:
        metric_obj_or_dict: Either a Metrics object or a dict.
        key: Field name to extract.
        default: Default value if key not found.

    Returns:
        Field value or default.
    """
    if hasattr(metric_obj_or_dict, key):
        return getattr(metric_obj_or_dict, key, default)
    elif isinstance(metric_obj_or_dict, dict):
        return metric_obj_or_dict.get(key, default)
    return default


def score_error_rate(metrics_list: List[Any]) -> float:
    """Higher error rate = higher difficulty. Returns raw avg error rate %.

    Args:
        metrics_list: List of metric dicts or Metrics objects.

    Returns:
        Average tool_call_error_rate across all metric entries.
    """
    if not metrics_list:
        return 0.0
    vals = [_metric_val(m, "tool_call_error_rate", 0.0) for m in metrics_list]
    return sum(vals) / len(vals)


def score_fix_requests(metrics_list: List[Any]) -> float:
    """Count total review fix requests. More = harder.

    Args:
        metrics_list: List of metric dicts or Metrics objects.

    Returns:
        Total review_fix_requests across all entries.
    """
    return sum(_metric_val(m, "review_fix_requests", 0) for m in metrics_list)


def score_tool_calls(metrics_list: List[Any]) -> float:
    """Total tool calls. More calls often indicate harder problems.

    Args:
        metrics_list: List of metric dicts or Metrics objects.

    Returns:
        Total nb_tool_calls across all entries.
    """
    return sum(_metric_val(m, "nb_tool_calls", 0) for m in metrics_list)


def score_runtime(ms_total: float) -> float:
    """Total runtime in milliseconds. Longer = harder.

    Args:
        ms_total: Sum of agent_runtime_ms across entries.

    Returns:
        Total milliseconds.
    """
    return ms_total


def score_token_count(metrics_list: List[Any]) -> float:
    """Total tokens consumed. More tokens often indicates harder problems.

    Args:
        metrics_list: List of metric dicts or Metrics objects.

    Returns:
        Sum of input_tokens + output_tokens across all entries.
    """
    total = 0
    for m in metrics_list:
        total += _metric_val(m, "input_tokens", 0) + _metric_val(m, "output_tokens", 0)
    return total


# ── Aggregated Difficulty Scoring ────────────────────────────────────


def compute_difficulty_scores(records: List[TrajectoryRecord],
                               weights: Dict[str, float]) -> List[DifficultyScore]:
    """Compute weighted difficulty scores for all records.

    First normalizes each dimension to [0, 1] across the entire pool,
    then combines using weighted sum.

    Args:
        records: List of TrajectoryRecord objects.
        weights: Dict mapping dimension name to weight (should sum to ~1.0).

    Returns:
        List of DifficultyScore objects, sorted descending by overall score.
    """
    if not records:
        return []

    # Extract raw scores for each dimension
    raw_scores: List[Dict[str, float]] = []
    for rec in records:
        metrics = rec.metrics
        raw_scores.append({
            "issue_number": rec.issue_number,
            "error_rate": score_error_rate(metrics),
            "fix_requests": score_fix_requests(metrics),
            "tool_calls": score_tool_calls(metrics),
            "runtime": score_runtime(sum(_metric_val(m, "agent_runtime_ms", 0) for m in metrics)),
            "token_count": score_token_count(metrics),
        })

    # Determine ranges for normalization
    dim_keys = ["error_rate", "fix_requests", "tool_calls", "runtime", "token_count"]
    ranges = {}
    for key in dim_keys:
        vals = [rs[key] for rs in raw_scores]
        ranges[key] = (min(vals) if vals else 0.0, max(vals) if vals else 1.0)

    # Normalize and combine
    difficulty_scores: List[DifficultyScore] = []
    for rs in raw_scores:
        norm_scores = {}
        for key in dim_keys:
            vmin, vmax = ranges[key]
            norm_scores[key] = normalize(rs[key], vmin, vmax)

        weighted_sum = sum(norm_scores.get(k, 0.0) * weights.get(k, 0.0) for k in dim_keys)

        difficulty_scores.append(DifficultyScore(
            issue_number=rs["issue_number"],
            overall=weighted_sum,
            error_rate=norm_scores["error_rate"],
            fix_requests=norm_scores["fix_requests"],
            tool_calls=norm_scores["tool_calls"],
            runtime=norm_scores["runtime"],
            token_count=norm_scores["token_count"],
        ))

    difficulty_scores.sort(key=lambda ds: ds.overall, reverse=True)
    return difficulty_scores


# ── Diversity Filtering ──────────────────────────────────────────────


def _has_any_successful_run(metrics_list: List[Any]) -> bool:
    """Check if any metric entry has near-zero error rate and no fix requests.

    Args:
        metrics_list: List of metric dicts or Metrics objects.

    Returns:
        True if at least one clean run exists.
    """
    for m in metrics_list:
        if (_metric_val(m, "tool_call_error_rate", 0) < 5.0 and
                _metric_val(m, "review_fix_requests", 0) == 0):
            return True
    return False


def diversify_selection(scored: List[DifficultyScore],
                        records: List[TrajectoryRecord],
                        target_size: int) -> List[DifficultyScore]:
    """Select a diverse coreset ensuring mix of failure-only and partial-success tasks.

    Strategy: pick half from pure failures, half from mixed-outcome tasks.

    Args:
        scored: Sorted list of DifficultyScore.
        records: Corresponding TrajectoryRecords (matched by issue_number).
        target_size: Desired coreset size.

    Returns:
        List of DifficultyScore forming the diverse coreset.
    """
    record_map = {r.issue_number: r for r in records}

    failures_only: List[DifficultyScore] = []
    mixed_outcomes: List[DifficultyScore] = []

    for ds in scored:
        rec = record_map.get(ds.issue_number)
        if rec and _has_any_successful_run(rec.metrics):
            mixed_outcomes.append(ds)
        else:
            failures_only.append(ds)

    half = max(1, target_size // 2)
    selected: List[DifficultyScore] = []

    selected.extend(failures_only[:half])
    selected.extend(mixed_outcomes[:target_size - len(selected)])

    selected.sort(key=lambda ds: ds.overall, reverse=True)
    return selected


# ── Public API ───────────────────────────────────────────────────────


def select_coreset(archive_dir: Path, target_size: int = 5,
                   min_score: float = 0.0,
                   weights: Optional[Dict[str, float]] = None) -> CoresetSelection:
    """Select a diverse coreset of challenging trajectories.

    Args:
        archive_dir: Path to trajectory archive directory.
        target_size: Number of tasks to select.
        min_score: Minimum overall difficulty score to consider.
        weights: Custom difficulty dimension weights. Defaults to rho_config.yaml values.

    Returns:
        CoresetSelection with ranked and filtered scores.
    """
    if weights is None:
        weights = {
            "error_rate": 0.30,
            "fix_requests": 0.25,
            "tool_calls": 0.20,
            "runtime": 0.15,
            "token_count": 0.10,
        }

    records = load_all_trajectories(archive_dir)
    if not records:
        return CoresetSelection(pool_size=0, target_size=target_size)

    scored = compute_difficulty_scores(records, weights)

    # Apply minimum score filter
    if min_score > 0:
        scored = [ds for ds in scored if ds.overall >= min_score]

    selected = diversify_selection(scored, records, target_size)

    return CoresetSelection(
        selected=selected,
        pool_size=len(records),
        target_size=target_size,
    )


def load_rho_weights(rho_config_path: str) -> Dict[str, float]:
    """Load difficulty weights from rho_config.yaml.

    Args:
        rho_config_path: Path to the RHO config file.

    Returns:
        Dict of dimension name to weight.
    """
    if not os.path.isfile(rho_config_path):
        return {
            "error_rate": 0.30,
            "fix_requests": 0.25,
            "tool_calls": 0.20,
            "runtime": 0.15,
            "token_count": 0.10,
        }

    try:
        import yaml
        with open(rho_config_path) as f:
            cfg = yaml.safe_load(f) or {}
        weights = cfg.get("coreset", {}).get("difficulty_weights", {})
        if weights:
            return weights
    except Exception as e:
        print(f"[coreset] Warning: Failed to load weights from {rho_config_path}: {e}")

    return {
        "error_rate": 0.30,
        "fix_requests": 0.25,
        "tool_calls": 0.20,
        "runtime": 0.15,
        "token_count": 0.10,
    }


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Coreset selector for RHO")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="Minimum difficulty score (0.0-1.0)")
    parser.add_argument("--size", type=int, default=None,
                        help="Target coreset size")
    parser.add_argument("--archive-dir", type=str, default=".rho/trajectories")
    args = parser.parse_args()

    rho_config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "rho_config.yaml")
    weights = load_rho_weights(rho_config_path)
    target_size = args.size

    if target_size is None:
        try:
            import yaml
            if os.path.isfile(rho_config_path):
                with open(rho_config_path) as f:
                    cfg = yaml.safe_load(f) or {}
                target_size = cfg.get("coreset", {}).get("size", 5)
        except Exception:
            target_size = 5

    archive_dir = Path(args.archive_dir)
    selection = select_coreset(archive_dir, target_size=target_size,
                                min_score=args.min_score, weights=weights)

    if not selection.selected:
        print(f"[coreset] No trajectories available in {args.archive_dir}/")
        print("[coreset] Run trajectory_archive.py fetch first.")
        sys.exit(0)

    print(f"[coreset] Pool size: {selection.pool_size}, Target: {target_size}")
    print(f"[coreset] Selected coreset:")
    for ds in selection.selected:
        print(
            f"  #{ds.issue_number:>4d}  "
            f"difficulty={ds.overall:.3f}  "
            f"(err={ds.error_rate:.2f} "
            f"fix={ds.fix_requests:.2f} "
            f"call={ds.tool_calls:.2f} "
            f"time={ds.runtime:.2f} "
            f"tok={ds.token_count:.2f})"
        )

    # Output as JSON for programmatic consumption
    output = {
        "pool_size": selection.pool_size,
        "target_size": target_size,
        "selected": [
            {
                "issue_number": ds.issue_number,
                "overall": ds.overall,
                "breakdown": {
                    "error_rate": ds.error_rate,
                    "fix_requests": ds.fix_requests,
                    "tool_calls": ds.tool_calls,
                    "runtime": ds.runtime,
                    "token_count": ds.token_count,
                }
            }
            for ds in selection.selected
        ],
    }
    print(f"\n[coreset] JSON output:")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
