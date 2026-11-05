"""Plan/execute core for ``nebuko init`` (issue #1272).

``GitHubInitClient`` is I/O-only; ``plan_init`` and ``execute_init`` are
module-level functions. ``plan_init`` is pure and receives the fetched
``existing_files`` / ``existing_secrets``; ``execute_init`` takes the client
as an argument and never prompts or prints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from nebuko.engine.init.manifest import Manifest

Scope = Literal["repo", "org"]


@dataclass(frozen=True)
class SecretTarget:
    scope: Scope
    repo: str  # owner/name; always the repo receiving the PR
    org: str | None = None  # required when scope == "org"; must equal repo's owner
    visibility: Literal["all", "private", "selected"] = "selected"
    selected_repos: tuple[str, ...] = ()  # repo NAMES; org "create" default: (repo,)


@dataclass(frozen=True)
class SecretAccess:
    """Per-secret access facts for an org-scope target (rev 13, N30)."""

    visible: bool  # target repo can read the secret
    visibility: Literal["all", "private", "selected"] | None = None
    repo_listed: bool | None = None  # meaningful only when visibility == "selected"


@dataclass(frozen=True)
class RenderInputs:
    pin_ref: str
    runs_on: str = "ubuntu-latest"


@dataclass(frozen=True)
class InitRequest:
    target: SecretTarget
    provider: str
    render: RenderInputs
    pat_mode: bool
    force: bool


@dataclass(frozen=True)
class SealedSecret:
    encrypted_value: str
    key_id: str


SecretValue = str | SealedSecret


@dataclass(frozen=True)
class SecretStatus:
    name: str
    status: Literal[
        "created",
        "updated",
        "skipped_existing",
        "add_repo_access",
        "report_inaccessible",
        "failed",
    ]
    detail: str


@dataclass(frozen=True)
class InitPlan:
    request: InitRequest
    rendered: dict[str, str]  # path -> content (agent config + .github/workflows/nebuko.yaml)
    secret_actions: dict[
        str,
        Literal["create", "update", "skip_existing", "add_repo_access", "report_inaccessible"],
    ]
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class InitOutcome:
    pr_url: str | None
    branch: str
    files: tuple[str, ...]
    secrets: tuple[SecretStatus, ...]
    success: bool


class GitHubInitClient(Protocol):  # I/O only — no planning/execution methods here
    def get_existing_files(self, target: SecretTarget, paths: list[str]) -> dict[str, bool]:
        # @stub-protected
        ...

    def existing_secrets(self, target: SecretTarget) -> frozenset[str]:
        # @stub-protected: raises on failure
        ...

    def upsert_branch_and_commit(self, target: SecretTarget, branch: str, files: dict[str, str]) -> None:
        # @stub-protected: force-updates nebuko/init
        ...

    def get_or_open_pull_request(self, target: SecretTarget, branch: str, title: str, body: str) -> str:
        # @stub-protected: reuses the open PR
        ...

    def existing_secret_access(self, target: SecretTarget, names: list[str]) -> dict[str, SecretAccess]:
        # @stub-protected: org scope only; raises on failure
        ...

    def add_repo_access(self, target: SecretTarget, name: str) -> None:
        # @stub-protected: single-repo endpoint; never sends a value, never replaces the list
        ...

    def put_secret(
        self,
        target: SecretTarget,
        name: str,
        value: SecretValue,
        visibility: Literal["all", "private", "selected"] | None = None,
        repos: tuple[str, ...] | None = None,
    ) -> None:
        # @stub-protected: org scope — visibility/repos are the plan's explicit choice
        # (create ⇒ selected + (target repo,); update ⇒ the EXISTING visibility and list);
        # repo scope ignores both kwargs
        ...


AGENT_CONFIG_PATH = ".nebuko/agents/agent-config.yaml"  # single home (D7); setup.py imports it
BRANCH = "nebuko/init"
WORKFLOW_PATH = ".github/workflows/nebuko.yaml"


def plan_init(
    request: InitRequest,
    manifest: "Manifest",
    caller_template: str,
    agent_config: str,
    existing_files: dict[str, bool],
    existing_secrets: frozenset[str],
    secret_access: dict[str, SecretAccess] | None = None,
) -> InitPlan:
    # @stub-protected: module-level, pure — secret_access is None at repo scope
    # (N31). Org-scope action mapping per Decision §1 (N32); org != repo owner
    # ⇒ ValueError (N34); never emits "update" for a secret the repo cannot read
    # and cannot be made to (private + public repo stays "report_inaccessible").
    raise NotImplementedError


def execute_init(plan: InitPlan, client: GitHubInitClient, secrets: dict[str, SecretValue]) -> InitOutcome:
    # @stub-protected: module-level; never prompts or prints. add_repo_access ⇒
    # client.add_repo_access (no value sent); report_inaccessible ⇒ recorded with
    # success=False; update at org scope passes the existing visibility/repo list
    # to put_secret; individual secret failure ⇒ status="failed", continue;
    # branch/PR errors raise.
    raise NotImplementedError


def _render_workflow(caller_template: str, request: InitRequest, provider, names: tuple[str, ...]) -> str:
    secrets_block = "\n".join(f"      {name}: ${{{{ secrets.{name} }}}}" for name in names)
    return (
        caller_template.replace("${pin_ref}", request.render.pin_ref)
        .replace("${agent_config_path}", AGENT_CONFIG_PATH)
        .replace("${runs_on}", request.render.runs_on)
        .replace("${secrets_block}", secrets_block)
    )


PR_TITLE = "chore: add Nebuko agent configuration and workflow"


def _pr_body(plan: InitPlan) -> str:
    return (
        "Generated by `nebuko init`.\n\n"
        f"- {', '.join(sorted(plan.rendered))}\n\n"
        "No secret value is included in this pull request: the secrets are stored in the "
        "repository settings by `nebuko init`.\n"
    )
