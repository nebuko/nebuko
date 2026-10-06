"""Tests for the AgentStartNotice sub-domain (issue #720)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from config import AgentStartNotice, brand

RUN_URL = "https://github.com/olivier-norture/git-flow-ai/actions/runs/36979038271"


class TestAgentStartNotice:
    """The rendered body is the acknowledgment the issue thread expects."""

    def test_render_matches_the_legacy_acknowledgment(self):
        notice = AgentStartNotice(agent="dev", role="dev", run_url=RUN_URL, issue_number=720)
        assert notice.render() == (
            "<!-- bot-agent-dev -->\n"
            f"{brand().logo_mark()} **dev agent started** — [View workflow run logs]({RUN_URL})"
        )

    def test_render_carries_the_caller_marker_for_the_loop_guard(self):
        notice = AgentStartNotice(agent="review", role="review", run_url=RUN_URL)
        assert notice.render().splitlines()[0] == "<!-- bot-agent-review -->"

    def test_render_uses_role_and_run_url_verbatim(self):
        notice = AgentStartNotice(agent="ctx", role="context", run_url="https://example.test/run")
        assert notice.render().splitlines()[1] == (
            f"{brand().logo_mark()} **context agent started** — [View workflow run logs](https://example.test/run)"
        )

    def test_issue_number_defaults_to_none(self):
        assert AgentStartNotice(agent="dev", role="dev", run_url=RUN_URL).issue_number is None

    def test_is_immutable(self):
        notice = AgentStartNotice(agent="dev", role="dev", run_url=RUN_URL)
        try:
            notice.agent = "genius"
        except Exception as exc:  # pydantic raises ValidationError on frozen models
            assert "frozen" in str(exc).lower()
        else:  # pragma: no cover - frozen models never reach this branch
            raise AssertionError("AgentStartNotice should be immutable")

    def test_round_trips_through_model_dump(self):
        notice = AgentStartNotice(agent="dev", role="dev", run_url=RUN_URL, issue_number=720)
        assert AgentStartNotice.model_validate(notice.model_dump()) == notice
