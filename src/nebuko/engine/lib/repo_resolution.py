"""The ``owner/name`` repository a run targets: one rule for every call site.

For ``repository_dispatch`` the event payload's ``repository`` is the *caller*
repo (the per-user repo that forwards the dispatch), not the repo the issue
lives in. The exported env (``REPO_NAME``/``GITHUB_REPOSITORY``) is therefore
read first; the trigger and payload are fallbacks for hosts that export nothing.
"""

from __future__ import annotations

from collections.abc import Mapping

from nebuko.engine.models import Trigger


def resolve_repo(env: Mapping[str, str], trigger: Trigger | None) -> str:
    """Return the targeted ``owner/name``, or ``""`` when none is determinable."""
    for key in ("REPO_NAME", "GITHUB_REPOSITORY"):
        value = env.get(key)
        if value:
            return str(value)
    if trigger is None:
        return ""
    if trigger.org_repo:
        return trigger.org_repo
    payload = trigger.payload or {}
    client_payload = payload.get("client_payload") or {}
    if isinstance(client_payload, Mapping) and client_payload.get("org_repo"):
        return str(client_payload["org_repo"])
    repository = payload.get("repository") or {}
    return str(repository.get("full_name") or "")
