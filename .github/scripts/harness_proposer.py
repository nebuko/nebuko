#!/usr/bin/env python3
"""
Harness Proposer — generate harness mutation variants for RHO rollout.

Analyzes coreset trajectories and proposes structured mutations to the
agent harness config. Creates variant YAML files for rollout comparison.

Two modes:
1. Programmatic — auto-generate mutations from metric patterns.
2. Agent-driven — invoke the genius agent with rho_proposer.md prompt.

This module implements the programmatic mode. The agent-driven mode
is orchestrated by the rho.yml workflow calling agent_script.py.

Usage:
    from harness_proposer import generate_variants, propose_mutations

    variants = generate_variants(coreset, baseline_config, num_variants=3)
"""

import copy
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from coreset_selector import load_rho_config, get_default_config
from trajectory_archive import Trajectory

# Path to the baseline harness config
DEFAULT_BASELINE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "agent-config.yaml",
)


@dataclass
class Mutation:
    """A single harness mutation."""

    variant_id: str
    description: str
    target: str  # e.g. "agents.dev.temperature"
    current_value: Any
    proposed_value: Any
    rationale: str


@dataclass
class VariantConfig:
    """A variant harness configuration ready for rollout."""

    variant_id: str
    config_path: str
    mutations: List[Mutation]
    config_data: Dict[str, Any]


def load_raw_config(config_path: str) -> Dict[str, Any]:
    """Load raw YAML config without resolving references.

    Args:
        config_path: Path to agent-config.yaml.

    Returns:
        Raw dict from YAML.
    """
    with open(config_path) as f:
        return yaml.safe_load(f)


def resolve_agent_path(target: str) -> Tuple[List[str], str]:
    """Parse a target path like 'agents.dev.temperature' into (path_parts, key).

    Args:
        target: Dot-separated path string.

    Returns:
        Tuple of (list of path components, final key name).
    """
    parts = target.split(".")
    return parts[:-1], parts[-1]


def get_nested(d: Dict, path: List[str]) -> Any:
    """Get a nested value from a dict by path."""
    for key in path:
        if isinstance(d, dict):
            d = d.get(key, {})
        else:
            return None
    return d


def set_nested(d: Dict, path: List[str], key: str, value: Any):
    """Set a nested value in a dict by path."""
    current = d
    for part in path:
        if isinstance(current, dict):
            current = current.setdefault(part, {})
        else:
            return
    if isinstance(current, dict):
        current[key] = value


def analyze_trajectories_for_patterns(
    coreset: List[Trajectory],
) -> Dict[str, Any]:
    """Analyze coreset trajectories to identify performance patterns.

    Looks for:
    - High error rates per agent role
    - High fix request counts
    - Excessive tool calls
    - Long runtimes
    - High token usage

    Args:
        coreset: List of Trajectory objects.

    Returns:
        Dict with per-agent role statistics and identified issues.
    """
    # Aggregate by agent role across all trajectories
    role_stats: Dict[str, Dict[str, Any]] = {}

    for traj in coreset:
        for metric in traj.metrics:
            role = metric.agent_role or "unknown"
            if role not in role_stats:
                role_stats[role] = {
                    "error_rates": [],
                    "fix_requests": [],
                    "tool_calls": [],
                    "runtimes": [],
                    "tokens": [],
                    "temperatures": [],  # from baseline config, set later
                }
            role_stats[role]["error_rates"].append(metric.tool_call_error_rate)
            role_stats[role]["fix_requests"].append(metric.review_fix_requests)
            role_stats[role]["tool_calls"].append(metric.nb_tool_calls)
            role_stats[role]["runtimes"].append(metric.agent_runtime_ms)
            role_stats[role]["tokens"].append(metric.input_tokens + metric.output_tokens)

    # Compute averages
    patterns = {}
    for role, stats in role_stats.items():
        patterns[role] = {
            "avg_error_rate": _avg(stats["error_rates"]),
            "avg_fix_requests": _avg(stats["fix_requests"]),
            "avg_tool_calls": _avg(stats["tool_calls"]),
            "avg_runtime_ms": _avg(stats["runtimes"]),
            "avg_tokens": _avg(stats["tokens"]),
            "sample_count": len(stats["error_rates"]),
        }

    return patterns


def _avg(values: List[float]) -> float:
    """Compute average of a list of values."""
    return sum(values) / len(values) if values else 0.0


