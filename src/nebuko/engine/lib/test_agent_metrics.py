#!/usr/bin/env python3
"""Tests for agent_metrics.py"""

import json
import re
import sys
import os
import unittest
from unittest.mock import patch, MagicMock
from io import StringIO

# Import the module under test
sys.path.insert(0, os.path.dirname(__file__))
from metrics_schema import Metrics
from workflow_state import STATE_MARKER_START, STATE_MARKER_END
from agent_script.sub_domains.metrics.report_cost_resolver import ReportCostResolver
from pricing.sub_domains.price_table.price_table import ModelPrice, PriceTable
from agent_metrics import (
    extract_metrics_from_comment,
    deduplicate_metrics,
    aggregate_by_model,
    aggregate_by_agent,
    generate_agent_summary_table,
    SecondsCell,
    InputTokenSplit,
    StatsTotals,
    format_number,
    format_cost,
    format_rate,
    get_agent_display_name,
    AGENT_DISPLAY_NAMES,
    _merge_per_tool_dict,
    _format_tool_dict,
    parse_iso_datetime,
    parse_agent_role,
    build_comment_record,
    collect_issue_data,
    build_human_metrics_section,
)


def _state_body(*entries, version=5):
    """Build a comment body carrying a ``workflow_state`` snapshot.

    Mirrors the collapsed `<details>` section emitted by
    ``workflow_state.features.comment`` so extraction exercises the real
    marker-based parser.
    """
    state = {
        "version": version,
        "workflow": "default",
        "step": "implement",
        "agent": "dev",
        "phase": "execution",
        "history": list(entries),
    }
    return (
        "<details>\n<summary>Workflow State (v{0})</summary>\n\n```json\n"
        "{1}\n{2}\n{3}\n```\n\n</details>\n".format(
            version, STATE_MARKER_START, json.dumps(state), STATE_MARKER_END
        )
    )


def _step_entry(run_id, **metrics_fields):
    """A history entry whose ``metrics`` carries ``run_id`` plus overrides."""
    return {
        "step": "implement",
        "agent": "dev",
        "summary": "did work",
        "turn_outcome": {"status": "completed", "appreciation": "approved"},
        "metrics": {"run_id": run_id, **metrics_fields},
    }


def _table_cells(row):
    """Split a rendered markdown table row into its trimmed cell contents."""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


class TestExtractMetricsFromComment(unittest.TestCase):
    """extract_metrics_from_comment reads per-step metrics from the state."""

    def test_extract_single_step(self):
        body = _state_body(_step_entry("run-1", model_name="gpt-4", input_tokens=100))
        result = extract_metrics_from_comment(body)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Metrics)
        self.assertEqual(result[0].model_name, "gpt-4")
        self.assertEqual(result[0].input_tokens, 100)
        self.assertEqual(result[0].run_id, "run-1")

    def test_extract_multiple_steps(self):
        body = _state_body(
            _step_entry("run-1", model_name="gpt-4", input_tokens=100),
            _step_entry("run-2", model_name="claude", input_tokens=200),
        )
        result = extract_metrics_from_comment(body)
        self.assertEqual(len(result), 2)
        self.assertEqual([m.model_name for m in result], ["gpt-4", "claude"])

    def test_non_overlapping_fields_survive(self):
        body = _state_body(
            _step_entry(
                "run-1",
                complexity="medium",
                start_time="2024-01-01T00:00:00",
                end_time="2024-01-01T00:01:00",
                agent_runtimes_per_call_ms=[10.0, 20.0],
                tool_calls_per_tool={"terminal": 3},
                errors_per_tool={"terminal": 1},
                tokens_per_tool={"terminal": {"input": 5, "output": 2}},
                dispatch_recall_count=2,
            )
        )
        (metric,) = extract_metrics_from_comment(body)
        self.assertEqual(metric.complexity, "medium")
        self.assertEqual(metric.agent_runtimes_per_call_ms, [10.0, 20.0])
        self.assertEqual(metric.tool_calls_per_tool, {"terminal": 3})
        self.assertEqual(metric.errors_per_tool, {"terminal": 1})
        self.assertEqual(metric.tokens_per_tool, {"terminal": {"input": 5, "output": 2}})
        self.assertEqual(metric.dispatch_recall_count, 2)

    def test_step_without_metrics_skipped(self):
        body = _state_body({"step": "analyze", "agent": "architect", "summary": "s"})
        self.assertEqual(extract_metrics_from_comment(body), [])

    def test_no_state_returns_empty(self):
        self.assertEqual(extract_metrics_from_comment("Just a normal comment."), [])

    def test_bot_marker_without_state(self):
        body = "<!-- bot-agent-architect -->\nSome answer"
        self.assertEqual(extract_metrics_from_comment(body), [])

    def test_malformed_state_json_skipped(self):
        body = f"{STATE_MARKER_START}\n{{invalid json}}\n{STATE_MARKER_END}"
        self.assertEqual(extract_metrics_from_comment(body), [])

    def test_empty_comment(self):
        self.assertEqual(extract_metrics_from_comment(""), [])


class TestDeduplicateMetrics(unittest.TestCase):
    """deduplicate_metrics collapses snapshot carry-over."""

    def test_repeated_run_kept_once(self):
        dupes = [
            Metrics(run_id="run-1", model_name="gpt-4"),
            Metrics(run_id="run-1", model_name="gpt-4"),
            Metrics(run_id="run-2", model_name="claude"),
        ]
        result = deduplicate_metrics(dupes)
        self.assertEqual([m.run_id for m in result], ["run-1", "run-2"])

    def test_first_occurrence_wins(self):
        dupes = [
            Metrics(run_id="run-1", input_tokens=100),
            Metrics(run_id="run-1", input_tokens=999),
        ]
        result = deduplicate_metrics(dupes)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].input_tokens, 100)

    def test_missing_run_id_uses_run_window(self):
        dupes = [
            Metrics(start_time="t0", end_time="t1", agent_role="dev"),
            Metrics(start_time="t0", end_time="t1", agent_role="dev"),
            Metrics(start_time="t0", end_time="t1", agent_role="architect"),
        ]
        result = deduplicate_metrics(dupes)
        self.assertEqual(len(result), 2)

    def test_empty(self):
        self.assertEqual(deduplicate_metrics([]), [])


