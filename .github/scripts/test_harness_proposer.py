#!/usr/bin/env python3
"""Tests for harness_proposer.py — proposal parsing and config mutation."""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from harness_proposer import (
    Mutation,
    Proposal,
    apply_mutation_to_raw_config,
    build_proposer_prompt,
    generate_variant_config,
    parse_proposals_from_text,
    save_variant_configs,
)


# ── Minimal test fixtures ────────────────────────────────────────────

BASE_CONFIG = {
    "providers": {"openrouter": {"api_url": "https://api.example.com", "api_token_env": "KEY"}},
    "llms": {
        "model-a": {"model_path": "x/model-a", "provider": "${providers.openrouter}", "default_temperature": 0.0},
        "model-b": {"model_path": "x/model-b", "provider": "${providers.openrouter}", "default_temperature": 0.0},
    },
    "agents": {
        "dev": {"model": "${llms.model-a}", "temperature": 0.0, "prompt_file": "dev.md", "tools": ["terminal", "file_editor"]},
        "genius": {"model": "${llms.model-a}", "temperature": 0.2, "prompt_file": "genius.md", "tools": ["think", "finish"]},
    },
}

SCORED_TRAJECTORIES = [
    {"issue_number": 1, "title": "Bug A", "score": 0.9, "metrics": {"error_rate": 15.0, "tool_calls": 30, "runtime_ms": 120000, "total_tokens": 50000, "review_fix_requests": 3}},
    {"issue_number": 2, "title": "Bug B", "score": 0.7, "metrics": {"error_rate": 5.0, "tool_calls": 10, "runtime_ms": 30000, "total_tokens": 15000, "review_fix_requests": 0}},
]


class TestParseProposalsFromText(unittest.TestCase):
    def test_parse_json_code_block(self):
        text = """Here is my proposal:
```json
{
  "proposals": [
    {
      "id": "variant-A",
      "rationale": "Lower temperature",
      "mutations": [
        {"type": "temperature", "agent": "dev", "field": "temperature", "old_value": 0.0, "new_value": 0.1, "reason": "Reduce errors"}
      ]
    }
  ],
  "predicted_improvements": {"variant-A": "Fewer errors"}
}
```
End of response."""
        proposals = parse_proposals_from_text(text)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].id, "variant-A")
        self.assertEqual(len(proposals[0].mutations), 1)
        self.assertEqual(proposals[0].mutations[0].new_value, 0.1)

    def test_parse_raw_json_without_code_block(self):
        text = """Some text
{"proposals": [{"id": "v1", "rationale": "test", "mutations": [{"type": "temperature", "agent": "dev", "field": "temperature", "old_value": 0.0, "new_value": 0.2, "reason": "r"}]}], "predicted_improvements": {"v1": "improvement"}}
More text."""
        proposals = parse_proposals_from_text(text)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].id, "v1")

    def test_parse_empty_text(self):
        proposals = parse_proposals_from_text("")
        self.assertEqual(len(proposals), 0)

    def test_parse_invalid_json(self):
        proposals = parse_proposals_from_text("not json at all")
        self.assertEqual(len(proposals), 0)

    def test_parse_multiple_proposals(self):
        text = """```json
{
  "proposals": [
    {"id": "A", "rationale": "r1", "mutations": [{"type": "temperature", "agent": "dev", "field": "temperature", "old_value": 0.0, "new_value": 0.1, "reason": "r"}]},
    {"id": "B", "rationale": "r2", "mutations": [{"type": "model", "agent": "genius", "field": "model", "old_value": "old", "new_value": "new", "reason": "r"}]}
  ],
  "predicted_improvements": {}
}```"""
        proposals = parse_proposals_from_text(text)
        self.assertEqual(len(proposals), 2)
        self.assertEqual(proposals[0].id, "A")
        self.assertEqual(proposals[1].id, "B")


