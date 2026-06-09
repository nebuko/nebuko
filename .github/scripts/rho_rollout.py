#!/usr/bin/env python3
"""
RHO Rollout — orchestrate parallel re-execution of coreset tasks with variant configs.

Generates variant harness configurations, invokes agent_script.py for each
variant against each coreset task, and collects resulting metrics for
comparison in the selection phase.

This module provides utility functions for:
    - Building rollout prompts from archived trajectory data
    - Invoking agent_script.py with variant configs
    - Collecting and aggregating rollout metrics

Not designed as a standalone runner — intended to be called from the
rho.yml GitHub Actions workflow or rho_select.py.

Usage:
    python rho_rollout.py [--output-dir .rho/rollout/]

Environment Variables:
    GITHUB_TOKEN: GitHub API token (required for posting results)
    GITHUB_REPOSITORY: Repository in format owner/repo (required)
    AGENT_SCRIPT_PATH: Path to agent_script.py (auto-detected if not set)
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from trajectory_archive import load_archived_trajectories
from coreset_selector import select_coreset_from_archive
from harness_proposer import generate_variants

DEFAULT_ROLLOUT_DIR = os.path.join("..", "..", ".rho", "rollout")
DEFAULT_CONFIG = "../agent-config.yaml"
DEFAULT_RHO_CONFIG = "../rho_config.yaml"


@dataclass
class RolloutResult:
    """Result of running a single task with a single harness variant.

    Attributes:
        task_id: Issue number or task identifier.
        variant_id: Identifier of the harness variant used.
        metrics_json: Raw metrics JSON string from agent output.
        success: Whether the agent run completed without fatal error.
        error: Error message if run failed.
        runtime_seconds: Wall-clock time for the agent run.
    """

    task_id: str
    variant_id: str
    metrics_json: Optional[str] = None
    success: bool = False
    error: Optional[str] = None
    runtime_seconds: float = 0.0


@dataclass
class RolloutSummary:
    """Aggregated summary of a full rollout batch.

    Attributes:
        results: List of individual RolloutResult entries.
        variant_results: Dict mapping variant_id to list of results.
        task_results: Dict mapping task_id to list of results per variant.
    """

    results: List[RolloutResult] = field(default_factory=list)
    variant_results: Dict[str, List[RolloutResult]] = field(default_factory=dict)
    task_results: Dict[str, List[RolloutResult]] = field(default_factory=dict)

    def add_result(self, result: RolloutResult):
        """Add a result and update indices."""
        self.results.append(result)
        vid = result.variant_id
        tid = result.task_id
        self.variant_results.setdefault(vid, []).append(result)
        self.task_results.setdefault(tid, []).append(result)


def build_rollout_prompt(trajectory: Dict[str, Any]) -> str:
    """Build a rollout prompt from a trajectory for re-execution.

    Reconstructs the original task context from the archived trajectory.
    Includes issue title, body, and labels as context.

    Args:
        trajectory: Trajectory dict from coreset_selector.

    Returns:
        Prompt string suitable for feeding to agent_script.py.
    """
    title = trajectory.get("title", "Untitled task")
    body = trajectory.get("body", "")
    labels = trajectory.get("labels", [])
    issue_num = trajectory.get("issue_number", "?")

    parts = [f"# Task (from Issue #{issue_num})\n"]
    if labels:
        parts.append(f"**Labels:** {', '.join(labels)}\n")
    parts.append(f"**Title:** {title}\n")
    if body:
        parts.append(f"**Description:**\n\n{body}")

    return "\n\n".join(parts)


def run_agent_with_config(prompt: str, config_path: str, role_name: str = "dev",
                          output_dir: str = ".", timeout_minutes: int = 30) -> Dict[str, Any]:
    """Run agent_script.py with a specific harness config.

    Writes the prompt to a temp file, invokes agent_script.py with the
    variant config, and captures the output metrics.

    Args:
        prompt: Task prompt string.
        config_path: Path to variant harness config YAML.
        role_name: Agent role to use (default: dev).
        output_dir: Working directory for the agent run.
        timeout_minutes: Maximum runtime before killing the process.

    Returns:
        Dict with keys: success, metrics_json, error, runtime_seconds.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Write prompt to temp file
    prompt_file = os.path.join(output_dir, "rho_prompt.txt")
    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(prompt)

    # Find agent_script.py
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    agent_script = os.environ.get(
        "AGENT_SCRIPT_PATH",
        os.path.join(scripts_dir, "agent_script.py"),
    )

    env = os.environ.copy()
    env["AGENT_CONFIG_PATH"] = config_path
    env["ROLE_NAME"] = role_name

    cmd = [sys.executable, agent_script, prompt_file]

    start_time = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=output_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_minutes * 60,
        )

        runtime = time.time() - start_time

        # Try to read metrics from output files
        metrics_json = _extract_metrics_from_run(output_dir)

        return {
            "success": proc.returncode == 0,
            "metrics_json": metrics_json,
            "error": proc.stderr.strip() if proc.returncode != 0 else None,
            "runtime_seconds": runtime,
        }

    except subprocess.TimeoutExpired:
        runtime = time.time() - start_time
        return {
            "success": False,
            "metrics_json": None,
            "error": f"Agent timed out after {timeout_minutes} minutes",
            "runtime_seconds": runtime,
        }
    except Exception as e:
        runtime = time.time() - start_time
        return {
            "success": False,
            "metrics_json": None,
            "error": str(e),
            "runtime_seconds": runtime,
        }


