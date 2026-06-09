#!/usr/bin/env python3
"""
RHO Rollout — Parallel variant evaluation against coreset tasks.

Applies each variant proposal's mutations to the baseline agent-config.yaml,
then orchestrates re-execution of the selected coreset tasks under each variant
configuration. Collects resulting metrics for comparison.

Does NOT actually run the OpenHands agent — that is handled by the GitHub
Actions workflow (rho.yml). This script generates the variant config files
and the rollout manifest for the workflow to consume.

Usage:
    python rho_rollout.py [options]
"""

import copy
import json
import os
import sys
import yaml
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from config import load_config, AppConfig, resolve_refs
from harness_proposer import VariantProposal, parse_proposals


@dataclass
class VariantConfig:
    """One variant configuration ready for rollout."""

    variant_index: int
    variant_name: str
    config_path: str          # Path to the variant's agent-config.yaml
    rationale: str = ""
    mutations_applied: int = 0


@dataclass
class RolloutManifest:
    """Rollout plan describing what the CI workflow should execute."""

    variant_configs: List[VariantConfig] = field(default_factory=list)
    coreset_issues: List[int] = field(default_factory=list)
    baseline_config_path: str = ""


# ── Mutation Application ─────────────────────────────────────────────


def apply_mutation(config_dict: Dict[str, Any], path: str, new_value: Any) -> bool:
    """Apply a single mutation to a resolved config dict.

    Walks the dot-path and replaces the leaf value. Returns False if the
    path cannot be resolved.

    Args:
        config_dict: Mutable config dict (already resolved refs).
        path: Dot-separated path (e.g. "agents.dev.temperature").
        new_value: New value to assign.

    Returns:
        True if successful, False if path doesn't exist.
    """
    parts = path.split(".")
    current = config_dict

    for part in parts[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False

    last_key = parts[-1]
    if isinstance(current, dict) and last_key in current:
        current[last_key] = new_value
        return True

    return False


def apply_variant(baseline_raw: Dict[str, Any],
                  proposal: VariantProposal) -> Dict[str, Any]:
    """Apply all mutations from a proposal to the baseline config.

    Args:
        baseline_raw: Raw (unresolved) baseline config dict from YAML.
        proposal: VariantProposal with mutations.

    Returns:
        Modified config dict ready to serialize as YAML.
    """
    variant = copy.deepcopy(baseline_raw)

    for mutation in proposal.mutations:
        success = apply_mutation(variant, mutation.path, mutation.new_value)
        if not success:
            print(f"[rollout] Warning: Failed to apply mutation {mutation.path}")

    return variant


# ── Rollout Preparation ──────────────────────────────────────────────


def prepare_rollout(config_path: str,
                    proposals_path: str,
                    coreset_issues: List[int],
                    output_dir: str = ".rho/rollout") -> RolloutManifest:
    """Generate variant configs and rollout manifest.

    Args:
        config_path: Path to baseline agent-config.yaml.
        proposals_path: Path to proposals JSON file (from harness_proposer.py).
        coreset_issues: List of issue numbers from coreset selection.
        output_dir: Directory to write variant configs and manifest.

    Returns:
        RolloutManifest describing the planned rollout.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Load baseline
    baseline_raw = None
    with open(config_path) as f:
        baseline_raw = yaml.safe_load(f)

    if not baseline_raw:
        print("[rollout] Error: Baseline config is empty or invalid.")
        sys.exit(1)

    # Copy baseline as the reference
    baseline_variant_path = output_path / "baseline-agent-config.yaml"
    with open(baseline_variant_path, "w") as f:
        yaml.dump(baseline_raw, f, default_flow_style=False)

    # Load proposals
    with open(proposals_path) as f:
        proposals_data = json.load(f)

    proposals = []
    for pd_item in proposals_data:
        from harness_proposer import MutationDelta
        mutations = [
            MutationDelta(
                path=m["path"],
                old_value=m["old_value"],
                new_value=m["new_value"],
            )
            for m in pd_item.get("mutations", [])
        ]
        proposals.append(VariantProposal(
            variant_name=pd_item.get("variant_name", f"variant-{len(proposals)}"),
            rationale=pd_item.get("rationale", ""),
            mutations=mutations,
        ))

    # Generate variant configs
    variant_configs: List[VariantConfig] = []
    for idx, proposal in enumerate(proposals):
        variant_dict = apply_variant(baseline_raw, proposal)
        variant_filename = f"variant-{idx}-{proposal.variant_name}.yaml"
        variant_path = output_path / variant_filename

        with open(variant_path, "w") as f:
            yaml.dump(variant_dict, f, default_flow_style=False)

        variant_configs.append(VariantConfig(
            variant_index=idx,
            variant_name=proposal.variant_name,
            config_path=str(variant_path),
            rationale=proposal.rationale,
            mutations_applied=len(proposal.mutations),
        ))

    # Also store the raw config path relative to runner working dir
    manifest = RolloutManifest(
        variant_configs=variant_configs,
        coreset_issues=coreset_issues,
        baseline_config_path=str(baseline_variant_path),
    )

    # Write manifest
    manifest_data = {
        "baseline_config_path": manifest.baseline_config_path,
        "coreset_issues": manifest.coreset_issues,
        "variants": [
            {
                "variant_index": vc.variant_index,
                "variant_name": vc.variant_name,
                "config_path": vc.config_path,
                "rationale": vc.rationale,
                "mutations_applied": vc.mutations_applied,
            }
            for vc in variant_configs
        ],
    }

    manifest_path = output_path / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest_data, f, indent=2)

    print(f"[rollout] Prepared {len(variant_configs)} variants + baseline")
    print(f"[rollout] Manifest: {manifest_path}")
    print(f"[rollout] Coreset issues: {coreset_issues}")

    for vc in variant_configs:
        print(f"  Variant {vc.variant_index}: {vc.variant_name} "
              f"({vc.mutations_applied} mutations) → {vc.config_path}")

    return manifest


# ── Metric Collection ────────────────────────────────────────────────


def collect_rollout_metrics(rollout_dir: str = ".rho/rollout/results") -> Dict[str, Any]:
    """Collect metrics from completed rollout runs.

    Each run writes a metrics JSON file at:
        <rollout_dir>/<variant_name>/<issue_number>/metrics.json

    Args:
        rollout_dir: Base directory for rollout results.

    Returns:
        Nested dict: { variant_name: { issue_number: metrics_dict } }
    """
    results: Dict[str, Any] = {}
    base_path = Path(rollout_dir)

    if not base_path.exists():
        return results

    for variant_dir in base_path.iterdir():
        if not variant_dir.is_dir():
            continue

        variant_name = variant_dir.name
        results[variant_name] = {}

        for issue_dir in variant_dir.iterdir():
            metrics_file = issue_dir / "metrics.json"
            if metrics_file.exists():
                issue_num = int(issue_dir.name)
                try:
                    with open(metrics_file) as f:
                        results[variant_name][issue_num] = json.load(f)
                except json.JSONDecodeError:
                    print(f"[rollout] Warning: Invalid metrics at {metrics_file}")

    return results


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="RHO Rollout — Prepare variant configs")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to baseline agent-config.yaml")
    parser.add_argument("--proposals", type=str, default=None,
                        help="Path to proposals JSON from harness_proposer.py")
    parser.add_argument("--coreset-issues", type=str, default=None,
                        help="Comma-separated issue numbers for coreset")
    parser.add_argument("--output-dir", type=str, default=".rho/rollout",
                        help="Output directory for variant configs")
    parser.add_argument("--collect", action="store_true",
                        help="Collect metrics from completed runs instead of preparing")
    args = parser.parse_args()

    if args.collect:
        results = collect_rollout_metrics()
        print(json.dumps(results, indent=2))
        return

    # Resolve paths
    repo_root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..")
    config_path = args.config or os.path.join(repo_root, "agent-config.yaml")
    proposals_path = args.proposals or os.path.join(repo_root, "rho_proposals.json")

    coreset_issues = []
    if args.coreset_issues:
        coreset_issues = [int(x.strip()) for x in args.coreset_issues.split(",")]

    prepare_rollout(config_path, proposals_path, coreset_issues, args.output_dir)


if __name__ == "__main__":
    main()
