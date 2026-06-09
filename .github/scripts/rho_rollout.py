#!/usr/bin/env python3
"""
RHO Rollout — re-run coreset tasks with variant harness configs.

Orchestrates parallel re-execution of selected coreset tasks using
different harness configurations (baseline + variants). Collects
per-variant metrics for comparison.

This script is designed to be called from the rho.yml workflow. It
invokes agent_script.py for each task × variant combination, then
aggregates the resulting metrics.

Environment Variables:
    AGENT_SCRIPT_PATH: Path to agent_script.py (default: .github/scripts/agent_script.py)
    RHO_ARCHIVE_DIR: Path to trajectory archive (default: .rho/trajectories)
    RHO_VARIANTS_DIR: Path to variant configs (default: .rho/variants)
    RHO_ROLLOUT_DIR: Path to store rollout results (default: .rho/rollout)
    LLM_API_KEY: API key for LLM (required for actual agent runs)
    LLM_BASE_URL: Optional base URL for LLM API

Usage:
    python rho_rollout.py
"""

import json
import os
import sys
import subprocess
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
from trajectory_archive import Trajectory, trajectory_to_dict
from coreset_selector import select_coreset, load_rho_config


@dataclass
class RolloutResult:
    """Result of a single task × variant rollout."""

    task_issue_number: int
    variant_id: str
    config_path: str
    success: bool = False
    error: Optional[str] = None
    metrics_json: Optional[str] = None
    # Extracted metrics for comparison
    error_rate: float = 0.0
    fix_requests: int = 0
    tool_calls: int = 0
    runtime_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


def load_variant_configs(variants_dir: str) -> List[Dict[str, Any]]:
    """Load all variant config files from the variants directory.

    Args:
        variants_dir: Path to .rho/variants/ directory.

    Returns:
        List of dicts with variant_id, config_path, and description.
    """
    variants = []

    if not os.path.isdir(variants_dir):
        return variants

    # Load proposal summary for descriptions
    proposals_path = os.path.join(variants_dir, "proposals.json")
    proposals = {}
    if os.path.isfile(proposals_path):
        with open(proposals_path) as f:
            data = json.load(f)
            for mut in data.get("mutations", []):
                proposals[mut["variant_id"]] = mut

    # Find variant YAML files
    for filename in sorted(os.listdir(variants_dir)):
        if not filename.startswith("variant_") or not filename.endswith(".yaml"):
            continue
        variant_id = filename.replace("variant_", "").replace(".yaml", "")
        filepath = os.path.join(variants_dir, filename)
        variants.append({
            "variant_id": variant_id,
            "config_path": filepath,
            "description": proposals.get(variant_id, {}).get("description", ""),
        })

    return variants


def build_task_prompt(traj: Trajectory) -> str:
    """Build a prompt from a trajectory's original issue context.

    Reconstructs the original task the agent was asked to solve,
    so it can be re-run with variant configs.

    Args:
        traj: Trajectory containing the original issue.

    Returns:
        Prompt string for agent_script.py.
    """
    parts = []
    if traj.issue_title:
        parts.append(f"## Issue: {traj.issue_title}")
    if traj.issue_body:
        parts.append(traj.issue_body)

    # Include relevant comment context (non-metric comments)
    relevant_comments = []
    for c in traj.comments:
        body = c.get("body_preview", "")
        # Skip bot markers and metric-only comments
        if body and not body.startswith("<!-- bot-") and "agent-metrics" not in body:
            author = c.get("author", "unknown")
            relevant_comments.append(f"**@{author}:** {body}")

    if relevant_comments:
        parts.append("## Discussion")
        parts.append("\n\n".join(relevant_comments[:10]))  # Limit context

    prompt = "\n\n".join(parts) if parts else traj.issue_title
    return prompt


