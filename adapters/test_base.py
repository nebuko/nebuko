"""Tests for adapters.base — a fake in-memory adapter satisfying the protocol.

Verifies the protocol is structurally satisfiable and that the
save_state -> load_state round-trip works by posting `to_git_comment(state)`
into a journal and recovering it with `extract_latest_state_from_comments`.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / ".github" / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))

from adapters.base import PlatformAdapter  # noqa: E402
from engine.models import CommentEntry, Trigger, WorkflowState  # noqa: E402
from workflow_state.workflow_state import to_git_comment  # noqa: E402
from workflow_state.features.get_last_state.get_last_state import (  # noqa: E402
    extract_latest_state_from_comments,
)


class InMemoryAdapter:
    """Trivial in-memory fake: no I/O, journal is a list of CommentEntry."""

    def __init__(self, trigger: Trigger, history: list[CommentEntry] | None = None):
        self._trigger = trigger
        self._history = list(history or [])
        self.posted: list[str] = []

    def read_trigger(self) -> Trigger:
        return self._trigger

    def load_history(self) -> list[CommentEntry]:
        return list(self._history)

    def post(self, body: str) -> None:
        self.posted.append(body)
        self._history.append(CommentEntry(author="bot", body=body, created_at="2026-09-15T10:00:00Z"))

    def load_state(self) -> WorkflowState | None:
        return extract_latest_state_from_comments([c.model_dump() for c in self._history])

    def save_state(self, state: WorkflowState) -> None:
        self.post(to_git_comment(state))


class TestFakeAdapterSatisfiesProtocol:
    def test_structural_protocol_check(self):
        adapter = InMemoryAdapter(Trigger(author="a", number=1))
        assert isinstance(adapter, PlatformAdapter)

    def test_read_trigger(self):
        t = Trigger(author="alice", body="hi", type="issue_comment", number=7)
        adapter = InMemoryAdapter(t)
        assert adapter.read_trigger() == t

    def test_load_history(self):
        h = [CommentEntry(author="a", body="b", created_at="t")]
        adapter = InMemoryAdapter(Trigger(), history=h)
        assert adapter.load_history() == h

    def test_post_appends_to_history(self):
        adapter = InMemoryAdapter(Trigger())
        adapter.post("hello")
        assert adapter.posted == ["hello"]
        assert adapter.load_history()[0].body == "hello"


class TestStateRoundTrip:
    def test_save_then_load_recovers_state(self):
        adapter = InMemoryAdapter(Trigger())
        state = WorkflowState(workflow="wf", step="implement", agent="dev", phase="execution")
        adapter.save_state(state)

        recovered = adapter.load_state()
        assert recovered is not None
        assert recovered.workflow == "wf"
        assert recovered.step == "implement"
        assert recovered.agent == "dev"
        assert recovered.phase == "execution"

    def test_load_state_without_saves_is_none(self):
        adapter = InMemoryAdapter(Trigger())
        adapter.post("<!-- bot-workflow-terminal -->\njust a comment")
        assert adapter.load_state() is None