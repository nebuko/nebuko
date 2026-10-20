"""Typed thread entries for context selection (issue #889).

:class:`ThreadEntryRepository` turns the host's raw ``(issue, comments)``
payload into the forward-ordered, typed :class:`ContextEntry` list that
:mod:`nebuko.engine.context_selection` selects from. It is read-only: the thread
stays the source of truth for entry order, and every artifact is tagged with

* ``kind`` — from the ledger classifier (:mod:`classify_comment`), with an
  untrusted human comment folded into ``HUMAN_COMMENT`` so it can still reach
  the prompt through the unverified appendix;
* ``step`` / ``round`` — the workflow identity of an agent answer, read from the
  workflow-state block it carries (a state-less answer inherits the identity of
  the previous state-carrying comment, since the engine attributes it to the
  advancing step);
* ``locator`` — the comment id of the answer's state-bearing first part when the
  answer is one of several comments (issue #913). The part ids are recorded on
  the advancing step's ``workflow_state.history`` entry (``comment_ids``, issue
  #504), the path's source of truth, so the split does not change an entry's id;
* ``parts`` — the rows of one logical agent answer. A split answer is posted as
  N comments; only part 1 carries the header and state, so selection sees one
  entry for the answer and renders every part.

Engine bookkeeping (``RUN_META`` / ``WORKFLOW_STATE``) is kept in the list so
the builder can prove it is excluded; it is never selectable.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from .context_selection import ContextEntry

__all__ = ["ThreadEntryRepository", "entries_from_raw_thread"]


class ThreadEntryRepository:
    """Typed, forward-ordered, read-only view of one issue's thread."""

    def __init__(self, entries: Sequence[ContextEntry]) -> None:
        self._entries = list(entries)

    @classmethod
    def from_raw_thread(
        cls,
        issue: Mapping[str, Any] | None,
        comments: Sequence[Mapping[str, Any]],
        *,
        state: Any = None,
    ) -> "ThreadEntryRepository":
        return cls(entries_from_raw_thread(issue, comments, state=state))

    def list_entries(self) -> list[ContextEntry]:  # @stub-protected
        """Every entry, ordered by thread sequence (oldest first)."""
        return list(self._entries)


def _kind_of(body: str, *, author_is_bot: bool) -> str:
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from workflow_state.features.classify_comment.classify_comment import (
        classify_comment,
    )

    classified = classify_comment(body, author_is_bot=author_is_bot)
    raw = str(getattr(classified, "value", classified))
    if raw in ("command", "human-feedback"):
        return "human-comment"
    if raw == "engine-output":
        return "run-meta"
    return raw


def _state_of(body: str) -> Any:
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from workflow_state import extract_state_from_body
    from workflow_state.features.comment.to_git_comment import (
        STATE_MARKER_END,
        STATE_MARKER_START,
    )

    # The first state block in a body is the one the comment advanced to (a
    # quoted earlier state follows it), which is this answer's own identity.
    match = re.search(
        rf"{re.escape(STATE_MARKER_START)}\s*\n(.*?)\n\s*{re.escape(STATE_MARKER_END)}",
        body or "",
        re.S,
    )
    if match is not None:
        try:
            from workflow_state.workflow_state import WorkflowState

            return WorkflowState.model_validate(json.loads(match.group(1).strip()))
        except Exception:  # noqa: BLE001 - fall back to the canonical extractor
            pass
    return extract_state_from_body(body)


def _output_identity(body: str) -> tuple[str | None, int | None]:
    """The ``(step, round)`` an agent answer was produced for.

    The answer's own workflow-state blocks name the step (both the state it
    advances to and the locator it carries), and the ledger classifier names the
    answering agent as the fallback. A re-run of a step is its later occurrence
    in ``history`` — ``round`` is not persisted, so it is derived from the path.
    """
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from workflow_state.step_output_record import StepOutputRecord

    state = _state_of(body)
    history = list(getattr(state, "history", None) or []) if state is not None else []
    records = (
        StepOutputRecord.load_all(dict(state.current_context or {}))
        if state is not None
        else []
    )
    answerer = _author_of(body)
    state_step = str(getattr(state, "step", "") or "")
    for candidate in (state_step, answerer, *(record.step for record in reversed(records))):
        # Without a state the answerer is not a workflow identity: it names the
        # agent (``dev``), not the step, so it can only be the fallback when a
        # state block names it too.
        if not candidate or (candidate == answerer and not state_step and state is None):
            continue
        if candidate == answerer or any(node.step == candidate for node in history) or any(
            record.step == candidate for record in records
        ):
            return candidate, 1 + sum(
                1 for node in history if node.step == candidate
            )
    return None, None


def _author_of(body: str) -> str:
    match = re.search(r"<!-- bot-agent-([a-z0-9\-]+) -->", body or "")
    return match.group(1) if match else ""


