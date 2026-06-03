#!/usr/bin/env python3
"""Tests for summarize_metrics.py"""

import json
import re
import sys
import os
import unittest
from unittest.mock import patch, MagicMock
from io import StringIO

# Import the module under test
sys.path.insert(0, os.path.dirname(__file__))
from summarize_metrics import (
    METRICS_REGEX,
    extract_metrics_from_comment,
    aggregate_by_model,
    aggregate_by_agent,
    generate_summary_table,
    generate_agent_summary_table,
    generate_model_summary_table,
    format_number,
    get_agent_display_name,
    AGENT_DISPLAY_NAMES,
    _merge_per_tool_dict,
    _format_tool_dict,
)


class TestMetricsRegex(unittest.TestCase):
    """Test the metrics regex pattern."""

    def test_extract_single_metrics(self):
        body = "Some text\n<!-- agent-metrics: {\"model_name\":\"gpt-4\",\"input_tokens\":100} -->\nMore text"
        matches = METRICS_REGEX.findall(body)
        self.assertEqual(len(matches), 1)
        data = json.loads(matches[0])
        self.assertEqual(data["model_name"], "gpt-4")
        self.assertEqual(data["input_tokens"], 100)

    def test_extract_multiple_metrics(self):
        body = (
            "<!-- agent-metrics: {\"model_name\":\"gpt-4\",\"input_tokens\":100} -->\n"
            "Some text\n"
            "<!-- agent-metrics: {\"model_name\":\"claude\",\"input_tokens\":200} -->"
        )
        matches = METRICS_REGEX.findall(body)
        self.assertEqual(len(matches), 2)

    def test_no_metrics(self):
        body = "This comment has no metrics."
        matches = METRICS_REGEX.findall(body)
        self.assertEqual(len(matches), 0)

    def test_bot_marker_without_metrics(self):
        body = "<!-- bot-agent-genius -->\nSome answer"
        matches = METRICS_REGEX.findall(body)
        self.assertEqual(len(matches), 0)

    def test_metrics_with_extra_spaces(self):
        body = "<!--  agent-metrics:  {\"model_name\":\"test\"}  -->"
        matches = METRICS_REGEX.findall(body)
        self.assertEqual(len(matches), 1)


class TestExtractMetricsFromComment(unittest.TestCase):
    """Test extract_metrics_from_comment function."""

    def test_extract_valid_metrics(self):
        body = "<!-- agent-metrics: {\"model_name\":\"gpt-4\",\"input_tokens\":100,\"output_tokens\":50} -->"
        result = extract_metrics_from_comment(body)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model_name"], "gpt-4")
        self.assertEqual(result[0]["input_tokens"], 100)

    def test_extract_multiple_from_one_comment(self):
        body = (
            "<!-- agent-metrics: {\"model_name\":\"gpt-4\",\"input_tokens\":100} -->\n"
            "<!-- agent-metrics: {\"model_name\":\"claude\",\"input_tokens\":200} -->"
        )
        result = extract_metrics_from_comment(body)
        self.assertEqual(len(result), 2)

    def test_malformed_json_skipped(self):
        body = "<!-- agent-metrics: {invalid json} -->"
        result = extract_metrics_from_comment(body)
        self.assertEqual(len(result), 0)

    def test_empty_comment(self):
        result = extract_metrics_from_comment("")
        self.assertEqual(len(result), 0)


class TestAggregateByModel(unittest.TestCase):
    """Test aggregate_by_model function."""

    def _make_metric(self, model, input_tokens=100, output_tokens=50,
                     latency=1000.0, tool_calls=5, error_rate=0.0,
                     review_fix_requests=0):
        return {
            "model_name": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_call_latency_ms": latency,
            "nb_tool_calls": tool_calls,
            "tool_call_error_rate": error_rate,
            "review_fix_requests": review_fix_requests,
        }

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
        metrics = [{"input_tokens": 100}]  # no model_name
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


