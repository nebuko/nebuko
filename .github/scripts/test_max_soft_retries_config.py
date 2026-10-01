"""Config surface for the soft-kill retry budget (issue #704).

The ``max_soft_retries`` workflow_call input (default 2) must be declared in
the reusable multi-agent workflow and exported as ``MAX_SOFT_RETRIES`` on the
"Resolve Agent Configuration" step — the environment ``resolve_max_soft_retries``
reads the budget from. Guards against the input being dropped from the YAML.
"""

import os
import unittest

import yaml

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORKFLOW = os.path.join(
    _REPO_ROOT, ".github", "workflows", "openhands-multi-agent.yml"
)


def _load_workflow():
    with open(_WORKFLOW, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _workflow_call(data):
    # PyYAML resolves the bare ``on:`` key to the boolean ``True`` (YAML 1.1).
    return data.get(True, data.get("on"))["workflow_call"]


def _resolve_agent_step(data):
    for job in data.get("jobs", {}).values():
        for step in job.get("steps", []):
            if step.get("name") == "Resolve Agent Configuration":
                return step
    return None


class TestMaxSoftRetriesWorkflowInput(unittest.TestCase):
    def test_declared_as_number_defaulting_to_two(self):
        declaration = _workflow_call(_load_workflow())["inputs"]["max_soft_retries"]
        self.assertEqual(declaration["type"], "number")
        self.assertEqual(declaration["default"], 2)
        self.assertFalse(declaration["required"])

    def test_exported_as_env_on_the_resolve_step(self):
        step = _resolve_agent_step(_load_workflow())
        self.assertIsNotNone(step)
        self.assertEqual(
            step["env"]["MAX_SOFT_RETRIES"], "${{ inputs.max_soft_retries }}"
        )


if __name__ == "__main__":
    unittest.main()
