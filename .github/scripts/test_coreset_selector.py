#!/usr/bin/env python3
"""Tests for coreset_selector.py — difficulty scoring and coreset selection."""

import os
import sys
import unittest
from dataclasses import dataclass, field
from typing import List

sys.path.insert(0, os.path.dirname(__file__))
from coreset_selector import (
    _normalize,
    compute_trajectory_metrics,
    score_trajectories,
    select_coreset,
)
from metrics_schema import Metrics


@dataclass
class _MockIssue:
    """Minimal issue mock for testing."""

    issue_number: int
    title: str


@dataclass
class _MockTrajectory:
    """Minimal trajectory mock for testing."""

    issue: _MockIssue
    metrics: List[Metrics] = field(default_factory=list)


# ── Test Helpers ─────────────────────────────────────────────────────


def _make_trajectory(issue_num: int, metrics_data=None) -> _MockTrajectory:
    """Create a mock trajectory with given metrics."""
    if metrics_data is None:
        metrics_data = []
    metrics_list = []
    for m in metrics_data:
        metrics_list.append(Metrics(**m))
    return _MockTrajectory(
        issue=_MockIssue(issue_number=issue_num, title=f"Issue {issue_num}"),
        metrics=metrics_list,
    )


# ── Tests ────────────────────────────────────────────────────────────


class TestNormalize(unittest.TestCase):
    def test_normalize_with_range(self):
        result = _normalize([0.0, 0.5, 1.0])
        self.assertAlmostEqual(result[0], 0.0, places=5)
        self.assertAlmostEqual(result[1], 0.5, places=5)
        self.assertAlmostEqual(result[2], 1.0, places=5)

    def test_normalize_all_same(self):
        result = _normalize([0.3, 0.3, 0.3])
        self.assertEqual(result, [0.0, 0.0, 0.0])

    def test_normalize_single_value(self):
        result = _normalize([0.5])
        self.assertEqual(result, [0.0])

    def test_normalize_empty_list(self):
        result = _normalize([])
        self.assertEqual(result, [])

    def test_normalize_negative_values(self):
        result = _normalize([-2.0, 0.0, 2.0])
        self.assertAlmostEqual(result[0], 0.0, places=5)
        self.assertAlmostEqual(result[1], 0.5, places=5)
        self.assertAlmostEqual(result[2], 1.0, places=5)


class TestComputeTrajectoryMetrics(unittest.TestCase):
    def test_empty_metrics(self):
        traj = _make_trajectory(1, [])
        result = compute_trajectory_metrics(traj)
        self.assertEqual(result["error_rate"], 0.0)
        self.assertEqual(result["tool_calls"], 0)
        self.assertEqual(result["num_runs"], 0)

    def test_single_run(self):
        traj = _make_trajectory(1, [{
            "tool_call_error_rate": 10.0,
            "review_fix_requests": 2,
            "nb_tool_calls": 15,
            "agent_runtime_ms": 50000.0,
            "input_tokens": 10000,
            "output_tokens": 5000,
        }])
        result = compute_trajectory_metrics(traj)
        self.assertEqual(result["error_rate"], 10.0)
        self.assertEqual(result["review_fix_requests"], 2)
        self.assertEqual(result["tool_calls"], 15)
        self.assertEqual(result["runtime_ms"], 50000.0)
        self.assertEqual(result["total_tokens"], 15000)
        self.assertEqual(result["num_runs"], 1)

    def test_multiple_runs_aggregate(self):
        traj = _make_trajectory(1, [
            {"tool_call_error_rate": 5.0, "review_fix_requests": 1, "nb_tool_calls": 10, "agent_runtime_ms": 30000.0, "input_tokens": 5000, "output_tokens": 2000},
            {"tool_call_error_rate": 15.0, "review_fix_requests": 3, "nb_tool_calls": 20, "agent_runtime_ms": 70000.0, "input_tokens": 10000, "output_tokens": 8000},
        ])
        result = compute_trajectory_metrics(traj)
        self.assertEqual(result["error_rate"], 20.0)  # 5 + 15
        self.assertEqual(result["review_fix_requests"], 4)  # 1 + 3
        self.assertEqual(result["tool_calls"], 30)  # 10 + 20
        self.assertEqual(result["runtime_ms"], 100000.0)  # 30000 + 70000
        self.assertEqual(result["total_tokens"], 25000)  # 7000 + 18000
        self.assertEqual(result["num_runs"], 2)


