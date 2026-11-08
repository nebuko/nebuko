"""Engine failure notification entity (issue #1288).

A run that crashes *after* the agent's answer was published (in ``persist``,
the PR finalizer or the git finalize) used to leave that answer's
``<!-- workflow-continue -->`` token live: the workflow gate re-fired and the
next step started as if the step had succeeded. The engine now posts this
notice on the thread — step, run URL, phase, error and whether the branch was
pushed — and pings the workflow starter, exactly like the human-action notice
(issue #795) does for a deliberate stop.

The notice lives here as an immutable value object, mirroring
``HumanActionNotice``, so every post-publish failure emits the same body no
matter which workflow file drives the run.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..brand import brand

# The universal bot marker (line 1) keeps bot detection exact and routes the
# engine's own comment to the no-op bot path, so the notice can never
# re-enter the workflow it just stopped.
_BOT_MARKER = "<!-- bot-workflow-terminal -->"

# Byte-stable extraction anchor (mirrors the ``bot-human-action`` convention):
# read by tooling/debug prompts to locate the engine's failure ping.
_FAILURE_MARKER = "<!-- bot-engine-failure -->"

# ``{ping}`` is the ``@login`` mention; it is the only place a human is
# notified, and the engine owns it.
_LINE = (
    "{ping}workflow **{workflow}** failed at step `{step}` "
    "({phase} phase): {error}\n\n"
    "Run: {run_url} — branch pushed: {pushed}.\n"
    "{provider_line}"
    "{aftermath}\n"
    "Fix the failure, then re-run the step with `/nebuko resume`."
)

#: Provider-failure line (issue #1322): names the provider, model and whether a
#: retry can help. Empty for every other engine failure.
_PROVIDER_LINE = "Provider: {provider} — model: {model}.\n"
_CONFIG_FIX_LINE = (
    "Retrying won't help until the provider configuration "
    "(API key, base URL or model) is fixed.\n"
)

# The two aftermath wordings (#1288 round 4): a run that failed before its
# answer was published did not advance the workflow; a run that failed
# *after* the answer comment was created (post-publish merge, route) may
# re-fire the workflow gate on the posted token — the notice must say so,
# not claim the workflow stopped.
_AFTERMATH_NOT_ADVANCED = "The workflow did **not** advance."
_AFTERMATH_MAY_ADVANCE = (
    "The answer comment was already posted; the workflow gate may re-fire on "
    "it. If a next step starts, follow its run instead of resuming."
)


class EngineFailureNotice(BaseModel):
    """One engine-authored "the run failed" notification (issue #1288).

    Attributes:
        step: Workflow step whose run failed.
        workflow: Active workflow name.
        phase: The phase the failure surfaced in (``state``, ``route``, ...).
        error: The exception message, verbatim.
        run_url: HTML URL of the failed workflow run.
        pushed: Whether the branch was pushed before the failure.
        target: Resolved human login to ping (the workflow starter).
        posted: Whether the answer comment was already created when the
            failure surfaced (a post-publish crash) — it selects the honest
            aftermath wording (issue #1288 round 4).
        provider: Provider base URL, when the failure came from a model call
            (issue #1322).
        model: Model identifier of that call (issue #1322).
        requires_config_fix: The provider refused the configuration, so a
            retry cannot help until it is fixed (issue #1322).
    """

    step: str
    workflow: str = ""
    phase: str = ""
    error: str = ""
    run_url: str = ""
    pushed: bool = False
    target: str
    posted: bool = False
    provider: str = ""
    model: str = ""
    requires_config_fix: bool = False

    # Frozen: a notice describes one already-failed run (functional-first
    # value object); nothing downstream may rewrite it.
    model_config = ConfigDict(frozen=True)

    # @stub-protected
    def render(self) -> str:
        """Canonical Markdown body carrying the byte-stable failure marker.

        The body is bot-marker-led and carries no ``workflow-continue`` token,
        so the automated chain stops here exactly like a crashed-run
        notification does (issue #978).
        """
        return "\n".join(
            [
                _BOT_MARKER,
                _FAILURE_MARKER,
                _LINE.format(
                    ping=f"@{self.target} " if self.target else "",
                    workflow=self.workflow or "unknown",
                    step=self.step or "unknown",
                    phase=self.phase or "unknown",
                    error=self.error or "unknown error",
                    run_url=self.run_url or "(unavailable)",
                    pushed="yes" if self.pushed else "no",
                    provider_line=self._provider_line(),
                    aftermath=(
                        _AFTERMATH_MAY_ADVANCE if self.posted
                        else _AFTERMATH_NOT_ADVANCED
                    ),
                ),
            ]
        )

    def _provider_line(self) -> str:
        """The provider/model line, with the config-fix warning when needed."""
        if not (self.provider or self.model):
            return ""
        line = _PROVIDER_LINE.format(
            provider=self.provider or "(unknown)",
            model=self.model or "(unknown)",
        )
        return line + (_CONFIG_FIX_LINE if self.requires_config_fix else "")
