#!/usr/bin/env python3
"""Tests for RHO (Retrospective Harness Optimization) components.

Tests cover:
    - trajectory_archive.py: Trajectory model, save/load, filtering
    - coreset_selector.py: Scoring, selection, normalization
    - harness_proposer.py: Proposal parsing, mutation application, prompt building
    - rho_select.py: Variant scoring, improvement computation, selection
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(__file__))

from metrics_schema import Metrics
from rho.trajectory_archive import (
    Trajectory,
    _filter_agent_issues,
    _build_trajectory,
    save_trajectory,
    load_trajectory,
    list_trajectories,
)
from rho.coreset_selector import (
    ScoredTrajectory,
    _normalize,
    _compute_metric_for_trajectory,
    score_trajectories,
    select_coreset,
)
from rho.harness_proposer import (
    Mutation,
    VariantProposal,
    parse_proposals_from_text,
    apply_mutations,
    build_proposer_prompt,
)
from rho.rho_select import (
    VariantScore,
    compute_variant_scores,
    compute_improvement,
)


# ── Helper Functions ──────────────────────────────────────────────────

def _make_metrics(**kwargs):
    """Create a Metrics object with default values and overrides."""
    defaults = {
        "run_id": "test-run",
        "model_name": "test-model",
        "agent_role": "dev",
        "input_tokens": 1000,
        "output_tokens": 500,
        "tool_call_error_rate": 0.0,
        "review_fix_requests": 0,
        "nb_tool_calls": 10,
        "agent_runtime_ms": 5000.0,
    }
    defaults.update(kwargs)
    return Metrics(**defaults)


def _make_trajectory(issue_number=1, metrics=None, **kwargs):
    """Create a Trajectory with default values and overrides."""
    defaults = {
        "issue_number": issue_number,
        "issue_title": f"Test Issue #{issue_number}",
        "issue_body": "Test issue body.",
        "closed_at": "2024-06-01T00:00:00Z",
        "metrics": metrics or [_make_metrics()],
        "outcome": "closed",
    }
    defaults.update(kwargs)
    return Trajectory(**defaults)


# ── Trajectory Archive Tests ─────────────────────────────────────────

class TestFilterAgentIssues(unittest.TestCase):
    """Test _filter_agent_issues function."""

    def test_filters_out_pull_requests(self):
        issues = [
            {"number": 1, "pull_request": {"url": "..."}, "body": "@@dev"},
            {"number": 2, "body": "@@dev"},
        ]
        result = _filter_agent_issues(issues)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["number"], 2)

    def test_filters_by_agent_markers(self):
        issues = [
            {"number": 1, "body": "@@genius please help"},
            {"number": 2, "body": "Just a regular issue"},
            {"number": 3, "body": "Hey @@dev fix this"},
            {"number": 4, "body": "<agentRunConfig>"},
        ]
        result = _filter_agent_issues(issues)
        numbers = [i["number"] for i in result]
        self.assertEqual(numbers, [1, 3, 4])

    def test_empty_list(self):
        result = _filter_agent_issues([])
        self.assertEqual(result, [])


class TestBuildTrajectory(unittest.TestCase):
    """Test _build_trajectory function."""

    def test_builds_trajectory_with_metrics(self):
        issue = {
            "number": 42,
            "title": "Fix login bug",
            "body": "<!-- agent-metrics: {\"model_name\":\"gpt-4\",\"input_tokens\":100} -->",
            "closed_at": "2024-06-01T00:00:00Z",
            "state": "closed",
        }
        comments = []
        traj = _build_trajectory(issue, comments)

        self.assertIsNotNone(traj)
        self.assertEqual(traj.issue_number, 42)
        self.assertEqual(traj.issue_title, "Fix login bug")
        self.assertEqual(len(traj.metrics), 1)

    def test_returns_none_when_no_metrics(self):
        issue = {"number": 1, "title": "No metrics", "body": "Just text"}
        traj = _build_trajectory(issue, [])
        self.assertIsNone(traj)


class TestTrajectoryPersistence(unittest.TestCase):
    """Test save/load/list trajectory functions."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def test_save_and_load_trajectory(self):
        traj = _make_trajectory(
            issue_number=99,
            metrics=[_make_metrics(tool_call_error_rate=5.0, review_fix_requests=2)],
        )
        path = save_trajectory(traj, self.temp_dir)
        self.assertTrue(Path(path).exists())

        loaded = load_trajectory(self.temp_dir, 99)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.issue_number, 99)
        self.assertEqual(len(loaded.metrics), 1)

    def test_load_nonexistent_trajectory(self):
        loaded = load_trajectory(self.temp_dir, 9999)
        self.assertIsNone(loaded)

    def test_list_trajectories(self):
        traj1 = _make_trajectory(issue_number=1)
        traj2 = _make_trajectory(issue_number=2)
        save_trajectory(traj1, self.temp_dir)
        save_trajectory(traj2, self.temp_dir)

        trajectories = list_trajectories(self.temp_dir)
        self.assertEqual(len(trajectories), 2)

    def test_list_empty_archive(self):
        trajectories = list_trajectories(self.temp_dir)
        self.assertEqual(trajectories, [])

    def test_roundtrip_preserves_metrics(self):
        metrics = _make_metrics(
            tool_call_error_rate=15.5,
            review_fix_requests=3,
            nb_tool_calls=25,
            agent_runtime_ms=12000.0,
        )
        traj = _make_trajectory(issue_number=77, metrics=[metrics])
        save_trajectory(traj, self.temp_dir)
        loaded = load_trajectory(self.temp_dir, 77)

        self.assertEqual(loaded.metrics[0].tool_call_error_rate, 15.5)
        self.assertEqual(loaded.metrics[0].review_fix_requests, 3)
        self.assertEqual(loaded.metrics[0].nb_tool_calls, 25)


