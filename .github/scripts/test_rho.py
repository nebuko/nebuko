#!/usr/bin/env python3
"""Tests for RHO components: trajectory_archive, coreset_selector, harness_proposer, rho_select."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from metrics_schema import Metrics
from trajectory_archive import (
    Trajectory,
    trajectory_to_dict,
    trajectory_from_dict,
    build_trajectory,
    save_trajectories,
    load_archive,
)
from coreset_selector import (
    DifficultyScore,
    normalize,
    compute_difficulty_scores,
    select_coreset,
    load_rho_config,
    get_default_config,
)
from harness_proposer import (
    Mutation,
    VariantConfig,
    load_raw_config,
    resolve_agent_path,
    get_nested,
    set_nested,
    analyze_trajectories_for_patterns,
    propose_mutations,
    apply_mutation,
)
from rho_select import (
    VariantScore,
    SelectionResult,
    aggregate_variant_scores,
    compute_improvement_score,
    count_improved_metrics,
    select_winner,
    generate_pr_description,
    RolloutResult,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_metric(
    agent_role="dev",
    error_rate=0.0,
    fix_requests=0,
    tool_calls=10,
    runtime_ms=5000.0,
    input_tokens=1000,
    output_tokens=500,
):
    return Metrics(
        run_id="test-run",
        model_name="test-model",
        agent_role=agent_role,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_call_error_rate=error_rate,
        agent_runtime_ms=runtime_ms,
        nb_tool_calls=tool_calls,
        review_fix_requests=fix_requests,
    )


def _make_trajectory(
    issue_number=1,
    error_rate=0.0,
    fix_requests=0,
    tool_calls=10,
    runtime_ms=5000.0,
    tokens=1500,
    title="Test Issue",
    body="Test body",
):
    metric = _make_metric(
        error_rate=error_rate,
        fix_requests=fix_requests,
        tool_calls=tool_calls,
        runtime_ms=runtime_ms,
        input_tokens=tokens,
    )
    return Trajectory(
        issue_number=issue_number,
        issue_title=title,
        issue_body=body,
        metrics=[metric],
        avg_error_rate=error_rate,
        total_fix_requests=fix_requests,
        total_tool_calls=tool_calls,
        avg_runtime_ms=runtime_ms,
        total_tokens=tokens,
    )


# ── Trajectory Archive Tests ───────────────────────────────────────────


class TestTrajectorySerialization(unittest.TestCase):
    """Test trajectory serialization and deserialization."""

    def test_trajectory_to_dict_and_back(self):
        traj = _make_trajectory(issue_number=42, error_rate=5.0)
        d = trajectory_to_dict(traj)
        self.assertEqual(d["issue_number"], 42)
        self.assertEqual(len(d["metrics"]), 1)
        # Metrics should be dicts, not Metrics objects
        self.assertIsInstance(d["metrics"][0], dict)

        restored = trajectory_from_dict(d)
        self.assertEqual(restored.issue_number, 42)
        self.assertIsInstance(restored.metrics[0], Metrics)
        self.assertEqual(restored.metrics[0].tool_call_error_rate, 5.0)

    def test_trajectory_with_no_metrics(self):
        traj = Trajectory(issue_number=1, issue_title="No metrics")
        d = trajectory_to_dict(traj)
        restored = trajectory_from_dict(d)
        self.assertEqual(restored.issue_number, 1)
        self.assertEqual(len(restored.metrics), 0)


class TestBuildTrajectory(unittest.TestCase):
    """Test trajectory building from issue data."""

    def test_build_trajectory_with_metrics(self):
        issue = {
            "number": 10,
            "title": "Fix bug",
            "body": "Something is broken",
            "closed_at": "2024-06-01T00:00:00Z",
            "state": "closed",
        }
        metric = _make_metric(error_rate=10.0, fix_requests=2)
        comments = [
            {
                "body": f"<!-- agent-metrics: {metric.to_json(compact=True)} -->",
                "user": {"login": "agent"},
                "created_at": "2024-06-01T00:00:00Z",
            }
        ]
        traj = build_trajectory(issue, comments)
        self.assertEqual(traj.issue_number, 10)
        self.assertEqual(len(traj.metrics), 1)
        self.assertEqual(traj.avg_error_rate, 10.0)
        self.assertEqual(traj.total_fix_requests, 2)

    def test_build_trajectory_without_metrics(self):
        issue = {"number": 5, "title": "No agent", "body": "Manual fix", "closed_at": "2024-05-01T00:00:00Z"}
        comments = [{"body": "Looks good!", "user": {"login": "human"}, "created_at": "2024-05-01T00:00:00Z"}]
        traj = build_trajectory(issue, comments)
        self.assertEqual(traj.issue_number, 5)
        self.assertEqual(len(traj.metrics), 0)


class TestSaveAndLoadArchive(unittest.TestCase):
    """Test saving and loading trajectory archives."""

    def test_save_and_load(self):
        trajectories = [
            _make_trajectory(issue_number=1, error_rate=5.0),
            _make_trajectory(issue_number=2, error_rate=10.0, fix_requests=3),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = save_trajectories(trajectories, tmpdir)
            self.assertIn(os.path.join(tmpdir, "index.json"), paths)
            self.assertIn(os.path.join(tmpdir, "issue_1.json"), paths)
            self.assertIn(os.path.join(tmpdir, "issue_2.json"), paths)

            loaded = load_archive(tmpdir)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0].issue_number, 1)
            self.assertEqual(loaded[1].issue_number, 2)

    def test_load_empty_archive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            loaded = load_archive(tmpdir)
            self.assertEqual(len(loaded), 0)

    def test_load_nonexistent_archive(self):
        loaded = load_archive("/nonexistent/path")
        self.assertEqual(len(loaded), 0)


# ── Coreset Selector Tests ──────────────────────────────────────────────


class TestNormalize(unittest.TestCase):
    """Test min-max normalization."""

    def test_normalize_basic(self):
        result = normalize([0, 5, 10])
        self.assertAlmostEqual(result[0], 0.0)
        self.assertAlmostEqual(result[1], 0.5)
        self.assertAlmostEqual(result[2], 1.0)

    def test_normalize_all_same(self):
        result = normalize([5, 5, 5])
        self.assertEqual(result, [0.0, 0.0, 0.0])

    def test_normalize_empty(self):
        result = normalize([])
        self.assertEqual(result, [])

    def test_normalize_single(self):
        result = normalize([42])
        self.assertEqual(result, [0.0])


class TestComputeDifficultyScores(unittest.TestCase):
    """Test difficulty scoring."""

    def test_scores_sorted_descending(self):
        trajectories = [
            _make_trajectory(issue_number=1, error_rate=1.0, fix_requests=0),
            _make_trajectory(issue_number=2, error_rate=10.0, fix_requests=5),
            _make_trajectory(issue_number=3, error_rate=5.0, fix_requests=2),
        ]
        weights = {"error_rate": 0.5, "review_fix_requests": 0.5}
        scores = compute_difficulty_scores(trajectories, weights)

        self.assertEqual(len(scores), 3)
        # Most challenging first
        self.assertEqual(scores[0].issue_number, 2)
        self.assertEqual(scores[2].issue_number, 1)
        self.assertGreater(scores[0].total_score, scores[1].total_score)

    def test_empty_trajectories(self):
        scores = compute_difficulty_scores([], {})
        self.assertEqual(scores, [])

    def test_trajectories_without_metrics(self):
        trajs = [Trajectory(issue_number=1), Trajectory(issue_number=2)]
        scores = compute_difficulty_scores(trajs, {"error_rate": 1.0})
        self.assertEqual(scores, [])


class TestSelectCoreset(unittest.TestCase):
    """Test coreset selection."""

    def test_select_coreset_from_archive(self):
        trajectories = [
            _make_trajectory(issue_number=i, error_rate=i * 2.0, fix_requests=i)
            for i in range(1, 11)
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            save_trajectories(trajectories, tmpdir)
            coreset = select_coreset(archive_dir=tmpdir, coreset_size=3)
            self.assertEqual(len(coreset), 3)
            # Most challenging issues should be selected (highest error_rate)
            numbers = [t.issue_number for t in coreset]
            self.assertIn(10, numbers)  # Highest error rate
            self.assertIn(9, numbers)


class TestLoadRhoConfig(unittest.TestCase):
    """Test RHO config loading."""

    def test_default_config(self):
        config = get_default_config()
        self.assertIn("coreset", config)
        self.assertEqual(config["coreset"]["size"], 5)
        self.assertIn("difficulty_weights", config["coreset"])

    def test_load_nonexistent_returns_default(self):
        config = load_rho_config("/nonexistent/path")
        self.assertIn("coreset", config)


# ── Harness Proposer Tests ──────────────────────────────────────────────


class TestAgentPathResolution(unittest.TestCase):
    """Test agent path parsing."""

    def test_resolve_simple(self):
        parts, key = resolve_agent_path("agents.dev.temperature")
        self.assertEqual(parts, ["agents", "dev"])
        self.assertEqual(key, "temperature")

    def test_resolve_deep(self):
        parts, key = resolve_agent_path("agents.dev.tools")
        self.assertEqual(parts, ["agents", "dev"])
        self.assertEqual(key, "tools")


class TestNestedGetSet(unittest.TestCase):
    """Test nested dict operations."""

    def test_get_nested(self):
        d = {"a": {"b": {"c": 42}}}
        self.assertEqual(get_nested(d, ["a", "b", "c"]), 42)
        self.assertEqual(get_nested(d, ["a", "b"]), {"c": 42})
        # Missing keys return empty dict (not None)
        self.assertEqual(get_nested(d, ["x", "y"]), {})

    def test_set_nested(self):
        d = {"a": {"b": {}}}
        set_nested(d, ["a", "b"], "c", 99)
        self.assertEqual(d["a"]["b"]["c"], 99)


class TestAnalyzePatterns(unittest.TestCase):
    """Test trajectory pattern analysis."""

    def test_pattern_analysis_basic(self):
        trajectories = [
            _make_trajectory(issue_number=1, error_rate=10.0, fix_requests=3),
            _make_trajectory(issue_number=2, error_rate=2.0, fix_requests=0),
        ]
        patterns = analyze_trajectories_for_patterns(trajectories)
        self.assertIn("dev", patterns)
        self.assertAlmostEqual(patterns["dev"]["avg_error_rate"], 6.0)
        self.assertAlmostEqual(patterns["dev"]["avg_fix_requests"], 1.5)


class TestProposeMutations(unittest.TestCase):
    """Test mutation proposal."""

    def _make_config(self):
        return {
            "providers": {"openrouter": {"api_url": "https://example.com"}},
            "llms": {
                "model-a": {"model_path": "a", "provider": "${providers.openrouter}"},
                "model-b": {"model_path": "b", "provider": "${providers.openrouter}"},
            },
            "agents": {
                "dev": {"model": "${llms.model-a}", "temperature": 0.0, "tools": ["terminal", "task_tracker", "think", "finish"], "prompt_file": "dev.md"},
                "genius": {"model": "${llms.model-a}", "temperature": 0.2, "tools": ["think", "finish"], "prompt_file": "genius.md"},
            },
        }

    def test_propose_temperature_for_fix_requests(self):
        trajectories = [
            _make_trajectory(issue_number=i, error_rate=0, fix_requests=3, tool_calls=5)
            for i in range(1, 4)
        ]
        mutations = propose_mutations(trajectories, self._make_config(), num_variants=3)
        # Should propose temperature increase for dev agent
        temp_mutations = [m for m in mutations if "temperature" in m.target]
        self.assertGreater(len(temp_mutations), 0)

    def test_propose_tool_removal_for_high_calls(self):
        trajectories = [
            _make_trajectory(issue_number=i, error_rate=0, fix_requests=0, tool_calls=20)
            for i in range(1, 4)
        ]
        mutations = propose_mutations(trajectories, self._make_config(), num_variants=3)
        tool_mutations = [m for m in mutations if "tools" in m.target]
        self.assertGreater(len(tool_mutations), 0)


class TestApplyMutation(unittest.TestCase):
    """Test mutation application."""

    def test_apply_temperature_mutation(self):
        config = {
            "agents": {
                "dev": {"temperature": 0.0, "tools": ["terminal"]},
            }
        }
        mutation = Mutation(
            variant_id="v1",
            description="Increase temperature",
            target="agents.dev.temperature",
            current_value=0.0,
            proposed_value=0.2,
            rationale="Test",
        )
        result = apply_mutation(config, mutation)
        self.assertEqual(result["agents"]["dev"]["temperature"], 0.2)
        # Original should be unchanged
        self.assertEqual(config["agents"]["dev"]["temperature"], 0.0)

    def test_apply_tools_mutation(self):
        config = {
            "agents": {
                "dev": {"temperature": 0.0, "tools": ["terminal", "task_tracker"]},
            }
        }
        mutation = Mutation(
            variant_id="v1",
            description="Remove task_tracker",
            target="agents.dev.tools",
            current_value=["terminal", "task_tracker"],
            proposed_value=["terminal"],
            rationale="Test",
        )
        result = apply_mutation(config, mutation)
        self.assertEqual(result["agents"]["dev"]["tools"], ["terminal"])


# ── RHO Select Tests ────────────────────────────────────────────────────


class TestAggregateVariantScores(unittest.TestCase):
    """Test variant score aggregation."""

    def _make_result(self, variant_id, error_rate=0.0, success=True,
                     fix_requests=0, tool_calls=10, runtime_ms=5000, tokens=1500):
        return RolloutResult(
            task_issue_number=1,
            variant_id=variant_id,
            config_path="/test.yaml",
            success=success,
            error_rate=error_rate,
            fix_requests=fix_requests,
            tool_calls=tool_calls,
            runtime_ms=runtime_ms,
            input_tokens=tokens,
            output_tokens=0,
        )

    def test_aggregation_basic(self):
        results = [
            self._make_result("baseline", error_rate=5.0, fix_requests=2, tool_calls=15),
            self._make_result("baseline", error_rate=3.0, fix_requests=1, tool_calls=10),
            self._make_result("v1", error_rate=2.0, fix_requests=0, tool_calls=8),
            self._make_result("v1", error_rate=4.0, fix_requests=1, tool_calls=12),
        ]
        scores = aggregate_variant_scores(results)

        self.assertIn("baseline", scores)
        self.assertIn("v1", scores)
        self.assertAlmostEqual(scores["baseline"].avg_error_rate, 4.0)
        self.assertAlmostEqual(scores["v1"].avg_error_rate, 3.0)
        self.assertEqual(scores["baseline"].task_count, 2)
        self.assertEqual(scores["v1"].task_count, 2)


class TestComputeImprovementScore(unittest.TestCase):
    """Test improvement scoring."""

    def _make_score(self, **kwargs):
        defaults = dict(avg_error_rate=5.0, avg_fix_requests=2.0, avg_runtime_ms=5000,
                        avg_tokens=10000, avg_tool_calls=15.0)
        defaults.update(kwargs)
        return VariantScore(variant_id="x", total_score=0, **defaults)

    def test_better_variant_scores_positive(self):
        baseline = self._make_score(avg_error_rate=10.0, avg_fix_requests=5.0, avg_runtime_ms=10000)
        variant = self._make_score(avg_error_rate=5.0, avg_fix_requests=2.0, avg_runtime_ms=5000)
        weights = {"error_rate_reduction": 0.4, "fix_request_reduction": 0.3, "latency_reduction": 0.3}
        score = compute_improvement_score(baseline, variant, weights)
        self.assertGreater(score, 0)

    def test_worse_variant_scores_negative(self):
        baseline = self._make_score(avg_error_rate=5.0, avg_fix_requests=2.0)
        variant = self._make_score(avg_error_rate=10.0, avg_fix_requests=5.0)
        weights = {"error_rate_reduction": 0.5, "fix_request_reduction": 0.5}
        score = compute_improvement_score(baseline, variant, weights)
        self.assertLess(score, 0)

    def test_identical_scores_zero(self):
        baseline = self._make_score()
        variant = self._make_score()
        weights = {"error_rate_reduction": 1.0}
        score = compute_improvement_score(baseline, variant, weights)
        self.assertAlmostEqual(score, 0.0)


class TestCountImprovedMetrics(unittest.TestCase):
    """Test metric improvement counting."""

    def test_all_improved(self):
        baseline = VariantScore(
            variant_id="base", total_score=0,
            avg_error_rate=10.0, avg_fix_requests=5.0,
            avg_runtime_ms=10000, avg_tokens=20000, avg_tool_calls=20.0,
        )
        variant = VariantScore(
            variant_id="v1", total_score=0,
            avg_error_rate=5.0, avg_fix_requests=2.0,
            avg_runtime_ms=5000, avg_tokens=10000, avg_tool_calls=10.0,
        )
        self.assertEqual(count_improved_metrics(baseline, variant), 5)

    def test_none_improved(self):
        baseline = VariantScore(
            variant_id="base", total_score=0,
            avg_error_rate=5.0, avg_fix_requests=2.0,
            avg_runtime_ms=5000, avg_tokens=10000, avg_tool_calls=10.0,
        )
        variant = VariantScore(
            variant_id="v1", total_score=0,
            avg_error_rate=10.0, avg_fix_requests=5.0,
            avg_runtime_ms=10000, avg_tokens=20000, avg_tool_calls=20.0,
        )
        self.assertEqual(count_improved_metrics(baseline, variant), 0)


class TestSelectWinner(unittest.TestCase):
    """Test winner selection from rollout results."""

    def test_select_winner_from_archive(self):
        results = [
            RolloutResult(task_issue_number=1, variant_id="baseline", config_path="",
                          success=True, error_rate=5.0, fix_requests=2, tool_calls=15,
                          runtime_ms=10000, input_tokens=5000),
            RolloutResult(task_issue_number=2, variant_id="baseline", config_path="",
                          success=True, error_rate=3.0, fix_requests=1, tool_calls=10,
                          runtime_ms=8000, input_tokens=4000),
            RolloutResult(task_issue_number=1, variant_id="v1", config_path="",
                          success=True, error_rate=1.0, fix_requests=0, tool_calls=5,
                          runtime_ms=3000, input_tokens=2000),
            RolloutResult(task_issue_number=2, variant_id="v1", config_path="",
                          success=True, error_rate=2.0, fix_requests=0, tool_calls=8,
                          runtime_ms=4000, input_tokens=3000),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = os.path.join(tmpdir, "results.json")
            with open(results_path, "w") as f:
                json.dump([r.__dict__ for r in results], f)

            selection = select_winner(rollout_dir=tmpdir)
            self.assertIsNotNone(selection)
            self.assertEqual(selection.winner_variant_id, "v1")
            self.assertGreater(selection.improvement_percent, 0)


class TestGeneratePRDescription(unittest.TestCase):
    """Test PR description generation."""

    def test_pr_description_contains_winner(self):
        selection = SelectionResult(
            winner_variant_id="v1",
            winner_score=0.25,
            baseline_score=0.0,
            improvement_percent=25.0,
            scores=[
                VariantScore(variant_id="baseline", total_score=0.0, avg_error_rate=5.0),
                VariantScore(variant_id="v1", total_score=0.25, avg_error_rate=2.0),
            ],
            recommendation="v1 is better",
            pr_ready=True,
        )
        desc = generate_pr_description(selection)
        self.assertIn("v1", desc)
        self.assertIn("RHO Harness Optimization", desc)
        self.assertIn("25.0%", desc)


if __name__ == "__main__":
    unittest.main()
