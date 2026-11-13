"""The engine failure notice's optional timing block (issue #1375).

A provider failure (issue #1322) stops the run before ``publish``, so the only
place a human sees where the wall-clock went is the notice. The block is
additive: a notice without timings renders exactly as before.
"""

from __future__ import annotations

from nebuko.engine.lib.config import EngineFailureNotice

TIMINGS = "Time spent: normalize=0.01s agent=42.50s\n"


def _notice(**overrides) -> EngineFailureNotice:
    fields = {
        "step": "implement",
        "workflow": "default",
        "phase": "agent",
        "error": "litellm.RateLimitError: 429",
        "target": "olivier-norture",
        "provider": "https://api.mistral.ai/v1",
        "model": "mistral/deepseek-v4-1-flash-1017",
    }
    fields.update(overrides)
    return EngineFailureNotice(**fields)


class TestEngineFailureNoticeTimings:
    def test_timings_block_is_rendered_when_present(self):
        body = _notice(timings=TIMINGS).render()

        assert TIMINGS.strip() in body

    def test_no_timings_block_without_timings(self):
        body = _notice().render()

        assert "Time spent:" not in body

    def test_the_failure_marker_and_aftermath_survive(self):
        body = _notice(timings=TIMINGS).render()

        assert body.startswith("<!-- bot-workflow-terminal -->")
        assert "<!-- bot-engine-failure -->" in body
        assert "did **not** advance" in body
        assert "/nebuko resume" in body