# ── Coreset Selector Tests ───────────────────────────────────────────

class TestNormalize(unittest.TestCase):
    """Test _normalize function."""

    def test_normalize_basic(self):
        values = [0, 5, 10]
        result = _normalize(values)
        self.assertAlmostEqual(result[0], 0.0)
        self.assertAlmostEqual(result[1], 0.5)
        self.assertAlmostEqual(result[2], 1.0)

    def test_normalize_all_same(self):
        values = [5, 5, 5]
        result = _normalize(values)
        self.assertEqual(result, [0.5, 0.5, 0.5])

    def test_normalize_empty(self):
        result = _normalize([])
        self.assertEqual(result, [])

    def test_normalize_single_value(self):
        result = _normalize([42])
        self.assertEqual(result, [0.5])


class TestComputeMetricForTrajectory(unittest.TestCase):
    """Test _compute_metric_for_trajectory function."""

    def test_error_rate(self):
        traj = _make_trajectory(metrics=[
            _make_metrics(tool_call_error_rate=10.0),
            _make_metrics(tool_call_error_rate=20.0),
        ])
        result = _compute_metric_for_trajectory(traj, "error_rate")
        self.assertAlmostEqual(result, 15.0)

    def test_review_fix_requests(self):
        traj = _make_trajectory(metrics=[
            _make_metrics(review_fix_requests=2),
            _make_metrics(review_fix_requests=4),
        ])
        result = _compute_metric_for_trajectory(traj, "review_fix_requests")
        self.assertEqual(result, 3.0)

    def test_tool_calls(self):
        traj = _make_trajectory(metrics=[
            _make_metrics(nb_tool_calls=10),
            _make_metrics(nb_tool_calls=30),
        ])
        result = _compute_metric_for_trajectory(traj, "tool_calls")
        self.assertEqual(result, 20.0)

    def test_token_usage(self):
        traj = _make_trajectory(metrics=[
            _make_metrics(input_tokens=1000, output_tokens=500),
            _make_metrics(input_tokens=2000, output_tokens=1000),
        ])
        result = _compute_metric_for_trajectory(traj, "token_usage")
        self.assertEqual(result, 2250.0)  # avg of 1500 and 3000

    def test_empty_trajectory(self):
        traj = _make_trajectory(metrics=[])
        result = _compute_metric_for_trajectory(traj, "error_rate")
        self.assertEqual(result, 0.0)


class TestScoreTrajectories(unittest.TestCase):
    """Test score_trajectories function."""

    def test_scores_by_difficulty(self):
        easy = _make_trajectory(
            issue_number=1,
            metrics=[_make_metrics(tool_call_error_rate=0, review_fix_requests=0, nb_tool_calls=5, agent_runtime_ms=1000)],
        )
        hard = _make_trajectory(
            issue_number=2,
            metrics=[_make_metrics(tool_call_error_rate=50, review_fix_requests=10, nb_tool_calls=100, agent_runtime_ms=50000)],
        )
        scored = score_trajectories([easy, hard])

        # Hard should be scored higher
        self.assertGreater(scored[0].score, scored[1].score)
        self.assertEqual(scored[0].trajectory.issue_number, 2)

    def test_returns_empty_for_empty_input(self):
        result = score_trajectories([])
        self.assertEqual(result, [])

    def test_component_scores_present(self):
        traj = _make_trajectory()
        scored = score_trajectories([traj])

        self.assertIn("error_rate", scored[0].component_scores)
        self.assertIn("review_fix_requests", scored[0].component_scores)
        self.assertIn("tool_calls", scored[0].component_scores)


