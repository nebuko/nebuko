#!/usr/bin/env python3
"""
Harness proposer for Retrospective Harness Optimization (RHO).

Analyzes trajectory coreset and proposes harness mutations by generating
a structured prompt for the agent. Parses the agent's proposal response
into YAML deltas that can be applied to agent-config.yaml.

Input: Coreset JSON (.rho/coreset.json), current agent-config.yaml
Output: Variant config files (.rho/variants/variant-{id}.yaml)
"""

import json
import os
import re
import sys
import yaml
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
from config import load_config, resolve_refs


# ── Data Models ──────────────────────────────────────────────────────


@dataclass
class Mutation:
    """A single mutation to the harness config."""

    type: str
    agent: str
    field: str
    old_value: Any
    new_value: Any
    reason: str


@dataclass
class Proposal:
    """A complete harness proposal (one variant)."""

    id: str
    rationale: str
    mutations: List[Mutation] = field(default_factory=list)
    predicted_improvement: str = ""


# ── Proposal Parsing ─────────────────────────────────────────────────


def _find_json_with_brace_matching(text: str, keyword: str) -> Optional[str]:
    """
    Find a JSON object in text containing the given keyword, using brace matching.

    Scans for the first '{' before the keyword, then counts braces to find
    the matching closing brace.

    Args:
        text: The full text to search in.
        keyword: A keyword that should appear inside the JSON object.

    Returns:
        The matched JSON string, or None if not found.
    """
    keyword_idx = text.find(keyword)
    if keyword_idx < 0:
        return None

    # Find the first '{' before the keyword
    open_brace_idx = text.rfind("{", 0, keyword_idx)
    if open_brace_idx < 0:
        return None

    # Count braces to find the matching closing brace
    depth = 0
    in_string = False
    escape_next = False

    for i in range(open_brace_idx, len(text)):
        char = text[i]

        if escape_next:
            escape_next = False
            continue

        if char == "\\":
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string

        if not in_string:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[open_brace_idx : i + 1]

    return None


def parse_proposals_from_text(text: str) -> List[Proposal]:
    """
    Parse structured proposals from agent response text.

    Expects JSON wrapped in ```json code blocks. Falls back to trying
    to find raw JSON if no code blocks are found.

    Args:
        text: The agent's response text.

    Returns:
        List of parsed Proposal objects.
    """
    # Try to extract JSON from code blocks first
    json_blocks = re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)

    json_strings = json_blocks if json_blocks else []

    # Fallback: try to find JSON object in the text
    if not json_strings:
        json_str = _find_json_with_brace_matching(text, '"proposals"')
        if json_str:
            json_strings = [json_str]

    proposals = []
    for json_str in json_strings:
        try:
            data = json.loads(json_str.strip())
            proposals = _parse_proposals_dict(data)
            if proposals:
                break
        except json.JSONDecodeError:
            continue

    return proposals


def _parse_proposals_dict(data: Dict) -> List[Proposal]:
    """
    Parse a proposals dictionary into Proposal objects.

    Args:
        data: Parsed JSON dict with 'proposals' key.

    Returns:
        List of Proposal objects.
    """
    proposals = []
    predictions = data.get("predicted_improvements", {})

    for proposal_data in data.get("proposals", []):
        mutations = []
        for mut_data in proposal_data.get("mutations", []):
            mutations.append(Mutation(
                type=mut_data.get("type", "unknown"),
                agent=mut_data.get("agent", ""),
                field=mut_data.get("field", ""),
                old_value=mut_data.get("old_value"),
                new_value=mut_data.get("new_value"),
                reason=mut_data.get("reason", ""),
            ))

        proposals.append(Proposal(
            id=proposal_data.get("id", f"variant-{len(proposals) + 1}"),
            rationale=proposal_data.get("rationale", ""),
            mutations=mutations,
            predicted_improvement=predictions.get(proposal_data.get("id", ""), ""),
        ))

    return proposals


# ── Config Mutation Application ──────────────────────────────────────


