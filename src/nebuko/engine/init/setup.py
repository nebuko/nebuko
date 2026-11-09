"""The ``nebuko init`` orchestration (issue #991, reworked for #1272).

``run_init`` is the single orchestration path: prompts informed by
``existing_secrets``, then the pure ``plan_init`` and the I/O-only
``execute_init``. Every rendered file goes to the ``nebuko/init`` branch
through the ``GitHubInitClient``; nothing is written to the local checkout.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from nebuko.engine.init.app_installation import AppInstallationResolver
from nebuko.engine.init.core import (
    AGENT_CONFIG_PATH,
    BRANCH,
    GitHubInitClient,
    InitOutcome,
    InitPlan,
    InitRequest,
    RenderInputs,
    SecretTarget,
    SecretValue,
    WORKFLOW_PATH,
    execute_init,
    plan_init,
)
from nebuko.engine.init.github_host import GitHubHost
from nebuko.engine.init.github_init_client import GhInitClient
from nebuko.engine.init.manifest import load_packaged_manifest
from nebuko.engine.init.ports import (
    InitAnswers,
    PreflightReport,
    Prompter,
    ReleaseRefResolver,
    SetupHost,
    SetupTarget,
    TemplateSource,
    UsageError,
)
from nebuko.engine.init.refs import (  # noqa: F401  (DEFAULT_WORKFLOW_REF is re-exported)
    DEFAULT_WORKFLOW_REF,
    GitTagRefResolver,
    StaticRefResolver,
)
from nebuko.engine.init.sources import FileTemplateSource, HttpTemplateSource

EXIT_OK = 0
EXIT_ERRORS = 1
EXIT_USAGE = 2

PAT_INSTRUCTIONS = """\
A GitHub Personal Access Token is required (fine-grained, with Contents,
Pull requests and Issues read/write on the target repository). Create one at
https://github.com/settings/personal-access-tokens/new
"""
SECRET_ENV_PREFIX = "NEBUKO_SECRET_"

NEXT_STEPS = """\
Next steps
1. Review and merge the pull request: {url}
2. Once merged, try it: comment "@@dev Hello Nebuko!" on an issue in {slug}.

Let's build something with Nebuko's agent teams 🤝"""


@dataclass(frozen=True)
class SetupContext:
    """The wired collaborators for one ``nebuko init`` invocation."""

    source: TemplateSource | None
    refs: ReleaseRefResolver | None
    host: SetupHost | None
    client: GitHubInitClient | None = None  # the write path; defaults to GhInitClient
    resolver: AppInstallationResolver | None = None


@dataclass(frozen=True)
class SetupOutcome:
    """What a finished ``run_init`` produced: the exit code and the PR URL."""

    code: int
    pr_url: str = ""


# @stub-protected
def build_setup(
    *,
    pin_ref: str | None,
    template_dir: Path | None,
    repo: str = "nebuko/nebuko",
) -> SetupContext:
    """Wire the adapters: local checkout when given, HTTPS otherwise."""
    source: TemplateSource = FileTemplateSource(template_dir) if template_dir else HttpTemplateSource()
    refs: ReleaseRefResolver = StaticRefResolver(pin_ref) if pin_ref else GitTagRefResolver(repo=repo)
    return SetupContext(source=source, refs=refs, host=GitHubHost(), client=GhInitClient())


# @stub-protected: signature defined by architect — do not modify
def run_init(
    answers: InitAnswers,
    source: TemplateSource,
    host: GitHubInitClient,
    prompter: Prompter,
    *,
    pat_mode: bool = False,
    non_interactive: bool = False,
) -> SetupOutcome:
    """Run the setup flow: plan (pure), then execute through the client."""
    target = _secret_target(answers.target)
    # Raises on failure (decision 9): a broken listing must never look like an
    # empty one and silently plan "create" for existing secrets.
    existing_secrets = host.existing_secrets(target)
    manifest = load_packaged_manifest()
    provider = manifest.providers[answers.provider.name]
    agent_config = source.fetch(provider.template_path, answers.pin_ref)
    request = InitRequest(
        target=target,
        provider=answers.provider.name,
        render=RenderInputs(pin_ref=answers.pin_ref, runs_on=answers.runs_on),
        pat_mode=pat_mode,
        force=answers.force,
    )
    paths = [AGENT_CONFIG_PATH, WORKFLOW_PATH]
    plan: InitPlan = plan_init(
        request=request,
        manifest=manifest,
        caller_template=_caller_template(),
        agent_config=agent_config,
        existing_files=host.get_existing_files(target, paths),
        existing_secrets=existing_secrets,
    )
    if plan.conflicts and not request.force:
        if not prompter.confirm(
            f"{', '.join(plan.conflicts)} already exist(s) in {answers.target.slug}; overwrite?",
            default=False,
        ):
            listed = ", ".join(plan.conflicts)
            print(f"Aborted: {listed} left untouched (pass --force to overwrite).", file=sys.stderr)
            return SetupOutcome(EXIT_ERRORS)
    # One value loop (D5): create/update need a value; skip_existing does not.
    secrets: dict[str, SecretValue] = {}
    for name, action in plan.secret_actions.items():
        if action == "skip_existing":
            continue
        if non_interactive:
            value = os.environ.get(f"{SECRET_ENV_PREFIX}{name}", "")
            if not value:
                raise UsageError(
                    f"missing {SECRET_ENV_PREFIX}{name} (required in non-interactive mode)"
                )
        else:
            if name == "GH_PAT":
                print(PAT_INSTRUCTIONS)
            value = prompter.ask_secret(name)
            if not value:
                # An empty answer is final: never ask again for another secret.
                print(f"No value for {name}; skipping.")
                break
        secrets[name] = value
    outcome: InitOutcome = execute_init(plan, host, secrets)
    # One message per secret, printed after execute_init from the status rows (D6).
    for status in outcome.secrets:
        if status.status == "created":
            print(f"Created: {status.name}")
        elif status.status == "updated":
            print(f"Updated: {status.name}")
        elif status.status == "skipped_existing":
            if target.scope == "org":
                print(
                    f"Skipped: {status.name}: already exists in {target.org}; "
                    "access from this repo not checked (see #1315)"
                )
            else:
                print(f"Skipped: {status.name}: already exists; use --force to update")
        else:
            print(f"Failed: {status.name}: {status.detail}", file=sys.stderr)
    if outcome.success:
        return SetupOutcome(EXIT_OK, outcome.pr_url or "")
    return SetupOutcome(EXIT_ERRORS, outcome.pr_url or "")


def next_steps_message(url: str, slug: str) -> str:
    """The closing message: what the client does once the setup succeeded."""
    return NEXT_STEPS.format(url=url, slug=slug)


def preflight_message(report: PreflightReport) -> str:
    """The client-facing explanation of a failed preflight."""
    if report.missing_scopes:
        missing = ", ".join(report.missing_scopes)
        return (
            f"nebuko init: the gh token is missing the {missing} scope, which is required to "
            f"push .github/workflows/*.\nRun: {report.remediation}"
        )
    return f"nebuko init: gh is not authenticated.\nRun: {report.remediation}"


def _secret_target(target: SetupTarget) -> SecretTarget:
    return SecretTarget(
        scope="org" if target.org else "repo",
        repo=target.slug,
        org=target.org,
    )


def _caller_template() -> str:
    resource = files("nebuko.engine.init.defaults").joinpath("nebuko-caller.yaml")
    return resource.read_text(encoding="utf-8")
