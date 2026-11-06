"""Shape of the root-cause workflow (issue #1348).

The workflow must keep the oracle engine-run (script steps), put a human gate
on the confirmation, loop a rejected diagnosis back to a new investigation, and
never route an agent past the seal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nebuko.engine.lib.config import WorkflowDefinition
from nebuko.engine.lint_cli import main as lint_main

REPO = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO / ".nebuko" / "workflows" / "root-cause.yaml"


@pytest.fixture(scope="module")
def definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(yaml.safe_load(WORKFLOW_PATH.read_text()))


def _step(definition: WorkflowDefinition, name: str):
    return next(step for step in definition.steps if step.name == name)


def _script_text(step) -> str:
    return " ".join([step.script.command, *step.script.args])


class TestTheGraph:
    def test_then_the_steps_run_in_the_documented_order(self, definition):
        assert [s.name for s in definition.steps] == [
            "investigate", "oracle_present", "confirm", "seal", "handoff",
        ]

    def test_then_it_passes_the_workflow_linter(self, capsys):
        assert lint_main(["--strict", str(WORKFLOW_PATH)]) == 0, capsys.readouterr().out

    def test_given_a_rejected_confirmation_then_it_loops_to_a_new_investigation(self, definition):
        routes = {c.on_result: c.next for c in _step(definition, "confirm").conditions}
        assert routes == {"approved": "seal", "rejected": "investigate"}

    def test_then_the_handoff_is_terminal(self, definition):
        assert _step(definition, "handoff").next is None


class TestTheOracleIsEngineRun:
    def test_then_the_presence_check_is_a_script_step_not_an_agent(self, definition):
        step = _step(definition, "oracle_present")
        assert step.agent is None and step.script is not None
        text = _script_text(step)
        assert "use-session-branch.sh" in text
        assert "evidence.py verify --issue auto" in text
        assert "evidence.py run --issue auto --expect present" in text

    def test_then_the_seal_is_a_script_step_that_verifies_the_manifest(self, definition):
        step = _step(definition, "seal")
        assert step.agent is None
        assert "evidence.py verify --issue auto" in _script_text(step)

    def test_then_the_confirmer_sees_the_engine_run_not_only_the_report(self, definition):
        assert set(_step(definition, "confirm").inputs) >= {"investigate", "oracle_present"}

    def test_then_the_handoff_sees_the_seal(self, definition):
        assert "seal" in _step(definition, "handoff").inputs


class TestTheGate:
    def test_then_the_confirmation_is_a_human_gate(self, definition):
        assert _step(definition, "confirm").human_gate is True

    def test_then_no_other_step_is_gated(self, definition):
        assert [s.name for s in definition.steps if s.human_gate] == ["confirm"]


class TestTheInvestigatorIsBoundedByPrompt:
    @pytest.mark.parametrize(
        "needle",
        [
            ".nebuko/evidence/issue-<n>/",
            "NEVER edit an earlier",
            "STOP RULE",
            "COUNTERFACTUAL",
            "evidence.py manifest",
            "appreciation 'hold'",
        ],
    )
    def test_then_the_prompt_states_the_rule(self, definition, needle):
        assert needle in _step(definition, "investigate").prompt

    def test_then_the_role_prompt_is_replaced_not_extended(self, definition):
        assert _step(definition, "investigate").prompt_replace is True

    def test_then_it_declares_a_deltas_table_and_a_counterfactual(self, definition):
        fields = {f.name: f for f in _step(definition, "investigate").output.fields}
        assert fields["deltas"].type == "table"
        assert fields["deltas"].columns == ["delta", "reported", "explained_by", "status"]
        assert fields["counterfactual"].required is not False
        assert fields["not_explained"].required is False
