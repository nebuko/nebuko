"""The orchestration engine: the 15-step bash ``run-task`` job, typed once.

Nine phases, in order, each emitting one ``[phase:<name>]`` log marker:
``normalize -> resolve_config -> guards -> prompt -> agent -> validate ->
publish -> state -> route``. The engine orchestrates legacy outputs by
reference to their existing types and contains no platform-mode branching.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from engine.models import (
    ExitCode,
    RoutingDecision,
    RunOutcome,
    Trigger,
    to_routing_decision,
)
from engine.paths import ensure_import_path

if TYPE_CHECKING:  # pragma: no cover - import bootstrap only
    from pathlib import Path

    from adapters.base import PlatformAdapter
    from config import AgentStartNotice
    from engine.artifacts import RunArtifactStager
    from engine.models import CommentEntry
    from workflow_state.workflow_state import WorkflowState

__all__ = ["PHASES", "Engine", "main"]

log = logging.getLogger(__name__)

# ``resolved`` is the legacy ``resolve_config`` dict passed verbatim between
# phases; the engine carries the trigger alongside it (under this namespaced
# key) so ``build_prompt`` can render the user context without re-reading it.
_TRIGGER_KEY = "ENGINE_TRIGGER"

# Key into ``Engine._run_resolved`` for the mapping the ``resolve_config`` phase
# produced. It is the single hand-off source the agent child and the routing
# phases read, so the resolver's output is never recomputed mid-run.
_RUN_RESOLVED_KEY = "resolved"

_USER_REQUEST_HEADER = "--- USER REQUEST / GITHUB CONTEXT ---"
_STEP_HEADER = "## Current step prompt"

_EXIT_CODE_BY_STATUS = {
    "advance": ExitCode.ADVANCE,
    "loop": ExitCode.ADVANCE,
    "hold": ExitCode.HOLD,
}


# @stub-protected
PHASES: tuple[str, ...] = (
    "normalize",
    "resolve_config",
    "guards",
    "prompt",
    "agent",
    "validate",
    "publish",
    "state",
    "route",
)


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _resolver_skip_verdict(resolved: Any) -> dict[str, str] | None:
    """The resolver's own skip verdict, which outranks both runtime guards.

    The bash exits as soon as ``SKIP_AGENT=true`` comes back from
    ``resolve_config`` (issue #458: a routine skip must never be flipped back
    to a run), and only evaluates the guards/breaker on a resolved task. Returns
    ``None`` when the resolver selected an agent, so the caller falls through to
    the runtime guards.
    """
    if isinstance(resolved, Mapping) and _is_true(resolved.get("SKIP_AGENT")):
        return {
            "SKIP_AGENT": "true",
            "REASON": str(resolved.get("REASON") or "resolver skipped the agent"),
        }
    return None


def _exit_code_for(routing: RoutingDecision) -> ExitCode:
    return _EXIT_CODE_BY_STATUS.get(routing.status, ExitCode.ADVANCE)


# The bash guard reads the caller with ``sed -n 's/.*<!-- bot-agent-\([a-z]*\)
# -->.*/\1/p'``: per line, last match wins, and only lowercase-letters names
# match (``issue-reviewer`` therefore never counts as a caller).
_CALLER_RE = re.compile(r"<!-- bot-agent-([a-z]*) -->")


def _caller_agent(trigger_body: str) -> str:
    """The caller marker the legacy guard-comparison reads from the body."""
    found = [
        matches[-1]
        for line in (trigger_body or "").splitlines()
        if (matches := _CALLER_RE.findall(line))
    ]
    return "\n".join(found)


def _answer_of(agent_result: Any) -> str:
    if isinstance(agent_result, str):
        return agent_result
    if isinstance(agent_result, Mapping):
        for key in ("answer", "latest_answer", "final_message", "text"):
            value = agent_result.get(key)
            if value:
                return str(value)
    return ""


def _final_message_of(agent_result: Any) -> str:
    if isinstance(agent_result, Mapping):
        value = agent_result.get("final_message") or agent_result.get("text")
        if value:
            return str(value)
    return ""


def resolved_env_overlay(resolved: Any) -> dict[str, str]:
    """The resolver's output as the legacy job exported it (``$GITHUB_ENV``).

    The bash ``run-task`` job copied the resolver's ``KEY=value`` output into
    ``$GITHUB_ENV`` after ``resolve_config``, and the agent step read it back
    from the environment — ``resolve_llm_config`` keys off ``ROLE_NAME`` to find
    the agent's model/provider in ``agent-config.yaml`` (issue #717). The engine
    resolves that config in-process, so it must reconstruct the same hand-off
    for the legacy child.

    ``Engine.resolve`` annotates the mapping with the engine-only
    ``ENGINE_TRIGGER`` object. Every other value is stringified the way the job
    interpolated it into ``$GITHUB_ENV`` — dropping non-strings silently would
    drop a real setting (issue #717 round 2).
    """
    if not isinstance(resolved, Mapping):
        return {}
    return {
        str(key): str(value)
        for key, value in resolved.items()
        if key != _TRIGGER_KEY
    }


def _anchor_config_path(env: dict[str, str], repo_root: "Path") -> dict[str, str]:
    """Make ``AGENT_CONFIG_PATH`` absolute for the temp-cwd legacy child.

    The legacy job ran the agent with the checkout as cwd, so a relative
    ``AGENT_CONFIG_PATH`` resolved; the engine runs the child in a temp workdir,
    where the same value misses the file and the agent boots with no model
    (issue #717). The resolver's ``PROMPT_LOCATION`` is already absolute for the
    same reason.
    """
    raw = env.get("AGENT_CONFIG_PATH")
    if raw and not os.path.isabs(raw):
        env["AGENT_CONFIG_PATH"] = os.path.join(str(repo_root), raw)
    return env


def _backfill_workflow_identity(
    env: dict[str, str], state: WorkflowState | None
) -> dict[str, str]:
    """Restore the workflow identity the resolver drops on a bare human mention.

    ``@@agent``/``/workflow`` mentions route through the resolver's human path,
    which does not carry the active workflow/step/position (those live only in
    the trigger's state block). ``build_state`` then records a history entry with
    an empty ``step`` and no ``position`` even though it keeps the top-level leaf
    via its own fallback (issue #739). Filling the gap from the state the run
    already resolved keeps the appended entry addressable.
    """
    if state is None:
        return env
    if not env.get("WORKFLOW_NAME") and state.workflow:
        env["WORKFLOW_NAME"] = state.workflow
    if not env.get("WORKFLOW_STEP") and state.step:
        env["WORKFLOW_STEP"] = state.step
    if not env.get("WORKFLOW_POSITION") and state.position:
        ensure_import_path()
        from config import WorkflowPosition

        position = WorkflowPosition(refs=list(state.position))
        env["WORKFLOW_POSITION"] = json.dumps(position.to_state_dict())
    return env


# @stub-protected
@dataclass(frozen=True)
class Engine:
    """Platform-free orchestration driver over a :class:`PlatformAdapter`."""

    adapter: PlatformAdapter
    repo_root: Path
    env: Mapping[str, str] = field(default_factory=dict)
    history_limit: int | None = None
    # Per-run memo of the resolver output. ``guard`` needs the same resolved
    # config the ``resolve_config`` phase already computed (it reads ``TASK``
    # and the workflow position back out of it), and the legacy job resolves
    # exactly once per run (issue #590 round-4, finding 3). Engine-internal, so
    # it is excluded from ``__init__``/equality/repr.
    _resolve_cache: dict[tuple[str, int], Any] = field(
        default_factory=dict, init=False, compare=False, repr=False
    )
    # The resolver mapping ``run`` computed in the ``resolve_config`` phase, kept
    # for the rest of the run (issue #717 round 2). ``invoke_agent`` and
    # ``_role_of`` read this single hand-off instead of re-resolving, so routing
    # can never diverge from the config the child actually runs with.
    # Engine-internal, so it is excluded from ``__init__``/equality/repr.
    _run_resolved: dict[str, Any] = field(
        default_factory=dict, init=False, compare=False, repr=False
    )
    # The previous state the ``agent`` phase seeded for the child (issue #739),
    # held for ``_agent_env`` to restore the workflow identity the resolver drops
    # on a bare human mention. ``None`` until ``_seed_previous_state`` has run.
    # Engine-internal, so it is excluded from ``__init__``/equality/repr.
    _seeded_prev_state: dict[str, Any] = field(
        default_factory=dict, init=False, compare=False, repr=False
    )

    # @stub-protected
    def run(self) -> RunOutcome:
        """End-to-end pipeline; always returns an outcome."""
        phases: list[str] = []
        prompt: str | None = None
        posted_body: str | None = None
        state: WorkflowState | None = None
        try:
            self._enter(phases, "normalize")
            trigger = self.normalize()

            self._enter(phases, "resolve_config")
            resolved = self.resolve(trigger)
            self._run_resolved[_RUN_RESOLVED_KEY] = resolved
            history: Sequence[CommentEntry] = list(
                self.adapter.load_history(limit=self.history_limit)
            )

            self._enter(phases, "guards")
            # The bash short-circuits on the resolver's SKIP_AGENT before the
            # loop guard and breaker ever run (issue #458); the circuit breaker
            # is gated on ``SKIP_AGENT == 'false'`` in the job too.
            verdict = _resolver_skip_verdict(resolved) or self.guard(trigger, history)
            if _is_true(verdict.get("SKIP_AGENT")):
                reason = str(verdict.get("REASON") or "agent skipped")
                log.info("[phase:guards] skipping agent — %s", reason)
                return RunOutcome(
                    exit_code=ExitCode.TERMINAL,
                    routing=RoutingDecision(status="hold", reason=reason),
                    prompt=None,
                    posted_body=None,
                    state=None,
                    phases=tuple(phases),
                )

            # The state comment is framed onto the *answer* (issue #736): the
            # legacy "Add bot marker and workflow state to comment" step
            # prepended the header there, and the run's authoritative state is
            # the agent's own output, which is not known before it runs.

            # Seed the pre-agent hand-off (issue #739). The legacy
            # ``query_context`` step wrote the resolved state to
            # ``$RUNNER_TEMP`` before the agent ran; the refactor dropped it,
            # so on a fresh runner the child starts from scratch and resets
            # ``history`` to a single entry (issue #726).
            self._seed_previous_state(history)

            self._enter(phases, "prompt")
            prompt = self.build_prompt(resolved, history)

            self._enter(phases, "agent")
            self.notify_agent_started(self._agent_start_notice(trigger))
            failure_reason: str | None = None
            try:
                agent_result = self.invoke_agent(prompt)
            except Exception as exc:  # noqa: BLE001 - never crash the host
                log.exception("[phase:agent] agent invocation failed: %s", exc)
                # The bash runs the agent behind ``| tee console.log``, so a
                # non-zero exit never fails the step, and "Clean and Prepare
                # Comment" is guarded by ``if: always()``. A crashed run still
                # reaches ``prepare_comment`` and posts its structured
                # fallback; mirror that with an empty result (no artefacts)
                # instead of aborting silently (issue #590 round-4, finding 2).
                agent_result = {}
                failure_reason = str(exc)

            self._enter(phases, "validate")
            if failure_reason is None:
                try:
                    self.validate(agent_result)
                except Exception as exc:  # noqa: BLE001 - publish, then error
                    log.exception("[phase:validate] output validation failed: %s", exc)
                    failure_reason = str(exc)

            # Published even on failure: legacy always reaches ``prepare_comment``
            # (``if: always()``), so the issue thread is never left silent. A
            # validation failure keeps the agent's real artefacts, a crash falls
            # back to the crash notice (finding 2).
            self._enter(phases, "publish")
            posted_body = self.publish(agent_result)

            if failure_reason is not None:
                return RunOutcome(
                    exit_code=ExitCode.ERROR,
                    routing=RoutingDecision(status="hold", reason=failure_reason),
                    prompt=prompt,
                    posted_body=posted_body,
                    state=None,
                    phases=tuple(phases),
                )

            self._enter(phases, "state")
            state = self.persist(agent_result)

            self._enter(phases, "route")
            routing = self.route(agent_result)
        except Exception as exc:  # noqa: BLE001 - the driver never crashes the host
            log.exception("[phase:error] %s", exc)
            return RunOutcome(
                exit_code=ExitCode.ERROR,
                routing=RoutingDecision(status="hold", reason=str(exc)),
                prompt=prompt,
                posted_body=posted_body,
                state=state,
                phases=tuple(phases),
            )

        return RunOutcome(
            exit_code=_exit_code_for(routing),
            routing=routing,
            prompt=prompt,
            posted_body=posted_body,
            state=state,
            phases=tuple(phases),
        )

    @staticmethod
    def _enter(phases: list[str], name: str) -> None:
        phases.append(name)
        log.info("[phase:%s]", name)

    # @stub-protected
    def normalize(self) -> Trigger:
        """Return the adapter's normalized trigger."""
        return self.adapter.read_trigger()

    # @stub-protected
    def resolve(self, trigger: Trigger) -> Any:
        """Delegate to the legacy ``resolve_config`` result (memoized per run)."""
        key = (trigger.body, trigger.number)
        cached = self._resolve_cache.get(key)
        if cached is not None:
            return cached

        ensure_import_path()
        from resolve_config.resolve_config import resolve_config

        resolved = dict(
            resolve_config(
                trigger_body=trigger.body,
                prompt_base=str(self.repo_root / ".agents" / "prompts"),
                workflows_dir=str(self.repo_root / "workflows"),
                repo=self._repo(trigger),
                issue_number=trigger.number or None,
                debug=_is_true(self.env.get("CONFIG_DEBUG")),
            )
        )
        # Copy before annotating: the resolver may hand back a cached/shared
        # mapping, and the engine must not mutate another component's object
        # (issue #590 round-4, finding 7).
        resolved[_TRIGGER_KEY] = trigger
        self._resolve_cache[key] = resolved
        return resolved

    # @stub-protected
    def guard(self, trigger: Trigger, history: Sequence[CommentEntry]) -> Any:
        """Run the loop guard + circuit breaker against the injected history.

        Mirrors the job's "Prevent all agent-trigger loops" block, which compares
        the caller's ``<!-- bot-agent-* -->`` marker against the ``TASK`` the
        resolve step exported. That triple is read back from the resolver when
        the trigger carries no ``@@mention`` — a bot answer routes through the
        workflow state, not a mention — which is a network-free workflow lookup
        for every trigger ``run`` reaches this phase with (the resolver's own
        skip verdict short-circuits earlier).

        Like the bash ``if [[ -n "$CALLER_AGENT" && -n "$TASK" ]]``, only the
        resolver's ``TASK`` feeds the comparison: when it is empty the resolver's
        own ``SKIP_AGENT`` decision stands, even if the body names an agent
        (issue #458).
        """
        ensure_import_path()
        from circuit_breaker import check_circuit_breaker, circuit_breaker_warning

        body = trigger.body or ""
        caller = _caller_agent(body)
        # Only a body carrying a caller marker reaches the comparison, so that is
        # exactly when the resolver's ``TASK`` is read back; every such trigger
        # routes through a workflow state (a stray bot marker without a route is
        # skipped by the resolver before this phase).
        resolved: Any = self.resolve(trigger) if caller else None
        task = str(self._get(resolved, "TASK") or "")

        verdict = self._loop_verdict(
            body,
            task,
            self._get(resolved, "WORKFLOW_POSITION_PATH"),
            self._get(resolved, "WORKFLOW_STEP"),
        )
        if _is_true(verdict.get("SKIP_AGENT")):
            return verdict

        verdict.update(
            check_circuit_breaker(
                trigger.number,
                self._threshold(),
                repo=self._repo(trigger) or "",
                dry_run=True,
                history=history,
            )
        )
        # The legacy job folds a tripped breaker into SKIP_AGENT and posts the
        # warning itself (circuit_breaker.py runs non-dry-run there); the engine
        # keeps that decision and publishes the warning through the adapter.
        if _is_true(verdict.get("CIRCUIT_BREAKER_TRIPPED")):
            verdict["SKIP_AGENT"] = "true"
            verdict.setdefault(
                "REASON",
                f"Circuit breaker tripped ({verdict.get('AGENT_CALLS')} agent calls)",
            )
            self.adapter.post(
                circuit_breaker_warning(
                    int(verdict.get("AGENT_CALLS") or 0), self._threshold()
                )
            )
        return verdict

    @staticmethod
    def _loop_verdict(
        body: str, task: str, position_path: Any, step: Any
    ) -> dict[str, str]:
        """The caller-vs-target half of the bash guard block, decision for decision.

        Mirrors ``.github/workflows/openhands-multi-agent.yml`` lines 495-563
        ("Prevent all agent-trigger loops"):

        * ``500`` reads the caller marker; ``501`` gates on ``caller && task``;
        * ``503`` same caller/target -> ``loop_guard.check_same_agent_advance``
          (``505-538``), differing -> allow (``539-540``);
        * ``543`` no caller but a task -> ``detect_human_plus_agent_ping`` (``552``)
          skips, otherwise keep the resolver decision (``560-563``).

        The circuit-breaker half (``596-632``) lives in :meth:`guard`.
        """
        ensure_import_path()
        from loop_guard import check_same_agent_advance, detect_human_plus_agent_ping

        caller = _caller_agent(body)
        if caller and task:
            if caller == task:
                return dict(
                    check_same_agent_advance(body, str(position_path or ""), str(step or ""))
                )
            return {"SKIP_AGENT": "false"}
        if not caller and task:
            if detect_human_plus_agent_ping(body):
                return {"SKIP_AGENT": "true", "REASON": "Human ping + agent ping detected"}
            return {"SKIP_AGENT": "false"}
        # No same-agent comparison applicable: keep the resolver's own decision.
        return {"SKIP_AGENT": "false"}

    # @stub-protected
    def build_prompt(self, resolved: Any, history: Sequence[CommentEntry]) -> str:
        """Build the agent prompt from the resolved config and history."""
        sections = [self._base_prompt(resolved)]
        step_prompt = self._step_prompt(resolved)
        if step_prompt:
            sections.append(f"{_STEP_HEADER}\n\n{step_prompt}")
        sections.append(_USER_REQUEST_HEADER)
        sections.append(self._render_context(resolved, history))
        return "\n\n".join(section for section in sections if section)

    # @stub-protected
    def notify_agent_started(self, notice: AgentStartNotice) -> None:
        """Post the acknowledgment comment for this run's agent.

        The cutover dropped the monolith's ``Post Acknowledgment Comment`` step,
        so runs went silent between the trigger and the final answer
        (issue #720). This is the single emitter: the notice is posted once for
        every agent start, whichever workflow file drives the run. A missing
        issue number or a failed post is logged and swallowed — the
        acknowledgment is informational and must never abort the run.
        """
        if notice.issue_number is None:
            log.warning("[phase:agent] no issue number; skipping agent-start notice")
            return
        try:
            self.adapter.post(notice.render())
        except Exception as exc:  # noqa: BLE001 - a notice must not abort a run
            log.warning("[phase:agent] agent-start notice failed: %s", exc)

    # @stub-protected
    def invoke_agent(self, prompt: str) -> Any:
        """Delegate to the legacy ``agent_script.arun`` result.

        The child receives the resolver's per-run hand-off (``ROLE_NAME``,
        ``CONFIG_*``, ...) on top of the ambient environment, mirroring the
        legacy ``$GITHUB_ENV`` export (issue #717).
        """
        from engine.agent_runner import run_legacy_agent

        return run_legacy_agent(
            prompt, repo_root=self.repo_root, env=self._agent_env()
        )

    def _agent_env(self) -> dict[str, str]:
        """``self.env`` overlaid with the resolver's ``$GITHUB_ENV`` hand-off.

        Without ``ROLE_NAME`` the legacy child cannot find its agent in
        ``agent-config.yaml``, so it boots the SDK with no model and 404s on the
        first request (issue #717). The hand-off is the mapping the run already
        resolved; a configuration error is fatal so the child never boots
        modelless, while an unexpected failure falls back to the ambient
        environment.
        """
        env = {**self.env, **resolved_env_overlay(self._handoff_config())}
        env = _backfill_workflow_identity(env, self._seeded_prev_state.get("state"))
        env.update(self._artifacts().child_log_env())
        return _anchor_config_path(env, self.repo_root)

    def _seed_previous_state(
        self, history: Sequence[CommentEntry]
    ) -> WorkflowState | None:
        """Persist the previous state for the agent child (issues #671/#726/#739).

        The child carries ``version`` and ``history`` forward by reading
        ``$RUNNER_TEMP/prev_workflow_state.json``; the pre-refactor
        ``query_context`` step wrote it before the agent ran. The engine's own
        write happens *after* the agent, so without this seeding the child sees a
        stale file from a previous job (persistent runner) or nothing at all
        (fresh runner), and ``build_state`` resets ``history`` to a single entry
        (issue #726). The state is re-extracted from the history the run already
        loaded, so no extra API call is made.
        """
        state = self._state_from_history(history)
        self._seeded_prev_state["state"] = state
        if state is not None:
            self.adapter.save_state(state)
        return state

    @staticmethod
    def _state_from_history(
        history: Sequence[CommentEntry],
    ) -> WorkflowState | None:
        """Extract the latest posted state from ``history`` (``None`` on miss)."""
        ensure_import_path()
        from resolve_config.features.fetch_state.fetch_state import extract_state

        try:
            return extract_state(history)
        except Exception:  # noqa: BLE001 - a corrupt comment must not abort the run
            log.warning("[phase:prompt] could not extract the previous state")
            return None

    def _artifacts(self) -> RunArtifactStager:
        """The per-run log stager over this run's host artifact directory."""
        from engine.artifacts import RunArtifactStager

        return RunArtifactStager(self.adapter.artifact_dir())

    def _stage_run_logs(self, console_log: str) -> None:
        """Stage ``console.log`` for the CI artifact; never abort the run.

        Mirrors the legacy ``| tee console.log``: a run that cannot persist its
        logs must still publish its comment, and the upload step's ``if: always()``
        tolerates a missing directory (issue #743).
        """
        try:
            staged = self._artifacts().bundle(console_log)
        except Exception as exc:  # noqa: BLE001 - log staging is best-effort
            log.warning("[phase:publish] could not stage run logs: %s", exc)
            return
        log.info("[phase:publish] staged run logs in %s", staged.staging_dir)

    def _handoff_config(self) -> Any:
        """The resolver mapping the run routes on and hands to the agent child.

        ``run`` stores it in the ``resolve_config`` phase; a direct call outside
        the run flow (routing, unit tests) resolves it here.
        """
        remembered = self._run_resolved.get(_RUN_RESOLVED_KEY)
        if remembered is not None:
            return remembered
        return self._resolve_for_handoff()

    def _resolve_for_handoff(self) -> Any:
        """Resolve the hand-off for a call that has no stored run mapping."""
        try:
            return self.resolve(self.normalize())
        except ValueError:
            # A config error means the child has no model/provider to run with.
            # Swallowing it booted the agent modelless, so the real cause
            # surfaced as a provider 404 (issue #717 round 2).
            log.error("[phase:agent] resolver rejected the config", exc_info=True)
            raise
        except Exception as exc:  # noqa: BLE001 - the ambient env still lets the agent run
            log.warning(
                "[phase:agent] no resolved config to hand the agent child: %s",
                exc,
                exc_info=True,
            )
            return {}

    # @stub-protected
    def validate(self, agent_result: Any) -> None:
        """Delegate to ``validate_output``."""
        ensure_import_path()
        from validate_output import validate_agent_output

        answer = _answer_of(agent_result)
        result = validate_agent_output(answer, self._role_of(agent_result))
        if not result.is_valid:
            raise ValueError("agent output failed validation: " + "; ".join(result.errors))

    # @stub-protected
    def publish(self, agent_result: Any) -> str:
        """Run the legacy publish chain, then post through the adapter.

        The four-path fallback of the ``run-task`` job (final message ->
        latest answer -> console.log -> crash notice) lives in
        ``prepare_comment.build_comment_body``; calling that same function is
        what keeps a run that produced no usable output posting the *same*
        structured fallback on both hosts (issue #590 round-4, finding 2).
        The body is framed with the workflow-state header (issue #736) before
        it is escaped and posted: only the sink differs here.
        """
        ensure_import_path()
        from prepare_comment import build_comment_body, escape_agent_triggers

        answer = _answer_of(agent_result)
        final_message = _final_message_of(agent_result) or answer
        console_log = self._console_log_of(agent_result)
        self._stage_run_logs(console_log)
        body = build_comment_body(
            latest_answer=answer,
            final_message=final_message,
            console_log=console_log,
            has_final_message=bool(final_message),
            has_console_log=bool(console_log),
            trigger_author=self._trigger_author(),
            role_name=self._role_of(agent_result),
            state=self._explicit_state(agent_result),
        )
        # Same invariant as ``prepare_comment._write_output``: a posted comment
        # must never carry a raw ``@@agent`` token, or it re-arms the very gate
        # that started this run (issue #482).
        escaped = escape_agent_triggers(body)
        self.adapter.post(escaped)
        return escaped

    # @stub-protected
    def persist(self, agent_result: Any) -> WorkflowState | None:
        """Persist the next workflow state through the adapter."""
        state = self._build_state(agent_result)
        if state is None:
            return None
        self.adapter.save_state(state)
        return state

    # @stub-protected
    def route(self, agent_result: Any) -> RoutingDecision:
        """Map the agent's dispatch payload to a :class:`RoutingDecision`."""
        return to_routing_decision(self._dispatch_payload(agent_result))

    # ── internals ────────────────────────────────────────────────────

    def _repo(self, trigger: Trigger) -> str | None:
        payload_repo = (trigger.payload or {}).get("repository") or {}
        return payload_repo.get("full_name") or self.env.get("REPO_NAME") or None

    def _agent_start_notice(self, trigger: Trigger) -> AgentStartNotice:
        """Build the acknowledgment for this run's agent.

        The role comes from the resolver hand-off already stored by the
        ``resolve_config`` phase, so it matches the role the child will run as.
        """
        ensure_import_path()
        from config import AgentStartNotice

        role = self._role_of({})
        return AgentStartNotice(
            agent=role,
            role=role,
            run_url=self._run_url(trigger),
            issue_number=trigger.number or None,
        )

    def _run_url(self, trigger: Trigger) -> str:
        """Link back to the workflow run hosting this agent (legacy ``run_id``).

        Read from the environment like the removed bash step did, so the notice
        works wherever the engine boots — including a locally simulated run,
        where the URL is simply not resolvable on github.com.
        """
        server = self.env.get("GITHUB_SERVER_URL") or "https://github.com"
        repo = self.env.get("GITHUB_REPOSITORY") or self._repo(trigger) or ""
        run_id = self.env.get("GITHUB_RUN_ID") or ""
        return f"{server}/{repo}/actions/runs/{run_id}"

    def _threshold(self) -> int:
        # The ``run-task`` job exports CIRCUIT_BREAKER_THRESHOLD with a 25
        # default (``inputs.circuit_breaker_threshold || 25``); match it so a
        # host that does not export the variable still breaks at the same count.
        raw = self.env.get("CIRCUIT_BREAKER_THRESHOLD") or "25"
        try:
            return int(raw)
        except ValueError:
            return 25

    def _base_prompt(self, resolved: Any) -> str:
        location = self._get(resolved, "PROMPT_LOCATION")
        if not location:
            return ""
        from pathlib import Path

        path = Path(location)
        if not path.is_absolute():
            path = self.repo_root / path
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def _step_prompt(self, resolved: Any) -> str:
        from pathlib import Path

        location = self._get(resolved, "STEP_PROMPT_FILE")
        if not location:
            return ""
        path = Path(location)
        if not path.is_absolute():
            path = self.repo_root / path
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def _render_context(self, resolved: Any, history: Sequence[CommentEntry]) -> str:
        trigger = self._get(resolved, _TRIGGER_KEY)
        parts: list[str] = []
        if isinstance(trigger, Trigger):
            parts.append(f"**Author:** {trigger.author}")
            parts.append(trigger.body)
        task = self._get(resolved, "TASK")
        if task:
            parts.append(f"**Task:** {task}")
        parts.append("## Recent thread\n\n" + _render_history(history))
        return "\n\n".join(part for part in parts if part)

    def _role_of(self, agent_result: Any) -> str:
        if isinstance(agent_result, Mapping) and agent_result.get("role"):
            return str(agent_result["role"])
        # Same hand-off as the agent child: the resolver's ROLE_NAME, not the
        # ambient environment (which never carries it on the engine host).
        return str(self._get(self._handoff_config(), "ROLE_NAME") or "")

    @staticmethod
    def _console_log_of(agent_result: Any) -> str:
        """The run's captured console output, when the host recorded one."""
        if isinstance(agent_result, Mapping):
            value = agent_result.get("console_log") or agent_result.get("console")
            if value:
                return str(value)
        return ""

    def _trigger_author(self) -> str:
        """Author pinged by the crash-path fallback bodies."""
        author = self.env.get("TRIGGER_AUTHOR")
        if author:
            return author
        try:
            return self.adapter.read_trigger().author
        except Exception:  # noqa: BLE001 - a missing event payload is not fatal here
            return ""

    def _build_state(self, agent_result: Any) -> WorkflowState | None:
        """Return the agent's own state artifact, else the previous state.

        The state is never synthesized here: the workflow/step/agent triple is
        the agent run's own output (``agent_script`` emits ``workflow_state.json``
        in the legacy ``WORKFLOW_STATE v1`` schema), and inventing it from a
        routing decision would corrupt the version history. A run that produced
        no state leaves the previous state untouched.
        """
        explicit = self._explicit_state(agent_result)
        if explicit is not None:
            return explicit
        return self._previous_state()

    @staticmethod
    def _explicit_state(agent_result: Any) -> WorkflowState | None:
        if not isinstance(agent_result, Mapping):
            return None
        raw = agent_result.get("state")
        if raw is None:
            return None
        ensure_import_path()
        from workflow_state.workflow_state import WorkflowState

        if isinstance(raw, WorkflowState):
            return raw
        if isinstance(raw, Mapping):
            try:
                return WorkflowState.model_validate(dict(raw))
            except Exception:  # noqa: BLE001 - a malformed artifact is not fatal
                return None
        return None

    def _previous_state(self) -> WorkflowState | None:
        try:
            return self.adapter.load_state()
        except Exception:  # noqa: BLE001 - a missing/corrupt state is not fatal
            return None

    def _dispatch_payload(self, agent_result: Any) -> dict[str, Any]:
        if isinstance(agent_result, Mapping):
            dispatch = agent_result.get("dispatch")
            if isinstance(dispatch, Mapping):
                return dict(dispatch)
            if agent_result.get("status"):
                return dict(agent_result)
        return _dispatch_from_text(
            _answer_of(agent_result) or _final_message_of(agent_result)
        )

    @staticmethod
    def _get(resolved: Any, key: str) -> Any:
        return resolved.get(key) if isinstance(resolved, Mapping) else None


def _render_history(history: Sequence[CommentEntry]) -> str:
    if not history:
        return "_(no prior thread)_"
    return "\n\n".join(
        f"### {entry.author} — {entry.created_at}\n\n{entry.body}" for entry in history
    )


def _dispatch_from_text(text: str) -> dict[str, Any]:
    """Recover the ``AGENT_RUN_CONFIG`` dispatch payload from rendered text."""
    if not text:
        return {}
    ensure_import_path()
    from validate_output import extract_dispatch

    raw = extract_dispatch(text)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if isinstance(data, Mapping) and isinstance(data.get("dispatch"), Mapping):
        return dict(data["dispatch"])
    return dict(data) if isinstance(data, Mapping) else {}


# @stub-protected
def main(argv: Sequence[str] | None = None) -> int:
    """Select the adapter, run once, map ``RunOutcome.exit_code`` to the process code."""
    import argparse

    from adapters import build_adapter
    from engine.paths import repo_root

    parser = argparse.ArgumentParser(description="Run one orchestration engine step")
    parser.add_argument(
        "--adapter",
        default=os.environ.get("ENGINE_ADAPTER", "github"),
        choices=("github", "local"),
    )
    parser.add_argument("--run-dir", default=os.environ.get("ENGINE_RUN_DIR"))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO)
    kwargs: dict[str, Any] = {}
    if args.adapter == "local":
        if not args.run_dir:
            parser.error("--run-dir is required for the local adapter")
        from pathlib import Path

        kwargs["run_dir"] = Path(args.run_dir)
    else:
        kwargs["repo"] = args.repo

    adapter = build_adapter(args.adapter, **kwargs)
    outcome = Engine(adapter=adapter, repo_root=repo_root(), env=dict(os.environ)).run()
    return int(outcome.exit_code)
