#!/usr/bin/env python3
"""
Harness Proposer — generate harness variant configurations for RHO rollout.

Analyzes coreset trajectories to identify pain points, then proposes
structured mutations to the agent harness (temperature tweaks, model swaps,
tool list changes, prompt adjustments). Outputs variant config files ready
for rollout evaluation.

Two modes:
  1. Analytical (offline): Rule-based proposals derived from metric patterns.
  2. Agent-assisted: Delegates to genius agent with rho_proposer prompt.

This module provides the analytical engine and the variant config generator.
The agent-assisted mode is handled by the workflow composing this module
with agent_script.py.

Usage:
    python harness_proposer.py [--variants N] [--output-dir .rho/variants/]

Environment Variables:
    None (reads from archived trajectories and config files).
"""

import copy
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from coreset_selector import select_coreset_from_archive
from config import load_config, AppConfig, AgentConfig, LLMConfig

DEFAULT_CONFIG = "../agent-config.yaml"
DEFAULT_RHO_CONFIG = "../rho_config.yaml"
DEFAULT_OUTPUT_DIR = os.path.join("..", "..", ".rho", "variants")


def analyze_pain_points(coreset: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze coreset trajectories to identify performance pain points.

    Computes aggregate statistics across the coreset and identifies which
    dimensions show degradation (high error rates, many fix requests, etc.).

    Args:
        coreset: List of trajectory dictionaries from coreset_selector.

    Returns:
        Dict with pain_point analysis: avg metrics and flagged issues.
    """
    if not coreset:
        return {"flagged": [], "stats": {}}

    total_errors = 0
    total_fix_requests = 0
    total_tool_calls = 0
    total_runtime = 0.0
    total_tokens = 0
    n = len(coreset)

    per_agent_stats: Dict[str, Dict[str, list]] = {}

    for traj in coreset:
        for metric in traj.get("metrics", []):
            role = metric.get("agent_role", "unknown")
            if role not in per_agent_stats:
                per_agent_stats[role] = {
                    "error_rates": [], "fix_requests": [], "tool_calls": [],
                    "runtimes": [], "tokens": [],
                }

            per_agent_stats[role]["error_rates"].append(metric.get("tool_call_error_rate", 0))
            per_agent_stats[role]["fix_requests"].append(metric.get("review_fix_requests", 0))
            per_agent_stats[role]["tool_calls"].append(metric.get("nb_tool_calls", 0))
            per_agent_stats[role]["runtimes"].append(metric.get("agent_runtime_ms", 0))
            tok = metric.get("input_tokens", 0) + metric.get("output_tokens", 0)
            per_agent_stats[role]["tokens"].append(tok)

            total_errors += metric.get("tool_call_error_rate", 0)
            total_fix_requests += metric.get("review_fix_requests", 0)
            total_tool_calls += metric.get("nb_tool_calls", 0)
            total_runtime += metric.get("agent_runtime_ms", 0)
            total_tokens += tok

    # Normalize across all metric entries
    n_metrics = sum(len(t.get("metrics", [])) for t in coreset)
    if n_metrics == 0:
        n_metrics = 1

    stats = {
        "avg_error_rate": total_errors / n_metrics,
        "avg_fix_requests": total_fix_requests / n_metrics,
        "avg_tool_calls": total_tool_calls / n_metrics,
        "avg_runtime_ms": total_runtime / n_metrics,
        "avg_tokens": total_tokens / n_metrics,
        "n_trajectories": n,
        "n_metrics": n_metrics,
    }

    # Flag pain points
    flagged: List[str] = []
    if stats["avg_error_rate"] > 5.0:
        flagged.append("high_error_rate")
    if stats["avg_fix_requests"] > 1.0:
        flagged.append("excessive_fix_requests")
    if stats["avg_tool_calls"] > 20:
        flagged.append("excessive_tool_calls")
    if stats["avg_runtime_ms"] > 30000:
        flagged.append("high_runtime")

    return {"flagged": flagged, "stats": stats, "per_agent": per_agent_stats}


def generate_analytical_proposals(harness: AppConfig,
                                  pain_points: Dict[str, Any],
                                  num_variants: int) -> List[Dict[str, Any]]:
    """Generate mutation proposals based on analytical pain point analysis.

    Creates variant proposals targeting identified pain points. Each proposal
    is a list of mutations with rationale.

    Args:
        harness: Current AppConfig from agent-config.yaml.
        pain_points: Pain point analysis from analyze_pain_points().
        num_variants: Number of distinct variant proposals to generate.

    Returns:
        List of proposal dicts, each containing id, description, mutations.
    """
    flagged = pain_points.get("flagged", [])
    stats = pain_points.get("stats", {})
    proposals: List[Dict[str, Any]] = []

    # Strategy 1: Temperature tweak for high error rate
    if "high_error_rate" in flagged:
        for agent_name in ["dev", "ctx"]:
            if agent_name in harness.agents:
                agent = harness.agents[agent_name]
                new_temp = min(agent.temperature + 0.1, 1.0)
                if new_temp != agent.temperature:
                    proposals.append({
                        "id": f"temp_{agent_name}",
                        "description": f"Increase {agent_name} temperature to reduce rigid tool-call errors",
                        "mutations": [{
                            "type": "temperature_tweak",
                            "agent": agent_name,
                            "field": "temperature",
                            "from": agent.temperature,
                            "to": new_temp,
                            "rationale": f"High avg error rate ({stats['avg_error_rate']:.1f}%). Slight temperature increase may help recover from incorrect tool calls.",
                        }],
                    })

    # Strategy 2: Model swap for high fix requests (give review a stronger model)
    if "excessive_fix_requests" in flagged:
        available_models = list(harness.llms.keys())
        if "review" in harness.agents and len(available_models) >= 2:
            current = harness.agents["review"].model_ref.model_path.split("/")[-1]
            for model_name in available_models:
                if model_name != current and "30b" in model_name:
                    proposals.append({
                        "id": f"model_swap_review_{model_name}",
                        "description": f"Upgrade review agent to {model_name} for better code assessment",
                        "mutations": [{
                            "type": "model_swap",
                            "agent": "review",
                            "field": "model",
                            "from": current,
                            "to": model_name,
                            "rationale": f"Average {stats['avg_fix_requests']:.1f} fix requests/task suggests review misses issues. Larger model may catch more.",
                        }],
                    })
                    break

    # Strategy 3: Tool reduction for excessive tool calls
    if "excessive_tool_calls" in flagged and "dev" in harness.agents:
        dev_tools = harness.agents["dev"].tools[:]
        removable = [t for t in dev_tools if t in ("task_tracker",)]
        if removable:
            reduced_tools = [t for t in dev_tools if t not in removable]
            proposals.append({
                "id": "reduce_dev_tools",
                "description": "Remove task_tracker from dev to reduce overhead tool calls",
                "mutations": [{
                    "type": "tool_add_remove",
                    "agent": "dev",
                    "field": "tools",
                    "from": dev_tools,
                    "to": reduced_tools,
                    "rationale": f"Avg {stats['avg_tool_calls']:.0f} tool calls/task is high. Reducing tool set may focus the agent.",
                }],
            })

    # Strategy 4: Temperature decrease for determinism (always propose as baseline improvement)
    for agent_name in ["dev"]:
        if agent_name in harness.agents:
            agent = harness.agents[agent_name]
            if agent.temperature > 0:
                proposals.append({
                    "id": f"deterministic_{agent_name}",
                    "description": f"Lower {agent_name} temperature for more deterministic outputs",
                    "mutations": [{
                        "type": "temperature_tweak",
                        "agent": agent_name,
                        "field": "temperature",
                        "from": agent.temperature,
                        "to": max(agent.temperature - 0.1, 0.0),
                        "rationale": "Lower temperature reduces variance and may improve reproducibility on complex tasks.",
                    }],
                })

    # Strategy 5: Increase temperature for creative problem solving
    for agent_name in ["genius"]:
        if agent_name in harness.agents:
            agent = harness.agents[agent_name]
            if agent.temperature < 0.5:
                proposals.append({
                    "id": f"creative_{agent_name}",
                    "description": f"Increase {agent_name} temperature for broader architectural exploration",
                    "mutations": [{
                        "type": "temperature_tweak",
                        "agent": agent_name,
                        "field": "temperature",
                        "from": agent.temperature,
                        "to": min(agent.temperature + 0.1, 1.0),
                        "rationale": "Higher temperature for architect role may yield better plans for hard tasks.",
                    }],
                })

    # Cap to requested number of variants
    if len(proposals) > num_variants:
        proposals = proposals[:num_variants]

    return proposals


def apply_mutations(harness: AppConfig,
                    mutations: List[Dict[str, Any]]) -> AppConfig:
    """Apply a list of mutations to a harness config, returning a new config.

    Does NOT modify the original harness. Uses deep copy for safety.

    Args:
        harness: Original AppConfig.
        mutations: List of mutation dicts with type, agent, field, from, to.

    Returns:
        New AppConfig with mutations applied.
    """
    variant = copy.deepcopy(harness)

    for mut in mutations:
        mut_type = mut["type"]
        agent_name = mut["agent"]
        field = mut["field"]
        new_value = mut["to"]

        if agent_name not in variant.agents:
            continue

        if mut_type == "temperature_tweak" and field == "temperature":
            variant.agents[agent_name].temperature = new_value

        elif mut_type == "model_swap" and field == "model":
            if new_value in variant.llms:
                # Re-validate by replacing the model_ref
                variant.agents[agent_name].model_ref = copy.deepcopy(variant.llms[new_value])

        elif mut_type == "tool_add_remove" and field == "tools":
            variant.agents[agent_name].tools = new_value

    return variant


def save_variant_config(variant: AppConfig, variant_id: str,
                        output_dir: str) -> str:
    """Save a variant config as a YAML file.

    Args:
        variant: Variant AppConfig to save.
        variant_id: Identifier string for the variant.
        output_dir: Directory to write the YAML file.

    Returns:
        Path to the saved file.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Convert to dict-like structure matching agent-config.yaml format
    config_dict = _app_config_to_yaml_dict(variant)
    filepath = os.path.join(output_dir, f"variant-{variant_id}.yaml")

    with open(filepath, "w") as f:
        yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)

    return filepath