class TestMetricsSchema(unittest.TestCase):
    """Test Metrics schema class."""

    def _make_full_metrics(self):
        """Create a full Metrics object for testing."""
        return Metrics(
            run_id="test-run-123",
            model_name="gpt-4",
            agent_role="architect",
            input_tokens=1000,
            output_tokens=500,
            max_context_window=8192,
            cache_hit_rate=0.4819,
            reasoning_tokens=10170,
            complexity="medium",
            tool_call_latency_ms=1500.5,
            agent_runtime_ms=5000.0,
            agent_runtimes_per_call_ms=[1000.0, 1500.0, 2500.0],
            nb_tool_calls=10,
            tool_call_error_rate=5.0,
            start_time="2024-01-01T00:00:00",
            end_time="2024-01-01T00:01:00",
            tool_calls_per_tool={"terminal": 5, "file_editor": 5},
            errors_per_tool={"terminal": 1},
            tokens_per_tool={
                "terminal": {"input": 500, "output": 250, "cache_hit": 100, "reasoning": 50},
                "file_editor": {"input": 500, "output": 250, "cache_hit": 50, "reasoning": 25},
            },
            review_fix_requests=2,
            cost=0.0123,
            tool_time_per_tool={"terminal": {"total_ms": 1200.0, "count": 5}},
            tool_runtime_ms=1200.0,
            wall_clock_ms=60000.0,
        )

    def test_to_dict_roundtrip(self):
        """Metrics should serialize to dict and back correctly."""
        original = self._make_full_metrics()
        d = original.to_dict()
        restored = Metrics.from_dict(d)
        self.assertEqual(restored.run_id, original.run_id)
        self.assertEqual(restored.model_name, original.model_name)
        self.assertEqual(restored.agent_role, original.agent_role)
        self.assertEqual(restored.input_tokens, original.input_tokens)
        self.assertEqual(restored.output_tokens, original.output_tokens)
        self.assertEqual(restored.cache_hit_rate, original.cache_hit_rate)
        self.assertEqual(restored.reasoning_tokens, original.reasoning_tokens)
        self.assertEqual(restored.complexity, original.complexity)
        self.assertEqual(restored.tool_call_latency_ms, original.tool_call_latency_ms)
        self.assertEqual(restored.agent_runtime_ms, original.agent_runtime_ms)
        self.assertEqual(restored.agent_runtimes_per_call_ms, original.agent_runtimes_per_call_ms)
        self.assertEqual(restored.nb_tool_calls, original.nb_tool_calls)
        self.assertEqual(restored.tool_call_error_rate, original.tool_call_error_rate)
        self.assertEqual(restored.start_time, original.start_time)
        self.assertEqual(restored.end_time, original.end_time)
        self.assertEqual(restored.tool_calls_per_tool, original.tool_calls_per_tool)
        self.assertEqual(restored.errors_per_tool, original.errors_per_tool)
        self.assertEqual(restored.tokens_per_tool, original.tokens_per_tool)
        self.assertEqual(restored.review_fix_requests, original.review_fix_requests)
        self.assertEqual(restored.cost, original.cost)
        self.assertEqual(restored.tool_time_per_tool, original.tool_time_per_tool)
        self.assertEqual(restored.tool_runtime_ms, original.tool_runtime_ms)
        self.assertEqual(restored.wall_clock_ms, original.wall_clock_ms)

    def test_from_dict_with_missing_optional_fields(self):
        """from_dict should handle missing optional fields gracefully."""
        minimal_dict = {
            "run_id": "test",
            "model_name": "gpt-4",
            "agent_role": "dev",
            "input_tokens": 100,
            "output_tokens": 50,
            "complexity": "low",
            "tool_call_latency_ms": 100.0,
            "agent_runtime_ms": 1000.0,
            "agent_runtimes_per_call_ms": [],
            "nb_tool_calls": 1,
            "tool_call_error_rate": 0.0,
            "start_time": "2024-01-01T00:00:00",
            "end_time": "2024-01-01T00:01:00",
            "tool_calls_per_tool": {},
            "errors_per_tool": {},
            "tokens_per_tool": {},
        }
        m = Metrics.from_dict(minimal_dict)
        self.assertEqual(m.run_id, "test")
        self.assertEqual(m.review_fix_requests, 0)
        self.assertEqual(m.cost, 0.0)
        self.assertIsNone(m.error)
        # Tool-time fields default when an older payload omits them (issue #1016).
        self.assertEqual(m.tool_time_per_tool, {})
        self.assertEqual(m.tool_runtime_ms, 0.0)
        self.assertEqual(m.wall_clock_ms, 0.0)

    def test_from_dict_with_error_field(self):
        """from_dict should handle error field."""
        dict_with_error = {
            "run_id": "test",
            "model_name": "gpt-4",
            "agent_role": "dev",
            "input_tokens": 100,
            "output_tokens": 50,
            "complexity": "low",
            "tool_call_latency_ms": 100.0,
            "agent_runtime_ms": 1000.0,
            "agent_runtimes_per_call_ms": [],
            "nb_tool_calls": 1,
            "tool_call_error_rate": 0.0,
            "start_time": "2024-01-01T00:00:00",
            "end_time": "2024-01-01T00:01:00",
            "tool_calls_per_tool": {},
            "errors_per_tool": {},
            "tokens_per_tool": {},
            "error": "Something went wrong",
        }
        m = Metrics.from_dict(dict_with_error)
        self.assertEqual(m.error, "Something went wrong")

    def test_to_json_compact(self):
        """to_json(compact=True) should produce compact JSON."""
        m = self._make_full_metrics()
        json_str = m.to_json(compact=True)
        # Compact JSON should not have spaces after separators
        self.assertNotIn(": ", json_str)
        self.assertNotIn(", ", json_str)
        # Should be valid JSON
        parsed = json.loads(json_str)
        self.assertEqual(parsed["model_name"], "gpt-4")

    def test_to_json_pretty(self):
        """to_json(compact=False) should produce pretty JSON."""
        m = self._make_full_metrics()
        json_str = m.to_json(compact=False)
        # Pretty JSON should have newlines
        self.assertIn("\n", json_str)
        # Should be valid JSON
        parsed = json.loads(json_str)
        self.assertEqual(parsed["model_name"], "gpt-4")

    def test_to_json_from_json_roundtrip(self):
        """to_json + from_json should roundtrip correctly."""
        original = self._make_full_metrics()
        json_str = original.to_json(compact=True)
        restored = Metrics.from_json(json_str)
        self.assertEqual(restored.run_id, original.run_id)
        self.assertEqual(restored.input_tokens, original.input_tokens)
        self.assertEqual(restored.tool_calls_per_tool, original.tool_calls_per_tool)

    def test_metrics_backward_compatible_with_dict(self):
        """Metrics should be usable where dicts were expected."""
        m = self._make_full_metrics()
        d = m.to_dict()
        # Verify dict has all expected keys
        expected_keys = {
            "run_id", "model_name", "agent_role", "input_tokens", "output_tokens",
            "max_context_window", "cache_hit_rate", "cache_hit_tokens",
            "reasoning_tokens", "cost",
            "complexity", "tool_call_latency_ms", "agent_runtime_ms",
            "agent_runtimes_per_call_ms", "nb_tool_calls", "tool_call_error_rate",
            "start_time", "end_time", "tool_calls_per_tool", "errors_per_tool",
            "tokens_per_tool", "tool_time_per_tool", "tool_runtime_ms",
            "wall_clock_ms", "review_fix_requests", "dispatch_recall_count",
        }
        self.assertEqual(set(d.keys()), expected_keys)


