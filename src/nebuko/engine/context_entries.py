"""Typed thread entries for context selection (issue #889).

:class:`ThreadEntryRepository` turns the host's raw ``(issue, comments)``
payload into the forward-ordered, typed :class:`ContextEntry` list that
:mod:`nebuko.engine.context_selection` selects from. It is read-only: the thread
stays the source of truth for entry order, and every artifact is tagged with

* ``kind`` — from the ledger classifier (:mod:`classify_comment`), with an
  untrusted human comment folded into ``HUMAN_COMMENT`` so it still reaches
  the prompt, truncated, through the unverified appendix;
* ``step`` / ``round`` — the workflow identity of an agent answer, read from the
  workflow-state block it carries (a state-less answer inherits the identity of
  the previous state-carrying comment, since the engine attributes it to the
  advancing step);
* ``trusted`` — the ledger's trust verdict for a human row (allowlisted
  association of the run's initiator). Engine rows are always trusted; an
  untrusted human row is never rendered in full (issue #872);
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

__all__ = ["ThreadEntryRepository", "author_is_bot", "entries_from_raw_thread"]


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
        engine_login: str = "",
    ) -> "ThreadEntryRepository":
        return cls(
            entries_from_raw_thread(
                issue, comments, state=state, engine_login=engine_login
            )
        )

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

            # The block is collapsed in the rendered comment, so the JSON may be
            # cut short; history (with the answer's part ids, issue #504)
            # still parses from a truncated tail.
            return WorkflowState.model_validate(
                json.loads(match.group(1).strip(), strict=False)
            )
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
    comments: Sequence[Mapping[str, Any]],
    state: Any,
    engine_login: str = "",
    engine_actor_id: int = 0,
) -> dict[str, list[str]]:
    """Map an answer's state-bearing comment id to its part ids (issue #913).

    ``*(Part i/N)*`` comments are continuation fragments of one answer. The
    owning step's ``history`` entry records every part id (issue #504), so the
    path is the source of truth for "which comments are one answer". A thread
    whose state recorded none (no persisted state, legacy blob), or whose
    ``history`` lists no ``comment_ids`` at all, falls back to adjacency: the
    contiguous fragments the ledger classifies as agent answers, posted by the
    engine account itself, which are the only rows it can have appended to one
    of its answers. An engine-start notice (``RUN_META``) is never a fragment,
    and the marker only tells a fragment apart from an ordinary comment.
    """
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from workflow_state.features.get_last_state.get_last_state import _is_part_comment

    bot_options = {
        "engine_login": engine_login,
        "engine_actor_id": engine_actor_id,
        "engine_actor_login": engine_login,
    }
    known: dict[str, list[str]] = {}
    part_order: dict[str, int] = {}
    if state is not None:
        for entry in getattr(state, "history", None) or []:
            ids = [str(cid) for cid in (getattr(entry, "comment_ids", None) or [])]
            if not ids:
                continue
            known[ids[0]] = ids
            part_order.update({cid: index for index, cid in enumerate(ids)})
    recorded = bool(known)
    groups: dict[str, list[str]] = {}
    owner: str | None = None
    owner_author: str | None = None
    for comment in comments:
        cid = comment.get("id")
        if cid is None:
            continue
        cid = str(cid)
        author = _comment_author(comment)
        body = str(comment.get("body") or "")
        if _is_part_comment(comment) and not _is_first_part(body):
            owner_parts = known.get(owner, ()) if owner is not None else ()
            if cid in owner_parts:
                # Already placed by ``history`` order; the digest's order is not
                # the answer order.
                continue
            if recorded:
                # The history is authoritative even when it lists no parts:
                # adjacency must not re-split the answer on a stale digest.
                linked = False
            else:
                linked = (
                    owner is not None
                    and author == owner_author
                    and bool(engine_login)
                    and author == engine_login
                    and _kind_of(body, author_is_bot=author_is_bot(comment, **bot_options))
                    == "agent-output"
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
    # A recorded answer is posted in ``history`` order, which is not necessarily
    # the order the API returns the comments in (issue #913).
    return {
        owner: sorted(ids, key=lambda cid: part_order.get(cid, len(part_order)))
        if owner in known
        else ids
        for owner, ids in groups.items()
    }


def _answer_claims(groups: Mapping[str, list[str]], cid: str) -> str | None:
    """The answer group ``cid`` belongs to, or ``None`` when it owns its own.

    A continuation row may be listed under its answer's first comment even
    when the two are not adjacent (``_unclaimed_answers`` attaches fragments to
    the nearest prefix answer), so membership is looked up across the groups.
    """
    for owner, parts in groups.items():
        if cid == owner:
            return owner
        if cid in parts:
            return owner
    return None


def _is_part_row(comment: Mapping[str, Any]) -> bool:
    """True for a state-less continuation fragment that continues an answer.

    A ``*(Part i/N)*`` row past part 1 is a continuation regardless of what it
    says: the engine posts one of these for *every* answer, engine bookkeeping
    included (its content is then classified as ``RUN_META``). A row without
    the part header continues only when the ledger reads it as an agent answer
    (it carries the bot marker) — a start notice does not.
    """
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from workflow_state.features.get_last_state.get_last_state import _is_part_comment

    body = str(comment.get("body") or "")
    if _is_part_comment(comment) and not _is_first_part(body):
        return True
    return False


def _is_first_part(body: str) -> bool:
    """True for a ``*(Part 1/N)*`` header: the answer's first, state-bearing row.

    ``_is_part_comment`` only requires a part header, so it also matches part 1;
    that row opens an answer, it never continues one.
    """
    match = re.search(r"^\s*\*\(Part (\d+)/\d+\)\*\s*$", body, re.MULTILINE)
    return match is not None and match.group(1) == "1"


def _comment_author(comment: Mapping[str, Any]) -> str:
    user = comment.get("user")
    if not isinstance(user, Mapping) or not user:
        # A local journal row names its author directly.
        return str(comment.get("author") or "")
    return str(user.get("login") or "")


def _comment_user_id(comment: Mapping[str, Any]) -> int:
    user = comment.get("user")
    user = user if isinstance(user, Mapping) else {}
    try:
        return int(user.get("id") or comment.get("user_id") or 0)
    except (TypeError, ValueError):
        return 0


def author_is_bot(
    comment: Mapping[str, Any],
    *,
    engine_login: str = "",
    engine_actor_id: int = 0,
    engine_actor_login: str = "",
) -> bool:
    """True when the comment's author is bot-generated (issue #872).

    Delegates to the ledger's own rule (:func:`reconcile_thread._author_is_bot`),
    so context selection and reconciliation agree on what an agent answer is: a
    genuine bot account, or a *top-level* bot marker anchored to the engine's
    posting account. In the documented deployment every comment is posted by the
    PAT owner as ``type == "User"``, so a ``user.type`` check alone would classify
    every agent answer as human and the prompt would lose its step context.

    ``engine_login`` is shorthand for ``engine_actor_login`` (the account the
    engine posts as).
    """
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from workflow_state.features.reconcile_thread.reconcile_thread import (
        _author_is_bot,
    )

    return _author_is_bot(
        comment, engine_actor_id, engine_actor_login or engine_login
    )


def _author_is_trusted(
    comment: Mapping[str, Any],
    *,
    trigger_user_id: int = 0,
    trigger_login: str = "",
    allowlist: tuple[str, ...] = (),
) -> bool:
    """The ledger's trust verdict for a human comment (issue #872).

    Reuses :func:`reconcile_thread._is_trusted`: only allowlisted human feedback
    from the run's initiator reaches the prompt in full; anyone else is
    quarantined to the unverified appendix and fails closed when the run's
    trigger identity is unknown.
    """
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from workflow_state.features.reconcile_thread.reconcile_thread import (
        DEFAULT_TRUST_ALLOWLIST,
        _is_trusted,
    )

    return _is_trusted(
        str(comment.get("author_association") or ""),
        _comment_user_id(comment),
        _comment_author(comment),
        trigger_user_id,
        trigger_login,
        allowlist or DEFAULT_TRUST_ALLOWLIST,
    )


def entries_from_raw_thread(  # @stub-protected
    issue: Mapping[str, Any] | None,
    comments: Sequence[Mapping[str, Any]],
    *,
    engine_login: str = "",
    engine_actor_id: int = 0,
    trigger_user_id: int = 0,
    trigger_login: str = "",
    state: Any = None,
) -> list[ContextEntry]:
    """Build the typed entries for one thread (pure except for the classifier).

    ``engine_login`` / ``engine_actor_id`` identify the account the engine posts
    as; a comment authored by it (or by a bot account) may carry agent/engine
    markers. Every other comment is human, so a human quoting a bot marker stays
    human (issue #872). ``trigger_user_id`` / ``trigger_login`` are the run's
    initiator: only their allowlisted comments are ``trusted``.

    ``state`` is the previous workflow state: its ``history`` records each
    answer's part ids, so a split answer becomes one entry (``parts``) naming its
    state-bearing first comment instead of N unrelated rows.
    """
    bot_options = {
        "engine_login": engine_login,
        "engine_actor_id": engine_actor_id,
        "engine_actor_login": engine_login,
    }
    groups = _answer_groups(comments, state, engine_login, engine_actor_id)
    if state is None:
        # No persisted state (e.g. the next run's view of a thread whose answers
        # carried it inline): the newest state-bearing comment still records the
        # part ids of its own answer. Engine bookkeeping is grouped next.
        for comment in reversed(list(comments)):
            candidate = _state_of(str(comment.get("body") or ""))
            if candidate is not None:
                groups = _answer_groups(comments, candidate, engine_login, engine_actor_id)
                break
    groups = _unclaimed_answers(groups, comments, engine_login, bot_options)
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
                author=_comment_author(issue),
            )
        )
        sequence += 1
    for comment in comments:
        cid = comment.get("id")
        cid = str(cid) if cid is not None else ""
        group_owner = _answer_claims(groups, cid) if cid else None
        if cid and (cid in seen_parts or (group_owner and group_owner != cid)):
            continue
        sequence += 1
        body = str(comment.get("body") or "")
        is_bot = author_is_bot(comment, **bot_options)
        kind = _kind_of(body, author_is_bot=is_bot)
        entry_state = _state_of(body)
        if (
            kind not in ("run-meta", "workflow-state")
            and cid
            and group_owner
            and group_owner != cid
            and entry_state is not None
        ):
            # A continuation row that already carries a workflow-state block is
            # the answer itself (the split answered from the sub-workflow whose
            # state block is its own); the group's owner names the previous
            # block only. Keep the row as an entry, not as a rendered part.
            group_owner = None
        if kind in ("run-meta", "workflow-state"):
            # Bookkeeping splits like any other answer, but it owns no parts and
            # is never selectable (D2): keep its identity out of the answer
            # sequence.
            parts: list[str] = []
        elif group_owner:
            parts = list(groups[group_owner])
        elif _is_part_row(comment):
            # A continuation fragment that no recorded answer claims but the
            # ledger already classifies as an agent answer (it carries the bot
            # marker): it belongs to the nearest preceding answer. Marking it
            # as a run-meta (engine-start notice) opts it out.
            owner = next(reversed(groups)) if groups else None
            if owner is None:
                parts = [cid]
            else:
                groups[owner].append(cid)
                parts = [cid]
        else:
            parts = [cid] if cid else []
        seen_parts.update(part for part in parts if part and part != cid)
        step: str | None = None
        round_number: int | None = None
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
                author=_comment_author(comment),
                # Engine/agent rows are the engine's own; only a human row can
                # be untrusted (issue #872).
                trusted=is_bot
                or _author_is_trusted(
                    comment,
                    trigger_user_id=trigger_user_id,
                    trigger_login=trigger_login,
                ),
                parts=None if len(parts) < 2 else tuple(parts),
            )
        )
    return entries


def _unclaimed_answers(
    groups: dict[str, list[str]],
    comments: Sequence[Mapping[str, Any]],
    engine_login: str,
    bot_options: Mapping[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Attach continuation rows that no recorded answer claims.

    A thread whose state recorded no part ids (a host that strips the state
    block, a legacy snapshot) still splits an answer across N comments. A
    bot-marked ``*(Part i/N)*`` row the ledger classifies as an agent answer
    belongs to the nearest preceding answer; an engine-start notice classified
    as ``RUN_META`` owns itself and never extends another answer, and
    bookkeeping kinds stay ungrouped (D2).
    """
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    from workflow_state.features.get_last_state.get_last_state import _is_part_comment

    options = dict(bot_options or {"engine_login": engine_login})
    merged = {owner: list(parts) for owner, parts in groups.items()}
    # Only a real multi-part answer claims rows: ``_answer_groups`` lists every
    # row as its own group, which must not read as "already claimed".
    claimed = any(len(parts) > 1 for parts in groups.values())
    owner: str | None = None
    for comment in comments:
        cid = comment.get("id")
        if cid is None:
            continue
        cid = str(cid)
        body = str(comment.get("body") or "")
        if (
            owner is not None
            and not claimed
            and _is_part_comment(comment)
            and not _is_first_part(body)
            and _kind_of(body, author_is_bot=author_is_bot(comment, **options))
            == "agent-output"
        ):
            merged[owner].append(cid)
            continue
        owner = cid
    return merged

