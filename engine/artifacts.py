"""Per-run log staging for the CI artifact (issue #743).

The new-engine host runs the legacy agent behind :mod:`engine.agent_runner` in a
throwaway temp directory, so the ``console.log`` the bash job captured with
``| tee`` and the OpenHands SDK's on-disk ``app.log`` would both vanish when that
directory is removed. :class:`RunArtifactStager` owns one host-provided staging
directory, points the SDK file logger at it, and persists the captured console
output, so the workflow can upload both files as a single artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["CONSOLE_LOG_FILE", "RunArtifactStager", "RunArtifacts"]

# Matches the ``console.log`` the legacy ``| tee console.log`` wrote and the
# debug tooling reads back out of the artifact.
CONSOLE_LOG_FILE = "console.log"


# @stub-protected
@dataclass(frozen=True)
class RunArtifacts:
    """Immutable snapshot of a run's staged log artifacts."""

    staging_dir: Path
    console_log: str


# @stub-protected
class RunArtifactStager:
    """Owns the per-run log staging dir and stages logs for CI upload."""

    # @stub-protected
    def __init__(self, base_dir: Path) -> None:
        # Absolute is essential: the agent child runs with ``cwd`` set to a temp
        # workdir, so a relative ``LOG_DIR`` would land in (and die with) that
        # directory instead of the host's staging area (issue #743).
        self._base_dir = Path(base_dir).resolve()

    # @stub-protected
    def log_dir(self) -> Path:
        """Absolute directory the SDK logger writes ``app.log`` into."""
        return self._base_dir

    # @stub-protected
    def child_log_env(self) -> dict[str, str]:
        """Env overrides that make the agent child log into :meth:`log_dir`."""
        return {"LOG_TO_FILE": "true", "LOG_DIR": str(self.log_dir())}

    # @stub-protected
    def stage_console_log(self, console_log: str) -> None:
        """Persist the captured console output as ``console.log``."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        (self._base_dir / CONSOLE_LOG_FILE).write_text(console_log, encoding="utf-8")

    # @stub-protected
    def bundle(self, console_log: str) -> RunArtifacts:
        """Stage ``console_log`` and freeze the run's artifacts for the host."""
        self.stage_console_log(console_log)
        return RunArtifacts(staging_dir=self.log_dir(), console_log=console_log)
