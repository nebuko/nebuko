"""Tests for the NebukoRuntimeConfig sub-domain (issue #1094)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[4]))
from config import NebukoRuntimeConfig
from config.sub_domains.nebuko_config.nebuko_config import (
    DEFAULT_NEBUKO_CONFIG_PATH,
    NEBUKO_CONFIG_PATH_ENV,
    resolve_caller_root,
)


class TestCallerRoot:
    """The workspace relative config paths anchor at (issue #930 rules)."""

    def test_prefers_an_existing_github_workspace(self, tmp_path):
        assert resolve_caller_root({"GITHUB_WORKSPACE": str(tmp_path)}) == tmp_path.resolve()

    def test_ignores_a_nonexistent_github_workspace(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert resolve_caller_root({"GITHUB_WORKSPACE": str(tmp_path / "nope")}) == tmp_path.resolve()

    def test_falls_back_to_the_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert resolve_caller_root({}) == tmp_path.resolve()


class TestNebukoRuntimeConfig:
    """The flat schema and the load contract are the issue #1094 contract."""

    def test_flat_schema_defaults(self):
        config = NebukoRuntimeConfig()
        assert config.default_workflow is None
        assert config.agent_config_path is None

    def test_parses_flat_keys(self):
        config = NebukoRuntimeConfig.model_validate(
            {"default_workflow": "spec-driven", "agent_config_path": ".nebuko/agents/x.yaml"}
        )
        assert config.default_workflow == "spec-driven"
        assert config.agent_config_path == ".nebuko/agents/x.yaml"

    def test_unknown_keys_are_ignored(self):
        """Forward compatibility: future settings never break older readers."""
        config = NebukoRuntimeConfig.model_validate(
            {"default_workflow": "default", "pin_comment": True, "future": "x"}
        )
        assert config.default_workflow == "default"
        assert not hasattr(config, "pin_comment")

    def test_default_path_constant(self):
        assert DEFAULT_NEBUKO_CONFIG_PATH == ".nebuko/nebuko-config.yaml"
        assert NEBUKO_CONFIG_PATH_ENV == "NEBUKO_CONFIG_PATH"


class TestLoad:
    """``load`` resolves the file caller-anchored; absent means defaults."""

    def test_absent_file_yields_defaults(self, tmp_path):
        config = NebukoRuntimeConfig.load(caller_root=str(tmp_path), env={})
        assert config == NebukoRuntimeConfig()

    def test_reads_the_default_location(self, tmp_path):
        (tmp_path / ".nebuko").mkdir()
        (tmp_path / ".nebuko" / "nebuko-config.yaml").write_text(
            "default_workflow: spec-driven\n", encoding="utf-8"
        )

        config = NebukoRuntimeConfig.load(caller_root=str(tmp_path), env={})

        assert config.default_workflow == "spec-driven"
        assert config.agent_config_path is None

    def test_env_pin_absolute_is_honored_as_is(self, tmp_path):
        config_dir = tmp_path / "elsewhere"
        config_dir.mkdir()
        config_file = config_dir / "nebuko-config.yaml"
        config_file.write_text("default_workflow: spec-driven\n", encoding="utf-8")

        config = NebukoRuntimeConfig.load(
            caller_root=str(tmp_path), env={"NEBUKO_CONFIG_PATH": str(config_file)}
        )

        assert config.default_workflow == "spec-driven"

    def test_env_pin_relative_anchors_at_the_caller_root(self, tmp_path):
        nested = tmp_path / "configs"
        nested.mkdir()
        (nested / "nebuko-config.yaml").write_text(
            "agent_config_path: .nebuko/agents/agent-config.yaml\n", encoding="utf-8"
        )

        config = NebukoRuntimeConfig.load(
            caller_root=str(tmp_path), env={"NEBUKO_CONFIG_PATH": "configs/nebuko-config.yaml"}
        )

        assert config.agent_config_path == ".nebuko/agents/agent-config.yaml"

    def test_env_pin_defaults_to_the_caller_root_workspace(self, tmp_path):
        (tmp_path / ".nebuko").mkdir()
        (tmp_path / ".nebuko" / "nebuko-config.yaml").write_text(
            "default_workflow: spec-driven\n", encoding="utf-8"
        )

        config = NebukoRuntimeConfig.load(env={"GITHUB_WORKSPACE": str(tmp_path)})

        assert config.default_workflow == "spec-driven"

    def test_env_pin_set_but_missing_yields_defaults(self, tmp_path):
        """A set-but-missing pin does not fall back to the default location."""
        (tmp_path / ".nebuko").mkdir()
        (tmp_path / ".nebuko" / "nebuko-config.yaml").write_text(
            "default_workflow: spec-driven\n", encoding="utf-8"
        )

        config = NebukoRuntimeConfig.load(
            caller_root=str(tmp_path), env={"NEBUKO_CONFIG_PATH": str(tmp_path / "nope.yaml")}
        )

        assert config == NebukoRuntimeConfig()

    def test_empty_file_yields_defaults(self, tmp_path):
        (tmp_path / ".nebuko").mkdir()
        (tmp_path / ".nebuko" / "nebuko-config.yaml").write_text("", encoding="utf-8")

        assert NebukoRuntimeConfig.load(caller_root=str(tmp_path), env={}) == NebukoRuntimeConfig()

    def test_non_mapping_file_fails_loudly(self, tmp_path):
        (tmp_path / ".nebuko").mkdir()
        (tmp_path / ".nebuko" / "nebuko-config.yaml").write_text("- just\n- a list\n", encoding="utf-8")

        with pytest.raises(ValueError, match="must contain a YAML mapping"):
            NebukoRuntimeConfig.load(caller_root=str(tmp_path), env={})

    def test_schema_invalid_file_fails_loudly(self, tmp_path):
        (tmp_path / ".nebuko").mkdir()
        (tmp_path / ".nebuko" / "nebuko-config.yaml").write_text(
            "default_workflow: 42\n", encoding="utf-8"
        )

        with pytest.raises(ValueError):
            NebukoRuntimeConfig.load(caller_root=str(tmp_path), env={})
