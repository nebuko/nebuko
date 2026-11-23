"""``nebuko init claude-code-repo`` — the per-user Lane D credential repo (ADR-0017).

Creates a private ``<login>/nebuko-claude-code`` repository, stores the
client's own ``CLAUDE_CODE_OAUTH_TOKEN`` as its secret, writes a thin workflow
pinned to the reusable workflow's commit SHA, and prints the GitHub App
installation link. The token is never stored, logged or passed in argv: it
travels only on the stdin of ``gh secret set``.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import subprocess
from typing import Callable, Sequence

from nebuko.engine.init.ports import SetupError, UsageError
from nebuko.engine.init.setup import EXIT_ERRORS, EXIT_OK, EXIT_USAGE

REUSABLE_WORKFLOW_REPO = "nebuko/nebuko"
REUSABLE_WORKFLOW_PATH = ".github/workflows/nebuko-workflow.yaml"
USER_REPO_NAME = "nebuko-claude-code"
USER_WORKFLOW_PATH = ".github/workflows/nebuko-run.yaml"
TOKEN_SECRET = "CLAUDE_CODE_OAUTH_TOKEN"
APP_INSTALL_URL = "https://github.com/apps/nebuko/installations/new"
DEFAULT_PIN_REF = "main"

GhRunner = Callable[..., subprocess.CompletedProcess]


def render_workflow(reusable_sha: str) -> str:
    """The user-repo workflow: owner guard first, then the pinned reusable run."""
    if len(reusable_sha) != 40 or any(c not in "0123456789abcdef" for c in reusable_sha):
        raise ValueError(f"reusable workflow pin must be a full commit SHA, got {reusable_sha!r}")
    return f"""\
name: Nebuko (claude-code, per-user)

# Written by `nebuko init claude-code-repo`. Only the repository owner may run
# here; the owner assert below is guard 4 of ADR-0017 and runs before any job
# that can reach the token.
on:
  repository_dispatch:
    types: [nebuko-run]

permissions:
  contents: read

jobs:
  owner-guard:
    runs-on: ubuntu-latest
    steps:
      - name: Assert the triggering actor is the repository owner
        env:
          ACTOR: ${{{{ github.event.client_payload.actor }}}}
          OWNER: ${{{{ github.repository_owner }}}}
        run: |
          if [ -z "$ACTOR" ] || [ "$ACTOR" != "$OWNER" ]; then
            echo "::error::actor '$ACTOR' is not the owner '$OWNER' of this repository" >&2
            exit 1
          fi

  agent:
    needs: owner-guard
    uses: {REUSABLE_WORKFLOW_REPO}/.github/workflows/nebuko-workflow.yaml@{reusable_sha}
    with:
      prompt_body: ${{{{ github.event.client_payload.prompt_body }}}}
      org_repo: ${{{{ github.event.client_payload.org_repo }}}}
      issue_number: ${{{{ github.event.client_payload.issue_number }}}}
      agent_config_path: agent-config-anthropic.yaml
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{{{ secrets.{TOKEN_SECRET} }}}}
      GH_PAT: ${{{{ github.event.client_payload.gh_token }}}}
    permissions:
      contents: write
      pull-requests: write
      issues: write
      id-token: write
      actions: write
"""


def _gh(runner: GhRunner, args: Sequence[str], *, stdin: str | None = None, check: bool = True):
    result = runner(["gh", *args], capture_output=True, text=True, input=stdin)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SetupError(f"gh {args[0]} failed: {detail}")
    return result


def resolve_reusable_sha(runner: GhRunner, ref: str) -> str:
    """The commit SHA the reusable workflow is pinned to; ``uses:`` never tracks a branch."""
    out = _gh(
        runner,
        ["api", f"repos/{REUSABLE_WORKFLOW_REPO}/commits/{ref}", "--jq", ".sha"],
    ).stdout.strip()
    return out


def _login(runner: GhRunner) -> str:
    login = _gh(runner, ["api", "user", "--jq", ".login"]).stdout.strip()
    if not login:
        raise SetupError("could not determine the GitHub login from `gh api user`")
    return login


def _repo_exists(runner: GhRunner, slug: str) -> bool:
    return _gh(runner, ["repo", "view", slug, "--json", "name"], check=False).returncode == 0


def _read_token(env: dict[str, str] | None = None, secret_fn: Callable[[str], str] | None = None) -> str:
    env = os.environ if env is None else env
    token = env.get(TOKEN_SECRET, "").strip()
    if token:
        return token
    token = (secret_fn or getpass.getpass)(
        f"{TOKEN_SECRET} (from `claude setup-token`): "
    ).strip()
    if not token:
        raise UsageError(f"{TOKEN_SECRET} is required (run `claude setup-token`)")
    return token


def run_claude_code_repo(
    pin_ref: str = DEFAULT_PIN_REF,
    *,
    runner: GhRunner = subprocess.run,
    secret_fn: Callable[[str], str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Create the user repo and return ``(exit_code, message)``; never raises setup errors."""
    try:
        login = _login(runner)
        slug = f"{login}/{USER_REPO_NAME}"
        if _repo_exists(runner, slug):
            raise SetupError(f"{slug} already exists; delete it or choose another name first")
        token = _read_token(env, secret_fn)
        sha = resolve_reusable_sha(runner, pin_ref)
        if not sha:
            raise SetupError(f"could not resolve {REUSABLE_WORKFLOW_REPO}@{pin_ref}")
        workflow = render_workflow(sha)
        _gh(runner, ["repo", "create", slug, "--private", "--add-readme",
                     "--description", "Nebuko Claude Code credential repo"])
        _gh(runner, ["secret", "set", TOKEN_SECRET, "--repo", slug], stdin=token)
        del token
        content = base64.b64encode(workflow.encode()).decode()
        payload = json.dumps({"message": "Add Nebuko run workflow", "content": content})
        _gh(runner, ["api", "--method", "PUT", f"repos/{slug}/contents/{USER_WORKFLOW_PATH}",
                     "--input", "-"], stdin=payload)
    except UsageError as exc:
        return EXIT_USAGE, f"nebuko init: {exc}"
    except SetupError as exc:
        return EXIT_ERRORS, f"nebuko init: {exc}"
    message = (
        f"Created private repository {slug} with the {TOKEN_SECRET} secret and {USER_WORKFLOW_PATH}\n"
        f"pinned to {REUSABLE_WORKFLOW_REPO}@{sha}.\n"
        f"Install the Nebuko GitHub App on {slug}: {APP_INSTALL_URL}"
    )
    return EXIT_OK, message
