"""Tests for the SCAFFOLD_BRANCH marker handling (issue #604)."""

from dataclasses import FrozenInstanceError

import pytest

from nebuko.engine.lib.agent_script.sub_domains.scaffold_branch.scaffold_branch import (
    ScaffoldBranchResult,
    extract_scaffold_branch,
    extract_scaffold_branch_any,
    scaffold_branch_summary,
    strip_scaffold_branch,
    validate_scaffold_branch_ref,
)


class TestExtractScaffoldBranch:
    """extract_scaffold_branch: pure I/O on the final answer text."""

    def test_extracts_marker_on_own_line(self):
        msg = (
            "Stub deliverable complete.\n"
            "SCAFFOLD_BRANCH: feature/issue-590-port-adapter-engine\n"
            "Handing off to the next step."
        )
        assert extract_scaffold_branch(msg) == "feature/issue-590-port-adapter-engine"

    def test_extracts_marker_with_extra_indent_and_spaces(self):
        assert extract_scaffold_branch("  SCAFFOLD_BRANCH:   feature/issue-1-x  ") == "feature/issue-1-x"

    def test_no_marker_returns_none(self):
        assert extract_scaffold_branch("Nothing here, just prose.") is None

    def test_empty_message_returns_none(self):
        assert extract_scaffold_branch("") is None

    def test_none_message_returns_none(self):
        assert extract_scaffold_branch(None) is None

    def test_marker_without_value_returns_none(self):
        assert extract_scaffold_branch("SCAFFOLD_BRANCH:\n") is None

    def test_marker_with_malformed_value_returns_none(self):
        # Quote, angle bracket, or pipe in the ref would break a git fetch.
        for bad in ("SCAFFOLD_BRANCH: 'feature/x'", "SCAFFOLD_BRANCH: feature|evil"):
            assert extract_scaffold_branch(bad) is None

    def test_marker_starting_with_dash_returns_none(self):
        assert extract_scaffold_branch("SCAFFOLD_BRANCH: -invalid") is None

    def test_first_wellformed_marker_wins(self):
        msg = "SCAFFOLD_BRANCH: feature/old\nSCAFFOLD_BRANCH: feature/new"
        # Documented behavior: first well-formed marker wins (a correct
        # agent emits exactly one).
        assert extract_scaffold_branch(msg) == "feature/old"

    def test_marker_word_in_prose_does_not_match(self):
        # Not in the `SCAFFOLD_BRANCH: <ref>` line form → ignored.
        msg = "The SCAFFOLD_BRANCH value lives in the state."
        assert extract_scaffold_branch(msg) is None


