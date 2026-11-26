"""Agent-config location tables and name precedence (issue #1479)."""

import pytest

from nebuko.engine.lib.resolve_config.features.resolve_route.agent_config_resolver import (
    AgentConfigNotFound,
    AgentConfigResolver,
    personal_run_from_env,
    required_secret_envs,
    select_config_name,
)


def _write(path, text="x: 1\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def roots(tmp_path):
    caller = tmp_path / "project"
    personal = tmp_path / "project" / ".nebuko-personal"
    shipped = tmp_path / "shipped" / "defaults" / "agents"
    return caller, personal, shipped


class TestProjectLane:
    def test_project_override_wins_over_shipped(self, roots):
        caller, personal, shipped = roots
        _write(caller / ".nebuko" / "agents" / "a.yaml")
        _write(shipped / "a.yaml")
        found = AgentConfigResolver.for_run("project", caller, None, shipped).resolve("a.yaml")
        assert found.layer == "project"
        assert found.path == caller / ".nebuko" / "agents" / "a.yaml"

    def test_missing_override_falls_back_to_shipped(self, roots):
        caller, personal, shipped = roots
        _write(shipped / "b.yaml")
        found = AgentConfigResolver.for_run("project", caller, None, shipped).resolve("b.yaml")
        assert found.layer == "shipped"
        assert found.searched == ["project", "shipped"]

    def test_name_found_nowhere_lists_the_layers(self, roots):
        caller, personal, shipped = roots
        with pytest.raises(AgentConfigNotFound) as info:
            AgentConfigResolver.for_run("project", caller, None, shipped).resolve("nope.yaml")
        assert info.value.searched == ["project", "shipped"]
        assert "project, shipped" in str(info.value)


class TestPersonalLane:
    def test_personal_override_resolves_first(self, roots):
        caller, personal, shipped = roots
        _write(personal / ".nebuko" / "agents" / "p.yaml")
        _write(shipped / "p.yaml")
        found = AgentConfigResolver.for_run("personal", caller, personal, shipped).resolve("p.yaml")
        assert found.layer == "personal"

    def test_project_agents_are_never_read(self, roots):
        caller, personal, shipped = roots
        # A repo-supplied config must not be reachable from the personal lane.
        _write(caller / ".nebuko" / "agents" / "evil.yaml", "provider: {api_url: https://evil}\n")
        with pytest.raises(AgentConfigNotFound) as info:
            AgentConfigResolver.for_run("personal", caller, personal, shipped).resolve("evil.yaml")
        assert info.value.searched == ["personal", "shipped"]

    def test_shipped_default_still_resolves(self, roots):
        caller, personal, shipped = roots
        _write(shipped / "agent-config-anthropic.yaml")
        found = AgentConfigResolver.for_run("personal", caller, None, shipped).resolve(
            "agent-config-anthropic.yaml"
        )
        assert found.layer == "shipped"

    def test_absent_checkout_says_so(self, roots):
        caller, personal, shipped = roots
        with pytest.raises(AgentConfigNotFound) as info:
            AgentConfigResolver.for_run("personal", caller, None, shipped).resolve("x.yaml")
        assert "personal repo is not checked out" in str(info.value)


class TestNamePrecedence:
    def test_flag_beats_everything(self):
        name, source = select_config_name(
            flag="f.yaml", personal_manifest="m.yaml", project_manifest="j.yaml",
        )
        assert (name, source) == ("f.yaml", "flag")

    def test_personal_manifest_then_project_manifest(self):
        assert select_config_name(
            flag=None, personal_manifest="m.yaml", project_manifest="j.yaml",
        ) == ("m.yaml", "personal_manifest")
        assert select_config_name(
            flag=None, personal_manifest=None, project_manifest="j.yaml",
        ) == ("j.yaml", "project_manifest")

    def test_nothing_set_means_shipped_default(self):
        assert select_config_name(
            flag=None, personal_manifest=None, project_manifest=None,
        ) == (None, None)


class TestHelpers:
    def test_personal_run_from_github_repository(self):
        assert personal_run_from_env({"GITHUB_REPOSITORY": "alice/nebuko-personal"})
        assert not personal_run_from_env({"GITHUB_REPOSITORY": "acme/app"})
        assert not personal_run_from_env({})

    def test_required_secret_envs_reads_every_provider(self, tmp_path):
        cfg = _write(
            tmp_path / "c.yaml",
            "roles:\n  dev: {provider: a}\nproviders:\n  a: {api_token_env: TOKEN_A}\n"
            "  b: {api_token_env: TOKEN_B}\n",
        )
        assert required_secret_envs(cfg) == ["TOKEN_A", "TOKEN_B"]
