"""``nebuko init`` — the command surface (issue #991).

Reached as ``nebuko init`` (dispatched by ``run_loop.main``). Interactive by
default; ``--non-interactive`` takes every answer from flags and
``NEBUKO_SECRET_<NAME>`` environment variables. Exit codes follow the
``lint-config`` contract: ``0`` success, ``1`` failure, ``2`` usage/preflight.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Collection, Sequence

from nebuko.engine.init.app_installation import DEFAULT_APP_SLUG, AppInstallationResolver
from nebuko.engine.init.caller_workflow import CallerWorkflowWriter
from nebuko.engine.init.github_host import GitHubHost
from nebuko.engine.init.ports import (
    InitAnswers,
    ProviderProfile,
    SetupError,
    SetupHost,
    SetupTarget,
    UsageError,
)
from nebuko.engine.init.providers import (
    DEFAULT_PROVIDER_NAME,
    PROVIDER_PROFILES,
    provider_by_name,
)
from nebuko.engine.init.repo_variables import RepoVariableWriter
from nebuko.engine.init.setup import (
    BROKER_VARIABLE,
    DEFAULT_BROKER_URL,
    EXIT_ERRORS,
    EXIT_OK,
    EXIT_USAGE,
    SetupContext,
    build_setup,
    next_steps_message,
    preflight_message,
    run_init,
    secret_names,
)

DEFAULT_RUNS_ON = "ubuntu-latest"
SECRET_ENV_PREFIX = "NEBUKO_SECRET_"
SECRET_SCOPE_REPOSITORY = "repository"
SECRET_SCOPE_ORGANIZATION = "organization"
SECRET_EXISTS_MESSAGE = "{name} already setup in {scope} [ skipping, use --force to update value ]"
WORDMARK = """
███╗   ██╗███████╗██████╗ ██╗   ██╗██╗  ██╗ ██████╗
████╗  ██║██╔════╝██╔══██╗██║   ██║██║ ██╔╝██╔═══██╗
██╔██╗ ██║█████╗  ██████╔╝██║   ██║█████╔╝ ██║   ██║
██║╚██╗██║██╔══╝  ██╔══██╗██║   ██║██╔═██╗ ██║   ██║
██║ ╚████║███████╗██████╔╝╚██████╔╝██║  ██╗╚██████╔╝
╚═╝  ╚═══╝╚══════╝╚═════╝  ╚═════╝ ╚═╝  ╚═╝ ╚═════╝
"""
GH_MISSING_MESSAGE = """\
nebuko init: the GitHub CLI (gh) is required but was not found on PATH.
Install it from https://cli.github.com and run `gh auth login`."""
PAT_INSTRUCTIONS = """\
A GitHub Personal Access Token is required (fine-grained, with Contents,
Pull requests and Issues read/write on the target repository). Create one at
https://github.com/settings/personal-access-tokens/new
"""


