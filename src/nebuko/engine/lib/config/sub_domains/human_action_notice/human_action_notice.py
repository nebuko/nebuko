"""Engine-owned human-action notification entity (issue #795).

Agents report their results; they never ping a human. Pinging a human is an
orchestration concern: only the engine deterministically knows that a workflow
*stopped* — at a ``human_gate`` hold, at a plain hold, or at its terminal step
— and only the engine knows who started the workflow. The notice therefore
lives here as an immutable value object, mirroring ``AgentStartNotice`` (the
other engine-authored comment), so every stop emits the same ping and no agent
prompt can reintroduce the responsibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from workflow_state import TurnOutcome

# The universal bot marker (line 1) keeps bot detection exact and routes the
# engine's own comment to the no-op bot path, so the notice can never re-enter
# the workflow it just stopped.
_BOT_MARKER = "<!-- bot-workflow-terminal -->"

# Byte-stable extraction anchor (mirrors the ``bot-agent-*`` convention): read
# by tooling/debug prompts to locate the engine's human ping.
_HUMAN_ACTION_MARKER = "<!-- bot-human-action -->"

AWAITING_APPROVAL = "awaiting_approval"
AWAITING_INPUT = "awaiting_input"
WORKFLOW_COMPLETE = "workflow_complete"
HOLD = "hold"

#: The three kinds of stop that owe a human a notification.
KINDS = frozenset({AWAITING_APPROVAL, AWAITING_INPUT, WORKFLOW_COMPLETE})

# ``{ping}`` is the ``@login`` mention; it is the *only* place a human is
# notified, and the engine owns it.
_LINES = {
    AWAITING_APPROVAL: (
        "{ping} workflow **{workflow}** is waiting for your approval at step "
        "`{step}`. Review the output above, then run `/nebuko resume` to "
        "continue."
    ),
    AWAITING_INPUT: (
        "{ping} workflow **{workflow}** is paused at step `{step}` and needs "
        "your input. Run `/nebuko resume <your feedback>` to continue."
    ),
    WORKFLOW_COMPLETE: (
        "{ping} workflow **{workflow}** has finished at step `{step}`. "
        "Please review the result."
    ),
}


class HumanActionNotice(BaseModel):
    """One engine-authored "human action needed" notification.

    Attributes:
        kind: One of :data:`KINDS` — the classification of the stop.
        target: Resolved human login to ping (workflow starter, issue author,
            or trigger author — see the engine's resolution chain).
        step: Workflow step at which the run stopped.
        workflow: Active workflow name.
        reason: Raw stop reason (e.g. ``human_gate:plan``); recorded verbatim
            but rendered inert by :meth:`render` (see there for why).
    """

    kind: str
    target: str
    step: str = ""
    workflow: str = ""
    reason: Optional[str] = None

    # Frozen: a notice describes one already-finished run (functional-first
    # value object); nothing downstream may rewrite its classification.
    model_config = ConfigDict(frozen=True)

    # @stub-protected
    @classmethod
    def classify(
        cls,
        turn_outcome: Optional["TurnOutcome"],
        *,
        terminal: bool,
        agent_ran: bool,
        target: str,
        step: str,
        workflow: str,
        unresolved: bool = False,
    ) -> Optional["HumanActionNotice"]:
        """Return the notice the finished run demands, or ``None`` (no ping).

        The classification reads the run's factual :class:`TurnOutcome`
        (issue #964), never a navigation status:

        * a ``HOLD`` awaiting approval is an approval request; any other hold
          (awaiting input) is an input request;
        * an ``unresolved`` step — the router found no matching ``on_result``
          edge and no linear successor — is an input request, never a claim
          that the workflow finished (issue #924);
        * a completed verdict routed out of a *terminal* step is the honest
          end-of-workflow notice (``terminal`` is the router's own
          ``DECLARED_FINISH`` verdict);
        * an INCOMPLETE turn, or a verdict routed out of a non-terminal step,
          owes nothing — the workflow continues on its own.

        A notice is emitted only when a target resolves and an agent actually
        produced output (``agent_ran``) or the step was unresolved; otherwise
        ``None``.
        """
        if turn_outcome is None or not target:
            return None
        if not agent_ran and not unresolved:
            return None
        appreciation = getattr(turn_outcome, "appreciation", None)
        appreciation_value = getattr(appreciation, "value", appreciation)
        if appreciation_value == HOLD:
            kind = (
                AWAITING_APPROVAL
                if getattr(getattr(turn_outcome, "hold_kind", None), "value", None)
                == AWAITING_APPROVAL
                else AWAITING_INPUT
            )
        elif unresolved:
            kind = AWAITING_INPUT
        elif terminal and appreciation_value in ("approved", "rejected"):
            kind = WORKFLOW_COMPLETE
        else:
            return None
        return cls(
            kind=kind,
            target=target,
            step=step or "",
            workflow=workflow or "",
            reason=getattr(turn_outcome, "note", "") or None,
        )

    # @stub-protected
    def render(self) -> str:
        """Canonical Markdown body carrying the byte-stable notice marker.

        The ``reason`` is the agent's own dispatch text (or its parse
        diagnostic), so it is passed through the publish path's human-ping
        neutralizer before being emitted. The engine-authored body is otherwise
        exempt from that neutralizer, and without this an agent could smuggle a
        live ``@mention`` into the engine's comment via ``reason`` — exactly the
        responsibility inversion issue #795 removes. Only ``target`` may be a
        live ping.
        """
        body = "\n".join(
            [
                _BOT_MARKER,
                _HUMAN_ACTION_MARKER,
                _LINES[self.kind].format(
                    ping=f"@{self.target}",
                    workflow=self.workflow,
                    step=self.step,
                ),
            ]
        )
        if self.reason:
            # Deferred: ``prepare_comment`` pulls in ``workflow_state`` and
            # ``validate_output``, which import ``config`` — a module-level
            # import here would be circular.
            from prepare_comment import neutralize_human_pings

            body += f"\n\n_{neutralize_human_pings(self.reason)}_"
        return body