def _app_config_to_yaml_dict(app: AppConfig) -> Dict[str, Any]:
    """Convert an AppConfig back to a YAML-friendly dict structure.

    Reverse of load_config(), producing the three-layer structure.
    """
    result: Dict[str, Any] = {"providers": {}, "llms": {}, "agents": {}}

    for pname, pconf in app.providers.items():
        result["providers"][pname] = pconf.model_dump()

    for lname, lconf in app.llms.items():
        d = lconf.model_dump()
        # Convert provider back to a ref string
        prov_name = _find_provider_name(app, lconf)
        d["provider"] = f"${{providers.{prov_name}}}"
        del d["provider_ref"]
        result["llms"][lname] = d

    for aname, aconf in app.agents.items():
        d = aconf.model_dump()
        # Convert model back to a ref string
        model_name = _find_llm_name(app, aconf.model_ref)
        d["model"] = f"${{llms.{model_name}}}"
        del d["model_ref"]
        result["agents"][aname] = d

    return result


def _find_provider_name(app: AppConfig, llm: LLMConfig) -> str:
    """Find the provider name for an LLM config by matching api_url."""
    for pname, pconf in app.providers.items():
        if pconf.api_url == llm.provider_ref.api_url:
            return pname
    return "openrouter"  # fallback


def _find_llm_name(app: AppConfig, llm: LLMConfig) -> str:
    """Find the LLM name for an LLM config by matching model_path."""
    for lname, lconf in app.llms.items():
        if lconf.model_path == llm.model_path:
            return lname
    return list(app.llms.keys())[0]  # fallback


