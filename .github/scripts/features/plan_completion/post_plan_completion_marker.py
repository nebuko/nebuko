"""Standalone terminal plan-step completion posting (issue #613, G3).

Covers the plan-*terminates* case: when the last plan step's sub-workflow
reaches its terminal leaf the resolver returns ``SKIP_AGENT=true`` (workflow
completed), so the agent step — and with it ``build_output`` — never runs and
the last step's ``bot-subtask-complete`` marker would never be posted.

This script runs as a small gated GHA step in that case and posts the marker
for ``COMPLETED_PLAN_STEP_INDEX`` on the current issue (``TARGET_NUMBER``).
The idempotency guard (paginated comment scan) keeps re-runs duplicate-free.
Any failure exits 0 with a warning: a missed marker degrades to manual
resume, never to a crashed run.

Usage (GHA): ``python3 "$SCRIPTS/features/plan_completion/post_plan_completion_marker.py"``
with env: ``TARGET_NUMBER`` (required), ``COMPLETED_PLAN_STEP_INDEX``
(1-based, required); missing/empty → no-op exit.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_script.features.plan_completion.plan_completion import (
    fetch_existing_bodies,
    marker_already_present,
    post_completion_marker,
)


def _post_marker(completed_index: int, issue_number: int) -> bool:
    """Post the zero-indexed marker for the completed plan step, if absent."""
    zero_index = completed_index - 1
    existing = fetch_existing_bodies(issue_number)
    if existing is not None and marker_already_present(zero_index, existing):
        print(f"Plan completion marker {zero_index} already present — skipping.", flush=True)
        return False
    return post_completion_marker(issue_number, zero_index)


def main() -> int:
    issue_raw = os.environ.get("TARGET_NUMBER", "").strip()
    index_raw = os.environ.get("COMPLETED_PLAN_STEP_INDEX", "").strip()
    if not issue_raw or not index_raw:
        print(
            "Plan completion marker: no TARGET_NUMBER/COMPLETED_PLAN_STEP_INDEX — skipping.",
            flush=True,
        )
        return 0
    issue_number = int(issue_raw)
    completed_index = int(index_raw)
    try:
        if _post_marker(completed_index, issue_number):
            print(
                f"Plan completion marker posted: step {completed_index} "
                f"(zero-indexed {completed_index - 1}).",
                flush=True,
            )
    except Exception as e:  # containment: never fail the run over a missed marker
        print(f"Warning: failed to post plan completion marker: {e}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
