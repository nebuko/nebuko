"""Shared "did this run state a verdict?" predicate (issues #524, #964, #1033).

Both the termination-path recall gate (``run_agent``) and the output-time
resolution (``build_output._resolve_outcome``) need to answer the same
question: *did the agent state a turn outcome?* A verdict exists when EITHER
structured channel produced one:

1. a ``finalize`` turn outcome (the primary signal), or
2. a typed agent-to-agent hand-off recorded without one (a partial write,
   issue #876).

The legacy ``<!-- AGENT_RUN_CONFIG ... -->`` sentinel is **not** a source
(issue #964): it is agent-rendered prose, and routing is engine truth.
Defining the predicate once guarantees the two call sites cannot drift apart.

This module is deliberately leaf-level (stdlib + the typed registries) so it
can be imported from anywhere without cycles.
"""

from __future__ import annotations

import os
from typing import Optional

from openhands.sdk import get_logger

from nebuko.engine.lib.agent_script.features.dispatch_next_tool import get_recorded_turn_outcome
from nebuko.engine.lib.agent_script.features.call_agent_tool import get_recorded_function_call

logger = get_logger(__name__)


def has_dispatch_decision(conversation, final_message: Optional[str]) -> bool:
    """True when the run already stated a turn outcome.

    Mirrors ``build_output._resolve_outcome``'s priority: a recorded
    ``finalize`` turn outcome wins; a recorded typed hand-off is itself a
    decision (issue #876). All sources empty means no verdict yet, so the
    recall pass runs.

    Args:
        conversation: Object exposing the run id (``conversation.id``); the
            registry is keyed by ``str(id)``.
        final_message: Unused — kept for call-site compatibility; the legacy
            sentinel is no longer scanned (issue #964).
    """
    conv_id = getattr(conversation, "id", None)
    if conv_id is not None:
        if get_recorded_turn_outcome(conv_id) is not None:
            return True
        if get_recorded_function_call(conv_id) is not None:
            # A recorded hand-off is itself the turn's outcome (issue #876) —
            # suppress the recall pass, same as a finalize turn outcome.
            return True
    return False


def recorded_is_approved(conversation) -> bool:
    """True when the run's recorded verdict is ``approved``.

    The publication guard must only rescue steps the agent *completed*: a
    rejected or hold verdict ends the step on purpose, and its dirty tree is
    not an error. Uses the same sources as :func:`has_dispatch_decision`
    (``build_output._resolve_outcome``'s priority), so the guard cannot drift
    from the verdict the engine actually routes on.

    A typed hand-off recorded without a turn outcome counts as approved —
    ``build_output`` resolves that partial write to an APPROVED completion.
    """
    conv_id = getattr(conversation, "id", None)
    if conv_id is None:
        return False
    outcome = get_recorded_turn_outcome(conv_id)
    if outcome is not None:
        appreciation = getattr(outcome, "appreciation", None)
        return getattr(appreciation, "value", appreciation) == "approved"
    return get_recorded_function_call(conv_id) is not None


def agent_to_function_call(agent: str) -> dict:
    """Build the function-call spec for an explicit agent target.

    Cross-agent targeting flows through ``function_call`` in the routing chain,
    so the tool path must keep that state field populated when it names a
    target. An unknown roster name (typo) is logged but still passed through:
    downstream resolution degrades to a clear error rather than silently
    dropping the target.
    """
    _warn_unknown_agent(agent)
    return {"function": "call_agent", "agent_name": agent}


_config_names_cache: Optional[frozenset] = None


def _agent_roster_names() -> frozenset:
    """Names of the agents registered in ``agent-config.yaml`` (cached).

    Loads relative to this package so CWD doesn't matter. Any failure (missing
    file, unresolvable refs, bad schema) yields an empty roster and validation
    degrades to a no-op — the check must never break a run over its own config.
    """
    global _config_names_cache
    if _config_names_cache is not None:
        return _config_names_cache
    try:
        import nebuko.engine.lib.default_paths
        from nebuko.engine.lib.config import load_config

        config = load_config(default_paths.agent_config_file())
        _config_names_cache = frozenset(config.agents.keys())
    except Exception as exc:  # noqa: BLE001 - validation must be non-fatal
        logger.debug(f"Could not load agent roster for dispatch validation: {exc}")
        _config_names_cache = frozenset()
    return _config_names_cache


def _warn_unknown_agent(agent: str) -> None:
    """Log a warning when a dispatch target is not in the agent roster."""
    roster = _agent_roster_names()
    if roster and agent not in roster:
        env_role = os.environ.get("ROLE_NAME", "")
        logger.warning(
            f"finalize named unknown agent '{agent}' "
            f"(role={env_role or '-'}, roster={sorted(roster)}) — "
            "target may be a typo; downstream routing will reject it."
        )
