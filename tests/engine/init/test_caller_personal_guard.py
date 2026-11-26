"""The project caller template must not start a run for a --personal comment (#1479)."""

from __future__ import annotations

from pathlib import Path

CALLER = (
    Path(__file__).resolve().parents[3]
    / "src/nebuko/engine/init/defaults/nebuko-caller.yaml"
)


def test_caller_job_condition_skips_personal_comments():
    text = CALLER.read_text(encoding="utf-8")
    assert "!contains(github.event.comment.body, '--personal')" in text
    condition = text.split("if: >-", 1)[1].split("uses:", 1)[0]
    assert "github.event_name != 'issue_comment' ||" in condition
    assert condition.index("--personal") > condition.index("nebuko[bot]")
