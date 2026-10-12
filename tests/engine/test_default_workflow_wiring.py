"""Shipped-config regression guards for the exec-step output wiring (issue #885).

The exec chain (``git_diff``/``run_tests`` script steps feeding ``review``) was
deliberately relocated out of the shipped default by maintainer commit
``b0679610`` ("add .nebuko/workflow/default-with-cmd workflow"), which moved it
into ``.nebuko/workflows/default-with-cmd.yaml`` (now
``plan-dev-review-cmd.yaml``). The example workflow still carried the two script
steps but ``implement`` routed straight to ``review``, so they were unreachable
and never posted the start/completion comments that are the point of #885.

These tests pin the intended wiring: the exec chain is reachable in both shipped
configs and ``review`` consumes the script-produced outputs. They deliberately
do NOT pin the exact ``review.inputs`` set — the example workflow may broaden it
back to ``[plan, implement, git_diff, run_tests]`` once required bindings can
degrade across a soft-kill retry (issue #793).
"""

from __future__ import annotations

from pathlib import Path

from config import load_workflow

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOW = REPO_ROOT / "defaults" / "workflows" / "default.yaml"
EXAMPLE_WORKFLOW = REPO_ROOT / ".nebuko" / "workflows" / "plan-dev-review-cmd.yaml"

SCRIPT_CHAIN = ("git_diff", "run_tests")


def _steps(path: Path) -> dict:
    definition = load_workflow(str(path))
    return {step.name: step for step in definition.steps}


class TestExecChainIsReachable:
    def test_default_routes_through_the_script_chain(self):
        steps = _steps(DEFAULT_WORKFLOW)
        assert steps["implement"].next == "git_diff"
        assert steps["git_diff"].next == "run_tests"
        assert steps["run_tests"].next == "review"

    def test_example_routes_through_the_script_chain(self):
        steps = _steps(EXAMPLE_WORKFLOW)
        assert steps["implement"].next == "git_diff"
        assert steps["git_diff"].next == "run_tests"
        assert steps["run_tests"].next == "review"


class TestScriptStepsAreScripts:
    def test_default_chain_steps_are_scripts(self):
        steps = _steps(DEFAULT_WORKFLOW)
        for name in SCRIPT_CHAIN:
            assert steps[name].script is not None

    def test_example_chain_steps_are_scripts(self):
        steps = _steps(EXAMPLE_WORKFLOW)
        for name in SCRIPT_CHAIN:
            assert steps[name].script is not None


class TestReviewConsumesScriptOutputs:
    def test_default_review_consumes_the_script_outputs(self):
        review = _steps(DEFAULT_WORKFLOW)["review"]
        assert review.inputs is not None
        assert set(SCRIPT_CHAIN).issubset(set(review.inputs))

    def test_example_review_consumes_the_script_outputs(self):
        review = _steps(EXAMPLE_WORKFLOW)["review"]
        assert review.inputs is not None
        assert set(SCRIPT_CHAIN).issubset(set(review.inputs))
