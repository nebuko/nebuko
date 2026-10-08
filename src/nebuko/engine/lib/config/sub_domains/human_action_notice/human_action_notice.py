"""Engine-owned human-action notification entity (issue #795).

Agents report their results; they never ping a human. Ping a human is an
orchestration concern: only the engine deterministically knows that a workflow
*stopped* — at a ``human_gate`` hold, at a plain hold, or at its terminal step
— and only the engine knows who started the workflow. The notice therefore
lives here as an immutable value object, mirroring ``AgentStartNotice`` (the
other engine-authored comment), so every stop emits the same ping and no agent
prompt can reintroduce the responsibility.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

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

#: The three kinds of stop that owe a human a notification.
KINDS = frozenset({AWAITING_APPROVAL, AWAITING_INPUT, WORKFLOW_COMPLETE})

# Only these run statuses demand a human: ``hold`` (the workflow is paused) and
# ``terminate`` (the workflow ended). Everything else — advance/loop/soft_kill —
# either continues on its own or is handled by the resolver on a later trigger.
_STOP_STATUSES = {"hold", "terminate"}

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
        reason: Raw stop reason (e.g. ``human_gate:plan``), recorded verbatim.
    """

    kind: str
    target: str
    step: str = ""
    workflow: str = ""
    reason: Optional[str] = None

    # Frozen: a notice describes one already-finished run (functional-first
    # value object); nothing downstream may rewrite its classification.
    model_config = ConfigDict(frozen=True)

    @classmethod
    def classify(
        cls,
        outcome_status: str,
        reason: Optional[str],
        *,
        agent_ran: bool,
        target: str,
        step: str,
        workflow: str,
    ) -> Optional["HumanActionNotice"]:
        """Return the notice the finished run demands, or ``None`` (no ping).

        A notice is emitted only when the run stopped for a human *and* an
        agent actually produced output for them to act on. A guard/terminal
        skip (``agent_ran=False``) produced nothing, and a run with no
        resolvable target cannot notify anyone, so both return ``None``.

        A ``hold`` whose reason names a ``human_gate`` is an approval request;
        any other hold (explicit hold, no-action default) is an input request.
        A ``terminate`` is end-of-workflow.
        """
        status = (outcome_status or "").strip()
        if status not in _STOP_STATUSES:
            return None
        if not agent_ran or not target:
            return None
        if status == "hold":
            kind = (
                AWAITING_APPROVAL
                if (reason or "").startswith("human_gate")
                else AWAITING_INPUT
            )
        else:
            kind = WORKFLOW_COMPLETE
        return cls(
            kind=kind,
            target=target,
            step=step or "",
            workflow=workflow or "",
            reason=reason,
        )

    def render(self) -> str:
        """Canonical Markdown body carrying the byte-stable notice marker."""
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
            body += f"\n\n_{self.reason}_"
        return body
