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
    org: str | None = None  # required when scope == "org"
    visibility: Literal["all", "private", "selected"] = "selected"
    selected_repository_ids: tuple[int, ...] = ()


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
    status: Literal["created", "updated", "skipped_existing", "failed"]
    detail: str


@dataclass(frozen=True)
class InitPlan:
    request: InitRequest
    rendered: dict[str, str]  # path -> content (agent config + .github/workflows/nebuko.yaml)
    secret_actions: dict[str, Literal["create", "update", "skip_existing"]]
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

    def put_secret(self, target: SecretTarget, name: str, value: SecretValue) -> None:
        # @stub-protected
        ...


BRANCH = "nebuko/init"
WORKFLOW_PATH = ".github/workflows/nebuko.yaml"


def plan_init(
    request: InitRequest,
    manifest: "Manifest",
    caller_template: str,
    agent_config: str,
    existing_files: dict[str, bool],
    existing_secrets: frozenset[str],
) -> InitPlan:
    # @stub-protected: module-level, pure
    if request.target.scope == "org" and not request.target.org:
        raise ValueError("org-scope targets require an org name")
    provider = manifest.providers[request.provider]
    names = (*provider.secret_names, "GH_PAT") if request.pat_mode else provider.secret_names
    secret_actions: dict[str, Literal["create", "update", "skip_existing"]] = {}
    for name in names:
        if name in existing_secrets:
            secret_actions[name] = "update" if request.force else "skip_existing"
        else:
            secret_actions[name] = "create"
    conflicts = tuple(
        sorted(path for path, exists in existing_files.items() if exists and not request.force)
    )
    rendered = {
        provider.template_path: agent_config,
        WORKFLOW_PATH: _render_workflow(caller_template, request, provider, names),
    }
    return InitPlan(
        request=request,
        rendered=rendered,
        secret_actions=secret_actions,
        conflicts=conflicts,
    )


def execute_init(plan: InitPlan, client: GitHubInitClient, secrets: dict[str, SecretValue]) -> InitOutcome:
    # @stub-protected: module-level; never prompts or prints
    target = plan.request.target
    # Structural steps raise: a failed branch upsert or PR is not recoverable
    # per-secret, unlike a failed secret write.
    client.upsert_branch_and_commit(target, BRANCH, plan.rendered)
    pr_url = client.get_or_open_pull_request(
        target, BRANCH, PR_TITLE, _pr_body(plan)
    )
    statuses: list[SecretStatus] = []
    for name, action in plan.secret_actions.items():
        if action == "skip_existing":
            statuses.append(SecretStatus(name=name, status="skipped_existing", detail="already present; use --force to update"))
            continue
        if name not in secrets or not secrets[name]:
            statuses.append(SecretStatus(name=name, status="failed", detail="no value provided"))
            continue
        try:
            client.put_secret(target, name, secrets[name])
        except Exception as err:  # noqa: BLE001 - per-secret failures never abort the run
            statuses.append(SecretStatus(name=name, status="failed", detail=str(err)))
            continue
        statuses.append(
            SecretStatus(name=name, status="created" if action == "create" else "updated", detail=f"{action}d on {target.scope}")
        )
    return InitOutcome(
        pr_url=pr_url,
        branch=BRANCH,
        files=tuple(plan.rendered),
        secrets=tuple(statuses),
        success=not any(status.status == "failed" for status in statuses),
    )


def _render_workflow(caller_template: str, request: InitRequest, provider, names: tuple[str, ...]) -> str:
    secrets_block = "\n".join(f"      {name}: ${{{{ secrets.{name} }}}}" for name in names)
    return (
        caller_template.replace("${pin_ref}", request.render.pin_ref)
        .replace("${agent_config_path}", provider.template_path)
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