class TestApplyMutationToRawConfig(unittest.TestCase):
    def test_temperature_mutation(self):
        mut = Mutation(type="temperature", agent="dev", field="temperature", old_value=0.0, new_value=0.1, reason="test")
        result = apply_mutation_to_raw_config(BASE_CONFIG, mut)
        self.assertEqual(result["agents"]["dev"]["temperature"], 0.1)
        self.assertEqual(BASE_CONFIG["agents"]["dev"]["temperature"], 0.0)  # Original unchanged

    def test_model_mutation(self):
        mut = Mutation(type="model", agent="dev", field="model", old_value="${llms.model-a}", new_value="${llms.model-b}", reason="test")
        result = apply_mutation_to_raw_config(BASE_CONFIG, mut)
        self.assertEqual(result["agents"]["dev"]["model"], "${llms.model-b}")

    def test_tools_add_mutation(self):
        mut = Mutation(type="tools", agent="dev", field="tools", old_value=["terminal"], new_value="+think", reason="test")
        result = apply_mutation_to_raw_config(BASE_CONFIG, mut)
        self.assertIn("think", result["agents"]["dev"]["tools"])
        self.assertEqual(len(result["agents"]["dev"]["tools"]), 3)

    def test_tools_remove_mutation(self):
        mut = Mutation(type="tools", agent="dev", field="tools", old_value=["terminal", "file_editor"], new_value="-file_editor", reason="test")
        result = apply_mutation_to_raw_config(BASE_CONFIG, mut)
        self.assertNotIn("file_editor", result["agents"]["dev"]["tools"])
        self.assertEqual(len(result["agents"]["dev"]["tools"]), 1)

    def test_unknown_agent_skipped(self):
        mut = Mutation(type="temperature", agent="nonexistent", field="temperature", old_value=0.0, new_value=0.1, reason="test")
        result = apply_mutation_to_raw_config(BASE_CONFIG, mut)
        # Config should be unchanged
        self.assertEqual(result["agents"]["dev"]["temperature"], 0.0)


class TestGenerateVariantConfig(unittest.TestCase):
    def test_apply_multiple_mutations(self):
        proposal = Proposal(
            id="test-variant",
            rationale="Test",
            mutations=[
                Mutation(type="temperature", agent="dev", field="temperature", old_value=0.0, new_value=0.1, reason="r1"),
                Mutation(type="model", agent="genius", field="model", old_value="${llms.model-a}", new_value="${llms.model-b}", reason="r2"),
            ],
        )
        result = generate_variant_config(BASE_CONFIG, proposal)
        self.assertEqual(result["agents"]["dev"]["temperature"], 0.1)
        self.assertEqual(result["agents"]["genius"]["model"], "${llms.model-b}")


class TestBuildProposerPrompt(unittest.TestCase):
    def test_prompt_contains_config_and_coreset(self):
        prompt = build_proposer_prompt(BASE_CONFIG, SCORED_TRAJECTORIES, prompt_file=".agents/prompts/rho_proposer.md")
        self.assertIn("Bug A", prompt)
        self.assertIn("Bug B", prompt)
        self.assertIn("score=0.9", prompt)
        self.assertIn("qwen-3-6-27b", prompt)  # From actual proposer prompt file

    def test_prompt_with_missing_prompt_file(self):
        prompt = build_proposer_prompt(BASE_CONFIG, SCORED_TRAJECTORIES, prompt_file="/nonexistent/path.md")
        self.assertIn("Bug A", prompt)


class TestSaveVariantConfigs(unittest.TestCase):
    def test_save_creates_files(self):
        with patch("os.makedirs"), patch("builtins.open", create=True):
            proposal = Proposal(
                id="test",
                rationale="Test rationale",
                mutations=[Mutation(type="temperature", agent="dev", field="temperature", old_value=0.0, new_value=0.1, reason="r")],
            )
            paths = save_variant_configs([proposal], BASE_CONFIG, output_dir=".rho/variants")
            self.assertEqual(len(paths), 1)
            self.assertTrue(paths[0].endswith("variant-test.yaml"))


if __name__ == "__main__":
    unittest.main()