def propose_mutations(
    coreset: List[Trajectory],
    baseline_config: Dict[str, Any],
    num_variants: int = 3,
) -> List[Mutation]:
    """Propose harness mutations based on trajectory analysis.

    Generates mutations by analyzing patterns in the coreset and matching
    them to appropriate mutation strategies.

    Args:
        coreset: List of Trajectory objects.
        baseline_config: Raw YAML config dict.
        num_variants: Number of mutation variants to propose.

    Returns:
        List of Mutation objects.
    """
    patterns = analyze_trajectories_for_patterns(coreset)
    agents = baseline_config.get("agents", {})
    llms = baseline_config.get("llms", {})

    mutations = []

    # Strategy 1: Temperature adjustment for high fix-request agents
    for role, stats in patterns.items():
        if role not in agents:
            continue
        if stats["avg_fix_requests"] > 0.5 and stats["sample_count"] >= 2:
            current_temp = agents[role].get("temperature", 0.0)
            # Small increase to encourage more creative solutions
            proposed_temp = min(round(current_temp + 0.1, 2), 1.0)
            if proposed_temp != current_temp:
                mutations.append(Mutation(
                    variant_id=f"v{len(mutations) + 1}",
                    description=f"Increase {role} temperature to reduce fix requests",
                    target=f"agents.{role}.temperature",
                    current_value=current_temp,
                    proposed_value=proposed_temp,
                    rationale=(
                        f"Agent '{role}' averaged {stats['avg_fix_requests']:.1f} "
                        f"fix requests. Increasing temperature from {current_temp} to "
                        f"{proposed_temp} may reduce rigid patterns."
                    ),
                ))

    # Strategy 2: Tool reduction for high tool-call agents
    for role, stats in patterns.items():
        if role not in agents:
            continue
        if stats["avg_tool_calls"] > 15 and stats["sample_count"] >= 2:
            current_tools = agents[role].get("tools", [])
            # Consider removing task_tracker if present (it adds overhead)
            if "task_tracker" in current_tools:
                proposed_tools = [t for t in current_tools if t != "task_tracker"]
                mutations.append(Mutation(
                    variant_id=f"v{len(mutations) + 1}",
                    description=f"Remove task_tracker from {role} to reduce tool calls",
                    target=f"agents.{role}.tools",
                    current_value=current_tools,
                    proposed_value=proposed_tools,
                    rationale=(
                        f"Agent '{role}' averaged {stats['avg_tool_calls']:.1f} tool calls. "
                        f"Removing task_tracker may reduce overhead without losing capability."
                    ),
                ))

    # Strategy 3: Model swap for high error-rate agents
    for role, stats in patterns.items():
        if role not in agents:
            continue
        if stats["avg_error_rate"] > 5.0 and stats["sample_count"] >= 2:
            current_model_ref = agents[role].get("model", "")
            # Find a larger/stronger model if available
            available_models = list(llms.keys())
            for candidate in available_models:
                if f"${{llms.{candidate}}}" != current_model_ref:
                    mutations.append(Mutation(
                        variant_id=f"v{len(mutations) + 1}",
                        description=f"Swap {role} model to {candidate}",
                        target=f"agents.{role}.model",
                        current_value=current_model_ref,
                        proposed_value=f"${{llms.{candidate}}}",
                        rationale=(
                            f"Agent '{role}' had {stats['avg_error_rate']:.1f}% error rate. "
                            f"Switching to '{candidate}' may improve reliability."
                        ),
                    ))
                    break  # Only propose one model swap per role

    # Strategy 4: Temperature reduction for high-token agents
    for role, stats in patterns.items():
        if role not in agents:
            continue
        if stats["avg_tokens"] > 100000 and stats["sample_count"] >= 2:
            current_temp = agents[role].get("temperature", 0.0)
            if current_temp > 0:
                proposed_temp = max(round(current_temp - 0.1, 2), 0.0)
                mutations.append(Mutation(
                    variant_id=f"v{len(mutations) + 1}",
                    description=f"Decrease {role} temperature for token efficiency",
                    target=f"agents.{role}.temperature",
                    current_value=current_temp,
                    proposed_value=proposed_temp,
                    rationale=(
                        f"Agent '{role}' averaged {stats['avg_tokens']:.0f} tokens. "
                        f"Lowering temperature from {current_temp} to {proposed_temp} "
                        f"may reduce verbosity."
                    ),
                ))

    # If we have fewer mutations than variants, add a generic temperature sweep
    if len(mutations) < num_variants and agents:
        for role in list(agents.keys())[:num_variants - len(mutations)]:
            current_temp = agents[role].get("temperature", 0.0)
            # Try a small delta in either direction
            delta = 0.05 if current_temp < 0.5 else -0.05
            proposed_temp = max(0.0, min(round(current_temp + delta, 2), 1.0))
            if proposed_temp != current_temp:
                mutations.append(Mutation(
                    variant_id=f"v{len(mutations) + 1}",
                    description=f"Fine-tune {role} temperature",
                    target=f"agents.{role}.temperature",
                    current_value=current_temp,
                    proposed_value=proposed_temp,
                    rationale=(
                        f"Fine-grained temperature adjustment for '{role}' "
                        f"from {current_temp} to {proposed_temp} as a controlled experiment."
                    ),
                ))

    # Re-assign variant IDs sequentially
    for i, m in enumerate(mutations):
        m.variant_id = f"v{i + 1}"

    return mutations[:max(num_variants, len(mutations))]


