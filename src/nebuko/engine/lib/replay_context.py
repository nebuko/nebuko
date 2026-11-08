"""Prompt-context and recorded-output replay for ``nebuko.engine.lib.replay``.

Given the resolver output the replay already computed for one invocation, build
the :class:`~nebuko.engine.prompt_plan.PromptPlan` the live engine would have
assembled -- the ordered sections that feed the agent prompt and where each came
from -- plus the rendered prompt and the output the live run recorded.

Everything here is read-only and offline: no LLM, no agent, no GitHub writes,
and no step-input providers are executed. What a state cannot reproduce (comment
bodies, the issue body) comes from an optional recorded thread; without one the
plan is still produced, with an empty thread, and the gap is reported in
``ContextReplay.notes``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from nebuko.engine.lib.workflow_state.workflow_state import (
    STATE_MARKER_END,
    STATE_MARKER_START,
    WorkflowState,
)
from nebuko.engine.prompt_plan import PromptPlan, render_prompt


_RAW_STATE_BLOCK = re.compile(
    re.escape(STATE_MARKER_START) + r".*" + re.escape(STATE_MARKER_END), re.DOTALL
)


def _without_state(body: str) -> str:
    """Drop the raw state block the synthetic trigger body carries.

    The live trigger is a rendered comment whose state sits in a collapsed
    ``<details>`` that the prompt strips; replay's rebuilt body has the bare
    markers, so strip those here to keep state JSON out of the plan.
    """
    return _RAW_STATE_BLOCK.sub("", body).strip()


# ── Recorded thread ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReplayThread:
    """An issue and its comments as recorded (GitHub REST shapes)."""

    issue: Optional[Mapping[str, Any]] = None
    comments: tuple[Mapping[str, Any], ...] = ()

    def before(self, comment_id: Optional[int]) -> "ReplayThread":
        """The thread as it stood before ``comment_id`` was posted."""
        if comment_id is None:
            return self
        kept = tuple(
            c for c in self.comments if _numeric_id(c) is None or _numeric_id(c) < comment_id
        )
        return ReplayThread(self.issue, kept)

    def body_of(self, comment_id: int) -> Optional[str]:
        for comment in self.comments:
            if _numeric_id(comment) == comment_id:
                return str(comment.get("body") or "")
        return None


def _numeric_id(comment: Mapping[str, Any]) -> Optional[int]:
    try:
        return int(comment.get("id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def thread_from_text(text: str) -> ReplayThread:
    """Parse ``{"issue": {...}, "comments": [...]}`` or a bare comment list."""
    data = json.loads(text)
    if isinstance(data, list):
        return ReplayThread(None, tuple(data))
    if not isinstance(data, dict):
        raise ValueError("thread JSON must be an object or a list of comments")
    return ReplayThread(data.get("issue"), tuple(data.get("comments") or ()))


# ── Result ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecordedOutput:
    """What the live run posted for this invocation."""

    comment_ids: tuple[int, ...] = ()
    #: State-stripped comment bodies in posting order; empty when unavailable.
    bodies: tuple[str, ...] = ()
    available: bool = False

    def as_dict(self, *, with_content: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "comment_ids": list(self.comment_ids),
            "available": self.available,
            "chars": sum(len(b) for b in self.bodies),
        }
        if with_content:
            data["bodies"] = list(self.bodies)
        return data


@dataclass(frozen=True)
class ContextReplay:
    """The replayed input context and recorded output of one invocation."""

    plan: PromptPlan
    prompt: str
    output: RecordedOutput
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self, *, with_content: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "plan": self.plan.as_dict(with_content=with_content),
            "output": self.output.as_dict(with_content=with_content),
            "notes": list(self.notes),
            "prompt_chars": len(self.prompt),
        }
        if with_content:
            data["prompt"] = self.prompt
        return data


# ── Offline adapter ───────────────────────────────────────────────────────


class _ReplayAdapter:
    """The read-only slice of ``PlatformAdapter`` that prompt assembly uses."""

    def __init__(
        self, prev_state: WorkflowState, thread: ReplayThread, trigger_login: str
    ) -> None:
        self._state = prev_state
        self._thread = thread
        self._login = trigger_login

    def load_state(self) -> WorkflowState:
        return self._state

    def load_raw_thread(self) -> tuple[Any, list[Mapping[str, Any]]]:
        return self._thread.issue, list(self._thread.comments)

    def load_history(self, *, limit: Optional[int] = None) -> list[Any]:
        from nebuko.engine.models import CommentEntry

        entries = [
            CommentEntry(
                author=str((c.get("user") or {}).get("login") or ""),
                body=str(c.get("body") or ""),
                created_at=str(c.get("created_at") or ""),
                comment_id=str(c.get("id")) if c.get("id") is not None else None,
            )
            for c in self._thread.comments
        ]
        if limit is not None:
            entries = entries[-limit:] if limit > 0 else []
        return entries

    def load_context_entries(self) -> list[Any]:
        from nebuko.engine.context_entries import entries_from_raw_thread

        return entries_from_raw_thread(
            self._thread.issue,
            list(self._thread.comments),
            state=self._state,
            trigger_login=self._login,
        )


def _make_engine(adapter: _ReplayAdapter, repo_root: Path, env: Mapping[str, str]):
    from nebuko.engine.run_loop import Engine

    class _ReplayEngine(Engine):
        def _render_previous_handoff(self) -> str:  # type: ignore[override]
            # The handoff store may revive a deleted file into the worktree
            # (a write) and its content is not recorded in state.
            return ""

    return _ReplayEngine(adapter=adapter, repo_root=repo_root, env=dict(env))


# ── Entry point ───────────────────────────────────────────────────────────


def replay_context(
    state: WorkflowState,
    index: int,
    *,
    prev_state: WorkflowState,
    resolved: Mapping[str, Any],
    trigger_body: str,
    thread: Optional[ReplayThread] = None,
    repo_root: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> ContextReplay:
    """Build the prompt plan + recorded output of history entry ``index``."""
    from nebuko.engine.models import Trigger
    from nebuko.engine.run_loop import _TRIGGER_KEY, _RUN_RESOLVED_KEY

    notes: list[str] = []
    entry = state.history[index]
    own_ids = tuple(int(i) for i in (entry.comment_ids or []) if i)

    if thread is None:
        notes.append(
            "no recorded thread (pass --thread FILE, or use --issue): the issue "
            "body and prior comments are absent from the plan"
        )
        visible = ReplayThread()
    else:
        # The invocation saw everything posted before its own output.
        visible = thread.before(min(own_ids) if own_ids else None)
        if not own_ids:
            notes.append("entry records no comment ids: the whole thread is shown")

    started_by = str(getattr(prev_state, "started_by", "") or "")
    issue_body = str((visible.issue or {}).get("body") or "")
    issue_number = int(state.issue_number) if getattr(state, "issue_number", None) else 0
    trigger = Trigger(
        author=started_by or "nebuko[bot]",
        body=_without_state(trigger_body),
        type="issue_comment",
        number=issue_number,
        issue_body=issue_body,
    )

    full = dict(resolved)
    full[_TRIGGER_KEY] = trigger
    adapter = _ReplayAdapter(prev_state, visible, started_by)
    engine = _make_engine(adapter, repo_root or Path.cwd(), env or {})
    engine._run_resolved[_RUN_RESOLVED_KEY] = full

    history = adapter.load_history(limit=engine.history_limit)
    try:
        step_scope = engine._step_output_scope(history)
    except Exception as exc:  # noqa: BLE001 - scope is best-effort context
        notes.append(f"step outputs unavailable: {exc}")
        step_scope = {}
    if full.get("STEP_INPUTS"):
        notes.append("step input providers are not executed: the inputs block is empty")
    notes.append("session handoff is not recorded: that block is omitted")

    plan = engine.plan_prompt(full, history, (), step_scope)
    return ContextReplay(
        plan=plan,
        prompt=render_prompt(plan),
        output=_recorded_output(own_ids, thread),
        notes=tuple(notes),
    )


def _recorded_output(
    ids: Sequence[int], thread: Optional[ReplayThread]
) -> RecordedOutput:
    if not ids or thread is None:
        return RecordedOutput(comment_ids=tuple(ids))
    from nebuko.engine.lib.workflow_state.features.comment.to_git_comment import (
        strip_state_section,
    )

    bodies = [thread.body_of(i) for i in ids]
    if any(b is None for b in bodies):
        return RecordedOutput(comment_ids=tuple(ids))
    return RecordedOutput(
        comment_ids=tuple(ids),
        bodies=tuple(strip_state_section(str(b)) for b in bodies),
        available=True,
    )
