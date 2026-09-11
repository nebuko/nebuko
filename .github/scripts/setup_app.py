"""Guided setup for the git-flow-ai GitHub App (issue #303).

Replaces the long-lived PAT (`GH_PAT`) with a dedicated GitHub App identity:
short-lived installation tokens, per-repo revocation, and bot-type actions.

Subcommands:
    manifest-url   Build the pre-filled "New GitHub App" manifest URL.
    lookup         Resolve APP_ID + INSTALLATION_ID(s) for an existing app.
    secrets        Print the three workflow secrets from --app-id/--installation-id.
    print-secrets  Format the same values as consumer-workflow `secrets:` YAML.

The private key is intentionally NOT fetched here: GitHub only exposes it as a
one-time download in the browser UI, and it must never pass through this CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

API_BASE = "https://api.github.com"
APP_SLUG = "git-flow-ai"

DEFAULT_PERMISSIONS = {
    "contents": "write",
    "issues": "write",
    "pull_requests": "write",
    "checks": "read",
    "metadata": "read",
}
DEFAULT_EVENTS = ["issues", "issue_comment", "pull_request", "push"]


class SetupError(Exception):
    """Raised when a setup step cannot be completed."""


def build_manifest(slug: str = APP_SLUG,
                   permissions: Optional[Dict[str, str]] = None,
                   events: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build the GitHub App manifest payload (pure)."""
    manifest: Dict[str, Any] = {
        "name": slug,
        "slug": slug,
        "description": "Multi-agent AI orchestration on GitHub issues (git-flow-ai).",
        "default_event_permissions": "none",
        "default_permissions": permissions or DEFAULT_PERMISSIONS,
        "events": events or DEFAULT_EVENTS,
        "hook_attributes": {
            "active": False,
            "config": {"secret": ""},
        },
        "public": True,
    }
    return manifest


def manifest_url(payload: Dict[str, Any]) -> str:
    """Encode a manifest payload as the /new?manifest= URL (pure)."""
    encoded = urllib.request.quote(json.dumps(payload, separators=(",", ":")))
    return f"https://github.com/apps/new?manifest={encoded}"


def lookup_app_and_installations(slug: str, token: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Fetch the app record and its installations for `slug` (I/O boundary)."""
    app = _fetch_app_by_slug(slug, token)
    installations = _fetch_installations(app["id"], token)
    return app, installations


def _fetch_app_by_slug(slug: str, token: str) -> Dict[str, Any]:
    """Fetch the app record matching `slug` (I/O boundary)."""
    request = urllib.request.Request(
        f"{API_BASE}/app",
        headers=_headers(token),
    )
    try:
        with urllib.request.urlopen(request) as response:
            current = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise SetupError(f"GET /app failed: HTTP {exc.code}: {exc.read().decode()}") from exc
    except urllib.error.URLError as exc:
        raise SetupError(f"GET /app failed: {exc.reason}") from exc
    if current.get("slug") != slug:
        raise SetupError(
            f"Token belongs to app '{current.get('slug')}', expected '{slug}'. "
            "Use a token of the git-flow-ai app."
        )
    return current


def _fetch_installations(app_id: int, token: str) -> List[Dict[str, Any]]:
    """List installation records for `app_id` (I/O boundary)."""
    request = urllib.request.Request(
        f"{API_BASE}/app/installations",
        headers=_headers(token),
    )
    try:
        with urllib.request.urlopen(request) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise SetupError(f"GET /app/installations failed: HTTP {exc.code}: {exc.read().decode()}") from exc
    except urllib.error.URLError as exc:
        raise SetupError(f"GET /app/installations failed: {exc.reason}") from exc
    if not isinstance(body, list):
        raise SetupError(f"Unexpected installations response: {body!r}")
    return body


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "git-flow-ai-setup",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def secrets_block(slug: str, app_id: int, installation_ids: List[int]) -> str:
    """Render the three workflow secrets with one installation id (pure)."""
    if not installation_ids:
        raise SetupError("No installations found — install the app on your repo first.")
    primary = installation_ids[0]
    return (
        f"GH_APP_ID: {app_id}\n"
        f"GH_APP_INSTALLATION_ID: {primary}\n"
        f"GH_APP_PRIVATE_KEY: (paste the PEM key downloaded at https://github.com/settings/apps/{slug})"
    )


def consumer_secrets_yaml(slug: str, app_id: int, installation_ids: List[int]) -> str:
    """Render the `secrets:` mapping to paste into a consumer workflow (pure)."""
    if not installation_ids:
        raise SetupError("No installations found — install the app on your repo first.")
    primary = installation_ids[0]
    expr_open = "${{"
    expr_close = "}}"
    return (
        "    secrets:" + chr(10)
        + "      OPENROUTER_API_KEY: " + expr_open + " secrets.OPENROUTER_API_KEY " + expr_close + chr(10)
        + "      GH_APP_ID: " + expr_open + " secrets.GH_APP_ID " + expr_close + f" # {app_id}" + chr(10)
        + "      GH_APP_PRIVATE_KEY: " + expr_open + " secrets.GH_APP_PRIVATE_KEY " + expr_close + chr(10)
        + "      GH_APP_INSTALLATION_ID: " + expr_open + " secrets.GH_APP_INSTALLATION_ID " + expr_close + f" # {primary}" + chr(10)
    )


def cmd_manifest_url(args: argparse.Namespace) -> int:
    payload = build_manifest()
    print(manifest_url(payload))
    print(
        "\nOpen the URL above in a browser, review the pre-filled form and click "
        "'Create GitHub App'.\n"
        "Then download the private key (one-time download, never shared with this CLI) "
        "and run: python3 .github/scripts/setup_app.py lookup --token <app-token>",
    )
    return 0


def cmd_lookup(args: argparse.Namespace) -> int:
    app, installations = lookup_app_and_installations(args.slug, args.token)
    installation_ids = [i["id"] for i in installations]
    print(f"Found app: {app['name']} (slug={app['slug']}, id={app['id']})")
    if not installation_ids:
        print("No installations yet. Install the app on a repo "
              "(Settings → Applications → your app → Install) and re-run lookup.")
        return 0
    for installation in installations:
        owner = installation.get("account", {}).get("login", "?")
        print(f"  installation {installation['id']} → {owner}")
    print()
    print("Add these as repository (or organization) secrets:")
    print(secrets_block(args.slug, app["id"], installation_ids))
    return 0


def cmd_secrets(args: argparse.Namespace) -> int:
    ids = [int(v) for v in args.installation_id.split(",") if v.strip()]
    print(secrets_block(args.slug, args.app_id, ids))
    return 0


def cmd_print_secrets(args: argparse.Namespace) -> int:
    ids = [int(v) for v in args.installation_id.split(",") if v.strip()]
    print(consumer_secrets_yaml(args.slug, args.app_id, ids))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("manifest-url", help="Print the pre-filled app creation URL").set_defaults(
        func=cmd_manifest_url)

    p_lookup = sub.add_parser("lookup", help="Fetch APP_ID and INSTALLATION_ID(s)")
    p_lookup.add_argument("--token", required=True, help="GitHub token (PAT or app token)")
    p_lookup.add_argument("--slug", default=APP_SLUG)
    p_lookup.set_defaults(func=cmd_lookup)

    for name, func in (("secrets", cmd_secrets), ("print-secrets", cmd_print_secrets)):
        p = sub.add_parser(name)
        p.add_argument("--app-id", type=int, required=True)
        p.add_argument("--installation-id", required=True, help="Comma-separated installation ids")
        p.add_argument("--slug", default=APP_SLUG)
        p.set_defaults(func=func)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SetupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())