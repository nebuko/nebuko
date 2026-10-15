"""Wiring guards for the example exec chain (issue #885).

An ``exec``/``script`` step only posts its output comment — and only becomes
addressable by a downstream ``inputs: [...]`` binding — when it is actually
*reached*. The failure this pins: the shipped example workflow declared
``git_diff`` / ``run_tests`` between ``implement`` and ``review``, but
``implement.next`` still pointed straight at ``review``, so both script steps
were unreachable (linter ``L2-unreachable-step``), never executed, and
``review``'s required inputs had nothing to bind to.

These guards keep the chain structural: ``implement → fetch_base → git_diff →
run_tests → review``. Since #957 ``fetch_base`` materializes the remote-tracking
``origin/main`` a shallow, ref-pinned checkout never creates, so the diff cannot
die with ``fatal: ambiguous argument 'origin/main'``.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from config import load_workflow

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_WORKFLOW = REPO_ROOT / ".nebuko" / "workflows" / "plan-dev-review-cmd.yaml"


def _steps() -> dict:
    definition = load_workflow(str(EXAMPLE_WORKFLOW))
    return {step.name: step for step in definition.steps}


class TestExecChainIsReachable:
    def test_implement_hands_off_to_fetch_base(self):
        assert _steps()["implement"].next == "fetch_base"

    def test_fetch_base_materializes_origin_main_before_the_diff(self):
        """The checkout is shallow: ``origin/main`` must be fetched, not assumed."""
        fetch_base = _steps()["fetch_base"]
        assert fetch_base.script is not None
        assert fetch_base.script.path == "git"
        args = fetch_base.script.args
        assert args[0] == "fetch"
        assert "main:refs/remotes/origin/main" in args
        assert fetch_base.next == "git_diff"

    def test_git_diff_is_a_script_step_that_continues_to_run_tests(self):
        git_diff = _steps()["git_diff"]
        assert git_diff.script is not None
        assert git_diff.script.path == "git"
        assert git_diff.script.args[:2] == ["diff", "origin/main"]
        assert git_diff.next == "run_tests"

    def test_run_tests_is_a_script_step_that_continues_to_review(self):
        run_tests = _steps()["run_tests"]
        assert run_tests.script is not None
        assert run_tests.script.path == "uv"
        assert run_tests.next == "review"

    def test_review_consumes_both_script_outputs(self):
        review = _steps()["review"]
        bound = set(review.inputs or {})
        assert {"git_diff", "run_tests"} <= bound

    def test_no_script_step_relies_on_shell_word_splitting(self):
        """Script ``args`` round-trip through a space-join/``shlex.split``.

        The resolver joins ``args`` with a space and the executor re-splits
        that string, so a shell-quoting construct such as
        ``path: "sh", args: ["-c", "git fetch && git diff"]`` loses its
        grouping and runs as ``sh -c git fetch`` (issue #957). Keep every
        script step's argv self-consistent under that contract.
        """
        for name, step in _steps().items():
            if step.script is None:
                continue
            args = step.script.args
            assert shlex.split(" ".join(args)) == args, name


class TestHandoffCleanupChain:
    """The handoff cleanup runs after the reviewer, before the terminal (#965).

    Ordering is load-bearing: a ``rejected`` review sends the work back to
    ``implement``, which still needs the document, so cleanup may only sit on
    the ``approved`` route.
    """

    def test_approved_review_goes_to_the_cleanup_step(self):
        review = _steps()["review"]
        approved = [c for c in review.conditions if c.on_result == "approved"]
        assert approved and approved[0].next == "handoff_cleanup"

    def test_rejected_review_still_returns_to_implement(self):
        review = _steps()["review"]
        rejected = [c for c in review.conditions if c.on_result == "rejected"]
        assert rejected and rejected[0].next == "implement"

    def test_cleanup_then_gate_check_then_terminal(self):
        steps = _steps()
        cleanup = steps["handoff_cleanup"]
        assert cleanup.script is not None
        assert cleanup.script.path == "bash"
        assert cleanup.script.args == ["scripts/handoff_cleanup.sh"]
        assert cleanup.next == "handoff_gate_check"

        gate = steps["handoff_gate_check"]
        assert gate.script is not None
        assert gate.script.path == "bash"
        assert gate.script.args == ["scripts/handoff_check.sh"]
        assert gate.next is None

    def test_review_precedes_the_cleanup(self):
        """Cleanup must never run before the reviewer agent."""
        steps = _steps()
        assert steps["run_tests"].next == "review"