def apply_mutation_to_raw_config(
    raw_config: Dict,
    mutation: Mutation,
) -> Dict:
    """
    Apply a single mutation to the raw (unresolved) YAML config dict.

    Returns a new dict with the mutation applied. The original is unchanged.

    Args:
        raw_config: The raw config dict from YAML.
        mutation: The mutation to apply.

    Returns:
        New config dict with the mutation applied.
    """
    import copy
    config = copy.deepcopy(raw_config)

    agents = config.get("agents", {})
    agent_name = mutation.agent

    if agent_name not in agents:
        print(f"Warning: Agent '{agent_name}' not found in config, skipping mutation.")
        return config

    agent = agents[agent_name]

    if mutation.type == "temperature":
        agent["temperature"] = mutation.new_value

    elif mutation.type == "model":
        agent["model"] = mutation.new_value

    elif mutation.type == "tools":
        current_tools = list(agent.get("tools", []))
        # Handle add/remove notation
        if isinstance(mutation.new_value, list):
            agent["tools"] = mutation.new_value
        elif isinstance(mutation.new_value, str):
            # Parse "+tool" or "-tool" notation
            for change in mutation.new_value.split(","):
                change = change.strip()
                if change.startswith("+"):
                    tool = change[1:]
                    if tool not in current_tools:
                        current_tools.append(tool)
                elif change.startswith("-"):
                    tool = change[1:]
                    if tool in current_tools:
                        current_tools.remove(tool)
            agent["tools"] = current_tools

    elif mutation.type == "prompt":
        # Prompt mutations are stored as metadata for manual review
        agent["_proposed_prompt_change"] = mutation.new_value

    elif mutation.type == "max_output_tokens":
        # This is an LLM-level setting, need to find the model
        model_ref = agent.get("model", "")
        # Extract model name from ref like "${llms.qwen-3-6-27b}"
        model_name = re.sub(r"\$\{llms\.(.*?)\}", r"\1", model_ref)
        if model_name and model_name in config.get("llms", {}):
            config["llms"][model_name]["max_output_tokens"] = mutation.new_value

    return config


def generate_variant_config(
    raw_config: Dict,
    proposal: Proposal,
) -> Dict:
    """
    Generate a complete variant config by applying all mutations in a proposal.

    Args:
        raw_config: The raw config dict from YAML.
        proposal: The proposal containing mutations.

    Returns:
        New config dict with all mutations applied.
    """
    config = raw_config
    for mutation in proposal.mutations:
        config = apply_mutation_to_raw_config(config, mutation)
    return config


# ── Prompt Generation ────────────────────────────────────────────────


def build_proposer_prompt(
    current_config: Dict,
    coreset: List[Dict],
    prompt_file: str = ".agents/prompts/rho_proposer.md",
) -> str:
    """
    Build the full prompt for the harness proposer agent.

    Args:
        current_config: The current agent-config.yaml as a dict.
        coreset: The selected coreset from coreset_selector.py.
        prompt_file: Path to the proposer system prompt.

    Returns:
        Complete prompt string for the proposer agent.
    """
    # Load system prompt
    system_prompt = ""
    if os.path.isfile(prompt_file):
        with open(prompt_file) as f:
            system_prompt = f.read()

    # Build context section
    config_summary = yaml.dump(current_config, default_flow_style=False)

    # Summarize coreset
    coreset_summary = []
    for item in coreset:
        metrics = item.get("metrics", {})
        coreset_summary.append(
            f"- Issue #{item.get('issue_number', '?')}: '{item.get('title', 'N/A')}' "
            f"(score={item.get('score', 0):.3f}, "
            f"errors={metrics.get('error_rate', 0):.1f}%, "
            f"fixes={metrics.get('review_fix_requests', 0)}, "
            f"tools={metrics.get('tool_calls', 0)}, "
            f"runtime={metrics.get('runtime_ms', 0):.0f}ms, "
            f"tokens={metrics.get('total_tokens', 0)})"
        )

    context = f"""
---
# Current Harness Configuration
```yaml
{config_summary}
```

# Coreset Trajectories (Challenging Past Tasks)
{chr(10).join(coreset_summary)}

# Your Task
Analyze these trajectories and propose 2-3 specific mutations to the harness
config that would improve performance on similar tasks. Focus on the weakest
signals (high error rates, many fix requests, excessive tool calls or runtime).
"""

    return system_prompt + context


