#!/usr/bin/env python3
"""Tests for RHO (Retrospective Harness Optimization) components."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

from metrics_schema import Metrics
from trajectory_archive import (
    extract_metrics_from_comment,
    build_trajectory,
    load_archived_trajectories,
)
from coreset_selector import (
    normalize,
    compute_dimension_values,
    score_trajectories,
    select_coreset,
)
from harness_proposer import (
    generate_analytical_proposals,
    apply_mutations,
    analyze_pain_points,
)
from rho_select import (
    compute_variant_scores,
    compare_variants,
    should_create_pr,
    build_pr_body,
)


class TestTrajectoryArchive(unittest.TestCase):
    def test_extract_metrics_single(self):
        body = "Text\n<!-- agent-metrics: {\"run_id\":\"a\",\"input_tokens\":100,\"output_tokens\":200} -->"
        metrics = extract_metrics_from_comment(body)
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0].run_id, "a")

    def test_extract_metrics_multiple(self):
        body = (
            "<!-- agent-metrics: {\"run_id\":\"1\",\"input_tokens\":100,\"output_tokens\":200,\"agent_role\":\"dev\"} -->\n"
            "<!-- agent-metrics: {\"run_id\":\"2\",\"input_tokens\":50,\"output_tokens\":100,\"agent_role\":\"genius\"} -->"
        )
        metrics = extract_metrics_from_comment(body)
        self.assertEqual(len(metrics), 2)

    def test_extract_metrics_none(self):
        metrics = extract_metrics_from_comment("No metrics here.")
        self.assertEqual(len(metrics), 0)

    def test_build_trajectory_empty(self):
        issue = {"number": 42, "title": "T", "body": "B", "labels": [{"name": "bug"}],
                 "state": "closed", "closed_at": "2024-06-01", "created_at": "2024-05-01", "updated_at": "2024-06-01"}
        traj = build_trajectory(issue, [])
        self.assertEqual(traj["issue_number"], 42)
        self.assertEqual(traj["metrics_count"], 0)

    def test_build_trajectory_with_metrics(self):
        m = Metrics(run_id="r1", agent_role="dev", tool_call_error_rate=10.0,
                     review_fix_requests=2, nb_tool_calls=15, agent_runtime_ms=30000)
        comment = {"body": f"ans\n\n<!-- agent-metrics: {m.to_json()} -->"}
        issue = {"number": 1, "title": "H", "body": "B", "labels": [{"name": "hard"}],
                 "state": "closed", "closed_at": "2024-06-01", "created_at": "2024-05-01", "updated_at": "2024-06-01"}
        traj = build_trajectory(issue, [comment])
        self.assertEqual(traj["metrics_count"], 1)
        self.assertEqual(traj["quick_difficulty"]["max_error_rate"], 10.0)

    def test_load_archived_trajectories_empty(self):
        with patch("trajectory_archive.ARCHIVE_DIR", "/nonexistent"):
            self.assertEqual(load_archived_trajectories(), [])


class TestCoresetSelector(unittest.TestCase):
    def test_normalize(self):
        self.assertAlmostEqual(normalize(5, 0, 10), 0.5)
        self.assertEqual(normalize(5, 5, 5), 0.0)

    def test_compute_dimension_values(self):
        t1 = {"metrics_count": 1, "metrics": [{"input_tokens": 100, "output_tokens": 50}],
              "quick_difficulty": {"max_error_rate": 5, "total_fix_requests": 1,
                                   "total_tool_calls": 10, "avg_runtime_ms": 20000}, "labels": ["bug"]}
        t2 = {"metrics_count": 1, "metrics": [{"input_tokens": 200, "output_tokens": 100}],
              "quick_difficulty": {"max_error_rate": 15, "total_fix_requests": 3,
                                   "total_tool_calls": 20, "avg_runtime_ms": 40000}, "labels": ["feat"]}
        dims = compute_dimension_values([t1, t2])
        self.assertEqual(dims["error_rate"], [5, 15])
        self.assertEqual(dims["tokens"], [150, 300])

    def test_score_trajectories_orders_descending(self):
        t_easy = {"metrics_count": 1, "metrics": [], "quick_difficulty": {
            "max_error_rate": 0, "total_fix_requests": 0, "total_tool_calls": 5, "avg_runtime_ms": 1000}, "labels": ["easy"]}
        t_hard = {"metrics_count": 1, "metrics": [], "quick_difficulty": {
            "max_error_rate": 20, "total_fix_requests": 5, "total_tool_calls": 30, "avg_runtime_ms": 60000}, "labels": ["hard"]}
        w = {"error_rate": 0.3, "review_fix_requests": 0.25, "tool_calls": 0.2, "runtime": 0.15, "tokens": 0.1}
        scored = score_trajectories([t_easy, t_hard], w)
        self.assertGreater(scored[0][0], scored[1][0])

    def test_select_coreset_limits_and_scores(self):
        t1 = {"metrics_count": 1, "metrics": [], "quick_difficulty": {
            "max_error_rate": 20, "total_fix_requests": 5, "total_tool_calls": 30, "avg_runtime_ms": 60000}, "labels": ["a"]}
        t2 = {"metrics_count": 1, "metrics": [], "quick_difficulty": {
            "max_error_rate": 10, "total_fix_requests": 2, "total_tool_calls": 15, "avg_runtime_ms": 30000}, "labels": ["b"]}
        w = {"error_rate": 0.3, "review_fix_requests": 0.25, "tool_calls": 0.2, "runtime": 0.15, "tokens": 0.1}
        coreset = select_coreset(score_trajectories([t1, t2], w), coreset_size=1)
        self.assertEqual(len(coreset), 1)
        self.assertTrue("_difficulty_score" in coreset[0])

    def test_select_coreset_deduplicates_labels(self):
        t1 = {"metrics_count": 1, "metrics": [], "quick_difficulty": {
            "max_error_rate": 20, "total_fix_requests": 5, "total_tool_calls": 30, "avg_runtime_ms": 60000}, "labels": ["bug"]}
        t2 = {"metrics_count": 1, "metrics": [], "quick_difficulty": {
            "max_error_rate": 15, "total_fix_requests": 3, "total_tool_calls": 20, "avg_runtime_ms": 40000}, "labels": ["bug"]}
        t3 = {"metrics_count": 1, "metrics": [], "quick_difficulty": {
            "max_error_rate": 10, "total_fix_requests": 2, "total_tool_calls": 15, "avg_runtime_ms": 30000}, "labels": ["feat"]}
        w = {"error_rate": 0.3, "review_fix_requests": 0.25, "tool_calls": 0.2, "runtime": 0.15, "tokens": 0.1}
        coreset = select_coreset(score_trajectories([t1, t2, t3], w), coreset_size=5)
        self.assertEqual(len(coreset), 2)

    def test_analyze_pain_points_flagged(self):
        m = Metrics(tool_call_error_rate=20, review_fix_requests=5, nb_tool_calls=50, agent_runtime_ms=60000)
        traj = {"metrics_count": 1, "metrics": [m.to_dict()]}
        pp = analyze_pain_points([traj])
        self.assertIn("high_error_rate", pp["flagged"])
        self.assertIn("excessive_fix_requests", pp["flagged"])


class TestHarnessProposer(unittest.TestCase):
    def _make_config(self):
        from config import AppConfig, AgentConfig, LLMConfig, ProviderConfig
        p = {"or": ProviderConfig(api_url="https://api.test", api_token_env="K")}
        l = {"qwen": LLMConfig(model_path="v/qwen", provider=p["or"], default_temperature=0.0),
             "deep": LLMConfig(model_path="v/deep", provider=p["or"], default_temperature=0.2)}
        a = {"dev": AgentConfig(model=l["qwen"], temperature=0.2, prompt_file="d.md",
              tools=["terminal", "file_editor", "task_tracker"]),
             "genius": AgentConfig(model=l["qwen"], temperature=0.2, prompt_file="g.md", tools=["think", "finish"]),
             "review": AgentConfig(model=l["qwen"], temperature=0.1, prompt_file="r.md", tools=["think", "finish"])}
        return AppConfig(providers=p, llms=l, agents=a)

    def test_proposals_generated_for_high_error(self):
        cfg = self._make_config()
        pp = {"flagged": ["high_error_rate"], "stats": {"avg_error_rate": 20}}
        props = generate_analytical_proposals(cfg, pp, num_variants=5)
        self.assertTrue(any(p["id"].startswith("temp_") for p in props))

    def test_apply_mutations_temperature(self):
        cfg = self._make_config()
        orig_temp = cfg.agents["dev"].temperature
        var = apply_mutations(cfg, [{"type": "temperature_tweak", "agent": "dev",
                                     "field": "temperature", "from": orig_temp, "to": 0.5}])
        self.assertEqual(var.agents["dev"].temperature, 0.5)
        self.assertEqual(cfg.agents["dev"].temperature, orig_temp)

    def test_apply_mutations_tool_removal(self):
        cfg = self._make_config()
        var = apply_mutations(cfg, [{"type": "tool_add_remove", "agent": "dev",
                                     "field": "tools", "from": ["terminal", "file_editor"],
                                     "to": ["terminal"]}])
        self.assertEqual(var.agents["dev"].tools, ["terminal"])

    def test_apply_mutations_nonexistent_agent_skipped(self):
        cfg = self._make_config()
        var = apply_mutations(cfg, [{"type": "temperature_tweak", "agent": "fake",
                                     "field": "temperature", "from": 0, "to": 1}])
        self.assertNotIsInstance(var, type(None))
        self.assertEqual(var.agents["dev"].temperature, cfg.agents["dev"].temperature)


class TestRhoSelect(unittest.TestCase):
    def _make_summary(self):
        return {"results": [
            {"variant_id": "baseline", "success": True, "error": None,
             "runtime_seconds": 30, "metrics": {"tool_call_error_rate": 5, "agent_runtime_ms": 20000,
                                                "input_tokens": 1000, "output_tokens": 500,
                                                "review_fix_requests": 1, "nb_tool_calls": 15}},
            {"variant_id": "variant-x", "success": True, "error": None,
             "runtime_seconds": 25, "metrics": {"tool_call_error_rate": 2, "agent_runtime_ms": 15000,
                                                "input_tokens": 800, "output_tokens": 400,
                                                "review_fix_requests": 0, "nb_tool_calls": 12}},
        ]}

    def test_compute_variant_scores_orders_by_composite(self):
        scores = compute_variant_scores(self._make_summary())
        self.assertGreaterEqual(scores[0].composite_score, scores[1].composite_score)

    def test_compare_variants_identifies_winner(self):
        scores = compute_variant_scores(self._make_summary())
        winner, comp = compare_variants(scores)
        self.assertIsNotNone(winner)
        self.assertEqual(comp["winner"], winner.variant_id)

    def test_should_create_pr_above_threshold(self):
        _, comp = compare_variants(compute_variant_scores(self._make_summary()))
        self.assertTrue(should_create_pr(comp, threshold=-100))

    def test_build_pr_body_contains_winner(self):
        _, comp = compare_variants(compute_variant_scores(self._make_summary()))
        body = build_pr_body(comp)
        self.assertIn("RHO Harness Update", body)
        self.assertIn("Winning variant", body)
        self.assertIn("|", body)  # table

    def test_build_pr_body_no_winner(self):
        body = build_pr_body({"winner": None, "scores": []})
        self.assertIn("No variant outperformed", body)


if __name__ == "__main__":
    unittest.main()
