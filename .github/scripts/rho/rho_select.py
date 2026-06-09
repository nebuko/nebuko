#!/usr/bin/env python3
"""
RHO select — compare rollout results and select the best harness variant.

Compares baseline vs variant configs across coreset tasks using both
quantitative metrics (error rate, fix requests, runtime) and qualitative
analysis (self-validation via agent).

Output: Creates a PR with the winning harness config update.

Port/Adapter design:
    PRBuilder  —  Adapter (GitHub API for PR creation)
    VariantComparator —  Core logic (metric comparison + scoring)
"""

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(__file__) + "/..")

from metrics_schema import Metrics
from rho.harness_proposer import VariantProposal


# ── Data Models ───────────────────────────────────────────────────────

@dataclass
class VariantScore:
    """Aggregated score for a single variant across all tasks."""

    variant_id: str
    total_score: float
    task_scores: List[float] = field(default_factory=list)
    avg_error_rate: float = 0.0
    total_fix_requests: int = 0
    avg_tool_calls: float = 0.0
    avg_runtime_ms: float = 0.0
    avg_tokens: float = 0.0
    success_rate: float = 0.0
    num_tasks: int = 0


@dataclass
class SelectionResult:
    """Final selection result with winner and comparison data."""

    winner_variant_id: str
    winner_description: str
    variant_scores: List[VariantScore]
    improvement_pct: float
    comparison_summary: str
    winning_config: Optional[Dict[str, Any]] = None


# ── Comparison Logic ──────────────────────────────────────────────────

def _load_evaluation_weights(rho_config_path: str) -> Dict[str, float]:
    """Load evaluation weights from rho_config.yaml."""
    if not os.path.exists(rho_config_path):
        return {
            "error_rate_reduction": 0.35,
            "fix_request_reduction": 0.25,
            "efficiency_gain": 0.20,
            "cost_reduction": 0.20,
        }

    with open(rho_config_path) as f:
        config = yaml.safe_load(f)

    if config and "evaluation" in config and "weights" in config["evaluation"]:
        return config["evaluation"]["weights"]
    return _load_evaluation_weights("")


