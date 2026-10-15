"""Wiring guards for the example exec chain (issue #885).

An ``exec``/``script`` step only posts its output comment — and only becomes
addressable by a downstream ``inputs: [...]`` binding — when it is actually
*reached*. The failure this pins: the shipped example workflow declared
``git_diff`` / ``run_tests`` between ``implement`` and ``review``, but
``implement.next`` still pointed straight at ``review``, so both script steps
were unreachable (linter ``L2-unreachable-step``), never executed, and
``review``'s required inputs had nothing to bind to.

These guards keep the chain structural: ``implement → git_diff → run_tests →
review``.
"""

from __future__ import annotations

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
