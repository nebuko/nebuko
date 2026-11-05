"""The review's required cases for #1288 (round 3 fixes).

Two things the previous round got wrong are pinned here:

* the continue token is dropped **at creation** — the header builder sees the
  engine's verdict before the comment is framed, so a failed run's comment is
  a notification from the moment GitHub fires on it (an edit-after-post cannot
  retract a token GitHub already acted on);
* the engine-failure facts ride the **posted** comment's embedded state, so
  the next run — which reads its state from that comment, not from the
  runner-local hand-off file — actually sees them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nebuko.adapters.local import LocalAdapter
from nebuko.engine.lib.prepare_comment import (
    CONTINUE_TOKEN,
    BOT_MARKER,
    _header_line,
    build_comment_body_parts,
)
from nebuko.engine.run_loop import Engine
from nebuko.engine.lib.workflow_state import WorkflowState

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / "defaults" / "workflows"
LOCAL_WORKFLOWS_DIR = REPO_ROOT / ".nebuko" / "workflows"

RESOLVED = {
    "SKIP_AGENT": "false",
    "TASK": "dev",
    "WORKFLOW_NAME": "spec-tdd",
    "WORKFLOW_STEP": "spec",
}


def _trigger(number: int = 1288):
    from nebuko.engine.models import Trigger

    return Trigger(
        author="olivier-norture",
        body="@@dev do it",
        type="issue_comment",
        number=number,
        issue_body="",
    )


def _engine(tmp_path: Path) -> Engine:
    return Engine(
        adapter=LocalAdapter(tmp_path / "runs" / "run-1", trigger=_trigger()),
        repo_root=tmp_path,
        env={
            "CIRCUIT_BREAKER_THRESHOLD": "3",
            "GITHUB_REPOSITORY": "olivier-norture/nebuko",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "41540909805",
            "TRIGGER_AUTHOR": "olivier-norture",
        },
    )


def _agent_result(answer: str = "## headline\n\nDid the work.") -> dict:
    return {
        "answer": answer,
        "turn_outcome": {"appreciation": "approved"},
        "state": WorkflowState(
            workflow="spec-tdd",
            step="spec",
            agent="po",
            position=[{"workflow": "spec-tdd", "step": "spec"}],
        ),
    }


def _stub_resolved(monkeypatch, resolved: dict) -> None:
    import sys

    import nebuko.engine.lib.resolve_config  # noqa: F401

    monkeypatch.setattr(
        sys.modules["nebuko.engine.lib.resolve_config.resolve_config"],
        "resolve_config",
        lambda **_: resolved,
    )


def _failed_state_payload() -> dict:
    """The ``engine_failure`` payload the engine stamps on a failed run."""
    return {
        "ok": False,
        "step": "spec",
        "workflow": "spec-tdd",
        "phase": "git_finalize",
        "error": "git session finalize failed: push rejected",
        "pushed": False,
        "branch": "feature/issue-42-x",
        "head_sha": "",
        "base_branch": "main",
        "run_url": "https://github.com/olivier-norture/nebuko/actions/runs/1",
        "pr_url": "",
    }


@pytest.fixture(autouse=True)
def _quiet_git(monkeypatch):
    """Keep the git-bound phases inert: this suite exercises the crash paths."""
    from nebuko.engine.lib.git import branch_scaffold, git_session
    from nebuko.engine.lib.git import pr_finalize

    monkeypatch.setattr(
        Engine,
        "_git_session_preflight",
        lambda self: git_session.GuardResult(ok=True, skipped=True),
    )
    monkeypatch.setattr(
        Engine,
        "_branch_scaffold",
        lambda self: branch_scaffold.ScaffoldResult(ok=True, skipped=True),
    )
    monkeypatch.setattr(
        Engine,
        "_git_session_finalize",
        lambda self: git_session.GuardResult(ok=True, skipped=True),
    )
    monkeypatch.setattr(
        pr_finalize.PrFinalizer,
        "finalize",
        lambda self: pr_finalize.PrFinalizeResult(ok=True, skipped=True),
    )


@pytest.fixture
def capture(monkeypatch):
    """Every body the adapter's ``post`` receives, as GitHub would see it."""
    bodies: list[str] = []
    original = LocalAdapter.post

    def post(self, body):
        bodies.append(body)
        return original(self, body)

    monkeypatch.setattr(LocalAdapter, "post", post)
    return bodies


def _resolve_git_finalize_failure(monkeypatch, detail="push rejected"):
    """Force the git-session finalize to fail after publish succeeded."""
    from nebuko.engine.lib import git as git_lib

    monkeypatch.setattr(
        Engine,
        "_git_session_finalize",
        lambda self: git_lib.git_session.GuardResult(ok=False, detail=detail),
    )


