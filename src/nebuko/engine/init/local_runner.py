"""``nebuko init anthropic-local-runner`` — GitHub runners on the user's machine.

The Claude Code backend bills the user's own Claude subscription, so the job
must run where that token lives. This module installs one or more GitHub
Actions self-hosted runners on the local machine, labelled ``nebuko-<login>``,
and keeps the ``CLAUDE_CODE_OAUTH_TOKEN`` (from ``claude setup-token``) in each
runner's own ``.env`` file: the token never becomes a GitHub secret.

Every external effect (``gh``, the download, ``config.sh``, ``svc.sh``) sits
behind :class:`RunnerHost`, so the flow is testable without a network.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tarfile
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from nebuko.engine.init.ports import SetupError, SetupTarget, UsageError

__all__ = [
    "GhRunnerHost",
    "RunnerHost",
    "RunnerRequest",
    "TOKEN_ENV",
    "install_runners",
    "remove_runners",
    "render_env_file",
    "runner_label",
]

TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
LABEL_PREFIX = "nebuko-"
DEFAULT_BASE_DIR = Path.home() / ".nebuko" / "runner"
REQUIRED_TOOLS = ("git", "node", "npx")

TOKEN_INSTRUCTIONS = """\
Nebuko runs Claude Code with your own Claude subscription. In another terminal run

    claude setup-token

and paste the long-lived token it prints. It is stored only in the runner's
private .env file on this machine; it is never sent to GitHub."""

NIXOS_RUNNER_HINT = (
    "this system has no /bin/bash (NixOS?): the official Actions runner needs a "
    "standard Linux layout (its scripts use #!/bin/bash and its .NET binaries need "
    "system libraries). Run the runner from nixpkgs (`github-runner`, or "
    "services.github-runners), or enable programs.nix-ld, then register it with the "
    "label printed by `nebuko init anthropic-local-runner --no-config` and put "
    "CLAUDE_CODE_OAUTH_TOKEN in its environment."
)

PUBLIC_REPO_REFUSAL = (
    "{slug} is a public repository. A self-hosted runner executes workflow code "
    "on this machine, and anyone can open a pull request against a public "
    "repository. Use a private repository, or pass --allow-public if you accept "
    "that risk."
)


@dataclass(frozen=True)
class RunnerRequest:
    """What to install: where it registers, how many, and how it is run."""

    target: SetupTarget
    login: str
    count: int = 1
    base_dir: Path = DEFAULT_BASE_DIR
    install_service: bool = True

    @property
    def scope_slug(self) -> str:
        """The GitHub account or repository the runners belong to."""
        return self.target.org or self.target.slug

    @property
    def label(self) -> str:
        return runner_label(self.login)

    @property
    def url(self) -> str:
        return f"https://{self.target.host}/{self.scope_slug}"

    @property
    def root(self) -> Path:
        """Parent directory of this scope's runners, one sub-directory each."""
        return self.base_dir / self.scope_slug.replace("/", "__")

    def runner_dir(self, index: int) -> Path:
        return self.root / str(index)

    def runner_name(self, hostname: str, index: int) -> str:
        return f"{self.label}-{_slug(hostname)}-{index}"


class RunnerHost(ABC):
    """The machine- and GitHub-facing operations a runner install needs."""

    @abstractmethod
    def missing_tools(self, tools: Sequence[str]) -> list[str]: ...

    @abstractmethod
    def gh_login(self) -> str: ...

    @abstractmethod
    def is_private(self, target: SetupTarget) -> bool: ...

    @abstractmethod
    def runner_token(self, target: SetupTarget, kind: str) -> str:
        """A short-lived ``registration`` or ``remove`` token for the scope."""

    @abstractmethod
    def hostname(self) -> str: ...

    @abstractmethod
    def unpack_runner(self, directory: Path) -> None:
        """Download (cached) and extract the Actions runner into ``directory``."""

    @abstractmethod
    def configure(
        self, directory: Path, url: str, token: str, name: str, label: str
    ) -> None: ...

    @abstractmethod
    def install_service(self, directory: Path) -> None: ...

    @abstractmethod
    def remove_service(self, directory: Path) -> None: ...

    @abstractmethod
    def unconfigure(self, directory: Path, token: str) -> None: ...


def runner_label(login: str) -> str:
    """The label a user's runners carry, and the workflow's ``runs_on`` value."""
    return f"{LABEL_PREFIX}{_slug(login)}"


def render_env_file(token: str) -> str:
    """The runner ``.env`` content: the token its jobs inherit."""
    return f"{TOKEN_ENV}={token}\n"


def install_runners(
    request: RunnerRequest,
    token: str,
    host: RunnerHost,
    *,
    allow_public: bool = False,
    out: Callable[[str], None] = print,
) -> list[Path]:
    """Install ``request.count`` runners and return their directories.

    Raises:
        UsageError: A prerequisite is missing, the token is blank, or the target
            is a public repository (without ``allow_public``).
        SetupError: A runner could not be configured.
    """
    if request.count < 1:
        raise UsageError("--count must be at least 1")
    token = token.strip()
    if not token or any(ch.isspace() for ch in token):
        raise UsageError(f"{TOKEN_ENV} must be a single non-empty token")
    missing = host.missing_tools(REQUIRED_TOOLS)
    if missing:
        raise UsageError(
            f"missing required tool(s) on PATH: {', '.join(missing)} "
            "(Node.js provides node and npx)"
        )
    if not request.target.org and not allow_public:
        if not host.is_private(request.target):
            raise UsageError(PUBLIC_REPO_REFUSAL.format(slug=request.target.slug))

    hostname = host.hostname()
    created: list[Path] = []
    for index in range(1, request.count + 1):
        directory = request.runner_dir(index)
        if (directory / ".runner").exists():
            out(f"Runner {index} already configured in {directory}; refreshing its token.")
        else:
            directory.mkdir(parents=True, exist_ok=True)
            host.unpack_runner(directory)
            host.configure(
                directory,
                request.url,
                host.runner_token(request.target, "registration"),
                request.runner_name(hostname, index),
                request.label,
            )
            out(f"Runner {index} registered with label {request.label}.")
        _write_env(directory, token)
        if request.install_service:
            try:
                host.install_service(directory)
            except SetupError as exc:
                out(f"Runner {index}: service install failed ({exc}); start it with: {directory}/run.sh")
        else:
            out(f"Runner {index}: start it with {directory}/run.sh")
        created.append(directory)
    return created


