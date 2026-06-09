#!/usr/bin/env python3
"""
Harness Proposer — Structured harness mutation proposals for RHO.

Analyzes coreset trajectories alongside the baseline harness config, then
generates structured mutation proposals. Uses the same config.py to load
and validate agent-config.yaml, ensuring proposals remain compatible.

Output: A list of VariantProposal objects, each containing a name, rationale,
and a list of MutationDelta entries specifying YAML dot-path changes.

Usage:
    python harness_proposer.py [--config PATH] [--coreset-json PATH]
"""

import json
import os
import re
import sys
import yaml
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from config import load_config, AppConfig
from coreset_selector import DifficultyScore, CoresetSelection


@dataclass
class MutationDelta:
    """A single field mutation within a variant."""

    path: str              # Dot-path, e.g. "agents.dev.temperature"
    old_value: Any         # Current value in baseline config
    new_value: Any         # Proposed new value


@dataclass
class VariantProposal:
    """One complete harness variant proposal."""

    variant_name: str
    rationale: str = ""
    mutations: List[MutationDelta] = field(default_factory=list)


# ── Parsing Structured Proposal Output ───────────────────────────────

VARIANT_BLOCK_RE = re.compile(
    r"=== VARIANT START ===\s*\n(.*?)\n=== VARIANT END ===",
    re.DOTALL,
)


def parse_proposal_block(block_text: str) -> Optional[VariantProposal]:
    """Parse a single variant block from proposer output.

    Expected format:
        variant_name: <name>
        rationale: <text>
        mutations:
          - path: <dot-path>
            old_value: <value>
            new_value: <value>

    Args:
        block_text: Raw text between VARIANT START/END markers.

    Returns:
        VariantProposal or None if parsing fails.
    """
    block_text = block_text.strip()
    lines = block_text.split("\n")

    variant_name = None
    rationale = None
    mutations: List[MutationDelta] = []
    current_mutation: Optional[Dict[str, Any]] = None
    in_mutations = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("variant_name:"):
            variant_name = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("rationale:"):
            rationale = stripped.split(":", 1)[1].strip()
        elif stripped == "mutations:":
            in_mutations = True
        elif in_mutations and stripped.startswith("- path:"):
            if current_mutation and all(k in current_mutation for k in ("path", "old_value", "new_value")):
                mutations.append(MutationDelta(
                    path=current_mutation["path"],
                    old_value=current_mutation["old_value"],
                    new_value=current_mutation["new_value"],
                ))
            current_mutation = {"path": stripped.split(":", 1)[1].strip()}
        elif in_mutations and current_mutation:
            if ":" in stripped:
                key, val = stripped.split(":", 1)
                key = key.strip()
                val = val.strip()
                if key in ("old_value", "new_value"):
                    current_mutation[key] = _parse_yaml_value(val)

    # Final mutation
    if current_mutation and all(k in current_mutation for k in ("path", "old_value", "new_value")):
        mutations.append(MutationDelta(
            path=current_mutation["path"],
            old_value=current_mutation["old_value"],
            new_value=current_mutation["new_value"],
        ))

    if not variant_name:
        return None

    return VariantProposal(
        variant_name=variant_name,
        rationale=rationale or "",
        mutations=mutations,
    )


def _parse_yaml_value(val_str: str) -> Any:
    """Parse a YAML-ish scalar value.

    Handles: integers, floats, booleans, quoted strings, lists, and bare strings.

    Args:
        val_str: String representation of a YAML value.

    Returns:
        Parsed Python value.
    """
    val_str = val_str.strip()

    try:
        return yaml.safe_load(val_str)
    except Exception:
        return val_str


def parse_proposals(text: str) -> List[VariantProposal]:
    """Extract all VariantProposals from proposer output text.

    Args:
        text: Raw output text from the proposer (potentially markdown-formatted).

    Returns:
        List of parsed VariantProposal objects.
    """
    proposals = []
    for match in VARIANT_BLOCK_RE.finditer(text):
        proposal = parse_proposal_block(match.group(1))
        if proposal:
            proposals.append(proposal)
    return proposals


# ── Mutation Validation ──────────────────────────────────────────────


SAFE_FIELDS = {
    "agents.*.temperature",
    "agents.*.tools",
    "agents.*.skills",
    "agents.*.prompt_file",
    "llms.*.default_temperature",
    "llms.*.max_output_tokens",
    "llms.*.top_p",
    "llms.*.top_k",
    "llms.*.repetition_penalty",
}

FORBIDDEN_PREFIXES = ("providers.",)


def is_safe_mutation(path: str) -> bool:
    """Check if a mutation path targets a safe (non-secret) config field.

    Args:
        path: Dot-path string (e.g. "agents.dev.temperature").

    Returns:
        True if the mutation is considered safe.
    """
    for prefix in FORBIDDEN_PREFIXES:
        if path.startswith(prefix):
            return False

    # Wildcard matching
    parts = path.split(".")
    if len(parts) >= 2 and parts[0] in ("agents", "llms"):
        return True

    return False


