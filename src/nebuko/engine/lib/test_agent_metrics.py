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
from agent_metrics import (
    extract_metrics_from_comment,
    deduplicate_metrics,
    aggregate_by_model,
    aggregate_by_agent,
    generate_summary_table,
    generate_agent_summary_table,
    generate_model_summary_table,
    format_number,
    format_cost,
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
        "metrics": {"run_id": run_id, **metrics_fields},
    }


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
            "max_context_window", "cost", "complexity", "tool_call_latency_ms", "agent_runtime_ms",
            "agent_runtimes_per_call_ms", "nb_tool_calls", "tool_call_error_rate",
            "start_time", "end_time", "tool_calls_per_tool", "errors_per_tool",
            "tokens_per_tool", "review_fix_requests", "dispatch_recall_count",
        }
        self.assertEqual(set(d.keys()), expected_keys)


class TestAggregateByModel(unittest.TestCase):
    """Test aggregate_by_model function."""

    def _make_metric(self, model, input_tokens=100, output_tokens=50,
                     latency=1000.0, tool_calls=5, error_rate=0.0,
                     review_fix_requests=0, max_context_window=8192,
                     cost=0.0):
        return Metrics(
            run_id="test-run",
            model_name=model,
            agent_role="architect",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            max_context_window=max_context_window,
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


class TestAggregateByAgent(unittest.TestCase):
    """Test aggregate_by_agent function."""

    def _make_metric(self, agent, input_tokens=100, output_tokens=50,
                     latency=1000.0, tool_calls=5, error_rate=0.0,
                     review_fix_requests=0, max_context_window=8192,
                     cost=0.0):
        return Metrics(
            run_id="test-run",
            model_name="gpt-4",
            agent_role=agent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            max_context_window=max_context_window,
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


class TestGenerateSummaryTable(unittest.TestCase):
    """Test generate_summary_table function."""

    def _make_aggregated(self):
        return {
            "gpt-4": {
                "runs": 2,
                "total_input_tokens": 300,
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
            "claude": {
                "runs": 1,
                "total_input_tokens": 200,
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
        }

    def test_no_metrics_message(self):
        result = generate_summary_table({})
        self.assertIn("No agent metrics", result)

    def test_table_header(self):
        result = generate_summary_table(self._make_aggregated())
        self.assertIn("| Model | Runs |", result)
        self.assertIn("## 📊 Agent Statistics Summary", result)

    def test_model_rows(self):
        result = generate_summary_table(self._make_aggregated())
        self.assertIn("| gpt-4 ", result)
        self.assertIn("| claude ", result)

    def test_totals_line(self):
        result = generate_summary_table(self._make_aggregated())
        self.assertIn("Totals:", result)
        self.assertIn("3 runs", result)
        self.assertIn("500 input tokens", result)
        self.assertIn("250 output tokens", result)

    def test_auto_generated_footer(self):
        result = generate_summary_table(self._make_aggregated())
        self.assertIn("auto-generated", result)
        self.assertIn("issue close", result)

    def test_single_run_uses_singular(self):
        agg = {
            "gpt-4": {
                "runs": 1,
                "total_input_tokens": 100,
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
        result = generate_summary_table(agg)
        self.assertIn("1 run", result)
        self.assertNotIn("1 runs", result)

    def test_average_latency(self):
        result = generate_summary_table(self._make_aggregated())
        # gpt-4: 2000/2 = 1000ms avg
        self.assertIn("| 1000 ", result)

    def test_average_tool_calls(self):
        result = generate_summary_table(self._make_aggregated())
        # gpt-4: 10/2 = 5.0 avg tool calls
        lines = result.split("\n")
        gpt_line = [l for l in lines if "gpt-4" in l][0]
        self.assertIn("5.0", gpt_line)

    def test_average_error_rate(self):
        result = generate_summary_table(self._make_aggregated())
        lines = result.split("\n")
        claude_line = [l for l in lines if "claude" in l][0]
        self.assertIn("5.0%", claude_line)

    def test_cost_column_shown(self):
        result = generate_summary_table(self._make_aggregated())
        self.assertIn("Cost (USD)", result)
        # Missing total_cost defaults to $0.0000
        gpt_line = [l for l in result.split("\n") if "gpt-4" in l][0]
        self.assertIn("$0.0000", gpt_line)

    def test_totals_includes_cost(self):
        agg = self._make_aggregated()
        agg["gpt-4"]["total_cost"] = 0.1234
        agg["claude"]["total_cost"] = 0.0500
        result = generate_summary_table(agg)
        self.assertIn("$0.1734", result)


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
                     latency=1000.0, tool_calls=5, error_rate=0.0):
        return {
            "model_name": "gpt-4",
            "agent_role": agent,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "max_context_window": 8192,
            "tool_call_latency_ms": latency,
            "nb_tool_calls": tool_calls,
            "tool_call_error_rate": error_rate,
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


class TestGenerateAgentSummaryTable(unittest.TestCase):
    """Test generate_agent_summary_table function."""

    def _make_aggregated(self):
        return {
            "architect": {
                "runs": 2,
                "total_input_tokens": 300,
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

    def test_totals_line(self):
        result = generate_agent_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        self.assertIn("Totals:", result_str)
        self.assertIn("4 runs", result_str)
        self.assertIn("900 input tokens", result_str)
        self.assertIn("450 output tokens", result_str)

    def test_single_run_singular(self):
        agg = {
            "architect": {
                "runs": 1,
                "total_input_tokens": 100,
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
        self.assertIn("1 run", result_str)
        self.assertNotIn("1 runs", result_str)

    def test_average_latency(self):
        result = generate_agent_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        # architect: 2000/2 = 1000ms avg
        architect_line = [l for l in result if "Architect" in l][0]
        self.assertIn("| 1000 ", architect_line)

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
        """Agent table should include a Cost column and sum costs in totals."""
        agg = {
            "architect": {
                "runs": 1,
                "total_input_tokens": 100,
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
        result_str = "\n".join(generate_agent_summary_table(agg))
        self.assertIn("Cost (USD)", result_str)
        self.assertIn("$0.1234", result_str)


class TestGenerateModelSummaryTable(unittest.TestCase):
    """Test generate_model_summary_table function."""

    def _make_aggregated(self):
        return {
            "gpt-4": {
                "runs": 2,
                "total_input_tokens": 300,
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
            "claude": {
                "runs": 1,
                "total_input_tokens": 200,
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
        }

    def test_empty_returns_empty(self):
        result = generate_model_summary_table({})
        self.assertEqual(result, "")

    def test_table_header(self):
        result = generate_model_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        self.assertIn("| Model | Runs |", result_str)
        self.assertIn("## 📊 Model Statistics Summary", result_str)

    def test_model_rows(self):
        result = generate_model_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        self.assertIn("| gpt-4 ", result_str)
        self.assertIn("| claude ", result_str)

    def test_tool_errors_column(self):
        """Test that tool errors column shows correct values."""
        result = generate_model_summary_table(self._make_aggregated())
        # All per-tool error dicts are empty, so all should show 0
        for line in result:
            if any(name in line for name in ["gpt-4", "claude"]):
                self.assertIn("| 0 ", line)

    def test_fix_requests_column_header(self):
        """Test that Fix Requests column header is present."""
        result = generate_model_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        self.assertIn("Fix Requests", result_str)

    def test_fix_requests_column_zero_by_default(self):
        """Test that Fix Requests column shows 0 when no fix requests."""
        result = generate_model_summary_table(self._make_aggregated())
        for line in result:
            if any(name in line for name in ["gpt-4", "claude"]):
                self.assertIn("| 0 ", line)

    def test_fix_requests_column_with_values(self):
        """Test that Fix Requests column shows correct values."""
        agg = {
            "gpt-4": {
                "runs": 2,
                "total_input_tokens": 300,
                "total_output_tokens": 150,
                "total_context_window": 16384,
                "latency_sum": 2000.0,
                "llm_runtime_sum": 10000.0,
                "tool_calls_sum": 10,
                "error_rate_sum": 0.0,
                "review_fix_requests_sum": 3,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
            "claude": {
                "runs": 1,
                "total_input_tokens": 200,
                "total_output_tokens": 100,
                "total_context_window": 8192,
                "latency_sum": 1500.0,
                "llm_runtime_sum": 5000.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 5.0,
                "review_fix_requests_sum": 2,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
        }
        result = generate_model_summary_table(agg)
        gpt4_line = [l for l in result if "gpt-4" in l][0]
        claude_line = [l for l in result if "claude" in l][0]
        self.assertIn("| 3 ", gpt4_line)
        self.assertIn("| 2 ", claude_line)

    def test_backward_compatible_without_fix_requests(self):
        """Model table should show 0 for models without review_fix_requests_sum."""
        agg = {
            "gpt-4": {
                "runs": 1,
                "total_input_tokens": 100,
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
        result = generate_model_summary_table(agg)
        result_str = "\n".join(result)
        self.assertIn("| 0 ", result_str)


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
                     tokens_per_tool=None):
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

    def test_model_table_has_new_columns(self):
        """Model table should include Tool Errors and Per-Tool Calls columns."""
        result = generate_model_summary_table(self._make_aggregated_with_per_tool())
        result_str = "\n".join(result)
        self.assertIn("Tool Errors", result_str)
        self.assertIn("Per-Tool Calls", result_str)

    def test_model_table_shows_tool_errors(self):
        """Model table should show correct tool error counts."""
        result = generate_model_summary_table(self._make_aggregated_with_per_tool())
        # Both architect and context have errors
        for line in result:
            if "| Architect " in line or "| Context " in line:
                # Should contain error count
                self.assertTrue(any(c.isdigit() for c in line))

    def test_model_table_cost_column(self):
        """Model table should include a Cost column populated from total_cost."""
        agg = self._make_aggregated_with_per_tool()
        agg["architect"]["total_cost"] = 0.2500
        result_str = "\n".join(generate_model_summary_table(agg))
        self.assertIn("Cost (USD)", result_str)
        self.assertIn("$0.2500", result_str)


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


if __name__ == "__main__":
    unittest.main()
