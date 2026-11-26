"""The project caller template must not start a run for a human --personal comment (#1479)."""

from __future__ import annotations

from pathlib import Path

CALLER = (
    Path(__file__).resolve().parents[3]
    / "src/nebuko/engine/init/defaults/nebuko-caller.yaml"
)


def _condition() -> str:
    text = CALLER.read_text(encoding="utf-8")
    return text.split("if: >-", 1)[1].split("uses:", 1)[0]


def test_caller_job_condition_skips_personal_comments():
    condition = _condition()
    assert "github.event_name != 'issue_comment' ||" in condition
    assert "!contains(github.event.comment.body, '--personal')" in condition
    # Bot comments are exempt, so a bot body that mentions --personal still passes.
    assert condition.index("github.event.comment.user.login == 'nebuko[bot]' ||") < condition.index(
        "!contains(github.event.comment.body, '--personal')"
    )


def test_caller_guard_only_matches_command_lines():
    # Prose that mentions --personal without a leading /nebuko does not suppress the run.
    assert "!startsWith(github.event.comment.body, '/nebuko')" in _condition()
