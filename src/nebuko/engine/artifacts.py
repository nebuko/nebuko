"""Per-run log staging for the CI artifacts (issue #743, #779, #894).

The new-engine host runs the legacy agent behind :mod:`nebuko.engine.agent_runner` in a
throwaway temp directory, so the captured console output and the OpenHands SDK's
on-disk ``app.log`` would both vanish when that directory is removed.
:class:`RunArtifactStager` owns one host-provided staging directory, points the
SDK file logger at it, persists the captured console output, and renames the
SDK's ``app.log`` to the browser-viewable ``app.txt`` (issue #894). Each staged
file is then uploaded as its own *unzipped* artifact (issue #779), so a run's
logs can be fetched raw without any unzip step.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "APP_LOG_FILE",
    "ArtifactFile",
    "CONSOLE_LOG_FILE",
    "PERSISTENCE_DIR_NAME",
    "RunArtifactStager",
    "RunArtifacts",
    "SDK_APP_LOG_FILE",
]

# The captured console output, uploaded as the ``console.txt`` artifact. The
# ``.txt`` extension is what lets a browser render it inline instead of
# downloading it (issue #894).
CONSOLE_LOG_FILE = "console.txt"

# The browser-viewable name the SDK's on-disk log is uploaded under (issue #894).
APP_LOG_FILE = "app.txt"

# The OpenHands SDK file logger writes this name into ``LOG_DIR`` (the filename
# is hardcoded in the SDK); :meth:`RunArtifactStager.bundle` renames it to
# ``APP_LOG_FILE`` before the upload manifest is frozen.
SDK_APP_LOG_FILE = "app.log"

# Subdirectory of the staging dir the SDK conversation persists its event log
# into (issue #1120); uploaded with the rest of the run artifacts.
PERSISTENCE_DIR_NAME = "conversation"


# @stub-protected
@dataclass(frozen=True)
class ArtifactFile:
    """One uploadable, unzipped run artifact (artifact name = filename).

    ``actions/upload-artifact`` with ``archive: false`` accepts exactly one file
    and ignores ``name``: the artifact is named after the file itself. Hence
    ``name`` is both the manifest key and the staged file's basename (issue #779).
    """

    name: str
    path: Path


# @stub-protected
@dataclass(frozen=True)
class RunArtifacts:
    """Immutable snapshot of a run's staged log artifacts."""

    staging_dir: Path
    console_log: str
    files: tuple[ArtifactFile, ...] = ()


# @stub-protected
class RunArtifactStager:
    """Owns the per-run log staging dir and the explicit unzipped upload manifest."""

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
    def persistence_dir(self) -> Path:
        """Directory the SDK conversation persists its event log into.

        Kept inside the staging dir so the workflow's single
        ``actions/upload-artifact`` path carries the event JSON too (issue
        #1120); the agent child resolves it through ``OPENHANDS_PERSISTENCE_DIR``.
        """
        return self.log_dir() / PERSISTENCE_DIR_NAME

    # @stub-protected
    def child_persistence_env(self) -> dict[str, str]:
        """Env override pointing the agent child at :meth:`persistence_dir`."""
        return {"OPENHANDS_PERSISTENCE_DIR": str(self.persistence_dir())}

    # @stub-protected
    def stage_console_log(self, console_log: str) -> None:
        """Persist the captured console output as ``console.txt``."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        (self._base_dir / CONSOLE_LOG_FILE).write_text(console_log, encoding="utf-8")

    def _rename_sdk_app_log(self) -> None:
        """Give the SDK's ``app.log`` its browser-viewable ``.txt`` name (#894)."""
        source = self._base_dir / SDK_APP_LOG_FILE
        if not source.is_file():
            return
        source.replace(self._base_dir / APP_LOG_FILE)

    # @stub-protected
    def artifact_files(self) -> tuple[ArtifactFile, ...]:
        """The per-file unzipped upload manifest (``console.txt``, ``app.txt``).

        The workflow uploads each entry as its own unzipped artifact; the
        filename-derived artifact name is what the debug prompt fetches raw.

        ``app.txt`` is listed unconditionally, but the SDK file logger only
        writes its ``app.log`` when ``LOG_TO_FILE`` is on — the entry may point
        at a file that never appears. That is intentional: the upload steps run
        with ``if-no-files-found: ignore``, so a missing ``app.txt`` simply
        yields no artifact instead of failing the run.
        """
        return tuple(
            ArtifactFile(name=name, path=self._base_dir / name)
            for name in (CONSOLE_LOG_FILE, APP_LOG_FILE)
        )

    # @stub-protected
    def bundle(self, console_log: str) -> RunArtifacts:
        """Stage ``console_log`` and freeze the run's artifacts for the host."""
        self.stage_console_log(console_log)
        self._rename_sdk_app_log()
        return RunArtifacts(
            staging_dir=self.log_dir(),
            console_log=console_log,
            files=self.artifact_files(),
        )
