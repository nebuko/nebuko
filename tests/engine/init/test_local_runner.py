"""``nebuko init anthropic-local-runner``: local runners that hold the token."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from nebuko.engine.init import cli
from nebuko.engine.init.local_runner import (
    RunnerHost,
    RunnerRequest,
    TOKEN_ENV,
    install_runners,
    remove_runners,
    render_env_file,
    runner_label,
)
from nebuko.engine.init.ports import PreflightReport, SetupError, SetupTarget, UsageError
from nebuko.engine.init.providers import LOCAL_RUNNER_PROFILE, PROVIDER_PROFILES
from nebuko.engine.init.refs import StaticRefResolver
from nebuko.engine.init.setup import SetupContext, secret_names
from nebuko.engine.init.sources import FileTemplateSource

from test_cli import FakeHost, FakePrompter, PIN, REPO_ROOT

TOKEN = "sk-ant-oat01-secret-token"


class FakeRunnerHost(RunnerHost):
    def __init__(self, *, private=True, missing=(), service_error=False) -> None:
        self.private = private
        self.missing = list(missing)
        self.service_error = service_error
        self.calls: list[tuple] = []

    def missing_tools(self, tools):
        return [tool for tool in tools if tool in self.missing]

    def gh_login(self):
        return "Olivier"

    def is_private(self, target):
        self.calls.append(("is_private", target.slug))
        return self.private

    def runner_token(self, target, kind):
        self.calls.append(("token", kind))
        return f"{kind}-token"

    def hostname(self):
        return "Dev Box"

    def unpack_runner(self, directory):
        self.calls.append(("unpack", directory.name))

    def configure(self, directory, url, token, name, label):
        self.calls.append(("configure", directory.name, url, token, name, label))
        (directory / ".runner").write_text("{}")

    def install_service(self, directory):
        if self.service_error:
            raise SetupError("sudo failed")
        self.calls.append(("service", directory.name))

    def remove_service(self, directory):
        self.calls.append(("remove_service", directory.name))

    def unconfigure(self, directory, token):
        self.calls.append(("unconfigure", directory.name, token))


def _request(tmp_path: Path, **overrides) -> RunnerRequest:
    values = dict(
        target=SetupTarget(host="github.com", owner="acme", repo="widgets"),
        login="Olivier",
        count=2,
        base_dir=tmp_path / "runners",
    )
    values.update(overrides)
    return RunnerRequest(**values)


def test_the_label_is_derived_from_the_github_login():
    assert runner_label("Olivier") == "nebuko-olivier"
    assert runner_label("o.n_x") == "nebuko-o.n_x"
    assert runner_label("we ird!") == "nebuko-we-ird"


def test_env_file_carries_only_the_token_variable():
    assert render_env_file(TOKEN) == f"{TOKEN_ENV}={TOKEN}\n"


class TestInstallRunners:
    def test_registers_each_runner_with_the_user_label(self, tmp_path):
        host = FakeRunnerHost()
        request = _request(tmp_path)

        created = install_runners(request, TOKEN, host, out=lambda _: None)

        assert [d.name for d in created] == ["1", "2"]
        configures = [c for c in host.calls if c[0] == "configure"]
        assert [c[4] for c in configures] == [
            "nebuko-olivier-dev-box-1",
            "nebuko-olivier-dev-box-2",
        ]
        assert {c[5] for c in configures} == {"nebuko-olivier"}
        assert {c[2] for c in configures} == {"https://github.com/acme/widgets"}
        assert {c[3] for c in configures} == {"registration-token"}
        assert [c for c in host.calls if c[0] == "service"] == [
            ("service", "1"),
            ("service", "2"),
        ]

    def test_the_token_lands_in_a_private_env_file_per_runner(self, tmp_path):
        host = FakeRunnerHost()

        created = install_runners(_request(tmp_path), TOKEN, host, out=lambda _: None)

        for directory in created:
            env = directory / ".env"
            assert env.read_text() == f"{TOKEN_ENV}={TOKEN}\n"
            assert stat.S_IMODE(env.stat().st_mode) == 0o600

    def test_the_token_never_reaches_the_output_or_the_host_calls(self, tmp_path):
        host = FakeRunnerHost()
        lines: list[str] = []

        install_runners(_request(tmp_path), TOKEN, host, out=lines.append)

        assert TOKEN not in "\n".join(lines)
        assert TOKEN not in repr(host.calls)

    def test_a_rerun_refreshes_the_token_without_registering_again(self, tmp_path):
        request = _request(tmp_path, count=1)
        first = FakeRunnerHost()
        install_runners(request, "old-token", first, out=lambda _: None)
        second = FakeRunnerHost()

        install_runners(request, "new-token", second, out=lambda _: None)

        assert not [c for c in second.calls if c[0] in ("configure", "unpack")]
        env = (request.runner_dir(1) / ".env").read_text()
        assert env == f"{TOKEN_ENV}=new-token\n"

    def test_a_public_repository_is_refused_unless_allowed(self, tmp_path):
        host = FakeRunnerHost(private=False)

        with pytest.raises(UsageError, match="public repository"):
            install_runners(_request(tmp_path), TOKEN, host, out=lambda _: None)
        assert not [c for c in host.calls if c[0] == "configure"]

        install_runners(
            _request(tmp_path), TOKEN, host, allow_public=True, out=lambda _: None
        )
        assert [c for c in host.calls if c[0] == "configure"]

    def test_an_organization_scope_skips_the_visibility_check(self, tmp_path):
        host = FakeRunnerHost(private=False)
        target = SetupTarget(host="github.com", owner="acme", repo="widgets", org="acme")

        install_runners(
            _request(tmp_path, target=target, count=1), TOKEN, host, out=lambda _: None
        )

        assert ("is_private", "acme/widgets") not in host.calls
        configure = next(c for c in host.calls if c[0] == "configure")
        assert configure[2] == "https://github.com/acme"

    @pytest.mark.parametrize("token", ["", "   ", "two words"])
    def test_a_blank_or_spaced_token_is_rejected(self, tmp_path, token):
        with pytest.raises(UsageError, match=TOKEN_ENV):
            install_runners(_request(tmp_path), token, FakeRunnerHost(), out=lambda _: None)

    def test_missing_tools_are_named(self, tmp_path):
        host = FakeRunnerHost(missing=["node", "npx"])

        with pytest.raises(UsageError, match="node, npx"):
            install_runners(_request(tmp_path), TOKEN, host, out=lambda _: None)

    def test_a_failed_service_install_falls_back_to_run_sh(self, tmp_path):
        lines: list[str] = []
        host = FakeRunnerHost(service_error=True)

        created = install_runners(
            _request(tmp_path, count=1), TOKEN, host, out=lines.append
        )

        assert created
        assert any("run.sh" in line and "service install failed" in line for line in lines)

    def test_no_service_prints_the_start_command(self, tmp_path):
        lines: list[str] = []
        host = FakeRunnerHost()

        install_runners(
            _request(tmp_path, count=1, install_service=False),
            TOKEN,
            host,
            out=lines.append,
        )

        assert not [c for c in host.calls if c[0] == "service"]
        assert any("run.sh" in line for line in lines)

    def test_a_non_positive_count_is_a_usage_error(self, tmp_path):
        with pytest.raises(UsageError, match="--count"):
            install_runners(
                _request(tmp_path, count=0), TOKEN, FakeRunnerHost(), out=lambda _: None
            )


class TestRemoveRunners:
    def test_unregisters_and_deletes_each_installed_runner(self, tmp_path):
        request = _request(tmp_path)
        install_runners(request, TOKEN, FakeRunnerHost(), out=lambda _: None)
        host = FakeRunnerHost()

        removed = remove_runners(request, host, out=lambda _: None)

        assert removed == 2
        assert [c for c in host.calls if c[0] == "unconfigure"] == [
            ("unconfigure", "1", "remove-token"),
            ("unconfigure", "2", "remove-token"),
        ]
        assert not request.runner_dir(1).exists()

    def test_nothing_installed_is_not_an_error(self, tmp_path):
        assert remove_runners(_request(tmp_path), FakeRunnerHost(), out=lambda _: None) == 0


# ── the CLI flow ───────────────────────────────────────────────────────────


@pytest.fixture
def wired(monkeypatch, tmp_path):
    setup_host = FakeHost()
    context = SetupContext(
        source=FileTemplateSource(REPO_ROOT), refs=StaticRefResolver(PIN), host=setup_host
    )
    monkeypatch.setattr(cli, "build_setup", lambda **kwargs: context)
    runner_host = FakeRunnerHost()
    monkeypatch.setattr(cli, "build_runner_host", lambda: runner_host)
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    return setup_host, runner_host, tmp_path / "runners"


def test_the_local_runner_profile_stores_no_provider_secret():
    assert LOCAL_RUNNER_PROFILE.secret_names == ()
    assert secret_names(LOCAL_RUNNER_PROFILE) == ("GH_PAT",)
    assert LOCAL_RUNNER_PROFILE not in PROVIDER_PROFILES


def test_installs_runners_then_opens_the_config_pr(wired, monkeypatch, capsys):
    setup_host, runner_host, base = wired
    monkeypatch.setenv("NEBUKO_SECRET_CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    monkeypatch.setenv("NEBUKO_SECRET_GH_PAT", "pat-456")

    code = cli.main(
        [
            "anthropic-local-runner", "--repo", "acme/widgets",
            "--non-interactive", "--runner-dir", str(base), "--no-service",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert [c for c in runner_host.calls if c[0] == "configure"]
    files = setup_host.prs[0]["files"]
    assert 'runs_on: "nebuko-olivier"' in files.workflow
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in files.workflow
    assert "backend: \"claude-code\"" in files.agent_config
    assert setup_host.secrets == {"GH_PAT": "pat-456"}
    assert TOKEN not in captured.out + captured.err
    assert (base / "acme__widgets" / "1" / ".env").read_text().endswith(f"{TOKEN}\n")


def test_no_config_installs_the_runners_only(wired, monkeypatch, capsys):
    setup_host, runner_host, base = wired
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)

    code = cli.main(
        [
            "anthropic-local-runner", "--repo", "acme/widgets", "--non-interactive",
            "--runner-dir", str(base), "--no-service", "--no-config",
        ]
    )

    assert code == 0
    assert setup_host.prs == []
    assert "runs_on: nebuko-olivier" in capsys.readouterr().out


def test_the_token_is_asked_with_hidden_input_when_interactive(wired, monkeypatch):
    setup_host, runner_host, base = wired
    prompter = FakePrompter(secrets=[TOKEN])
    monkeypatch.setattr(cli, "InitPrompter", lambda: prompter)

    code = cli.main(
        [
            "anthropic-local-runner", "--repo", "acme/widgets",
            "--runner-dir", str(base), "--no-service", "--no-config",
        ]
    )

    assert code == 0
    assert prompter.secret_values == []


def test_a_missing_token_in_non_interactive_mode_exits_two(wired, capsys):
    _, _, base = wired

    code = cli.main(
        [
            "anthropic-local-runner", "--repo", "acme/widgets",
            "--non-interactive", "--runner-dir", str(base),
        ]
    )

    assert code == 2
    assert "NEBUKO_SECRET_CLAUDE_CODE_OAUTH_TOKEN" in capsys.readouterr().err


def test_remove_unregisters_the_runners(wired, monkeypatch):
    _, runner_host, base = wired
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", TOKEN)
    args = [
        "anthropic-local-runner", "--repo", "acme/widgets",
        "--non-interactive", "--runner-dir", str(base), "--no-service", "--no-config",
    ]
    assert cli.main(args) == 0

    assert cli.main([*args, "--remove"]) == 0

    assert [c for c in runner_host.calls if c[0] == "unconfigure"]


def test_a_system_without_bin_bash_gets_a_clear_error_before_any_download(monkeypatch, tmp_path):
    from nebuko.engine.init import local_runner

    host = local_runner.GhRunnerHost(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(local_runner.Path, "exists", lambda self: str(self) != "/bin/bash")
    monkeypatch.setattr(
        host, "_download_runner", lambda: pytest.fail("must not download first")
    )

    with pytest.raises(SetupError, match="NixOS"):
        host.unpack_runner(tmp_path / "1")
