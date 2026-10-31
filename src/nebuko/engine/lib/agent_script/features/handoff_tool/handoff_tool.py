"""handoff tool — structured session handoff for early-ended runs (issue #965).

Any role can call this tool, including the read-only ones (architect / review /
po have no ``terminal``/``file_editor``). The executor runs **engine-side**: it
renders the document, and :class:`HandoffStore` stages only ``.nebuko/handoff.md``,
commits and pushes. The agent needs no worktree write access.

Each call *overwrites* the document — no appending — so stale claims cannot
accumulate. ``fold: true`` additionally records a run-scoped marker: "stop here,
I want a fresh start". ``run_agent`` reads it and classifies the run as
``OutcomeStatus.AGENT_CHECKPOINT``, which routes through the existing per-step
soft-retry budget (``MAX_SOFT_RETRIES``) — the cap that stops folding from
becoming an exit from hard tasks.
"""

from __future__ import annotations

from collections.abc import Sequence
from threading import Lock
from typing import TYPE_CHECKING, Any, Final, Optional, Self

from openhands.sdk import get_logger
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)
from pydantic import Field
from rich.text import Text

from nebuko.engine.lib.agent_script.features.handoff_tool.handoff_document import HandoffDocument
from nebuko.engine.lib.agent_script.features.handoff_tool.handoff_store import (
    HANDOFF_RELATIVE_PATH,
    HandoffStore,
)

if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)


class HandoffAction(Action):
    """Action for writing (overwriting) the structured session handoff."""

    completed: list[str] = Field(
        default_factory=list,
        description=(
            "Concrete deliverables produced so far, one per entry. Replaces "
            "the previous document's list entirely — do not repeat stale items."
        ),
    )
    state: str = Field(
        default="",
        description="Branch / PR / current status in a sentence or two.",
    )
    verified_facts: list[str] = Field(
        default_factory=list,
        description=(
            "Claims you actually checked, each with its evidence (the command "
            "you ran, the file, the test output). The next agent re-checks "
            "these before relying on them."
        ),
    )
    hypotheses: list[str] = Field(
        default_factory=list,
        description=(
            "Unverified guesses and intended approaches. Kept apart from "
            "verified_facts on purpose."
        ),
    )
    blockers: list[str] = Field(
        default_factory=list,
        description="What is stuck right now, and why.",
    )
    next_steps: list[str] = Field(
        default_factory=list,
        description="Ordered next actions; they are rendered as a numbered list.",
    )
    plan_changes: list[str] = Field(
        default_factory=list,
        description=(
            "What changed since the previous version of this document, and why."
        ),
    )
    fold: bool = Field(
        default=False,
        description=(
            "Set true only to stop here and start fresh: the document is still "
            "saved, and the run then ends as an incomplete agent_checkpoint, "
            "which the engine retries against MAX_SOFT_RETRIES."
        ),
    )


class HandoffObservation(Observation):
    """Confirmation returned after the handoff document was written."""

    @property
    def visualize(self) -> Text:
        return Text("Handoff recorded.", style="bold green")


HANDOFF_DESCRIPTION: Final[str] = """Write or update the session handoff document so the next run can start from your current design and thinking instead of guessing.

Call this before ending a run that will not finish its step, and whenever a durable checkpoint of your reasoning is worth leaving behind. Each call OVERWRITES the document at .nebuko/handoff.md (there is no appending): fill every section you want to survive, because anything you leave out is gone from the new version — prior versions remain in git history. The engine does the git work (stage, commit, push), so no worktree write access is needed.

Parameters:
- completed: concrete deliverables so far.
- state: branch / PR / current status.
- verified_facts: claims WITH evidence (command run, file, test output).
- hypotheses: unverified guesses — keep them out of verified_facts.
- blockers: what is stuck, and why.
- next_steps: ordered next actions (rendered as a numbered list).
- plan_changes: what changed since the previous version of this document and why.
- fold: set true to stop here and start fresh — the document is saved first, then the run ends incomplete (agent_checkpoint) and is retried under the engine's per-step retry budget.

NEVER put secrets, tokens, credentials or private customer content in any field: the document is committed to the branch and stays in its history."""  # noqa: E501


# ── Run-scoped fold registry ──────────────────────────────────────────────
# Keys are opaque run ids (conversation id, or the literal "default" key for
# single-run processes and tests). Recording happens on the tool thread, so the
# registry is guarded by a lock. Mirrors the dispatch/scaffold-branch registries.

