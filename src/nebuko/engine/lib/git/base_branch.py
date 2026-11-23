"""The project's base branch, resolved in one place (issue #1452)."""

from __future__ import annotations

from collections.abc import Callable, Mapping

DEFAULT_BASE_BRANCH = "main"


def resolve_base_branch(
    env: Mapping[str, str], symref_head: Callable[[], str | None]
) -> str:
    """``NEBUKO_BASE_BRANCH`` > ``GITHUB_BASE_REF`` > ``origin/HEAD`` > ``main``.

    ``NEBUKO_BASE_BRANCH`` comes from ``.nebuko/nebuko.yaml`` (``base_branch``),
    so an explicit repo setting beats the trigger context. ``symref_head`` returns
    the remote HEAD (``origin/<name>``) or ``None`` when the checkout has none.
    """
    for key in ("NEBUKO_BASE_BRANCH", "GITHUB_BASE_REF"):
        value = (env.get(key) or "").strip()
        if value:
            return value
    head = (symref_head() or "").strip()
    if head:
        return head.removeprefix("origin/")
    return DEFAULT_BASE_BRANCH
