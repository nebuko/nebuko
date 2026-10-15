"""Soft-kill retry-vs-halt routing (issue #704).

Given a ``soft_kill`` dispatch, the router re-runs the interrupted step while
its per-step retry budget remains and halts the workflow for a human once the
budget is exhausted — never advancing to the next step.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from resolve_config import resolve_config
from resolve_config.features.resolve_route.resolve_route import (
    _resolve_dispatch_path,
    resolve_max_soft_retries,
    resolve_soft_kill_route,
)
from workflow_state import Dispatch, SoftRetryState, WorkflowState


def _state(step="implement", agent="dev", attempts=0, counter_step="implement"):
    return WorkflowState(
        workflow="dev-review-loop",
        step=step,
        agent=agent,
        soft_retry=SoftRetryState(step=counter_step, attempts=attempts),
    )


class TestSoftKillRoute(unittest.TestCase):
    def test_non_soft_kill_dispatch_falls_through(self):
        result = resolve_soft_kill_route(
            _state(), Dispatch(status="advance"), max_soft_retries=2
        )
        self.assertIsNone(result)

    def test_retries_the_same_step_while_budget_remains(self):
        result = resolve_soft_kill_route(
            _state(attempts=0), Dispatch(status="soft_kill"), max_soft_retries=2
        )
        self.assertNotEqual(result.get("SKIP_AGENT"), "true")
        self.assertEqual(result["config_step"], "implement")
        self.assertEqual(result["task"], "dev")
        # The emitted retry config must flag itself as an engine retry so
        # build_state consumes exactly one budget attempt (issue #704).
        self.assertEqual(result["config_soft_retry_attempt"], "true")

    def test_retry_honors_the_dispatch_task_hint(self):
        result = resolve_soft_kill_route(
            _state(agent="dev"),
            Dispatch(status="soft_kill", task_hint="review"),
            max_soft_retries=2,
        )
        self.assertEqual(result["task"], "review")

    def test_second_retry_is_still_within_budget(self):
        result = resolve_soft_kill_route(
            _state(attempts=1), Dispatch(status="soft_kill"), max_soft_retries=2
        )
        self.assertNotEqual(result.get("SKIP_AGENT"), "true")
        self.assertEqual(result["config_step"], "implement")

    def test_exhausted_budget_halts_for_a_human(self):
        result = resolve_soft_kill_route(
            _state(attempts=2), Dispatch(status="soft_kill"), max_soft_retries=2
        )
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertIn("implement", result["REASON"])
        # A halt must tell the human how to take over.
        self.assertIn("/nebuko resume", result["REASON"])

    def test_exhausted_budget_marks_itself_for_the_halt_notice(self):
        """Issue #793: the halt carries a marker so the engine pings a human.

        Without it the terminal skip is indistinguishable from a routine one and
        the ``/nebuko resume`` instruction would live only in the CI log.
        """
        result = resolve_soft_kill_route(
            _state(attempts=2), Dispatch(status="soft_kill"), max_soft_retries=2
        )
        self.assertEqual(result["SOFT_RETRY_EXHAUSTED"], "true")

    def test_a_retry_does_not_carry_the_halt_marker(self):
        result = resolve_soft_kill_route(
            _state(attempts=0), Dispatch(status="soft_kill"), max_soft_retries=2
        )
        self.assertNotIn("SOFT_RETRY_EXHAUSTED", result)

    def test_zero_budget_halts_immediately(self):
        result = resolve_soft_kill_route(
            _state(attempts=0), Dispatch(status="soft_kill"), max_soft_retries=0
        )
        self.assertEqual(result["SKIP_AGENT"], "true")

    def test_counter_of_a_different_step_does_not_consume_budget(self):
        result = resolve_soft_kill_route(
            _state(step="review", agent="reviewer", attempts=5, counter_step="implement"),
            Dispatch(status="soft_kill"),
            max_soft_retries=2,
        )
        self.assertNotEqual(result.get("SKIP_AGENT"), "true")
        self.assertEqual(result["config_step"], "review")


class TestResolveMaxSoftRetries(unittest.TestCase):
    """The single, validated read site for the retry budget (issue #704)."""

    def test_reads_the_declared_value(self):
        self.assertEqual(resolve_max_soft_retries({"MAX_SOFT_RETRIES": "3"}), 3)

    def test_defaults_to_two_when_unset(self):
        self.assertEqual(resolve_max_soft_retries({}), 2)

    def test_defaults_when_malformed(self):
        self.assertEqual(resolve_max_soft_retries({"MAX_SOFT_RETRIES": "many"}), 2)

    def test_negative_is_clamped_to_zero(self):
        self.assertEqual(resolve_max_soft_retries({"MAX_SOFT_RETRIES": "-1"}), 0)


class TestDispatchPathSoftKillWiring(unittest.TestCase):
    """`_resolve_dispatch_path` must consult the soft-kill router first."""

    def test_soft_kill_dispatch_retries_in_place(self):
        result = _resolve_dispatch_path(
            _state(attempts=0),
            Dispatch(status="soft_kill"),
            "workflows",
        )
        self.assertEqual(result["config_step"], "implement")
        self.assertEqual(result["task"], "dev")


class TestSoftKillResumeAfterHalt(unittest.TestCase):
    """An exhausted soft-kill halt must be resumable by a human (issue #704).

    Round-3 review: the halt stop-gap must not become a dead end — a human
    ``/nebuko resume`` on the halted step re-runs that same step.
    """

    _WORKFLOW_YAML = (
        "name: dev-review-loop\n"
        "description: dev review loop\n"
        "steps:\n"
        "  - name: implement\n"
        "    agent: dev\n"
        "    next: review\n"
        "  - name: review\n"
        "    agent: review\n"
        "    next: null\n"
    )

    def _dirs(self):
        prompt_base = tempfile.mkdtemp()
        workflows_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(prompt_base, "dev"), exist_ok=True)
        with open(os.path.join(prompt_base, "dev", "system.md"), "w") as handle:
            handle.write("# dev")
        with open(
            os.path.join(workflows_dir, "dev-review-loop.yaml"), "w"
        ) as handle:
            handle.write(self._WORKFLOW_YAML)
        return prompt_base, workflows_dir

    def _resume_body(self, state):
        state_json = json.dumps(
            state.model_dump(mode="json", exclude={"pinned_content"})
        )
        return "\n".join(
            [
                "/nebuko resume --feedback resumed by human",
                "<details>",
                "<summary>🤖 Workflow State (v1)</summary>",
                "",
                "```json",
                "<!-- WORKFLOW_STATE_START -->",
                state_json,
                "<!-- WORKFLOW_STATE_END -->",
                "```",
                "",
                "</details>",
            ]
        )

    def test_resume_after_exhausted_soft_kill_reruns_the_step(self):
        prompt_base, workflows_dir = self._dirs()
        halted = _state(attempts=2)
        halted.dispatch = Dispatch(status="soft_kill")
        result = resolve_config(
            self._resume_body(halted), prompt_base, workflows_dir, is_bot=False
        )
        self.assertNotEqual(result.get("SKIP_AGENT"), "true")
        self.assertEqual(result["TASK"], "dev")
        self.assertEqual(result["WORKFLOW_STEP"], "implement")
        self.assertEqual(result["HUMAN_RESUME_INPUT"], "resumed by human")


