"""The one rule for which ``owner/name`` a run targets.

For ``repository_dispatch`` the event payload's ``repository`` is the *caller*
repo, not the repo the issue lives in, so it must never win over the run's
exported identity (issue #1099 / #1388).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from nebuko.engine.models import Trigger


def resolve_repo(env: Mapping[str, str], trigger: Trigger | None) -> str:
    """``owner/name`` the run targets, or ``""`` when undeterminable.

    A ``repository_dispatch`` targets ``REPO_NAME`` / ``GITHUB_REPOSITORY`` (the
    workflow exports them) or the dispatched ``org_repo``; its payload
    ``repository`` is the caller and is never used. Other triggers keep the
    payload's repository, falling back to the env.
    """
    env_repo = str(env.get("REPO_NAME") or env.get("GITHUB_REPOSITORY") or "")
    if trigger is None:
        return env_repo
    if trigger.type == "repository_dispatch":
        return env_repo or trigger.org_repo
    payload_repo = (trigger.payload or {}).get("repository") or {}
    return str(payload_repo.get("full_name") or "") or env_repo