def validate_proposals(proposals: List[VariantProposal]) -> List[tuple[int, str]]:
    """Validate proposals against safety rules.

    Args:
        proposals: List of VariantProposal to validate.

    Returns:
        List of (index, error_message) tuples. Empty if all valid.
    """
    errors: List[tuple[int, str]] = []
    for idx, prop in enumerate(proposals):
        for mut in prop.mutations:
            if not is_safe_mutation(mut.path):
                errors.append((idx, f"Unsafe mutation path: {mut.path}"))
            if "." not in mut.path:
                errors.append((idx, f"Incomplete mutation path: {mut.path}"))
    return errors


# ── Auto-Generated Proposals (when no LLM proposer is available) ─────


def generate_heuristic_proposals(selection: CoresetSelection,
                                  config: AppConfig,
                                  num_variants: int = 2) -> List[VariantProposal]:
    """Generate heuristic-based proposals without an LLM proposer.

    Analyzes the coreset's difficulty profile and suggests targeted mutations.

    Args:
        selection: CoresetSelection from coreset_selector.
        config: Loaded AppConfig (baseline).
        num_variants: Number of variants to generate.

    Returns:
        List of VariantProposal objects.
    """
    proposals: List[VariantProposal] = []

    if not selection.selected:
        return proposals

    # Analyze dominant difficulty dimensions
    dims = ["error_rate", "fix_requests", "tool_calls", "runtime", "token_count"]
    dim_avgs: Dict[str, float] = {}
    for dim in dims:
        vals = [getattr(ds, dim, 0) for ds in selection.selected]
        dim_avgs[dim] = sum(vals) / len(vals) if vals else 0.0

    # Sort by dominant difficulty factor
    dominant_dims = sorted(dim_avgs.keys(), key=lambda d: dim_avgs[d], reverse=True)[:2]

    for i, dim in enumerate(dominant_dims[:num_variants]):
        variant = _heuristic_for_dimension(dim, config, dim_avgs[dim])
        if variant:
            proposals.append(variant)

    return proposals


def _heuristic_for_dimension(dim: str, config: AppConfig,
                              severity: float) -> Optional[VariantProposal]:
    """Generate a proposal targeting a specific difficulty dimension.

    Args:
        dim: Difficulty dimension name.
        config: Baseline AppConfig.
        severity: Normalized severity (0.0-1.0).

    Returns:
        VariantProposal or None.
    """
    if dim == "error_rate":
        # High error rates: lower temperature for determinism
        return _propose_temp_reduction(config, "lower-temp-for-stability",
                                        "High error rates suggest agents benefit from more deterministic behavior")

    elif dim == "fix_requests":
        # Many fix requests: give dev more focused tools
        return _propose_dev_tools_focused(config, "focused-dev-tools",
                                           "High fix requests suggest dev needs fewer but more relevant tools")

    elif dim == "tool_calls":
        # Excessive tool calls: tighter constraints
        return _propose_temps_reduction(config, "tighter-constraints",
                                         "Excessive tool calls indicate agents need tighter guidance")

    elif dim == "runtime":
        # Long runtime: reduce token budget
        return _propose_token_reduction(config, "reduced-token-budget",
                                         "Long runtimes suggest reducing max output tokens to prevent rambling")

    elif dim == "token_count":
        # High token count: more creativity needed
        return _propose_slight_warming(config, "slight-warmth-for-efficiency",
                                        "High token consumption suggests slightly higher temperature for conciseness")

    return None


def _get_agent_temp(config: AppConfig, agent_name: str) -> Optional[float]:
    """Get current temperature for an agent.

    Args:
        config: AppConfig.
        agent_name: Agent key name.

    Returns:
        Temperature or None.
    """
    agent = config.agents.get(agent_name)
    return agent.temperature if agent else None


def _propose_temp_reduction(config: AppConfig, name: str,
                             rationale: str) -> Optional[VariantProposal]:
    """Propose lowering dev agent temperature.

    Args:
        config: AppConfig.
        name: Variant name.
        rationale: Explanation text.

    Returns:
        VariantProposal.
    """
    temp = _get_agent_temp(config, "dev")
    if temp is None or temp <= 0.0:
        # Try genius agent
        temp = _get_agent_temp(config, "genius")
        if temp is None or temp <= 0.0:
            return None
        path = "agents.genius.temperature"
    else:
        path = "agents.dev.temperature"

    reduction = min(0.1, temp * 0.3)
    return VariantProposal(
        variant_name=name,
        rationale=rationale,
        mutations=[MutationDelta(path=path, old_value=temp, new_value=round(temp - reduction, 2))],
    )


def _propose_dev_tools_focused(config: AppConfig, name: str,
                                 rationale: str) -> Optional[VariantProposal]:
    """Propose reducing dev agent tools for focus.

    Args:
        config: AppConfig.
        name: Variant name.
        rationale: Explanation text.

    Returns:
        VariantProposal.
    """
    agent = config.agents.get("dev")
    if not agent:
        return None

    current_tools = agent.tools[:]
    # Remove task_tracker if present (reduce overhead)
    reduced = [t for t in current_tools if t != "task_tracker"]
    if reduced == current_tools:
        return None

    return VariantProposal(
        variant_name=name,
        rationale=rationale,
        mutations=[MutationDelta(path="agents.dev.tools", old_value=current_tools, new_value=reduced)],
    )