def apply_mutation(
    config_data: Dict[str, Any],
    mutation: Mutation,
) -> Dict[str, Any]:
    """Apply a single mutation to a config dict.

    Args:
        config_data: Raw config dict (will be modified via deep copy).
        mutation: Mutation to apply.

    Returns:
        New config dict with mutation applied.
    """
    variant = copy.deepcopy(config_data)
    parts, key = resolve_agent_path(mutation.target)
    set_nested(variant, parts, key, mutation.proposed_value)
    return variant


def generate_variants(
    coreset: List[Trajectory],
    baseline_config: Optional[Dict[str, Any]] = None,
    baseline_path: Optional[str] = None,
    num_variants: Optional[int] = None,
    output_dir: str = ".rho/variants",
) -> List[VariantConfig]:
    """Generate variant harness configs from coreset analysis.

    Full pipeline: load baseline → propose mutations → write variant files.

    Args:
        coreset: List of Trajectory objects.
        baseline_config: Optional pre-loaded config dict.
        baseline_path: Path to agent-config.yaml.
        num_variants: Number of variants to generate.
        output_dir: Directory to write variant YAML files.

    Returns:
        List of VariantConfig objects.
    """
    rho_config = load_rho_config()
    if num_variants is None:
        num_variants = rho_config.get("variants", {}).get("num_variants", 3)

    if baseline_config is None:
        baseline_path = baseline_path or DEFAULT_BASELINE_PATH
        baseline_config = load_raw_config(baseline_path)

    mutations = propose_mutations(coreset, baseline_config, num_variants)

    if not mutations:
        print("Warning: No mutations proposed. Coreset may lack signal.")
        return []

    os.makedirs(output_dir, exist_ok=True)
    variants = []

    for mutation in mutations:
        variant_data = apply_mutation(baseline_config, mutation)
        filename = f"variant_{mutation.variant_id}.yaml"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w") as f:
            yaml.dump(variant_data, f, default_flow_style=False, sort_keys=False)

        variants.append(VariantConfig(
            variant_id=mutation.variant_id,
            config_path=filepath,
            mutations=[mutation],
            config_data=variant_data,
        ))
        print(f"Generated variant {mutation.variant_id}: {mutation.description} -> {filepath}")

    # Also save mutation proposal summary
    summary = {
        "baseline_config": baseline_path,
        "coreset_size": len(coreset),
        "num_variants": len(variants),
        "mutations": [
            {
                "variant_id": m.variant_id,
                "description": m.description,
                "target": m.target,
                "current_value": m.current_value,
                "proposed_value": m.proposed_value,
                "rationale": m.rationale,
            }
            for m in mutations
        ],
    }
    summary_path = os.path.join(output_dir, "proposals.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    return variants


def main():
    """CLI entry point for harness proposal."""
    archive_dir = os.environ.get("RHO_ARCHIVE_DIR", ".rho/trajectories")
    baseline_path = os.environ.get("RHO_BASELINE_CONFIG", DEFAULT_BASELINE_PATH)
    num_variants = int(os.environ.get("RHO_NUM_VARIANTS", "0")) or None

    # Load coreset (requires archive to be populated first)
    from coreset_selector import select_coreset
    coreset = select_coreset(archive_dir=archive_dir)

    if not coreset:
        print("Error: No coreset available. Run trajectory archive first.")
        sys.exit(1)

    variants = generate_variants(
        coreset=coreset,
        baseline_path=baseline_path,
        num_variants=num_variants,
    )

    print(f"Generated {len(variants)} variant configs.")


if __name__ == "__main__":
    main()
