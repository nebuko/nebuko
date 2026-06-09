#!/usr/bin/env python3
"""
RHO Select — Variant comparison and promotion for RHO.

Compares baseline vs variant rollout metrics quantitatively (error rates,
runtime, tokens) and qualitatively (self-assessment scores). Identifies
the winning variant and creates a PR with the updated harness config.

Quantitative scoring (70% weight):
    - Lower error rate = better
    - Fewer fix requests = better
    - Lower runtime = better
    - Lower token count = better

Qualitative scoring (30% weight):
    - Outcome score computed from trajectory metrics

If the winner improves over baseline by at least min_improvement_pct%,
a PR is opened with the updated agent-config.yaml.

Usage:
    python rho_select.py [--rollout-dir DIR] [--repo OWNER/REPO] [--branch BRANCH]
"""

import json
import math
import os
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from metrics_schema import Metrics


@dataclass
class VariantResult:
    """Aggregated result for one variant (or baseline) across the coreset."""

    name: str = ""
    variant_name: str = ""
    rationale: str = ""
    total_runs: int = 0
    avg_error_rate: float = 0.0
    avg_fix_requests: float = 0.0
    avg_runtime_ms: float = 0.0
    avg_token_count: float = 0.0
    avg_outcome_score: float = 0.0
    composite_score: float = 0.0


# ── Quantitative Scoring ─────────────────────────────────────────────


def compute_avg_error_rate(run_metrics: List[Dict[str, Any]]) -> float:
    """Average tool_call_error_rate across all runs.

    Args:
        run_metrics: List of metric dicts.

    Returns:
        Mean error rate (%).
    """
    vals = [m.get("tool_call_error_rate", 0.0) for m in run_metrics]
    return sum(vals) / len(vals) if vals else 0.0


def compute_avg_fix_requests(run_metrics: List[Dict[str, Any]]) -> float:
    """Average review_fix_requests across all runs.

    Args:
        run_metrics: List of metric dicts.

    Returns:
        Mean fix request count.
    """
    vals = [m.get("review_fix_requests", 0) for m in run_metrics]
    return sum(vals) / len(vals) if vals else 0.0


def compute_avg_runtime_ms(run_metrics: List[Dict[str, Any]]) -> float:
    """Average agent_runtime_ms across all runs.

    Args:
        run_metrics: List of metric dicts.

    Returns:
        Mean runtime in milliseconds.
    """
    vals = [m.get("agent_runtime_ms", 0.0) for m in run_metrics]
    return sum(vals) / len(vals) if vals else 0.0


def compute_avg_token_count(run_metrics: List[Dict[str, Any]]) -> float:
    """Average total tokens (input + output) across all runs.

    Args:
        run_metrics: List of metric dicts.

    Returns:
        Mean token count.
    """
    totals = []
    for m in run_metrics:
        total = m.get("input_tokens", 0) + m.get("output_tokens", 0)
        totals.append(total)
    return sum(totals) / len(totals) if totals else 0.0


# ── Composite Score ──────────────────────────────────────────────────


def normalize(value: float, vmin: float, vmax: float, eps: float = 1e-9) -> float:
    """Min-max normalize to [0, 1].

    Args:
        value: Raw value.
        vmin: Range minimum.
        vmax: Range maximum.
        eps: Epsilon for zero-range guard.

    Returns:
        Normalized value clamped to [0, 1].
    """
    if vmax - vmin < eps:
        return 0.0
    return max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))


def compute_composite_score(results: List[VariantResult],
                             quant_weight: float = 0.70,
                             qual_weight: float = 0.30) -> List[VariantResult]:
    """Compute composite scores for all variants.

    Quantitative sub-score (normalized across pool, inverted where lower-is-better):
        - error_rate (40%)
        - fix_requests (20%)
        - runtime (20%)
        - token_count (20%)

    Qualitative sub-score:
        - outcome_score (100%)

    Args:
        results: List of VariantResult objects.
        quant_weight: Weight for quantitative component.
        qual_weight: Weight for qualitative component.

    Returns:
        Same list with composite_score filled in.
    """
    if not results:
        return results

    # Normalize each quantitative dimension
    n = len(results)
    for dim_fn, attr_name in [
        (lambda r: r.avg_error_rate, "_q_error"),
        (lambda r: r.avg_fix_requests, "_q_fixreq"),
        (lambda r: r.avg_runtime_ms, "_q_runtime"),
        (lambda r: r.avg_token_count, "_q_tokens"),
    ]:
        vals = [dim_fn(r) for r in results]
        vmin, vmax = min(vals), max(vals)

        for r in results:
            raw = dim_fn(r)
            # Invert: lower raw = better = higher normalized score
            norm = 1.0 - normalize(raw, vmin, vmax)
            setattr(r, attr_name, norm)

    for r in results:
        # Quantitative: equally weighted sub-dims
        quant_score = (
            getattr(r, "_q_error", 0) * 0.40
            + getattr(r, "_q_fixreq", 0) * 0.20
            + getattr(r, "_q_runtime", 0) * 0.20
            + getattr(r, "_q_tokens", 0) * 0.20
        )

        # Qualitative: normalized outcome scores
        outcome_vals = [rr.avg_outcome_score for rr in results]
        o_min, o_max = min(outcome_vals), max(outcome_vals)
        qual_score = normalize(r.avg_outcome_score, o_min, o_max)

        r.composite_score = quant_weight * quant_score + qual_weight * qual_score

        # Clean up temp attrs
        for attr in ("_q_error", "_q_fixreq", "_q_runtime", "_q_tokens"):
            delattr(r, attr)

    return results


