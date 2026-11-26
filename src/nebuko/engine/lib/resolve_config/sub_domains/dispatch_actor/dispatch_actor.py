"""Verified dispatch actor for gate decisions on the personal repo (issue #1482).

A ``repository_dispatch`` carries ``client_payload.actor``, and the caller
workflow controls that field. It is trusted only when the runner-set values
agree: the event is a dispatch, the run is the personal repo's own, and the
actor equals ``GITHUB_REPOSITORY_OWNER``. :func:`personal_dispatch_actor` is that
one check; the personal lane and the gate decision both call it. The gate adds a
project-repo permission lookup on top (:func:`verify_dispatch_actor`).
"""

import os
import re
import subprocess
from typing import Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DispatchActorCheck",
    "personal_dispatch_actor",
    "lookup_collaborator_association",
    "verify_dispatch_actor",
    "WRITE_PERMISSIONS",
    "REASON_NOT_DISPATCH",
    "REASON_NOT_PERSONAL_REPO",
    "REASON_NOT_OWNER",
    "REASON_NO_PROJECT_TOKEN",
    "REASON_NO_PROJECT_REPO",
    "REASON_LOOKUP_TIMED_OUT",
    "REASON_LOOKUP_UNAVAILABLE",
    "REASON_LOOKUP_REFUSED",
    "REASON_NO_WRITE",
]

#: Repository permissions that map onto the ``COLLABORATOR`` gate association.
WRITE_PERMISSIONS = frozenset({"admin", "maintain", "write"})

#: Coarse refusal reasons. Notices name a reason, never a token or raw stderr.
REASON_NOT_DISPATCH = "not-a-dispatch"
REASON_NOT_PERSONAL_REPO = "not-personal-repo"
REASON_NOT_OWNER = "not-owner"
REASON_NO_PROJECT_TOKEN = "no-project-token"
REASON_NO_PROJECT_REPO = "no-project-repo"
REASON_LOOKUP_TIMED_OUT = "lookup-timed-out"
REASON_LOOKUP_UNAVAILABLE = "lookup-unavailable"
#: GitHub answered 403 or 404 to the lookup. Not evidence of missing write access.
REASON_LOOKUP_REFUSED = "lookup-refused-by-github"
REASON_NO_WRITE = "no-write-access"

_LOOKUP_TIMEOUT_S = 30.0
_HTTP_STATUS = re.compile(r"HTTP (\d{3})")
_REFUSED_STATUSES = frozenset({"403", "404"})


class DispatchActorCheck(BaseModel):
    """Outcome of verifying a dispatch actor for a gate decision.

    Attributes:
        verified: True only when the actor passed every check, including write
            access on the project repo.
        actor: the payload actor (may be empty when the payload has none).
        association: ``COLLABORATOR`` when verified, else empty.
        reason: empty when verified, else one of the ``REASON_*`` codes.
    """

    model_config = ConfigDict(frozen=True)

    verified: bool
    actor: str = ""
    association: str = ""
    reason: str = ""


def personal_dispatch_actor(env: Mapping[str, str]) -> Tuple[str, str]:
    """The owner-verified dispatch actor of this personal-repo run.

    Returns ``(actor, "")`` when the run is a ``repository_dispatch`` on the
    personal repo and the actor equals ``GITHUB_REPOSITORY_OWNER`` (case
    insensitive). Otherwise returns ``(actor_or_empty, reason)``. Reads only
    GitHub-set values, never caller inputs.
    """
    # Lazy: these modules import the resolver, which imports this package.
    from nebuko.engine.lib.resolve_config.features.resolve_route.agent_config_resolver import (  # noqa: PLC0415
        personal_run_from_env,
    )
    from nebuko.engine.lib.workflow_state.workflow_state import (  # noqa: PLC0415
        dispatch_actor_from_event,
    )

    actor = dispatch_actor_from_event(env) or ""
    if not actor:
        return "", REASON_NOT_DISPATCH
    if not personal_run_from_env(env):
        return actor, REASON_NOT_PERSONAL_REPO
    owner = (env.get("GITHUB_REPOSITORY_OWNER") or "").strip()
    if not owner or actor.lower() != owner.lower():
        return actor, REASON_NOT_OWNER
    return actor, ""


def lookup_collaborator_association(
    repo: str, login: str, token: str, timeout: float = _LOOKUP_TIMEOUT_S
) -> Tuple[Optional[str], str]:
    """Map ``login``'s permission on ``repo`` onto a gate association.

    Returns ``("COLLABORATOR", "")`` for write, maintain or admin. Otherwise
    returns ``(None, reason)``. A 403 or 404 from GitHub gets its own reason,
    because it does not show that the login lacks write access. The token is
    passed only through the subprocess env.
    """
    if not (token or "").strip():
        return None, REASON_NO_PROJECT_TOKEN
    if not (repo or "").strip():
        return None, REASON_NO_PROJECT_REPO
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GH_TOKEN": token.strip(),
        "GH_PROMPT_DISABLED": "1",
    }
    try:
        proc = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo.strip()}/collaborators/{login}/permission",
                "--jq",
                ".permission",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, REASON_LOOKUP_TIMED_OUT
    except OSError:
        return None, REASON_LOOKUP_UNAVAILABLE
    if proc.returncode != 0:
        status = _HTTP_STATUS.search(proc.stderr or "")
        if status and status.group(1) in _REFUSED_STATUSES:
            return None, REASON_LOOKUP_REFUSED
        return None, REASON_LOOKUP_UNAVAILABLE
    if proc.stdout.strip() in WRITE_PERMISSIONS:
        return "COLLABORATOR", ""
    return None, REASON_NO_WRITE


def verify_dispatch_actor(env: Mapping[str, str]) -> DispatchActorCheck:
    """Verify a personal-repo dispatch actor for a gate decision (issue #1482).

    Runs the shared :func:`personal_dispatch_actor` check first. Only when it
    passes does it look up the actor's permission on the project repo
    (``REPO_NAME``, set from the dispatched ``org_repo``) with ``GH_TOKEN``.
    ``GITHUB_REPOSITORY`` is never used: the owner has admin on their own
    personal repo, so a lookup there would verify any owner dispatch.
    The caller's own owner-guard and flags are never read.
    """
    actor, reason = personal_dispatch_actor(env)
    if reason:
        return DispatchActorCheck(verified=False, actor=actor, reason=reason)
    association, lookup_reason = lookup_collaborator_association(
        env.get("REPO_NAME") or "", actor, env.get("GH_TOKEN") or ""
    )
    if association is None:
        return DispatchActorCheck(verified=False, actor=actor, reason=lookup_reason)
    return DispatchActorCheck(verified=True, actor=actor, association=association)
