"""Tests for cache-token extraction in the LLM metrics collector (issue #651)."""

from types import SimpleNamespace

from agent_script.sub_domains.metrics.metrics_collector import (
    _compute_tokens_per_tool,
    _sum_cache_hit_tokens,
)
from metrics_schema import Metrics


def _usage(**kwargs):
    """Build a usage object with only the fields the collector reads."""
    return SimpleNamespace(**kwargs)


def _combined(usages):
    return SimpleNamespace(token_usages=usages)


class TestComputeTokensPerToolCacheHit:
    """The per-tool ``cache_hit`` value must come from the SDK cache field."""

    def test_reads_cache_read_tokens(self):
        usage = _usage(
            tool_name="terminal",
            prompt_tokens=1000,
            completion_tokens=100,
            cache_read_tokens=42,
            reasoning_tokens=0,
        )
        result = _compute_tokens_per_tool(_combined([usage]))
        assert result["terminal"]["cache_hit"] == 42

    def test_falls_back_to_legacy_cache_hit_tokens(self):
        usage = _usage(
            tool_name="terminal",
            prompt_tokens=1000,
            completion_tokens=100,
            cache_hit_tokens=17,
            reasoning_tokens=0,
        )
        result = _compute_tokens_per_tool(_combined([usage]))
        assert result["terminal"]["cache_hit"] == 17

    def test_prefers_cache_read_tokens_over_legacy(self):
        usage = _usage(
            tool_name="terminal",
            prompt_tokens=1000,
            completion_tokens=100,
            cache_read_tokens=42,
            cache_hit_tokens=17,
            reasoning_tokens=0,
        )
        result = _compute_tokens_per_tool(_combined([usage]))
        assert result["terminal"]["cache_hit"] == 42

    def test_zero_cache_read_tokens_falls_back_to_legacy(self):
        usage = _usage(
            tool_name="terminal",
            cache_read_tokens=0,
            cache_hit_tokens=9,
        )
        result = _compute_tokens_per_tool(_combined([usage]))
        assert result["terminal"]["cache_hit"] == 9

    def test_none_cache_read_tokens_yields_zero(self):
        usage = _usage(tool_name="terminal", cache_read_tokens=None)
        result = _compute_tokens_per_tool(_combined([usage]))
        assert result["terminal"]["cache_hit"] == 0

    def test_missing_cache_fields_yield_zero(self):
        usage = _usage(tool_name="terminal", prompt_tokens=5)
        result = _compute_tokens_per_tool(_combined([usage]))
        assert result["terminal"]["cache_hit"] == 0


class TestSumCacheHitTokens:
    """Run-level total is the sum of the per-tool ``cache_hit`` values."""

    def test_sums_across_tools(self):
        tokens_per_tool = {
            "terminal": {"cache_hit": 42},
            "file_editor": {"cache_hit": 7},
        }
        assert _sum_cache_hit_tokens(tokens_per_tool) == 49

    def test_empty_dict_is_zero(self):
        assert _sum_cache_hit_tokens({}) == 0

    def test_ignores_non_dict_values(self):
        assert _sum_cache_hit_tokens({"terminal": 5, "file_editor": {"cache_hit": 7}}) == 7

    def test_none_values_count_as_zero(self):
        assert _sum_cache_hit_tokens({"terminal": {"cache_hit": None}}) == 0


def test_run_level_cache_read_tokens_in_emitted_metrics():
    tokens_per_tool = {
        "terminal": {"input": 10, "output": 2, "cache_hit": 42, "reasoning": 0},
        "file_editor": {"input": 5, "output": 1, "cache_hit": 7, "reasoning": 0},
    }
    metrics = Metrics(
        run_id="run-1",
        cache_read_tokens=_sum_cache_hit_tokens(tokens_per_tool),
    )
    assert metrics.to_dict()["cache_read_tokens"] == 49