def remove_runners(
    request: RunnerRequest, host: RunnerHost, *, out: Callable[[str], None] = print
) -> int:
    """Unregister and delete every runner installed for ``request``'s scope."""
    directories = sorted(
        (d for d in request.root.glob("*") if (d / ".runner").exists()),
        key=lambda d: d.name,
    )
    for directory in directories:
        host.remove_service(directory)
        host.unconfigure(directory, host.runner_token(request.target, "remove"))
        shutil.rmtree(directory, ignore_errors=True)
        out(f"Removed runner in {directory}.")
    if not directories:
        out(f"No runner installed for {request.scope_slug} under {request.root}.")
    return len(directories)


def _write_env(directory: Path, token: str) -> None:
    """Write the runner ``.env`` with owner-only permissions from the first byte."""
    path = directory / ".env"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(render_env_file(token))
    os.chmod(path, 0o600)


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower() or "x"


# ── real host ──────────────────────────────────────────────────────────────

_RUNNER_OS = {"Linux": "linux", "Darwin": "osx"}
_RUNNER_ARCH = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}


class GhRunnerHost(RunnerHost):
    """Runs the real thing: ``gh`` for the API, the Actions runner scripts."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache = cache_dir or DEFAULT_BASE_DIR / "cache"

    def missing_tools(self, tools: Sequence[str]) -> list[str]:
        return [tool for tool in tools if shutil.which(tool) is None]

    def gh_login(self) -> str:
        return self._gh("api", "user", "--jq", ".login")

    def is_private(self, target: SetupTarget) -> bool:
        return self._gh("api", f"repos/{target.slug}", "--jq", ".private") == "true"

    def runner_token(self, target: SetupTarget, kind: str) -> str:
        scope = f"orgs/{target.org}" if target.org else f"repos/{target.slug}"
        return self._gh(
            "api", "-X", "POST", f"{scope}/actions/runners/{kind}-token", "--jq", ".token"
        )

    def hostname(self) -> str:
        return platform.node() or "host"

    def unpack_runner(self, directory: Path) -> None:
        if not Path("/bin/bash").exists():
            raise SetupError(NIXOS_RUNNER_HINT)
        archive = self._download_runner()
        with tarfile.open(archive) as tar:
            tar.extractall(directory, filter="data")

    def configure(
        self, directory: Path, url: str, token: str, name: str, label: str
    ) -> None:
        self._run(
            [
                "./config.sh", "--unattended", "--replace", "--url", url,
                "--token", token, "--name", name, "--labels", label,
                "--work", "_work",
            ],
            directory,
        )

    def install_service(self, directory: Path) -> None:
        sudo = ["sudo"] if platform.system() == "Linux" else []
        user = [os.environ.get("USER", "")] if sudo else []
        self._run([*sudo, "./svc.sh", "install", *user], directory)
        self._run([*sudo, "./svc.sh", "start"], directory)

    def remove_service(self, directory: Path) -> None:
        sudo = ["sudo"] if platform.system() == "Linux" else []
        for action in ("stop", "uninstall"):
            try:
                self._run([*sudo, "./svc.sh", action], directory)
            except SetupError:
                pass  # no service was installed (--no-service)

    def unconfigure(self, directory: Path, token: str) -> None:
        self._run(["./config.sh", "remove", "--token", token], directory)

    def _download_runner(self) -> Path:
        tag = self._gh("api", "repos/actions/runner/releases/latest", "--jq", ".tag_name")
        system, machine = platform.system(), platform.machine().lower()
        if system not in _RUNNER_OS or machine not in _RUNNER_ARCH:
            raise SetupError(f"unsupported platform {system}/{machine} for the Actions runner")
        archive = (
            f"actions-runner-{_RUNNER_OS[system]}-{_RUNNER_ARCH[machine]}-{tag.lstrip('v')}.tar.gz"
        )
        path = self._cache / archive
        if not path.exists():
            self._cache.mkdir(parents=True, exist_ok=True)
            url = f"https://github.com/actions/runner/releases/download/{tag}/{archive}"
            partial = path.with_suffix(".part")
            with urllib.request.urlopen(url, timeout=120) as response, open(partial, "wb") as handle:
                shutil.copyfileobj(response, handle)
            partial.rename(path)
        return path

    @staticmethod
    def _gh(*args: str) -> str:
        try:
            done = subprocess.run(
                ["gh", *args], capture_output=True, text=True, check=True, timeout=60
            )
        except subprocess.CalledProcessError as exc:
            raise SetupError(f"gh {args[0]} failed: {exc.stderr.strip() or exc}") from exc
        return done.stdout.strip()

    @staticmethod
    def _run(command: list[str], directory: Path) -> None:
        try:
            subprocess.run(command, cwd=directory, check=True, timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise SetupError(f"{' '.join(command[:2])} failed: {exc}") from exc
