"""``run_agent`` persists the conversation's event log (issue #1120).

Without a ``persistence_dir`` the OpenHands SDK falls back to an
``InMemoryFileStore`` ("No persistence_dir provided; falling back to
InMemoryFileStore") and the per-event JSON files — the per-command durations
the per-command analysis needs — are lost when the run ends. The conversation
must be constructed with a persistence directory that lands inside the
artifact staging area so the log uploads with the run's other artifacts.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_conversation(status_after: str = "finished") -> MagicMock:
    convo = MagicMock()

    async def _arun():
        convo.execution_status = SimpleNamespace(value=status_after)
        return None

    convo.arun = _arun
    convo.interrupt = MagicMock()
    convo.send_message = MagicMock()
    return convo


def _run_agent(monkeypatch):
    """Drive ``run_agent`` with a mocked SDK and return the Conversation kwargs."""
    from nebuko.engine.lib.agent_script.features.run_agent import run_agent as module

    with patch.object(module, "Conversation") as conversation_cls, patch.object(
        module, "Agent"
    ), patch.object(module, "LLM"), patch.object(
        module, "resolve_timeout_budget"
    ) as budget, patch.object(module, "build_condenser", return_value=None):
        budget.return_value = SimpleNamespace(
            soft_timeout_s=60.0, wind_down_timeout_s=30.0
        )
        conversation_cls.return_value = _make_conversation()
        module.run_agent(llm_config={}, prompt="do the thing")

    return conversation_cls.call_args.kwargs


def test_the_conversation_gets_a_persistence_dir(monkeypatch):
    kwargs = _run_agent(monkeypatch)

    assert kwargs["persistence_dir"]


def test_the_default_persistence_dir_lands_next_to_the_sdk_log(monkeypatch):
    monkeypatch.setenv("LOG_DIR", "/tmp/nebuko-task-logs")
    monkeypatch.delenv("OPENHANDS_PERSISTENCE_DIR", raising=False)

    kwargs = _run_agent(monkeypatch)

    assert kwargs["persistence_dir"] == "/tmp/nebuko-task-logs/persistence"


def test_openhands_persistence_dir_overrides_the_default(monkeypatch):
    monkeypatch.setenv("LOG_DIR", "/tmp/nebuko-task-logs")
    monkeypatch.setenv("OPENHANDS_PERSISTENCE_DIR", "/tmp/custom-persistence")

    kwargs = _run_agent(monkeypatch)

    assert kwargs["persistence_dir"] == "/tmp/custom-persistence"


def test_without_a_log_dir_the_persistence_dir_still_resolves(monkeypatch):
    monkeypatch.delenv("LOG_DIR", raising=False)
    monkeypatch.delenv("OPENHANDS_PERSISTENCE_DIR", raising=False)

    kwargs = _run_agent(monkeypatch)

    assert kwargs["persistence_dir"].endswith("persistence")
