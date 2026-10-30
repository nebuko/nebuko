"""Tests for the ScriptStepReport sub-domain (issue #885)."""

import sys
from pathlib import Path

from nebuko.engine.lib.config import ScriptStepReport, brand
from nebuko.engine.lib.config.sub_domains.script_step_report.script_step_report import MAX_OUTPUT_CHARS


class TestScriptStepReport:
    def test_success_banner_names_the_step_exit_code_and_duration(self):
        report = ScriptStepReport(
            step="run_tests", ok=True, exit_code=0, duration_seconds=12.34, output="ok"
        )
        body = report.render()
        assert body.splitlines()[0] == (
            f"{brand().logo_mark()} **run_tests command succeeded** (exit code 0, 12.3s)"
        )

    def test_failure_banner_says_failed(self):
        report = ScriptStepReport(step="run_tests", ok=False, exit_code=1)
        assert "**run_tests command failed** (exit code 1, 0.0s)" in report.render()

    def test_timeout_without_exit_code_omits_it(self):
        report = ScriptStepReport(step="run_tests", ok=False, exit_code=None)
        assert "(0.0s)" in report.render()
        assert "exit code" not in report.render()

    def test_output_is_wrapped_in_a_collapsed_fenced_block(self):
        report = ScriptStepReport(step="git_diff", ok=True, exit_code=0, output="+new line")
        body = report.render()
        assert "<details>" in body
        assert "<summary>command output</summary>" in body
        assert "</details>" in body
        assert "```\n+new line\n```" in body

    def test_empty_output_renders_a_placeholder(self):
        report = ScriptStepReport(step="git_diff", ok=True, exit_code=0, output="   ")
        assert "_(no output)_" in report.render()

    def test_fence_outlives_backticks_in_the_output(self):
        report = ScriptStepReport(
            step="git_diff", ok=True, exit_code=0, output="```\ninner fence\n```"
        )
        body = report.render()
        assert "````\n" in body  # a 4-backtick fence, longer than the inner run

    def test_oversized_output_is_truncated_with_a_marker(self):
        report = ScriptStepReport(
            step="run_tests",
            ok=False,
            exit_code=1,
            output="a" * (MAX_OUTPUT_CHARS + 5_000),
        )
        body = report.render()
        assert "characters truncated" in body
        assert len(body) < MAX_OUTPUT_CHARS + 1_000

    def test_the_report_never_carries_a_command_line(self):
        # The value object has no command/args field at all; only the step name
        # is echoed (issue #885 secret-lean).
        report = ScriptStepReport(step="git_diff", ok=True, exit_code=0, output="x")
        assert "git diff" not in report.render()

    def test_is_immutable(self):
        report = ScriptStepReport(step="git_diff", ok=True)
        try:
            report.ok = False
        except Exception as exc:  # pydantic raises ValidationError on frozen models
            assert "frozen" in str(exc).lower()
        else:  # pragma: no cover - frozen models never reach this branch
            raise AssertionError("ScriptStepReport should be immutable")

    def test_round_trips_through_model_dump(self):
        report = ScriptStepReport(
            step="run_tests", ok=True, exit_code=0, duration_seconds=1.5, output="out"
        )
        assert ScriptStepReport.model_validate(report.model_dump()) == report
