"""Dispatch recall — bounded cognitive recall when a run ends without dispatch.

The ``dispatch_next`` tool makes the missing-decision case programmatically
detectable (issue #524). When the run nevertheless ends without one, this
prompt is injected as a short, focused follow-up turn (max
``MAX_DISPATCH_RECALLS`` times) asking the model for the next-step decision
— a cognitive question, so the answer comes from the model, not a heuristic.
"""

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

Call the `dispatch_next` tool NOW with exactly these parameters:
- status: 'advance' (proceed to the next step/agent), 'loop' (re-run the
  current step), 'hold' (wait for a human), or 'terminate' (stop the workflow)
- agent: explicit target agent name if you are handing off (e.g. 'ctx',
  'dev'), otherwise omit it — the workflow definition resolves the next agent
- complexity: 'low', 'medium', or 'high'
- reason: one short sentence justifying the decision

Base the decision on the work you just completed. Call the tool, then finish.
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
        conversation.send_message(DISPATCH_RECALL_PROMPT)
        if not run_pass(count):
            break
    return count