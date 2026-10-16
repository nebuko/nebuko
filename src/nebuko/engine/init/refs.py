"""Release-ref resolution for the generated caller workflow (issue #991).

The workflow pins a release tag, never ``@main``: the tag is the version axis
shared by the binary the client installed and the reusable workflow it calls.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any, Callable

from nebuko.engine.init.ports import ReleaseRefResolver

# Offline fallback when the tag list cannot be read (no network, no ``gh``).
# Re-exported by ``setup`` as the module-level default pin.
DEFAULT_WORKFLOW_REF = "v0.0.2"

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=False, **kwargs)


class GitTagRefResolver(ReleaseRefResolver):
    """The semver-max of the remote tag list, via the client's ``gh``."""

    def __init__(
        self,
        repo: str = "nebuko/nebuko",
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ) -> None:
        self._repo = repo
        self._runner = runner or _run

    def latest(self) -> str:
        try:
            result = self._runner(
                ["gh", "api", f"repos/{self._repo}/tags", "--paginate", "--jq", ".[].name"],
                capture_output=True,
                text=True,
            )
        except OSError:
            return DEFAULT_WORKFLOW_REF
        if result.returncode != 0:
            return DEFAULT_WORKFLOW_REF
        return _semver_max(result.stdout.split()) or DEFAULT_WORKFLOW_REF


class StaticRefResolver(ReleaseRefResolver):
    """A fixed ref — ``--pin-ref``, or a test double."""

    def __init__(self, ref: str) -> None:
        self._ref = ref

    def latest(self) -> str:
        return self._ref


def _semver_max(tags: list[str]) -> str:
    """The highest ``vX.Y.Z`` tag, keeping the tag's own spelling."""
    best: tuple[tuple[int, int, int], str] | None = None
    for tag in tags:
        match = _SEMVER.match(tag.strip())
        if match is None:
            continue
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if best is None or version > best[0]:
            best = (version, tag.strip())
    return best[1] if best else ""
