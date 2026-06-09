#!/usr/bin/env python3
"""
RHO rollout orchestrator.

Re-runs the agent on coreset tasks using variant harness configurations,
collects per-variant metrics, and saves results for comparison.

This module does NOT run the agent directly. Instead, it generates the
execution manifest (task list + config assignments) that the GitHub
Actions workflow uses to run parallel rollout jobs.

In sequential mode, it also provides a helper to run rollouts one at a time
by invoking agent_script.py with the appropriate config path.

Input: Coreset (.rho/coreset.json), variant configs (.rho/variants/)
Output: Rollout results (.rho/rollouts/)
"""

import json
import os
import sys
import subprocess
import time
import yaml
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

sys.path.insert(0, os.path.dirname(__file__))
from metrics_schema import Metrics


# ── Data Models ──────────────────────────────────────────────────────


@dataclass
class RolloutTask:
    """A single rollout task: one coreset issue + one variant config."""

    task_id: str
    issue_number: int
    issue_title: str
    variant_id: str
    config_path: str
    prompt: str


@dataclass
class RolloutResult:
    """The result of executing a single rollout task."""

    task_id: str
    variant_id: str
    issue_number: int
    success: bool
    metrics: Optional[Metrics] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    start_time: str = ""
    end_time: str = ""


# ── Rollout Planning ─────────────────────────────────────────────────


def load_coreset(path: str = ".rho/coreset.json") -> Dict:
    """Load coreset JSON."""
    with open(path) as f:
        return json.load(f)


def list_variant_configs(variants_dir: str = ".rho/variants") -> List[str]:
    """
    List variant config files.

    Returns:
        List of variant config file paths.
    """
    configs = []
    if not os.path.isdir(variants_dir):
        return configs
    for filename in sorted(os.listdir(variants_dir)):
        if filename.startswith("variant-") and filename.endswith(".yaml"):
            configs.append(os.path.join(variants_dir, filename))
    return configs


def build_rollout_tasks(
    coreset: Dict,
    variant_configs: List[str],
    baseline_config: str = "agent-config.yaml",
) -> List[RolloutTask]:
    """
    Build the list of rollout tasks: each coreset issue × each variant.

    Also includes a baseline task for each issue.

    Args:
        coreset: Coreset dict from coreset.json.
        variant_configs: List of variant config file paths.
        baseline_config: Path to the baseline config.

    Returns:
        List of RolloutTask objects.
    """
    tasks = []
    selected = coreset.get("selected", [])

    # Baseline tasks (one per coreset issue)
    for item in selected:
        task_id = f"baseline-{item['issue_number']}"
        tasks.append(RolloutTask(
            task_id=task_id,
            issue_number=item["issue_number"],
            issue_title=item["title"],
            variant_id="baseline",
            config_path=baseline_config,
            prompt=_build_task_prompt(item),
        ))

    # Variant tasks
    for config_path in variant_configs:
        variant_id = os.path.basename(config_path).replace("variant-", "").replace(".yaml", "")
        for item in selected:
            task_id = f"{variant_id}-{item['issue_number']}"
            tasks.append(RolloutTask(
                task_id=task_id,
                issue_number=item["issue_number"],
                issue_title=item["title"],
                variant_id=variant_id,
                config_path=config_path,
                prompt=_build_task_prompt(item),
            ))

    return tasks


def _build_task_prompt(item: Dict) -> str:
    """
    Build a prompt for re-running an agent on a past issue.

    Extracts the original issue context and formats it as a
    self-contained task prompt.

    Args:
        item: Coreset item dict with issue_number and title.

    Returns:
        Prompt string for agent_script.py.
    """
    # Load the trajectory to get the original issue body
    trajectory_path = f".rho/trajectories/issue-{item['issue_number']}.json"
    if os.path.isfile(trajectory_path):
        with open(trajectory_path) as f:
            trajectory = json.load(f)
        issue_body = trajectory.get("issue", {}).get("body", "")
        title = trajectory.get("issue", {}).get("title", item.get("title", ""))
    else:
        issue_body = ""
        title = item.get("title", "")

    prompt = f"""Retrospective Harness Optimization (RHO) Rollout Task

You are re-solving a past issue to evaluate a harness configuration variant.
Please solve this issue as you normally would.

Issue #{item['issue_number']}: {title}

{issue_body}

Please implement a solution for this issue."""

    return prompt


# ── Rollout Execution ────────────────────────────────────────────────


