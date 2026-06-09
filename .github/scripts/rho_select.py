#!/usr/bin/env python3
"""
RHO Select — compare baseline vs variants and select the winning harness.

Analyzes rollout results, computes weighted improvement scores, and
identifies the best variant. If the winner exceeds the improvement
threshold, outputs a PR-ready diff and summary.

Usage:
    from rho_select import compare_rollouts, select_winner

    winner, summary = select_winner(rollout_dir=".rho/rollout")
"""

import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from coreset_selector import load_rho_config
from rho_rollout import RolloutResult, load_variant_configs


@dataclass
class VariantScore:
    """Aggregated score for a single variant (including baseline)."""

    variant_id: str
    total_score: float
    avg_error_rate: float = 0.0
    avg_fix_requests: float = 0.0
    avg_tool_calls: float = 0.0
    avg_runtime_ms: float = 0.0
    avg_tokens: float = 0.0
    success_rate: float = 0.0
    task_count: int = 0


@dataclass
class SelectionResult:
    """Result of the selection process."""

    winner_variant_id: str
    winner_score: float
    baseline_score: float
    improvement_percent: float
    scores: List[VariantScore]
    recommendation: str
    pr_ready: bool = False


def load_rollout_results(rollout_dir: str = ".rho/rollout") -> List[RolloutResult]:
    """Load rollout results from the results.json file.

    Args:
        rollout_dir: Path to .rho/rollout/ directory.

    Returns:
        List of RolloutResult objects.
    """
    results_path = os.path.join(rollout_dir, "results.json")
    if not os.path.isfile(results_path):
        print(f"Error: No rollout results found at {results_path}")
        return []

    with open(results_path) as f:
        data = json.load(f)

    return [RolloutResult(**r) for r in data]


def aggregate_variant_scores(results: List[RolloutResult]) -> Dict[str, VariantScore]:
    """Aggregate rollout results by variant ID.

    Computes per-variant averages for all metrics.

    Args:
        results: List of RolloutResult objects.

    Returns:
        Dict mapping variant_id to VariantScore.
    """
    by_variant: Dict[str, List[RolloutResult]] = {}
    for r in results:
        by_variant.setdefault(r.variant_id, []).append(r)

    scores = {}
    for vid, variant_results in by_variant.items():
        total = len(variant_results)
        succeeded = [r for r in variant_results if r.success]

        scores[vid] = VariantScore(
            variant_id=vid,
            total_score=0.0,  # Computed later
            avg_error_rate=_avg([r.error_rate for r in succeeded]),
            avg_fix_requests=_avg([r.fix_requests for r in succeeded]),
            avg_tool_calls=_avg([r.tool_calls for r in succeeded]),
            avg_runtime_ms=_avg([r.runtime_ms for r in succeeded]),
            avg_tokens=_avg(
                [r.input_tokens + r.output_tokens for r in succeeded]
            ),
            success_rate=len(succeeded) / total if total > 0 else 0.0,
            task_count=total,
        )

    return scores


def _avg(values: List[float]) -> float:
    """Compute average of a list of values."""
    return sum(values) / len(values) if values else 0.0


def compute_improvement_score(
    baseline: VariantScore,
    variant: VariantScore,
    weights: Dict[str, float],
) -> float:
    """Compute weighted improvement score of a variant over baseline.

    Positive score = variant is better than baseline.

    Metrics:
    - error_rate_reduction: Lower is better → improvement = (base - variant) / base
    - fix_request_reduction: Lower is better → same formula
    - latency_reduction: Lower is better → same formula
    - token_efficiency: Lower is better → same formula
    - tool_call_reduction: Lower is better → same formula

    Args:
        baseline: Baseline VariantScore.
        variant: Variant VariantScore.
        weights: Dict mapping metric name to weight.

    Returns:
        Weighted improvement score (positive = better).
    """
    score = 0.0

    # Error rate reduction
    if baseline.avg_error_rate > 0:
        err_improvement = (baseline.avg_error_rate - variant.avg_error_rate) / baseline.avg_error_rate
        score += weights.get("error_rate_reduction", 0) * err_improvement

    # Fix request reduction
    if baseline.avg_fix_requests > 0:
        fix_improvement = (baseline.avg_fix_requests - variant.avg_fix_requests) / baseline.avg_fix_requests
        score += weights.get("fix_request_reduction", 0) * fix_improvement

    # Latency reduction
    if baseline.avg_runtime_ms > 0:
        latency_improvement = (baseline.avg_runtime_ms - variant.avg_runtime_ms) / baseline.avg_runtime_ms
        score += weights.get("latency_reduction", 0) * latency_improvement

    # Token efficiency
    if baseline.avg_tokens > 0:
        token_improvement = (baseline.avg_tokens - variant.avg_tokens) / baseline.avg_tokens
        score += weights.get("token_efficiency", 0) * token_improvement

    # Tool call reduction
    if baseline.avg_tool_calls > 0:
        tool_improvement = (baseline.avg_tool_calls - variant.avg_tool_calls) / baseline.avg_tool_calls
        score += weights.get("tool_call_reduction", 0) * tool_improvement

    return score


