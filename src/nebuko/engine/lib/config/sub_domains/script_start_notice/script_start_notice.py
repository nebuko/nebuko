"""Script-step-start acknowledgment entity (issue #885).

Deterministic ``script`` steps run a repo-local command instead of an agent, so
they were invisible on the issue thread. The engine now mirrors the agent-start
acknowledgment: one "command started, view the logs" line on entry. The notice
lives here as an immutable value object so every script start emits the same
body, independent of which workflow file drives the run.
"""

from pydantic import BaseModel, ConfigDict

from ..brand import brand

# The caller marker that attributes the comment to the script step. It uses a
# distinct ``bot-command-`` prefix (not ``bot-agent-``) so a script comment is
# never counted as an agent turn by ``count_bot_markers`` (issue #885).
_SCRIPT_MARKER = "<!-- bot-command-{step} -->"

# ``{logo}`` is the theme-adaptive brand mark (issue #778); the tail from
# ``**{step}`` onward mirrors the agent-start line's shape.
_NOTICE_LINE = "{logo} **{step} command started** — [View workflow run logs]({run_url})"


class ScriptStartNotice(BaseModel):
    """One script-step-start acknowledgment, plus its canonical rendering.

    Attributes:
        step: Workflow step name running the command (e.g. ``run_tests``).
        run_url: HTML URL of the workflow run to link to.
        issue_number: Issue the notice is posted to; ``None`` skips posting.
    """

    step: str
    run_url: str
    issue_number: int | None = None

    # Frozen: a notice describes one already-started run, so it is never
    # mutated after construction (functional-first value object).
    model_config = ConfigDict(frozen=True)

    def render(self) -> str:
        """Canonical Markdown body posted as the start acknowledgment.

        Carries no ``<!-- workflow-continue -->`` token and no state block: the
        start notice is informational, and a continue token here would re-trigger
        the workflow gate before the command has finished.
        """
        return "\n".join(
            [
                _SCRIPT_MARKER.format(step=self.step),
                _NOTICE_LINE.format(
                    logo=brand().logo_mark(), step=self.step, run_url=self.run_url
                ),
            ]
        )