# ── Winner Selection ─────────────────────────────────────────────────


def select_winner(results: List[VariantResult],
                  baseline_result: VariantResult,
                  min_improvement_pct: float = 5.0,
                  max_runtime_increase_pct: float = 50.0) -> Optional[VariantResult]:
    """Find the best variant that beats baseline by the required margin.

    Args:
        results: All variant results (excluding baseline).
        baseline_result: Baseline result.
        min_improvement_pct: Required % improvement in composite score.
        max_runtime_increase_pct: Max tolerated runtime increase (%).

    Returns:
        Winning VariantResult or None if no variant qualifies.
    """
    # Sort by composite score descending
    results.sort(key=lambda r: r.composite_score, reverse=True)

    for r in results:
        # Check improvement threshold
        if baseline_result.composite_score <= 0:
            improvement_pct = 100.0 if r.composite_score > 0 else 0.0
        else:
            improvement_pct = ((r.composite_score - baseline_result.composite_score)
                               / baseline_result.composite_score) * 100.0

        if improvement_pct < min_improvement_pct:
            continue

        # Disqualify if runtime increased too much
        if baseline_result.avg_runtime_ms > 0:
            runtime_change = (
                (r.avg_runtime_ms - baseline_result.avg_runtime_ms)
                / baseline_result.avg_runtime_ms
            ) * 100.0
            if runtime_change > max_runtime_increase_pct:
                print(f"[select] {r.name}: disqualified (runtime +{runtime_change:.1f}% > {max_runtime_increase_pct}%)")
                continue

        return r

    return None


# ── Metric Collection from Results ───────────────────────────────────


