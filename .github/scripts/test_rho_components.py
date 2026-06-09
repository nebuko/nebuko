#!/usr/bin/env python3
"""Tests for RHO pipeline components."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(__file__))
from metrics_schema import Metrics
from trajectory_archive import (
    TrajectoryRecord,
    compute_outcome_score,
    save_trajectory,
    load_trajectory,
    load_all_trajectories,
    prune_archive,
    trajectory_filename,
    get_archive_dir,
    list_archived,
    _metadata_to_dict,
)
from coreset_selector import (
    DifficultyScore,
    CoresetSelection,
    normalize,
    percentile,
    score_error_rate,
    score_fix_requests,
    score_tool_calls,
    score_runtime,
    score_token_count,
    compute_difficulty_scores,
    diversify_selection,
    select_coreset,
)
from harness_proposer import (
    MutationDelta,
    VariantProposal,
    parse_proposals,
    parse_proposal_block,
    validate_proposals,
    is_safe_mutation,
    _parse_yaml_value,
)
from rho_rollout import apply_mutation
from rho_select import (
    VariantResult,
    compute_avg_error_rate,
    compute_avg_fix_requests,
    compute_avg_runtime_ms,
    compute_avg_token_count,
    compute_composite_score,
    select_winner,
    compute_improvement_summary,
)


# ── Trajectory Archive Tests ────────────────────────────────────────


class TestOutcomeScore(unittest.TestCase):
    """Test compute_outcome_score function."""

    def test_neutral_score_for_empty_metrics(self):
        self.assertAlmostEqual(compute_outcome_score([]), 50.0)

    def test_high_score_for_clean_run(self):
        metrics = [Metrics(tool_call_error_rate=0.0, review_fix_requests=0, agent_runtime_ms=30000)]
        score = compute_outcome_score(metrics)
        self.assertGreater(score, 90.0)

    def test_low_score_for_high_errors(self):
        metrics = [Metrics(tool_call_error_rate=40.0, review_fix_requests=5, agent_runtime_ms=200000)]
        score = compute_outcome_score(metrics)
        self.assertLess(score, 30.0)

    def test_score_clamped_between_0_and_100(self):
        metrics = [Metrics(tool_call_error_rate=100.0, review_fix_requests=20, agent_runtime_ms=1000000)]
        score = compute_outcome_score(metrics)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)

    def test_fix_requests_penalty(self):
        m_good = [Metrics(review_fix_requests=0)]
        m_bad = [Metrics(review_fix_requests=6)]
        self.assertGreater(compute_outcome_score(m_good), compute_outcome_score(m_bad))


class TestTrajectoryPersistence(unittest.TestCase):
    """Test save/load/prune operations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.archive_dir = Path(self.tmpdir)

    def test_save_and_load_single_trajectory(self):
        record = TrajectoryRecord(
            issue_number=42,
            metadata={"title": "Test issue"},
            metrics=[{"input_tokens": 100}],
            comments_count=3,
            archiving_date="2024-01-01T00:00:00",
            outcome_score=85.0,
        )
        save_trajectory(record, self.archive_dir)
        loaded = load_trajectory(42, self.archive_dir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.issue_number, 42)
        self.assertEqual(loaded.outcome_score, 85.0)
        self.assertEqual(loaded.comments_count, 3)

    def test_load_nonexistent_returns_none(self):
        self.assertIsNone(load_trajectory(9999, self.archive_dir))

    def test_load_all_trajectories(self):
        for i in range(1, 4):
            save_trajectory(TrajectoryRecord(issue_number=i, outcome_score=10.0 * i), self.archive_dir)
        all_records = load_all_trajectories(self.archive_dir)
        self.assertEqual(len(all_records), 3)

    def test_prune_removes_oldest(self):
        for i in range(1, 6):
            save_trajectory(TrajectoryRecord(issue_number=i), self.archive_dir)
        deleted = prune_archive(self.archive_dir, max_archived=3)
        self.assertEqual(deleted, 2)
        remaining = load_all_trajectories(self.archive_dir)
        self.assertEqual(len(remaining), 3)

    def test_trajectory_filename_format(self):
        self.assertEqual(trajectory_filename(42), "issue-0042.json")

    def test_get_archive_dir_creates_if_needed(self):
        subdir = Path(self.tmpdir) / "sub" / "dir"
        result = get_archive_dir(str(subdir))
        self.assertTrue(subdir.exists())

    def test_list_archived(self):
        save_trajectory(TrajectoryRecord(issue_number=10, outcome_score=75.0), self.archive_dir)
        summaries = list_archived(self.archive_dir)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["issue_number"], 10)

    def test_metadata_to_dict(self):
        issue_dict = {
            "number": 5,
            "title": "Bug",
            "state": "closed",
            "labels": [{"name": "bug"}, {"name": "priority"}],
            "html_url": "https://example.com/5",
            "extra_key": "should_not_appear",
        }
        meta = _metadata_to_dict(issue_dict)
        self.assertEqual(meta["number"], 5)
        self.assertEqual(meta["labels"], ["bug", "priority"])
        self.assertNotIn("extra_key", meta)