class TestAggregateByModel(unittest.TestCase):
    """Test aggregate_by_model function."""

    def _make_metric(self, model, input_tokens=100, output_tokens=50,
                     latency=1000.0, tool_calls=5, error_rate=0.0,
                     review_fix_requests=0, max_context_window=8192,
                     cost=0.0, cache_hit_rate=0.0, reasoning_tokens=0,
                     cache_hit_tokens=0):
        return Metrics(
            run_id="test-run",
            model_name=model,
            agent_role="architect",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            max_context_window=max_context_window,
            cache_hit_rate=cache_hit_rate,
            cache_hit_tokens=cache_hit_tokens,
            reasoning_tokens=reasoning_tokens,
            complexity="medium",
            tool_call_latency_ms=latency,
            agent_runtime_ms=5000.0,
            agent_runtimes_per_call_ms=[],
            nb_tool_calls=tool_calls,
            tool_call_error_rate=error_rate,
            start_time="2024-01-01T00:00:00",
            end_time="2024-01-01T00:01:00",
            tool_calls_per_tool={},
            errors_per_tool={},
            tokens_per_tool={},
            review_fix_requests=review_fix_requests,
            cost=cost,
        )

    def test_single_model_single_run(self):
        metrics = [self._make_metric("gpt-4")]
        result = aggregate_by_model(metrics)
        self.assertEqual(result["gpt-4"]["runs"], 1)
        self.assertEqual(result["gpt-4"]["total_input_tokens"], 100)
        self.assertEqual(result["gpt-4"]["total_output_tokens"], 50)

    def test_single_model_multiple_runs(self):
        metrics = [
            self._make_metric("gpt-4", input_tokens=100, output_tokens=50),
            self._make_metric("gpt-4", input_tokens=200, output_tokens=100),
        ]
        result = aggregate_by_model(metrics)
        self.assertEqual(result["gpt-4"]["runs"], 2)
        self.assertEqual(result["gpt-4"]["total_input_tokens"], 300)
        self.assertEqual(result["gpt-4"]["total_output_tokens"], 150)

    def test_multiple_models(self):
        metrics = [
            self._make_metric("gpt-4", input_tokens=100),
            self._make_metric("claude", input_tokens=200),
            self._make_metric("gpt-4", input_tokens=150),
        ]
        result = aggregate_by_model(metrics)
        self.assertEqual(result["gpt-4"]["runs"], 2)
        self.assertEqual(result["gpt-4"]["total_input_tokens"], 250)
        self.assertEqual(result["claude"]["runs"], 1)
        self.assertEqual(result["claude"]["total_input_tokens"], 200)

    def test_empty_metrics(self):
        result = aggregate_by_model([])
        self.assertEqual(len(result), 0)

    def test_unknown_model(self):
        # Legacy dict support: no model_name
        metrics = [{"input_tokens": 100}]
        result = aggregate_by_model(metrics)
        self.assertIn("unknown", result)
        self.assertEqual(result["unknown"]["runs"], 1)

    def test_review_fix_requests_aggregation(self):
        """Review fix requests should be summed across runs."""
        metrics = [
            self._make_metric("gpt-4", review_fix_requests=2),
            self._make_metric("gpt-4", review_fix_requests=3),
        ]
        result = aggregate_by_model(metrics)
        self.assertEqual(result["gpt-4"]["review_fix_requests_sum"], 5)

    def test_review_fix_requests_default_zero(self):
        """Review fix requests should default to 0 when not present."""
        metrics = [self._make_metric("gpt-4")]
        result = aggregate_by_model(metrics)
        self.assertEqual(result["gpt-4"]["review_fix_requests_sum"], 0)

    def test_metrics_object_aggregation(self):
        """Metrics objects should aggregate correctly."""
        metrics = [
            self._make_metric("gpt-4", input_tokens=100, output_tokens=50),
            self._make_metric("gpt-4", input_tokens=200, output_tokens=100),
        ]
        result = aggregate_by_model(metrics)
        self.assertEqual(result["gpt-4"]["runs"], 2)
        self.assertEqual(result["gpt-4"]["total_input_tokens"], 300)
        self.assertEqual(result["gpt-4"]["total_output_tokens"], 150)

    def test_legacy_dict_aggregation(self):
        """Legacy dict metrics should still aggregate correctly."""
        metrics = [
            {"model_name": "gpt-4", "input_tokens": 100, "output_tokens": 50},
            {"model_name": "gpt-4", "input_tokens": 200, "output_tokens": 100},
        ]
        result = aggregate_by_model(metrics)
        self.assertEqual(result["gpt-4"]["runs"], 2)
        self.assertEqual(result["gpt-4"]["total_input_tokens"], 300)
        self.assertEqual(result["gpt-4"]["total_output_tokens"], 150)

    def test_cost_aggregation_metrics_objects(self):
        """Cost should sum across runs per model."""
        metrics = [
            self._make_metric("gpt-4", cost=0.01),
            self._make_metric("gpt-4", cost=0.02),
        ]
        result = aggregate_by_model(metrics)
        self.assertAlmostEqual(result["gpt-4"]["total_cost"], 0.03)

    def test_cost_aggregation_legacy_dicts(self):
        """Legacy dicts without a cost key default to zero."""
        metrics = [
            {"model_name": "gpt-4", "cost": 0.5},
            {"model_name": "gpt-4"},
        ]
        result = aggregate_by_model(metrics)
        self.assertAlmostEqual(result["gpt-4"]["total_cost"], 0.5)

    def test_provider_health_means_metrics_objects(self):
        """TPS/TTFT are averaged over the runs that observed them (#829)."""
        first = self._make_metric("gpt-4")
        first.tokens_per_second = 100.0
        first.time_to_first_token = 0.5
        second = self._make_metric("gpt-4")
        second.tokens_per_second = 200.0
        second.time_to_first_token = 0.7
        agg = aggregate_by_model([first, second])["gpt-4"]
        self.assertEqual(agg["tps_count"], 2)
        self.assertEqual(agg["ttft_count"], 2)
        self.assertAlmostEqual(agg["tps_sum"] / agg["tps_count"], 150.0)
        self.assertAlmostEqual(agg["ttft_sum"] / agg["ttft_count"], 0.6)

    def test_provider_health_counts_are_independent(self):
        """A run missing one value still contributes the other."""
        partial = self._make_metric("gpt-4")
        partial.tokens_per_second = 90.0
        agg = aggregate_by_model([partial])["gpt-4"]
        self.assertEqual(agg["tps_count"], 1)
        self.assertEqual(agg["ttft_count"], 0)

    def test_provider_health_absent_leaves_counts_at_zero(self):
        """Runs without observations leave both counters empty."""
        agg = aggregate_by_model([self._make_metric("gpt-4")])["gpt-4"]
        self.assertEqual(agg["tps_count"], 0)
        self.assertEqual(agg["ttft_count"], 0)

    def test_provider_health_legacy_dicts(self):
        """Legacy dicts read the two provider-health keys."""
        metrics = [
            {"model_name": "gpt-4", "tokens_per_second": 10.0, "time_to_first_token": 1.0},
            {"model_name": "gpt-4", "tokens_per_second": 20.0},
        ]
        agg = aggregate_by_model(metrics)["gpt-4"]
        self.assertEqual(agg["tps_count"], 2)
        self.assertEqual(agg["ttft_count"], 1)

    def test_cache_and_reasoning_metrics_objects(self):
        """Reasoning/cache-hit tokens sum; the rate is the input-weighted mean (#888)."""
        first = self._make_metric(
            "gpt-4", input_tokens=1000, cache_hit_rate=0.5, reasoning_tokens=100,
            cache_hit_tokens=400,
        )
        second = self._make_metric(
            "gpt-4", input_tokens=3000, cache_hit_rate=0.6, reasoning_tokens=200,
            cache_hit_tokens=900,
        )
        agg = aggregate_by_model([first, second])["gpt-4"]
        self.assertEqual(agg["total_reasoning_tokens"], 300)
        self.assertEqual(agg["total_cache_hit_tokens"], 1300)
        self.assertEqual(agg["cache_weight"], 4000)
        # (0.5*1000 + 0.6*3000) / 4000 = 2300 / 4000 = 0.575
        self.assertAlmostEqual(agg["cache_weighted_sum"] / agg["cache_weight"], 0.575)

    def test_cache_and_reasoning_default_to_zero(self):
        """A run with no signals contributes no weight, hits, or reasoning (#888)."""
        agg = aggregate_by_model([self._make_metric("gpt-4", input_tokens=0)])["gpt-4"]
        self.assertEqual(agg["total_reasoning_tokens"], 0)
        self.assertEqual(agg["total_cache_hit_tokens"], 0)
        self.assertEqual(agg["cache_weight"], 0)
        self.assertEqual(agg["cache_weighted_sum"], 0.0)

    def test_cache_and_reasoning_legacy_dicts(self):
        """Legacy dicts read the two new keys (#888)."""
        metrics = [
            {"model_name": "gpt-4", "input_tokens": 1000,
             "cache_hit_rate": 0.25, "reasoning_tokens": 10, "cache_hit_tokens": 100},
            {"model_name": "gpt-4", "input_tokens": 1000,
             "cache_hit_rate": 0.75, "reasoning_tokens": 20, "cache_hit_tokens": 300},
        ]
        agg = aggregate_by_model(metrics)["gpt-4"]
        self.assertEqual(agg["total_reasoning_tokens"], 30)
        self.assertEqual(agg["total_cache_hit_tokens"], 400)
        self.assertAlmostEqual(agg["cache_weighted_sum"] / agg["cache_weight"], 0.5)

    def test_uncached_input_tokens_are_derived_per_run(self):
        """Model buckets carry the per-run uncached input sum next to the raw total (#948)."""
        metrics = [
            self._make_metric("gpt-4", input_tokens=4000, cache_hit_tokens=1920),
            self._make_metric("gpt-4", input_tokens=1000),
        ]
        agg = aggregate_by_model(metrics)["gpt-4"]
        self.assertEqual(agg["total_input_tokens"], 5000)
        self.assertEqual(agg["total_uncached_input_tokens"], 3080)


class TestAggregateByAgent(unittest.TestCase):
    """Test aggregate_by_agent function."""

    def _make_metric(self, agent, input_tokens=100, output_tokens=50,
                     latency=1000.0, tool_calls=5, error_rate=0.0,
                     review_fix_requests=0, max_context_window=8192,
                     cost=0.0, cache_hit_rate=0.0, reasoning_tokens=0,
                     cache_hit_tokens=0):
        return Metrics(
            run_id="test-run",
            model_name="gpt-4",
            agent_role=agent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            max_context_window=max_context_window,
            cache_hit_rate=cache_hit_rate,
            cache_hit_tokens=cache_hit_tokens,
            reasoning_tokens=reasoning_tokens,
            complexity="medium",
            tool_call_latency_ms=latency,
            agent_runtime_ms=5000.0,
            agent_runtimes_per_call_ms=[],
            nb_tool_calls=tool_calls,
            tool_call_error_rate=error_rate,
            start_time="2024-01-01T00:00:00",
            end_time="2024-01-01T00:01:00",
            tool_calls_per_tool={},
            errors_per_tool={},
            tokens_per_tool={},
            review_fix_requests=review_fix_requests,
            cost=cost,
        )

    def test_single_agent_single_run(self):
        metrics = [self._make_metric("architect")]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["runs"], 1)
        self.assertEqual(result["architect"]["total_input_tokens"], 100)

    def test_single_agent_multiple_runs(self):
        metrics = [
            self._make_metric("architect", input_tokens=100, output_tokens=50),
            self._make_metric("architect", input_tokens=200, output_tokens=100),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["runs"], 2)
        self.assertEqual(result["architect"]["total_input_tokens"], 300)

    def test_multiple_agents(self):
        metrics = [
            self._make_metric("architect", input_tokens=100),
            self._make_metric("dev", input_tokens=200),
            self._make_metric("architect", input_tokens=150),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["runs"], 2)
        self.assertEqual(result["architect"]["total_input_tokens"], 250)
        self.assertEqual(result["dev"]["runs"], 1)
        self.assertEqual(result["dev"]["total_input_tokens"], 200)

    def test_review_fix_requests_aggregation(self):
        """Review fix requests should be summed across runs."""
        metrics = [
            self._make_metric("architect", review_fix_requests=2),
            self._make_metric("architect", review_fix_requests=3),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["review_fix_requests_sum"], 5)

    def test_review_fix_requests_dev_zero(self):
        """Dev agent should have 0 review fix requests."""
        metrics = [self._make_metric("dev", review_fix_requests=0)]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["dev"]["review_fix_requests_sum"], 0)

    def test_review_fix_requests_context_zero(self):
        """Context agent should have 0 review fix requests."""
        metrics = [self._make_metric("context", review_fix_requests=0)]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["context"]["review_fix_requests_sum"], 0)

    def test_review_fix_requests_default_zero(self):
        """Review fix requests should default to 0 when not present."""
        metrics = [self._make_metric("architect")]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["review_fix_requests_sum"], 0)

    def test_metrics_object_aggregation(self):
        """Metrics objects should aggregate correctly."""
        metrics = [
            self._make_metric("architect", input_tokens=100, output_tokens=50),
            self._make_metric("architect", input_tokens=200, output_tokens=100),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["runs"], 2)
        self.assertEqual(result["architect"]["total_input_tokens"], 300)
        self.assertEqual(result["architect"]["total_output_tokens"], 150)

    def test_legacy_dict_aggregation(self):
        """Legacy dict metrics should still aggregate correctly."""
        metrics = [
            {"agent_role": "architect", "input_tokens": 100, "output_tokens": 50},
            {"agent_role": "architect", "input_tokens": 200, "output_tokens": 100},
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["runs"], 2)
        self.assertEqual(result["architect"]["total_input_tokens"], 300)
        self.assertEqual(result["architect"]["total_output_tokens"], 150)

    def test_cost_aggregation_metrics_objects(self):
        """Cost should sum across runs per agent."""
        metrics = [
            self._make_metric("architect", cost=0.01),
            self._make_metric("architect", cost=0.02),
        ]
        result = aggregate_by_agent(metrics)
        self.assertAlmostEqual(result["architect"]["total_cost"], 0.03)

    def test_cost_aggregation_legacy_dicts(self):
        """Legacy dicts without a cost key default to zero."""
        metrics = [
            {"agent_role": "dev", "cost": 1.25},
            {"agent_role": "dev"},
        ]
        result = aggregate_by_agent(metrics)
        self.assertAlmostEqual(result["dev"]["total_cost"], 1.25)

    def test_cache_and_reasoning_aggregation(self):
        """Reasoning/cache-hit tokens sum; the rate is the input-weighted mean (#888)."""
        metrics = [
            self._make_metric(
                "dev", input_tokens=1000, cache_hit_rate=0.4, reasoning_tokens=100,
                cache_hit_tokens=200,
            ),
            self._make_metric(
                "dev", input_tokens=1000, cache_hit_rate=0.8, reasoning_tokens=50,
                cache_hit_tokens=300,
            ),
        ]
        agg = aggregate_by_agent(metrics)["dev"]
        self.assertEqual(agg["total_reasoning_tokens"], 150)
        self.assertEqual(agg["total_cache_hit_tokens"], 500)
        # (0.4*1000 + 0.8*1000) / 2000 = 1200 / 2000 = 0.6
        self.assertAlmostEqual(agg["cache_weighted_sum"] / agg["cache_weight"], 0.6)