class TestSelectCoreset(unittest.TestCase):
    """Test select_coreset function."""

    def test_selects_top_n(self):
        trajectories = [_make_trajectory(issue_number=i) for i in range(10)]
        coreset = select_coreset(trajectories, coreset_size=3)
        self.assertEqual(len(coreset), 3)

    def test_selects_all_when_fewer_than_n(self):
        trajectories = [_make_trajectory(issue_number=i) for i in range(2)]
        coreset = select_coreset(trajectories, coreset_size=5)
        self.assertEqual(len(coreset), 2)

    def test_returns_scored_trajectories(self):
        trajectories = [_make_trajectory()]
        coreset = select_coreset(trajectories, coreset_size=1)
        self.assertIsInstance(coreset[0], ScoredTrajectory)


# ── Harness Proposer Tests ───────────────────────────────────────────

class TestParseProposalsFromText(unittest.TestCase):
    """Test parse_proposals_from_text function."""

    def test_parse_single_proposal(self):
        text = """
Here is my proposal:
```json
{
  "variant_id": "v1",
  "description": "Test variant",
  "mutations": [
    {
      "type": "temperature",
      "agent": "dev",
      "field": "temperature",
      "old_value": 0.0,
      "new_value": 0.2,
      "rationale": "Increase creativity"
    }
  ]
}
```
"""
        proposals = parse_proposals_from_text(text)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].variant_id, "v1")
        self.assertEqual(len(proposals[0].mutations), 1)

    def test_parse_multiple_proposals(self):
        text = """
```json
[
  {"variant_id": "v1", "description": "Variant 1", "mutations": []},
  {"variant_id": "v2", "description": "Variant 2", "mutations": []}
]
```
"""
        proposals = parse_proposals_from_text(text)
        self.assertEqual(len(proposals), 2)
        self.assertEqual(proposals[0].variant_id, "v1")
        self.assertEqual(proposals[1].variant_id, "v2")

    def test_handles_invalid_json(self):
        text = "```json\n{invalid json}\n```"
        proposals = parse_proposals_from_text(text)
        self.assertEqual(proposals, [])

    def test_skips_proposals_without_variant_id(self):
        text = "```json\n{\"description\": \"No ID\", \"mutations\": []}\n```"
        proposals = parse_proposals_from_text(text)
        self.assertEqual(proposals, [])

    def test_parses_all_mutation_types(self):
        text = """
```json
{
  "variant_id": "v1",
  "description": "Multi-mutation",
  "mutations": [
    {"type": "temperature", "agent": "dev", "field": "temperature", "old_value": 0.0, "new_value": 0.2, "rationale": "test"},
    {"type": "model", "agent": "dev", "field": "model", "old_value": "${llms.qwen}", "new_value": "${llms.deepseek}", "rationale": "test"},
    {"type": "tools", "agent": "dev", "field": "tools", "old_value": ["a"], "new_value": ["a", "b"], "rationale": "test"}
  ]
}
```
"""
        proposals = parse_proposals_from_text(text)
        self.assertEqual(len(proposals[0].mutations), 3)
        types = [m.type for m in proposals[0].mutations]
        self.assertEqual(types, ["temperature", "model", "tools"])