class TestExtractScaffoldBranchAny:
    """extract_scaffold_branch_any: dual-source search (issue #636)."""

    def test_marker_only_in_latest_answer(self):
        result = extract_scaffold_branch_any(
            "Short finish note.",
            "Report.\nSCAFFOLD_BRANCH: feature/issue-635-x\nDone.",
        )
        assert result == ScaffoldBranchResult(
            branch="feature/issue-635-x", source="latest_answer"
        )

    def test_marker_in_both_finish_message_wins(self):
        result = extract_scaffold_branch_any(
            "SCAFFOLD_BRANCH: feature/issue-1-finish",
            "SCAFFOLD_BRANCH: feature/issue-1-answer",
        )
        assert result == ScaffoldBranchResult(
            branch="feature/issue-1-finish", source="finish_message"
        )

    def test_malformed_in_finish_valid_in_answer_falls_back(self):
        result = extract_scaffold_branch_any(
            "SCAFFOLD_BRANCH: 'broken'",
            "SCAFFOLD_BRANCH: feature/issue-1-ok",
        )
        assert result == ScaffoldBranchResult(
            branch="feature/issue-1-ok", source="latest_answer"
        )

    def test_malformed_in_both_returns_none(self):
        result = extract_scaffold_branch_any(
            "SCAFFOLD_BRANCH: -bad", "SCAFFOLD_BRANCH: 'also-bad'"
        )
        assert result == ScaffoldBranchResult(branch=None, source=None)

    def test_no_marker_anywhere_returns_none(self):
        result = extract_scaffold_branch_any("Prose A.", "Prose B.")
        assert result == ScaffoldBranchResult(branch=None, source=None)

    def test_none_inputs_return_none(self):
        result = extract_scaffold_branch_any(None, None)
        assert result == ScaffoldBranchResult(branch=None, source=None)

    def test_invalid_ref_rejected_in_both_sources(self):
        for bad in ("feature|evil", "<x>", "feature/ok<x>"):
            result = extract_scaffold_branch_any(
                f"SCAFFOLD_BRANCH: {bad}", f"SCAFFOLD_BRANCH: {bad}"
            )
            assert result == ScaffoldBranchResult(branch=None, source=None)

    def test_result_is_frozen(self):
        result = extract_scaffold_branch_any(None, "SCAFFOLD_BRANCH: feature/1")
        with pytest.raises(FrozenInstanceError):
            result.branch = "other"

    def test_tool_branch_beats_both_markers(self):
        result = extract_scaffold_branch_any(
            "SCAFFOLD_BRANCH: feature/issue-1-finish",
            "SCAFFOLD_BRANCH: feature/issue-1-answer",
            tool_branch="feature/issue-635-tool",
        )
        assert result == ScaffoldBranchResult(
            branch="feature/issue-635-tool", source="tool"
        )

    def test_tool_branch_with_no_markers(self):
        result = extract_scaffold_branch_any(
            "Short finish note.", "Plain answer.", tool_branch="feature/issue-635-x"
        )
        assert result == ScaffoldBranchResult(
            branch="feature/issue-635-x", source="tool"
        )

    def test_no_tool_branch_preserves_dual_source_behavior(self):
        """Regression: without tool_branch the PR #637 2-source
        precedence is unchanged."""
        result = extract_scaffold_branch_any(
            "Short finish note.",
            "Report.\nSCAFFOLD_BRANCH: feature/issue-635-x\nDone.",
            tool_branch=None,
        )
        assert result == ScaffoldBranchResult(
            branch="feature/issue-635-x", source="latest_answer"
        )


class TestValidateScaffoldBranchRef:
    """validate_scaffold_branch_ref: shared ref-validation predicate."""

    def test_valid_refs_accepted(self):
        for good in ("feature/issue-635-workflow-command", "main", "fix/a-b/c"):
            assert validate_scaffold_branch_ref(good)

    def test_empty_and_none_rejected(self):
        assert not validate_scaffold_branch_ref(None)
        assert not validate_scaffold_branch_ref("")

    def test_leading_dash_rejected(self):
        assert not validate_scaffold_branch_ref("-main")

    def test_hostile_chars_rejected(self):
        for bad in ("feature|evil", "<x>", "feature/ok<x>", "a'b", 'a"b', "a|b"):
            assert not validate_scaffold_branch_ref(bad)


class TestStripScaffoldBranch:
    """strip_scaffold_branch: removes the marker line, keeps the rest."""

    def test_strips_marker_line(self):
        msg = "Report.\nSCAFFOLD_BRANCH: feature/issue-1-x\nMore text."
        assert strip_scaffold_branch(msg) == "Report.\nMore text."

    def test_no_marker_is_noop(self):
        assert strip_scaffold_branch("Just prose.") == "Just prose."

    def test_empty_and_none_are_noop(self):
        assert strip_scaffold_branch("") == ""
        assert strip_scaffold_branch(None) is None

    def test_removes_trailing_whitespace_left_by_strip(self):
        msg = "Report.\nSCAFFOLD_BRANCH: feature/x   \n"
        assert strip_scaffold_branch(msg) == "Report."


class TestScaffoldBranchSummary:
    """scaffold_branch_summary: one-line human summary."""

    def test_renders_branch_in_backticks(self):
        assert scaffold_branch_summary("feature/issue-1-x") == "Scaffold branch: `feature/issue-1-x`"