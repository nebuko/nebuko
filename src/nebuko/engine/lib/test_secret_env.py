"""Tests for secret_env.py and the secret-slot wiring in nebuko-workflow.yaml.

Covers the pure slot-pairing/materialization logic plus shape assertions on the
reusable workflow: nine NAME inputs, nine VALUE secrets, a materialize step
that precedes the engine step, and no ``toJSON(secrets)`` blob anywhere.
"""

from pathlib import Path

import yaml

from secret_env import EXTRA_SECRET_SLOTS, RESERVED_ENV_NAMES, SecretEnvironment

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW_YAML = REPO_ROOT / ".github" / "workflows" / "nebuko-workflow.yaml"

NAME_KEYS = [f"EXTRA_SECRET_{i}_NAME" for i in range(1, EXTRA_SECRET_SLOTS + 1)]
VALUE_KEYS = [f"EXTRA_SECRET_{i}_VALUE" for i in range(1, EXTRA_SECRET_SLOTS + 1)]

MATERIALIZE_STEP = "Materialize extra secret slots as env vars"
ENGINE_STEP = "Run orchestration engine"


def slot_pair(index: int, name: str, value: str) -> dict[str, str]:
    return {f"EXTRA_SECRET_{index}_NAME": name, f"EXTRA_SECRET_{index}_VALUE": value}


class TestFromSlots:
    """Pairing and filtering semantics of ``SecretEnvironment.from_slots``."""

    def test_pairs_name_and_value_across_all_nine_slots(self):
        env = {}
        for index in range(1, EXTRA_SECRET_SLOTS + 1):
            env.update(slot_pair(index, f"MCP_KEY_{index}", f"value-{index}"))

        secret_env = SecretEnvironment.from_slots(env)

        assert dict(secret_env.values) == {
            f"MCP_KEY_{index}": f"value-{index}" for index in range(1, EXTRA_SECRET_SLOTS + 1)
        }

    def test_empty_env_yields_empty_mapping(self):
        assert dict(SecretEnvironment.from_slots({}).values) == {}

    def test_name_without_value_is_dropped(self):
        env = {"EXTRA_SECRET_1_NAME": "FOO_API_KEY"}

        assert dict(SecretEnvironment.from_slots(env).values) == {}

    def test_value_without_name_is_dropped(self):
        env = {"EXTRA_SECRET_1_VALUE": "secret"}

        assert dict(SecretEnvironment.from_slots(env).values) == {}

    def test_blank_name_is_dropped(self):
        env = slot_pair(1, "   ", "secret")

        assert dict(SecretEnvironment.from_slots(env).values) == {}

    def test_name_is_stripped(self):
        env = slot_pair(1, "  FOO_API_KEY  ", "secret")

        assert dict(SecretEnvironment.from_slots(env).values) == {"FOO_API_KEY": "secret"}

    def test_exact_reserved_names_are_dropped(self):
        for reserved in sorted(RESERVED_ENV_NAMES):
            env = slot_pair(1, reserved, "secret")

            assert dict(SecretEnvironment.from_slots(env).values) == {}

    def test_reserved_prefixes_are_dropped(self):
        for reserved in ("GITHUB_FOO", "ACTIONS_BAR", "RUNNER_TEMP"):
            env = slot_pair(1, reserved, "secret")

            assert dict(SecretEnvironment.from_slots(env).values) == {}

    def test_duplicate_name_keeps_first_populated_slot(self):
        env = {}
        env.update(slot_pair(1, "FOO_API_KEY", "first"))
        env.update(slot_pair(2, "FOO_API_KEY", "second"))

        assert dict(SecretEnvironment.from_slots(env).values) == {"FOO_API_KEY": "first"}

    def test_first_populated_slot_wins_even_when_not_first_slot(self):
        env = {}
        env.update(slot_pair(1, "NON_RESERVED_NAME", ""))
        env.update(slot_pair(3, "FOO_API_KEY", "third"))
        env.update(slot_pair(5, "FOO_API_KEY", "fifth"))

        assert dict(SecretEnvironment.from_slots(env).values) == {"FOO_API_KEY": "third"}


class TestGithubEnvLines:
    """Rendering to the ``$GITHUB_ENV`` writer format."""

    def test_empty_mapping_renders_no_lines(self):
        assert SecretEnvironment.from_slots({}).github_env_lines() == ()

    def test_single_line_value_renders_a_heredoc_block(self):
        secret_env = SecretEnvironment(values={"FOO_API_KEY": "secret"})

        lines = secret_env.github_env_lines()

        assert lines[0].startswith("FOO_API_KEY<<")
        delimiter = lines[0].split("<<", 1)[1]
        assert lines[1] == "secret"
        assert lines[2] == delimiter

    def test_multiline_value_is_preserved(self):
        secret_env = SecretEnvironment(values={"FOO_API_KEY": "line1\nline2"})

        lines = secret_env.github_env_lines()

        delimiter = lines[0].split("<<", 1)[1]
        assert lines[1] == "line1\nline2"
        assert lines[2] == delimiter

    def test_delimiter_never_occurs_in_the_value(self):
        secret_env = SecretEnvironment(values={"FOO_API_KEY": "NEBUKO_SECRET_EOF_0"})

        lines = secret_env.github_env_lines()

        delimiter = lines[0].split("<<", 1)[1]
        assert delimiter not in lines[1]
        assert delimiter == "NEBUKO_SECRET_EOF_1"


class TestWorkflowShape:
    """The reusable workflow declares and orders the secret-slot machinery."""

    @staticmethod
    def _workflow() -> dict:
        return yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))

    def test_declares_nine_name_inputs(self):
        # PyYAML parses the unquoted `on:` top-level key as the boolean True.
        inputs = self._workflow()[True]["workflow_call"]["inputs"]
        for key in NAME_KEYS:
            assert inputs[key]["type"] == "string"
            assert inputs[key]["default"] == ""

    def test_declares_nine_value_secrets(self):
        secrets = self._workflow()[True]["workflow_call"]["secrets"]
        for key in VALUE_KEYS:
            assert secrets[key]["required"] is False

    def test_materialize_step_precedes_engine_step(self):
        steps = self._workflow()["jobs"]["run-task"]["steps"]
        names = [step.get("name") for step in steps]
        assert MATERIALIZE_STEP in names
        assert names.index(MATERIALIZE_STEP) < names.index(ENGINE_STEP)

    def test_materialize_step_feeds_every_slot_pair(self):
        steps = self._workflow()["jobs"]["run-task"]["steps"]
        materialize = next(step for step in steps if step.get("name") == MATERIALIZE_STEP)
        env = materialize["env"]
        for key in NAME_KEYS:
            assert env[key] == f"${{{{ inputs.{key} }}}}"
        for key in VALUE_KEYS:
            assert env[key] == f"${{{{ secrets.{key} }}}}"

    def test_workflow_contains_no_tojson_secret_blob(self):
        assert "toJSON(" not in WORKFLOW_YAML.read_text(encoding="utf-8")
