"""Tests for the HistoryWindowConfig sub-domain (ADR-0003, issue #844)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import HistoryWindowConfig
from config.sub_domains.history_window.history_window import (
    DEFAULT_HISTORY_COMMENT_COUNT,
)


class TestHistoryWindowConfig:
    """The declared default, env resolution and normalization are the contract."""

    def test_defaults_to_five(self):
        assert HistoryWindowConfig().normalized_count() == 5
        assert DEFAULT_HISTORY_COMMENT_COUNT == 5

    def test_from_env_absent_uses_the_default(self):
        assert HistoryWindowConfig.from_env({}).normalized_count() == 5

    def test_from_env_reads_a_positive_count(self):
        config = HistoryWindowConfig.from_env({"HISTORY_COMMENT_COUNT": "2"})
        assert config.history_comment_count == 2
        assert config.normalized_count() == 2

    def test_from_env_tolerates_surrounding_whitespace(self):
        config = HistoryWindowConfig.from_env({"HISTORY_COMMENT_COUNT": " 7 "})
        assert config.normalized_count() == 7

    def test_from_env_unparseable_falls_back_to_the_default(self):
        for raw in ("abc", "", "  ", "2.5", "1e3"):
            config = HistoryWindowConfig.from_env({"HISTORY_COMMENT_COUNT": raw})
            assert config.normalized_count() == 5, raw

    def test_normalized_count_bounds_non_positive_values(self):
        assert HistoryWindowConfig(history_comment_count=0).normalized_count() == 5
        assert HistoryWindowConfig(history_comment_count=-3).normalized_count() == 5

    def test_normalized_count_keeps_a_positive_value(self):
        assert HistoryWindowConfig(history_comment_count=7).normalized_count() == 7

    def test_from_env_keeps_a_parseable_non_positive_value(self):
        config = HistoryWindowConfig.from_env({"HISTORY_COMMENT_COUNT": "0"})
        assert config.history_comment_count == 0
        assert config.normalized_count() == 5

    def test_render_limit_is_unbounded_when_unset(self):
        # Issue #872: the thread ledger renders the full relevant flow by default.
        assert HistoryWindowConfig().render_limit() is None
        assert HistoryWindowConfig.from_env({}).render_limit() is None

    def test_render_limit_is_unbounded_for_a_non_positive_value(self):
        assert HistoryWindowConfig(history_comment_count=0).render_limit() is None
        assert HistoryWindowConfig(history_comment_count=-3).render_limit() is None

    def test_render_limit_caps_a_positive_value(self):
        assert HistoryWindowConfig(history_comment_count=2).render_limit() == 2
        assert (
            HistoryWindowConfig.from_env({"HISTORY_COMMENT_COUNT": "3"}).render_limit()
            == 3
        )

    def test_is_immutable(self):
        config = HistoryWindowConfig()
        try:
            config.history_comment_count = 2
        except Exception as exc:  # pydantic raises ValidationError on frozen models
            assert "frozen" in str(exc).lower()
        else:  # pragma: no cover - frozen models never reach this branch
            raise AssertionError("HistoryWindowConfig should be immutable")