class TestScoreTrajectories(unittest.TestCase):
    def _make_trajectories(self):
        return [
            _make_trajectory(0, [{"tool_call_error_rate": 30.0, "review_fix_requests": 5, "nb_tool_calls": 40, "agent_runtime_ms": 150000.0, "input_tokens": 50000, "output_tokens": 30000}]),
            _make_trajectory(1, [{"tool_call_error_rate": 0.0, "review_fix_requests": 0, "nb_tool_calls": 3, "agent_runtime_ms": 5000.0, "input_tokens": 1000, "output_tokens": 1000}]),
            _make_trajectory(2, [{"tool_call_error_rate": 15.0, "review_fix_requests": 2, "nb_tool_calls": 25, "agent_runtime_ms": 100000.0, "input_tokens": 30000, "output_tokens": 20000}]),
        ]

    def test_returns_scored_trajectories(self):
        trajectories = self._make_trajectories()
        scored = score_trajectories(trajectories)
        self.assertEqual(len(scored), 3)

    def test_sorted_by_score_descending(self):
        trajectories = self._make_trajectories()
        scored = score_trajectories(trajectories)
        # First should have highest score
        self.assertGreaterEqual(scored[0].score, scored[1].score)
        self.assertGreaterEqual(scored[1].score, scored[2].score)

    def test_highest_difficulty_is_first(self):
        trajectories = self._make_trajectories()
        scored = score_trajectories(trajectories)
        # Issue 0 has highest error rate + fix requests → should rank first
        self.assertEqual(scored[0].trajectory.issue.issue_number, 0)

    def test_lowest_difficulty_is_last(self):
        trajectories = self._make_trajectories()
        scored = score_trajectories(trajectories)
        # Issue 1 has zero error rate and zero fix requests → should rank last
        self.assertEqual(scored[-1].trajectory.issue.issue_number, 1)

    def test_custom_weights(self):
        trajectories = self._make_trajectories()
        # Put all weight on error_rate
        weights = {"error_rate": 1.0, "review_fix_requests": 0.0, "tool_calls": 0.0, "runtime": 0.0, "token_usage": 0.0}
        scored = score_trajectories(trajectories, weights=weights)
        self.assertEqual(len(scored), 3)
        # Still sorted by error rate since it's the only weight
        self.assertEqual(scored[0].trajectory.issue.issue_number, 0)

    def test_empty_list(self):
        scored = score_trajectories([])
        self.assertEqual(len(scored), 0)


class TestSelectCoreset(unittest.TestCase):
    def _make_scored(self):
        trajectories = [
            _make_trajectory(i, [{"tool_call_error_rate": float(i * 5), "review_fix_requests": i, "nb_tool_calls": i * 10, "agent_runtime_ms": float(i * 30000), "input_tokens": i * 10000, "output_tokens": i * 5000}])
            for i in range(5)
        ]
        return score_trajectories(trajectories)

    def test_select_top_n(self):
        scored = self._make_scored()
        coreset = select_coreset(scored, size=3)
        self.assertEqual(len(coreset), 3)

    def test_select_all_when_size_exceeds(self):
        scored = self._make_scored()
        coreset = select_coreset(scored, size=100)
        self.assertEqual(len(coreset), 5)

    def test_select_zero(self):
        scored = self._make_scored()
        coreset = select_coreset(scored, size=0)
        self.assertEqual(len(coreset), 0)

    def test_select_empty(self):
        coreset = select_coreset([], size=5)
        self.assertEqual(len(coreset), 0)

    def test_selected_have_scores(self):
        scored = self._make_scored()
        coreset = select_coreset(scored, size=3)
        for item in coreset:
            self.assertGreaterEqual(item.score, 0)


if __name__ == "__main__":
    unittest.main()