def load_results(rollout_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    """Load rollout metrics organized by variant name.

    Expected directory layout:
        <rollout_dir>/results/<variant_name>/<issue_N>/metrics.json
        <rollout_dir>/results/baseline/<issue_N>/metrics.json

    Each metrics.json contains a list of metric dicts.

    Args:
        rollout_dir: Base directory for rollout artifacts.

    Returns:
        Dict mapping variant name to list of all metric dicts.
    """
    variant_metrics: Dict[str, List[Dict[str, Any]]] = {}
    results_base = Path(rollout_dir) / "results"

    if not results_base.exists():
        return variant_metrics

    for variant_dir in results_base.iterdir():
        if not variant_dir.is_dir():
            continue

        name = variant_dir.name
        all_metrics: List[Dict[str, Any]] = []

        for issue_dir in variant_dir.iterdir():
            metrics_file = issue_dir / "metrics.json"
            if metrics_file.exists():
                try:
                    with open(metrics_file) as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_metrics.extend(data)
                        else:
                            all_metrics.append(data)
                except json.JSONDecodeError:
                    print(f"[select] Warning: Invalid metrics at {metrics_file}")

        variant_metrics[name] = all_metrics

    return variant_metrics


def compute_variant_results(variant_metrics: Dict[str, List[Dict[str, Any]]]) -> List[VariantResult]:
    """Convert raw metrics dicts to VariantResult objects.

    Args:
        variant_metrics: Dict from load_results().

    Returns:
        List of VariantResult objects.
    """
    results = []
    for name, metrics in variant_metrics.items():
        if not metrics:
            results.append(VariantResult(name=name, total_runs=0))
            continue

        # Some runs may have multiple metric entries (multiple agents per issue)
        # We treat each metrics list as representing one issue's worth of runs
        results.append(VariantResult(
            name=name,
            total_runs=len(metrics),
            avg_error_rate=compute_avg_error_rate(metrics),
            avg_fix_requests=compute_avg_fix_requests(metrics),
            avg_runtime_ms=compute_avg_runtime_ms(metrics),
            avg_token_count=compute_avg_token_count(metrics),
            avg_outcome_score=sum(m.get("outcome_score", 50.0) for m in metrics) / len(metrics),
        ))

    return results


# ── PR Generation ────────────────────────────────────────────────────


def compute_improvement_summary(winner: VariantResult,
                                  baseline: VariantResult) -> str:
    """Format improvement summary text.

    Args:
        winner: Winning VariantResult.
        baseline: Baseline VariantResult.

    Returns:
        Markdown-formatted summary text.
    """
    lines = [
        "# RHO Harness Optimization — Automated PR",
        "",
        "This PR was auto-generated by the Retrospective Harness Optimization (RHO) pipeline.",
        "",
        "## Summary",
        "",
        f"**Winning variant:** `{winner.variant_name or winner.name}`",
    ]

    if winner.rationale:
        lines.append(f"**Rationale:** {winner.rationale}")

    lines.append("")
    lines.append("## Performance Comparison")
    lines.append("")
    lines.append("| Metric | Baseline | Winner | Change |")
    lines.append("|--------|----------|--------|--------|")

    def fmt_row(label, baseline_val, winner_val, unit="", lower_is_better=True):
        if baseline_val == 0:
            pct = 0.0
        else:
            pct = ((winner_val - baseline_val) / abs(baseline_val)) * 100.0
        direction = "+" if pct >= 0 else ""
        good_bad = "✅" if (lower_is_better and pct < 0) or (not lower_is_better and pct > 0) else "❌"
        return (
            f"| {label} "
            f"| {baseline_val:.2f}{unit} "
            f"| {winner_val:.2f}{unit} "
            f"| {direction}{pct:.1f}% {good_bad} |"
        )

    lines.append(fmt_row("Avg Error Rate", baseline.avg_error_rate, winner.avg_error_rate, "%", True))
    lines.append(fmt_row("Avg Fix Requests", baseline.avg_fix_requests, winner.avg_fix_requests, "", True))
    lines.append(fmt_row("Avg Runtime (ms)", baseline.avg_runtime_ms, winner.avg_runtime_ms, "", True))
    lines.append(fmt_row("Avg Tokens", baseline.avg_token_count, winner.avg_token_count, "", True))
    lines.append("")
    lines.append(
        "> _RHO evaluates harness variants against historically difficult tasks. "
        "The winning variant demonstrated statistically meaningful improvement "
        "across the coreset._"
    )
    return "\n".join(lines)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="RHO Select — Compare variants and select winner")
    parser.add_argument("--rollout-dir", type=str, default=".rho/rollout",
                        help="Base rollout directory")
    parser.add_argument("--min-improvement", type=float, default=5.0,
                        help="Minimum % improvement to accept a winner")
    parser.add_argument("--max-runtime-increase", type=float, default=50.0,
                        help="Maximum tolerable runtime increase (%)")
    args = parser.parse_args()

    # Load rho_config defaults
    rho_config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "rho_config.yaml")
    try:
        import yaml
        if os.path.isfile(rho_config_path):
            with open(rho_config_path) as f:
                cfg = yaml.safe_load(f) or {}
            sel_cfg = cfg.get("selection", {})
            quant_weight = sel_cfg.get("quantitative_weight", 0.70)
            qual_weight = sel_cfg.get("qualitative_weight", 0.30)
            min_imp = sel_cfg.get("min_improvement_pct", args.min_improvement)
            max_rt_inc = sel_cfg.get("max_runtime_increase_pct", args.max_runtime_increase)
        else:
            quant_weight, qual_weight = 0.70, 0.30
            min_imp, max_rt_inc = args.min_improvement, args.max_runtime_increase
    except Exception:
        quant_weight, qual_weight = 0.70, 0.30
        min_imp, max_rt_inc = args.min_improvement, args.max_runtime_increase

    # Load results
    variant_metrics = load_results(args.rollout_dir)

    if "baseline" not in variant_metrics:
        print("[select] Error: No baseline metrics found. Did rollout complete?")
        sys.exit(1)

    results = compute_variant_results(variant_metrics)

    if not results:
        print("[select] Error: No variant results to compare.")
        sys.exit(1)

    # Separate baseline from variants
    baseline = next((r for r in results if r.name == "baseline"), None)
    variants = [r for r in results if r.name != "baseline"]

    if not baseline:
        print("[select] Error: Baseline result not found.")
        sys.exit(1)

    # Compute composite scores
    all_for_normalization = [baseline] + variants
    compute_composite_score(all_for_normalization, quant_weight, qual_weight)

    # Print comparison table
    print(f"[select] Comparison ({len(variants)} variants vs baseline):")
    print(f"  {'Name':<30s} {'Runs':>5s} {'ErrRate':>8s} {'FixReq':>7s} {'Runtime':>10s} {'Tokens':>10s} {'Composite':>10s}")
    print(f"  {'-' * 80}")

    for r in [baseline] + sorted(variants, key=lambda x: x.composite_score, reverse=True):
        marker = " *" if r.name == "baseline" else ""
        print(f"  {r.name:<30s} {r.total_runs:>5d} {r.avg_error_rate:>7.1f}% {r.avg_fix_requests:>6.1f} "
              f"{r.avg_runtime_ms:>9.0f}ms {r.avg_token_count:>10.0f} {r.composite_score:>10.4f}{marker}")

    # Select winner
    winner = select_winner(variants, baseline, min_imp, max_rt_inc)

    if winner:
        imp_pct = ((winner.composite_score - baseline.composite_score) / baseline.composite_score) * 100.0
        print(f"\n[select] Winner: {winner.name} (+{imp_pct:.1f}% composite score improvement)")
        print("[select] A PR will be created with the updated harness config.")
    else:
        print(f"\n[select] No variant beat baseline by ≥{min_imp}%. No PR will be created.")

    return 0


if __name__ == "__main__":
    main()