def count_improved_metrics(
    baseline: VariantScore,
    variant: VariantScore,
) -> int:
    """Count how many individual metrics improved (lower is better).

    Args:
        baseline: Baseline VariantScore.
        variant: Variant VariantScore.

    Returns:
        Number of metrics where variant < baseline.
    """
    count = 0
    if variant.avg_error_rate < baseline.avg_error_rate:
        count += 1
    if variant.avg_fix_requests < baseline.avg_fix_requests:
        count += 1
    if variant.avg_runtime_ms < baseline.avg_runtime_ms:
        count += 1
    if variant.avg_tokens < baseline.avg_tokens:
        count += 1
    if variant.avg_tool_calls < baseline.avg_tool_calls:
        count += 1
    return count


def select_winner(
    rollout_dir: str = ".rho/rollout",
    config_path: Optional[str] = None,
) -> Optional[SelectionResult]:
    """Select the winning variant from rollout results.

    Full pipeline: load results → aggregate → score → compare → select.

    Args:
        rollout_dir: Path to rollout results directory.
        config_path: Optional path to rho_config.yaml.

    Returns:
        SelectionResult with winner info, or None if no valid results.
    """
    rho_config = load_rho_config(config_path)
    selection_cfg = rho_config.get("selection", {})
    min_improvement = selection_cfg.get("min_improvement_percent", 5.0)
    improvement_weights = selection_cfg.get("improvement_weights", {
        "error_rate_reduction": 0.35,
        "fix_request_reduction": 0.25,
        "latency_reduction": 0.20,
        "token_efficiency": 0.10,
        "tool_call_reduction": 0.10,
    })
    min_metrics = selection_cfg.get("min_metrics_improved", 2)

    # Load results
    results = load_rollout_results(rollout_dir)
    if not results:
        print("Error: No rollout results to analyze.")
        return None

    # Aggregate by variant
    scores = aggregate_variant_scores(results)

    if "baseline" not in scores:
        print("Error: No baseline results found.")
        return None

    baseline = scores["baseline"]

    # Score each variant against baseline
    variant_scores = []
    for vid, score in scores.items():
        if vid == "baseline":
            score.total_score = 0.0
            variant_scores.append(score)
            continue

        imp_score = compute_improvement_score(baseline, score, improvement_weights)
        score.total_score = round(imp_score, 4)
        variant_scores.append(score)

    # Sort by total score descending
    variant_scores.sort(key=lambda s: s.total_score, reverse=True)

    # Find best variant (excluding baseline)
    best = None
    for score in variant_scores:
        if score.variant_id == "baseline":
            continue
        best = score
        break

    if best is None:
        print("No variants to compare against baseline.")
        return None

    # Compute improvement percentage
    if baseline.avg_error_rate > 0:
        improvement_pct = (
            (baseline.avg_error_rate - best.avg_error_rate) / baseline.avg_error_rate * 100
        )
    else:
        # Fall back to weighted score as percentage
        improvement_pct = best.total_score * 100

    metrics_improved = count_improved_metrics(baseline, best)

    # Determine if PR is ready
    pr_ready = (
        improvement_pct >= min_improvement
        and metrics_improved >= min_metrics
        and best.success_rate >= 0.5
    )

    # Build recommendation
    if pr_ready:
        recommendation = (
            f"Variant '{best.variant_id}' shows {improvement_pct:.1f}% improvement "
            f"over baseline with {metrics_improved} metrics improved. "
            f"Recommended for promotion."
        )
    else:
        recommendation = (
            f"Best variant '{best.variant_id}' shows {improvement_pct:.1f}% improvement "
            f"({metrics_improved} metrics improved). "
            f"Threshold: {min_improvement}% with {min_metrics} metrics. "
            f"{'Approve manually.' if improvement_pct > 0 else 'No improvement detected.'}"
        )

    return SelectionResult(
        winner_variant_id=best.variant_id,
        winner_score=best.total_score,
        baseline_score=baseline.total_score,
        improvement_percent=round(improvement_pct, 2),
        scores=variant_scores,
        recommendation=recommendation,
        pr_ready=pr_ready,
    )


