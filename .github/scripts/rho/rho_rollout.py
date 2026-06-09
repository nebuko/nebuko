#!/usr/bin/env python3
"""
RHO rollout — re-run agent on coreset tasks with variant harness configs.

Generates variant config files, invokes agent_script.py for each variant
on each coreset task, and collects per-variant metrics for comparison.

This module is designed to run in GitHub Actions. Each rollout task is
executed by writing a temporary variant config and calling the existing
agent_script.py entry point.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(__file__) + "/..")

from metrics_schema import Metrics
from rho.coreset_selector import ScoredTrajectory
from rho.harness_proposer import VariantProposal, generate_variant_configs
from rho.trajectory_archive import Trajectory


# ── Data Models ───────────────────────────────────────────────────────

@dataclass
class RolloutResult:
    """Result of running one variant on one task."""

    variant_id: str
    issue_number: int
    success: bool
    metrics: Optional[Metrics] = None
    error: Optional[str] = None
    runtime_ms: float = 0.0
    config_path: Optional[str] = None


@dataclass
class RolloutBatch:
    """Results for all variants on a single task."""

    issue_number: int
    results: List[RolloutResult] = field(default_factory=list)


# ── Rollout Execution ─────────────────────────────────────────────────

def _write_variant_config(config: Dict[str, Any], output_dir: str) -> str:
    """
    Write a variant harness config to a temporary YAML file.

    Args:
        config: Harness config dict.
        output_dir: Directory to write the config file.

    Returns:
        Path to the written config file.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filepath = Path(output_dir) / f"variant-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.yaml"
    filepath.write_text(yaml.dump(config, default_flow_style=False))
    return str(filepath)


def _build_task_prompt(trajectory: Trajectory) -> str:
    """
    Build the task prompt from a trajectory's original issue content.

    Args:
        trajectory: The trajectory containing the original issue.

    Returns:
        Prompt string for the agent to execute.
    """
    lines = []
    lines.append(f"# Task: {trajectory.issue_title}")
    lines.append("")
    if trajectory.issue_body:
        lines.append(trajectory.issue_body)
    lines.append("")
    lines.append("---")
    lines.append("*This is an RHO rollout task. Please solve it to the best of your ability.*")
    return "\n".join(lines)


def run_rollout(
    variant_config: Dict[str, Any],
    trajectory: Trajectory,
    agent_script_path: str,
    output_dir: str,
    api_key: str,
    base_url: Optional[str] = None,
    timeout_minutes: int = 20,
) -> RolloutResult:
    """
    Run a single rollout: one variant config on one task.

    Invokes agent_script.py with the variant config and task prompt.

    Args:
        variant_config: The variant harness config to use.
        trajectory: The task trajectory to solve.
        agent_script_path: Path to agent_script.py.
        output_dir: Directory for temporary files.
        api_key: LLM API key.
        base_url: Optional LLM base URL override.
        timeout_minutes: Maximum execution time.

    Returns:
        RolloutResult with metrics and status.
    """
    variant_id = "baseline"
    # Extract variant_id from config if it has one
    if "rho_variant_id" in variant_config:
        variant_id = variant_config["rho_variant_id"]
    else:
        # Generate a simple id based on config hash
        variant_id = f"v-{hash(json.dumps(variant_config, sort_keys=True)) % 10000}"

    # Write variant config
    variant_config_path = _write_variant_config(variant_config, output_dir)

    # Write task prompt
    prompt = _build_task_prompt(trajectory)
    prompt_path = os.path.join(output_dir, f"prompt-{trajectory.issue_number}.txt")
    Path(prompt_path).write_text(prompt)

    # Build environment
    env = os.environ.copy()
    env["AGENT_CONFIG_PATH"] = variant_config_path
    env["LLM_API_KEY"] = api_key
    if base_url:
        env["LLM_BASE_URL"] = base_url

    start_time = datetime.now(timezone.utc)

    try:
        # Run agent_script.py
        result = subprocess.run(
            [sys.executable, agent_script_path, prompt_path],
            env=env,
            cwd=output_dir,
            capture_output=True,
            text=True,
            timeout=timeout_minutes * 60,
        )

        runtime_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

        # Extract metrics from output files
        metrics = _extract_metrics_from_output(output_dir)

        return RolloutResult(
            variant_id=variant_id,
            issue_number=trajectory.issue_number,
            success=result.returncode == 0,
            metrics=metrics,
            error=result.stderr if result.returncode != 0 else None,
            runtime_ms=runtime_ms,
            config_path=variant_config_path,
        )

    except subprocess.TimeoutExpired:
        runtime_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        return RolloutResult(
            variant_id=variant_id,
            issue_number=trajectory.issue_number,
            success=False,
            error=f"Timeout after {timeout_minutes} minutes",
            runtime_ms=runtime_ms,
            config_path=variant_config_path,
        )
    except Exception as e:
        runtime_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        return RolloutResult(
            variant_id=variant_id,
            issue_number=trajectory.issue_number,
            success=False,
            error=str(e),
            runtime_ms=runtime_ms,
            config_path=variant_config_path,
        )


