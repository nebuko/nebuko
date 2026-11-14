"""Engine git subsystem — branch scaffold, session guard, PR finalizer.

Package-by-feature layout (mirrors ``pricing/`` and ``workflow_state/``):
``branch_scaffold.py`` prepares the dev session's feature branch,
``git_session.py`` guards credentials and leftover work, and
``pr_finalize.py`` opens the PR after the agent phase (issue #1138).
"""

from .branch_scaffold import (
    DEFAULT_BASE_BRANCH,
    BranchScaffold,
    ScaffoldResult,
    branch_slug,
    feature_branch_name,
    issue_title_from_payload,
)
from .git_session import (
    BENIGN_SKIPS,
    GIT_IDENTITY_EMAIL,
    GIT_IDENTITY_NAME,
    GitSessionGuard,
    GuardResult,
)
from .idempotency import GitIdempotency
from .pr_finalize import PrFinalizer, PrFinalizeResult, pr_number_from_url

__all__ = [
    "BENIGN_SKIPS",
    "DEFAULT_BASE_BRANCH",
    "GIT_IDENTITY_EMAIL",
    "GIT_IDENTITY_NAME",
    "BranchScaffold",
    "GitIdempotency",
    "GitSessionGuard",
    "GuardResult",
    "PrFinalizeResult",
    "PrFinalizer",
    "ScaffoldResult",
    "branch_slug",
    "feature_branch_name",
    "issue_title_from_payload",
    "pr_number_from_url",
]
