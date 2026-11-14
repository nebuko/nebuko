"""Tests for the ScriptStartNotice sub-domain (issue #885)."""

import sys
from pathlib import Path

from nebuko.engine.lib.config import ScriptStartNotice, brand

RUN_URL = "https://github.com/nebuko/nebuko/actions/runs/38163682059"


class TestScriptStartNotice:
    """The rendered body is the acknowledgment the issue thread expects."""

    def test_render_matches_the_agent_start_shape(self):
        notice = ScriptStartNotice(step="run_tests", run_url=RUN_URL, issue_number=885)
        assert notice.render() == (
            "<!-- bot-command-run_tests -->\n"
            f"{brand().logo_mark()} **run_tests command started** — "
            f"[View workflow run logs]({RUN_URL})"
        )

    def test_render_uses_the_command_marker_not_the_agent_marker(self):
        notice = ScriptStartNotice(step="git_diff", run_url=RUN_URL)
        first = notice.render().splitlines()[0]
        assert first == "<!-- bot-command-git_diff -->"
        # A ``bot-agent-`` tag here would inflate the circuit-breaker agent
        # call count (issue #885).
        assert "bot-agent-" not in first

    def test_render_carries_no_continue_token_or_state_block(self):
        notice = ScriptStartNotice(step="git_diff", run_url=RUN_URL)
        body = notice.render()
        assert "<!-- workflow-continue -->" not in body
        assert "WORKFLOW_STATE_START" not in body

    def test_issue_number_defaults_to_none(self):
        assert ScriptStartNotice(step="git_diff", run_url=RUN_URL).issue_number is None

    def test_is_immutable(self):
        notice = ScriptStartNotice(step="git_diff", run_url=RUN_URL)
        try:
            notice.step = "other"
        except Exception as exc:  # pydantic raises ValidationError on frozen models
            assert "frozen" in str(exc).lower()
        else:  # pragma: no cover - frozen models never reach this branch
            raise AssertionError("ScriptStartNotice should be immutable")

    def test_round_trips_through_model_dump(self):
        notice = ScriptStartNotice(step="run_tests", run_url=RUN_URL, issue_number=885)
        assert ScriptStartNotice.model_validate(notice.model_dump()) == notice
