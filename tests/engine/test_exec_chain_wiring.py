"""Wiring guards for the example exec chain (issue #885).

An ``exec``/``script`` step only posts its output comment — and only becomes
addressable by a downstream ``inputs: [...]`` binding — when it is actually
*reached*. The failure this pins: the shipped example workflow declared
``git_diff`` / ``run_tests`` between ``implement`` and ``review``, but
``implement.next`` still pointed straight at ``review``, so both script steps
were unreachable (linter ``L2-unreachable-step``), never executed, and
``review``'s required inputs had nothing to bind to.

These guards keep the chain structural: ``implement → git_diff → run_tests →
review``. Since #957 the diff needs the remote-tracking ``origin/main`` a
shallow, ref-pinned checkout never creates, so the step fetches it first; since
#965 (issue #972) fetch and diff are one ``sh -c`` step, so the base ref can
never be separated from the diff. Each step runs in its own engine invocation
(issue #994), so cross-step workspace state is not relied upon at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import load_workflow

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_WORKFLOW = REPO_ROOT / ".nebuko" / "workflows" / "plan-dev-review-cmd.yaml"


def _steps() -> dict:
    definition = load_workflow(str(EXAMPLE_WORKFLOW))
    return {step.name: step for step in definition.steps}


class TestExecChainIsReachable:
    def test_implement_hands_off_to_git_diff(self):
        assert _steps()["implement"].next == "git_diff"

    def test_git_diff_fetches_the_base_ref_then_diffs_in_one_step(self):
        """The checkout is shallow: ``origin/main`` must be fetched, not assumed.

        Fetch and diff are one ``sh -c`` step (issue #972), so the base ref can
        never be separated from the diff by a chain break.
        """
        git_diff = _steps()["git_diff"]
        assert git_diff.script is not None
        assert git_diff.script.command == "sh"
        args = git_diff.script.args
        assert args[0] == "-c"
        script = args[1]
        assert "main:refs/remotes/origin/main" in script
        assert script.index("main:refs/remotes/origin/main") < script.index(
            "git diff origin/main HEAD"
        )
        assert git_diff.next == "run_tests"

    def test_run_tests_is_a_script_step_that_continues_to_review(self):
        run_tests = _steps()["run_tests"]
        assert run_tests.script is not None
        assert run_tests.script.command == "uv"
        assert run_tests.next == "review"

    def test_review_consumes_both_script_outputs(self):
        review = _steps()["review"]
        bound = set(review.inputs or {})
        assert {"git_diff", "run_tests"} <= bound

    def test_argv_survives_the_executor_contract_unchanged(self):
        """``args`` reach the child verbatim — no shell, no re-splitting.

        The executor decodes the resolver's JSON array into ``[command, *args]``
        (issue #974), so a quoted construct such as
        ``command: "sh", args: ["-c", "git fetch && git diff"]`` keeps its
        grouping instead of running as ``sh -c git fetch`` (issue #957).
        """
        for name, step in _steps().items():
            if step.script is None:
                continue
            env = step.script.to_process_env()
            assert json.loads(env["SCRIPT_ARGS"]) == (step.script.args or []), name

    def test_rejected_review_returns_to_implement(self):
        review = _steps()["review"]
        rejected = [c for c in review.conditions if c.on_result == "rejected"]
        assert rejected and rejected[0].next == "implement"