class TestAggregateByAgent(unittest.TestCase):
    """Test aggregate_by_agent function."""

    def _make_metric(self, agent, input_tokens=100, output_tokens=50,
                     latency=1000.0, tool_calls=5, error_rate=0.0,
                     review_fix_requests=0):
        return {
            "agent_role": agent,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_call_latency_ms": latency,
            "nb_tool_calls": tool_calls,
            "tool_call_error_rate": error_rate,
            "review_fix_requests": review_fix_requests,
        }

    def test_single_agent_single_run(self):
        metrics = [self._make_metric("genius")]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["runs"], 1)
        self.assertEqual(result["genius"]["total_input_tokens"], 100)

    def test_single_agent_multiple_runs(self):
        metrics = [
            self._make_metric("genius", input_tokens=100, output_tokens=50),
            self._make_metric("genius", input_tokens=200, output_tokens=100),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["runs"], 2)
        self.assertEqual(result["genius"]["total_input_tokens"], 300)

    def test_multiple_agents(self):
        metrics = [
            self._make_metric("genius", input_tokens=100),
            self._make_metric("dev", input_tokens=200),
            self._make_metric("genius", input_tokens=150),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["runs"], 2)
        self.assertEqual(result["genius"]["total_input_tokens"], 250)
        self.assertEqual(result["dev"]["runs"], 1)
        self.assertEqual(result["dev"]["total_input_tokens"], 200)

    def test_review_fix_requests_aggregation(self):
        """Review fix requests should be summed across runs."""
        metrics = [
            self._make_metric("genius", review_fix_requests=2),
            self._make_metric("genius", review_fix_requests=3),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["review_fix_requests_sum"], 5)

    def test_review_fix_requests_dev_zero(self):
        """Dev agent should have 0 review fix requests."""
        metrics = [self._make_metric("dev", review_fix_requests=0)]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["dev"]["review_fix_requests_sum"], 0)

    def test_review_fix_requests_ctx_zero(self):
        """Ctx agent should have 0 review fix requests."""
        metrics = [self._make_metric("ctx", review_fix_requests=0)]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["ctx"]["review_fix_requests_sum"], 0)

    def test_review_fix_requests_default_zero(self):
        """Review fix requests should default to 0 when not present."""
        metrics = [self._make_metric("genius")]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["review_fix_requests_sum"], 0)


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


class TestGenerateSummaryTable(unittest.TestCase):
    """Test generate_summary_table function."""

    def _make_aggregated(self):
        return {
            "gpt-4": {
                "runs": 2,
                "total_input_tokens": 300,
                "total_output_tokens": 150,
                "latency_sum": 2000.0,
                "tool_calls_sum": 10,
                "error_rate_sum": 0.0,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
            "claude": {
                "runs": 1,
                "total_input_tokens": 200,
                "total_output_tokens": 100,
                "latency_sum": 1500.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 5.0,
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
                "latency_sum": 1000.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 0.0,
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


class TestGetAgentDisplayName(unittest.TestCase):
    """Test get_agent_display_name function."""

    def test_genius(self):
        self.assertEqual(get_agent_display_name("genius"), "Genius")

    def test_ctx(self):
        self.assertEqual(get_agent_display_name("ctx"), "Ctx")

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
            "tool_call_latency_ms": latency,
            "nb_tool_calls": tool_calls,
            "tool_call_error_rate": error_rate,
        }

    def test_single_agent_single_run(self):
        metrics = [self._make_metric("genius")]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["runs"], 1)
        self.assertEqual(result["genius"]["total_input_tokens"], 100)
        self.assertEqual(result["genius"]["total_output_tokens"], 50)

    def test_single_agent_multiple_runs(self):
        metrics = [
            self._make_metric("genius", input_tokens=100, output_tokens=50),
            self._make_metric("genius", input_tokens=200, output_tokens=100),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["runs"], 2)
        self.assertEqual(result["genius"]["total_input_tokens"], 300)
        self.assertEqual(result["genius"]["total_output_tokens"], 150)

    def test_multiple_agents(self):
        metrics = [
            self._make_metric("genius", input_tokens=100),
            self._make_metric("ctx", input_tokens=200),
            self._make_metric("dev", input_tokens=150),
            self._make_metric("genius", input_tokens=50),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["runs"], 2)
        self.assertEqual(result["genius"]["total_input_tokens"], 150)
        self.assertEqual(result["ctx"]["runs"], 1)
        self.assertEqual(result["ctx"]["total_input_tokens"], 200)
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
            self._make_metric("genius", input_tokens=100),
            {"model_name": "gpt-4", "input_tokens": 200},  # no agent_role
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["runs"], 1)
        self.assertEqual(result["unknown"]["runs"], 1)


