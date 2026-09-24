"""report_scaffold_branch tool — structured scaffold-branch hand-off.

Replaces the prose ``SCAFFOLD_BRANCH: <ref>`` marker as the primary
channel (issue #636 rework). The scaffold agent (dev) calls this tool
*immediately after pushing* the scaffold feature branch; the runtime
reads the validated ref from the run-scoped registry in ``build_output``
and persists it in ``WorkflowState.scaffold_branch`` — no regex over
prose, no comment stripping on the happy path.

Marker parsing (``extract_scaffold_branch_any``) stays in place as the
legacy fallback for in-flight or older runs that never call the tool.

The tool only records the ref in a run-scoped registry; the persistence
stays in the output pipeline (``build_output``). Recording happens on
the tool thread, so the registry is guarded by a lock.
"""

from collections.abc import Sequence
from threading import Lock
from typing import TYPE_CHECKING, Any, Optional, Self

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

from agent_script.sub_domains.scaffold_branch.scaffold_branch import (
    validate_scaffold_branch_ref,
)

if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)


class ScaffoldBranchReportAction(Action):
    """Action for reporting the pushed scaffold feature branch."""

    branch: str = Field(
        description=(
            "The pushed feature-branch ref carrying the scaffolded "
            "tmp_impl/workflow.yaml (e.g. feature/issue-635-...)."
        ),
    )


class ScaffoldBranchReportObservation(Observation):
    """Confirmation returned after the branch ref was recorded.

    A success confirmation, or a validation error the agent can correct
    and re-call on.
    """

    @property
    def visualize(self) -> Text:
        return Text("Scaffold branch recorded.", style="bold green")


SCAFFOLD_BRANCH_DESCRIPTION = """Report the pushed scaffold feature branch to the engine. You MUST call this tool once, immediately after pushing the scaffold branch, before calling dispatch_next.

Parameters:
- branch: the exact pushed branch ref that carries tmp_impl/workflow.yaml (e.g. feature/issue-635-...).

The engine persists this ref as scaffold_branch so later runs can fetch the branch-mounted workflow files from it. Invalid refs are rejected with an error — correct and re-call. Do NOT rely on printing a SCAFFOLD_BRANCH marker in your answer; that text fallback is only for legacy runs."""  # noqa: E501


# ── Run-scoped scaffold-branch registry ───────────────────────────────────
# Keys are opaque run ids (conversation id, or the literal "default" key for
# single-run processes and tests). The latest *valid* ref wins; invalid refs
# are rejected before reaching the registry and never clobber a stored value.

_registry_lock = Lock()
_scaffold_branch_registry: dict[str, str] = {}


def record_scaffold_branch(conversation_id: str, branch: str) -> None:
    """Store the validated scaffold branch ref for a run (thread-safe)."""
    with _registry_lock:
        _scaffold_branch_registry[str(conversation_id)] = branch
    logger.info(f"report_scaffold_branch recorded for run {conversation_id}: {branch}")


def get_recorded_scaffold_branch(conversation_id: str) -> Optional[str]:
    """Return the recorded scaffold branch ref for a run, or None if absent."""
    with _registry_lock:
        return _scaffold_branch_registry.get(str(conversation_id))


def clear_scaffold_branch_run(conversation_id: str) -> None:
    """Drop the recorded scaffold branch for a run (cleanup after output build)."""
    with _registry_lock:
        _scaffold_branch_registry.pop(str(conversation_id), None)


def reset_registry() -> None:
    """Clear every run (intended for tests)."""
    with _registry_lock:
        _scaffold_branch_registry.clear()


class ScaffoldBranchReportExecutor(ToolExecutor):
    def __init__(self, conversation_id: str = "default") -> None:
        self._conversation_id = conversation_id

    def __call__(
        self,
        action: ScaffoldBranchReportAction,
        conversation: "BaseConversation | None" = None,
    ) -> ScaffoldBranchReportObservation:
        # @stub-protected
        branch = action.branch.strip()
        if not validate_scaffold_branch_ref(branch):
            return ScaffoldBranchReportObservation.from_text(
                text=(
                    f"Scaffold branch rejected: '{action.branch}' is not a "
                    "valid branch ref. Re-call report_scaffold_branch with "
                    "the exact pushed branch name (e.g. feature/issue-635-...)."
                ),
                is_error=True,
            )
        record_scaffold_branch(self._conversation_id, branch)
        return ScaffoldBranchReportObservation.from_text(
            text=(
                f"Scaffold branch recorded: {branch}. The engine will persist "
                "it as scaffold_branch after this run."
            )
        )


class ScaffoldBranchReportTool(
    ToolDefinition[ScaffoldBranchReportAction, ScaffoldBranchReportObservation]
):
    """Structured scaffold-branch reporting tool (issue #636 rework)."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        conversation_id: str = "default",
        **params: Any,
    ) -> Sequence[Self]:
        """Create a ScaffoldBranchReportTool bound to a run-scoped conversation id.

        # @stub-protected
        Mirrors ``DispatchNextTool.create``: rejects extra parameters, binds
        ``str(conv_state.id)`` as the registry key (the read side keys under
        ``str(conversation.id)``, which equals ``str(state.id)``).

        Args:
            conv_state: Optional conversation state; its id becomes the
                registry key when provided.
            conversation_id: Fallback registry key for single-run processes
                and tests.
            **params: Additional parameters (none supported).
        """
        if params:
            raise ValueError("ScaffoldBranchReportTool doesn't accept parameters")
        if conv_state is not None:
            # ConversationState exposes its id as ``id`` (ConversationID);
            # no ``conversation_id`` attribute exists on it.
            try:
                conversation_id = str(conv_state.id)
            except Exception:
                pass
        return [
            cls(
                description=SCAFFOLD_BRANCH_DESCRIPTION,
                action_type=ScaffoldBranchReportAction,
                observation_type=ScaffoldBranchReportObservation,
                executor=ScaffoldBranchReportExecutor(
                    conversation_id=conversation_id
                ),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]