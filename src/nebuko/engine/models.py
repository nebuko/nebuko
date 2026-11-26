"""Platform-free vocabulary for the orchestration engine (issue #590, Stage A).

Leaf module of the ``engine`` package: the only external type it names is the
reused ``workflow_state.workflow_state.WorkflowState`` — never re-declared here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from nebuko.engine.lib.workflow_state.sub_domains.turn_outcome.turn_outcome import TurnOutcome
    from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

__all__ = [
    "Trigger",
    "CommentEntry",
    "ExitCode",
    "RunOutcome",
    "SkipKind",
    "SkipOutcome",
    "DeterministicStep",
    "StepResult",
    "WorkflowCallFrame",
    "NebukoFeatures",
    "NebukoManifest",
    "circuit_breaker_update_notice",
]

log = logging.getLogger(__name__)


# @stub-protected
class NebukoFeatures(BaseModel):
    """Opt-in engine features from ``.nebuko/nebuko.yaml`` (issue #1347).

    Every flag defaults to ``False``: a feature ships disabled and a repo turns
    it on explicitly, so an upgraded engine never changes what an existing
    repository does.
    """

    model_config = ConfigDict(extra="forbid")

    #: Pre-push and pre-post secret scanning + redaction (issue #1347).
    secret_scan: bool = False


# @stub-protected
class NebukoManifest(BaseModel):
    """Parsed ``.nebuko/nebuko.yaml``: the caller's non-secret engine settings.

    Paths are resolved relative to the repo root. Every field is optional, so an
    absent or empty manifest keeps the fixed ``<kind>/`` subfolders and the
    built-in defaults. Secrets never live here — they stay in the workflow.
    Paths are resolved relative to the repo root. Both fields are optional, so an
    absent or empty manifest keeps the fixed ``<kind>/`` subfolders.

    ``merge_main`` (issue #1344) is the repo-wide default for script steps that
    run tests: CI tests GitHub's PR merge commit, so a step that tests the bare
    branch head reports failures ``main`` already fixed. Defaults to ``True``;
    a step opts out with ``NEBUKO_MERGE_MAIN=0`` (``git_diff`` does, since a
    merge would hide the change under review).

    Unknown keys are ignored (not rejected): a manifest written for a newer
    engine (e.g. a ``features:`` block) must not abort every run of an older one.
    """

    model_config = ConfigDict(extra="ignore")

    skills_dir: str | None = None
    default_workflow: str | None = None
    merge_main: bool = True
    features: NebukoFeatures = NebukoFeatures()
    agent_config: str | None = None
    circuit_breaker_threshold: int | None = Field(None, ge=1)
    history_comment_count: int | None = None
    pin_comment: bool | None = None
    base_branch: str | None = None
    #: Shell command that runs the repo's tests (``run_tests``); absent = none.
    test_command: str | None = None
    #: Minutes allowed for ``run_tests``; the workflow's static timeout otherwise.
    test_timeout_minutes: int | None = Field(None, ge=1)

    @field_validator("base_branch")
    @classmethod
    def _check_base_branch(cls, value: str | None) -> str | None:
        """Reject a value that is not a plain git branch name (it reaches ``sh -c``)."""
        if value is None or not value.strip():
            return None
        name = value.strip()
        if not _is_branch_name(name):
            raise ValueError(
                f"base_branch {name!r} is not a valid git branch name: use letters, "
                "digits and '._/-' (at most 200 characters), no '..', no leading '-'"
            )
        return name

    @field_validator("test_command")
    @classmethod
    def _blank_test_command_is_absent(cls, value: str | None) -> str | None:
        """A blank command counts as not configured, so nothing is exported."""
        if value is None or not value.strip():
            return None
        return value.strip()

    def env_defaults(self) -> dict[str, str]:
        """The env vars this manifest sets, as the workflow would export them."""
        values: dict[str, str] = {}
        if self.base_branch:
            values["NEBUKO_BASE_BRANCH"] = self.base_branch
        if self.test_command:
            values["NEBUKO_TEST_COMMAND"] = self.test_command
        if self.test_timeout_minutes is not None:
            values["NEBUKO_TEST_TIMEOUT_MINUTES"] = str(self.test_timeout_minutes)
        if self.agent_config is not None:
            values["AGENT_CONFIG_PATH"] = self.agent_config
        if self.circuit_breaker_threshold is not None:
            values["CIRCUIT_BREAKER_THRESHOLD"] = str(self.circuit_breaker_threshold)
        if self.history_comment_count is not None:
            values["HISTORY_COMMENT_COUNT"] = str(self.history_comment_count)
        if self.pin_comment is not None:
            values["PIN_COMMENT"] = _render_bool(self.pin_comment)
        return values

    def overlay_env(self, env: Mapping[str, str]) -> dict[str, str]:
        """``env`` with the manifest filling every unset (missing or blank) key.

        Precedence is explicit env/workflow input > manifest > built-in default,
        so an existing caller that passes an input keeps its value. ``PIN_COMMENT``
        is OR-ed instead: a boolean workflow input cannot express "unset", so a
        true value from either source wins.
        """
        merged = dict(env)
        for key, value in self.env_defaults().items():
            if key == "PIN_COMMENT":
                merged[key] = _render_bool(
                    _is_true(merged.get(key)) or _is_true(value)
                )
                continue
            if not (merged.get(key) or "").strip():
                merged[key] = value
        # A managed key that ends up blank is dropped rather than left empty: the
        # workflow now exports the inputs as ``''`` when unset, and readers using
        # ``os.environ.get(key, default)`` would take the empty string as a value
        # instead of applying their own default.
        for key in _MANAGED_ENV_KEYS:
            if key != "PIN_COMMENT" and not (merged.get(key) or "").strip():
                merged.pop(key, None)
        return merged


_MANAGED_ENV_KEYS = (
    "AGENT_CONFIG_PATH",
    "CIRCUIT_BREAKER_THRESHOLD",
    "HISTORY_COMMENT_COUNT",
    "NEBUKO_BASE_BRANCH",
    "NEBUKO_TEST_COMMAND",
    "NEBUKO_TEST_TIMEOUT_MINUTES",
    "PIN_COMMENT",
)

_MANIFEST_RELPATH = ".nebuko/nebuko.yaml"
_MANIFEST_NOTE_ENV = "NEBUKO_MANIFEST_NOTE"
_DEFAULT_BASE_BRANCH = "main"
_BRANCH_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def _is_branch_name(name: str) -> bool:
    """A conservative subset of ``git check-ref-format --branch`` rules.

    Only characters that are inert in a shell and in a ref are allowed, so the
    name can never change the meaning of a command it is interpolated into.
    """
    return (
        _BRANCH_NAME_RE.fullmatch(name) is not None
        and ".." not in name
        and "//" not in name
        and not name.endswith(("/", ".", ".lock"))
    )


def _render_bool(value: bool) -> str:
    return "true" if value else "false"


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes")


# @stub-protected

class PersonalManifest(BaseModel):
    """The personal repo's ``.nebuko/nebuko.yaml`` (issue #1479).

    Only ``agent_config`` is read. Every other key belongs to the project
    manifest (workflows, gates, guards, tests) and is ignored, with a warning
    naming it, so a copied project manifest cannot change the project's
    behaviour from the personal repo.
    """

    model_config = ConfigDict(extra="ignore")

    agent_config: str | None = None

    @classmethod
    def load(cls, path: Path) -> tuple["PersonalManifest", list[str]]:
        """Parse ``path``; return the manifest and the sorted names of ignored keys.

        An absent file yields an empty manifest and no ignored keys.
        """
        if not path.is_file():
            return cls(), []
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must contain a YAML mapping")
        ignored = sorted(str(key) for key in raw if key != "agent_config")
        return cls.model_validate({"agent_config": raw.get("agent_config")}), ignored


@dataclass(frozen=True)
class Trigger:
    """Normalized platform event that starts one engine run."""

    author: str
    body: str
    # "issue" | "issue_comment" | "workflow_dispatch" | "pull_request_review_comment"
    type: str
    number: int
    # raw event, for legacy resolvers that still read event JSON
    payload: Mapping[str, Any] = field(default_factory=dict)
    # body of the issue this event belongs to ("" when unavailable)
    issue_body: str = ""
    # ``owner/name`` the dispatch targets (``client_payload.org_repo``, "" otherwise)
    org_repo: str = ""
    # ``client_payload.step`` / ``client_payload.agent_config`` (ADR 0017); None when absent
    step: str | None = None
    agent_config: str | None = None


# @stub-protected
@dataclass(frozen=True)
class CommentEntry:
    """One entry of the platform comment history."""

    author: str
    body: str
    created_at: str  # ISO-8601 UTC, as emitted by the GitHub API
    # Platform comment id (issue #756): lets a step's posted comment be located
    # by name for step-output resolution. ``None`` for a hand-built entry.
    comment_id: str | None = None


# @stub-protected
class ExitCode(IntEnum):
    """Run-outcome codes; the host process status is derived (issue #1057).

    These distinguish *what* the run did, not whether the host job failed:
    only :attr:`ERROR` maps to a non-zero process status (see
    :meth:`RunOutcome.process_exit_code`). A hold or a terminal skip is a
    deliberate stop that announces itself on the thread, so it must not fail
    the CI job.
    """

    ADVANCE = 0  # next step proceeds
    ERROR = 1  # uncaught failure / contract violation
    HOLD = 10  # human gate: stop, human resumes
    TERMINAL = 20  # terminal journal marker written


# @stub-protected
@dataclass(frozen=True)
class RunOutcome:
    """Everything one engine run produced, including the turn outcome."""

    exit_code: ExitCode
    turn_outcome: "TurnOutcome"
    prompt: str | None
    posted_body: str | None
    state: WorkflowState | None
    phases: tuple[str, ...]  # ordered "[phase:*]" markers actually emitted
    # Per-phase wall-clock durations in milliseconds (issue #1375), keyed by
    # phase name. Empty for a run that never entered a timed phase.
    timings: dict[str, float] = field(default_factory=dict)
    # The guard's skip verdict when no agent ran (issue #1457): a benign skip
    # exits ADVANCE, so the single-job driver needs the kind to tell "workflow
    # ended" from "step completed".
    skip_kind: "SkipKind | None" = None

    # @stub-protected
    def process_exit_code(self) -> int:
        """The host process status: ``1`` only when the engine itself broke.

        ``HOLD`` / ``TERMINAL`` are deliberate stops that post their reason on
        the thread (issue #1057); exiting non-zero for them marked the CI job
        red and notified the maintainer for something a human must read, not
        fix in the log.
        """
        return 1 if self.exit_code is ExitCode.ERROR else 0


# @stub-protected
class SkipKind(Enum):
    """Why a run stopped without dispatching an agent."""

    NO_NEXT_AGENT = "no_next_agent"  # benign — nothing left to dispatch
    TERMINAL = "terminal"  # benign — terminal marker / workflow exhausted
    CONFIG_ERROR = "config_error"  # failure — workflow not found / bad config
    # Benign — an armed root gate is holding the workflow (issue #1003): the
    # run stops deliberately and the human releases it with a gate command.
    # Without this kind the resolver's gate skip defaulted to CONFIG_ERROR and
    # exited 20 instead of the hold exit 10.
    HUMAN_GATE_HOLD = "human_gate_hold"
    # Benign — an ``approve|reject gate`` command found nothing it may decide
    # (issue #1012): the command changed nothing, so the run is an inert no-op
    # that exits 0 and posts the skip reason. Without this kind the rejection
    # defaulted to CONFIG_ERROR and exited 20.
    GATE_NOOP = "gate_noop"


# @stub-protected
@dataclass(frozen=True)
class SkipOutcome:
    """The typed verdict for a run that stops without dispatching an agent.

    Single source of truth for *why* the guards phase skipped the agent and the
    exit code that follows: a benign verdict (``NO_NEXT_AGENT`` / ``TERMINAL`` /
    ``HUMAN_GATE_HOLD``) is a deliberate no-op, while a ``CONFIG_ERROR`` keeps
    the terminal code that tells machine callers the run failed. Neither fails
    the host process (``RunOutcome.process_exit_code``, issue #1057). Resolver
    verdicts may omit the kind — an unclassified reason defaults to
    ``CONFIG_ERROR`` so behaviour is never softened by accident.
    """

    kind: SkipKind
    reason: str

    # @stub-protected
    @property
    def is_benign(self) -> bool:
        return self.kind in (
            SkipKind.NO_NEXT_AGENT,
            SkipKind.TERMINAL,
            SkipKind.HUMAN_GATE_HOLD,
            SkipKind.GATE_NOOP,
        )

    # @stub-protected
    @property
    def exit_code(self) -> ExitCode:
        if self.kind is SkipKind.HUMAN_GATE_HOLD:
            # An armed gate is a deliberate stop, not a workflow end: report
            # ``HOLD`` (10) and let the engine announce the reason on the
            # thread — the human is the only one who can resume it (#1003).
            return ExitCode.HOLD
        return ExitCode.ADVANCE if self.is_benign else ExitCode.TERMINAL

    @classmethod
    def from_verdict(cls, verdict: Mapping[str, Any]) -> "SkipOutcome":
        """Build the verdict from a guards-phase ``SKIP_AGENT`` dict.

        Unknown/missing ``SKIP_KIND`` falls back to ``CONFIG_ERROR`` (the
        fail-safe), and the human-readable ``REASON`` is preserved verbatim.
        """
        raw_kind = str(verdict.get("SKIP_KIND") or "").strip()
        try:
            kind = SkipKind(raw_kind) if raw_kind else SkipKind.CONFIG_ERROR
        except ValueError:
            kind = SkipKind.CONFIG_ERROR
        return cls(kind=kind, reason=str(verdict.get("REASON") or ""))


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _as_text(value: bytes | str | None) -> str:
    """Normalize a possibly-bytes captured stream to ``str``."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


# @stub-protected
@dataclass(frozen=True)
class StepResult:
    """Immutable record of one executed deterministic step."""

    step_kind: str
    ok: bool
    detail: str  # human-readable outcome (e.g. "deleted 42 comments on issue 590")
    affected: int  # comments deleted / script exit code
    output: str = ""  # captured stdout/stderr (run_script; issue #885)
    duration_seconds: float = 0.0  # wall-clock execution time (run_script)
    # The commit the command ran on (issue #1327): the session branch checked
    # out for evidence, or "" when it ran on the workflow ref.
    branch: str = ""
    head_sha: str = ""
    # The tree the command actually saw (issue #1344): "merge of <head> into
    # <base>" when the base was merged in, "branch head <sha>" otherwise.
    tested: str = ""


# @stub-protected
@dataclass(frozen=True)
class DeterministicStep:
    """An agent-free step the resolver hands to the engine to execute directly."""

    kind: Literal["delete_comments", "run_script", "update_circuit_breaker"]
    issue: str = ""  # delete_comments: target issue
    from_comment: str = ""  # delete_comments: --from boundary ("" = all)
    script_path: str = ""  # run_script: the `command` (executable or repo-local script)
    script_args: str = "[]"  # run_script: JSON array of argv entries (issue #974)
    script_working_dir: str = ""  # run_script: repo-relative cwd ("" = workspace root)
    script_env: str = "{}"  # run_script: JSON object merged over the engine env
    script_checkout: str = "session"  # run_script: "session" | "workflow"
    timeout_minutes: int | None = None
    step_name: str = ""  # run_script: workflow step name (from WORKFLOW_STEP)
    circuit_breaker_value: int = 0  # update_circuit_breaker: new per-issue limit

    # @stub-protected
    @classmethod
    def from_resolved(cls, resolved: Mapping[str, str]) -> DeterministicStep | None:
        """Typed view over the resolver env contract; None when no step is present.

        ``DELETE_COMMENTS`` wins over ``RUN_SCRIPT`` on a pathological overlap;
        a pure ``SKIP_AGENT=true`` carrying neither flag resolves to ``None``,
        which keeps the guards' skip verdict (``ExitCode.TERMINAL``) intact.
        """
        if not isinstance(resolved, Mapping):
            return None
        if _is_true(resolved.get("DELETE_COMMENTS")):
            return cls(
                kind="delete_comments",
                issue=str(resolved.get("DELETE_COMMENTS_ISSUE") or ""),
                from_comment=str(resolved.get("DELETE_COMMENTS_FROM") or ""),
            )
        if _is_true(resolved.get("RUN_SCRIPT")):
            timeout = str(resolved.get("SCRIPT_TIMEOUT_MINUTES") or "").strip()
            return cls(
                kind="run_script",
                script_path=str(resolved.get("SCRIPT_PATH") or ""),
                script_args=str(resolved.get("SCRIPT_ARGS") or "[]"),
                script_working_dir=str(resolved.get("SCRIPT_WORKING_DIR") or ""),
                script_env=str(resolved.get("SCRIPT_ENV") or "{}"),
                script_checkout=str(resolved.get("SCRIPT_CHECKOUT") or "session"),
                timeout_minutes=int(timeout) if timeout.isdigit() else None,
                step_name=str(resolved.get("WORKFLOW_STEP") or ""),
            )
        if _is_true(resolved.get("UPDATE_CIRCUIT_BREAKER")):
            raw = str(resolved.get("UPDATE_CIRCUIT_BREAKER_VALUE") or "").strip()
            return cls(
                kind="update_circuit_breaker",
                circuit_breaker_value=int(raw) if raw.isascii() and raw.isdigit() else 0,
            )
        return None

    # @stub-protected
    def env_overrides(self) -> dict[str, str]:
        """The step's declared ``env`` block, decoded (issue #1344).

        The engine reads it for step-scoped switches such as
        ``NEBUKO_MERGE_MAIN``; the child process gets the same values through
        :func:`_script_process_context`.
        """
        return _script_env(self.script_env)

    # @stub-protected
    def execute(self, adapter: Any, checkout: Any = None) -> StepResult:
        """Run the step through the adapter/runner; never raises for step failure.

        ``checkout`` is the already-resolved evidence checkout (issue #1327):
        the caller resolves it first so an unreachable session branch fails the
        run *before* the command executes. Only its ``branch``/``head_sha`` are
        read here — the checkout itself is already in place on disk.
        """
        try:
            if self.kind == "delete_comments":
                deleted = int(
                    adapter.delete_issue_comments(self.issue, self.from_comment)
                )
                return StepResult(
                    step_kind=self.kind,
                    ok=True,
                    detail=f"deleted {deleted} comments on issue {self.issue}",
                    affected=deleted,
                )
            if self.kind == "update_circuit_breaker":
                return self._execute_circuit_breaker_update(adapter)
            return self._execute_script(session_workspace(), checkout)
        except Exception as exc:  # noqa: BLE001 - a step failure is data, not a crash
            return StepResult(step_kind=self.kind, ok=False, detail=str(exc), affected=0)

    def _execute_circuit_breaker_update(self, adapter: Any) -> StepResult:
        """Persist the new per-issue breaker limit and post a confirmation.

        The override must survive into the next run, and on GitHub the only
        cross-run carrier is the comment history (the disk hand-off is
        per-runner). So the step loads the previous state, applies the limit,
        and posts it wrapped around a human confirmation — the guard of the
        next run reads it back via ``extract_state``.
        """

        from nebuko.engine.lib.circuit_breaker import count_bot_markers
        from nebuko.engine.lib.workflow_state.features.comment.to_git_comment import (
            wrap_comment_with_state,
        )
        from nebuko.engine.lib.workflow_state.workflow_state import WorkflowState

        prev = adapter.load_state() or WorkflowState()
        new_state = prev.model_copy(
            update={
                "circuit_breaker": prev.circuit_breaker.with_limit(
                    self.circuit_breaker_value
                )
            }
        )
        history = adapter.load_history() or []
        agent_calls = max(0, count_bot_markers(list(history)) // 2)
        detail = circuit_breaker_update_notice(self.circuit_breaker_value, agent_calls)
        adapter.post(wrap_comment_with_state(new_state, detail))
        adapter.save_state(new_state)
        return StepResult(
            step_kind=self.kind,
            ok=True,
            detail=detail,
            affected=self.circuit_breaker_value,
        )

    def _timeout_minutes_for(self, manifest_env: Mapping[str, str] | None) -> int | None:
        """The step's timeout; the ``run_tests`` step takes the manifest's override."""
        if self.step_name == TEST_STEP_NAME and manifest_env:
            raw = (manifest_env.get("NEBUKO_TEST_TIMEOUT_MINUTES") or "").strip()
            if raw.isdigit() and int(raw) >= 1:
                return int(raw)
        return self.timeout_minutes

    def _execute_script(
        self, workspace: Path | None = None, checkout: Any = None
    ) -> StepResult:
        """Run the script, capturing combined output and timing it.

        ``argv`` is ``[command, *args]`` — no shell — with the args decoded from
        the resolver's JSON array, so an entry containing spaces or quotes
        reaches the child verbatim (issue #974). The process runs in the
        session workspace (or ``working_dir`` under it) with the engine
        environment overlaid by the step's ``env``; ``HookRunner`` uses the
        same anchor.

        A timeout is data, not a crash (issue #885): it returns ``ok=False`` with
        the partial output and no exit code, mirroring a non-zero exit. Output is
        captured merged (stderr folded into stdout) so the posted report shows
        the interleaved stream a terminal would.
        """
        base_env = _manifest_overlay(workspace)
        minutes = self._timeout_minutes_for(base_env)
        timeout = minutes * 60 if minutes else None
        cwd, env = _script_process_context(
            workspace, self.script_working_dir, self.script_env, base_env
        )
        branch = str(getattr(checkout, "branch", "") or "")
        head_sha = str(getattr(checkout, "head_sha", "") or "")
        tested = str(getattr(checkout, "tested", "") or "")
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [self.script_path, *_script_argv(self.script_args)],
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return StepResult(
                step_kind=self.kind,
                ok=False,
                detail=f"script timed out after {minutes}m",
                affected=0,
                output=_as_text(exc.stdout),
                duration_seconds=time.monotonic() - started,
                branch=branch,
                head_sha=head_sha,
                tested=tested,
            )
        return StepResult(
            step_kind=self.kind,
            ok=completed.returncode == 0,
            detail=f"script exited with code {completed.returncode}",
            affected=completed.returncode,
            output=completed.stdout or "",
            duration_seconds=time.monotonic() - started,
            branch=branch,
            head_sha=head_sha,
            tested=tested,
        )


def session_workspace() -> Path | None:
    """The session checkout a script step runs in, or ``None`` when unknown.

    Script steps are leaf vocabulary and must stay free of workspace policy, so
    the directory is read here from the same env contract the platform sets for
    the engine (``GITHUB_WORKSPACE`` in CI, ``NEBUKO_WORKSPACE`` for a local
    host) rather than injected through the model. The evidence checkout
    (issue #1327) resolves against the same directory, so the commit it checks
    out is the one the command then sees.
    """
    for key in ("NEBUKO_WORKSPACE", "GITHUB_WORKSPACE"):
        value = os.environ.get(key)
        if value:
            return Path(value)
    return None


def _script_argv(encoded: str) -> list[str]:
    """Decode the resolver's JSON argv array; malformed input means no args.

    Degradation is logged, never silent: a legacy shell-string ``SCRIPT_ARGS``
    (the pre-#974 ``" ".join`` contract) is not valid JSON, and losing every
    argument without a trace would be indistinguishable from a step that
    genuinely declared none.
    """
    try:
        decoded = json.loads(encoded or "[]")
    except ValueError:
        log.warning("SCRIPT_ARGS is not valid JSON; running with no arguments")
        return []
    if not isinstance(decoded, list):
        log.warning("SCRIPT_ARGS is not a JSON array; running with no arguments")
        return []
    return [str(arg) for arg in decoded]


#: The workflow step whose ``timeout_minutes`` the manifest's test override replaces.
TEST_STEP_NAME = "run_tests"


@dataclass(frozen=True)
class BaseSnapshot:
    """The base branch as the engine resolved it once, before the run's first step (issue #1475).

    Script steps take the base name, its commit and the manifest committed there
    from this value, never from the checkout's refs or working tree. In the
    single-job loop an agent step shares the checkout and could move
    ``origin/HEAD`` or ``origin/<base>`` or add a local ``origin/<name>`` ref.
    """

    branch: str
    sha: str  # "" when origin/<branch> does not resolve
    manifest: NebukoManifest
    note: str | None = None


_BASE_SNAPSHOT: BaseSnapshot | None = None


@contextmanager
def base_snapshot_scope() -> Iterator[None]:
    """Hold one base snapshot for the run; the single-job loop wraps all its steps in it.

    Outside a scope each script step resolves its own snapshot, which is the same
    as one per run in a fresh runner per step.
    """
    global _BASE_SNAPSHOT
    previous = _BASE_SNAPSHOT
    workspace = session_workspace()
    _BASE_SNAPSHOT = resolve_base_snapshot(workspace) if workspace is not None else None
    try:
        yield
    finally:
        _BASE_SNAPSHOT = previous


def resolve_base_snapshot(workspace: Path) -> BaseSnapshot:
    """The base name, its commit and the manifest committed at that commit."""
    branch = _trusted_base_branch(workspace)
    sha = _git_stdout(workspace, "rev-parse", "--verify", "--quiet", f"origin/{branch}^{{commit}}")
    manifest, note = _committed_manifest(workspace, branch, sha)
    return BaseSnapshot(branch=branch, sha=sha, manifest=manifest, note=note)


def _manifest_overlay(workspace: Path | None) -> dict[str, str] | None:
    """The engine env with the base snapshot filling unset keys (issue #1475).

    ``None`` without a workspace, where the child keeps the ambient environment.
    ``NEBUKO_BASE_BRANCH`` and ``NEBUKO_BASE_SHA`` are set after the overlay, so
    no ambient or step value can repoint them. ``NEBUKO_MANIFEST_NOTE`` says why
    no committed manifest was read, so ``run_tests`` can report it.
    """
    if workspace is None:
        return None
    snapshot = _BASE_SNAPSHOT or resolve_base_snapshot(workspace)
    env = snapshot.manifest.overlay_env(os.environ)
    env["NEBUKO_BASE_BRANCH"] = snapshot.branch
    env["NEBUKO_BASE_SHA"] = snapshot.sha
    if snapshot.note:
        env[_MANIFEST_NOTE_ENV] = snapshot.note
    return env


def _git_stdout(workspace: Path, *args: str) -> str:
    """Stripped stdout of a read-only git query; ``""`` when it fails."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _committed_manifest(
    workspace: Path, base: str, sha: str
) -> tuple[NebukoManifest, str | None]:
    """The manifest committed at ``sha``, the base branch's commit, never the working-tree copy.

    The session checkout is the branch under review and the dev agent can edit
    it, so nothing read from it may pick the ref or change what ``run_tests``
    runs. Returns the manifest and a note when the base commit did not resolve,
    the file could not be read from it, or it is invalid. An absent file on a
    readable base is a repo without a manifest: no note.
    """
    import yaml

    ref = f"origin/{base}"
    unreadable = f"committed {_MANIFEST_RELPATH} could not be read from {ref}; no test_command was checked"
    if not sha:
        log.warning("could not read %s from %s", _MANIFEST_RELPATH, ref)
        return NebukoManifest(), unreadable
    try:
        listed = subprocess.run(
            ["git", "-C", str(workspace), "ls-tree", "--name-only", sha, "--", _MANIFEST_RELPATH],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if listed.returncode != 0:
            log.warning("could not read %s from %s", _MANIFEST_RELPATH, ref)
            return NebukoManifest(), unreadable
        if not listed.stdout.strip():
            return NebukoManifest(), None
        completed = subprocess.run(
            ["git", "-C", str(workspace), "show", f"{sha}:{_MANIFEST_RELPATH}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        log.warning("could not read %s from %s", _MANIFEST_RELPATH, ref)
        return NebukoManifest(), unreadable
    if completed.returncode != 0:
        log.warning("could not read %s from %s", _MANIFEST_RELPATH, ref)
        return NebukoManifest(), unreadable
    try:
        raw = yaml.safe_load(completed.stdout) or {}
        if not isinstance(raw, dict):
            raise ValueError("manifest must be a mapping")
        return NebukoManifest.model_validate(raw), None
    except (ValueError, yaml.YAMLError) as exc:
        log.warning("committed %s on %s is invalid: %s", _MANIFEST_RELPATH, ref, exc)
        invalid = f"committed {_MANIFEST_RELPATH} on {ref} is invalid; no test_command was checked"
        return NebukoManifest(), invalid


def _trusted_base_branch(workspace: Path) -> str:
    """The base branch that locates the committed manifest; never the working tree.

    The engine's own ``NEBUKO_BASE_BRANCH`` first, then the remote's default
    branch (``origin/HEAD``), then ``main``. The working-tree manifest is not
    read: a branch that names another base could otherwise pick its own ref.
    """
    engine_base = (os.environ.get("NEBUKO_BASE_BRANCH") or "").strip()
    if engine_base and _is_branch_name(engine_base):
        return engine_base
    if engine_base:
        log.warning("NEBUKO_BASE_BRANCH %r is not a valid branch name; ignored", engine_base)
    name = _git_stdout(workspace, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if name.startswith("origin/") and _is_branch_name(name[len("origin/"):]):
        return name[len("origin/"):]
    return _DEFAULT_BASE_BRANCH


def _script_process_context(
    workspace: Path | None,
    working_dir: str,
    encoded_env: str,
    base_env: Mapping[str, str] | None = None,
) -> tuple[str | None, dict[str, str] | None]:
    """The ``(cwd, env)`` for a script child (issue #974).

    The step's ``working_dir`` is anchored at the session ``workspace`` and the
    environment is ``base_env`` (the engine's own, overlaid by the manifest;
    ``os.environ`` when not given) with the step's ``env`` on top, so explicit
    keys win. Without a workspace — a host with no session directory — both
    stay ``None``, so the child inherits the ambient process state as before.
    """
    if workspace is None:
        return None, None
    cwd = Path(workspace) / working_dir if working_dir else Path(workspace)
    engine_env = os.environ if base_env is None else base_env
    env = {**engine_env, **_script_env(encoded_env)}
    return str(cwd), env


def _script_env(encoded: str) -> dict[str, str]:
    """Decode the resolver's JSON env object; malformed input means no overlay.

    Like :func:`_script_argv`, a malformed value degrades with a log line so a
    dropped ``env`` block is visible rather than silently ignored.
    """
    try:
        decoded = json.loads(encoded or "{}")
    except ValueError:
        log.warning("SCRIPT_ENV is not valid JSON; running with no env overlay")
        return {}
    if not isinstance(decoded, dict):
        log.warning("SCRIPT_ENV is not a JSON object; running with no env overlay")
        return {}
    return {str(k): str(v) for k, v in decoded.items()}


def circuit_breaker_update_notice(value: int, agent_calls: int) -> str:
    """Human-facing confirmation for a per-issue circuit-breaker update.

    Notes explicitly when the new limit does not clear the current usage, so a
    human does not mistake a no-op for an unlock.
    """
    if value <= agent_calls:
        return (
            f"⚙️ Circuit-breaker limit set to **{value}** for this issue.\n\n"
            f"⚠️ Current usage is {agent_calls} agent calls, so the breaker is "
            f"still tripped; raise the limit above {agent_calls} to unlock."
        )
    return (
        f"⚙️ Circuit-breaker limit set to **{value}** for this issue.\n\n"
        f"Current usage is {agent_calls} agent calls; the issue is unlocked."
    )


# @stub-protected
@dataclass(frozen=True)
class WorkflowCallFrame:
    """One live sub-workflow invocation — the function call frame (issue #813).

    Pushed when a ``workflow`` call step enters a child workflow and popped at
    the child's terminal step. Frames make workflows behave as typed functions:
    ``bindings`` are the frozen call-site parameter values, ``materialized``
    holds the values resolved just-in-time at the consuming step (an ``exec``
    binding runs there, not at the call boundary, so fresh context is injected
    immediately before the model runs), and ``outputs`` carries only the
    callee's declared return values back to the caller.

    Bindings are duck-typed: each one exposes ``resolve(scope) -> str``, so
    this leaf module never imports the config sub-domain.
    """

    callee: str
    repo_ref: str = ""
    call_step: str = ""
    bindings: Mapping[str, Any] = field(default_factory=dict)
    materialized: Mapping[str, str] = field(default_factory=dict)
    outputs: Mapping[str, str] = field(default_factory=dict)

    # @stub-protected
    def materialize(self, name: str, scope: Mapping[str, str]) -> "WorkflowCallFrame":
        """Return a new frame with ``name``'s binding resolved against ``scope``.

        A frozen frame is never mutated; the caller threads the returned frame.
        Raises whatever the binding's ``resolve`` raises (exec failure or an
        unresolved reference) so the caller can apply the ``required`` policy.
        """
        binding = self.bindings[name]
        value = binding.resolve(dict(scope))
        return replace(self, materialized={**self.materialized, name: value})

    # @stub-protected
    def pop(self) -> dict[str, str]:
        """Return the callee's declared outputs — and nothing else."""
        return dict(self.outputs)