class TestGenerateAgentSummaryTable(unittest.TestCase):
    """Test generate_agent_summary_table function."""

    def _make_aggregated(self):
        return {
            "genius": {
                "runs": 2,
                "total_input_tokens": 300,
                "total_output_tokens": 150,
                "latency_sum": 2000.0,
                "tool_calls_sum": 10,
                "error_rate_sum": 0.0,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
            "ctx": {
                "runs": 1,
                "total_input_tokens": 200,
                "total_output_tokens": 100,
                "latency_sum": 1500.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 5.0,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
            "dev": {
                "runs": 1,
                "total_input_tokens": 400,
                "total_output_tokens": 200,
                "latency_sum": 3000.0,
                "tool_calls_sum": 8,
                "error_rate_sum": 2.0,
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
        self.assertIn("| Genius ", result_str)
        self.assertIn("| Ctx ", result_str)
        self.assertIn("| Dev ", result_str)

    def test_agent_sort_order(self):
        result = generate_agent_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        genius_pos = result_str.index("| Genius ")
        ctx_pos = result_str.index("| Ctx ")
        dev_pos = result_str.index("| Dev ")
        self.assertLess(genius_pos, ctx_pos)
        self.assertLess(ctx_pos, dev_pos)

    def test_totals_line(self):
        result = generate_agent_summary_table(self._make_aggregated())
        result_str = "\n".join(result)
        self.assertIn("Totals:", result_str)
        self.assertIn("4 runs", result_str)
        self.assertIn("900 input tokens", result_str)
        self.assertIn("450 output tokens", result_str)

    def test_single_run_singular(self):
        agg = {
            "genius": {
                "runs": 1,
                "total_input_tokens": 100,
                "total_output_tokens": 50,
                "latency_sum": 1000.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 0.0,
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
        # genius: 2000/2 = 1000ms avg
        genius_line = [l for l in result if "Genius" in l][0]
        self.assertIn("| 1000 ", genius_line)

    def test_average_tool_calls(self):
        result = generate_agent_summary_table(self._make_aggregated())
        genius_line = [l for l in result if "Genius" in l][0]
        self.assertIn("5.0", genius_line)

    def test_tool_errors_column(self):
        """Test that tool errors column shows correct values."""
        result = generate_agent_summary_table(self._make_aggregated())
        # All per-tool error dicts are empty, so all should show 0
        for line in result:
            if any(name in line for name in ["Genius", "Ctx", "Dev"]):
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
            if any(name in line for name in ["Genius", "Ctx", "Dev"]):
                self.assertIn("| 0 ", line)

    def test_fix_requests_column_with_values(self):
        """Test that Fix Requests column shows correct values."""
        agg = {
            "genius": {
                "runs": 2,
                "total_input_tokens": 300,
                "total_output_tokens": 150,
                "latency_sum": 2000.0,
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
                "latency_sum": 1500.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 0.0,
                "review_fix_requests_sum": 0,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
        }
        result = generate_agent_summary_table(agg)
        genius_line = [l for l in result if "Genius" in l][0]
        dev_line = [l for l in result if "Dev" in l][0]
        self.assertIn("| 5 ", genius_line)
        self.assertIn("| 0 ", dev_line)

    def test_backward_compatible_without_fix_requests(self):
        """Agent table should show 0 for agents without review_fix_requests_sum."""
        agg = {
            "genius": {
                "runs": 1,
                "total_input_tokens": 100,
                "total_output_tokens": 50,
                "latency_sum": 1000.0,
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


class TestGenerateModelSummaryTable(unittest.TestCase):
    """Test generate_model_summary_table function."""

    def _make_aggregated(self):
        return {
            "gpt-4": {
                "runs": 2,
                "total_input_tokens": 300,
                "total_output_tokens": 150,
                "latency_sum": 2000.0,
                "tool_calls_sum": 10,
                "error_rate_sum": 0.0,
                "tool_calls_per_tool": {},
                "errors_per_tool": {},
                "tokens_per_tool": {},
            },
            "claude": {
                "runs": 1,
                "total_input_tokens": 200,
                "total_output_tokens": 100,
                "latency_sum": 1500.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 5.0,
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
                "latency_sum": 2000.0,
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
                "latency_sum": 1500.0,
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
                "latency_sum": 1000.0,
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
            self._make_metric("genius", tool_calls_per_tool={"terminal": 10}),
            self._make_metric("genius", tool_calls_per_tool={"terminal": 5, "bash": 3}),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["tool_calls_per_tool"]["terminal"], 15)
        self.assertEqual(result["genius"]["tool_calls_per_tool"]["bash"], 3)

    def test_merge_errors_per_tool_across_runs(self):
        """Errors per tool should be summed across runs."""
        metrics = [
            self._make_metric("genius", errors_per_tool={"terminal": 2}),
            self._make_metric("genius", errors_per_tool={"terminal": 1, "file_editor": 0}),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["errors_per_tool"]["terminal"], 3)
        self.assertEqual(result["genius"]["errors_per_tool"]["file_editor"], 0)

    def test_merge_tokens_per_tool_across_runs(self):
        """Tokens per tool should be summed across runs."""
        metrics = [
            self._make_metric("genius", tokens_per_tool={
                "terminal": {"input": 1000, "output": 500, "cache_hit": 200, "reasoning": 10}
            }),
            self._make_metric("genius", tokens_per_tool={
                "terminal": {"input": 500, "output": 250, "cache_hit": 100, "reasoning": 5}
            }),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["tokens_per_tool"]["terminal"]["input"], 1500)
        self.assertEqual(result["genius"]["tokens_per_tool"]["terminal"]["output"], 750)
        self.assertEqual(result["genius"]["tokens_per_tool"]["terminal"]["cache_hit"], 300)
        self.assertEqual(result["genius"]["tokens_per_tool"]["terminal"]["reasoning"], 15)

    def test_backward_compatible_without_per_tool(self):
        """Old metrics without per-tool data should still work."""
        metrics = [
            self._make_metric("genius"),  # no per-tool fields
            self._make_metric("genius"),
        ]
        result = aggregate_by_agent(metrics)
        self.assertEqual(result["genius"]["tool_calls_per_tool"], {})
        self.assertEqual(result["genius"]["errors_per_tool"], {})
        self.assertEqual(result["genius"]["tokens_per_tool"], {})


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
            "genius": {
                "runs": 2,
                "total_input_tokens": 300,
                "total_output_tokens": 150,
                "latency_sum": 2000.0,
                "tool_calls_sum": 10,
                "error_rate_sum": 0.0,
                "tool_calls_per_tool": {"terminal": 15, "file_editor": 8},
                "errors_per_tool": {"terminal": 2, "file_editor": 0},
                "tokens_per_tool": {},
            },
            "ctx": {
                "runs": 1,
                "total_input_tokens": 200,
                "total_output_tokens": 100,
                "latency_sum": 1500.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 5.0,
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
        genius_line = [l for l in result if "Genius" in l][0]
        # genius has 2 terminal errors + 0 file_editor errors = 2 total
        self.assertIn("| 2 ", genius_line)

    def test_agent_table_shows_per_tool_calls(self):
        """Agent table should show formatted per-tool calls."""
        result = generate_agent_summary_table(self._make_aggregated_with_per_tool())
        genius_line = [l for l in result if "Genius" in l][0]
        self.assertIn("file_editor: 8, terminal: 15", genius_line)

    def test_agent_table_backward_compatible(self):
        """Agent table should show N/A for agents without per-tool data."""
        agg = {
            "genius": {
                "runs": 1,
                "total_input_tokens": 100,
                "total_output_tokens": 50,
                "latency_sum": 1000.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 0.0,
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
        # Both genius and ctx have errors
        for line in result:
            if "Genius" in line or "Ctx" in line:
                # Should contain error count
                self.assertTrue(any(c.isdigit() for c in line))


if __name__ == "__main__":
    unittest.main()