class TestFormatNumber(unittest.TestCase):
    """Test format_number function."""

    def test_simple_number(self):
        self.assertEqual(format_number(100), "100")

    def test_thousands(self):
        self.assertEqual(format_number(1000), "1,000")

    def test_millions(self):
        self.assertEqual(format_number(1500000), "1,500,000")

    def test_zero(self):
        self.assertEqual(format_number(0), "0")


class TestFormatCost(unittest.TestCase):
    """Test format_cost function."""

    def test_zero(self):
        self.assertEqual(format_cost(0.0), "$0.0000")

    def test_sub_cent_precision(self):
        self.assertEqual(format_cost(0.0123), "$0.0123")

    def test_thousands_separator(self):
        self.assertEqual(format_cost(1234.5), "$1,234.5000")

    def test_rounds_to_four_decimals(self):
        self.assertEqual(format_cost(0.123456), "$0.1235")


class TestFormatRate(unittest.TestCase):
    """Test format_rate function."""

    def test_typical_rate(self):
        self.assertEqual(format_rate(0.4819), "48.19%")

    def test_full_and_zero_cache(self):
        self.assertEqual(format_rate(1.0), "100.00%")

    def test_zero_renders_not_available(self):
        self.assertEqual(format_rate(0.0), "n/a")

    def test_missing_rate_renders_not_available(self):
        self.assertEqual(format_rate(None), "n/a")


class TestGetAgentDisplayName(unittest.TestCase):
    """Test get_agent_display_name function."""

    def test_architect(self):
        self.assertEqual(get_agent_display_name("architect"), "Architect")

    def test_context(self):
        self.assertEqual(get_agent_display_name("context"), "Context")

    def test_dev(self):
        self.assertEqual(get_agent_display_name("dev"), "Dev")

    def test_unknown_agent(self):
        self.assertEqual(get_agent_display_name("unknown"), "Unknown")

    def test_custom_agent(self):
        self.assertEqual(get_agent_display_name("custom"), "Custom")


class TestAggregateByAgent(unittest.TestCase):
    """Test aggregate_by_agent function."""

    def _make_metric(self, agent, input_tokens=100, output_tokens=50,
                     latency=1000.0, tool_calls=5, error_rate=0.0,
                     cache_hit_tokens=0):
        return {
            "model_name": "gpt-4",
            "agent_role": agent,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "max_context_window": 8192,
            "tool_call_latency_ms": latency,
            "nb_tool_calls": tool_calls,
            "tool_call_error_rate": error_rate,
            "cache_hit_tokens": cache_hit_tokens,
        }

    def test_single_agent_single_run(self):
        metrics = [self._make_metric("architect")]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["runs"], 1)
        self.assertEqual(result["architect"]["total_input_tokens"], 100)
        self.assertEqual(result["architect"]["total_output_tokens"], 50)

    def test_single_agent_multiple_runs(self):
        metrics = [
            self._make_metric("architect", input_tokens=100, output_tokens=50),
            self._make_metric("architect", input_tokens=200, output_tokens=100),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["runs"], 2)
        self.assertEqual(result["architect"]["total_input_tokens"], 300)
        self.assertEqual(result["architect"]["total_output_tokens"], 150)

    def test_uncached_input_tokens_are_derived_per_run(self):
        """Agent buckets carry the per-run uncached input sum next to the raw total (#948)."""
        metrics = [
            self._make_metric("architect", input_tokens=4000, cache_hit_tokens=1920),
            self._make_metric("architect", input_tokens=1000),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["total_input_tokens"], 5000)
        self.assertEqual(result["architect"]["total_uncached_input_tokens"], 3080)

    def test_uncached_input_tokens_guard_applies_per_run(self):
        """A bucket mixing token shapes guards each run, never post-hoc Σinput − Σcache (#948).

        Run 1 is the exclusive/ACP shape (cache_read 9000 > input 100, SDK #5975), run 2
        the inclusive litellm shape: the correct sum is 100 + 600 = 700, whereas a
        rescaled post-hoc subtraction 900 − 9200 would be wrong.
        """
        metrics = [
            self._make_metric("architect", input_tokens=100, cache_hit_tokens=9000),
            self._make_metric("architect", input_tokens=800, cache_hit_tokens=200),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["total_input_tokens"], 900)
        self.assertEqual(result["architect"]["total_uncached_input_tokens"], 700)

    def test_multiple_agents(self):
        metrics = [
            self._make_metric("architect", input_tokens=100),
            self._make_metric("context", input_tokens=200),
            self._make_metric("dev", input_tokens=150),
            self._make_metric("architect", input_tokens=50),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["runs"], 2)
        self.assertEqual(result["architect"]["total_input_tokens"], 150)
        self.assertEqual(result["context"]["runs"], 1)
        self.assertEqual(result["context"]["total_input_tokens"], 200)
        self.assertEqual(result["dev"]["runs"], 1)
        self.assertEqual(result["dev"]["total_input_tokens"], 150)

    def test_empty_metrics(self):
        result = aggregate_by_agent([])
        self.assertEqual(len(result), 0)

    def test_missing_agent_role_fallback(self):
        metrics = [{"model_name": "gpt-4", "input_tokens": 100}]  # no agent_role
        result = aggregate_by_agent(metrics)
        self.assertIn("unknown", result)
        self.assertEqual(result["unknown"]["runs"], 1)

    def test_mixed_with_and_without_agent_role(self):
        metrics = [
            self._make_metric("architect", input_tokens=100),
            {"model_name": "gpt-4", "input_tokens": 200},  # no agent_role
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["runs"], 1)
        self.assertEqual(result["unknown"]["runs"], 1)

    def test_provider_health_legacy_dicts(self):
        """Legacy dict path reads both provider-health keys (#829)."""
        metrics = [
            {"agent_role": "dev", "tokens_per_second": 10.0, "time_to_first_token": 1.0},
            {"agent_role": "dev", "time_to_first_token": 0.5},
        ]
        agg = aggregate_by_agent(metrics)["dev"]
        self.assertEqual(agg["tps_count"], 1)
        self.assertEqual(agg["ttft_count"], 2)
        self.assertAlmostEqual(agg["ttft_sum"] / agg["ttft_count"], 0.75)

    def test_provider_health_metrics_objects(self):
        """Metrics objects feed the same sums/counters."""
        metric = Metrics(
            run_id="r1",
            model_name="gpt-4",
            agent_role="dev",
            tokens_per_second=42.0,
            time_to_first_token=0.25,
        )
        agg = aggregate_by_agent([metric])["dev"]
        self.assertEqual(agg["tps_sum"], 42.0)
        self.assertEqual(agg["ttft_count"], 1)


