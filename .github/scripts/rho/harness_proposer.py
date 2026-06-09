#!/usr/bin/env python3
"""
Harness proposer — generate structured mutation proposals via agent analysis.

Feeds coreset trajectories and current harness config to a proposer agent,
then parses the structured output into candidate harness variants.

Port/Adapter design:
    AgentRunner  —  Adapter (runs agent_script.py with variant config)
    ProposalParser —  Core logic (parses structured output)
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(__file__) + "/..")

from rho.coreset_selector import ScoredTrajectory
from rho.trajectory_archive import Trajectory


# ── Data Models ───────────────────────────────────────────────────────

@dataclass
class Mutation:
    """A single harness mutation."""

    type: str  # "temperature", "model", "tools", "prompt"
    agent: str
    field: str
    old_value: Any
    new_value: Any
    rationale: str


@dataclass
class VariantProposal:
    """A complete variant proposal with multiple mutations."""

    variant_id: str
    description: str
    mutations: List[Mutation] = field(default_factory=list)


# ── Parsing Logic ─────────────────────────────────────────────────────

PROPOSAL_JSON_PATTERN = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def parse_proposals_from_text(text: str) -> List[VariantProposal]:
    """
    Parse variant proposals from agent response text.

    Extracts JSON from code blocks and validates structure.

    Args:
        text: Raw text from the proposer agent.

    Returns:
        List of valid VariantProposal objects.
    """
    proposals: list[VariantProposal] = []

    # Find all JSON code blocks
    matches = PROPOSAL_JSON_PATTERN.findall(text)

    for json_str in matches:
        json_str = json_str.strip()
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            continue

        # Handle both single proposal and list of proposals
        if isinstance(data, dict):
            data = [data]

        for item in data:
            proposal = _parse_single_proposal(item)
            if proposal:
                proposals.append(proposal)

    return proposals


def _parse_single_proposal(data: Dict[str, Any]) -> Optional[VariantProposal]:
    """Parse a single proposal dict into a VariantProposal object."""
    if not isinstance(data, dict):
        return None

    variant_id = data.get("variant_id")
    description = data.get("description", "")
    mutations_data = data.get("mutations", [])

    if not variant_id:
        return None

    mutations = []
    for m_data in mutations_data:
        mutation = Mutation(
            type=m_data.get("type", "unknown"),
            agent=m_data.get("agent", ""),
            field=m_data.get("field", ""),
            old_value=m_data.get("old_value"),
            new_value=m_data.get("new_value"),
            rationale=m_data.get("rationale", ""),
        )
        mutations.append(mutation)

    return VariantProposal(
        variant_id=variant_id,
        description=description,
        mutations=mutations,
    )


# ── Prompt Building ───────────────────────────────────────────────────

def build_proposer_prompt(
    coreset: List[ScoredTrajectory],
    harness_config: Dict[str, Any],
    proposer_prompt_file: Optional[str] = None,
) -> str:
    """
    Build the full prompt for the harness proposer agent.

    Args:
        coreset: Selected challenging trajectories.
        harness_config: Current harness config dict.
        proposer_prompt_file: Path to the proposer system prompt file.

    Returns:
        Complete prompt string.
    """
    # Load system prompt
    if proposer_prompt_file and os.path.exists(proposer_prompt_file):
        with open(proposer_prompt_file) as f:
            system_prompt = f.read()
    else:
        system_prompt = "Analyze the following trajectories and propose harness mutations."

    # Summarize coreset
    trajectory_summaries = []
    for st in coreset:
        traj = st.trajectory
        summary = {
            "issue_number": traj.issue_number,
            "title": traj.issue_title[:100],
            "score": round(st.score, 3),
            "component_scores": {k: round(v, 3) for k, v in st.component_scores.items()},
            "metrics_summary": [],
        }

        # Summarize metrics per agent role
        role_stats: Dict[str, Dict[str, list]] = {}
        for m in traj.metrics:
            role = m.agent_role or "unknown"
            if role not in role_stats:
                role_stats[role] = {
                    "error_rates": [],
                    "fix_requests": [],
                    "tool_calls": [],
                    "runtimes": [],
                    "tokens": [],
                }
            role_stats[role]["error_rates"].append(m.tool_call_error_rate)
            role_stats[role]["fix_requests"].append(m.review_fix_requests)
            role_stats[role]["tool_calls"].append(m.nb_tool_calls)
            role_stats[role]["runtimes"].append(m.agent_runtime_ms)
            role_stats[role]["tokens"].append(m.input_tokens + m.output_tokens)

        for role, stats in role_stats.items():
            summary["metrics_summary"].append({
                "role": role,
                "runs": len(stats["error_rates"]),
                "avg_error_rate": round(sum(stats["error_rates"]) / len(stats["error_rates"]), 2) if stats["error_rates"] else 0,
                "total_fix_requests": sum(stats["fix_requests"]),
                "avg_tool_calls": round(sum(stats["tool_calls"]) / len(stats["tool_calls"]), 1) if stats["tool_calls"] else 0,
                "avg_runtime_ms": round(sum(stats["runtimes"]) / len(stats["runtimes"]), 0) if stats["runtimes"] else 0,
                "avg_tokens": round(sum(stats["tokens"]) / len(stats["tokens"]), 0) if stats["tokens"] else 0,
            })

        trajectory_summaries.append(summary)

    # Build user prompt
    user_prompt = f"""
