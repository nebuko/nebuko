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
    generate_summary_table,
    format_number,
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
                     latency=1000.0, tool_calls=5, error_rate=0.0):
        return {
            "model_name": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_call_latency_ms": latency,
            "nb_tool_calls": tool_calls,
            "tool_call_error_rate": error_rate,
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
            },
            "claude": {
                "runs": 1,
                "total_input_tokens": 200,
                "total_output_tokens": 100,
                "latency_sum": 1500.0,
                "tool_calls_sum": 5,
                "error_rate_sum": 5.0,
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


if __name__ == "__main__":
    unittest.main()