class InitPrompter:
    """The terminal adapter: text, choice, hidden secret and confirmation."""

    def __init__(
        self,
        input_fn: Callable[[str], str] | None = None,
        secret_fn: Callable[[str], str] | None = None,
    ) -> None:
        self._input = input_fn or input
        self._secret = secret_fn or getpass.getpass

    # @stub-protected
    def ask_text(self, label: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        answer = self._input(f"{label}{suffix}: ").strip()
        return answer or default

    # @stub-protected
    def ask_choice(self, label: str, options: Sequence[str], default: str = "") -> str:
        print(label)
        for index, option in enumerate(options, start=1):
            print(f"  {index}) {option}")
        while True:
            answer = self._input(f"Choice [{default}]: ").strip()
            if not answer and default:
                return default
            if answer in options:
                return answer
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
            print(f"Please answer with a number between 1 and {len(options)}.")

    # @stub-protected
    def ask_secret(self, name: str) -> str:
        return self._secret(f"{name}: ")

    # @stub-protected
    def confirm(self, label: str, default: bool = False) -> bool:
        hint = "Y/n" if default else "y/N"
        answer = self._input(f"{label} [{hint}]: ").strip().lower()
        if not answer:
            return default
        return answer in ("y", "yes")


def _no_input(prompt: str) -> str:
    raise SetupError("no interactive input available (use --non-interactive with flags)")


class NonInteractivePrompter(InitPrompter):
    """No TTY: never confirm an overwrite, and fail loudly on any prompt."""

    def __init__(self) -> None:
        super().__init__(input_fn=_no_input, secret_fn=_no_input)

    def confirm(self, label: str, default: bool = False) -> bool:
        return False


# @stub-protected
def main(argv: Sequence[str] | None = None) -> int:
    """Collect the answers and run the setup flow; return the exit code."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return _run(args)
    except UsageError as exc:
        print(f"nebuko init: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except SetupError as exc:
        print(f"nebuko init: {exc}", file=sys.stderr)
        return EXIT_ERRORS


def _run(args: argparse.Namespace) -> int:
    print(WORDMARK)
    context = build_setup(
        pin_ref=args.pin_ref,
        template_dir=Path(args.template_dir) if args.template_dir else None,
    )
    if not context.host.gh_available():
        # Fail before any prompt: without `gh` nothing else can work.
        print(GH_MISSING_MESSAGE, file=sys.stderr)
        return EXIT_USAGE
    prompter: InitPrompter = NonInteractivePrompter() if args.non_interactive else InitPrompter()
    target = _resolve_target(args, prompter)
    report = context.host.preflight(target)
    if not report.ok:
        # Check the token before asking for any secret value: the client can
        # refresh the scope and re-run without re-entering the values.
        print(preflight_message(report), file=sys.stderr)
        return EXIT_USAGE
    target = _resolve_secret_scope(args, prompter, target)
    context = _with_app_collaborators(context, target, args)
    # App mode is decided once, here, and reused for the PAT prompt and the
    # broker/caller-workflow steps (issue #1025).
    app_mode = _app_mode(args, context, target)
    # One read-only probe, reused for both the PAT and the provider key.
    existing = frozenset() if args.force else context.host.existing_secrets(target)
    skipped = _report_existing(context.host, target, existing, ("GH_PAT",), app_mode)
    provider = _resolve_provider(args, prompter)
    skipped |= _report_existing(context.host, target, existing, provider.secret_names, app_mode)
    answers = InitAnswers(
        target=target,
        provider=provider,
        runs_on=_resolve_runs_on(args, prompter),
        pin_ref=args.pin_ref or context.refs.latest(),
        secrets=_collect_secrets(
            args, prompter, context.host, target, provider, skipped, app_mode=app_mode
        ),
        force=args.force,
        skipped=skipped,
    )
    outcome = run_init(answers, context.source, context.host, prompter)
    if outcome.code == EXIT_OK:
        _setup_app_mode(context, app_mode, args)
        print(next_steps_message(outcome.pr_url, target.slug))
    return outcome.code


def _app_mode(args: argparse.Namespace, context: SetupContext, target: SetupTarget) -> bool:
    """Whether the App is installed; ``--assume-*`` overrides the detection."""
    if args.assume_app:
        return True
    if args.assume_pat:
        return False
    if context.resolver is not None:
        return context.resolver.is_installed()
    return context.host.app_installed(target)


def _with_app_collaborators(
    context: SetupContext, target: SetupTarget, args: argparse.Namespace
) -> SetupContext:
    """Bind the App-aware collaborators to the resolved target (issue #1025)."""
    if context.resolver is not None or not isinstance(context.host, GitHubHost):
        # Already injected (tests, or a caller-provided context): only the
        # per-invocation broker override still applies.
        return replace(context, broker_url=args.broker_url or context.broker_url)
    client = _github_transport()
    return replace(
        context,
        resolver=AppInstallationResolver(client, target.owner, target.repo, DEFAULT_APP_SLUG),
        variable_writer=RepoVariableWriter(client, target.owner, target.repo),
        workflow_writer=CallerWorkflowWriter(broker_variable=BROKER_VARIABLE),
        broker_url=args.broker_url or context.broker_url,
    )


def _github_transport() -> Any:
    """The ``gh``-backed transport, imported lazily like the GitHub adapter."""
    from nebuko.engine.paths import ensure_import_path

    ensure_import_path()
    import github_client

    return github_client.GitHubTransport()


def _setup_app_mode(context: SetupContext, app_mode: bool, args: argparse.Namespace) -> None:
    """Write the broker repo variable and the caller workflow (issue #1025)."""
    if context.variable_writer is not None:
        context.variable_writer.set(BROKER_VARIABLE, context.broker_url)
    if context.workflow_writer is not None:
        result = context.workflow_writer.write(update=True, dry_run=args.dry_run)
        print(f"Caller workflow: {result.value} ({context.workflow_writer.target})")
    if app_mode:
        print("Nebuko App detected: the workflow mints its token from the OIDC broker.")


def _resolve_target(args: argparse.Namespace, prompter: InitPrompter) -> SetupTarget:
    repo = args.repo
    if not repo:
        if args.non_interactive:
            raise UsageError("--repo is required in non-interactive mode")
        repo = prompter.ask_text("Target repository (owner/name)")
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise UsageError(f"invalid repository {repo!r}; expected owner/name")
    return SetupTarget(host="github.com", owner=owner, repo=name)


def _resolve_secret_scope(
    args: argparse.Namespace, prompter: InitPrompter, target: SetupTarget
) -> SetupTarget:
    """Where the secrets land: repository level (default) or organization level."""
    if args.org:
        return replace(target, org=args.org)
    if args.non_interactive:
        return target
    scope = prompter.ask_choice(
        "Store the secrets at repository level or organization level?",
        (SECRET_SCOPE_REPOSITORY, SECRET_SCOPE_ORGANIZATION),
        default=SECRET_SCOPE_REPOSITORY,
    )
    if scope != SECRET_SCOPE_ORGANIZATION:
        return target
    return replace(target, org=prompter.ask_text("Organization", target.owner))


def _report_existing(
    host: SetupHost,
    target: SetupTarget,
    existing: frozenset[str],
    names: Sequence[str],
    app_mode: bool = False,
) -> frozenset[str]:
    """Print the skip note for the ``names`` already stored at the target scope."""
    scope = target.org or target.slug
    skipped = set()
    for name in names:
        if name not in existing:
            continue
        if name == "GH_PAT" and app_mode:
            continue
        print(SECRET_EXISTS_MESSAGE.format(name=name, scope=scope))
        skipped.add(name)
    return frozenset(skipped)


def _resolve_provider(args: argparse.Namespace, prompter: InitPrompter) -> ProviderProfile:
    if args.provider:
        return provider_by_name(args.provider)
    if args.non_interactive:
        return provider_by_name(DEFAULT_PROVIDER_NAME)
    names = [profile.name for profile in PROVIDER_PROFILES]
    return provider_by_name(prompter.ask_choice("LLM provider", names, default=DEFAULT_PROVIDER_NAME))


def _resolve_runs_on(args: argparse.Namespace, prompter: InitPrompter) -> str:
    if args.runs_on:
        return args.runs_on
    if args.non_interactive:
        return DEFAULT_RUNS_ON
    return prompter.ask_text("Runner labels", DEFAULT_RUNS_ON)


def _collect_secrets(
    args: argparse.Namespace,
    prompter: InitPrompter,
    host: SetupHost,
    target: SetupTarget,
    provider: ProviderProfile,
    skip: Collection[str] = (),
    *,
    app_mode: bool | None = None,
) -> dict[str, str]:
    """Ask for (or read from the environment) the values to store as secrets."""
    names = list(secret_names(provider))
    installed = host.app_installed(target) if app_mode is None else app_mode
    if installed:
        names.remove("GH_PAT")
    secrets: dict[str, str] = {}
    for name in names:
        if name in skip:
            continue
        if args.non_interactive:
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
                print(f"No value for {name}; skipping.")
                continue
        secrets[name] = value
    return secrets


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nebuko init",
        description="Prepare a repository (or organization) for Nebuko.",
    )
    parser.add_argument("--repo", help="target repository as owner/name")
    parser.add_argument(
        "--org",
        help="store the secrets at organization level (skips the interactive scope prompt)",
    )
    parser.add_argument(
        "--provider",
        choices=[profile.name for profile in PROVIDER_PROFILES],
        help="LLM provider profile",
    )
    parser.add_argument(
        "--runs-on",
        dest="runs_on",
        help=f"runner labels for the workflow jobs (default: {DEFAULT_RUNS_ON})",
    )
    parser.add_argument(
        "--pin-ref",
        dest="pin_ref",
        help="release tag the generated workflow pins (default: the latest tag)",
    )
    parser.add_argument(
        "--template-dir",
        dest="template_dir",
        help="read the provider templates from a local checkout instead of HTTPS",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="take every answer from flags and NEBUKO_SECRET_* environment variables",
    )
    parser.add_argument(
        "--broker-url",
        dest="broker_url",
        default=None,
        help=(
            "OIDC token broker written to the "
            f"{BROKER_VARIABLE} repository variable (default: {DEFAULT_BROKER_URL})"
        ),
    )
    parser.add_argument(
        "--assume-app",
        dest="assume_app",
        action="store_true",
        help="treat the Nebuko App as installed without probing GitHub (offline)",
    )
    parser.add_argument(
        "--assume-pat",
        dest="assume_pat",
        action="store_true",
        help="force PAT mode even when the App is installed (offline)",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="print what would be written without writing the caller workflow",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing files and secrets without asking",
    )
    return parser