# ── Coreset Selector Tests ──────────────────────────────────────────


class TestNormalize(unittest.TestCase):
    """Test normalize utility."""

    def test_midpoint(self):
        self.assertAlmostEqual(normalize(5, 0, 10), 0.5)

    def test_minimum(self):
        self.assertAlmostEqual(normalize(0, 0, 10), 0.0)

    def test_maximum(self):
        self.assertAlmostEqual(normalize(10, 0, 10), 1.0)

    def test_below_minimum_clamped(self):
        self.assertAlmostEqual(normalize(-5, 0, 10), 0.0)

    def test_above_maximum_clamped(self):
        self.assertAlmostEqual(normalize(15, 0, 10), 1.0)

    def test_zero_range(self):
        self.assertAlmostEqual(normalize(5, 5, 5), 0.0)


class TestDimensionScorers(unittest.TestCase):
    """Test per-dimension scorer functions."""

    def test_score_error_rate_basic(self):
        metrics = [{"tool_call_error_rate": 10.0}, {"tool_call_error_rate": 20.0}]
        self.assertAlmostEqual(score_error_rate(metrics), 15.0)

    def test_score_error_rate_empty(self):
        self.assertAlmostEqual(score_error_rate([]), 0.0)

    def test_score_fix_requests(self):
        metrics = [{"review_fix_requests": 2}, {"review_fix_requests": 3}]
        self.assertEqual(score_fix_requests(metrics), 5)

    def test_score_tool_calls(self):
        metrics = [{"nb_tool_calls": 5}, {"nb_tool_calls": 10}]
        self.assertEqual(score_tool_calls(metrics), 15)

    def test_score_runtime(self):
        self.assertAlmostEqual(score_runtime(5000.5), 5000.5)

    def test_score_token_count(self):
        metrics = [
            {"input_tokens": 100, "output_tokens": 50},
            {"input_tokens": 200, "output_tokens": 100},
        ]
        self.assertAlmostEqual(score_token_count(metrics), 450.0)