def run_rollout_task(
    task: RolloutTask,
    timeout_minutes: int = 15,
    agent_script: str = ".github/scripts/agent_script.py",
) -> RolloutResult:
    """
    Execute a single rollout task by invoking agent_script.py.

    Args:
        task: The rollout task to execute.
        timeout_minutes: Max runtime in minutes.
        agent_script: Path to agent_script.py.

    Returns:
        RolloutResult with metrics and outcome.
    """
    task_id = task.task_id
    start_time = datetime.now(timezone.utc).isoformat()
    start_ts = time.time()

    print(f"[{task_id}] Starting rollout: variant={task.variant_id}, issue=#{task.issue_number}")

    # Write prompt to a temp file
    prompt_file = f".rho/rollouts/prompt-{task_id}.txt"
    os.makedirs(".rho/rollouts", exist_ok=True)
    with open(prompt_file, "w") as f:
        f.write(task.prompt)

    # Run agent_script.py with the variant config
    env = os.environ.copy()
    env["AGENT_CONFIG_PATH"] = task.config_path

    try:
        result = subprocess.run(
            [sys.executable, agent_script, prompt_file],
            capture_output=True,
            text=True,
            timeout=timeout_minutes * 60,
            env=env,
        )

        end_ts = time.time()
        duration_ms = (end_ts - start_ts) * 1000
        end_time = datetime.now(timezone.utc).isoformat()

        # Extract metrics from final_message.txt if it exists
        metrics = None
        success = result.returncode == 0
        error = None

        if os.path.isfile("final_message.txt"):
            metrics = _extract_metrics_from_output(task_id)

        if not success:
            error = result.stderr[:500] if result.stderr else f"Exit code {result.returncode}"

        return RolloutResult(
            task_id=task_id,
            variant_id=task.variant_id,
            issue_number=task.issue_number,
            success=success,
            metrics=metrics,
            error=error,
            duration_ms=duration_ms,
            start_time=start_time,
            end_time=end_time,
        )

    except subprocess.TimeoutExpired:
        end_ts = time.time()
        duration_ms = (end_ts - start_ts) * 1000
        return RolloutResult(
            task_id=task_id,
            variant_id=task.variant_id,
            issue_number=task.issue_number,
            success=False,
            error=f"Timeout after {timeout_minutes} minutes",
            duration_ms=duration_ms,
            start_time=start_time,
            end_time=datetime.now(timezone.utc).isoformat(),
        )


def _extract_metrics_from_output(task_id: str) -> Optional[Metrics]:
    """
    Extract Metrics from the agent_script.py output files.

    agent_script.py saves metrics JSON to metrics.json and embeds it
    in final_message.txt as an HTML comment.

    Args:
        task_id: The task identifier.

    Returns:
        Metrics object if found, None otherwise.
    """
    # Try metrics.json first
    metrics_path = ".rho/rollouts/metrics.json"
    if os.path.isfile(metrics_path):
        with open(metrics_path) as f:
            try:
                return Metrics.from_json(f.read())
            except json.JSONDecodeError:
                pass

    # Try to extract from final_message.txt
    import re
    metrics_regex = re.compile(r"<!--\s*agent-metrics:\s*(.*?)\s*-->")
    if os.path.isfile("final_message.txt"):
        with open("final_message.txt") as f:
            content = f.read()
        for match in metrics_regex.finditer(content):
            try:
                return Metrics.from_json(match.group(1))
            except json.JSONDecodeError:
                continue

    return None


# ── Result Collection ────────────────────────────────────────────────


def save_rollout_results(
    results: List[RolloutResult],
    output_dir: str = ".rho/rollouts",
) -> str:
    """
    Save all rollout results to a single JSON file.

    Args:
        results: List of rollout results.
        output_dir: Directory to save results.

    Returns:
        Path to the saved results file.
    """
    os.makedirs(output_dir, exist_ok=True)
    results_path = os.path.join(output_dir, "results.json")

    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_tasks": len(results),
        "results": [],
    }

    for r in results:
        entry = {
            "task_id": r.task_id,
            "variant_id": r.variant_id,
            "issue_number": r.issue_number,
            "success": r.success,
            "duration_ms": r.duration_ms,
            "start_time": r.start_time,
            "end_time": r.end_time,
        }
        if r.metrics:
            entry["metrics"] = r.metrics.to_dict()
        if r.error:
            entry["error"] = r.error
        data["results"].append(entry)

    with open(results_path, "w") as f:
        json.dump(data, f, indent=2)

    return results_path


