"""Session-handoff document model + prompt rendering (issue #965).

When a run ends early (soft kill, crash, CI timeout) the next run starts
without a reliable record of what was done and thought. ``HandoffDocument`` is
that record: a fixed-headings Markdown document an agent writes through the
``handoff`` tool, and ``render_handoff_block`` is how the next run's prompt
carries it.

The fields deliberately separate *verified facts* (claims with evidence) from
*hypotheses* (unverified guesses), so the incoming agent knows which claims it
may rely on and which it must re-derive. The block preamble says so explicitly:
an unverified claim presented as truth is worse than no claim at all.
"""

from __future__ import annotations

from pydantic import BaseModel

#: Header of the handoff block injected into the next run's prompt.
HANDOFF_BLOCK_HEADER = "## Previous session handoff"

#: Standard Markdown comment marking the file as engine-managed.
_MANAGED_COMMENT = (
    "<!-- Managed by the nebuko `handoff` tool — overwritten on every call.\n"
    "     Never store secrets, tokens or private customer content. -->"
)

#: Fixed framing: the incoming agent must re-check, not trust.
_FRAMING = (
    "The previous run on this branch ended before completing its step. The "
    "document below is what it left behind.\n\n"
    "Treat every **verified fact** as a *claim* to re-check, not as truth: "
    "verify it (re-run the command, re-read the file, re-check the test "
    "output) before relying on it. Hypotheses are unverified guesses."
)

#: Fields rendered as fenced Markdown list sections, in fixed document order.
_SECTION_TITLES = {
    "completed": "## Completed",
    "verified_facts": "## Verified facts (claims to re-check)",
    "hypotheses": "## Hypotheses (unverified)",
    "blockers": "## Blockers",
    "plan_changes": "## Plan changes",
}


def _render_bullets(items: tuple[str, ...], empty: str = "_none recorded_") -> str:
    """Render ``items`` as a Markdown bullet list, or ``empty`` when blank."""
    rendered = [f"- {item.strip()}" for item in items if item and item.strip()]
    if not rendered:
        return empty
    return "\n".join(rendered)


def _render_ordered(items: tuple[str, ...]) -> str:
    """Render ``items`` as an ordered Markdown list (ordered next steps)."""
    rendered = [item.strip() for item in items if item and item.strip()]
    if not rendered:
        return "_none recorded_"
    return "\n".join(f"{i}. {item}" for i, item in enumerate(rendered, start=1))


class HandoffDocument(BaseModel):
    """The structured session handoff, rendered with fixed headings.

    Attributes:
        completed: Concrete deliverables produced so far.
        state: Branch / PR / current status.
        verified_facts: Claims with evidence (command run, file, test output).
        hypotheses: Unverified guesses, kept apart from the facts.
        blockers: What is stuck and why.
        next_steps: Ordered next actions.
        plan_changes: What changed since the previous version of this document,
            and why. Overwritten with the rest of the document on every call.
        updated_at: ISO-8601 UTC timestamp of the write.
    """

    completed: tuple[str, ...] = ()
    state: str = ""
    verified_facts: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    plan_changes: tuple[str, ...] = ()
    updated_at: str = ""

    # @stub-protected
    def render(self) -> str:
        """Render the document with the fixed headings (overwrite-friendly).

        Every call produces the complete document from the fields at hand, so
        a later call drops claims the new state no longer carries — stale
        facts cannot accumulate across runs.
        """
        sections = [
            "# Session handoff",
            "",
            _MANAGED_COMMENT,
            "",
            f"- updated: {self.updated_at}",
            "",
            _SECTION_TITLES["completed"],
            "",
            _render_bullets(self.completed),
            "",
            "## State",
            "",
            self.state.strip() or "_not recorded_",
            "",
            _SECTION_TITLES["verified_facts"],
            "",
            _render_bullets(self.verified_facts),
            "",
            _SECTION_TITLES["hypotheses"],
            "",
            _render_bullets(self.hypotheses),
            "",
            _SECTION_TITLES["blockers"],
            "",
            _render_bullets(self.blockers),
            "",
            "## Next steps",
            "",
            _render_ordered(self.next_steps),
            "",
            _SECTION_TITLES["plan_changes"],
            "",
            _render_bullets(self.plan_changes),
        ]
        return "\n".join(sections).rstrip() + "\n"


# @stub-protected
def render_handoff_block(body: str) -> str:
    """The ``## Previous session handoff`` prompt block, or ``""`` when empty.

    Framed the same way the injected-skills block is framed, plus the explicit
    claims-to-re-check preamble: the block is *evidence to verify*, never an
    instruction to follow blindly.
    """
    if not body or not body.strip():
        return ""
    return f"{HANDOFF_BLOCK_HEADER}\n\n{_FRAMING}\n\n{body.strip()}\n"