class TestApplyMutations(unittest.TestCase):
    """Test apply_mutations function."""

    def _make_config(self):
        return {
            "agents": {
                "dev": {
                    "model": "${llms.qwen-3-6-27b}",
                    "temperature": 0.0,
                    "tools": ["terminal", "file_editor"],
                    "prompt_file": "dev.md",
                }
            }
        }

    def test_temperature_mutation(self):
        config = self._make_config()
        proposal = VariantProposal(
            variant_id="v1",
            description="Temp change",
            mutations=[Mutation(
                type="temperature",
                agent="dev",
                field="temperature",
                old_value=0.0,
                new_value=0.3,
                rationale="Test",
            )],
        )
        result = apply_mutations(config, proposal)
        self.assertEqual(result["agents"]["dev"]["temperature"], 0.3)

    def test_model_mutation(self):
        config = self._make_config()
        proposal = VariantProposal(
            variant_id="v1",
            description="Model change",
            mutations=[Mutation(
                type="model",
                agent="dev",
                field="model",
                old_value="${llms.qwen-3-6-27b}",
                new_value="${llms.deepseek-v4-flash}",
                rationale="Test",
            )],
        )
        result = apply_mutations(config, proposal)
        self.assertEqual(result["agents"]["dev"]["model"], "${llms.deepseek-v4-flash}")

    def test_tools_mutation(self):
        config = self._make_config()
        proposal = VariantProposal(
            variant_id="v1",
            description="Tools change",
            mutations=[Mutation(
                type="tools",
                agent="dev",
                field="tools",
                old_value=["terminal", "file_editor"],
                new_value=["terminal", "file_editor", "task_tracker"],
                rationale="Test",
            )],
        )
        result = apply_mutations(config, proposal)
        self.assertEqual(result["agents"]["dev"]["tools"], ["terminal", "file_editor", "task_tracker"])

    def test_does_not_modify_original(self):
        config = self._make_config()
        original_temp = config["agents"]["dev"]["temperature"]

        proposal = VariantProposal(
            variant_id="v1",
            description="Test",
            mutations=[Mutation(
                type="temperature",
                agent="dev",
                field="temperature",
                old_value=0.0,
                new_value=0.5,
                rationale="Test",
            )],
        )
        apply_mutations(config, proposal)
        self.assertEqual(config["agents"]["dev"]["temperature"], original_temp)

    def test_handles_unknown_agent(self):
        config = self._make_config()
        proposal = VariantProposal(
            variant_id="v1",
            description="Unknown agent",
            mutations=[Mutation(
                type="temperature",
                agent="nonexistent",
                field="temperature",
                old_value=0.0,
                new_value=0.5,
                rationale="Test",
            )],
        )
        result = apply_mutations(config, proposal)
        self.assertNotIn("nonexistent", result["agents"])


class TestBuildProposerPrompt(unittest.TestCase):
    """Test build_proposer_prompt function."""

    def test_prompt_contains_harness_config(self):
        config = {"agents": {"dev": {"temperature": 0.0}}}
        coreset = [ScoredTrajectory(
            trajectory=_make_trajectory(),
            score=0.8,
            component_scores={"error_rate": 0.5},
        )]
        prompt = build_proposer_prompt(coreset, config)

        self.assertIn("agents", prompt)
        self.assertIn("dev", prompt)

    def test_prompt_contains_trajectory_info(self):
        traj = _make_trajectory(issue_number=42, issue_title="Critical Bug")
        coreset = [ScoredTrajectory(
            trajectory=traj,
            score=0.9,
            component_scores={"error_rate": 0.8},
        )]
        prompt = build_proposer_prompt(coreset, {}, proposer_prompt_file=None)

        self.assertIn("42", prompt)
        self.assertIn("Critical Bug", prompt)


# ── RHO Select Tests ──────────────────────────────────────────────────

class TestComputeVariantScores(unittest.TestCase):
    """Test compute_variant_scores function."""

    def _make_rollout_result(self, variant_id, error_rate=0, fix_requests=0, tool_calls=10, success=True):
        return {
            "results": [{
                "variant_id": variant_id,
                "success": success,
                "metrics": {
                    "tool_call_error_rate": error_rate,
                    "review_fix_requests": fix_requests,
                    "nb_tool_calls": tool_calls,
                    "agent_runtime_ms": 5000,
                    "input_tokens": 1000,
                    "output_tokens": 500,
                },
            }],
        }

    def test_scores_variants(self):
        results = [
            self._make_rollout_result("baseline", error_rate=10, fix_requests=5),
            self._make_rollout_result("v1", error_rate=5, fix_requests=2),
        ]
        scores = compute_variant_scores(results)

        self.assertEqual(len(scores), 2)
        # v1 should score higher (lower error rate and fix requests)
        self.assertGreater(scores[0].total_score, scores[1].total_score)

    def test_scores_include_stats(self):
        results = [self._make_rollout_result("v1", error_rate=5, fix_requests=3, success=True)]
        scores = compute_variant_scores(results)

        score = scores[0]
        self.assertEqual(score.variant_id, "v1")
        self.assertEqual(score.avg_error_rate, 5.0)
        self.assertEqual(score.total_fix_requests, 3)
        self.assertEqual(score.success_rate, 1.0)