# ── Save Variants ────────────────────────────────────────────────────


def save_variant_configs(
    proposals: List[Proposal],
    raw_config: Dict,
    output_dir: str = ".rho/variants",
) -> List[str]:
    """
    Generate and save variant config files for each proposal.

    Args:
        proposals: List of proposals from the proposer.
        raw_config: The raw config dict from YAML.
        output_dir: Directory to save variant configs.

    Returns:
        List of file paths for saved variant configs.
    """
    os.makedirs(output_dir, exist_ok=True)
    paths = []

    for proposal in proposals:
        variant_config = generate_variant_config(raw_config, proposal)
        filename = f"variant-{proposal.id}.yaml"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w") as f:
            yaml.dump(variant_config, f, default_flow_style=False)

        # Save proposal metadata alongside the config
        meta_path = filepath.replace(".yaml", ".json")
        with open(meta_path, "w") as f:
            json.dump({
                "id": proposal.id,
                "rationale": proposal.rationale,
                "predicted_improvement": proposal.predicted_improvement,
                "mutations": [
                    {
                        "type": m.type,
                        "agent": m.agent,
                        "field": m.field,
                        "old_value": m.old_value,
                        "new_value": m.new_value,
                        "reason": m.reason,
                    }
                    for m in proposal.mutations
                ],
            }, f, indent=2)

        paths.append(filepath)
        print(f"Saved variant config: {filepath}")

    return paths


# ── CLI Entry Point ──────────────────────────────────────────────────


def main():
    """
    CLI: Load coreset, parse agent proposals, generate variant configs.

    Expects:
        RHO_PROPOSAL_TEXT: Path to file containing the proposer agent's response
        OR inline JSON via PROPOSAL_JSON env var
    """
    # Load current config
    config_path = os.environ.get("AGENT_CONFIG_PATH", "agent-config.yaml")
    config = load_config(config_path)

    # Load raw config for mutation
    with open(config_path) as f:
        raw_config = yaml.safe_load(f)

    # Load coreset
    coreset_path = ".rho/coreset.json"
    if not os.path.isfile(coreset_path):
        print("Error: No coreset found. Run coreset_selector.py first.")
        sys.exit(1)

    with open(coreset_path) as f:
        coreset_data = json.load(f)

    coreset = coreset_data.get("selected", [])

    # Get proposals from agent response
    proposal_text = os.environ.get("PROPOSAL_JSON", "")

    # If no JSON env, try to read from file
    if not proposal_text:
        proposal_file = os.environ.get("RHO_PROPOSAL_TEXT", "")
        if proposal_file and os.path.isfile(proposal_file):
            with open(proposal_file) as f:
                proposal_text = f.read()
        else:
            print("Error: No proposal input. Set PROPOSAL_JSON or RHO_PROPOSAL_TEXT.")
            sys.exit(1)

    proposals = parse_proposals_from_text(proposal_text)

    if not proposals:
        print("Warning: No valid proposals parsed from agent response.")
        print("Raw input preview:", proposal_text[:500])
        sys.exit(1)

    print(f"Parsed {len(proposals)} proposals:")
    for p in proposals:
        print(f"  {p.id}: {p.rationale}")
        for m in p.mutations:
            print(f"    - {m.type}: {m.agent}.{m.field} = {m.old_value} → {m.new_value}")

    # Save variant configs
    paths = save_variant_configs(proposals, raw_config)
    print(f"\nGenerated {len(paths)} variant configs.")


if __name__ == "__main__":
    main()