class TestGenerateAgentSummaryTable(unittest.TestCase):
    """Test generate_agent_summary_table function."""

    def _make_aggregated(self):
        return {
            "architect": {
                "runs": 2,
                "total_input_tokens": 300,
                "total_uncached_input_tokens": 300,
                "total_output_tokens": 150,
                "total_context_window": 16384,
                "latency_sum": 2000.0,
                "llm_runtime_sum": 10000.0,
                "tool_calls_sum": 10,
                "error_rate_sum": 0.0,
                "review_fix_requests_sum": 0,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
            "context": {
                "runs": 1,
                "total_input_tokens": 200,
                "total_uncached_input_tokens": 200,
                "total_output_tokens": 100,
                "total_context_window": 8192,
                "latency_sum": 1500.0,
                "llm_runtime_sum": 5000.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 5.0,
                "review_fix_requests_sum": 0,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
            "dev": {
                "runs": 1,
                "total_input_tokens": 400,
                "total_uncached_input_tokens": 400,
                "total_output_tokens": 200,
                "total_context_window": 16384,
                "latency_sum": 3000.0,
                "llm_runtime_sum": 8000.0,
                "tool_calls_sum": 8,
                "error_rate_sum": 2.0,
                "review_fix_requests_sum": 0,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
        }

    def test_empty_returns_empty(self):
        result = generate_agent_summary_table({})
        self.assertEqual(result, "")

    def test_table_header(self):
        result = generate_agent_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        self.assertIn("| Agent | Runs |", result_str)
        self.assertIn("## 📊 Agent Statistics Summary", result_str)

    def test_agent_display_names(self):
        result = generate_agent_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        self.assertIn("| Architect ", result_str)
        self.assertIn("| Context ", result_str)
        self.assertIn("| Dev ", result_str)

    def test_agent_sort_order(self):
        result = generate_agent_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        architect_pos = result_str.index("| Architect ")
        context_pos = result_str.index("| Context ")
        dev_pos = result_str.index("| Dev ")
        self.assertLess(architect_pos, context_pos)
        self.assertLess(context_pos, dev_pos)

    def test_totals_row(self):
        """The removed Totals sentence is now the table's final summary row."""
        result = generate_agent_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        self.assertNotIn("Totals:", result_str)
        self.assertNotIn(" runs · ", result_str)
        self.assertNotIn("Model Statistics Summary", result_str)
        self.assertEqual(result[-1], "| **Totals** | 4 | 900 | 450 | — | — | — | $0.0000 | — | — | — | — | — | — | — | — | — |")

    def test_totals_row_is_contiguous_and_last(self):
        """GFM ends a table at a blank line, so the totals row sits directly beneath."""
        lines = generate_agent_summary_table(self._make_aggregated())
        self.assertTrue(lines[-1].startswith("| **Totals** "))
        self.assertNotEqual(lines[-2], "")

    def test_single_run_totals_row(self):
        agg = {
            "architect": {
                "runs": 1,
                "total_input_tokens": 100,
                "total_uncached_input_tokens": 100,
                "total_output_tokens": 50,
                "total_context_window": 8192,
                "latency_sum": 1000.0,
                "llm_runtime_sum": 5000.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 0.0,
                "review_fix_requests_sum": 0,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            }
        }
        result = generate_agent_summary_table(agg)
        totals_row = result[-1]
        # Row semantics win: the runs cell is the bare integer, never pluralized.
        self.assertTrue(totals_row.startswith("| **Totals** | 1 |"))
        self.assertNotIn("1 runs", totals_row)

    def test_average_latency(self):
        result = generate_agent_summary_table(self._make_aggregated())
        # architect: 2000ms/2 = 1000ms avg = 1s (whole seconds, issue #947)
        architect_line = [l for l in result if "Architect" in l][0]
        self.assertEqual(_table_cells(architect_line)[9], "1")

    def test_average_llm_runtime_seconds(self):
        result = generate_agent_summary_table(self._make_aggregated())
        # architect: 10000ms/2 = 5000ms avg = 5s
        architect_line = [l for l in result if "Architect" in l][0]
        self.assertEqual(_table_cells(architect_line)[10], "5")

    def test_seconds_unit_labels(self):
        """Duration columns are labelled seconds; no millisecond label survives."""
        result = generate_agent_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        self.assertIn("| Avg Latency (s) |", result_str)
        self.assertIn("| Avg LLM Runtime (s) |", result_str)
        self.assertNotIn("(ms)", result_str)

    def test_average_tool_calls(self):
        result = generate_agent_summary_table(self._make_aggregated())
        architect_line = [l for l in result if "Architect" in l][0]
        self.assertIn("5.0", architect_line)

    def test_tool_errors_column(self):
        """Test that tool errors column shows correct values."""
        result = generate_agent_summary_table(self._make_aggregated())
        # All per-tool error dicts are empty, so all should show 0
        for line in result:
            if any(name in line for name in ["| Architect ", "| Context ", "| Dev "]):
                self.assertIn("| 0 ", line)

    def test_fix_requests_column_header(self):
        """Test that Fix Requests column header is present."""
        result = generate_agent_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        self.assertIn("Fix Requests", result_str)

    def test_fix_requests_column_zero_by_default(self):
        """Test that Fix Requests column shows 0 when no fix requests."""
        result = generate_agent_summary_table(self._make_aggregated())
        for line in result:
            if any(name in line for name in ["| Architect ", "| Context ", "| Dev "]):
                self.assertIn("| 0 ", line)

    def test_fix_requests_column_with_values(self):
        """Test that Fix Requests column shows correct values."""
        agg = {
            "architect": {
                "runs": 2,
                "total_input_tokens": 300,
                "total_uncached_input_tokens": 300,
                "total_output_tokens": 150,
                "total_context_window": 16384,
                "latency_sum": 2000.0,
                "llm_runtime_sum": 10000.0,
                "tool_calls_sum": 10,
                "error_rate_sum": 0.0,
                "review_fix_requests_sum": 5,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
            "dev": {
                "runs": 1,
                "total_input_tokens": 200,
                "total_uncached_input_tokens": 200,
                "total_output_tokens": 100,
                "total_context_window": 8192,
                "latency_sum": 1500.0,
                "llm_runtime_sum": 5000.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 0.0,
                "review_fix_requests_sum": 0,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
        }
        result = generate_agent_summary_table(agg)
        architect_line = [l for l in result if "Architect" in l][0]
        dev_line = [l for l in result if "Dev" in l][0]
        self.assertIn("| 5 ", architect_line)
        self.assertIn("| 0 ", dev_line)

    def test_backward_compatible_without_fix_requests(self):
        """Agent table should show 0 for agents without review_fix_requests_sum."""
        agg = {
            "architect": {
                "runs": 1,
                "total_input_tokens": 100,
                "total_uncached_input_tokens": 100,
                "total_output_tokens": 50,
                "total_context_window": 8192,
                "latency_sum": 1000.0,
                "llm_runtime_sum": 5000.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 0.0,
                # No review_fix_requests_sum key
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            }
        }
        result = generate_agent_summary_table(agg)
        result_str = "\n".join(result)
        self.assertIn("| 0 ", result_str)

    def test_cost_column_and_total(self):
        """Agent table should include a Cost column and sum costs in its totals row."""
        agg = {
            "architect": {
                "runs": 1,
                "total_input_tokens": 100,
                "total_uncached_input_tokens": 100,
                "total_output_tokens": 50,
                "total_context_window": 8192,
                "latency_sum": 1000.0,
                "llm_runtime_sum": 5000.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 0.0,
                "total_cost": 0.1234,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            }
        }
        result = generate_agent_summary_table(agg)
        result_str = "\n".join(result)
        self.assertIn("Cost (USD)", result_str)
        self.assertIn("$0.1234", result_str)
        totals_row = result[-1]
        # The totals cost cell is the shared format_cost output, not a bespoke string.
        self.assertIn(f"| {format_cost(0.1234)} |", totals_row)

    def test_cache_hit_and_reasoning_columns(self):
        """The columns render the rate, summed cache-hit and summed reasoning (#888)."""
        agg = {
            "dev": {
                "runs": 1,
                "total_input_tokens": 4000,
                "total_uncached_input_tokens": 2080,
                "total_output_tokens": 200,
                "total_context_window": 16384,
                "latency_sum": 3000.0,
                "llm_runtime_sum": 8000.0,
                "tool_calls_sum": 8,
                "error_rate_sum": 2.0,
                "review_fix_requests_sum": 0,
                "cache_weighted_sum": 2300.0,
                "cache_weight": 4000,
                "total_cache_hit_tokens": 1920,
                "total_reasoning_tokens": 10170,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            }
        }
        result = generate_agent_summary_table(agg)
        result_str = "\n".join(result)
        self.assertIn("Cache Hit Rate", result_str)
        self.assertIn("Cache Hit Tokens", result_str)
        self.assertIn("Reasoning Tokens", result_str)
        dev_line = [line for line in result if "Dev" in line][0]
        # 2300 / 4000 = 57.50%
        self.assertIn("| 57.50% ", dev_line)
        self.assertIn("| 1,920 ", dev_line)
        self.assertIn("| 10,170 ", dev_line)
        # Input cell is the bucket's uncached sum (4,000 − 1,920), not the raw 4,000 (#948).
        self.assertIn("| 2,080 ", dev_line)

    def test_cache_hit_shows_na_without_signal(self):
        """A bucket with no observed input tokens renders 'n/a'."""
        result_str = "\n".join(generate_agent_summary_table(self._make_aggregated()))
        self.assertIn("n/a", result_str)


class TestMergePerToolDict(unittest.TestCase):
    """Test _merge_per_tool_dict function."""

    def test_merge_simple_values(self):
        """Merge two dicts with simple numeric values."""
        base = {"terminal": 10, "file_editor": 5}
        override = {"terminal": 5, "bash": 3}
        result = _merge_per_tool_dict(base, override)
        self.assertEqual(result["terminal"], 15)
        self.assertEqual(result["file_editor"], 5)
        self.assertEqual(result["bash"], 3)

    def test_merge_nested_token_dicts(self):
        """Merge two dicts with nested token usage dicts."""
        base = {
            "terminal": {
                "input": 1000,
                "output": 500,
                "cache_hit": 200,
                "reasoning": 10,
            }
        }
        override = {
            "terminal": {
                "input": 500,
                "output": 250,
                "cache_hit": 100,
                "reasoning": 5,
            }
        }
        result = _merge_per_tool_dict(base, override)
        self.assertEqual(result["terminal"]["input"], 1500)
        self.assertEqual(result["terminal"]["output"], 750)
        self.assertEqual(result["terminal"]["cache_hit"], 300)
        self.assertEqual(result["terminal"]["reasoning"], 15)

    def test_merge_different_tools(self):
        """Merge dicts with non-overlapping tools."""
        base = {"terminal": 10}
        override = {"file_editor": 5}
        result = _merge_per_tool_dict(base, override)
        self.assertEqual(result["terminal"], 10)
        self.assertEqual(result["file_editor"], 5)

    def test_merge_empty_base(self):
        """Merge when base is empty."""
        base = {}
        override = {"terminal": 10}
        result = _merge_per_tool_dict(base, override)
        self.assertEqual(result, {"terminal": 10})

    def test_merge_empty_override(self):
        """Merge when override is empty."""
        base = {"terminal": 10}
        override = {}
        result = _merge_per_tool_dict(base, override)
        self.assertEqual(result, {"terminal": 10})

    def test_merge_none_base(self):
        """Merge when base is None."""
        base = None
        override = {"terminal": 10}
        result = _merge_per_tool_dict(base, override)
        self.assertEqual(result, {"terminal": 10})

    def test_merge_none_override(self):
        """Merge when override is None."""
        base = {"terminal": 10}
        override = None
        result = _merge_per_tool_dict(base, override)
        self.assertEqual(result, {"terminal": 10})

    def test_merge_both_none(self):
        """Merge when both are None."""
        result = _merge_per_tool_dict(None, None)
        self.assertEqual(result, {})

    def test_merge_both_empty(self):
        """Merge when both are empty dicts."""
        result = _merge_per_tool_dict({}, {})
        self.assertEqual(result, {})

    def test_merge_generic_dict_values(self):
        """Merge dicts with generic (non-token) nested dicts."""
        base = {"terminal": {"calls": 10, "errors": 2}}
        override = {"terminal": {"calls": 5, "errors": 1}}
        result = _merge_per_tool_dict(base, override)
        # Should sum numeric values in generic dicts
        self.assertEqual(result["terminal"]["calls"], 15)
        self.assertEqual(result["terminal"]["errors"], 3)


class TestFormatToolDict(unittest.TestCase):
    """Test _format_tool_dict function."""

    def test_format_non_empty_dict(self):
        """Format a non-empty dict."""
        tool_dict = {"terminal": 15, "file_editor": 8}
        result = _format_tool_dict(tool_dict)
        # Should be sorted alphabetically
        self.assertEqual(result, "file_editor: 8, terminal: 15")

    def test_format_empty_dict(self):
        """Format an empty dict."""
        result = _format_tool_dict({})
        self.assertEqual(result, "N/A")

    def test_format_none(self):
        """Format None."""
        result = _format_tool_dict(None)
        self.assertEqual(result, "N/A")

    def test_format_single_tool(self):
        """Format a dict with a single tool."""
        result = _format_tool_dict({"terminal": 10})
        self.assertEqual(result, "terminal: 10")

    def test_format_sorted_order(self):
        """Verify alphabetical sorting."""
        tool_dict = {"z_tool": 1, "a_tool": 2, "m_tool": 3}
        result = _format_tool_dict(tool_dict)
        self.assertEqual(result, "a_tool: 2, m_tool: 3, z_tool: 1")


class TestPerToolAggregationAgent(unittest.TestCase):
    """Test per-tool aggregation in aggregate_by_agent."""

    def _make_metric(self, agent, tool_calls_per_tool=None, errors_per_tool=None,
                     tokens_per_tool=None, tool_time_per_tool=None,
                     tool_runtime_ms=None, wall_clock_ms=None):
        metric = {
            "agent_role": agent,
            "input_tokens": 100,
            "output_tokens": 50,
            "tool_call_latency_ms": 1000.0,
            "nb_tool_calls": 5,
            "tool_call_error_rate": 0.0,
        }
        if tool_calls_per_tool is not None:
            metric["tool_calls_per_tool"] = tool_calls_per_tool
        if errors_per_tool is not None:
            metric["errors_per_tool"] = errors_per_tool
        if tokens_per_tool is not None:
            metric["tokens_per_tool"] = tokens_per_tool
        if tool_time_per_tool is not None:
            metric["tool_time_per_tool"] = tool_time_per_tool
        if tool_runtime_ms is not None:
            metric["tool_runtime_ms"] = tool_runtime_ms
        if wall_clock_ms is not None:
            metric["wall_clock_ms"] = wall_clock_ms
        return metric

    def test_merge_per_tool_across_runs(self):
        """Per-tool calls should be summed across runs for same agent."""
        metrics = [
            self._make_metric("architect", tool_calls_per_tool={"terminal": 10}),
            self._make_metric("architect", tool_calls_per_tool={"terminal": 5, "bash": 3}),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["tool_calls_per_tool"]["terminal"], 15)
        self.assertEqual(result["architect"]["tool_calls_per_tool"]["bash"], 3)

    def test_merge_errors_per_tool_across_runs(self):
        """Errors per tool should be summed across runs."""
        metrics = [
            self._make_metric("architect", errors_per_tool={"terminal": 2}),
            self._make_metric("architect", errors_per_tool={"terminal": 1, "file_editor": 0}),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["errors_per_tool"]["terminal"], 3)
        self.assertEqual(result["architect"]["errors_per_tool"]["file_editor"], 0)

    def test_merge_tokens_per_tool_across_runs(self):
        """Tokens per tool should be summed across runs."""
        metrics = [
            self._make_metric("architect", tokens_per_tool={
                "terminal": {"input": 1000, "output": 500, "cache_hit": 200, "reasoning": 10}
            }),
            self._make_metric("architect", tokens_per_tool={
                "terminal": {"input": 500, "output": 250, "cache_hit": 100, "reasoning": 5}
            }),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["tokens_per_tool"]["terminal"]["input"], 1500)
        self.assertEqual(result["architect"]["tokens_per_tool"]["terminal"]["output"], 750)
        self.assertEqual(result["architect"]["tokens_per_tool"]["terminal"]["cache_hit"], 300)
        self.assertEqual(result["architect"]["tokens_per_tool"]["terminal"]["reasoning"], 15)

    def test_backward_compatible_without_per_tool(self):
        """Old metrics without per-tool data should still work."""
        metrics = [
            self._make_metric("architect"),  # no per-tool fields
            self._make_metric("architect"),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["architect"]["tool_calls_per_tool"], {})
        self.assertEqual(result["architect"]["errors_per_tool"], {})
        self.assertEqual(result["architect"]["tokens_per_tool"], {})
        self.assertEqual(result["architect"]["tool_time_per_tool"], {})
        self.assertEqual(result["architect"]["tool_runtime_sum"], 0.0)
        self.assertEqual(result["architect"]["wall_clock_sum"], 0.0)

    def test_merge_tool_time_per_tool_across_runs(self):
        """Tool time buckets sum total_ms and count; the mean stays derived."""
        metrics = [
            self._make_metric("dev", tool_time_per_tool={
                "terminal": {"total_ms": 1000.0, "count": 4}
            }),
            self._make_metric("dev", tool_time_per_tool={
                "terminal": {"total_ms": 500.0, "count": 1},
                "bash": {"total_ms": 300.0, "count": 2},
            }),
        ]
        result = aggregate_by_agent(metrics)
        bucket = result["dev"]["tool_time_per_tool"]["terminal"]
        self.assertEqual(bucket["total_ms"], 1500.0)
        self.assertEqual(bucket["count"], 5)
        self.assertEqual(bucket["total_ms"] / bucket["count"], 300.0)
        self.assertEqual(result["dev"]["tool_time_per_tool"]["bash"],
                         {"total_ms": 300.0, "count": 2})

    def test_merge_does_not_mutate_source_metrics(self):
        """Merging tool-time buckets must not alias the source dicts."""
        source = {"terminal": {"total_ms": 100.0, "count": 1}}
        metrics = [
            self._make_metric("dev", tool_time_per_tool=source),
            self._make_metric("dev", tool_time_per_tool={"terminal": {"total_ms": 100.0, "count": 1}}),
        ]
        aggregate_by_agent(metrics)
        self.assertEqual(source, {"terminal": {"total_ms": 100.0, "count": 1}})

    def test_sums_tool_runtime_and_wall_clock(self):
        metrics = [
            self._make_metric("dev", tool_runtime_ms=1200.0, wall_clock_ms=60000.0),
            self._make_metric("dev", tool_runtime_ms=800.0, wall_clock_ms=30000.0),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["dev"]["tool_runtime_sum"], 2000.0)
        self.assertEqual(result["dev"]["wall_clock_sum"], 90000.0)

    def test_reads_tool_time_from_metrics_objects(self):
        metrics = [
            Metrics(
                agent_role="dev",
                tool_time_per_tool={"terminal": {"total_ms": 250.0, "count": 2}},
                tool_runtime_ms=250.0,
                wall_clock_ms=1000.0,
            ),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["dev"]["tool_time_per_tool"]["terminal"],
                         {"total_ms": 250.0, "count": 2})
        self.assertEqual(result["dev"]["tool_runtime_sum"], 250.0)
        self.assertEqual(result["dev"]["wall_clock_sum"], 1000.0)


class TestPerToolAggregationModel(unittest.TestCase):
    """Test per-tool aggregation in aggregate_by_model."""

    def _make_metric(self, model, tool_calls_per_tool=None, errors_per_tool=None,
                     tokens_per_tool=None):
        metric = {
            "model_name": model,
            "input_tokens": 100,
            "output_tokens": 50,
            "tool_call_latency_ms": 1000.0,
            "nb_tool_calls": 5,
            "tool_call_error_rate": 0.0,
        }
        if tool_calls_per_tool is not None:
            metric["tool_calls_per_tool"] = tool_calls_per_tool
        if errors_per_tool is not None:
            metric["errors_per_tool"] = errors_per_tool
        if tokens_per_tool is not None:
            metric["tokens_per_tool"] = tokens_per_tool
        return metric

    def test_merge_per_tool_across_runs(self):
        """Per-tool calls should be summed across runs for same model."""
        metrics = [
            self._make_metric("gpt-4", tool_calls_per_tool={"terminal": 10}),
            self._make_metric("gpt-4", tool_calls_per_tool={"terminal": 5, "bash": 3}),
        ]
        result = aggregate_by_model(metrics)
        self.assertEqual(result["gpt-4"]["tool_calls_per_tool"]["terminal"], 15)
        self.assertEqual(result["gpt-4"]["tool_calls_per_tool"]["bash"], 3)

    def test_merge_errors_per_tool_across_runs(self):
        """Errors per tool should be summed across runs."""
        metrics = [
            self._make_metric("gpt-4", errors_per_tool={"terminal": 2}),
            self._make_metric("gpt-4", errors_per_tool={"terminal": 1, "file_editor": 0}),
        ]
        result = aggregate_by_model(metrics)
        self.assertEqual(result["gpt-4"]["errors_per_tool"]["terminal"], 3)
        self.assertEqual(result["gpt-4"]["errors_per_tool"]["file_editor"], 0)


class TestTableGenerationWithPerTool(unittest.TestCase):
    """Test table generation with per-tool data."""

    def _make_aggregated_with_per_tool(self):
        return {
            "architect": {
                "runs": 2,
                "total_input_tokens": 300,
                "total_uncached_input_tokens": 300,
                "total_output_tokens": 150,
                "total_context_window": 16384,
                "latency_sum": 2000.0,
                "llm_runtime_sum": 10000.0,
                "tool_calls_sum": 10,
                "error_rate_sum": 0.0,
                "review_fix_requests_sum": 0,
                "tool_calls_per_tool": {"terminal": 15, "file_editor": 8},
                "errors_per_tool": {"terminal": 2, "file_editor": 0},
                "tokens_per_tool": {},
            },
            "context": {
                "runs": 1,
                "total_input_tokens": 200,
                "total_uncached_input_tokens": 200,
                "total_output_tokens": 100,
                "total_context_window": 8192,
                "latency_sum": 1500.0,
                "llm_runtime_sum": 5000.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 5.0,
                "review_fix_requests_sum": 0,
                "tool_calls_per_tool": {"bash": 5},
                "errors_per_tool": {"bash": 1},
                "tokens_per_tool": {},
            },
        }

    def test_agent_table_has_new_columns(self):
        """Agent table should include Tool Errors and Per-Tool Calls columns."""
        result = generate_agent_summary_table(self._make_aggregated_with_per_tool())
        result_str = "\n".join(result)
        self.assertIn("Tool Errors", result_str)
        self.assertIn("Per-Tool Calls", result_str)

    def test_agent_table_shows_tool_errors(self):
        """Agent table should show correct tool error counts."""
        result = generate_agent_summary_table(self._make_aggregated_with_per_tool())
        architect_line = [l for l in result if "Architect" in l][0]
        # architect has 2 terminal errors + 0 file_editor errors = 2 total
        self.assertIn("| 2 ", architect_line)

    def test_agent_table_shows_per_tool_calls(self):
        """Agent table should show formatted per-tool calls."""
        result = generate_agent_summary_table(self._make_aggregated_with_per_tool())
        architect_line = [l for l in result if "Architect" in l][0]
        self.assertIn("file_editor: 8, terminal: 15", architect_line)

    def test_agent_table_backward_compatible(self):
        """Agent table should show N/A for agents without per-tool data."""
        agg = {
            "architect": {
                "runs": 1,
                "total_input_tokens": 100,
                "total_uncached_input_tokens": 100,
                "total_output_tokens": 50,
                "total_context_window": 8192,
                "latency_sum": 1000.0,
                "llm_runtime_sum": 5000.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 0.0,
                "review_fix_requests_sum": 0,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            }
        }
        result = generate_agent_summary_table(agg)
        result_str = "\n".join(result)
        self.assertIn("N/A", result_str)

    def test_agent_table_columns_align(self):
        """Header, separator, rows and the totals row keep one column count (#829, #942)."""
        lines = generate_agent_summary_table(self._make_aggregated_with_per_tool())
        header_cols = lines[2].count("|")
        self.assertEqual(lines[3].count("|"), header_cols)
        arch_row = next(l for l in lines if l.startswith("| Architect "))
        self.assertEqual(arch_row.count("|"), header_cols)
        self.assertEqual(lines[-1].count("|"), header_cols)


class TestInputTokenSplit(unittest.TestCase):
    """Test the inclusive/exclusive input-token derivation (issue #948)."""

    def test_inclusive_shape_subtracts_cache_reads(self):
        """litellm/OpenAI shape: cache reads sit inside input_tokens."""
        split = InputTokenSplit(input_tokens=4000, cache_read_tokens=1920)
        self.assertEqual(split.uncached_input_tokens, 2080)

    def test_all_cached_leaves_zero(self):
        split = InputTokenSplit(input_tokens=500, cache_read_tokens=500)
        self.assertEqual(split.uncached_input_tokens, 0)

    def test_cache_writes_are_subtracted_too(self):
        split = InputTokenSplit(
            input_tokens=1000, cache_read_tokens=200, cache_write_tokens=300
        )
        self.assertEqual(split.uncached_input_tokens, 500)

    def test_exclusive_shape_keeps_input_unchanged(self):
        """Anthropic shape: cache_read > input means input already excludes cache."""
        split = InputTokenSplit(input_tokens=100, cache_read_tokens=9000)
        self.assertEqual(split.uncached_input_tokens, 100)

    def test_input_without_cache_is_unchanged(self):
        self.assertEqual(InputTokenSplit(input_tokens=700).uncached_input_tokens, 700)

    def test_empty_defaults_to_zero(self):
        self.assertEqual(InputTokenSplit().uncached_input_tokens, 0)
        self.assertEqual(InputTokenSplit(cache_read_tokens=9000).uncached_input_tokens, 0)


class TestStatsTotals(unittest.TestCase):
    """Test the StatsTotals summary-row value object (issue #942)."""

    def _make_aggregated(self):
        return {
            "architect": {
                "runs": 2,
                "total_input_tokens": 300,
                "total_uncached_input_tokens": 300,
                "total_output_tokens": 150,
                "total_cost": 0.1000,
            },
            "dev": {
                "runs": 1,
                "total_input_tokens": 600,
                "total_uncached_input_tokens": 600,
                "total_output_tokens": 100,
                "total_cost": 0.0234,
            },
        }

    def test_from_aggregated_sums_every_bucket(self):
        totals = StatsTotals.from_aggregated(self._make_aggregated())
        self.assertEqual(totals.runs, 3)
        self.assertEqual(totals.input_tokens, 900)
        self.assertEqual(totals.output_tokens, 250)
        self.assertAlmostEqual(totals.cost_usd, 0.1234)

    def test_from_aggregated_empty_is_zeroed(self):
        self.assertEqual(StatsTotals.from_aggregated({}), StatsTotals())

    def test_from_aggregated_tolerates_missing_cost(self):
        totals = StatsTotals.from_aggregated(
            {"architect": {"runs": 1, "total_input_tokens": 10, "total_output_tokens": 5}}
        )
        self.assertEqual(totals.cost_usd, 0.0)

    def test_render_row_shape(self):
        row = StatsTotals.from_aggregated(self._make_aggregated()).render_row()
        self.assertEqual(
            row,
            "| **Totals** | 3 | 900 | 250 | — | — | — | $0.1234 | — | — | — "
            "| — | — | — | — | — | — |",
        )
        self.assertTrue(row.endswith(" |"))
        self.assertEqual(row.count("|"), 18)

    def test_render_row_uses_shared_cell_formatters(self):
        """Totals cells call the same formatters as data rows (parity by construction)."""
        totals = StatsTotals.from_aggregated(self._make_aggregated())
        row = totals.render_row()
        self.assertIn(f"| {format_number(totals.input_tokens)} ", row)
        self.assertIn(f"| {format_number(totals.output_tokens)} ", row)
        self.assertIn(f"| {format_cost(totals.cost_usd)} ", row)

    def test_render_row_matches_agent_table_layout(self):
        """The row keeps the agent table's 17-cell layout and ends the table."""
        aggregated = {
            "architect": {
                "runs": 2,
                "total_input_tokens": 300,
                "total_uncached_input_tokens": 300,
                "total_output_tokens": 150,
                "total_context_window": 16384,
                "latency_sum": 2000.0,
                "llm_runtime_sum": 10000.0,
                "tool_calls_sum": 10,
                "error_rate_sum": 0.0,
                "review_fix_requests_sum": 0,
                "total_cost": 0.1000,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            }
        }
        lines = generate_agent_summary_table(aggregated)
        self.assertEqual(lines[-1], StatsTotals.from_aggregated(aggregated).render_row())


class TestSecondsCell(unittest.TestCase):
    """Test the SecondsCell duration-cell value object (issue #947)."""

    def test_from_average_divides_by_runs(self):
        self.assertEqual(SecondsCell.from_average(2000.0, 2), SecondsCell(1000.0))

    def test_from_average_with_no_runs_is_zero(self):
        self.assertEqual(SecondsCell.from_average(5000.0, 0), SecondsCell(0.0))

    def test_render_is_plain_seconds_without_decimals(self):
        self.assertEqual(SecondsCell(0.0).render(), "0")
        self.assertEqual(SecondsCell(1000.0).render(), "1")
        self.assertEqual(SecondsCell(28823.0).render(), "29")
        self.assertEqual(SecondsCell(687853.0).render(), "688")

    def test_from_average_render_matches_table_cell(self):
        self.assertEqual(SecondsCell.from_average(2000.0, 2).render(), "1")


class TestParseHelpers(unittest.TestCase):
    """Parse GitHub timestamps and agent-role markers."""

    def test_parse_iso_datetime_handles_z_suffix(self):
        parsed = parse_iso_datetime("2026-09-01T10:00:00Z")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.year, 2026)

    def test_parse_iso_datetime_returns_none_for_missing(self):
        self.assertIsNone(parse_iso_datetime(None))
        self.assertIsNone(parse_iso_datetime("not-a-date"))

    def test_parse_agent_role_from_marker(self):
        self.assertEqual(parse_agent_role("<!-- bot-agent-dev -->\nhi"), "dev")
        self.assertIsNone(parse_agent_role("just a human comment"))


class TestBuildCommentRecord(unittest.TestCase):
    """Normalisation of GitHub comments into CommentRecords."""

    def _comment(self, body, login="alice", created_at="2026-09-01T10:00:00Z"):
        return {"body": body, "user": {"login": login}, "created_at": created_at}

    def test_human_comment_is_not_bot(self):
        record = build_comment_record(self._comment("please continue"))
        self.assertFalse(record.author_is_bot)
        self.assertEqual(record.author_login, "alice")
        self.assertFalse(record.is_human_command)

    def test_bot_marker_marks_comment_as_bot(self):
        record = build_comment_record(
            self._comment("<!-- bot-workflow-terminal -->\nagent output", login="alice")
        )
        self.assertTrue(record.author_is_bot)

    def test_human_command_detected(self):
        record = build_comment_record(self._comment("@@dev please fix"))
        self.assertTrue(record.is_human_command)

    def test_missing_created_at_returns_none(self):
        self.assertIsNone(build_comment_record(self._comment("hi", created_at=None)))


class TestCollectIssueData(unittest.TestCase):
    """Aggregate comments and metrics into a pure IssueData input."""

    def _comments(self):
        return [
            {"body": "spec go ahead", "user": {"login": "alice"},
             "created_at": "2026-09-01T10:00:00Z"},
            {"body": "<!-- bot-workflow-terminal -->\n<!-- bot-agent-dev -->\ndone",
             "user": {"login": "alice"}, "created_at": "2026-09-01T10:01:00Z"},
            {"body": "please fix this", "user": {"login": "alice"},
             "created_at": "2026-09-01T10:02:00Z"},
        ]

    def test_records_comments_and_metrics(self):
        metrics = [
            Metrics(agent_role="dev", nb_tool_calls=4, agent_runtime_ms=1000.0,
                    start_time="2026-09-01T10:01:00Z"),
            Metrics(agent_role="review", nb_tool_calls=3, agent_runtime_ms=2000.0,
                    start_time="2026-09-01T10:05:00Z"),
        ]
        data = collect_issue_data(
            self._comments(), metrics, "issue body",
            parse_iso_datetime("2026-09-01T11:00:00Z"),
        )
        self.assertEqual(len(data.comments), 3)
        self.assertEqual(data.agent_tool_invocations, 7)
        self.assertAlmostEqual(data.agent_time_spent_seconds, 3.0)
        # earliest agent start wins
        self.assertEqual(data.first_agent_start,
                         parse_iso_datetime("2026-09-01T10:01:00Z"))

    def test_no_metrics_leaves_counts_unavailable(self):
        data = collect_issue_data(self._comments(), [], "body", None)
        self.assertIsNone(data.agent_tool_invocations)
        self.assertIsNone(data.agent_time_spent_seconds)
        self.assertIsNone(data.first_agent_start)


class TestHumanMetricsHardcodedConfig(unittest.TestCase):
    """Config is hardcoded (200 wpm, empty allowlist); no workflow env is wired."""

    def test_workflow_env_vars_are_ignored(self):
        env = {
            "HUMAN_METRICS_READING_WPM": "250",
            "HUMAN_METRICS_HUMAN_LOGINS": "alice",
        }
        with patch.dict(os.environ, env, clear=True):
            section = build_human_metrics_section([], [], "body", None)
        self.assertIn("reading speed: 200 wpm", section)


class TestBuildHumanMetricsSection(unittest.TestCase):
    """End-to-end rendering of the human-metrics section."""

    def test_section_maps_scenarios(self):
        comments = [
            {"body": "spec ready", "user": {"login": "alice"},
             "created_at": "2026-09-01T10:00:00Z"},
            {"body": "<!-- bot-workflow-terminal -->\n<!-- bot-agent-dev -->\nimpl",
             "user": {"login": "alice"}, "created_at": "2026-09-01T10:01:00Z"},
            {"body": "please change direction", "user": {"login": "alice"},
             "created_at": "2026-09-01T10:02:00Z"},
        ]
        metrics = [Metrics(agent_role="dev", nb_tool_calls=5, agent_runtime_ms=60000.0,
                           start_time="2026-09-01T10:01:00Z")]
        section = build_human_metrics_section(
            comments, metrics, "issue body",
            parse_iso_datetime("2026-09-01T10:30:00Z"),
        )
        self.assertIn("Human metrics", section)
        self.assertIn("Human comments", section)
        self.assertIn("| 2 |", section)  # two human comments
        # one human intervention after the implementation turn
        self.assertIn("| 1 |", section)

    def test_reading_time_excludes_context_agent_reports(self):
        body = " ".join(["word"] * 200)  # 200 body words -> 1.0 min @ 200 wpm
        context_report = (
            "<!-- bot-workflow-terminal -->\n<!-- bot-agent-context -->\n"
            + " ".join(["context"] * 400)  # excluded, else it would add 2.0 min
        )
        comments = [
            {"body": context_report, "user": {"login": "alice"},
             "created_at": "2026-09-01T10:01:00Z"},
        ]
        section = build_human_metrics_section(comments, [], body, None)
        self.assertIn("| Estimated human reading time | 1.0 min |", section)


class TestReportTimePricing(unittest.TestCase):
    """Report-time fallback for zero-cost entries (issue #928)."""

    @staticmethod
    def _resolver():
        table = PriceTable(
            source="openrouter",
            fetched_at=1.0,
            entries={
                "deepseek/deepseek-v4.1-flash": ModelPrice(
                    input_price=0.30, output_price=1.20,
                )
            },
        )
        return ReportCostResolver(table=table)

    def test_zero_cost_entries_are_priced_and_totaled(self):
        metrics = [
            Metrics(model_name="openai/deepseek/deepseek-v4.1-flash",
                    agent_role="dev", input_tokens=1_000_000, output_tokens=500_000),
            Metrics(model_name="openai/deepseek/deepseek-v4.1-flash",
                    agent_role="dev", input_tokens=1_000_000, output_tokens=0),
        ]
        by_model = aggregate_by_model(metrics, self._resolver())[
            "openai/deepseek/deepseek-v4.1-flash"
        ]
        # (0.30 + 0.60) + 0.30 = 1.20
        self.assertAlmostEqual(by_model["total_cost"], 1.20)
        by_agent = aggregate_by_agent(metrics, self._resolver())["dev"]
        self.assertAlmostEqual(by_agent["total_cost"], 1.20)

    def test_placeholder_entries_collapse_into_one_row(self):
        # Legacy "default" entries share one placeholder bucket; their identity
        # is unresolved (pre-#928 runs) so the bucket sums to zero, while new
        # runs carry a real model name and price per model.
        metrics = [
            Metrics(model_name="default", agent_role="dev",
                    input_tokens=1_000_000, output_tokens=0),
            Metrics(model_name="default", agent_role="review",
                    input_tokens=4_000_000, output_tokens=1_000_000),
        ]
        by_model = aggregate_by_model(metrics, self._resolver())
        self.assertEqual(list(by_model), ["default"])
        self.assertEqual(by_model["default"]["runs"], 2)
        self.assertAlmostEqual(by_model["default"]["total_cost"], 0.0)

    def test_reported_cost_is_not_recomputed(self):
        metrics = [Metrics(model_name="openai/deepseek/deepseek-v4.1-flash",
                           agent_role="dev", cost=2.5)]
        by_agent = aggregate_by_agent(metrics, self._resolver())["dev"]
        self.assertAlmostEqual(by_agent["total_cost"], 2.5)

    def test_resolver_none_preserves_todays_behaviour(self):
        metrics = [Metrics(model_name="openai/deepseek/deepseek-v4.1-flash",
                           agent_role="dev", input_tokens=1_000_000)]
        by_model = aggregate_by_model(metrics)
        self.assertAlmostEqual(by_model["openai/deepseek/deepseek-v4.1-flash"]["total_cost"], 0.0)

    def test_legacy_dict_entries_are_priced(self):
        metrics = [{"model_name": "openai/deepseek/deepseek-v4.1-flash",
                    "agent_role": "dev", "input_tokens": 1_000_000, "cost": 0.0}]
        by_model = aggregate_by_model(metrics, self._resolver())
        self.assertAlmostEqual(
            by_model["openai/deepseek/deepseek-v4.1-flash"]["total_cost"], 0.30
        )


if __name__ == "__main__":
    unittest.main()