def _answer_groups(
    comments: Sequence[Mapping[str, Any]], state: Any, engine_login: str = ""
) -> dict[str, list[str]]:
    """Map an answer's state-bearing comment id to its part ids (issue #913).

    ``*(Part i/N)*`` comments are continuation fragments of one answer. The
    owning step's ``history`` entry records every part id (issue #504), so the
    path is the source of truth for "which comments are one answer". A thread
    whose state recorded none (no persisted state, legacy blob) falls back to
    adjacency: the contiguous fragments posted by the engine account itself,
    which are the only rows it can have appended to one of its answers. The
    marker only tells a fragment apart from an ordinary comment.
    """
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from workflow_state.features.get_last_state.get_last_state import _is_part_comment

    known: dict[str, list[str]] = {}
    for entry in getattr(state, "history", None) or []:
        ids = [str(cid) for cid in (getattr(entry, "comment_ids", None) or [])]
        if ids:
            known[ids[0]] = ids
    groups: dict[str, list[str]] = {}
    owner: str | None = None
    owner_author: str | None = None
    for comment in comments:
        cid = comment.get("id")
        if cid is None:
            continue
        cid = str(cid)
        author = _comment_author(comment)
        if _is_part_comment(comment) and not _is_first_part(comment.get("body") or ""):
            linked = owner is not None and (
                cid in known.get(owner, ())
                or (
                    owner not in known
                    and author == owner_author
                    and bool(engine_login)
                    and author == engine_login
                )
            )
            if linked:
                groups[owner].append(cid)
            else:
                # An owner-less fragment (an engine-start notice posted from
                # inside the agent child, or a lone orphan) is its own entry: it
                # must never inherit, or extend, another answer.
                groups[cid] = [cid]
            continue
        owner = cid
        owner_author = author
        groups[cid] = list(known.get(cid, [cid]))
    return groups


def _is_first_part(body: str) -> bool:
    """True for a ``*(Part 1/N)*`` header: the answer's first, state-bearing row.

    ``_is_part_comment`` only requires a part header, so it also matches part 1;
    that row opens an answer, it never continues one.
    """
    match = re.search(r"^\s*\*\(Part (\d+)/\d+\)\*\s*$", body, re.MULTILINE)
    return match is not None and match.group(1) == "1"


def _comment_author(comment: Mapping[str, Any]) -> str:
    user = comment.get("user")
    user = user if isinstance(user, Mapping) else {}
    return str(user.get("login") or "")


def entries_from_raw_thread(  # @stub-protected
    issue: Mapping[str, Any] | None,
    comments: Sequence[Mapping[str, Any]],
    *,
    engine_login: str = "",
    state: Any = None,
) -> list[ContextEntry]:
    """Build the typed entries for one thread (pure except for the classifier).

    ``engine_login`` names the account the engine posts as; a comment authored
    by it (or by a bot account) may carry agent/engine markers. Every other
    comment is human, so a human quoting a bot marker stays human (issue #872).

    ``state`` is the previous workflow state: its ``history`` records each
    answer's part ids, so a split answer becomes one entry (``parts``) naming its
    state-bearing first comment instead of N unrelated rows.
    """
    groups = _answer_groups(comments, state, engine_login)
    entries: list[ContextEntry] = []
    sequence = 0
    carried_step: str | None = None
    carried_round: int | None = None
    seen_parts: set[str] = set()
    if issue is not None:
        entries.append(
            ContextEntry(
                kind="issue-body",
                sequence=sequence,
                body=str(issue.get("body") or ""),
                source_comment_id=str(issue.get("id") or "") or None,
            )
        )
        sequence += 1
    for comment in comments:
        cid = comment.get("id")
        cid = str(cid) if cid is not None else ""
        if cid and cid in seen_parts:
            continue
        sequence += 1
        body = str(comment.get("body") or "")
        user = comment.get("user")
        user = user if isinstance(user, Mapping) else {}
        login = str(user.get("login") or "")
        author_is_bot = str(user.get("type") or "").lower() == "bot" or (
            bool(engine_login) and login == engine_login
        )
        kind = _kind_of(body, author_is_bot=author_is_bot)
        parts = list(groups.get(cid, [cid])) if cid else []
        seen_parts.update(part for part in parts if part and part != cid)
        step: str | None = None
        round_number: int | None = None
        entry_state = _state_of(body)
        if entry_state is None:
            # A state-less answer inherits the identity of the previous
            # state-carrying comment: the engine attributes it to the advancing
            # step.
            step, round_number = carried_step, carried_round
        else:
            step, round_number = _output_identity(body)
            if step is not None:
                carried_step, carried_round = step, round_number
        entries.append(
            ContextEntry(
                kind=kind,
                sequence=sequence,
                body=body,
                step=step,
                round=round_number,
                source_comment_id=cid or None,
                trusted=True,
                parts=None if len(parts) < 2 else tuple(parts),
            )
        )
    return entries