def run_agent_rollout(
    prompt: str,
    config_path: str,
    output_dir: str,
    timeout_minutes: int = 15,
) -> RolloutResult:
    """Run agent_script.py with a specific config and prompt.

    Args:
        prompt: Task prompt for the agent.
        config_path: Path to variant config YAML.
        output_dir: Directory to write agent output.
        timeout_minutes: Max runtime for the agent.

    Returns:
        RolloutResult with extracted metrics.
    """
    agent_script = os.environ.get(
        "AGENT_SCRIPT_PATH",
        os.path.join(os.path.dirname(__file__), "agent_script.py"),
    )

    # Write prompt to a temp file for agent_script.py to read
    prompt_path = os.path.join(output_dir, "prompt.txt")
    with open(prompt_path, "w") as f:
        f.write(prompt)

    # Run agent_script.py with variant config
    env = os.environ.copy()
    env["AGENT_CONFIG_PATH"] = config_path

    try:
        result = subprocess.run(
            [sys.executable, agent_script, prompt_path],
            cwd=output_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_minutes * 60,
        )

        success = result.returncode == 0
        error = None if success else result.stderr[-500:]

        # Extract metrics from final_message.txt
        metrics_json = None
        final_msg_path = os.path.join(output_dir, "final_message.txt")
        if os.path.isfile(final_msg_path):
            with open(final_msg_path) as f:
                content = f.read()
            # Find embedded metrics comment
            import re
            match = re.search(r"<!--\s*agent-metrics:\s*(.*?)\s*-->", content)
            if match:
                metrics_json = match.group(1)

        rollout_result = RolloutResult(
            task_issue_number=0,  # Set by caller
            variant_id="",  # Set by caller
            config_path=config_path,
            success=success,
            error=error,
            metrics_json=metrics_json,
        )

        # Parse metrics if available
        if metrics_json:
            try:
                from metrics_schema import Metrics
                m = Metrics.from_json(metrics_json)
                rollout_result.error_rate = m.tool_call_error_rate
                rollout_result.fix_requests = m.review_fix_requests
                rollout_result.tool_calls = m.nb_tool_calls
                rollout_result.runtime_ms = m.agent_runtime_ms
                rollout_result.input_tokens = m.input_tokens
                rollout_result.output_tokens = m.output_tokens
            except (json.JSONDecodeError, Exception) as e:
                print(f"Warning: Failed to parse metrics: {e}")

        return rollout_result

    except subprocess.TimeoutExpired:
        return RolloutResult(
            task_issue_number=0,
            variant_id="",
            config_path=config_path,
            success=False,
            error=f"Agent timed out after {timeout_minutes} minutes",
        )
    except Exception as e:
        return RolloutResult(
            task_issue_number=0,
            variant_id="",
            config_path=config_path,
            success=False,
            error=str(e),
        )


def run_rollouts(
    coreset: List[Trajectory],
    variants: List[Dict[str, Any]],
    baseline_config_path: str,
    rollout_dir: str = ".rho/rollout",
    timeout_minutes: int = 15,
) -> List[RolloutResult]:
    """Execute full rollout: baseline + variants × coreset tasks.

    For each task in the coreset, runs the agent with the baseline config
    and each variant config, collecting metrics for comparison.

    Args:
        coreset: List of Trajectory objects.
        variants: List of variant config dicts.
        baseline_config_path: Path to baseline agent-config.yaml.
        rollout_dir: Base directory for rollout results.
        timeout_minutes: Per-task timeout.

    Returns:
        List of RolloutResult objects.
    """
    results = []
    configs = [
        {"variant_id": "baseline", "config_path": baseline_config_path},
        *variants,
    ]

    total_runs = len(coreset) * len(configs)
    run_count = 0

    for traj in coreset:
        prompt = build_task_prompt(traj)

        for config in configs:
            run_count += 1
            variant_id = config["variant_id"]
            config_path = config["config_path"]

            # Create isolated output directory for this run
            run_dir = os.path.join(
                rollout_dir,
                f"issue_{traj.issue_number}",
                variant_id,
            )
            os.makedirs(run_dir, exist_ok=True)

            print(f"[{run_count}/{total_runs}] Running {variant_id} "
                  f"on issue #{traj.issue_number}...")

            result = run_agent_rollout(
                prompt=prompt,
                config_path=config_path,
                output_dir=run_dir,
                timeout_minutes=timeout_minutes,
            )
            result.task_issue_number = traj.issue_number
            result.variant_id = variant_id

            results.append(result)
            status = "✓" if result.success else "✗"
            print(f"  {status} {variant_id}: error_rate={result.error_rate:.1f}% "
                  f"tool_calls={result.tool_calls} runtime={result.runtime_ms:.0f}ms")

    # Save rollout results
    results_path = os.path.join(rollout_dir, "results.json")
    with open(results_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)

    print(f"\nCompleted {len(results)} rollout runs. Results saved to {results_path}")
    return results


def main():
    """CLI entry point for RHO rollout."""
    rho_config = load_rho_config()
    archive_dir = os.environ.get("RHO_ARCHIVE_DIR", ".rho/trajectories")
    variants_dir = os.environ.get("RHO_VARIANTS_DIR", ".rho/variants")
    rollout_dir = os.environ.get("RHO_ROLLOUT_DIR", ".rho/rollout")
    baseline_path = os.environ.get("RHO_BASELINE_CONFIG", "")
    timeout = int(
        os.environ.get("RHO_TIMEOUT_MINUTES",
                       str(rho_config.get("rollout", {}).get("timeout_minutes", 15)))
    )

    # Load coreset
    coreset = select_coreset(archive_dir=archive_dir)
    if not coreset:
        print("Error: No coreset available.")
        sys.exit(1)

    # Load variants
    variants = load_variant_configs(variants_dir)
    if not variants:
        print("Error: No variant configs found.")
        sys.exit(1)

    # Baseline config path
    if not baseline_path:
        baseline_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "agent-config.yaml",
        )

    results = run_rollouts(
        coreset=coreset,
        variants=variants,
        baseline_config_path=baseline_path,
        rollout_dir=rollout_dir,
        timeout_minutes=timeout,
    )

    # Print summary
    successes = sum(1 for r in results if r.success)
    print(f"\nRollout Summary: {successes}/{len(results)} runs succeeded")


if __name__ == "__main__":
    main()
