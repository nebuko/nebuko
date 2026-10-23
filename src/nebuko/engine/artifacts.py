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
    "EVENT_LOG_FILE",
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

# The OpenHands SDK conversation persistence lives under this subdirectory of
# ``LOG_DIR`` (see ``run_agent._resolve_persistence_dir``): one
# ``<conversation-id>/`` per conversation holding ``base_state.json`` and the
# per-event ``events/event-*.json`` files with the per-command timings
# (issue #1120). The SDK also writes dot-prefixed lock/length-marker files the
# bundle skips.
PERSISTENCE_DIR_NAME = "persistence"

# The browser-viewable bundle of the persisted event log (issue #1120).
EVENT_LOG_FILE = "events.jsonl"

# The SDK's per-conversation layout: ``<id>/events/event-<idx>-<event_id>.json``
# (``persistence_const.EVENTS_DIR`` / ``EVENT_FILE_PATTERN``), plus dot-prefixed
# lock/length-marker files the bundle skips.
_EVENTS_SUBDIR = "events"
_EVENT_GLOB = "event-*.json"


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

    def _stage_event_log(self) -> None:
        """Bundle the persisted conversation events into ``events.jsonl`` (#1120).

        The SDK writes one JSON file per event under
        ``persistence/<conversation-id>/events/``; the bundle concatenates them
        in index order so the per-command durations can be read straight from
        the artifact. Best-effort: a run without persistence (or an empty one)
        simply yields no bundle — the upload steps tolerate a missing file.
        """
        events_dir = self._base_dir / PERSISTENCE_DIR_NAME
        if not events_dir.is_dir():
            return
        event_files = sorted(
            path
            for path in events_dir.glob(f"*/{_EVENTS_SUBDIR}/{_EVENT_GLOB}")
            if not path.name.startswith(".")
        )
        if not event_files:
            return
        lines: list[str] = []
        for path in event_files:
            payload = path.read_text(encoding="utf-8").strip()
            if payload:
                lines.append(payload)
        if lines:
            (self._base_dir / EVENT_LOG_FILE).write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )

    # @stub-protected
    def artifact_files(self) -> tuple[ArtifactFile, ...]:
        """The per-file unzipped upload manifest (``console.txt``, ``app.txt``,
        ``events.jsonl``).

        The workflow uploads each entry as its own unzipped artifact; the
        filename-derived artifact name is what the debug prompt fetches raw.

        ``app.txt`` is listed unconditionally, but the SDK file logger only
        writes its ``app.log`` when ``LOG_TO_FILE`` is on — the entry may point
        at a file that never appears. That is intentional: the upload steps run
        with ``if-no-files-found: ignore``, so a missing ``app.txt`` simply
        yields no artifact instead of failing the run. ``events.jsonl`` is the
        same deal: it only appears when the conversation persisted events
        (issue #1120).
        """
        return tuple(
            ArtifactFile(name=name, path=self._base_dir / name)
            for name in (CONSOLE_LOG_FILE, APP_LOG_FILE, EVENT_LOG_FILE)
        )

    # @stub-protected
    def bundle(self, console_log: str) -> RunArtifacts:
        """Stage ``console_log`` and freeze the run's artifacts for the host."""
        self.stage_console_log(console_log)
        self._rename_sdk_app_log()
        self._stage_event_log()
        return RunArtifacts(
            staging_dir=self.log_dir(),
            console_log=console_log,
            files=self.artifact_files(),
        )
