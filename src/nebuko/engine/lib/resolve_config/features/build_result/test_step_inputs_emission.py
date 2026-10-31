"""Tests for the resolver's STEP_INPUTS emission (issue #813).

The resolver turns the declared wiring into a single JSON contract — the run's
call-frame stack plus this step's own bindings and its workflow's signature —
so the engine can materialize providers just before the agent runs. These tests
pin the emission, and that a workflow wiring nothing stays byte-for-byte
unchanged.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from nebuko.engine.lib.config import StepRef, WorkflowPosition
from nebuko.engine.lib.resolve_config.features.build_result.build_result import (
    _attach_step_inputs,
    _build_result,
)
from nebuko.engine.lib.resolve_workflow import TASK_TO_PROMPT


def _make_dirs() -> tuple[str, str]:
    prompt_base = tempfile.mkdtemp()
    for name in TASK_TO_PROMPT.values():
        Path(prompt_base, name).write_text(f"# {name}")
    return prompt_base, tempfile.mkdtemp()


def _write(workflows_dir: str, name: str, body: str) -> None:
    Path(workflows_dir, name).write_text(body)


class TestConsumerEmission:
    def test_step_without_inputs_emits_nothing(self):
        prompt_base, workflows_dir = _make_dirs()
        _write(workflows_dir, "plain.yaml", """
name: plain
description: d
steps:
  - name: work
    agent: dev
""")
        result = _build_result(
            {"task": "dev", "config_workflow": "plain", "config_step": "work"},
            prompt_base, workflows_dir,
        )
        assert "STEP_INPUTS" not in result

    def test_consumer_exec_binding_is_emitted_with_specs(self):
        prompt_base, workflows_dir = _make_dirs()
        _write(workflows_dir, "review.yaml", """
name: review
description: d
inputs:
  - name: diff
    kind: exec
steps:
  - name: inspect
    agent: dev
    inputs:
      diff:
        exec: ["echo", "hello"]
""")
        result = _build_result(
            {"task": "dev", "config_workflow": "review", "config_step": "inspect"},
            prompt_base, workflows_dir,
        )
        payload = json.loads(result["STEP_INPUTS"])
        assert payload.get("frames", []) == []
        assert payload["consumer"] == {"diff": {"exec": ["echo", "hello"]}}
        assert payload["specs"]["diff"] == {"kind": "exec", "required": True}

    def test_bare_string_consumer_binding_becomes_a_ref(self):
        prompt_base, workflows_dir = _make_dirs()
        _write(workflows_dir, "review.yaml", """
name: review
description: d
steps:
  - name: inspect
    agent: dev
    inputs:
      diff: input.diff
""")
        result = _build_result(
            {"task": "dev", "config_workflow": "review", "config_step": "inspect"},
            prompt_base, workflows_dir,
        )
        assert json.loads(result["STEP_INPUTS"])["consumer"] == {
            "diff": {"ref": "input.diff"}
        }


class TestFrameEmission:
    def _trio(self, workflows_dir: str) -> None:
        _write(workflows_dir, "super-parent.yaml", """
name: super-parent
description: root
steps:
  - name: call_spec
    workflow: spec-driven
    inputs:
      run_test:
        exec: ["echo", "1 passed"]
""")
        _write(workflows_dir, "spec-driven.yaml", """
name: spec-driven
description: middle
inputs:
  - name: run_test
    kind: exec
steps:
  - name: build
    workflow: spec-driven-impl
    inputs:
      run_test: input.run_test
""")
        _write(workflows_dir, "spec-driven-impl.yaml", """
name: spec-driven-impl
description: leaf
inputs:
  - name: run_test
    kind: exec
steps:
  - name: stub
    agent: dev
    inputs:
      run_test: input.run_test
""")

    def test_frames_are_derived_from_the_carried_position(self):
        _, workflows_dir = _make_dirs()
        self._trio(workflows_dir)
        position = WorkflowPosition(refs=[
            StepRef(workflow="super-parent", step="call_spec"),
            StepRef(workflow="spec-driven", step="build"),
            StepRef(workflow="spec-driven-impl", step="stub"),
        ])
        result = {
            "WORKFLOW_POSITION": json.dumps(position.to_state_dict()),
            "WORKFLOW_NAME": "spec-driven-impl",
            "WORKFLOW_STEP": "stub",
        }
        wired = _attach_step_inputs(result, {}, workflows_dir, None)
        payload = json.loads(wired["STEP_INPUTS"])
        assert [frame["callee"] for frame in payload["frames"]] == [
            "spec-driven", "spec-driven-impl",
        ]
        assert payload["frames"][0]["bindings"] == {
            "run_test": {"exec": ["echo", "1 passed"]}
        }
        # The middle layer forwards the parameter, kind-preserving and unmaterialized.
        assert payload["frames"][1]["bindings"] == {
            "run_test": {"ref": "input.run_test"}
        }
        assert payload["consumer"] == {"run_test": {"ref": "input.run_test"}}

    def test_top_level_run_emits_no_frames(self):
        _, workflows_dir = _make_dirs()
        position = WorkflowPosition.bare("review", "inspect")
        result = {
            "WORKFLOW_POSITION": json.dumps(position.to_state_dict()),
            "WORKFLOW_NAME": "review",
            "WORKFLOW_STEP": "inspect",
        }
        assert _attach_step_inputs(result, {}, workflows_dir, None) == result

    def test_depth_two_entry_splices_the_full_mount_chain(self):
        """Entering the leaf from a mounted middle workflow keeps every ancestor.

        The resolver's entry path rebuilds the position from the *current*
        run's workflow, so entering ``spec-driven-impl`` from ``spec-driven``
        alone would drop ``super-parent``; the carried-forward base underpins
        the emitted tail so the super-parent frame survives (issue #813).
        """
        prompt_base, workflows_dir = _make_dirs()
        self._trio(workflows_dir)
        base = WorkflowPosition(refs=[
            StepRef(workflow="super-parent", step="call_spec"),
            StepRef(workflow="spec-driven", step="build"),
        ])

        result = _build_result(
            {
                "task": "dev",
                "config_workflow": "spec-driven",
                "config_step": "build",
                "config_position": base,
            },
            prompt_base,
            workflows_dir,
        )

        assert result["WORKFLOW_NAME"] == "spec-driven-impl"
        refs = json.loads(result["WORKFLOW_POSITION"])["refs"]
        assert [(r["workflow"], r["step"]) for r in refs] == [
            ("super-parent", "call_spec"),
            ("spec-driven", "build"),
            ("spec-driven-impl", "stub"),
        ]
        frames = json.loads(result["STEP_INPUTS"])["frames"]
        assert [frame["callee"] for frame in frames] == [
            "spec-driven", "spec-driven-impl",
        ]
        assert frames[0]["call_step"] == "call_spec"
