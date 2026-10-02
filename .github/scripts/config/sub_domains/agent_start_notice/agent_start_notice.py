"""Agent-start acknowledgment entity (issue #720).

The Stage B cutover replaced the monolith's ``Post Acknowledgment Comment``
bash step with the engine, and the notice was dropped: an agent run no longer
posted its "started, view the logs" comment. The notice now lives here as an
immutable value object so that every agent-start emits the same body,
independent of which workflow file drives the run.
"""

from pydantic import BaseModel, ConfigDict

# The caller marker the legacy guard (``run_loop._caller_agent``) reads to
# attribute a comment to the agent that posted it. Kept local so the value
# object stays a pure payload (no GitHub adapter import); the universal
# ``<!-- bot-workflow-terminal -->`` marker is injected by the comment client.
_AGENT_MARKER = "<!-- bot-agent-{agent} -->"

_NOTICE_LINE = "🤖 **{role} agent started** — [View workflow run logs]({run_url})"


class AgentStartNotice(BaseModel):
    """One agent-start acknowledgment, plus its canonical rendering.

    Attributes:
        agent: Agent name driving the caller marker (e.g. ``dev``).
        role: Role shown in the message (e.g. ``dev``).
        run_url: HTML URL of the workflow run to link to.
        issue_number: Issue the notice is posted to; ``None`` skips posting.
    """

    agent: str
    role: str
    run_url: str
    issue_number: int | None = None

    # Frozen: a notice describes one already-started run, so it is never
    # mutated after construction (functional-first value object).
    model_config = ConfigDict(frozen=True)

    def render(self) -> str:
        """Canonical Markdown body posted as the acknowledgment comment."""
        return "\n".join(
            [
                _AGENT_MARKER.format(agent=self.agent),
                _NOTICE_LINE.format(role=self.role, run_url=self.run_url),
            ]
        )