def generate_variants(config_path: str = DEFAULT_CONFIG,
                      rho_config_path: str = DEFAULT_RHO_CONFIG,
                      output_dir: str = DEFAULT_OUTPUT_DIR,
                      coreset: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Main entry point: generate variant configs from coreset analysis.

    Args:
        config_path: Path to agent-config.yaml.
        rho_config_path: Path to rho_config.yaml.
        output_dir: Directory to save variant YAML files.
        coreset: Optional pre-selected coreset. If None, selects from archive.

    Returns:
        List of variant info dicts: {id, filepath, mutations, proposal_description}.
    """
    # Load configs
    harness = load_config(config_path)
    with open(rho_config_path) as f:
        rho_config = yaml.safe_load(f)

    num_variants = rho_config.get("num_variants", 3)

    # Select coreset if not provided
    if coreset is None:
        coreset, _ = select_coreset_from_archive(config_path=rho_config_path)

    # Analyze pain points
    pain_points = analyze_pain_points(coreset)

    # Generate proposals
    proposals = generate_analytical_proposals(harness, pain_points, num_variants)

    if not proposals:
        print("No proposals generated — no pain points detected or no suitable mutations.")
        return []

    # Apply mutations and save variant files
    variants: List[Dict[str, Any]] = []
    for proposal in proposals:
        variant_harness = apply_mutations(harness, proposal["mutations"])
        filepath = save_variant_config(variant_harness, proposal["id"], output_dir)

        variants.append({
            "id": proposal["id"],
            "filepath": filepath,
            "description": proposal["description"],
            "mutations": proposal["mutations"],
            "pain_points": pain_points.get("flagged", []),
        })

    return variants


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate harness variant configs for RHO")
    parser.add_argument("--variants", type=int, default=None)
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--config", type=str, default=DEFAULT_CONFIG)
    parser.add_argument("--rho-config", type=str, default=DEFAULT_RHO_CONFIG)
    args = parser.parse_args()

    variants = generate_variants(
        config_path=args.config,
        rho_config_path=args.rho_config,
        output_dir=args.output_dir,
    )

    print(f"Generated {len(variants)} variant configs:")
    for v in variants:
        print(f"  - {v['id']}: {v['description']}")
        print(f"    File: {v['filepath']}")
        for mut in v["mutations"]:
            print(f"    Mutate: {mut['type']} on {mut['agent']}.{mut['field']}: {mut['from']} -> {mut['to']}")


if __name__ == "__main__":
    main()
