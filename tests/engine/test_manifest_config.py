"""Non-secret engine settings from ``.nebuko/nebuko.yaml`` (issue #1094).

Precedence is explicit env / workflow input > manifest > built-in default, and
the engine applies the manifest at startup so a caller can centralise its
configuration in one file.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nebuko.engine.models import NebukoManifest
from nebuko.engine.paths import load_manifest, repo_root


class TestManifestFields:
    def test_new_fields_parse(self):
        manifest = NebukoManifest.model_validate(
            {
                "agent_config": ".nebuko/agents/agent-config.yaml",
                "circuit_breaker_threshold": 15,
                "history_comment_count": 3,
                "pin_comment": True,
            }
        )

        assert manifest.agent_config == ".nebuko/agents/agent-config.yaml"
        assert manifest.circuit_breaker_threshold == 15
        assert manifest.history_comment_count == 3
        assert manifest.pin_comment is True

    def test_every_field_is_optional(self):
        manifest = NebukoManifest()

        assert manifest.env_defaults() == {}
        assert manifest.overlay_env({}) == {}

    def test_unknown_key_is_ignored(self):
        # A manifest written for a newer engine must not abort an older run.
        manifest = NebukoManifest.model_validate(
            {"circuit_breaker_threshold": 15, "future_setting": True}
        )

        assert manifest.circuit_breaker_threshold == 15
        assert not hasattr(manifest, "future_setting")

    def test_threshold_below_one_is_rejected(self):
        with pytest.raises(ValidationError):
            NebukoManifest.model_validate({"circuit_breaker_threshold": 0})


class TestEnvDefaults:
    def test_maps_set_fields_to_their_env_names(self):
        manifest = NebukoManifest(
            agent_config="agent-config.yaml",
            circuit_breaker_threshold=15,
            history_comment_count=3,
            pin_comment=False,
        )

        assert manifest.env_defaults() == {
            "AGENT_CONFIG_PATH": "agent-config.yaml",
            "CIRCUIT_BREAKER_THRESHOLD": "15",
            "HISTORY_COMMENT_COUNT": "3",
            "PIN_COMMENT": "false",
        }


class TestOverlayEnv:
    def test_env_value_beats_the_manifest(self):
        manifest = NebukoManifest(circuit_breaker_threshold=15)

        merged = manifest.overlay_env({"CIRCUIT_BREAKER_THRESHOLD": "3"})

        assert merged["CIRCUIT_BREAKER_THRESHOLD"] == "3"

    def test_a_blank_env_value_takes_the_manifest(self):
        manifest = NebukoManifest(agent_config=".nebuko/agents/agent-config.yaml")

        merged = manifest.overlay_env({"AGENT_CONFIG_PATH": ""})

        assert merged["AGENT_CONFIG_PATH"] == ".nebuko/agents/agent-config.yaml"

    def test_a_missing_key_takes_the_manifest(self):
        manifest = NebukoManifest(history_comment_count=2)

        assert manifest.overlay_env({})["HISTORY_COMMENT_COUNT"] == "2"

    def test_a_key_neither_source_sets_stays_unset(self):
        manifest = NebukoManifest(default_workflow="default")

        merged = manifest.overlay_env({"AGENT_CONFIG_PATH": ""})

        assert "AGENT_CONFIG_PATH" not in merged

    def test_pin_comment_is_ored(self):
        manifest = NebukoManifest(pin_comment=True)

        assert manifest.overlay_env({"PIN_COMMENT": "false"})["PIN_COMMENT"] == "true"
        assert manifest.overlay_env({})["PIN_COMMENT"] == "true"

    def test_pin_comment_stays_false_when_neither_source_enables_it(self):
        manifest = NebukoManifest(pin_comment=False)

        assert manifest.overlay_env({"PIN_COMMENT": ""})["PIN_COMMENT"] == "false"

    def test_the_input_mapping_is_not_mutated(self):
        manifest = NebukoManifest(circuit_breaker_threshold=15)
        env = {"CIRCUIT_BREAKER_THRESHOLD": ""}

        manifest.overlay_env(env)

        assert env == {"CIRCUIT_BREAKER_THRESHOLD": ""}


class TestLoadManifest:
    def test_an_absent_file_yields_an_empty_manifest(self, tmp_path):
        assert load_manifest(tmp_path / "nebuko.yaml") == NebukoManifest()

    def test_reads_the_engine_settings(self, tmp_path):
        path = tmp_path / "nebuko.yaml"
        path.write_text("circuit_breaker_threshold: 15\npin_comment: true\n")

        manifest = load_manifest(path)

        assert manifest.circuit_breaker_threshold == 15
        assert manifest.pin_comment is True

    def test_a_non_mapping_is_rejected(self, tmp_path):
        path = tmp_path / "nebuko.yaml"
        path.write_text("- just\n- a list\n")

        with pytest.raises(ValueError):
            load_manifest(path)


class TestMainAppliesTheManifest:
    def _caller(self, tmp_path, monkeypatch, body: str):
        caller = tmp_path / "caller"
        (caller / ".nebuko").mkdir(parents=True)
        (caller / ".nebuko" / "nebuko.yaml").write_text(body)
        monkeypatch.setenv("GITHUB_WORKSPACE", str(caller))
        return caller

    def _run_main(self, tmp_path, monkeypatch, mocker):
        import nebuko.engine.run_loop as run_loop

        run = mocker.patch.object(
            run_loop.Engine,
            "run",
            autospec=True,
            return_value=mocker.Mock(exit_code=0),
        )
        run_loop.main(["--adapter", "local", "--run-dir", str(tmp_path / "run")])
        return run.call_args[0][0]

    def test_manifest_settings_reach_the_engine(self, tmp_path, monkeypatch, mocker):
        for key in ("AGENT_CONFIG_PATH", "CIRCUIT_BREAKER_THRESHOLD", "HISTORY_COMMENT_COUNT"):
            monkeypatch.delenv(key, raising=False)
        self._caller(
            tmp_path,
            monkeypatch,
            "agent_config: .nebuko/agents/agent-config.yaml\n"
            "circuit_breaker_threshold: 15\n"
            "history_comment_count: 2\n",
        )

        engine = self._run_main(tmp_path, monkeypatch, mocker)

        assert engine.env["AGENT_CONFIG_PATH"] == ".nebuko/agents/agent-config.yaml"
        assert engine.env["CIRCUIT_BREAKER_THRESHOLD"] == "15"
        assert engine.history_limit == 2

    def test_an_explicit_env_value_beats_the_manifest(self, tmp_path, monkeypatch, mocker):
        monkeypatch.setenv("HISTORY_COMMENT_COUNT", "7")
        self._caller(tmp_path, monkeypatch, "history_comment_count: 2\n")

        engine = self._run_main(tmp_path, monkeypatch, mocker)

        assert engine.history_limit == 7

    def test_a_missing_manifest_leaves_the_env_untouched(self, tmp_path, monkeypatch, mocker):
        monkeypatch.delenv("CIRCUIT_BREAKER_THRESHOLD", raising=False)
        workspace = tmp_path / "empty"
        workspace.mkdir()
        monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))

        engine = self._run_main(tmp_path, monkeypatch, mocker)

        assert "CIRCUIT_BREAKER_THRESHOLD" not in engine.env


class TestThresholdFallback:
    def test_an_unset_threshold_falls_back_to_the_workflow_default(self, tmp_path):
        from nebuko.adapters.local import LocalAdapter
        from nebuko.engine.models import Trigger
        from nebuko.engine.run_loop import Engine

        trigger = Trigger(
            author="olivier-norture",
            body="@@dev fix it",
            type="issue_comment",
            number=1,
        )
        engine = Engine(
            adapter=LocalAdapter(tmp_path / "run", trigger=trigger),
            repo_root=repo_root(),
            env={},
        )

        assert engine._threshold() == 15


class TestDefaultWorkflowReader:
    """One manifest reader: routing reuses ``paths.load_manifest`` (issue #1094)."""

    def test_default_workflow_name_uses_the_manifest_loader(self, tmp_path, monkeypatch):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import (
            _default_workflow_name,
        )

        (tmp_path / ".nebuko").mkdir()
        (tmp_path / ".nebuko" / "nebuko.yaml").write_text(
            "default_workflow: review-only\ncircuit_breaker_threshold: 15\n"
        )
        monkeypatch.chdir(tmp_path)

        assert _default_workflow_name() == "review-only"

    def test_an_unreadable_manifest_degrades_to_the_default(self, tmp_path, monkeypatch):
        from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import (
            _default_workflow_name,
        )

        (tmp_path / ".nebuko").mkdir()
        (tmp_path / ".nebuko" / "nebuko.yaml").write_text("unknown_key: 1\n")
        monkeypatch.chdir(tmp_path)

        assert _default_workflow_name() == "default"


class TestTestCommand:
    def test_command_and_timeout_are_exported(self):
        manifest = NebukoManifest.model_validate(
            {"test_command": "uv run pytest -q", "test_timeout_minutes": 30}
        )

        assert manifest.env_defaults() == {
            "NEBUKO_TEST_COMMAND": "uv run pytest -q",
            "NEBUKO_TEST_TIMEOUT_MINUTES": "30",
        }

    def test_the_command_is_stripped(self):
        manifest = NebukoManifest.model_validate({"test_command": "  make test \n"})

        assert manifest.env_defaults()["NEBUKO_TEST_COMMAND"] == "make test"

    @pytest.mark.parametrize("value", ["", "   ", "\n"])
    def test_a_blank_command_counts_as_absent(self, value):
        manifest = NebukoManifest.model_validate({"test_command": value})

        assert "NEBUKO_TEST_COMMAND" not in manifest.env_defaults()
        assert "NEBUKO_TEST_COMMAND" not in manifest.overlay_env({"NEBUKO_TEST_COMMAND": " "})

    def test_an_absent_command_leaves_no_variable(self):
        assert "NEBUKO_TEST_COMMAND" not in NebukoManifest().overlay_env({})

    def test_an_explicit_env_value_beats_the_manifest(self):
        manifest = NebukoManifest.model_validate({"test_command": "make test"})

        merged = manifest.overlay_env({"NEBUKO_TEST_COMMAND": "pytest -x"})

        assert merged["NEBUKO_TEST_COMMAND"] == "pytest -x"

    def test_a_timeout_below_one_minute_is_rejected(self):
        with pytest.raises(ValidationError):
            NebukoManifest.model_validate({"test_timeout_minutes": 0})


class TestBaseBranchValidation:
    @pytest.mark.parametrize("name", ["main", "trunk", "release/1.x", "feature-2_b.c"])
    def test_a_git_branch_name_is_accepted(self, name):
        manifest = NebukoManifest.model_validate({"base_branch": name})

        assert manifest.env_defaults()["NEBUKO_BASE_BRANCH"] == name

    @pytest.mark.parametrize(
        "name",
        [
            "main; touch PWNED",
            "main$(touch PWNED)",
            "main`id`",
            "a b",
            "a|b",
            "../etc",
            "a..b",
            "-rf",
            "/main",
            "main/",
            "main.lock",
            "x" * 201,
        ],
    )
    def test_a_hostile_or_malformed_name_is_rejected_with_a_clear_message(self, name):
        with pytest.raises(ValidationError, match="not a valid git branch name"):
            NebukoManifest.model_validate({"base_branch": name})

    def test_a_blank_base_branch_is_absent(self):
        manifest = NebukoManifest.model_validate({"base_branch": "  "})

        assert "NEBUKO_BASE_BRANCH" not in manifest.env_defaults()
