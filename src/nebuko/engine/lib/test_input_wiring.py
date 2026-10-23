"""Tests for deterministic step-input materialization (issue #813).

The engine — not the model — runs a step's declared ``exec`` providers and
injects their captured output, so a review context bundle is guaranteed rather
than model-discretionary. These tests pin the payload decoding, the frame
resolution order (kind-preserving lazy forwarding), the ``required`` failure
policy, and the labeled-block rendering.
"""

from __future__ import annotations

import json

import pytest

from nebuko.engine.input_wiring import (  # noqa: E402
    MAX_CALL_FRAMES,
    InputWiringError,
    Materialization,
    StepInputs,
    materialize,
    render,
    to_records,
)


def _payload(frames=(), consumer=None, specs=None) -> str:
    return json.dumps(
        {"frames": list(frames), "consumer": consumer or {}, "specs": specs or {}},
        separators=(",", ":"),
    )


class TestStepInputsDecoding:
    def test_absent_payload_yields_none(self):
        assert StepInputs.from_resolved({}) is None
        assert StepInputs.from_resolved({"STEP_INPUTS": ""}) is None

    def test_payload_decodes_frames_consumer_and_specs(self):
        raw = _payload(
            consumer={"diff": {"exec": ["echo", "x"]}},
            specs={"diff": {"kind": "exec", "required": True}},
        )
        step_inputs = StepInputs.from_resolved({"STEP_INPUTS": raw})
        assert step_inputs is not None
        assert step_inputs.consumer["diff"].exec_ == ["echo", "x"]
        assert step_inputs.specs["diff"]["kind"] == "exec"

    def test_malformed_json_raises_typed_error(self):
        with pytest.raises(InputWiringError, match="invalid STEP_INPUTS"):
            StepInputs.from_resolved({"STEP_INPUTS": "{not json"})

    def test_non_object_payload_raises_typed_error(self):
        with pytest.raises(InputWiringError, match="must decode to an object"):
            StepInputs.from_resolved({"STEP_INPUTS": "[]"})


class TestMaterialize:
    def test_exec_binding_captures_stdout(self):
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(consumer={"diff": {"exec": ["echo", "hello"]}})}
        )
        results = materialize(step_inputs)
        assert results == [
            Materialization(name="diff", ok=True, body="hello\n", binding="exec")
        ]

    def test_str_binding_returns_the_constant(self):
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(consumer={"name": {"str": "nebuko"}})}
        )
        assert materialize(step_inputs)[0].body == "nebuko"

    def test_failing_exec_is_captured_as_data_not_raised(self):
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(
                consumer={"diff": {"exec": ["sh", "-c", "exit 7"]}},
            )}
        )
        result = materialize(step_inputs)[0]
        assert result.ok is False
        assert "7" in result.detail

    def test_consumer_ref_resolves_through_a_frame_binding(self):
        frames = [{
            "callee": "impl",
            "call_step": "build",
            "bindings": {"diff": {"exec": ["echo", "one"]}},
        }]
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(
                frames=frames, consumer={"diff": "input.diff"},
            )}
        )
        assert materialize(step_inputs)[0].body == "one\n"

    def test_unresolved_consumer_ref_is_a_failure(self):
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(consumer={"diff": "input.missing"})}
        )
        result = materialize(step_inputs)[0]
        assert result.ok is False
        assert "unresolved" in result.detail

    def test_three_level_kind_preserving_lazy_forwarding(self):
        """super-parent -> middle -> leaf: an ``exec`` forwards unmaterialized."""
        frames = [
            {
                "callee": "spec-driven",
                "call_step": "call_spec",
                "bindings": {"run_test": {"exec": ["echo", "1 passed"]}},
            },
            {
                "callee": "spec-driven-impl",
                "call_step": "build",
                "bindings": {"run_test": "input.run_test"},
            },
        ]
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(
                frames=frames, consumer={"run_test": "input.run_test"},
            )}
        )
        result = materialize(step_inputs)[0]
        assert result.ok is True
        assert result.body == "1 passed\n"

    def test_forwarded_exec_runs_only_at_the_consuming_step(self, tmp_path):
        """A frame binding is frozen, never executed at the call boundary."""
        marker = tmp_path / "ran.txt"
        frames = [{
            "callee": "impl",
            "call_step": "build",
            "bindings": {"setup": {"exec": ["sh", "-c", f"echo ran > {marker}"]}},
        }]
        # A consumer that never references the frame binding must not trigger it.
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(frames=frames, consumer={"x": {"str": "v"}})}
        )
        materialize(step_inputs)
        assert not marker.exists()

        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(frames=frames, consumer={"setup": "input.setup"})}
        )
        materialize(step_inputs)
        assert marker.read_text().strip() == "ran"