class TestDifficultyScoring(unittest.TestCase):
    """Test compute_difficulty_scores."""

    def _make_record(self, issue_num, error_rate=0.0, fix_req=0, tool_calls=0, runtime=0.0, tokens_in=0, tokens_out=0):
        metrics = [Metrics(
            tool_call_error_rate=error_rate,
            review_fix_requests=fix_req,
            nb_tool_calls=tool_calls,
            agent_runtime_ms=runtime,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
        )]
        return TrajectoryRecord(issue_number=issue_num, metrics=metrics)

    def test_harder_task_scores_higher(self):
        easy = self._make_record(1, error_rate=1.0, fix_req=0, tool_calls=2)
        hard = self._make_record(2, error_rate=20.0, fix_req=5, tool_calls=30)
        records = [easy, hard]
        weights = {"error_rate": 0.3, "fix_requests": 0.25, "tool_calls": 0.2, "runtime": 0.15, "token_count": 0.1}
        scored = compute_difficulty_scores(records, weights)
        # Hard task should be first (highest score)
        self.assertEqual(scored[0].issue_number, 2)
        self.assertGreater(scored[0].overall, scored[1].overall)

    def test_same_tasks_have_same_scores(self):
        a = self._make_record(1, error_rate=5.0)
        b = self._make_record(2, error_rate=5.0)
        weights = {"error_rate": 1.0, "fix_requests": 0, "tool_calls": 0, "runtime": 0, "token_count": 0}
        scored = compute_difficulty_scores([a, b], weights)
        self.assertAlmostEqual(scored[0].overall, scored[1].overall)

    def test_empty_records(self):
        scored = compute_difficulty_scores([], {"error_rate": 1.0})
        self.assertEqual(len(scored), 0)

    def test_custom_weights(self):
        high_err = self._make_record(1, error_rate=30.0, fix_req=0)
        high_fix = self._make_record(2, error_rate=1.0, fix_req=10)
        # When fix_requests weighted heavily, high_fix should win
        weights = {"error_rate": 0.0, "fix_requests": 1.0, "tool_calls": 0, "runtime": 0, "token_count": 0}
        scored = compute_difficulty_scores([high_err, high_fix], weights)
        self.assertEqual(scored[0].issue_number, 2)


class TestDiversifySelection(unittest.TestCase):
    """Test diversify_selection."""

    def _make_record(self, issue_num, error_rate=0.0, fix_req=0):
        metrics = [Metrics(tool_call_error_rate=error_rate, review_fix_requests=fix_req)]
        return TrajectoryRecord(issue_number=issue_num, metrics=metrics)

    def _make_score(self, issue_num, overall=0.5):
        return DifficultyScore(issue_number=issue_num, overall=overall)

    def test_diverse_selection_mixes_failures_and_mixed(self):
        # Failure: high error, no clean runs
        fail_rec = self._make_record(1, error_rate=30.0, fix_req=5)
        # Mixed: has some clean metrics
        mixed_rec = self._make_record(2, error_rate=1.0, fix_req=0)

        records = [fail_rec, mixed_rec]
        scores = [self._make_score(1, 0.8), self._make_score(2, 0.6)]
        selected = diversify_selection(scores, records, target_size=2)
        self.assertEqual(len(selected), 2)


class TestSelectCoreset(unittest.TestCase):
    """Test select_coreset integration."""

    def test_empty_archive_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_dir = Path(tmpdir)
            result = select_coreset(archive_dir, target_size=5)
            self.assertEqual(len(result.selected), 0)
            self.assertEqual(result.pool_size, 0)


# ── Harness Proposer Tests ──────────────────────────────────────────


class TestParseProposalBlock(unittest.TestCase):
    """Test parse_proposal_block."""

    def test_parse_complete_block(self):
        text = """variant_name: test-variant
rationale: Testing purposes
mutations:
  - path: agents.dev.temperature
    old_value: 0.0
    new_value: 0.1
  - path: agents.dev.tools
    old_value: ["terminal", "file_editor"]
    new_value: ["terminal"]"""
        result = parse_proposal_block(text)
        self.assertIsNotNone(result)
        self.assertEqual(result.variant_name, "test-variant")
        self.assertEqual(len(result.mutations), 2)
        self.assertEqual(result.mutations[0].path, "agents.dev.temperature")
        self.assertAlmostEqual(result.mutations[0].new_value, 0.1)

    def test_parse_no_variant_name_returns_none(self):
        text = "mutations:\n  - path: foo.bar\n    old_value: 1\n    new_value: 2"
        result = parse_proposal_block(text)
        self.assertIsNone(result)

    def test_parse_empty_block(self):
        result = parse_proposal_block("")
        self.assertIsNone(result)


