"""Publication guard — bounded recall when approved work was never published.

An approved write step can end with its edits neither committed, pushed nor
opened as a PR (run 39622244543: the model edited a file with no shell tool
available). ``ensure_pr`` only covers the graceful-timeout path and the
dispatch recall only looks for a missing verdict, so nothing noticed.

This module owns the agent-facing half of the fix: the recall prompt and its
bounded loop, mirroring ``dispatch_recall`` so both recalls share the same
cost properties (a model turn each, capped, never escaping the termination
path). The deterministic ``ensure_pr`` fallback stays in ``run_agent``.
"""

from collections.abc import Sequence

from openhands.sdk import get_logger

from nebuko.engine.lib.git.git_session import BENIGN_SKIPS

logger = get_logger(__name__)

# Hard cap on recall turns. Each one costs a model turn, and an agent that
# ignores the first nudge is unlikely to obey the tenth.
MAX_PUBLICATION_RECALLS = 2

# Watchdog budget for a single recall pass (seconds). Committing and pushing
# is fast; anything slower is treated as a hang and hands over to ensure_pr.
PUBLICATION_RECALL_PASS_BUDGET_S = 600.0

_REASON_TEXT = {
    "dirty_tree": "your working tree has uncommitted changes",
    "unpushed_commits": "your branch has commits that are not pushed",
    "missing_pr": "no pull request is open for your branch",
    "workflow_patch": (
        "your changes under .github/workflows/ cannot be pushed with the agent "
        "token (it has no `workflow` scope)"
    ),
}

#: Reasons a recall turn cannot fix. ``workflow_patch`` means the only
#: unpublished work is under ``.github/workflows/``, which the agent token may
#: never push (issue #1080) — recalling the agent would burn turns on a push
#: that is rejected no matter what. Those go straight to ``ensure_pr``, which
#: delivers the delta as a patch for a human to apply.
# @stub-protected: signature defined by architect — do not modify
NON_RECALLABLE_REASONS = frozenset(
    {
        # Work the agent token may never push (issue #1080): the delta is
        # delivered as a patch for a human to apply.
        "workflow_patch",
        # The engine owns the push and the PR now (issue #1150): "not pushed"
        # and "no PR" are the normal end state of an agent turn, so recalling
        # for them would only burn model turns.
        "unpushed_commits",
        "missing_pr",
    }
)


# @stub-protected: signature defined by architect — do not modify
def is_benign_skip(skip_reason: str) -> bool:
    """True when ``skip_reason`` means there is nothing to publish at all.

    Only ``BENIGN_SKIPS`` ("no git remote", "detached HEAD") are benign. A skip
    the guard *could* not inspect (``pr lookup failed``, ``error: …``) is not:
    reading it as "nothing to publish" is what let a credentials-less run end
    green (issue #1089), so it takes the fallback path instead.
    """
    return skip_reason in BENIGN_SKIPS


def recallable_reasons(reasons: Sequence[str]) -> tuple[str, ...]:
    """The subset of ``reasons`` a recall turn could actually fix."""
    return tuple(r for r in reasons if r not in NON_RECALLABLE_REASONS)


def publication_recall_prompt(reasons: Sequence[str]) -> str:
    """The recall message asking the agent to publish its approved work."""
    missing = [_REASON_TEXT.get(reason, reason) for reason in reasons]
    listed = "\n".join(f"- {item}" for item in missing) or "- the work is not published"

    return f"""\
⚠️   UNPUBLISHED WORK

You finalised this step as approved, but the work is not published yet:
{listed}

Do NOT start any new task and do NOT redo the work.

1. Commit your changes (stage only the files you changed — never generated
   files, caches, or secrets).
2. Do **not** push: the engine pushes the branch and opens the pull request
   after your run — `git push` is denied for agents.
3. The engine opens the pull request after your run — you only need the
   branch committed.

You keep control of the branch name, the commit message and the PR body.

Then call the `finalize` tool again with the same verdict and finish.

If you cannot publish the work (no git or shell tool available), say so
briefly and finalize: the engine will checkpoint the branch with a generic
pull request so the work is not lost.
"""


def perform_publication_recalls(
    conversation,
    unpublished_reasons,
    run_pass,
) -> int:
    """Run the bounded publication-recall loop.

    Dependencies are injected so the loop stays testable without an event
    loop or a real LLM:

    Args:
        conversation: Anything with ``send_message(prompt)`` (SDK
            Conversation or a test double).
        unpublished_reasons: Zero-arg callable returning the reasons the work
            is still unpublished, or an empty sequence once it is published.
            Re-checked every iteration so a compliant agent stops the loop.
        run_pass: ``n -> bool`` — executes one recall turn and returns True
            if it completed within its budget.

    Returns:
        Number of recall turns actually performed
        (0..MAX_PUBLICATION_RECALLS).
    """
    count = 0
    while count < MAX_PUBLICATION_RECALLS:
        reasons = tuple(unpublished_reasons() or ())
        if not reasons:
            break
        if not recallable_reasons(reasons):
            # Only work the agent cannot push is left (``workflow_patch``):
            # a recall would spend a model turn on a push GitHub always
            # rejects. Hand over to ensure_pr's patch delivery instead.
            logger.info(
                "Publication recall: only non-recallable reasons left "
                f"({', '.join(reasons)}) — skipping recalls"
            )
            break
        count += 1
        try:
            conversation.send_message(publication_recall_prompt(reasons))
        except Exception as exc:  # noqa: BLE001 - must not escape the termination path
            # A dead/closed conversation cannot answer the recall — log and
            # stop instead of losing the run outcome to the exception.
            logger.warning(
                f"Publication recall {count}: send_message failed — stopping "
                f"recalls (count={count}): {exc}"
            )
            break
        if not run_pass(count):
            break
    return count
