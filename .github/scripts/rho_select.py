#!/usr/bin/env python3
"""
RHO selection and PR creation.

Compares baseline vs variant rollout results using weighted scoring,
selects the best performing variant, and creates a PR with the
winning harness configuration.

Input: Rollout results (.rho/rollouts/results.json)
Output: PR with updated agent-config.yaml
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))


# ── Scoring ──────────────────────────────────────────────────────────


@dataclass
class VariantScore:
    """Score breakdown for a single variant."""

    variant_id: str
    overall_score: float
    metrics_scores: Dict[str, float]
    improvements: int
    regressions: int


def load_rollout_results(path: str = ".rho/rollouts/results.json") -> Dict:
    """Load rollout results from JSON file."""
    with open(path) as f:
        return json.load(f)


def load_rho_config(path: str = "rho_config.yaml") -> Dict:
    """Load RHO configuration from YAML file."""
    import yaml

    if not os.path.isfile(path):
        return {}

    with open(path) as f:
        return yaml.safe_load(f) or {}


def compute_variant_scores(
    results_data: Dict,
    weights: Optional[Dict[str, float]] = None,
) -> List[VariantScore]:
    """
    Compute weighted scores for each variant against the baseline.

    Lower error rate, runtime, and tokens are better. Higher scores win.

    Args:
        results_data: Rollout results dict from results.json.
        weights: Optional scoring weights from rho_config.yaml.

    Returns:
        List of VariantScore sorted by overall_score (descending).
    """
    if weights is None:
        weights = {
            "error_rate_reduction": 0.35,
            "runtime_reduction": 0.20,
            "token_reduction": 0.15,
            "fix_request_reduction": 0.20,
            "tool_call_reduction": 0.10,
        }

    # Aggregate metrics by variant
    aggregated = {}
    for result in results_data.get("results", []):
        variant = result.get("variant_id", "unknown")
        if variant not in aggregated:
            aggregated[variant] = {
                "tasks": 0,
                "successes": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tool_calls": 0,
                "total_error_rate_sum": 0.0,
                "total_fix_requests": 0,
                "total_duration_ms": 0.0,
            }
        agg = aggregated[variant]
        agg["tasks"] += 1
        if result.get("success", False):
            agg["successes"] += 1
        metrics = result.get("metrics", {})
        agg["total_input_tokens"] += metrics.get("input_tokens", 0)
        agg["total_output_tokens"] += metrics.get("output_tokens", 0)
        agg["total_tool_calls"] += metrics.get("nb_tool_calls", 0)
        agg["total_error_rate_sum"] += metrics.get("tool_call_error_rate", 0.0)
        agg["total_fix_requests"] += metrics.get("review_fix_requests", 0)
        agg["total_duration_ms"] += result.get("duration_ms", 0.0)

    # Compute averages
    for variant, agg in aggregated.items():
        n = agg["tasks"] or 1
        agg["avg_error_rate"] = agg["total_error_rate_sum"] / n
        agg["avg_tool_calls"] = agg["total_tool_calls"] / n
        agg["avg_tokens"] = (agg["total_input_tokens"] + agg["total_output_tokens"]) / n
        agg["avg_duration_ms"] = agg["total_duration_ms"] / n
        agg["avg_fix_requests"] = agg["total_fix_requests"] / n

    # Find baseline metrics for comparison
    baseline = aggregated.get("baseline")
    if not baseline:
        # If no baseline, score each variant independently (lower is better → invert)
        print("Warning: No baseline results found. Scoring variants independently.")
        return _score_independently(aggregated, weights)

    # Score each variant relative to baseline
    scores = []
    for variant, agg in aggregated.items():
        if variant == "baseline":
            # Baseline gets a score of 0.5 (neutral)
            scores.append(VariantScore(
                variant_id="baseline",
                overall_score=0.5,
                metrics_scores={},
                improvements=0,
                regressions=0,
            ))
            continue

        improvements = 0
        regressions = 0
        metrics_scores = {}

        # Error rate reduction (lower is better)
        if baseline["avg_error_rate"] > 0:
            error_change = (baseline["avg_error_rate"] - agg["avg_error_rate"]) / baseline["avg_error_rate"]
        else:
            error_change = 0.0 if agg["avg_error_rate"] == 0 else -1.0
        error_score = max(0, min(1, 0.5 + error_change))
        metrics_scores["error_rate"] = round(error_score, 4)
        if error_change > 0:
            improvements += 1
        elif error_change < 0:
            regressions += 1

        # Runtime reduction (lower is better)
        if baseline["avg_duration_ms"] > 0:
            runtime_change = (baseline["avg_duration_ms"] - agg["avg_duration_ms"]) / baseline["avg_duration_ms"]
        else:
            runtime_change = 0.0
        runtime_score = max(0, min(1, 0.5 + runtime_change))
        metrics_scores["runtime"] = round(runtime_score, 4)
        if runtime_change > 0:
            improvements += 1
        elif runtime_change < 0:
            regressions += 1

        # Token reduction (lower is better)
        if baseline["avg_tokens"] > 0:
            token_change = (baseline["avg_tokens"] - agg["avg_tokens"]) / baseline["avg_tokens"]
        else:
            token_change = 0.0
        token_score = max(0, min(1, 0.5 + token_change))
        metrics_scores["tokens"] = round(token_score, 4)
        if token_change > 0:
            improvements += 1
        elif token_change < 0:
            regressions += 1

        # Fix request reduction (lower is better)
        if baseline["avg_fix_requests"] > 0:
            fix_change = (baseline["avg_fix_requests"] - agg["avg_fix_requests"]) / baseline["avg_fix_requests"]
        else:
            fix_change = 0.0 if agg["avg_fix_requests"] == 0 else -1.0
        fix_score = max(0, min(1, 0.5 + fix_change))
        metrics_scores["fix_requests"] = round(fix_score, 4)
        if fix_change > 0:
            improvements += 1
        elif fix_change < 0:
            regressions += 1

        # Tool call reduction (lower is better)
        if baseline["avg_tool_calls"] > 0:
            tool_change = (baseline["avg_tool_calls"] - agg["avg_tool_calls"]) / baseline["avg_tool_calls"]
        else:
            tool_change = 0.0
        tool_score = max(0, min(1, 0.5 + tool_change))
        metrics_scores["tool_calls"] = round(tool_score, 4)
        if tool_change > 0:
            improvements += 1
        elif tool_change < 0:
            regressions += 1

        # Weighted overall score
        overall = (
            weights.get("error_rate_reduction", 0.35) * error_score
            + weights.get("runtime_reduction", 0.20) * runtime_score
            + weights.get("token_reduction", 0.15) * token_score
            + weights.get("fix_request_reduction", 0.20) * fix_score
            + weights.get("tool_call_reduction", 0.10) * tool_score
        )

        scores.append(VariantScore(
            variant_id=variant,
            overall_score=round(overall, 4),
            metrics_scores=metrics_scores,
            improvements=improvements,
            regressions=regressions,
        ))

    scores.sort(key=lambda s: s.overall_score, reverse=True)
    return scores


def _score_independently(
    aggregated: Dict[str, Dict],
    weights: Dict[str, float],
) -> List[VariantScore]:
    """
    Score variants independently when no baseline is available.

    Uses inverse normalization: lower error rates, runtime, and tokens
    get higher scores.

    Args:
        aggregated: Per-variant aggregated metrics.
        weights: Scoring weights.

    Returns:
        List of VariantScore sorted by score (descending).
    """
    variants = list(aggregated.keys())
    if not variants:
        return []

    # Collect values for normalization
    error_rates = [aggregated[v]["avg_error_rate"] for v in variants]
    durations = [aggregated[v]["avg_duration_ms"] for v in variants]
    tokens = [aggregated[v]["avg_tokens"] for v in variants]
    fix_requests = [aggregated[v]["avg_fix_requests"] for v in variants]
    tool_calls = [aggregated[v]["avg_tool_calls"] for v in variants]

    # Inverse normalize (lower raw value = higher score)
    def _inverse_normalize(values):
        if not values:
            return []
        min_v = min(values)
        max_v = max(values)
        if max_v == min_v:
            return [0.5] * len(values)
        return [1.0 - (v - min_v) / (max_v - min_v) for v in values]

    norm_error = _inverse_normalize(error_rates)
    norm_duration = _inverse_normalize(durations)
    norm_tokens = _inverse_normalize(tokens)
    norm_fix = _inverse_normalize(fix_requests)
    norm_tools = _inverse_normalize(tool_calls)

    scores = []
    for i, variant in enumerate(variants):
        overall = (
            weights.get("error_rate_reduction", 0.35) * norm_error[i]
            + weights.get("runtime_reduction", 0.20) * norm_duration[i]
            + weights.get("token_reduction", 0.15) * norm_tokens[i]
            + weights.get("fix_request_reduction", 0.20) * norm_fix[i]
            + weights.get("tool_call_reduction", 0.10) * norm_tools[i]
        )
        scores.append(VariantScore(
            variant_id=variant,
            overall_score=round(overall, 4),
            metrics_scores={},
            improvements=0,
            regressions=0,
        ))

    scores.sort(key=lambda s: s.overall_score, reverse=True)
    return scores


# ── Selection ────────────────────────────────────────────────────────


def select_winner(
    scores: List[VariantScore],
    min_improvement_pct: float = 5.0,
    min_improved_metrics: int = 2,
) -> Optional[VariantScore]:
    """
    Select the winning variant based on score and improvement thresholds.

    Args:
        scores: List of variant scores sorted by score (descending).
        min_improvement_pct: Minimum percentage improvement over baseline.
        min_improved_metrics: Minimum number of metrics that must improve.

    Returns:
        The winning VariantScore, or None if no variant meets thresholds.
    """
    if not scores:
        return None

    winner = scores[0]

    # Skip baseline from winning
    if winner.variant_id == "baseline":
        return None

    # Check improvement thresholds
    baseline_score = 0.5  # Baseline always scores 0.5
    improvement_pct = ((winner.overall_score - baseline_score) / baseline_score) * 100

    if improvement_pct < min_improvement_pct:
        print(f"No variant exceeded {min_improvement_pct}% improvement threshold.")
        print(f"Best variant '{winner.variant_id}' improved by {improvement_pct:.1f}%")
        return None

    if winner.improvements < min_improved_metrics:
        print(f"Variant '{winner.variant_id}' only improved {winner.improvements} metrics "
              f"(minimum: {min_improved_metrics})")
        return None

    return winner


# ── PR Creation ──────────────────────────────────────────────────────


def _gh_post(url: str, token: str, data: Dict) -> Dict:
    """POST to GitHub API."""
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "git-flow-ai-rho")

    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_default_branch(repo: str, token: str) -> str:
    """Get the default branch name for a repository."""
    url = f"https://api.github.com/repos/{repo}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "git-flow-ai-rho")

    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data.get("default_branch", "main")


def create_rho_pr(
    repo: str,
    token: str,
    winner: VariantScore,
    variant_config_path: str,
    baseline_config_path: str = "agent-config.yaml",
    scores: Optional[List[VariantScore]] = None,
    rho_config: Optional[Dict] = None,
) -> str:
    """
    Create a PR with the winning harness configuration.

    Args:
        repo: Repository in format owner/repo.
        token: GitHub personal access token.
        winner: The winning VariantScore.
        variant_config_path: Path to the winning variant config file.
        baseline_config_path: Path to the baseline config for diff reference.
        scores: Optional list of all scores for the PR description.

    Returns:
        The HTML URL of the created PR.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch_name = f"rho/optimization-{timestamp}"
    default_branch = _get_default_branch(repo, token)

    # Read the winning config
    with open(variant_config_path) as f:
        variant_content = f.read()

    # Read baseline for comparison
    baseline_content = ""
    if os.path.isfile(baseline_config_path):
        with open(baseline_config_path) as f:
            baseline_content = f.read()

    # Load variant metadata if available
    meta_path = variant_config_path.replace(".yaml", ".json")
    rationale = ""
    mutations_desc = ""
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        rationale = meta.get("rationale", "")
        mutations = meta.get("mutations", [])
        mutations_desc = "\n".join(
            f"- **{m.get('type', '?')}**: `{m.get('agent', '?')}.{m.get('field', '?')}` "
            f"= `{m.get('old_value', '?')}` → `{m.get('new_value', '?')}` "
            f"({m.get('reason', '')})"
            for m in mutations
        )

    # Build PR description
    pr_body = f"""# {(rho_config or {}).get('github', {}).get('pr_title_prefix', '[RHO]')} Harness Optimization Proposal

## Summary

Variant **{winner.variant_id}** was selected as the best performer after Retrospective Harness Optimization (RHO) evaluation against {len(scores) - 1 if scores else '?'} other variant(s) plus baseline.

**Overall Score:** {winner.overall_score:.4f} (improvements: {winner.improvements}, regressions: {winner.regressions})

## Rationale

{rationale}

## Proposed Mutations

{mutations_desc}

## Score Comparison

| Variant | Score | Improvements | Regressions |
|---------|-------|-------------|-------------|
"""

    if scores:
        for s in scores:
            marker = " ← WINNER" if s.variant_id == winner.variant_id else ""
            pr_body += f"| {s.variant_id} | {s.overall_score:.4f} | {s.improvements} | {s.regressions} |{marker}\n"

    pr_body += f"""
## Metrics Breakdown

"""

    if winner.metrics_scores:
        pr_body += "| Metric | Score |\n|--------|-------|\n"
        for metric, score in winner.metrics_scores.items():
            direction = "↑" if score > 0.5 else "↓" if score < 0.5 else "="
            pr_body += f"| {metric} | {score:.4f} {direction} |\n"

    pr_body += """

---

> _This PR was auto-generated by the RHO pipeline on behalf of the agent._
"""

    # Create branch via API (using git tree + commit)
    print(f"Creating branch '{branch_name}' with updated agent-config.yaml...")

    # Step 1: Get the latest commit SHA of the default branch
    url = f"https://api.github.com/repos/{repo}/git/ref/heads/{default_branch}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "git-flow-ai-rho")

    with urllib.request.urlopen(req) as response:
        ref_data = json.loads(response.read().decode("utf-8"))
    base_sha = ref_data["object"]["sha"]

    # Step 2: Create a blob for the new config
    blob_url = f"https://api.github.com/repos/{repo}/git/blobs"
    blob_data = _gh_post(blob_url, token, {
        "content": variant_content,
        "encoding": "utf-8",
    })
    blob_sha = blob_data["sha"]

    # Step 3: Get the tree of the base commit
    commit_url = f"https://api.github.com/repos/{repo}/git/commits/{base_sha}"
    req = urllib.request.Request(commit_url)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "git-flow-ai-rho")

    with urllib.request.urlopen(req) as response:
        commit_data = json.loads(response.read().decode("utf-8"))
    base_tree_sha = commit_data["tree"]["sha"]

    # Step 4: Create a new tree with the updated file
    tree_url = f"https://api.github.com/repos/{repo}/git/trees"
    tree_data = _gh_post(tree_url, token, {
        "tree": [
            {
                "path": "agent-config.yaml",
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha,
            }
        ],
        "base_tree": base_tree_sha,
    })
    tree_sha = tree_data["sha"]

    # Step 5: Create a commit
    commit_url = f"https://api.github.com/repos/{repo}/git/commits"
    commit_data = _gh_post(commit_url, token, {
        "message": f"[RHO] Optimize harness: {winner.variant_id} (score={winner.overall_score:.4f})",
        "tree": tree_sha,
        "parents": [base_sha],
    })
    commit_sha = commit_data["sha"]

    # Step 6: Create the branch
    branch_url = f"https://api.github.com/repos/{repo}/git/refs"
    _gh_post(branch_url, token, {
        "ref": f"refs/heads/{branch_name}",
        "sha": commit_sha,
    })

    # Step 7: Create the PR
    pr_url = f"https://api.github.com/repos/{repo}/pulls"
    pr_data = _gh_post(pr_url, token, {
        "title": f"[RHO] Harness Optimization: {winner.variant_id}",
        "body": pr_body,
        "head": branch_name,
        "base": default_branch,
    })

    print(f"PR created: {pr_data.get('html_url', 'URL not available')}")
    return pr_data.get("html_url", "")