class TestHeaderDropsTokenAtCreation:
    def test_a_failed_verdict_never_frames_the_token(self):
        """``engine_failed=True`` drops the token, whatever the state says."""
        header, _ = build_comment_body_parts(
            latest_answer="answer",
            final_message="answer",
            trigger_author="olivier-norture",
            role_name="dev",
            state=None,
            engine_failed=True,
        )
        assert CONTINUE_TOKEN not in header
        assert header.startswith(BOT_MARKER)

    def test_a_green_verdict_keeps_the_token(self):
        header, _ = build_comment_body_parts(
            latest_answer="answer",
            final_message="answer",
            trigger_author="olivier-norture",
            role_name="dev",
            state=None,
            engine_failed=False,
        )
        assert header.startswith(BOT_MARKER + CONTINUE_TOKEN)

    def test_a_state_carrying_a_failed_payload_frames_a_notification(self):
        """A state whose recorded outcome is a failed run drops the token.

        The failed ``turn_outcome`` is what ``_record_engine_failure`` stamps;
        a bare ``engine_failure`` context entry alone no longer decides the
        header (a stale entry from an earlier failed run must not block a
        later green run — issue #1288 round 4).
        """
        from nebuko.engine.lib.workflow_state.sub_domains.turn_outcome.turn_outcome import (
            IncompleteReason,
            TurnOutcome,
            TurnStatus,
        )

        state = WorkflowState(
            workflow="spec-tdd",
            step="spec",
            agent="po",
            turn_outcome=TurnOutcome(
                status=TurnStatus.INCOMPLETE,
                incomplete_reason=IncompleteReason.FAILURE,
            ),
            current_context={"engine_failure": _failed_state_payload()},
        )
        assert _header_line(state) == BOT_MARKER

    def test_a_state_without_a_failure_keeps_the_token(self):
        state = WorkflowState(workflow="s", step="x", agent="dev")
        assert _header_line(state) == BOT_MARKER + CONTINUE_TOKEN

    def test_a_stale_engine_failure_entry_keeps_the_token(self):
        """A green re-run over a stale ``engine_failure`` entry stays live.

        The stale facts must not silently stop the workflow (issue #1288
        round 4): the header reads the recorded outcome, not the context entry.
        """
        state = WorkflowState(
            workflow="s",
            step="x",
            agent="dev",
            current_context={"engine_failure": _failed_state_payload()},
        )
        assert _header_line(state) == BOT_MARKER + CONTINUE_TOKEN


class TestCreatedCommentCarriesNoTokenWhenFailed:
    def test_a_git_finalize_failure_creates_a_notification_comment(
        self, tmp_path, monkeypatch, capture
    ):
        """The #1288 round-3 core case: a git-finalize failure must frame the
        comment as a notification *at creation*, not edit it afterwards."""
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(RESOLVED))
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _agent_result()
        )
        _resolve_git_finalize_failure(monkeypatch)

        outcome = engine.run()

        assert outcome.exit_code.value != 0
        assert capture, "the run must have created a comment"
        for body in capture:
            assert CONTINUE_TOKEN not in body

    def test_a_publish_crash_posts_nothing_and_routes_on_dispatch(
        self, tmp_path, monkeypatch, capture
    ):
        """A real ``PublishError`` posts nothing (#766 semantics: nothing was
        posted, so nothing to re-frame) and routing proceeds on the dispatch."""
        from nebuko.adapters.github import PublishError

        def failing_post(self, body):
            raise PublishError("comment write failed", None)

        monkeypatch.setattr(LocalAdapter, "post", failing_post)
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(RESOLVED))
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _agent_result()
        )

        outcome = engine.run()

        assert "route" in outcome.phases
        assert capture == []


class TestEngineFactsRideThePostedComment:
    def test_the_failed_state_is_stashed_for_the_posted_payload(
        self, tmp_path, monkeypatch, capture
    ):
        """After a git-finalize failure, the state carrying the facts is the
        one the posted comment embeds — the next run reads its state from the
        posted comment, not from the runner-local hand-off file."""
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(RESOLVED))
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _agent_result()
        )
        _resolve_git_finalize_failure(monkeypatch)

        engine.run()

        failed_state = engine._publish_state.get("failed_state")
        assert failed_state is not None
        facts = failed_state.current_context["engine_failure"]
        assert facts["ok"] is False
        # The failure surfaced in the git-finalize phase; the notice names the
        # phase the failure actually happened in, not the phase the re-frame
        # runs in (issue #1288 round 4).
        assert facts["phase"] == "git_finalize"
        assert facts["pushed"] is False
        # The facts are visible in a created comment's embedded state too.
        assert any("engine_failure" in body for body in capture)

    def test_a_publish_crash_keeps_the_facts_it_recorded(
        self, tmp_path, monkeypatch
    ):
        """A real ``PublishError`` sequence must not lose the facts recorded
        before the post-publish merge re-saves the state (review finding 4)."""
        engine = _engine(tmp_path)
        stale = WorkflowState(workflow="s", step="x", agent="dev")
        engine._record_engine_failure(
            "publish exploded", phase="publish", pushed=True, state=stale
        )
        engine._merge_post_publish_state(stale)
        saved = json.loads(
            (tmp_path / "runs" / "run-1" / "state.json").read_text(
                encoding="utf-8"
            )
        )
        assert saved["current_context"]["engine_failure"]["ok"] is False


class TestEngineFailureClearedOnSuccess:
    def test_a_successful_run_clears_a_stale_engine_failure(
        self, tmp_path, monkeypatch, capture
    ):
        """The next green run must not inherit a stale failure payload (#1288
        review finding 5)."""
        engine = _engine(tmp_path)
        _stub_resolved(monkeypatch, dict(RESOLVED))
        monkeypatch.setattr(
            Engine, "invoke_agent", lambda self, prompt: _agent_result()
        )
        stale = WorkflowState(
            workflow="spec-tdd",
            step="spec",
            agent="dev",
            current_context={
                "engine_failure": {
                    "ok": False,
                    "phase": "git_finalize",
                    "error": "stale",
                    "pushed": False,
                }
            },
        )
        engine.adapter.save_state(stale)

        outcome = engine.run()

        assert outcome.exit_code.value == 0
        saved = json.loads(
            (tmp_path / "runs" / "run-1" / "state.json").read_text(
                encoding="utf-8"
            )
        )
        assert "engine_failure" not in saved["current_context"]