## Current Harness Config

```yaml
{yaml.dump(harness_config, default_flow_style=False)}
```

## Coreset Trajectories ({len(coreset)} challenging tasks)

```json
{json.dumps(trajectory_summaries, indent=2)}
```

Please analyze these trajectories and propose 3 variant harness configurations that could improve performance on similar tasks.
"""

    return system_prompt + "\n\n" + user_prompt


# ── Variant Application ───────────────────────────────────────────────

def apply_mutations(
    harness_config: Dict[str, Any],
    proposal: VariantProposal,
) -> Dict[str, Any]:
    """
    Apply a variant's mutations to the harness config.

    Returns a new config dict with all mutations applied.

    Args:
        harness_config: Original harness config dict.
        proposal: Variant proposal with mutations.

    Returns:
        New config dict with mutations applied.
    """
    import copy
    config = copy.deepcopy(harness_config)

    for mutation in proposal.mutations:
        if mutation.type == "temperature" and mutation.agent in config.get("agents", {}):
            config["agents"][mutation.agent]["temperature"] = mutation.new_value

        elif mutation.type == "model" and mutation.agent in config.get("agents", {}):
            config["agents"][mutation.agent]["model"] = mutation.new_value

        elif mutation.type == "tools" and mutation.agent in config.get("agents", {}):
            config["agents"][mutation.agent]["tools"] = mutation.new_value

        elif mutation.type == "prompt":
            # Prompt mutations are noted but applied separately
            # (prompt files are managed outside the config)
            pass

    return config


def generate_variant_configs(
    harness_config: Dict[str, Any],
    proposals: List[VariantProposal],
) -> List[Dict[str, Any]]:
    """
    Generate variant config dicts from proposals.

    Args:
        harness_config: Original harness config.
        proposals: List of variant proposals.

    Returns:
        List of variant config dicts.
    """
    return [apply_mutations(harness_config, p) for p in proposals]


def main():
    """CLI entry point for harness proposal generation."""
    archive_dir = os.environ.get("RHO_ARCHIVE_DIR", ".rho/trajectories")
    rho_config_path = os.environ.get("RHO_CONFIG_PATH", ".github/scripts/rho/rho_config.yaml")
    harness_config_path = os.environ.get("HARNESS_CONFIG_PATH", "agent-config.yaml")

    # Load configs
    with open(rho_config_path) as f:
        rho_config = yaml.safe_load(f)

    with open(harness_config_path) as f:
        harness_config = yaml.safe_load(f)

    print(f"RHO config loaded: {rho_config_path}")
    print(f"Harness config loaded: {harness_config_path}")
    print(f"Variant count: {rho_config.get('variants', {}).get('count', 3)}")

    # This would normally call the agent_script.py with the proposer prompt
    # For now, we print the prompt structure
    print("Proposer prompt would be built and executed via agent_script.py")


if __name__ == "__main__":
    main()