# ── CLI Entry Point ──────────────────────────────────────────────────


def main():
    """CLI: Load rollout results, select winner, optionally create PR."""
    config = load_rho_config()

    # Load results
    results_path = os.environ.get("RHO_RESULTS", ".rho/rollouts/results.json")
    if not os.path.isfile(results_path):
        print(f"Error: Results file not found: {results_path}")
        sys.exit(1)

    results_data = load_rollout_results(results_path)

    # Compute scores
    selection_config = config.get("selection", {})
    weights = selection_config.get("weights", {})
    scores = compute_variant_scores(results_data, weights=weights if weights else None)

    print("\nVariant Scores:")
    print("-" * 60)
    for s in scores:
        marker = " ← BEST" if s == scores[0] and s.variant_id != "baseline" else ""
        print(f"  {s.variant_id}: {s.overall_score:.4f} "
              f"(improvements={s.improvements}, regressions={s.regressions}){marker}")

    # Select winner
    min_improvement = selection_config.get("min_improvement_pct", 5.0)
    min_metrics = selection_config.get("min_improved_metrics", 2)
    winner = select_winner(scores, min_improvement_pct=min_improvement, min_improved_metrics=min_metrics)

    if not winner:
        print("\nNo variant met the improvement thresholds. No PR will be created.")

        # Save analysis for review anyway
        analysis = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scores": [
                {
                    "variant_id": s.variant_id,
                    "overall_score": s.overall_score,
                    "metrics_scores": s.metrics_scores,
                    "improvements": s.improvements,
                    "regressions": s.regressions,
                }
                for s in scores
            ],
            "winner": None,
            "reason": "No variant met improvement thresholds",
        }
        with open(".rho/selection.json", "w") as f:
            json.dump(analysis, f, indent=2)
        print("Analysis saved to .rho/selection.json")
        return

    print(f"\nWinner: {winner.variant_id} (score={winner.overall_score:.4f})")

    # Find variant config file
    variant_dir = ".rho/variants"
    variant_config = os.path.join(variant_dir, f"variant-{winner.variant_id}.yaml")
    if not os.path.isfile(variant_config):
        print(f"Error: Variant config not found: {variant_config}")
        sys.exit(1)

    # Save selection analysis
    analysis = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scores": [
            {
                "variant_id": s.variant_id,
                "overall_score": s.overall_score,
                "metrics_scores": s.metrics_scores,
                "improvements": s.improvements,
                "regressions": s.regressions,
            }
            for s in scores
        ],
        "winner": {
            "variant_id": winner.variant_id,
            "overall_score": winner.overall_score,
        },
    }
    os.makedirs(".rho", exist_ok=True)
    with open(".rho/selection.json", "w") as f:
        json.dump(analysis, f, indent=2)

    # Create PR if requested
    create_pr = os.environ.get("RHO_CREATE_PR", "false").lower() == "true"
    if not create_pr:
        print("\nSelection complete. Set RHO_CREATE_PR=true to create a PR.")
        return

    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not token or not repo:
        print("Error: GITHUB_TOKEN and GITHUB_REPOSITORY required for PR creation.")
        sys.exit(1)

    pr_url = create_rho_pr(
        repo=repo,
        token=token,
        winner=winner,
        variant_config_path=variant_config,
        scores=scores,
    )
    print(f"\nPR created: {pr_url}")


if __name__ == "__main__":
    main()
