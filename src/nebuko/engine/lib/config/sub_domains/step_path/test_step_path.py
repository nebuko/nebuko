"""Unit tests for the StepPath sub-domain (issue #822).

Covers the parse/validation contract of the nested step selector used by
``/nebuko run workflow --step`` and ``/nebuko force-step --name``.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import StepPath  # noqa: E402


class TestStepPathParse:
    def test_single_segment(self):
        path = StepPath.parse("scaffold")

        assert path is not None
        assert path.raw == "scaffold"
        assert path.segments == ("scaffold",)
        assert not path.is_nested
        assert path.depth == 1
        assert path.leaf_step_name() == "scaffold"
        assert path.mount_prefix() == ()

    def test_two_segments(self):
        path = StepPath.parse("sub_impl.step_3")

        assert path is not None
        assert path.segments == ("sub_impl", "step_3")
        assert path.is_nested
        assert path.depth == 2
        assert path.leaf_step_name() == "step_3"
        assert path.mount_prefix() == ("sub_impl",)

    def test_three_segments(self):
        path = StepPath.parse("sub_impl.step_3.review")

        assert path is not None
        assert path.segments == ("sub_impl", "step_3", "review")
        assert path.is_nested
        assert path.depth == 3
        assert path.leaf_step_name() == "review"
        assert path.mount_prefix() == ("sub_impl", "step_3")

    @pytest.mark.parametrize(
        "raw",
        ["", "a..b", ".a", "a.", ".", "a/b", "a b", " a", "a ", "../etc/passwd"],
    )
    def test_malformed_selectors_return_none(self, raw):
        assert StepPath.parse(raw) is None

    def test_hyphen_and_underscore_segments_are_allowed(self):
        path = StepPath.parse("dev-review-loop.step_3.final-fix")

        assert path is not None
        assert path.segments == ("dev-review-loop", "step_3", "final-fix")