class TestParseProposals(unittest.TestCase):
    """Test parse_proposals (full text parsing)."""

    def test_parse_two_blocks(self):
        text = """
=== VARIANT START ===
variant_name: alpha
rationale: First variant
mutations:
  - path: agents.dev.temperature
    old_value: 0.0
    new_value: 0.1
=== VARIANT END ===

=== VARIANT START ===
variant_name: beta
rationale: Second variant
mutations:
  - path: agents.genius.temperature
    old_value: 0.2
    new_value: 0.3
=== VARIANT END ===
"""
        results = parse_proposals(text)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].variant_name, "alpha")
        self.assertEqual(results[1].variant_name, "beta")

    def test_parse_no_blocks(self):
        text = "Just some random text."
        results = parse_proposals(text)
        self.assertEqual(len(results), 0)


class TestValidation(unittest.TestCase):
    """Test proposal validation."""

    def test_safe_mutation_paths(self):
        self.assertTrue(is_safe_mutation("agents.dev.temperature"))
        self.assertTrue(is_safe_mutation("agents.dev.tools"))
        self.assertTrue(is_safe_mutation("llms.qwen-3-6-27b.max_output_tokens"))

    def test_unsafe_mutation_paths(self):
        self.assertFalse(is_safe_mutation("providers.openrouter.api_url"))
        self.assertFalse(is_safe_mutation("providers.openrouter.api_token_env"))

    def test_validation_catches_unsafe(self):
        props = [VariantProposal(
            variant_name="bad",
            rationale="unsafe test",
            mutations=[MutationDelta(path="providers.openrouter.api_url", old_value="x", new_value="y")],
        )]
        errors = validate_proposals(props)
        self.assertEqual(len(errors), 1)
        self.assertIn("Unsafe", errors[0][1])

    def test_validation_passes_safe(self):
        props = [VariantProposal(
            variant_name="good",
            mutations=[MutationDelta(path="agents.dev.temperature", old_value=0.0, new_value=0.1)],
        )]
        errors = validate_proposals(props)
        self.assertEqual(len(errors), 0)


class TestParseYamlValue(unittest.TestCase):
    """Test _parse_yaml_value."""

    def test_parses_integer(self):
        self.assertEqual(_parse_yaml_value("42"), 42)

    def test_parses_float(self):
        self.assertEqual(_parse_yaml_value("0.25"), 0.25)

    def test_parses_boolean(self):
        self.assertEqual(_parse_yaml_value("true"), True)

    def test_parses_quoted_string(self):
        self.assertEqual(_parse_yaml_value('"hello"'), "hello")

    def test_falls_back_to_string(self):
        self.assertEqual(_parse_yaml_value("notavalidfloat"), "notavalidfloat")


class TestApplyMutation(unittest.TestCase):
    """Test apply_mutation."""

    def test_apply_scalar_mutation(self):
        config = {"agents": {"dev": {"temperature": 0.0}}}
        success = apply_mutation(config, "agents.dev.temperature", 0.1)
        self.assertTrue(success)
        self.assertEqual(config["agents"]["dev"]["temperature"], 0.1)

    def test_apply_list_mutation(self):
        config = {"agents": {"dev": {"tools": ["a", "b", "c"]}}}
        success = apply_mutation(config, "agents.dev.tools", ["a", "b"])
        self.assertTrue(success)
        self.assertEqual(config["agents"]["dev"]["tools"], ["a", "b"])

    def test_apply_nonexistent_path_fails(self):
        config = {"agents": {"dev": {"temperature": 0.0}}}
        success = apply_mutation(config, "agents.unknown.field", 0.1)
        self.assertFalse(success)


# ── RHO Select Tests ────────────────────────────────────────────────