class TestAgentCheckpointRoute(unittest.TestCase):
    """A fold (issue #965) routes through the soft-kill retry path, with its cap.

    ``agent_checkpoint`` is the dispatch a folded run carries: the step must
    re-run in place while the per-step budget remains and halt for a human once
    it is spent — never advance. Sharing MAX_SOFT_RETRIES with soft kills is the
    cap that stops folding from becoming an exit from hard tasks.
    """

    def test_checkpoint_is_not_treated_as_a_non_soft_dispatch(self):
        result = resolve_soft_kill_route(
            _state(), Dispatch(status="agent_checkpoint"), max_soft_retries=2
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["config_step"], "implement")

    def test_checkpoint_retries_the_same_step_while_budget_remains(self):
        result = resolve_soft_kill_route(
            _state(attempts=0), Dispatch(status="agent_checkpoint"), max_soft_retries=2
        )
        self.assertNotEqual(result.get("SKIP_AGENT"), "true")
        self.assertEqual(result["config_soft_retry_attempt"], "true")

    def test_checkpoint_consumes_the_same_budget_as_a_soft_kill(self):
        """A checkpoint recorded after one soft-kill attempt is the second try."""
        result = resolve_soft_kill_route(
            _state(attempts=1), Dispatch(status="agent_checkpoint"), max_soft_retries=2
        )
        self.assertNotEqual(result.get("SKIP_AGENT"), "true")

    def test_exhausted_checkpoint_budget_halts_for_a_human(self):
        result = resolve_soft_kill_route(
            _state(attempts=2), Dispatch(status="agent_checkpoint"), max_soft_retries=2
        )
        self.assertEqual(result["SKIP_AGENT"], "true")
        self.assertEqual(result["SOFT_RETRY_EXHAUSTED"], "true")
        self.assertIn("/nebuko resume", result["REASON"])


if __name__ == "__main__":
    unittest.main()