def _extract_metrics_from_output(output_dir: str) -> Optional[Metrics]:
    """
    Extract Metrics from final_message.txt in the output directory.

    Uses the same regex pattern as summarize_metrics.py.
    """
    from summarize_metrics import extract_metrics_from_comment

    message_path = Path(output_dir) / "final_message.txt"
    if not message_path.exists():
        return None

    content = message_path.read_text()
    metrics_list = extract_metrics_from_comment(content)

    if metrics_list:
        return metrics_list[-1]  # Return the last (most recent) metrics entry
    return None


def run_rollout_batch(
    variant_configs: List[Dict[str, Any]],
    variant_proposals: List[VariantProposal],
    coreset: List[ScoredTrajectory],
    agent_script_path: str,
    output_dir: str,
    api_key: str,
    base_url: Optional[str] = None,
    timeout_minutes: int = 20,
) -> List[RolloutBatch]:
    """
    Run all variants on all coreset tasks.

    Args:
        variant_configs: List of variant config dicts.
        variant_proposals: Corresponding variant proposals (for IDs).
        coreset: Selected challenging trajectories.
        agent_script_path: Path to agent_script.py.
        output_dir: Directory for rollout artifacts.
        api_key: LLM API key.
        base_url: Optional LLM base URL.
        timeout_minutes: Per-task timeout.

    Returns:
        List of RolloutBatch (one per coreset task).
    """
    # Add variant IDs to configs
    for i, (config, proposal) in enumerate(zip(variant_configs, variant_proposals)):
        config["rho_variant_id"] = proposal.variant_id

    # Also add baseline (original config without mutations)
    baseline_configs = variant_configs  # Already includes all variants

    batches: list[RolloutBatch] = []

    for st in coreset:
        batch = RolloutBatch(issue_number=st.trajectory.issue_number)

        # Create task-specific output dir
        task_dir = os.path.join(output_dir, f"task-{st.trajectory.issue_number}")

        for config in baseline_configs:
            result = run_rollout(
                variant_config=config,
                trajectory=st.trajectory,
                agent_script_path=agent_script_path,
                output_dir=task_dir,
                api_key=api_key,
                base_url=base_url,
                timeout_minutes=timeout_minutes,
            )
            batch.results.append(result)

        batches.append(batch)

    return batches


def save_rollout_results(batches: List[RolloutBatch], output_path: str):
    """
    Save rollout results to a JSON file for later selection.

    Args:
        batches: List of rollout batches.
        output_path: Path to write the JSON results file.
    """
    data = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "batches": [],
    }

    for batch in batches:
        batch_data = {
            "issue_number": batch.issue_number,
            "results": [],
        }
        for result in batch.results:
            result_data = {
                "variant_id": result.variant_id,
                "issue_number": result.issue_number,
                "success": result.success,
                "runtime_ms": result.runtime_ms,
                "config_path": result.config_path,
            }
            if result.metrics:
                result_data["metrics"] = result.metrics.to_dict()
            if result.error:
                result_data["error"] = result.error
            batch_data["results"].append(result_data)

        data["batches"].append(batch_data)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(data, indent=2))


def main():
    """CLI entry point for RHO rollout execution."""
    agent_script_path = os.environ.get("AGENT_SCRIPT_PATH", ".github/scripts/agent_script.py")
    api_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENROUTER_API_KEY", ""))
    base_url = os.environ.get("LLM_BASE_URL")
    output_dir = os.environ.get("RHO_OUTPUT_DIR", ".rho/rollouts")
    rho_config_path = os.environ.get("RHO_CONFIG_PATH", ".github/scripts/rho/rho_config.yaml")
    harness_config_path = os.environ.get("HARNESS_CONFIG_PATH", "agent-config.yaml")

    if not api_key:
        print("Error: LLM_API_KEY or OPENROUTER_API_KEY env var is required.")
        sys.exit(1)

    # Load configs
    with open(rho_config_path) as f:
        rho_config = yaml.safe_load(f)

    with open(harness_config_path) as f:
        harness_config = yaml.safe_load(f)

    timeout = rho_config.get("execution", {}).get("task_timeout_minutes", 20)

    print(f"RHO Rollout starting...")
    print(f"  Agent script: {agent_script_path}")
    print(f"  Output dir: {output_dir}")
    print(f"  Timeout: {timeout} minutes per task")

    # This would normally load coreset and variant configs
    # For CLI usage, these would be passed as JSON files or loaded from previous steps
    print("Note: Run after coreset selection and harness proposal steps.")
    print("Input: coreset results + variant proposals")
    print("Output: rollout results in .rho/rollouts/")


if __name__ == "__main__":
    main()
