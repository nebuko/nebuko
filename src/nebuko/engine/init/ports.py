"""Value objects and ports for ``nebuko init`` (issue #991).

The four ports keep every external dependency behind a seam: ``TemplateSource``
(HTTPS / filesystem), ``ReleaseRefResolver`` (the pinned tag), ``SetupHost``
(the ``gh`` CLI) and ``Prompter`` (the terminal). ``ProviderProfile`` is the
per-LLM-provider adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence


class SetupError(RuntimeError):
    """A setup step failed; the message is meant to be shown to the client."""


class UsageError(SetupError):
    """The invocation itself is wrong (missing flag or environment input)."""


@dataclass(frozen=True)
class SetupTarget:
    """Where the configuration lands and where the secrets are stored."""

    host: str
    owner: str
    repo: str
    org: str | None = None  # org-level secret target; None = repo-level

    # @stub-protected
    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class PreflightReport:
    """The result of checking the client's ``gh`` authentication."""

    ok: bool
    missing_scopes: tuple[str, ...]
    remediation: str


@dataclass(frozen=True)
class InitAnswers:
    """Everything the client chose, plus the secret values to store."""

    target: SetupTarget
    provider: ProviderProfile
    runs_on: str
    pin_ref: str
    secrets: Mapping[str, str] = field(default_factory=dict, repr=False)
    force: bool = False
    # Names already present at the target scope: never asked for, never rewritten.
    skipped: frozenset[str] = frozenset()


@dataclass(frozen=True)
class GeneratedFiles:
    """The two files the pull request carries."""

    agent_config_path: str
    agent_config: str
    workflow_path: str
    workflow: str


class ProviderProfile(ABC):
    """An LLM provider: its config template and the secrets it needs."""

    name: str
    description: str
    template_path: str  # repo-relative, e.g. "defaults/agents/agent-config-openrouter.yaml"
    secret_names: tuple[str, ...]


class TemplateSource(ABC):
    """Reads a repository file at a given ref."""

    # @stub-protected
    @abstractmethod
    def fetch(self, path: str, ref: str) -> str: ...


class ReleaseRefResolver(ABC):
    """Resolves the release tag the generated workflow pins."""

    # @stub-protected
    @abstractmethod
    def latest(self) -> str: ...


class SetupHost(ABC):
    """The forge operations ``init`` needs, all through the client's ``gh``."""

    # @stub-protected
    @abstractmethod
    def gh_available(self) -> bool: ...

    # @stub-protected
    @abstractmethod
    def preflight(self, target: SetupTarget) -> PreflightReport: ...

    # @stub-protected
    @abstractmethod
    def app_installed(self, target: SetupTarget) -> bool: ...

    # @stub-protected
    @abstractmethod
    def existing_paths(self, target: SetupTarget, paths: Sequence[str]) -> set[str]: ...

    # @stub-protected
    @abstractmethod
    def existing_secrets(self, target: SetupTarget) -> frozenset[str]: ...

    # @stub-protected
    @abstractmethod
    def open_pr(
        self,
        target: SetupTarget,
        branch: str,
        title: str,
        body: str,
        files: GeneratedFiles,
    ) -> str: ...

    # @stub-protected
    @abstractmethod
    def set_secret(self, target: SetupTarget, name: str, value: str) -> None: ...


class Prompter(Protocol):
    """The one prompt ``run_init`` needs; ``InitPrompter`` implements it."""

    # @stub-protected
    def confirm(self, label: str, default: bool = False) -> bool: ...
