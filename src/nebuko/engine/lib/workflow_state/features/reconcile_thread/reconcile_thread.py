"""Thread reconciliation feature (issue #872).

Rebuilds the workflow-state thread ledger from the *full* current comment list.
Between runs the engine reviews the whole thread and folds every artifact into a
:class:`ThreadRecord`, keyed by its GitHub id:

* new artifacts are appended (issue body first, then comments in API order);
* records already in the ledger are recomputed from the fetched payload (so a
  re-resolved ``trusted`` flag tracks the current trigger author);
* ids that vanished from the thread are kept for audit with ``deleted=True``.

Pure: no I/O. The caller supplies the fetched issue/comment payloads.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from ...sub_domains.thread_record.thread_record import ThreadRecord, ThreadRecordKind
from ..classify_comment.classify_comment import classify_comment

# The canonical bot markers live in resolve_config; importing the constant keeps
# the ledger's notion of "bot-authored" identical to the rest of the engine's.
# ``is_bot_comment`` is deliberately *not* reused here: it scans the first lines
# for a bare marker substring, so it also fires on a human quote-reply (which
# copies the marker verbatim) — the ledger needs the stricter top-level check
# below (see :func:`_has_top_level_bot_marker`).
from nebuko.engine.lib.resolve_config.features.parse_trigger.parse_trigger import UNIVERSAL_BOT_MARKER

__all__ = ["reconcile_thread", "DEFAULT_TRUST_ALLOWLIST"]

# Repository-trusted associations (mirrors the CI caller-auth gate and the
# AuthorIdentity gate allowlist).
DEFAULT_TRUST_ALLOWLIST: tuple[str, ...] = ("OWNER", "MEMBER", "COLLABORATOR")


def reconcile_thread(
    records: list[ThreadRecord],
    *,
    issue: Optional[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    trigger_user_id: int,
    trigger_login: str = "",
    allowlist: tuple[str, ...] = DEFAULT_TRUST_ALLOWLIST,
    engine_actor_id: int = 0,
    engine_actor_login: str = "",
) -> list[ThreadRecord]:  # @stub-protected
    """Fold the current thread into the ledger (see the module docstring).

    Args:
        records: The ledger carried forward from the previous state.
        issue: The raw GitHub issue object, or ``None`` when unavailable.
        comments: The full raw comment list, oldest first.
        trigger_user_id: GitHub user id of the human who triggered this run.
            Preferred trust match; pass ``0`` (and rely on ``trigger_login``, or
            trust nobody) when unknown.
        trigger_login: Login of the run's initiator, used as the trust match when
            ``trigger_user_id`` is unknown (fail-closed when both are empty).
        allowlist: Author associations eligible for trust.
        engine_actor_id: GitHub user id the engine posts as (the PAT owner),
            resolved via ``GET /user``. Anchor for body-marker bot-ness: pass
            ``0`` when unknown (markers then fail closed to the human path).
        engine_actor_login: Login the engine posts as; the login fallback for
            the bot-ness anchor when ``engine_actor_id`` is unknown.

    Returns:
        The reconciled ledger: present artifacts in thread order, then any
        vanished records flagged ``deleted``.
    """
    ordered: list[ThreadRecord] = []
    seen: set[int] = set()

    if issue is not None:
        _append(
            ordered,
            seen,
            _record_from_issue(issue, trigger_user_id, trigger_login, allowlist),
        )
    for comment in comments:
        _append(
            ordered,
            seen,
            _record_from_comment(
                comment,
                trigger_user_id,
                trigger_login,
                allowlist,
                engine_actor_id,
                engine_actor_login,
            ),
        )

    present = {record.id for record in ordered}
    for record in records:
        if record.id in present or record.id in seen:
            continue
        seen.add(record.id)
        ordered.append(
            record if record.deleted else record.model_copy(update={"deleted": True})
        )
    return ordered


def _append(
    ordered: list[ThreadRecord], seen: set[int], record: ThreadRecord
) -> None:
    """Append ``record`` unless its id was already folded in (dedup by id)."""
    if record.id in seen:
        return
    seen.add(record.id)
    ordered.append(record)


def _record_from_issue(
    issue: Mapping[str, Any],
    trigger_user_id: int,
    trigger_login: str,
    allowlist: tuple[str, ...],
) -> ThreadRecord:
    """Build the ``issue-body`` record from the raw issue object."""
    user_id = _user_id(issue)
    association = str(issue.get("author_association") or "")
    return ThreadRecord(
        kind=ThreadRecordKind.ISSUE_BODY,
        id=_int(issue.get("id")),
        url=str(issue.get("html_url") or ""),
        user_id=user_id,
        user_login=_user_login(issue),
        created_at=str(issue.get("created_at") or ""),
        author_association=association,
        trusted=_is_trusted(
            association, user_id, _user_login(issue), trigger_user_id, trigger_login, allowlist
        ),
    )


def _record_from_comment(
    comment: Mapping[str, Any],
    trigger_user_id: int,
    trigger_login: str,
    allowlist: tuple[str, ...],
    engine_actor_id: int,
    engine_actor_login: str,
) -> ThreadRecord:
    """Build a comment record, classifying its kind and trust."""
    user_id = _user_id(comment)
    login = _user_login(comment)
    association = str(comment.get("author_association") or "")
    return ThreadRecord(
        kind=classify_comment(
            str(comment.get("body") or ""),
            author_is_bot=_author_is_bot(
                comment, engine_actor_id, engine_actor_login
            ),
        ),
        id=_int(comment.get("id")),
        url=str(comment.get("html_url") or ""),
        user_id=user_id,
        user_login=login,
        created_at=str(comment.get("created_at") or ""),
        author_association=association,
        trusted=_is_trusted(
            association, user_id, login, trigger_user_id, trigger_login, allowlist
        ),
    )


def _is_trusted(
    association: str,
    user_id: int,
    login: str,
    trigger_user_id: int,
    trigger_login: str,
    allowlist: tuple[str, ...],
) -> bool:
    """Trust only allowlisted human feedback from the workflow trigger author.

    Fail-closed: when neither the trigger id nor the trigger login is known, no
    one is trusted, so a comment from a non-triggering account can never reach
    the model (prompt-injection guard, issue #872).
    """
    if association not in allowlist:
        return False
    if trigger_user_id > 0 and user_id == trigger_user_id:
        return True
    return bool(trigger_login) and login == trigger_login


def _user_block(item: Mapping[str, Any]) -> Mapping[str, Any]:
    user = item.get("user")
    return user if isinstance(user, Mapping) else {}


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _user_id(item: Mapping[str, Any]) -> int:
    return _int(_user_block(item).get("id"))


def _user_login(item: Mapping[str, Any]) -> str:
    return str(_user_block(item).get("login") or "")


def _author_is_bot(
    comment: Mapping[str, Any], engine_actor_id: int, engine_actor_login: str
) -> bool:
    """True when the comment's author is bot-generated (issue #872).

    The canonical signal is the body's own bot marker (the universal
    ``<!-- bot-workflow-terminal -->`` or a line-anchored ``<!-- bot-agent-* -->``
    token in the first five lines), because in this deployment the PAT owner
    posts every comment as ``type == "User"`` — a ``user.type``/``[bot]`` check
    therefore never fires and the agent/engine buckets go dead.

    But the marker alone is forgeable: any public commenter can type a
    top-level bot marker. So the body-marker signal is **anchored to the posting
    account** — it is honoured only when the comment author is a genuine bot
    account (``user.type == "Bot"`` / ``[bot]`` login) *or* it is the engine's
    own actor (the PAT resolved via ``GET /user``). When the marker matches but
    the account does not, the comment falls back to the human path
    (:func:`classify_comment` with ``author_is_bot=False``), so the existing
    ``_is_trusted`` gate + unverified appendix handle it (issue #872 review,
    prompt-injection guard).

    A *top-level* marker is required: GitHub's quote-reply copies a quoted
    comment verbatim (marker included) into the reply body, so a human quoting an
    agent answer must not be read as its author.
    """
    if _user_is_bot(comment):
        return True
    if not _has_top_level_bot_marker(str(comment.get("body") or "")):
        return False
    return _is_engine_actor(comment, engine_actor_id, engine_actor_login)


def _is_engine_actor(
    comment: Mapping[str, Any], engine_actor_id: int, engine_actor_login: str
) -> bool:
    """True when the comment author is the engine's own posting account.

    Resolved from ``GET /user`` for this token: the PAT owner (and, for a true
    GitHub App, its bot identity). Falls back to the login when the numeric id
    is unknown, and fails closed when neither is known — an unanchored marker
    must never grant bot status.
    """
    user_id = _user_id(comment)
    if engine_actor_id > 0 and user_id == engine_actor_id:
        return True
    return bool(engine_actor_login) and _user_login(comment) == engine_actor_login


def _has_top_level_bot_marker(body: str) -> bool:
    """True when ``body`` carries a bot marker that is not inside a quote block.

    Only the author's own (unquoted) lines count: a line carrying a leading
    ``>`` is quoted text, so a marker copied there by GitHub's quote-reply cannot
    impersonate a bot author. The universal marker or the legacy agent marker is
    honoured when it appears in the first five unquoted lines (mirroring the
    window used by :func:`has_universal_bot_marker` / :func:`is_bot_comment`).
    """
    for line in body.split("\n")[:5]:
        stripped = line.lstrip()
        if stripped.startswith(">"):
            continue
        if UNIVERSAL_BOT_MARKER in stripped or stripped.startswith("<!-- bot-agent-"):
            return True
    return False


def _user_is_bot(item: Mapping[str, Any]) -> bool:
    """True for a bot account (``type == "Bot"`` or an ``[bot]`` login suffix)."""
    block = _user_block(item)
    if str(block.get("type") or "") == "Bot":
        return True
    return _user_login(item).endswith("[bot]")