def _propose_temps_reduction(config: AppConfig, name: str,
                               rationale: str) -> Optional[VariantProposal]:
    """Similar to temp_reduction but targets review agent.

    Args:
        config: AppConfig.
        name: Variant name.
        rationale: Explanation text.

    Returns:
        VariantProposal.
    """
    temp = _get_agent_temp(config, "review")
    if temp is None or temp <= 0.0:
        return None

    new_temp = round(max(0.0, temp - 0.05), 2)
    return VariantProposal(
        variant_name=name,
        rationale=rationale,
        mutations=[MutationDelta(path="agents.review.temperature", old_value=temp, new_value=new_temp)],
    )


def _propose_token_reduction(config: AppConfig, name: str,
                               rationale: str) -> Optional[VariantProposal]:
    """Propose reducing max output tokens on an LLM.

    Args:
        config: AppConfig.
        name: Variant name.
        rationale: Explanation text.

    Returns:
        VariantProposal.
    """
    for llm_name, llm_cfg in config.llms.items():
        if llm_cfg.max_output_tokens and llm_cfg.max_output_tokens > 16384:
            return VariantProposal(
                variant_name=name,
                rationale=rationale,
                mutations=[MutationDelta(
                    path=f"llms.{llm_name}.max_output_tokens",
                    old_value=llm_cfg.max_output_tokens,
                    new_value=min(16384, llm_cfg.max_output_tokens // 2),
                )],
            )
    return None


def _propose_slight_warming(config: AppConfig, name: str,
                              rationale: str) -> Optional[VariantProposal]:
    """Propose slight temperature increase on dev agent.

    Args:
        config: AppConfig.
        name: Variant name.
        rationale: Explanation text.

    Returns:
        VariantProposal.
    """
    temp = _get_agent_temp(config, "dev")
    if temp is None:
        return None

    new_temp = round(min(0.5, temp + 0.1), 2)
    if new_temp == temp:
        return None

    return VariantProposal(
        variant_name=name,
        rationale=rationale,
        mutations=[MutationDelta(path="agents.dev.temperature", old_value=temp, new_value=new_temp)],
    )


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Harness Proposer for RHO")
    parser.add_argument("--coreset-json", type=str, default=None,
                        help="Path to coreset selection JSON (stdin if omitted)")
    parser.add_argument("--output", type=str, default="proposal_output.txt",
                        help="Path to save parsed proposals as JSON")
    parser.add_argument("--raw-input", type=str, default=None,
                        help="Path to raw proposer LLM output for parsing")
    parser.add_argument("--heuristic", action="store_true",
                        help="Generate heuristic proposals without LLM")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to agent-config.yaml")
    args = parser.parse_args()

    # Resolve config path
    if args.config:
        config_path = args.config
    else:
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "agent-config.yaml")

    # Parse raw LLM output
    if args.raw_input:
        with open(args.raw_input, encoding="utf-8") as f:
            raw = f.read()
        proposals = parse_proposals(raw)
    elif args.heuristic:
        # Heuristic mode: load coreset and config, generate proposals
        from coreset_selector import select_coreset, load_rho_weights

        rho_config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "rho_config.yaml")
        rho_cfg = {}
        if os.path.isfile(rho_config_path):
            with open(rho_config_path) as f:
                rho_cfg = yaml.safe_load(f) or {}
        
        archive_path = rho_cfg.get("archive", {}).get("path", ".rho/trajectories")
        target_size = rho_cfg.get("coreset", {}).get("size", 5)
        num_variants = rho_cfg.get("mutation", {}).get("num_variants", 2)
        weights = load_rho_weights(rho_config_path)

        selection = select_coreset(Path(archive_path), target_size=target_size, weights=weights)
        config = load_config(config_path)
        proposals = generate_heuristic_proposals(selection, config, num_variants=num_variants)
    else:
        print("[proposer] Use --raw-input to parse LLM output or --heuristic for auto-generation.")
        sys.exit(1)

    # Validate
    validation_errors = validate_proposals(proposals)
    if validation_errors:
        for idx, err in validation_errors:
            print(f"[proposer] Validation warning [{idx}]: {err}")

    if proposals:
        print(f"[proposer] Found {len(proposals)} proposals:")
        for i, p in enumerate(proposals):
            print(f"  Variant {i + 1}: {p.variant_name}")
            print(f"    Rationale: {p.rationale}")
            for m in p.mutations:
                print(f"    - {m.path}: {m.old_value} → {m.new_value}")
    else:
        print("[proposer] No valid proposals found.")

    # Save to JSON
    output_data = [
        {
            "variant_name": p.variant_name,
            "rationale": p.rationale,
            "mutations": [
                {"path": m.path, "old_value": m.old_value, "new_value": m.new_value}
                for m in p.mutations
            ],
        }
        for p in proposals
    ]
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, default=str)
    print(f"[proposer] Saved proposals to {args.output}")


if __name__ == "__main__":
    main()
