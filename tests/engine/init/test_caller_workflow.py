"""Caller-workflow generation contract for ``nebuko init`` (issue #1025).

Two properties are pinned here: the rendered workflow carries the #20 caller
contract (``issue_comment`` trigger, author gate, ``id-token: write``, broker
repo variable), and ``write`` never overwrites a file this command did not
generate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nebuko.engine.init.caller_workflow import MARKER, CallerWorkflowWriter, WriteResult

TARGET = Path(".github/workflows/nebuko-caller.yaml")


def _writer(tmp_path: Path, **kwargs) -> CallerWorkflowWriter:
    return CallerWorkflowWriter(target=tmp_path / "nebuko-caller.yaml", **kwargs)


# ── render ───────────────────────────────────────────────────────────────────


def test_given_a_writer_when_rendering_then_the_marker_is_the_first_line(tmp_path):
    rendered = _writer(tmp_path).render()

    assert rendered.splitlines()[0] == MARKER


def test_given_a_writer_when_rendering_then_the_issue_comment_trigger_is_present(tmp_path):
    rendered = _writer(tmp_path).render()

    assert "on:" in rendered
    assert "issue_comment:" in rendered
    assert "types: [created, edited]" in rendered


def test_given_a_writer_when_rendering_then_the_author_gate_is_present(tmp_path):
    rendered = _writer(tmp_path).render()

    assert 'contains(fromJSON(\'["OWNER","MEMBER","COLLABORATOR"]\')' in rendered
    assert "github.event.comment.author_association" in rendered
    assert "github.event.comment.user.login == 'nebuko[bot]'" in rendered
    assert "user.login != 'nebuko[bot]'" not in rendered


def test_given_a_writer_when_rendering_then_id_token_write_is_granted(tmp_path):
    rendered = _writer(tmp_path).render()
    permissions = rendered.split("permissions:", 1)[1].split("jobs:", 1)[0]

    assert "  id-token: write" in permissions


def test_given_a_writer_when_rendering_then_the_broker_repo_variable_is_referenced(tmp_path):
    rendered = _writer(tmp_path).render()

    assert "${{ vars.NEBUKO_BROKER_URL }}" in rendered


def test_given_a_custom_broker_variable_when_rendering_then_that_name_is_used(tmp_path):
    rendered = _writer(tmp_path, broker_variable="ACME_BROKER_URL").render()

    assert "${{ vars.ACME_BROKER_URL }}" in rendered
    assert "NEBUKO_BROKER_URL" not in rendered


def test_given_a_writer_when_rendering_then_the_output_is_valid_yaml(tmp_path):
    import yaml

    document = yaml.safe_load(_writer(tmp_path).render())

    assert "issue_comment" in document["on"]
    assert document["permissions"]["id-token"] == "write"


# ── write: CREATED / UNCHANGED / UPDATED / REFUSED ───────────────────────────


def test_given_no_file_when_writing_then_it_is_created(tmp_path):
    writer = _writer(tmp_path)

    assert writer.write() is WriteResult.CREATED
    assert writer.target.read_text(encoding="utf-8") == writer.render()


def test_given_an_identical_file_when_writing_then_it_is_unchanged(tmp_path):
    writer = _writer(tmp_path)
    writer.write()
    before = writer.target.stat().st_mtime_ns

    assert writer.write() is WriteResult.UNCHANGED
    assert writer.target.read_text(encoding="utf-8") == writer.render()
    assert writer.target.stat().st_mtime_ns == before


def test_given_a_generated_file_with_a_stale_body_when_writing_then_it_is_updated(tmp_path):
    writer = _writer(tmp_path)
    writer.write()
    writer.target.write_text(MARKER + "\nname: stale\n", encoding="utf-8")

    assert writer.write(update=True) is WriteResult.UPDATED
    assert writer.target.read_text(encoding="utf-8") == writer.render()


def test_given_a_generated_file_and_update_disabled_when_writing_then_it_is_refused(tmp_path):
    writer = _writer(tmp_path)
    writer.write()
    stale = MARKER + "\nname: stale\n"
    writer.target.write_text(stale, encoding="utf-8")

    assert writer.write(update=False) is WriteResult.REFUSED
    assert writer.target.read_text(encoding="utf-8") == stale


def test_given_a_foreign_file_when_writing_then_it_is_refused_and_untouched(tmp_path):
    writer = _writer(tmp_path)
    writer.target.parent.mkdir(parents=True, exist_ok=True)
    foreign = "name: Someone else's workflow\n"
    writer.target.write_text(foreign, encoding="utf-8")

    assert writer.write() is WriteResult.REFUSED
    assert writer.target.read_text(encoding="utf-8") == foreign


def test_given_no_file_and_dry_run_when_writing_then_nothing_is_written(tmp_path):
    writer = _writer(tmp_path)

    assert writer.write(dry_run=True) is WriteResult.CREATED
    assert not writer.target.exists()


def test_given_a_stale_generated_file_and_dry_run_when_writing_then_nothing_is_written(tmp_path):
    writer = _writer(tmp_path)
    stale = MARKER + "\nname: stale\n"
    writer.target.parent.mkdir(parents=True, exist_ok=True)
    writer.target.write_text(stale, encoding="utf-8")

    assert writer.write(dry_run=True) is WriteResult.UPDATED
    assert writer.target.read_text(encoding="utf-8") == stale


@pytest.mark.parametrize("result", list(WriteResult))
def test_given_the_enum_when_listing_then_it_has_the_four_outcomes(result):
    assert result in {
        WriteResult.CREATED,
        WriteResult.UPDATED,
        WriteResult.UNCHANGED,
        WriteResult.REFUSED,
    }
