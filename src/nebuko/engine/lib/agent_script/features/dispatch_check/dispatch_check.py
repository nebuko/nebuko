"""Shared "does this run have a dispatch decision?" predicate (issue #524).

Both the termination-path recall gate (``run_agent``) and the output-time
dispatch resolution (``build_output._resolve_dispatch``) need to answer the
same question: *was a next-step decision made this run?* A decision exists
when EITHER source produced one:

1. a ``dispatch_next`` tool call (the structured, primary signal), or
2. a parseable legacy ``<!-- AGENT_RUN_CONFIG ... -->`` sentinel in the final
   message (silent compat fallback during the dual-mode migration window).

Defining the predicate once guarantees the two call sites cannot drift apart.
If they did — e.g. the recall gate only checked the tool registry — every
sentinel-only legacy run would trigger needless recall turns, inflating
``dispatch_recall_count`` and risking a late dispatch overriding the intended
final decision.

This module is deliberately leaf-level (stdlib + ``validate_output`` + the
dispatch registry) so it can be imported from anywhere without cycles.
"""

from __future__ import annotations

import os
from typing import Optional

from openhands.sdk import get_logger

from agent_script.features.build_output.build_output import (
    agent_run_config_to_dispatch,
)
from agent_script.features.dispatch_next_tool import get_recorded_dispatch
from agent_script.features.call_agent_tool import get_recorded_function_call
from validate_output import extract_dispatch_resilient

logger = get_logger(__name__)


def has_dispatch_decision(conversation, final_message: Optional[str]) -> bool:
    """True when the run already produced a dispatch decision.

    Mirrors ``build_output._resolve_dispatch``'s priority: a recorded
    ``dispatch_next`` tool call wins; a recorded typed ``call_agent`` hand-off
    is itself a decision (issue #876); otherwise a legacy sentinel in the
    final message counts only if it both parses AND maps to a valid
    ``Dispatch`` (identical to the resolver — a present-but-unparseable or
    invalid sentinel does NOT count, so those runs still get recalled).
    All sources empty means no decision yet.

    Args:
        conversation: Object exposing the run id (``conversation.id``); the
            registry is keyed by ``str(id)``.
        final_message: The agent's final message text, or None when no text is
            available to scan (tool call still checked).
    """
    conv_id = getattr(conversation, "id", None)
    if conv_id is not None:
        if get_recorded_dispatch(conv_id) is not None:
            return True
        if get_recorded_function_call(conv_id) is not None:
            # A typed call_agent hand-off is itself the routing decision
            # (issue #876) — suppress the recall pass, same as dispatch_next.
            return True
    if not final_message:
        return False
    extract_result = extract_dispatch_resilient(final_message)
    if extract_result.payload is None:
        return False
    return agent_run_config_to_dispatch(extract_result.payload) is not None


def agent_to_function_call(agent: str) -> dict:
    """Build the ``call_agent`` function_call spec for an explicit agent target.

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
        import default_paths
        from config import load_config

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
            f"dispatch_next named unknown agent '{agent}' "
            f"(role={env_role or '-'}, roster={sorted(roster)}) — "
            "target may be a typo; downstream routing will reject it."
        )
