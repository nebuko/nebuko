"""Closing prompts share one outcome rule block across harnesses (issue #1437)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from nebuko.engine.harness.base import (
    CLOSING_OUTCOME_RULES,
    build_closing_prompt,
    warn_if_hold_means_done,
)
from nebuko.engine.harness.claude_code import CLOSING_PROMPT, FINALIZE_TOOL
from nebuko.engine.harness.openhands import FINALIZE_PROMPT
from nebuko.engine.lib.agent_script.features.run_agent.dispatch_recall import (
    DISPATCH_RECALL_PROMPT,
)


def _rendered(template: str) -> str:
    return template.format(sections="")


@pytest.mark.parametrize("template", [FINALIZE_PROMPT, CLOSING_PROMPT])
def test_adapter_prompt_carries_shared_outcome_rules(template: str) -> None:
    assert CLOSING_OUTCOME_RULES in _rendered(template)


@pytest.mark.parametrize("template", [FINALIZE_PROMPT, CLOSING_PROMPT])
def test_adapter_prompt_says_hold_only_when_blocked(template: str) -> None:
    text = _rendered(template)
    assert "only when you are blocked" in text
    assert "never hold because your work is finished" in text.lower()
    assert "finished work is 'approved'" in text


@pytest.mark.parametrize("template", [FINALIZE_PROMPT, CLOSING_PROMPT])
def test_adapter_prompt_carries_agent_call_and_empty_report_guidance(
    template: str,
) -> None:
    text = _rendered(template)
    assert "turn_outcome.agent_call" in text
    assert "Nothing is posted if `report` is empty." in text


def test_claude_code_prompt_names_its_mcp_finalize_tool() -> None:
    assert f"`{FINALIZE_TOOL}`" in _rendered(CLOSING_PROMPT)


def test_builder_keeps_sections_slot_and_escapes_rule_braces() -> None:
    template = build_closing_prompt(finalize_tool="finalize")
    assert template.count("{sections}") == 1
    assert template.format(sections="SEC").count("SEC") == 1
    assert "{agent_name, task}" in template.format(sections="")


def test_recall_prompt_forbids_holding_finished_work() -> None:
    assert "Never hold because your work is finished" in DISPATCH_RECALL_PROMPT
    assert "finished work is 'approved'" in DISPATCH_RECALL_PROMPT


def _outcome(appreciation: str, note: str | None) -> SimpleNamespace:
    return SimpleNamespace(appreciation=appreciation, note=note)


def test_hold_with_done_note_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        warned = warn_if_hold_means_done(
            "implement", "claude-haiku-5-5", _outcome("hold", "Work is complete.")
        )
    assert warned is True
    assert "step implement" in caplog.text
    assert "claude-haiku-5-5" in caplog.text


def test_hold_when_blocked_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        warned = warn_if_hold_means_done(
            "context", "m", _outcome("hold", "Blocked, need the API key.")
        )
    assert warned is False
    assert caplog.text == ""


def test_approved_done_note_does_not_warn() -> None:
    assert warn_if_hold_means_done("s", "m", _outcome("approved", "done")) is False


def test_missing_outcome_does_not_warn() -> None:
    assert warn_if_hold_means_done("s", "m", None) is False
