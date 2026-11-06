"""Comment-body scan + ``secret_scan`` state record (issue #1347).

A report is the likelier leak channel than a commit and is posted before any
push, so the publish seam scans the body too. The redaction is recorded on the
workflow state (file/line/rule only) and the next comment renders a short
warning; a clean run shows nothing.
"""

from __future__ import annotations

import pytest

from nebuko.adapters.local import LocalAdapter
from nebuko.engine.lib.prepare_comment import (
    build_comment_body_parts,
    format_secret_scan_warning,
)
from nebuko.engine.lib.workflow_state import (
    SecretFinding,
    SecretScan,
    SecretScanStatus,
    WorkflowState,
    build_state,
)
from nebuko.engine.models import Trigger
from nebuko.engine.run_loop import Engine

# Built from parts so this file never trips a scanner on itself.
FAKE_OAUTH = "gho_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def _redacted_scan() -> SecretScan:
    return SecretScan(
        status=SecretScanStatus.REDACTED,
        findings=[SecretFinding(file="answer", line=3, rule="github-oauth")],
        at="implement",
    )


class TestCommentBodyRedaction:
    def test_the_posted_body_never_carries_the_value(self):
        _, body = build_comment_body_parts(
            latest_answer=f"here is the log:\n{FAKE_OAUTH}\n",
            final_message=f"here is the log:\n{FAKE_OAUTH}\n",
            role_name="dev",
        )

        assert FAKE_OAUTH not in body
        assert "<REDACTED:github-oauth>" in body

    def test_the_header_is_scanned_too(self):
        state = WorkflowState(
            workflow="spec-tdd",
            step="spec",
            current_context={"leak": FAKE_OAUTH},
        )

        header, _ = build_comment_body_parts(
            latest_answer="done", final_message="done", role_name="dev", state=state
        )

        assert FAKE_OAUTH not in header

    def test_a_clean_body_is_untouched(self):
        _, body = build_comment_body_parts(
            latest_answer="all good\n", final_message="all good\n", role_name="dev"
        )

        assert body.startswith("all good")
        assert "REDACTED" not in body


class TestSecretScanWarning:
    def test_a_redacted_record_renders_a_warning(self):
        warning = format_secret_scan_warning(_redacted_scan())

        assert "Secret redacted" in warning
        assert "`answer`:3 (github-oauth)" in warning
        assert "re-entered by hand" in warning

    def test_a_clean_record_renders_nothing(self):
        assert format_secret_scan_warning(SecretScan()) == ""

    def test_no_record_renders_nothing(self):
        assert format_secret_scan_warning(None) == ""

    def test_the_warning_rides_the_comment(self):
        state = WorkflowState(workflow="spec-tdd", step="spec", secret_scan=_redacted_scan())

        _, body = build_comment_body_parts(
            latest_answer="done", final_message="done", role_name="dev", state=state
        )

        assert "Secret redacted" in body
        assert body.index("Secret redacted") < body.index("done")

    def test_a_clean_run_posts_no_warning(self):
        state = WorkflowState(workflow="spec-tdd", step="spec", secret_scan=SecretScan())

        _, body = build_comment_body_parts(
            latest_answer="done", final_message="done", role_name="dev", state=state
        )

        assert "Secret redacted" not in body


class TestSecretScanState:
    def test_merge_accumulates_and_stays_redacted(self):
        scan = SecretScan().merge([SecretFinding(file="a", line=1, rule="r")], at="s1")
        scan = scan.merge([SecretFinding(file="a", line=1, rule="r")], at="s2")

        assert scan.status is SecretScanStatus.REDACTED
        assert len(scan.findings) == 1
        assert scan.at == "s2"

    def test_a_clean_scan_stays_clean(self):
        assert SecretScan().merge([]).status is SecretScanStatus.CLEAN

    def test_build_state_carries_the_record_forward(self):
        previous = WorkflowState(secret_scan=_redacted_scan())

        state = build_state(previous, workflow="spec-tdd", step="review", agent="review")

        assert state.secret_scan is not None
        assert state.secret_scan.status is SecretScanStatus.REDACTED

    def test_legacy_state_without_the_key_loads(self):
        state = WorkflowState.model_validate({"workflow": "spec-tdd", "step": "spec"})

        assert state.secret_scan is None


def _trigger() -> Trigger:
    return Trigger(
        author="olivier-norture",
        body="@@dev do it",
        type="issue_comment",
        number=1347,
        issue_body="",
    )


class TestPublishSeam:
    def _engine(self, tmp_path) -> Engine:
        return Engine(
            adapter=LocalAdapter(tmp_path / "runs" / "run-1", trigger=_trigger()),
            repo_root=tmp_path,
            env={
                "GITHUB_REPOSITORY": "nebuko/nebuko",
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_RUN_ID": "1",
                "TRIGGER_AUTHOR": "olivier-norture",
            },
        )

    def test_the_publish_seam_redacts_and_records(self, tmp_path):
        engine = self._engine(tmp_path)
        state = WorkflowState(workflow="spec-tdd", step="spec", agent="dev")
        result = {
            "answer": f"leaked {FAKE_OAUTH}",
            "turn_outcome": {"appreciation": "approved"},
            "state": state,
        }

        posted = engine.publish(result)

        assert FAKE_OAUTH not in posted
        assert "<REDACTED:github-oauth>" in posted
        assert "Secret redacted" in posted
        # The record rides ``_publish_state`` and is folded into the persisted
        # state by the post-publish merge (issue #1347).
        scan = engine._publish_state.get("secret_scan")
        assert scan is not None and scan.status is SecretScanStatus.REDACTED
        assert scan.findings[0].rule == "github-oauth"
        merged = engine._merge_post_publish_state(result["state"])
        saved = engine.adapter.load_state()
        assert saved is not None and saved.secret_scan is not None
        assert saved.secret_scan.status is SecretScanStatus.REDACTED
        assert merged is not None and merged.secret_scan is not None

    def test_a_clean_publish_records_nothing(self, tmp_path):
        engine = self._engine(tmp_path)
        result = {
            "answer": "all good",
            "turn_outcome": {"appreciation": "approved"},
            "state": WorkflowState(workflow="spec-tdd", step="spec", agent="dev"),
        }

        posted = engine.publish(result)

        assert "Secret redacted" not in posted
        saved = engine.adapter.load_state()
        assert saved is None or saved.secret_scan is None
    def test_a_pre_push_redaction_warns_on_the_comment(self, tmp_path):
        """A git-finalize redaction reaches the comment of the same run."""
        engine = self._engine(tmp_path)
        engine._publish_state["secret_scan"] = _redacted_scan()
        result = {
            "answer": "all good",
            "turn_outcome": {"appreciation": "approved"},
            "state": WorkflowState(workflow="spec-tdd", step="spec", agent="dev"),
        }

        posted = engine.publish(result)

        assert "Secret redacted" in posted
        assert "`answer`:3 (github-oauth)" in posted


@pytest.fixture(autouse=True)
def _quiet_git(monkeypatch):
    """Keep the publish seam's git probes inert."""
    from nebuko.engine.lib.git import git_session

    monkeypatch.setattr(
        Engine,
        "_git_session_preflight",
        lambda self: git_session.GuardResult(ok=True, skipped=True),
    )
