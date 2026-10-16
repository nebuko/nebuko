"""Dispatch recall — bounded cognitive recall when a run ends without dispatch.

The ``dispatch_next`` tool makes the missing-decision case programmatically
detectable (issue #524). When the run nevertheless ends without one, this
prompt is injected as a short, focused follow-up turn (max
``MAX_DISPATCH_RECALLS`` times) asking the model for the next-step decision
— a cognitive question, so the answer comes from the model, not a heuristic.
"""

from openhands.sdk import get_logger

logger = get_logger(__name__)

# Hard cap on recall turns: each one costs one model turn, and an unbounded
# retry loop would defeat the bounded-cost property of the fix.
MAX_DISPATCH_RECALLS = 2

# Watchdog budget for a single recall pass (seconds). A dispatch-only answer
# needs no tools and no new work — anything slower is treated as a hang.
RECALL_PASS_BUDGET_S = 300.0

DISPATCH_RECALL_PROMPT = """\
⚠️ MISSING NEXT-STEP DECISION — dispatch_next REQUIRED

Your previous answer contained no next-step decision: you ended the run
WITHOUT calling the `dispatch_next` tool, so the workflow engine cannot
route the next step and the workflow would stall.

Do NOT start any new task. Do NOT restate your previous answer.

Call the `dispatch_next` tool NOW with your factual verdict on the turn:
- appreciation: 'approved' (the work meets the step's goal), 'rejected'
  (it does not), or 'hold' (you cannot decide — the turn needs a human)
- hold_kind: only with appreciation='hold' — 'awaiting_approval' (a human
  must approve before the workflow continues) or 'awaiting_input' (you need
  information from the human)
- note: one short sentence justifying the verdict
- complexity: 'low', 'medium', or 'high' for the next agent (optional)

The engine derives the route from the workflow topology — you report what
happened, not where to go. Base the verdict on the work you just completed.
Call the tool, then finish.

Your previous answer already stands as the posted deliverable — you are only
being asked to supply the missing turn outcome; do not restate or rework it.
"""


def perform_dispatch_recalls(
    conversation,
    has_dispatch,
    run_pass,
) -> int:
    """Run the bounded dispatch-recall loop (issue #524).

    Dependencies are injected so the loop stays testable without an event
    loop or a real LLM:

    Args:
        conversation: Anything with ``send_message(prompt)`` (SDK
            Conversation or a test double).
        has_dispatch: Zero-arg callable — True once a dispatch decision has
            been recorded for this run.
        run_pass: ``n -> bool`` — executes one recall turn and returns True
            if it completed within its budget.

    Returns:
        Number of recall turns actually performed (0..MAX_DISPATCH_RECALLS).
    """
    count = 0
    while count < MAX_DISPATCH_RECALLS and not has_dispatch():
        count += 1
        try:
            conversation.send_message(DISPATCH_RECALL_PROMPT)
        except Exception as exc:  # noqa: BLE001 - must not escape the termination path
            # A dead/closed conversation (or condenser failure after a
            # timeout run) cannot answer the recall — log and stop recalling
            # instead of letting the exception escape and losing the run
            # outcome entirely.
            logger.warning(
                f"Dispatch recall {count}: send_message failed — stopping recalls "
                f"(count={count}): {exc}"
            )
            break
        if not run_pass(count):
            break
    return count