def generate_pr_description(
    selection: SelectionResult,
    variants_dir: str = ".rho/variants",
) -> str:
    """Generate a PR description for the winning variant.

    Args:
        selection: SelectionResult from select_winner.
        variants_dir: Path to variant configs.

    Returns:
        Markdown PR description string.
    """
    lines = [
        "## 🔧 RHO Harness Optimization",
        "",
        "**Auto-generated by Retrospective Harness Optimization (RHO)**",
        "",
        "### Summary",
        "",
        f"- **Winning variant:** `{selection.winner_variant_id}`",
        f"- **Improvement:** {selection.improvement_percent:.1f}%",
        f"- **Recommendation:** {selection.recommendation}",
        "",
        "### Comparison",
        "",
        "| Variant | Score | Error Rate | Fix Requests | Tool Calls | Runtime (ms) | Tokens | Success Rate |",
        "|---------|-------|------------|-------------|------------|-------------|--------|-------------|",
    ]

    for score in selection.scores:
        lines.append(
            f"| {score.variant_id} "
            f"| {score.total_score:.3f} "
            f"| {score.avg_error_rate:.1f}% "
            f"| {score.avg_fix_requests:.1f} "
            f"| {score.avg_tool_calls:.1f} "
            f"| {score.avg_runtime_ms:.0f} "
            f"| {score.avg_tokens:.0f} "
            f"| {score.success_rate:.0%} |"
        )

    # Load winning variant config for diff
    variant_path = os.path.join(variants_dir, f"variant_{selection.winner_variant_id}.yaml")
    if os.path.isfile(variant_path):
        with open(variant_path) as f:
            variant_content = f.read()
        lines.extend([
            "",
            "### Winning Variant Config",
            "",
            "```yaml",
            variant_content.rstrip(),
            "```",
        ])

    lines.extend([
        "",
        "---",
        f"_Generated at {datetime.now(timezone.utc).isoformat()}_",
    ])

    return "\n".join(lines)


def main():
    """CLI entry point for RHO selection."""
    rollout_dir = os.environ.get("RHO_ROLLOUT_DIR", ".rho/rollout")
    variants_dir = os.environ.get("RHO_VARIANTS_DIR", ".rho/variants")
    config_path = os.environ.get("RHO_CONFIG_PATH", None)

    selection = select_winner(rollout_dir=rollout_dir, config_path=config_path)

    if selection is None:
        print("Error: Could not complete selection.")
        sys.exit(1)

    # Print results
    print("\n=== RHO Selection Results ===")
    print(f"Winner: {selection.winner_variant_id} (score: {selection.winner_score:.3f})")
    print(f"Improvement: {selection.improvement_percent:.1f}%")
    print(f"PR Ready: {selection.pr_ready}")
    print(f"Recommendation: {selection.recommendation}")

    # Generate and save PR description
    pr_desc = generate_pr_description(selection, variants_dir)
    pr_path = os.path.join(rollout_dir, "pr_description.md")
    with open(pr_path, "w") as f:
        f.write(pr_desc)
    print(f"\nPR description saved to {pr_path}")

    # Output JSON for workflow consumption
    output = {
        "winner_variant_id": selection.winner_variant_id,
        "improvement_percent": selection.improvement_percent,
        "pr_ready": selection.pr_ready,
        "recommendation": selection.recommendation,
    }
    print(f"\nRHO_SELECTION_RESULT={json.dumps(output)}")


if __name__ == "__main__":
    main()
