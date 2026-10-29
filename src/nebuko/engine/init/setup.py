"""The ``nebuko init`` orchestration (issue #991).

``render_files`` is pure (template in, files out); ``run_init`` is the flow:
render → idempotence check → pull request → secrets → outcome. The ``gh``
preflight runs in the CLI *before* the prompts, so a missing scope is reported
before the client is asked for any secret value.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from nebuko.engine.init.app_installation import AppInstallationResolver
from nebuko.engine.init.caller_workflow import CallerWorkflowWriter
from nebuko.engine.init.github_host import GitHubHost
from nebuko.engine.init.ports import (
    GeneratedFiles,
    InitAnswers,
    PreflightReport,
    Prompter,
    ProviderProfile,
    ReleaseRefResolver,
    SetupHost,
    TemplateSource,
)
from nebuko.engine.init.refs import (  # noqa: F401  (DEFAULT_WORKFLOW_REF is re-exported)
    DEFAULT_WORKFLOW_REF,
    GitTagRefResolver,
    StaticRefResolver,
)
from nebuko.engine.init.repo_variables import RepoVariableWriter
from nebuko.engine.init.sources import FileTemplateSource, HttpTemplateSource

EXIT_OK = 0
EXIT_ERRORS = 1
EXIT_USAGE = 2

WORKFLOW_TEMPLATE = "templates/nebuko-caller.yaml"
AGENT_CONFIG_PATH = ".nebuko/agents/agent-config.yaml"
WORKFLOW_PATH = ".github/workflows/nebuko.yaml"
# Published broker endpoint; the generated workflow reads it from the repository
# variable so a client can point at their own broker without editing the file.
DEFAULT_BROKER_URL = "https://token-broker.nebuko.dev"
BROKER_VARIABLE = "NEBUKO_BROKER_URL"
BRANCH = "nebuko/init"
PR_TITLE = "chore: add Nebuko agent configuration and workflow"
NEXT_STEPS = """\
Next steps
1. Review and merge the pull request: {url}
2. Once merged, try it: comment "@@dev Hello Nebuko!" on an issue in {slug}.

Let's build something with Nebuko's agent teams 🤝"""


@dataclass(frozen=True)
class SetupContext:
    """The adapters one ``init`` run talks to."""

    source: TemplateSource
    refs: ReleaseRefResolver
    host: SetupHost
    # App-aware setup (issue #1025): detection, the broker repo variable and the
    # generated caller workflow. ``None`` keeps the pre-App behaviour.
    resolver: AppInstallationResolver | None = None
    variable_writer: RepoVariableWriter | None = None
    workflow_writer: CallerWorkflowWriter | None = None
    broker_url: str = DEFAULT_BROKER_URL


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
    return SetupContext(source=source, refs=refs, host=GitHubHost())


# @stub-protected
def render_files(answers: InitAnswers, source: TemplateSource) -> GeneratedFiles:
    """Fetch the provider config and render the caller workflow."""
    agent_config = source.fetch(answers.provider.template_path, answers.pin_ref)
    secrets_block = "\n".join(
        f"      {name}: ${{{{ secrets.{name} }}}}" for name in secret_names(answers.provider)
    )
    workflow = (
        _workflow_template()
        .replace("${pin_ref}", answers.pin_ref)
        .replace("${agent_config_path}", AGENT_CONFIG_PATH)
        .replace("${runs_on}", answers.runs_on)
        .replace("${secrets_block}", secrets_block)
    )
    return GeneratedFiles(
        agent_config_path=AGENT_CONFIG_PATH,
        agent_config=agent_config,
        workflow_path=WORKFLOW_PATH,
        workflow=workflow,
    )


def secret_names(provider: ProviderProfile) -> tuple[str, ...]:
    """The provider's key plus ``GH_PAT``, which the reusable workflow requires."""
    return (*provider.secret_names, "GH_PAT")


# @stub-protected
def run_init(
    answers: InitAnswers,
    source: TemplateSource,
    host: SetupHost,
    prompter: Prompter,
) -> SetupOutcome:
    """Run the setup flow and return the exit code plus the pull-request URL."""
    generated = render_files(answers, source)
    existing = host.existing_paths(
        answers.target, (generated.agent_config_path, generated.workflow_path)
    )
    if existing and not answers.force:
        listed = ", ".join(sorted(existing))
        if not prompter.confirm(
            f"{listed} already exist(s) in {answers.target.slug}; overwrite?", default=False
        ):
            print(f"Aborted: {listed} left untouched (pass --force to overwrite).", file=sys.stderr)
            return SetupOutcome(EXIT_ERRORS)

    url = host.open_pr(answers.target, BRANCH, PR_TITLE, _pr_body(answers), generated)
    print(f"Pull request: {url}")

    scope = answers.target.org or answers.target.slug
    for name in secret_names(answers.provider):
        if name in answers.skipped and not answers.force:
            continue  # Already stored: the skip note was printed when it was probed.
        value = answers.secrets.get(name)
        if not value:
            print(f"Secret {name} not set (no value provided).")
            continue
        host.set_secret(answers.target, name, value)
        print(f"Secret {name} set on {scope}.")
    return SetupOutcome(EXIT_OK, url)


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


def _pr_body(answers: InitAnswers) -> str:
    return (
        "Generated by `nebuko init`.\n\n"
        f"- `.nebuko/agents/agent-config.yaml` — {answers.provider.name} provider profile\n"
        f"- `.github/workflows/nebuko.yaml` — caller workflow pinned to `{answers.pin_ref}`\n\n"
        "No secret value is included in this pull request: the secrets are stored in the "
        "repository settings by `nebuko init`.\n"
    )


def _workflow_template() -> str:
    resource = files("nebuko.engine.init").joinpath(*WORKFLOW_TEMPLATE.split("/"))
    return resource.read_text(encoding="utf-8")