def aggregate_by_variant(results: List[RolloutResult]) -> Dict[str, Dict]:
    """
    Aggregate rollout results by variant_id.

    Args:
        results: List of rollout results.

    Returns:
        Dict mapping variant_id to aggregated metrics.
    """
    aggregated = {}

    for r in results:
        variant = r.variant_id
        if variant not in aggregated:
            aggregated[variant] = {
                "tasks": 0,
                "successes": 0,
                "errors": 0,
                "total_duration_ms": 0.0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tool_calls": 0,
                "total_error_rate_sum": 0.0,
                "total_fix_requests": 0,
                "total_runtime_ms": 0.0,
            }

        agg = aggregated[variant]
        agg["tasks"] += 1
        if r.success:
            agg["successes"] += 1
        else:
            agg["errors"] += 1
        agg["total_duration_ms"] += r.duration_ms
        agg["total_runtime_ms"] += r.duration_ms

        if r.metrics:
            agg["total_input_tokens"] += r.metrics.input_tokens
            agg["total_output_tokens"] += r.metrics.output_tokens
            agg["total_tool_calls"] += r.metrics.nb_tool_calls
            agg["total_error_rate_sum"] += r.metrics.tool_call_error_rate
            agg["total_fix_requests"] += r.metrics.review_fix_requests

    # Compute averages
    for variant, agg in aggregated.items():
        n = agg["tasks"] or 1
        agg["avg_duration_ms"] = agg["total_duration_ms"] / n
        agg["avg_error_rate"] = agg["total_error_rate_sum"] / n
        agg["avg_tool_calls"] = agg["total_tool_calls"] / n
        agg["avg_runtime_ms"] = agg["total_runtime_ms"] / n

    return aggregated


# ── Manifest Generation (for GitHub Actions) ────────────────────────


def generate_manifest(
    tasks: List[RolloutTask],
    output_path: str = ".rho/rollouts/manifest.json",
) -> str:
    """
    Generate a rollout manifest for GitHub Actions parallel execution.

    Args:
        tasks: List of rollout tasks.
        output_path: Path to save the manifest.

    Returns:
        Path to the saved manifest.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    manifest = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_tasks": len(tasks),
        "tasks": [
            {
                "task_id": t.task_id,
                "issue_number": t.issue_number,
                "issue_title": t.issue_title,
                "variant_id": t.variant_id,
                "config_path": t.config_path,
                "prompt_file": f".rho/rollouts/prompt-{t.task_id}.txt",
            }
            for t in tasks
        ],
    }

    # Write prompt files
    for t in tasks:
        prompt_file = f".rho/rollouts/prompt-{t.task_id}.txt"
        with open(prompt_file, "w") as f:
            f.write(t.prompt)

    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=2)

    return output_path


# ── CLI Entry Point ──────────────────────────────────────────────────


def main():
    """
    CLI: Plan and optionally execute rollouts.

    Environment Variables:
        RHO_EXECUTE: Set to "true" to execute rollouts (default: plan only)
        RHO_TIMEOUT: Timeout per task in minutes (default: 15)
    """
    coreset = load_coreset()
    variant_configs = list_variant_configs()

    if not variant_configs:
        print("Error: No variant configs found. Run harness_proposer.py first.")
        sys.exit(1)

    tasks = build_rollout_tasks(coreset, variant_configs)
    print(f"Built {len(tasks)} rollout tasks ({len(variant_configs) + 1} configs × {coreset['coreset_size']} issues)")

    # Generate manifest for GitHub Actions
    manifest_path = generate_manifest(tasks)
    print(f"Manifest saved to {manifest_path}")

    # Execute if requested
    execute = os.environ.get("RHO_EXECUTE", "false").lower() == "true"
    if not execute:
        print("\nPlan mode only. Set RHO_EXECUTE=true to run rollouts.")
        return

    timeout = int(os.environ.get("RHO_TIMEOUT", "15"))
    results = []

    for task in tasks:
        result = run_rollout_task(task, timeout_minutes=timeout)
        results.append(result)
        status = "✓" if result.success else "✗"
        print(f"  {status} {result.task_id}: {result.duration_ms:.0f}ms")

    results_path = save_rollout_results(results)
    print(f"\nResults saved to {results_path}")

    # Print per-variant summary
    aggregated = aggregate_by_variant(results)
    print("\nPer-variant summary:")
    for variant, agg in sorted(aggregated.items()):
        print(f"\n  {variant}:")
        print(f"    Tasks: {agg['tasks']} (successes={agg['successes']}, errors={agg['errors']})")
        print(f"    Avg duration: {agg['avg_duration_ms']:.0f}ms")
        print(f"    Avg error rate: {agg['avg_error_rate']:.1f}%")
        print(f"    Avg tool calls: {agg['avg_tool_calls']:.1f}")


if __name__ == "__main__":
    main()
