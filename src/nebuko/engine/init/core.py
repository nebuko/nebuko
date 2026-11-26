"""Plan/execute core for ``nebuko init`` (issue #1272).

``GitHubInitClient`` is I/O-only; ``plan_init`` and ``execute_init`` are
module-level functions. ``plan_init`` is pure and receives the fetched
``existing_files`` / ``existing_secrets``; ``execute_init`` takes the client
as an argument and never prompts or prints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from nebuko.engine.init.ports import SetupError
from nebuko.engine.models import NebukoManifest

if TYPE_CHECKING:
    from nebuko.engine.init.manifest import Manifest

Scope = Literal["repo", "org"]


@dataclass(frozen=True)
class SecretTarget:
    scope: Scope
    repo: str  # owner/name; always the repo receiving the PR
    org: str | None = None  # required when scope == "org"; must equal repo's owner
    visibility: Literal["all", "private", "selected"] = "selected"
    # repo NAMES; None means "derive the target repo". An explicit empty tuple is
    # rejected below, mirroring gh's refusal of `--visibility selected` with no repos.
    selected_repos: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.visibility == "selected" and self.selected_repos is not None and not self.selected_repos:
            raise ValueError("visibility='selected' requires at least one selected repository")


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
    request_access: dict[str, SecretAccess] = field(default_factory=dict)  # org-scope access facts


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

    def read_file(self, target: SecretTarget, path: str) -> str | None:
        # @stub-protected: the default branch's text; None only when the file is absent
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
MANIFEST_PATH = ".nebuko/nebuko.yaml"


class ManifestMergeError(ValueError):
    """``MANIFEST_PATH`` cannot be edited in place; ``snippet`` is the manual edit."""

    def __init__(self, reason: str, snippet: str) -> None:
        super().__init__(
            f"{reason}\nAdd these keys to {MANIFEST_PATH} by hand:\n{snippet}"
        )
        self.snippet = snippet


def plan_init(
    request: InitRequest,
    manifest: "Manifest",
    caller_template: str,
    agent_config: str,
    existing_files: dict[str, bool],
    existing_secrets: frozenset[str],
    secret_access: dict[str, SecretAccess] | None = None,
    existing_manifest: str | None = None,
    base_branch: str = "main",
    test_command: str = "",
) -> InitPlan:
    # @stub-protected: module-level, pure — secret_access is None at repo scope
    # (N31). Org-scope action mapping per Decision §1 (N32); org != repo owner
    # ⇒ ValueError (N34); never emits "update" for a secret the repo cannot read
    # and cannot be made to (private + public repo stays "report_inaccessible").
    if request.target.scope == "org":
        if not request.target.org or request.target.org != request.target.repo.split("/")[0]:
            raise ValueError(
                f"org {request.target.org!r} is not the owner of repo "
                f"{request.target.repo!r}"
            )
    rendered = {
        AGENT_CONFIG_PATH: agent_config,
        WORKFLOW_PATH: _render_workflow(
            caller_template, request, manifest, manifest.providers[request.provider].secret_names
        ),
        MANIFEST_PATH: merge_manifest_settings(existing_manifest, base_branch, test_command),
    }
    secret_actions: dict[str, str] = {}
    secret_names = tuple(manifest.providers[request.provider].secret_names)
    if request.pat_mode:
        secret_names = (*secret_names, "GH_PAT")
    for name in secret_names:
        if name == "GH_PAT" and not request.pat_mode:
            continue
        secret_actions[name] = _secret_action(
            name,
            existing_secrets=existing_secrets,
            secret_access=secret_access,
            force=request.force,
        )
    conflicts = tuple(
        path for path, exists in sorted(existing_files.items()) if exists
    )
    return InitPlan(
        request=request,
        rendered=rendered,
        secret_actions=secret_actions,
        conflicts=conflicts,
        request_access=dict(secret_access) if secret_access else {},
    )


def merge_manifest_settings(existing: str | None, base_branch: str, test_command: str) -> str:
    """``existing`` with ``base_branch`` and ``test_command`` set, nothing else changed.

    Only the line of each managed key is rewritten, or a new line is appended
    at the end, so comments, other keys and their order survive. A blank
    ``test_command`` leaves that key alone. The result is parsed again and must
    equal the old mapping with just these keys changed; otherwise the edit is
    refused with the lines to add by hand. Invalid values raise ``ValueError``.
    """
    import yaml

    settings = {"base_branch": base_branch.strip()}
    if test_command.strip():
        settings["test_command"] = test_command.strip()
    NebukoManifest.model_validate(settings)  # the engine's own rules for these keys
    snippet = "\n".join(_render_setting(key, value).rstrip("\n") for key, value in settings.items())
    text = existing or ""
    try:
        before = yaml.safe_load(text) or {}
    except yaml.YAMLError as error:
        raise ManifestMergeError(f"{MANIFEST_PATH} is not valid YAML ({error})", snippet) from error
    if not isinstance(before, dict):
        raise ManifestMergeError(f"{MANIFEST_PATH} is not a YAML mapping", snippet)
    result = text
    for key, value in settings.items():
        if before.get(key) == value:
            continue
        result = _set_top_level_line(result, key, value)
        if result is None:
            raise ManifestMergeError(f"{key} is set in a form this edit cannot rewrite", snippet)
    try:
        after = yaml.safe_load(result) or {}
    except yaml.YAMLError as error:
        raise ManifestMergeError(
            f"the edited {MANIFEST_PATH} is not valid YAML ({error})", snippet
        ) from error
    if after != {**before, **settings}:
        raise ManifestMergeError(f"{MANIFEST_PATH} could not be edited in place", snippet)
    return result


def _render_setting(key: str, value: str) -> str:
    import yaml

    return yaml.safe_dump(
        {key: value}, default_flow_style=False, sort_keys=False, width=1 << 30, allow_unicode=True
    )


def _set_top_level_line(text: str, key: str, value: str) -> str | None:
    """Replace the single top-level ``key:`` line, or append one; None when unsafe."""
    lines = text.splitlines(keepends=True)
    pattern = re.compile(rf"{re.escape(key)}[ \t]*:")
    matches = [index for index, line in enumerate(lines) if pattern.match(line)]
    if len(matches) > 1:
        return None
    if not matches:
        if text and not text.endswith("\n"):
            text += "\n"
        return text + _render_setting(key, value)
    old = lines[matches[0]]
    # A trailing comment would be lost with the line, so a manual edit is needed.
    if " #" in old:
        return None
    lines[matches[0]] = _render_setting(key, value)
    return "".join(lines)


def _secret_action(
    name: str,
    *,
    existing_secrets: frozenset[str],
    secret_access: dict[str, SecretAccess] | None,
    force: bool,
) -> str:
    # Access facts win over existence: an existing org secret the repo cannot
    # read must be made readable (or reported), never silently skipped.
    access = (secret_access or {}).get(name)
    if access is not None and not access.visible:
        if access.visibility == "private" and not access.repo_listed:
            return "report_inaccessible"
        return "update" if force else "add_repo_access"
    if name in existing_secrets:
        return "update" if force else "skip_existing"
    return "create"


def execute_init(plan: InitPlan, client: GitHubInitClient, secrets: dict[str, SecretValue]) -> InitOutcome:
    # @stub-protected: module-level; never prompts or prints. add_repo_access ⇒
    # client.add_repo_access (no value sent); report_inaccessible ⇒ recorded with
    # success=False; update at org scope passes the existing visibility/repo list
    # to put_secret; individual secret failure ⇒ status="failed", continue;
    # branch/PR errors raise.
    statuses: list[SecretStatus] = []
    for name, action in plan.secret_actions.items():
        if action == "skip_existing":
            statuses.append(SecretStatus(name=name, status="skipped_existing", detail=""))
            continue
        if action == "add_repo_access":
            client.add_repo_access(plan.request.target, name)
            statuses.append(SecretStatus(name=name, status="add_repo_access", detail=""))
            continue
        if action == "report_inaccessible":
            statuses.append(
                SecretStatus(
                    name=name,
                    status="report_inaccessible",
                    detail="the target repository cannot read this org secret (private and not listed)",
                )
            )
            continue
        if name not in secrets:
            statuses.append(
                SecretStatus(name=name, status="failed", detail=f"no value provided for {name}")
            )
            continue
        value = secrets[name]
        try:
            if plan.request.target.scope == "org" and action == "update":
                # Keep the secret's existing visibility and repo list: gh's
                # defaults would replace the access list.
                access = (plan.request_access or {}).get(name)
                visibility = access.visibility if access and access.visibility else None
                client.put_secret(
                    plan.request.target,
                    name,
                    value,  # type: ignore[arg-type]
                    visibility=visibility,
                    repos=plan.request.target.selected_repos,
                )
            else:
                client.put_secret(plan.request.target, name, value)  # type: ignore[arg-type]
        except SetupError as error:
            # SetupError messages are client-facing; keep them verbatim.
            statuses.append(SecretStatus(name=name, status="failed", detail=str(error)))
            continue
        except Exception as error:
            statuses.append(SecretStatus(name=name, status="failed", detail=f"{type(error).__name__}: {error}"))
            continue
        statuses.append(SecretStatus(name=name, status="created" if action == "create" else "updated", detail=""))
    client.upsert_branch_and_commit(plan.request.target, BRANCH, plan.rendered)
    pr_url: str | None = None
    pr_error: str | None = None
    try:
        pr_url = client.get_or_open_pull_request(
            plan.request.target,
            BRANCH,
            PR_TITLE,
            _pr_body(plan),
        )
    except Exception as error:
        pr_error = f"{type(error).__name__}: {error}"
    success = pr_error is None and not any(
        s.status in ("failed", "report_inaccessible") for s in statuses
    )
    return InitOutcome(
        pr_url=pr_url,
        branch=BRANCH,
        files=tuple(sorted(plan.rendered)),
        secrets=tuple(statuses),
        success=success,
    )


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