def compute_variant_scores(
    rollout_results: List[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> List[VariantScore]:
    """
    Compute aggregated scores for each variant from rollout results.

    Args:
        rollout_results: List of result dicts (from rho_rollout.py JSON output).
        weights: Optional evaluation weight override.

    Returns:
        List of VariantScore sorted by total_score descending.
    """
    if weights is None:
        weights = _load_evaluation_weights("")

    # Group results by variant_id
    variant_results: Dict[str, List[Dict[str, Any]]] = {}
    for batch in rollout_results:
        for result in batch.get("results", []):
            vid = result.get("variant_id", "unknown")
            if vid not in variant_results:
                variant_results[vid] = []
            variant_results[vid].append(result)

    scores: list[VariantScore] = []

    for variant_id, results in variant_results.items():
        error_rates = []
        fix_requests = []
        tool_calls = []
        runtimes = []
        tokens = []
        successes = 0

        for r in results:
            metrics = r.get("metrics", {})
            if metrics:
                error_rates.append(metrics.get("tool_call_error_rate", 0))
                fix_requests.append(metrics.get("review_fix_requests", 0))
                tool_calls.append(metrics.get("nb_tool_calls", 0))
                runtimes.append(metrics.get("agent_runtime_ms", 0))
                tokens.append(metrics.get("input_tokens", 0) + metrics.get("output_tokens", 0))

            if r.get("success", False):
                successes += 1

        num_tasks = len(results)
        avg_error = sum(error_rates) / len(error_rates) if error_rates else 0
        total_fix = sum(fix_requests)
        avg_calls = sum(tool_calls) / len(tool_calls) if tool_calls else 0
        avg_runtime = sum(runtimes) / len(runtimes) if runtimes else 0
        avg_tok = sum(tokens) / len(tokens) if tokens else 0
        success_rate = successes / num_tasks if num_tasks > 0 else 0

        # Score: lower error rate, fewer fix requests, better efficiency = better
        # Normalize to [0, 1] per dimension (inverted: higher = better)
        # For simplicity, use raw weighted score
        score = (
            (1 - min(avg_error / 100, 1.0)) * weights.get("error_rate_reduction", 0.35)
            + max(0, 1 - total_fix / 10) * weights.get("fix_request_reduction", 0.25)
            + success_rate * weights.get("efficiency_gain", 0.20)
            + max(0, 1 - avg_tok / 100000) * weights.get("cost_reduction", 0.20)
        )

        scores.append(VariantScore(
            variant_id=variant_id,
            total_score=score,
            task_scores=[score / num_tasks] * num_tasks,
            avg_error_rate=avg_error,
            total_fix_requests=total_fix,
            avg_tool_calls=avg_calls,
            avg_runtime_ms=avg_runtime,
            avg_tokens=avg_tok,
            success_rate=success_rate,
            num_tasks=num_tasks,
        ))

    scores.sort(key=lambda s: s.total_score, reverse=True)
    return scores


def compute_improvement(baseline_score: VariantScore, winner_score: VariantScore) -> float:
    """
    Compute percentage improvement of winner over baseline.

    Returns:
        Improvement percentage (positive = improvement, negative = regression).
    """
    if baseline_score.total_score == 0:
        return 0.0

    return ((winner_score.total_score - baseline_score.total_score) / baseline_score.total_score) * 100


def select_best_variant(
    rollout_results_path: str,
    rho_config_path: str,
    proposals: Optional[List[VariantProposal]] = None,
) -> Optional[SelectionResult]:
    """
    Load rollout results, compute scores, and select the best variant.

    Args:
        rollout_results_path: Path to rollout results JSON file.
        rho_config_path: Path to RHO config.
        proposals: Optional list of variant proposals for descriptions.

    Returns:
        SelectionResult with winner info, or None if no valid results.
    """
    if not os.path.exists(rollout_results_path):
        print(f"Error: Rollout results file not found: {rollout_results_path}")
        return None

    with open(rollout_results_path) as f:
        data = json.load(f)

    rollout_results = data.get("batches", [])
    if not rollout_results:
        print("No rollout results to evaluate.")
        return None

    weights = _load_evaluation_weights(rho_config_path)
    scores = compute_variant_scores(rollout_results, weights=weights)

    if len(scores) < 2:
        print("Need at least 2 variants to compare.")
        return None

    # First score is winner (sorted by total_score desc)
    winner = scores[0]
    # Baseline is typically the one with variant_id "baseline"
    baseline = next((s for s in scores if s.variant_id == "baseline"), scores[-1])

    improvement = compute_improvement(baseline, winner)

    # Build description map from proposals
    proposal_desc_map = {}
    if proposals:
        for p in proposals:
            proposal_desc_map[p.variant_id] = p.description

    # Build comparison summary
    summary_lines = []
    summary_lines.append(f"# RHO Selection Results")
    summary_lines.append("")
    summary_lines.append(f"**Winner:** `{winner.variant_id}` (score: {winner.total_score:.3f})")
    summary_lines.append(f"**Improvement:** {improvement:+.1f}% over baseline")
    summary_lines.append("")
    summary_lines.append("## Variant Comparison")
    summary_lines.append("")
    summary_lines.append("| Variant | Score | Error Rate | Fix Requests | Avg Tool Calls | Success Rate |")
    summary_lines.append("|---------|-------|------------|--------------|----------------|--------------|")

    for s in scores:
        desc = proposal_desc_map.get(s.variant_id, "")
        summary_lines.append(
            f"| {s.variant_id} "
            f"| {s.total_score:.3f} "
            f"| {s.avg_error_rate:.1f}% "
            f"| {s.total_fix_requests} "
            f"| {s.avg_tool_calls:.1f} "
            f"| {s.success_rate:.0%} |"
        )

    if desc:
        summary_lines.append("")
        summary_lines.append(f"**Winner Strategy:** {desc}")

    summary = "\n".join(summary_lines)

    return SelectionResult(
        winner_variant_id=winner.variant_id,
        winner_description=proposal_desc_map.get(winner.variant_id, ""),
        variant_scores=scores,
        improvement_pct=improvement,
        comparison_summary=summary,
    )


# ── PR Builder (Adapter) ─────────────────────────────────────────────

def _find_git_user():
    """Get git user credentials."""
    import subprocess

    name = "openhands"
    email = "openhands@all-hands.dev"

    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            name = result.stdout.strip()
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            email = result.stdout.strip()
    except Exception:
        pass

    return name, email


class PRBuilder:
    """
    Adapter for creating GitHub PRs via the GitHub CLI or API.
    """

    def __init__(self, token: str, repo: str):
        self._token = token
        self._repo = repo

    def create_pr(
        self,
        title: str,
        body: str,
        branch_name: str,
        files_to_commit: Dict[str, str],
    ) -> Optional[str]:
        """
        Create a PR with the given changes.

        Args:
            title: PR title.
            body: PR body (markdown).
            branch_name: Branch to create the PR from.
            files_to_commit: Dict of filepath -> content for files to commit.

        Returns:
            PR URL if successful, None otherwise.
        """
        import subprocess

        # Configure git user
        name, email = _find_git_user()
        subprocess.run(["git", "config", "user.name", name], check=False)
        subprocess.run(["git", "config", "user.email", email], check=False)

        # Create branch
        subprocess.run(["git", "checkout", "-b", branch_name], check=True)

        # Write files
        for filepath, content in files_to_commit.items():
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            subprocess.run(["git", "add", filepath], check=True)

        # Commit
        commit_msg = f"RHO: {title}"
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            check=True
        )

        # Push
        subprocess.run(
            ["git", "push", "origin", branch_name, "--force"],
            check=True
        )

        # Create PR via gh CLI
        result = subprocess.run(
            [
                "gh", "pr", "create",
                "--title", title,
                "--body", body,
                "--head", branch_name,
                "--base", "main",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0:
            # PR URL is typically the first line of output
            return result.stdout.strip().split("\n")[0]

        print(f"Failed to create PR: {result.stderr}")
        return None


def main():
    """CLI entry point for RHO selection."""
    rollout_results_path = os.environ.get("RHO_ROLLOUT_RESULTS", ".rho/rollouts/results.json")
    rho_config_path = os.environ.get("RHO_CONFIG_PATH", ".github/scripts/rho/rho_config.yaml")

    result = select_best_variant(
        rollout_results_path=rollout_results_path,
        rho_config_path=rho_config_path,
    )

    if result:
        print(result.comparison_summary)
        print(f"\nWinner: {result.winner_variant_id}")
        print(f"Improvement: {result.improvement_pct:+.1f}%")
    else:
        print("No valid selection result.")
        sys.exit(1)


if __name__ == "__main__":
    main()
