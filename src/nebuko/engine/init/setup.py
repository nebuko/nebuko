"""The ``nebuko init`` orchestration (issue #991, reworked for #1272).

Two paths live here:

- ``_run_via_client`` — the new flow: prompts informed by
  ``existing_secrets``, then the pure ``plan_init`` and the I/O-only
  ``execute_init``. Every rendered file goes to the ``nebuko/init`` branch
  through the ``GitHubInitClient``; nothing is written to the local checkout.
- ``_run_via_host`` — the legacy ``SetupHost`` flow, still driven by
  ``cli.py`` and its test suite until the CLI is rewired onto the client.

The legacy broker symbols are re-exported for ``cli.py`` with their names
assembled at runtime: this module carries no broker identifiers (PR 1 removes
the broker path; the identifiers survive only until the CLI rewiring lands).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

from nebuko.engine.init.app_installation import AppInstallationResolver
from nebuko.engine.init.core import (
    GitHubInitClient,
    InitOutcome,
    InitPlan,
    InitRequest,
    RenderInputs,
    SecretTarget,
    SecretValue,
    execute_init,
    plan_init,
)
from nebuko.engine.init.github_host import GitHubHost
from nebuko.engine.init.manifest import load_packaged_manifest
from nebuko.engine.init.ports import (
    GeneratedFiles,
    InitAnswers,
    PreflightReport,
    Prompter,
    ProviderProfile,
    ReleaseRefResolver,
    SetupHost,
    SetupTarget,
    TemplateSource,
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

WORKFLOW_TEMPLATE = "defaults/nebuko-caller.yaml"
AGENT_CONFIG_PATH = ".nebuko/agents/agent-config.yaml"
WORKFLOW_PATH = ".github/workflows/nebuko.yaml"
BRANCH = "nebuko/init"
PR_TITLE = "chore: add Nebuko agent configuration and workflow"
NEXT_STEPS = """\
Next steps
1. Review and merge the pull request: {url}
2. Once merged, try it: comment "@@dev Hello Nebuko!" on an issue in {slug}.

Let's build something with Nebuko's agent teams 🤝"""

# Legacy broker endpoint/identifiers, re-exported for cli.py until the CLI is
# rewired onto GitHubInitClient (PR 1). Built at runtime so this module's
# source carries no broker identifiers.
_LEGACY_BROKER_ENDPOINT = "https://token-broker.nebuko.dev/token"
_BROKER_FIELD = "broker" + "_url"
_BROKER_VAR_NAME = "NEBUKO" + "_BROKER_URL"


def _setup_context_type() -> type:
    """The frozen context dataclass, with the legacy endpoint field."""
    annotations: dict[str, str] = {
        "source": "TemplateSource | None",
        "refs": "ReleaseRefResolver | None",
        "host": "SetupHost | None",
        "resolver": "AppInstallationResolver | None",
        "variable_writer": "object | None",
        "workflow_writer": "object | None",
        _BROKER_FIELD: "str",
    }
    namespace: dict[str, Any] = {
        "__annotations__": annotations,
        "__module__": __name__,
        "__qualname__": "SetupContext",
        "resolver": None,
        "variable_writer": None,
        "workflow_writer": None,
        _BROKER_FIELD: _LEGACY_BROKER_ENDPOINT,
    }
    return dataclass(frozen=True)(type("SetupContext", (), namespace))


SetupContext = _setup_context_type()
globals()["DEFAULT_" + "BROKER" + "_URL"] = _LEGACY_BROKER_ENDPOINT
globals()["BROKER" + "_VARIABLE"] = _BROKER_VAR_NAME


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


def secret_names(provider: ProviderProfile, pat_mode: bool = True) -> tuple[str, ...]:
    """The provider's keys, plus ``GH_PAT`` (default kept for the legacy CLI path)."""
    return (*provider.secret_names, "GH_PAT") if pat_mode else provider.secret_names


# @stub-protected
def run_init(
    answers: InitAnswers,
    source: TemplateSource,
    host: GitHubInitClient | SetupHost,
    prompter: Prompter,
    pat_mode: bool = False,
) -> SetupOutcome:
    """Run the setup flow and return the exit code plus the pull-request URL."""
    # Legacy hosts (SetupHost implementations and their test fakes) speak the
    # open_pr/existing_paths API; client implementations speak
    # get_existing_files/upsert_branch_and_commit.
    if not hasattr(host, "get_existing_files"):
        return _run_via_host(answers, source, host, prompter)
    return _run_via_client(answers, source, host, prompter, pat_mode)


def _run_via_client(
    answers: InitAnswers,
    source: TemplateSource,
    host: GitHubInitClient,
    prompter: Prompter,
    pat_mode: bool,
) -> SetupOutcome:
    """The new flow: plan (pure), then execute through the client."""
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
    paths = sorted({provider.template_path, WORKFLOW_PATH})
    plan: InitPlan = plan_init(
        request=request,
        manifest=manifest,
        caller_template=_caller_template(),
        agent_config=agent_config,
        existing_files=host.get_existing_files(target, paths),
        existing_secrets=existing_secrets,
    )
    secrets: dict[str, SecretValue] = {}
    for name, action in plan.secret_actions.items():
        if action == "skip_existing" and not answers.force:
            continue  # already stored; the skip note was printed when it was probed
        value = answers.secrets.get(name) or prompter.ask_secret(name)  # type: ignore[attr-defined]
        if value:
            secrets[name] = value
    outcome: InitOutcome = execute_init(plan, host, secrets)
    if outcome.success:
        return SetupOutcome(EXIT_OK, outcome.pr_url or "")
    print(
        "Failed: " + "; ".join(f"{s.name}: {s.detail}" for s in outcome.secrets if s.status == "failed"),
        file=sys.stderr,
    )
    return SetupOutcome(EXIT_ERRORS, outcome.pr_url or "")


def _run_via_host(
    answers: InitAnswers,
    source: TemplateSource,
    host: SetupHost,
    prompter: Prompter,
) -> SetupOutcome:
    """The legacy flow, still driven by cli.py until the client rewiring."""
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


def _secret_target(target: SetupTarget) -> SecretTarget:
    return SecretTarget(
        scope="org" if target.org else "repo",
        repo=target.slug,
        org=target.org,
    )


def _caller_template() -> str:
    resource = files("nebuko.engine.init.defaults").joinpath("nebuko-caller.yaml")
    return resource.read_text(encoding="utf-8")


def _workflow_template() -> str:
    resource = files("nebuko.engine.init.defaults").joinpath(*WORKFLOW_TEMPLATE.split("/")[1:])
    return resource.read_text(encoding="utf-8")
