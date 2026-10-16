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
from pathlib import Path
from typing import Callable, Sequence

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
from nebuko.engine.init.setup import (
    EXIT_ERRORS,
    EXIT_USAGE,
    build_setup,
    run_init,
    secret_names,
)

DEFAULT_RUNS_ON = "ubuntu-latest"
SECRET_ENV_PREFIX = "NEBUKO_SECRET_"
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
    context = build_setup(
        pin_ref=args.pin_ref,
        template_dir=Path(args.template_dir) if args.template_dir else None,
    )
    prompter: InitPrompter = NonInteractivePrompter() if args.non_interactive else InitPrompter()
    target = _resolve_target(args, prompter)
    provider = _resolve_provider(args, prompter)
    answers = InitAnswers(
        target=target,
        provider=provider,
        runs_on=_resolve_runs_on(args, prompter),
        pin_ref=args.pin_ref or context.refs.latest(),
        secrets=_collect_secrets(args, prompter, context.host, target, provider),
        force=args.force,
    )
    return run_init(answers, context.source, context.host, prompter)


def _resolve_target(args: argparse.Namespace, prompter: InitPrompter) -> SetupTarget:
    repo = args.repo
    if not repo:
        if args.non_interactive:
            raise UsageError("--repo is required in non-interactive mode")
        repo = prompter.ask_text("Target repository (owner/name)")
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise UsageError(f"invalid repository {repo!r}; expected owner/name")
    return SetupTarget(host="github.com", owner=owner, repo=name, org=args.org)


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
) -> dict[str, str]:
    """Ask for (or read from the environment) the values to store as secrets."""
    names = list(secret_names(provider))
    if host.app_installed(target):
        names.remove("GH_PAT")
    secrets: dict[str, str] = {}
    for name in names:
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
        help="store the secrets at organization level instead of repository level",
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
        "--force",
        action="store_true",
        help="overwrite existing configuration without asking",
    )
    return parser