class TestRequiredPolicy:
    def _failed(self):
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(
                consumer={"diff": {"exec": ["sh", "-c", "exit 1"]}},
                specs={"diff": {"kind": "exec", "required": True}},
            )}
        )
        results = materialize(step_inputs)
        return step_inputs, results

    def test_required_failure_is_reported(self):
        step_inputs, results = self._failed()
        assert [failure.name for failure in step_inputs.required_failures(results)] == ["diff"]

    def test_optional_failure_is_not_required(self):
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(
                consumer={"diff": {"exec": ["sh", "-c", "exit 1"]}},
                specs={"diff": {"kind": "exec", "required": False}},
            )}
        )
        results = materialize(step_inputs)
        assert step_inputs.required_failures(results) == []

    def test_unknown_input_defaults_to_required(self):
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(consumer={"x": "input.gone"})}
        )
        results = materialize(step_inputs)
        assert len(step_inputs.required_failures(results)) == 1


class TestCallFrameDepthGuard:
    """The frame chain is bounded so nested forwarding cannot run away."""

    def _frames(self, count: int) -> list:
        return [
            {"callee": f"wf-{i}", "call_step": f"s{i}", "bindings": {}}
            for i in range(count)
        ]

    def _step_inputs(self, count: int) -> StepInputs:
        return StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(
                frames=self._frames(count), consumer={"x": {"str": "v"}},
            )}
        )

    def test_chain_at_the_limit_is_allowed(self):
        assert self._step_inputs(MAX_CALL_FRAMES).depth_exceeded() is False

    def test_chain_past_the_limit_is_flagged(self):
        assert self._step_inputs(MAX_CALL_FRAMES + 1).depth_exceeded() is True


class TestRender:
    def test_empty_results_render_nothing(self):
        assert render([]) == ""

    def test_ok_results_render_as_labeled_blocks(self):
        rendered = render([Materialization(name="diff", ok=True, body="patch\n")])
        assert rendered.startswith("## Step inputs (deterministic)")
        assert "#### `diff`" in rendered
        assert "patch" in rendered

    def test_failed_results_render_a_placeholder(self):
        rendered = render(
            [Materialization(name="diff", ok=False, body="", detail="exit 3")]
        )
        assert "_(unavailable: exit 3)_" in rendered


class TestStepScopeResolution:
    """Bare ``<step>`` refs resolve against ``step_scope`` (issue #756)."""

    def _consumer(self, **consumer):
        return StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(consumer=consumer)}
        )

    def test_bare_step_ref_resolves_to_its_comment(self):
        step_inputs = self._consumer(plan="plan")
        result = materialize(step_inputs, {"plan": "THE PLAN COMMENT"})[0]
        assert result.ok is True
        assert result.body == "THE PLAN COMMENT"

    def test_missing_step_scope_key_is_unresolved(self):
        step_inputs = self._consumer(plan="plan")
        result = materialize(step_inputs, {})[0]
        assert result.ok is False
        assert "unresolved" in result.detail

    def test_step_scope_defaults_to_none(self):
        step_inputs = self._consumer(plan="plan")
        result = materialize(step_inputs)[0]
        assert result.ok is False

    def test_input_parameter_takes_precedence_over_step_scope(self):
        frames = [{
            "callee": "impl",
            "call_step": "build",
            "bindings": {"plan": {"str": "from the frame"}},
        }]
        step_inputs = StepInputs.from_resolved(
            {"STEP_INPUTS": _payload(frames=frames, consumer={"plan": "input.plan"})}
        )
        result = materialize(step_inputs, {"input.plan": "FROM COMMENT"})[0]
        assert result.body == "from the frame"

    def test_two_refs_fan_in(self):
        step_inputs = self._consumer(plan="plan", implement="implement")
        results = materialize(
            step_inputs, {"plan": "P", "implement": "I"}
        )
        assert {r.name: r.body for r in results} == {"plan": "P", "implement": "I"}


class TestToRecords:
    """Persisted step-input records (issue #872) round-trip into the state."""

    def _records(self, results):
        return to_records(results)

    def test_materialization_fields_are_carried(self):
        records = self._records(
            [Materialization(name="diff", ok=True, body="patch\n", binding="exec")]
        )
        assert records == [
            {
                "name": "diff",
                "binding": "exec",
                "ok": True,
                "detail": "",
                "value": "patch\n",
                "truncated": False,
            }
        ]

    def test_failure_detail_is_carried(self):
        records = self._records(
            [Materialization(name="diff", ok=False, body="", detail="exit 7", binding="exec")]
        )
        assert records[0]["ok"] is False
        assert records[0]["detail"] == "exit 7"

    def test_oversized_value_is_truncated_and_flagged(self):
        from workflow_state.sub_domains.step_input_record.step_input_record import (
            MAX_VALUE_CHARS,
        )

        records = self._records(
            [
                Materialization(
                    name="big", ok=True, body="x" * (MAX_VALUE_CHARS + 5)
                )
            ]
        )
        assert len(records[0]["value"]) == MAX_VALUE_CHARS
        assert records[0]["truncated"] is True

    def test_records_validate_as_step_input_records(self):
        from workflow_state.sub_domains.step_input_record import StepInputRecord

        records = self._records(
            [Materialization(name="diff", ok=True, body="patch", binding="str")]
        )
        assert StepInputRecord.model_validate(records[0]).name == "diff"

    def test_empty_results_yield_no_records(self):
        assert to_records([]) == []