_registry_lock = Lock()
_fold_registry: dict[str, str] = {}


# @stub-protected
def record_fold(conversation_id: str, note: str = "") -> None:
    """Mark the run as folded ("stop here, fresh start") for this run id."""
    with _registry_lock:
        _fold_registry[str(conversation_id)] = note
    logger.info(f"handoff fold recorded for run {conversation_id}: {note}")


# @stub-protected
def take_fold(conversation_id: str) -> str | None:
    """Return the run's fold note and clear the marker (``None`` when absent).

    Take-and-clear: ``run_agent`` classifies the outcome exactly once per run,
    so the marker must not survive into a later run in a long-lived process.
    """
    with _registry_lock:
        return _fold_registry.pop(str(conversation_id), None)


def reset_registry() -> None:
    """Clear every run (intended for tests)."""
    with _registry_lock:
        _fold_registry.clear()


class HandoffExecutor(ToolExecutor):
    """Render the handoff, commit it engine-side, and record a fold marker."""

    def __init__(
        self, conversation_id: str = "default", store: HandoffStore | None = None
    ) -> None:
        self._conversation_id = conversation_id
        self._store = store if store is not None else HandoffStore()

    def __call__(
        self,
        action: HandoffAction,
        conversation: "BaseConversation | None" = None,
    ) -> HandoffObservation:
        document = self._to_document(action)
        try:
            commit = self._store.write_and_commit(document)
        except Exception as e:  # noqa: BLE001 - surfaced to the agent, never a crash
            # Loud, actionable failure: a silently dropped handoff is
            # indistinguishable from the agent never having called the tool.
            logger.error(
                f"handoff failed for run {self._conversation_id}: {e}"
            )
            return HandoffObservation.from_text(
                text=(
                    f"handoff could not be saved to {HANDOFF_RELATIVE_PATH}: {e}. "
                    "Report the failure in your answer and carry on."
                ),
                is_error=True,
            )

        if action.fold:
            record_fold(self._conversation_id, action.state.strip())
            logger.info(
                f"handoff fold for run {self._conversation_id}: commit {commit[:8]}"
            )
            return HandoffObservation.from_text(
                text=(
                    f"Handoff saved ({HANDOFF_RELATIVE_PATH}, commit {commit[:8]}) "
                    "and fold recorded: the run is ending as an incomplete "
                    "agent_checkpoint. STOP NOW — end your turn, do not start "
                    "new work. The engine retries this step within its per-step "
                    "retry budget."
                )
            )

        return HandoffObservation.from_text(
            text=(
                f"Handoff saved ({HANDOFF_RELATIVE_PATH}, commit {commit[:8]}). "
                "You may continue working; calling this tool again overwrites "
                "the document."
            )
        )

    @staticmethod
    def _to_document(action: HandoffAction) -> HandoffDocument:
        """Build the document, stamping it with the current UTC time."""
        from nebuko.engine.lib.agent_script.features.handoff_tool.handoff_store import _timestamp

        return HandoffDocument(
            completed=tuple(action.completed),
            state=action.state,
            verified_facts=tuple(action.verified_facts),
            hypotheses=tuple(action.hypotheses),
            blockers=tuple(action.blockers),
            next_steps=tuple(action.next_steps),
            plan_changes=tuple(action.plan_changes),
            updated_at=_timestamp(),
        )


class HandoffTool(ToolDefinition[HandoffAction, HandoffObservation]):
    """Structured session-handoff tool (issue #965)."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        conversation_id: str = "default",
        **params: Any,
    ) -> Sequence[Self]:
        """Create a HandoffTool bound to a run-scoped conversation id.

        Args:
            conv_state: Optional conversation state (used for its id).
            conversation_id: Registry key for the fold marker. Falls back to
                the live conversation id when available.
            **params: Additional parameters (none supported).
        """
        if params:
            raise ValueError("HandoffTool doesn't accept parameters")
        if conv_state is not None:
            # ConversationState exposes its id as ``id`` (ConversationID).
            try:
                conversation_id = str(conv_state.id)
            except Exception:
                pass
        return [
            cls(
                description=HANDOFF_DESCRIPTION,
                action_type=HandoffAction,
                observation_type=HandoffObservation,
                executor=HandoffExecutor(conversation_id=conversation_id),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