class TestComputeImprovement(unittest.TestCase):
    """Test compute_improvement function."""

    def test_positive_improvement(self):
        baseline = VariantScore(variant_id="baseline", total_score=0.5)
        winner = VariantScore(variant_id="v1", total_score=0.6)
        improvement = compute_improvement(baseline, winner)
        self.assertAlmostEqual(improvement, 20.0)  # (0.6-0.5)/0.5 * 100 = 20%

    def test_negative_improvement(self):
        baseline = VariantScore(variant_id="baseline", total_score=0.6)
        winner = VariantScore(variant_id="v1", total_score=0.5)
        improvement = compute_improvement(baseline, winner)
        self.assertAlmostEqual(improvement, -16.67, places=1)

    def test_zero_baseline(self):
        baseline = VariantScore(variant_id="baseline", total_score=0.0)
        winner = VariantScore(variant_id="v1", total_score=0.5)
        improvement = compute_improvement(baseline, winner)
        self.assertEqual(improvement, 0.0)

    def test_no_change(self):
        baseline = VariantScore(variant_id="baseline", total_score=0.5)
        winner = VariantScore(variant_id="v1", total_score=0.5)
        improvement = compute_improvement(baseline, winner)
        self.assertAlmostEqual(improvement, 0.0)


# ── Integration: End-to-End Flow ──────────────────────────────────────

class TestEndToEndFlow(unittest.TestCase):
    """Test the complete RHO flow from trajectories to selection."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def test_full_flow_with_mock_data(self):
        """Simulate the full RHO pipeline with mock data."""

        # Step 1: Create mock trajectories with varying difficulty
        trajectories = [
            _make_trajectory(
                issue_number=1,
                metrics=[_make_metrics(
                    tool_call_error_rate=2.0,
                    review_fix_requests=0,
                    nb_tool_calls=5,
                    agent_runtime_ms=2000,
                )],
            ),
            _make_trajectory(
                issue_number=2,
                metrics=[_make_metrics(
                    tool_call_error_rate=30.0,
                    review_fix_requests=8,
                    nb_tool_calls=50,
                    agent_runtime_ms=45000,
                )],
            ),
            _make_trajectory(
                issue_number=3,
                metrics=[_make_metrics(
                    tool_call_error_rate=5.0,
                    review_fix_requests=1,
                    nb_tool_calls=15,
                    agent_runtime_ms=8000,
                )],
            ),
        ]

        # Step 2: Save trajectories
        for traj in trajectories:
            save_trajectory(traj, self.temp_dir)

        # Step 3: List and verify
        loaded = list_trajectories(self.temp_dir)
        self.assertEqual(len(loaded), 3)

        # Step 4: Score and select coreset
        coreset = select_coreset(loaded, coreset_size=2)
        self.assertEqual(len(coreset), 2)

        # Issue #2 should be hardest (highest score)
        self.assertGreater(coreset[0].score, coreset[1].score)

        # Step 5: Create variant proposals
        proposals = [
            VariantProposal(
                variant_id="v1",
                description="Higher temperature",
                mutations=[Mutation(
                    type="temperature",
                    agent="dev",
                    field="temperature",
                    old_value=0.0,
                    new_value=0.2,
                    rationale="Increase creativity for hard tasks",
                )],
            ),
            VariantProposal(
                variant_id="v2",
                description="Add task_tracker tool",
                mutations=[Mutation(
                    type="tools",
                    agent="dev",
                    field="tools",
                    old_value=["terminal", "file_editor"],
                    new_value=["terminal", "file_editor", "task_tracker"],
                    rationale="Better task organization",
                )],
            ),
        ]

        # Step 6: Apply mutations to config
        base_config = {
            "agents": {
                "dev": {
                    "model": "${llms.qwen-3-6-27b}",
                    "temperature": 0.0,
                    "tools": ["terminal", "file_editor"],
                }
            }
        }

        for proposal in proposals:
            variant_config = apply_mutations(base_config, proposal)
            self.assertIsNotNone(variant_config)

        # Step 7: Simulate rollout results and selection
        rollout_results = [
            {
                "results": [
                    {"variant_id": "baseline", "success": True, "metrics": {
                        "tool_call_error_rate": 15.0, "review_fix_requests": 3,
                        "nb_tool_calls": 25, "input_tokens": 2000, "output_tokens": 1000,
                    }},
                    {"variant_id": "v1", "success": True, "metrics": {
                        "tool_call_error_rate": 8.0, "review_fix_requests": 1,
                        "nb_tool_calls": 22, "input_tokens": 2100, "output_tokens": 1100,
                    }},
                    {"variant_id": "v2", "success": True, "metrics": {
                        "tool_call_error_rate": 12.0, "review_fix_requests": 2,
                        "nb_tool_calls": 20, "input_tokens": 1900, "output_tokens": 900,
                    }},
                ],
            },
        ]

        scores = compute_variant_scores(rollout_results)
        self.assertEqual(len(scores), 3)

        # v1 should win (lowest error rate and fix requests)
        winner = scores[0]
        self.assertEqual(winner.variant_id, "v1")


if __name__ == "__main__":
    unittest.main()