def _extract_metrics_from_run(run_dir: str) -> Optional[str]:
    """Extract metrics JSON from agent run output files.

    Checks final_message.txt, final_message_part_N.txt, and output/metrics-*.json.

    Args:
        run_dir: Directory where agent_script.py wrote its output.

    Returns:
        Metrics JSON string if found, None otherwise.
    """
    import re
    metrics_regex = re.compile(r"<!--\s*agent-metrics:\s*(.*?)\s*-->")

    # Check final_message.txt
    for fname in ["final_message.txt"]:
        fpath = os.path.join(run_dir, fname)
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            match = metrics_regex.search(content)
            if match:
                return match.group(1)

    # Check split parts (last part has metrics)
    parts_file = os.path.join(run_dir, "final_message_parts_count.txt")
    if os.path.exists(parts_file):
        with open(parts_file, "r") as f:
            try:
                n_parts = int(f.read().strip())
                last_part = os.path.join(run_dir, f"final_message_part_{n_parts}.txt")
                if os.path.exists(last_part):
                    with open(last_part, "r", encoding="utf-8") as f:
                        content = f.read()
                    match = metrics_regex.search(content)
                    if match:
                        return match.group(1)
            except ValueError:
                pass

    # Check output/metrics-*.json
    output_dir = os.path.join(run_dir, "output")
    if os.path.isdir(output_dir):
        for fname in sorted(os.listdir(output_dir)):
            if fname.startswith("metrics-") and fname.endswith(".json"):
                fpath = os.path.join(output_dir, fname)
                with open(fpath, "r") as f:
                    return f.read()

    return None


def run_rollout(coreset: List[Dict[str, Any]],
                variants: List[Dict[str, Any]],
                rollout_dir: str = DEFAULT_ROLLOUT_DIR,
                role_name: str = "dev",
                baseline_config: str = DEFAULT_CONFIG) -> RolloutSummary:
    """Execute a full rollout: run each coreset task with baseline and each variant.

    For each task in the coreset, runs agent_script.py once with the baseline
    config and once per variant config. Collects all metrics into a summary.

    Args:
        coreset: List of trajectory dicts from coreset_selector.
        variants: List of variant dicts from harness_proposer.
        rollout_dir: Base directory for rollout output.
        role_name: Agent role to use for rollout runs.
        baseline_config: Path to baseline agent-config.yaml.

    Returns:
        RolloutSummary with all results indexed by variant and task.
    """
    summary = RolloutSummary()

    # Run baseline first
    for traj in coreset:
        task_id = str(traj["issue_number"])
        prompt = build_rollout_prompt(traj)
        run_dir = os.path.join(rollout_dir, "baseline", task_id)

        print(f"[BASELINE] Running task #{task_id}...")
        result_data = run_agent_with_config(prompt, baseline_config, role_name, run_dir)
        result = RolloutResult(
            task_id=task_id,
            variant_id="baseline",
            metrics_json=result_data.get("metrics_json"),
            success=result_data.get("success", False),
            error=result_data.get("error"),
            runtime_seconds=result_data.get("runtime_seconds", 0),
        )
        summary.add_result(result)

    # Run each variant
    for variant in variants:
        variant_id = variant["id"]
        variant_config = variant["filepath"]

        for traj in coreset:
            task_id = str(traj["issue_number"])
            prompt = build_rollout_prompt(traj)
            run_dir = os.path.join(rollout_dir, variant_id, task_id)

            print(f"[VARIANT:{variant_id}] Running task #{task_id}...")
            result_data = run_agent_with_config(prompt, variant_config, role_name, run_dir)
            result = RolloutResult(
                task_id=task_id,
                variant_id=variant_id,
                metrics_json=result_data.get("metrics_json"),
                success=result_data.get("success", False),
                error=result_data.get("error"),
                runtime_seconds=result_data.get("runtime_seconds", 0),
            )
            summary.add_result(result)

    return summary


def save_rollout_summary(summary: RolloutSummary, output_dir: str) -> str:
    """Serialize rollout summary to JSON file.

    Args:
        summary: RolloutSummary to save.
        output_dir: Directory to write the file.

    Returns:
        Path to the saved file.
    """
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, "rollout_summary.json")

    data = {
        "results": [
            {
                "task_id": r.task_id,
                "variant_id": r.variant_id,
                "success": r.success,
                "error": r.error,
                "runtime_seconds": r.runtime_seconds,
                "metrics": json.loads(r.metrics_json) if r.metrics_json else None,
            }
            for r in summary.results
        ],
    }

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    return filepath


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run RHO rollout evaluation")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_ROLLOUT_DIR)
    parser.add_argument("--config", type=str, default=DEFAULT_CONFIG)
    parser.add_argument("--rho-config", type=str, default=DEFAULT_RHO_CONFIG)
    parser.add_argument("--role", type=str, default="dev")
    args = parser.parse_args()

    # Select coreset
    coreset, rho_config = select_coreset_from_archive(config_path=args.rho_config)

    if not coreset:
        print("No coreset tasks available. Run trajectory_archive.py first.")
        sys.exit(1)

    print(f"Coreset: {len(coreset)} tasks")

    # Generate variants
    variants = generate_variants(
        config_path=args.config,
        rho_config_path=args.rho_config,
        coreset=coreset,
        output_dir=os.path.join(args.output_dir, "variants"),
    )

    if not variants:
        print("No variants generated.")
        sys.exit(1)

    print(f"Variants: {len(variants)}")

    # Run rollout
    summary = run_rollout(
        coreset=coreset,
        variants=variants,
        rollout_dir=args.output_dir,
        role_name=args.role,
        baseline_config=args.config,
    )

    # Save summary
    summary_path = save_rollout_summary(summary, args.output_dir)
    print(f"Rollout complete. Results saved to {summary_path}")
    print(f"Total runs: {len(summary.results)}")


if __name__ == "__main__":
    main()
