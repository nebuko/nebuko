"""Filesystem-backed platform adapter (issue #590, Stage A).

Run directory ``runs/<run-id>/`` holds ``trigger.json``, ``journal.md``,
``state.json``, ``latest_answer.md`` and ``console.txt``. Journal entries carry
the standard ``<!-- bot-workflow-terminal -->`` / ``<!-- bot-agent-* -->``
markers, so ``loop_guard``, ``circuit_breaker`` and state extraction run
**unchanged** against :meth:`LocalAdapter.load_history`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Sequence


if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from pathlib import Path

    from nebuko.engine.context_selection import ContextEntry
    from nebuko.engine.models import CommentEntry, Trigger
    from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

__all__ = ["LocalAdapter"]

TRIGGER_FILE = "trigger.json"
JOURNAL_FILE = "journal.md"
STATE_FILE = "state.json"
LATEST_ANSWER_FILE = "latest_answer.md"
CONSOLE_LOG_FILE = "console.txt"

# Each journal entry is introduced by this boundary line so posted bodies keep
# their blank lines verbatim; the captured group is the entry timestamp.
_JOURNAL_ENTRY_RE = re.compile(r"^<!-- journal-entry @ (.+?) -->$", re.MULTILINE)
_BOT_AGENT_RE = re.compile(r"<!-- bot-agent-([a-z0-9\-]+) -->")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# @stub-protected
class LocalAdapter:  # structurally satisfies adapters.base.PlatformAdapter
    """Network-free adapter backed by a single run directory."""

    # @stub-protected
    def __init__(self, run_dir: Path, *, trigger: Trigger | None = None) -> None:
        from pathlib import Path as _Path

        self._run_dir = _Path(run_dir)
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._trigger = trigger

    # @stub-protected
    def read_trigger(self) -> Trigger:
        if self._trigger is not None:
            return self._trigger
        from nebuko.engine.models import Trigger

        payload = json.loads((self._run_dir / TRIGGER_FILE).read_text(encoding="utf-8"))
        return Trigger(**payload)

    # @stub-protected
    def load_history(self, *, limit: int | None = None) -> Sequence[CommentEntry]:
        entries = self._read_journal()
        if limit is not None:
            entries = entries[-limit:] if limit > 0 else []
        return entries

    # @stub-protected
    def load_raw_thread(self) -> tuple[Any, list[dict[str, Any]]]:
        """Read-only ``(issue, comments)`` payloads for context selection (#889).

        Same contract as :meth:`GitHubAdapter.load_raw_thread`: the issue object
        (``None`` locally) and the full, oldest-first comment list. The local
        journal carries no GitHub ids, so a stable 1-based positional
        id stands in (the journal is append-only, so existing ids never shift).
        There is no issue object locally, so the ledger is comment-only.

        Every journal entry was produced by the adapter's own ``post`` (the local
        engine bot) and carries the terminal marker, so the classifier derives
        the engine/agent kinds from that body marker — the same signal used on
        the GitHub path (issue #872). ``type: "Bot"`` is set as well so the
        synthetic thread mirrors a real bot account for the account-based
        OR-signal, but it is not what the classification relies on.
        """
        comments = [
            {
                "id": index,
                "body": entry.body,
                "created_at": entry.created_at,
                "user": {"login": entry.author, "type": "Bot"},
            }
            for index, entry in enumerate(self._read_journal(), start=1)
        ]
        return None, comments

    # @stub-protected
    def load_context_entries(self) -> "list[ContextEntry]":
        """Typed, forward-ordered thread entries for context selection (#889).

        Empty when the host cannot supply a full thread: the engine then
        degrades to its bounded comment window and warns (D9). The persisted
        state's ``history`` groups a split answer's parts (issue #913). Every
        journal row is the local engine bot's own post, but a human trigger is
        still the only trusted commenter (issue #872).
        """
        from nebuko.engine.context_entries import entries_from_raw_thread

        issue, comments = self.load_raw_thread()
        if issue is None and self._trigger is not None and self._trigger.issue_body:
            # No issue payload in the journal: the trigger's snapshot is the
            # issue body the entry rule needs (D11 / default-last).
            issue = {"body": self._trigger.issue_body, "id": None}
        state = self.load_state()
        login = str(getattr(state, "started_by", "") or "").strip()
        if not login:
            trigger = self.read_trigger()
            login = str(getattr(trigger, "author", "") or "").strip()
        return entries_from_raw_thread(
            issue,
            comments,
            state=state,
            trigger_login=login,
        )

    def post(self, body: str) -> str:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        stored = self._ensure_terminal_marker(body)
        journal = self._run_dir / JOURNAL_FILE
        separator = "\n\n" if journal.is_file() and journal.stat().st_size > 0 else ""
        with journal.open("a", encoding="utf-8") as handle:
            handle.write(f"{separator}<!-- journal-entry @ {_utc_now_iso()} -->\n{stored}")
        (self._run_dir / LATEST_ANSWER_FILE).write_text(stored, encoding="utf-8")
        return str(len(self._read_journal()))

    # @stub-protected
    def load_state(self) -> WorkflowState | None:
        path = self._run_dir / STATE_FILE
        if not path.is_file():
            return None
        from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

        return WorkflowState.model_validate_json(path.read_text(encoding="utf-8"))

    # @stub-protected
    def save_state(self, state: WorkflowState) -> None:
        (self._run_dir / STATE_FILE).write_text(
            state.model_dump_json(), encoding="utf-8"
        )

    # @stub-protected
    def delete_issue_comments(self, issue: str, from_comment: str = "") -> int:
        """Local equivalent of a batch comment deletion.

        The local journal is the issue thread, but its entries carry no numeric
        id, so the ``from_comment`` boundary cannot be resolved and the whole
        journal is cleared (the ``delete comments``-without-``--from`` behaviour).
        """
        entries = self._read_journal()
        if not entries:
            return 0
        (self._run_dir / JOURNAL_FILE).write_text("", encoding="utf-8")
        return len(entries)

    # @stub-protected
    def artifact_dir(self) -> Path:
        """The run directory doubles as the local host's artifact staging area."""
        return self._run_dir

    # @stub-protected
    def write_artifact(self, name: str, content: str) -> None:
        path = self.artifact_dir() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # ── internals ────────────────────────────────────────────────────

    @staticmethod
    def _ensure_terminal_marker(body: str) -> str:
        from nebuko.engine.lib.github_client import ensure_bot_marker

        return ensure_bot_marker(body)

    def _read_journal(self) -> list[CommentEntry]:
        from nebuko.engine.models import CommentEntry

        path = self._run_dir / JOURNAL_FILE
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return []

        parts = _JOURNAL_ENTRY_RE.split(text)
        entries: list[CommentEntry] = []
        position = 0
        for index in range(1, len(parts) - 1, 2):
            body = parts[index + 1].lstrip("\n").rstrip("\n")
            body = self._strip_injected_marker(body)
            position += 1
            # The journal entry's ordinal is its comment id, matching the
            # ``post`` return (issue #756): step-output locators round-trip
            # across runs.
            entries.append(
                CommentEntry(
                    author=self._entry_author(body),
                    body=body,
                    created_at=parts[index].strip(),
                    comment_id=str(position),
                )
            )
        return entries

    @staticmethod
    def _strip_injected_marker(body: str) -> str:
        """Drop the universal marker ``post`` injects on line 1.

        History entries must round-trip to exactly the body handed to
        :meth:`post`; consumers read the ``<!-- bot-agent-* -->`` markers the
        bodies themselves carry, not the injected terminal marker. A live
        answer already opens with the marker on its own line 1 (issue #978), so
        ``post`` injects nothing and there is nothing to strip.
        """
        from nebuko.engine.lib.github_client import BOT_MARKER

        if body == BOT_MARKER:
            return ""
        if body.startswith(BOT_MARKER + "\n"):
            return body[len(BOT_MARKER) + 1 :]
        return body

    @staticmethod
    def _entry_author(body: str) -> str:
        match = _BOT_AGENT_RE.search(body)
        return match.group(1) if match else "bot"
