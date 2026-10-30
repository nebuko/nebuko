"""Spec-scenario tests for the configuration-path resolution (issue #590)."""

from __future__ import annotations

from nebuko.engine.paths import (
    ConfigPathAnchor,
    LIB_DIR,
    defaults_dir,
    repo_root,
)


class TestRepoRoot:
    def test_points_at_the_checkout_that_holds_the_engine_package(self):
        root = repo_root()
        assert (root / "src" / "nebuko" / "engine" / "__init__.py").is_file()
        assert (root / "src" / "nebuko" / "adapters" / "base.py").is_file()

    def test_is_independent_of_the_current_working_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert (repo_root() / "src" / "nebuko" / "engine" / "__init__.py").is_file()


class TestLibDir:
    def test_points_at_the_migrated_orchestration_library(self):
        assert LIB_DIR.is_dir()
        assert (LIB_DIR / "event_normalizer.py").is_file()


class TestConfigPathAnchorResolve:
    """``ConfigPathAnchor.resolve`` anchors relative values at the caller (issue #930)."""

    def test_keeps_an_absolute_value(self, tmp_path):
        absolute = str(tmp_path / "agent-config.yaml")
        anchor = ConfigPathAnchor(absolute, tmp_path, repo_root())

        assert anchor.resolve() == absolute

    def test_anchors_a_separator_relative_value_at_the_caller_root(self, tmp_path):
        caller = tmp_path / "caller"
        engine = tmp_path / ".nebuko-engine"
        anchor = ConfigPathAnchor(".nebuko/agents/agent-config.yaml", caller, engine)

        assert anchor.resolve() == str(
            caller / ".nebuko" / "agents" / "agent-config.yaml"
        )
        assert ".nebuko-engine" not in anchor.resolve()

    def test_a_bare_name_prefers_the_callers_own_agents_dir(self, tmp_path):
        caller = tmp_path / "caller"
        (caller / ".nebuko" / "agents").mkdir(parents=True)
        (caller / ".nebuko" / "agents" / "agent-config.yaml").write_text("agents: {}")
        anchor = ConfigPathAnchor("agent-config.yaml", caller, repo_root())

        assert anchor.resolve() == str(
            caller / ".nebuko" / "agents" / "agent-config.yaml"
        )

    def test_a_bare_name_falls_back_to_the_engine_packaged_defaults(self, tmp_path):
        caller = tmp_path / "caller"
        caller.mkdir()
        shipped = defaults_dir() / "agents" / "agent-config-commandcode.yaml"
        assert shipped.is_file()
        anchor = ConfigPathAnchor("agent-config-commandcode.yaml", caller, repo_root())

        assert anchor.resolve() == str(shipped)
        assert anchor.resolve() == str(
            repo_root() / "defaults" / "agents" / "agent-config-commandcode.yaml"
        )

    def test_an_unknown_bare_name_falls_back_to_the_caller_root(self, tmp_path):
        caller = tmp_path / "caller"
        caller.mkdir()
        anchor = ConfigPathAnchor("missing.yaml", caller, repo_root())

        assert anchor.resolve() == str(caller / "missing.yaml")


class TestConfigPathAnchorCallerRoot:
    """``resolve_caller_root`` precedence: ``$GITHUB_WORKSPACE`` → cwd → engine."""

    def test_prefers_an_existing_github_workspace(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        assert (
            ConfigPathAnchor.resolve_caller_root(
                {"GITHUB_WORKSPACE": str(workspace)}, start_cwd=tmp_path
            )
            == workspace.resolve()
        )

    def test_falls_back_to_the_start_cwd_when_workspace_is_unset(self, tmp_path):
        start = tmp_path / "start"
        start.mkdir()

        assert ConfigPathAnchor.resolve_caller_root({}, start_cwd=start) == start.resolve()

    def test_falls_back_to_the_start_cwd_when_workspace_does_not_exist(self, tmp_path):
        start = tmp_path / "start"
        start.mkdir()

        assert (
            ConfigPathAnchor.resolve_caller_root(
                {"GITHUB_WORKSPACE": str(tmp_path / "ghost")}, start_cwd=start
            )
            == start.resolve()
        )

    def test_falls_back_to_the_engine_checkout_as_a_last_resort(self, tmp_path):
        ghost = tmp_path / "ghost"

        assert (
            ConfigPathAnchor.resolve_caller_root(
                {"GITHUB_WORKSPACE": str(ghost)}, start_cwd=ghost
            )
            == repo_root()
        )

    def test_from_env_derives_the_caller_root(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        anchor = ConfigPathAnchor.from_env(
            ".nebuko/agents/agent-config.yaml",
            engine_root=repo_root(),
            env={"GITHUB_WORKSPACE": str(workspace)},
        )

        assert anchor.caller_root == workspace.resolve()
        assert anchor.resolve() == str(
            workspace.resolve() / ".nebuko" / "agents" / "agent-config.yaml"
        )