class TestVariantMetricComputations(unittest.TestCase):
    """Test metric computation helpers."""

    def test_compute_avg_error_rate(self):
        metrics = [{"tool_call_error_rate": 10.0}, {"tool_call_error_rate": 20.0}]
        self.assertAlmostEqual(compute_avg_error_rate(metrics), 15.0)

    def test_compute_avg_error_rate_empty(self):
        self.assertAlmostEqual(compute_avg_error_rate([]), 0.0)

    def test_compute_avg_fix_requests(self):
        metrics = [{"review_fix_requests": 2}, {"review_fix_requests": 4}]
        self.assertAlmostEqual(compute_avg_fix_requests(metrics), 3.0)

    def test_compute_avg_runtime(self):
        metrics = [{"agent_runtime_ms": 1000}, {"agent_runtime_ms": 2000}]
        self.assertAlmostEqual(compute_avg_runtime_ms(metrics), 1500.0)

    def test_compute_avg_token_count(self):
        metrics = [
            {"input_tokens": 100, "output_tokens": 50},
            {"input_tokens": 200, "output_tokens": 100},
        ]
        self.assertAlmostEqual(compute_avg_token_count(metrics), 225.0)


class TestCompositeScore(unittest.TestCase):
    """Test compute_composite_score."""

    def _make_result(self, name, err=0.0, fix=0.0, rt=0.0, tok=0.0, outcome=50.0):
        return VariantResult(
            name=name, total_runs=1,
            avg_error_rate=err, avg_fix_requests=fix,
            avg_runtime_ms=rt, avg_token_count=tok,
            avg_outcome_score=outcome,
        )

    def test_better_variant_gets_higher_score(self):
        baseline = self._make_result("baseline", err=20.0, fix=3, rt=10000, tok=5000)
        improved = self._make_result("improved", err=5.0, fix=0, rt=3000, tok=2000, outcome=80.0)
        compute_composite_score([baseline, improved])
        self.assertGreater(improved.composite_score, baseline.composite_score)

    def test_empty_list(self):
        compute_composite_score([])
        # No exception expected


class TestSelectWinner(unittest.TestCase):
    """Test select_winner."""

    def _baseline(self, comp=0.5, rt=10000):
        return VariantResult(name="baseline", total_runs=1, composite_score=comp, avg_runtime_ms=rt)

    def test_clear_winner(self):
        baseline = self._baseline(comp=0.5, rt=10000)
        variant = VariantResult(
            name="fast", variant_name="fast",
            composite_score=0.8, avg_runtime_ms=8000,
        )
        winner = select_winner([variant], baseline, min_improvement_pct=5.0)
        self.assertIsNotNone(winner)
        self.assertEqual(winner.name, "fast")

    def test_no_winner_below_threshold(self):
        baseline = self._baseline(comp=0.9)
        variant = VariantResult(
            name="weak", variant_name="weak",
            composite_score=0.91,  # Only 1.1% improvement
        )
        winner = select_winner([variant], baseline, min_improvement_pct=10.0)
        self.assertIsNone(winner)

    def test_disqualified_by_runtime(self):
        baseline = self._baseline(comp=0.5, rt=10000)
        slow_variant = VariantResult(
            name="slow", variant_name="slow",
            composite_score=0.9,  # Good score but 10x slower
            avg_runtime_ms=100000,
        )
        winner = select_winner([slow_variant], baseline, max_runtime_increase_pct=50.0)
        self.assertIsNone(winner)

    def test_no_variants(self):
        baseline = self._baseline()
        winner = select_winner([], baseline)
        self.assertIsNone(winner)


class TestImprovementSummary(unittest.TestCase):
    """Test compute_improvement_summary."""

    def test_generates_markdown(self):
        winner = VariantResult(
            name="v1", variant_name="v1",
            avg_error_rate=5.0, avg_fix_requests=0, avg_runtime_ms=3000,
            avg_token_count=1000,
        )
        baseline = VariantResult(
            name="baseline", variant_name="baseline",
            avg_error_rate=20.0, avg_fix_requests=3, avg_runtime_ms=10000,
            avg_token_count=5000,
        )
        summary = compute_improvement_summary(winner, baseline)
        self.assertIn("# RHO Harness Optimization", summary)
        self.assertIn("`v1`", summary)
        self.assertIn("| Metric | Baseline | Winner | Change |", summary)


if __name__ == "__main__":
    unittest.main()
