"""Resolve configuration facade.

Package-by-feature DDD layout:
  sub_domains/      -- domain value objects (TriggerCommands)
  features/         -- behavioral features (parse_trigger, fetch_state, resolve_route, build_result)
  resolve_config.py -- facade (resolve_config() orchestrator + main() CLI)
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping, Optional

from .features.build_result.build_result import _build_result
from .features.fetch_state.fetch_state import _fetch_workflow_state_from_history
from .sub_domains.workflow_command.workflow_command import (
    WorkflowCommand,
    classify_comment,
    parse_command,
)
from .features.parse_trigger.parse_trigger import (
    is_bot_comment,
    has_universal_bot_marker,
    is_plain_mention_trigger,
    extract_plain_mention_agent,
    extract_mention_agent,
    normalize_resume_input,
    extract_debug_flag,
    extract_machine_trigger,
    is_answer_continuation,
    _parse_trigger_body,
)
from .features.resolve_route.agent_config_resolver import (
    PERSONAL_CHECKOUT_DIR,
    AgentConfigNotFound,
    AgentConfigResolver,
    current_personal_snapshot,
    personal_run_from_env,
    required_secret_envs,
    select_config_name,
)
from .features.resolve_route.resolve_route import (
    build_author_identity_from_env,
    _armed_gate,
    _agent_config_param,
    _available_agent_configs,
    _resolve_agent_config_value,
    _resolve_resume_path,
    _resolve_outcome_path,
    _resolve_function_call_path,
    _resolve_gate_decision_path,
    _resolve_handoff_decision,
    _resolve_machine_trigger_path,
    _resolve_human_path,
    _resolve_bot_fallback_path,
    _resolve_workflow_next_step,
)
from nebuko.engine.harness.credentials import (
    SHARED_LANE,
    USER_REPO_LANE,
    check_personal_secrets,
    decide_lane,
)
from nebuko.engine.lib.config import WorkflowPosition, is_self_function_call
from nebuko.engine.lib.workflow_state import Appreciation, IncompleteReason, TurnOutcome


def _is_resumable_outcome(outcome: Optional[TurnOutcome]) -> bool:
    """True when ``/nebuko resume`` may re-run the parked step (issue #964).

    Every halted shape is resumable: a ``hold`` verdict (human gate or awaiting
    input) and an incomplete halt — a soft time-kill (issue #704), an
    interruption (issue #978), an exhausted agent checkpoint (issue #965) or a
    crashed run (issue #978, whose notification names ``/nebuko resume`` as the
    recovery command). The explicit human resume re-runs the parked step.
    """
    if outcome is None:
        return False
    if outcome.appreciation == Appreciation.HOLD:
        return True
    return outcome.incomplete_reason in (
        IncompleteReason.SOFT_TIMEOUT,
        IncompleteReason.INTERRUPTED,
        IncompleteReason.AGENT_CHECKPOINT,
        IncompleteReason.FAILURE,
    )


def _delete_comments_env(
    issue_param: Optional[str],
    from_comment: Optional[str],
    issue_number: Optional[int],
):
    """Resolve an agent-free comment-removal command to its env contract.

    Serves ``/nebuko delete comments`` (issue #644) and its registry alias
    ``/nebuko clean comments`` (issues #674/#679): both run the deterministic
    deletion step and no agent, so this returns the full env dict directly (the
    same shape ``_build_result`` would emit) instead of a config routed through
    the workflow machinery. ``issue_param`` is the optional ``--issue`` value;
    it defaults to the invoking issue. When neither is available the command is
    treated as absent (``None``), so routing falls through to the normal
    cascade.

    ``from_comment`` is the optional ``--from`` comment id (issue #867): when
    set, it scopes the deletion to that comment and every later one on the
    target issue (``DELETE_COMMENTS_FROM``); when absent the whole issue is
    cleared, preserving today's behaviour.

    The parser already rejects non-numeric ``--issue``/``--from`` values, so
    the values reaching here are either empty or ASCII digits.
    """
    issue = issue_param or (str(issue_number) if issue_number else "")
    if not issue:
        return None
    env = {
        "TASK": f"Delete comments on issue {issue}",
        "ROLE_NAME": "script",
        "AGENT_SELECTED": "false",
        "SKIP_AGENT": "true",
        "DELETE_COMMENTS": "true",
        "DELETE_COMMENTS_ISSUE": issue,
    }
    if from_comment:
        env["DELETE_COMMENTS_FROM"] = from_comment
    return env


def _update_circuit_breaker_env(value_param: Optional[str]) -> dict:
    """Resolve an agent-free circuit-breaker limit update to its env contract.

    Serves ``/nebuko update circuit-breaker --value <n>`` (issue #839): a
    human-only, per-issue policy update that raises or lowers the breaker's
    call budget without touching the ``circuit_breaker_threshold`` workflow
    parameter. The deterministic step posts the updated workflow state (so the
    next run's guard reads the new limit) and a confirmation; no agent runs and
    no call budget is consumed, because the local path emits no
    ``bot-agent-`` markers.

    The parser guarantees ``--value`` is present and ASCII-numeric, so the only
    value rejected here is ``0`` (a breaker limit must be at least 1). Unlike
    :func:`_delete_comments_env`, a rejected value does not fall through to the
    agent cascade: it returns a ``COMMAND_ERROR`` contract so the engine posts
    the usage hint instead of booting an agent.
    """
    value = (value_param or "").strip()
    if not (value.isascii() and value.isdigit()) or int(value) < 1:
        usage = "/nebuko update circuit-breaker --value <n>"
        return {
            "SKIP_AGENT": "true",
            "COMMAND_ERROR": "true",
            "REASON": f"invalid circuit-breaker value; expected {usage}",
        }
    return {
        "TASK": f"Update circuit-breaker limit to {value}",
        "ROLE_NAME": "script",
        "AGENT_SELECTED": "false",
        "SKIP_AGENT": "true",
        "UPDATE_CIRCUIT_BREAKER": "true",
        "UPDATE_CIRCUIT_BREAKER_VALUE": value,
    }


def _apply_dispatch_fields(
    wf_cmd: Optional[WorkflowCommand],
    step: Optional[str],
    agent_config: Optional[str],
) -> tuple[Optional[WorkflowCommand], Optional[str]]:
    """Seed a ``run workflow`` command from a dispatch's ``step`` / ``agent_config``.

    The dispatch fields (ADR 0017, ``client_payload``) enter through the same
    ``--step`` / ``--config`` params a human command carries, so the entry and
    agent-config paths validate and persist them unchanged. An explicit flag in
    the command wins. ``agent_config`` must be the file name of an agent config
    reachable from the search roots: it is matched by name, never read as a path.

    Returns:
        ``(command, error)``; ``error`` is a terminal usage message, else ``None``.
    """
    if not (step or agent_config):
        return wf_cmd, None
    if wf_cmd is None or wf_cmd.action != "run" or wf_cmd.resource != "workflow":
        return wf_cmd, (
            "dispatch step/agent_config require a /nebuko run workflow prompt"
        )
    params = dict(wf_cmd.params)
    if step and not params.get("step"):
        params["step"] = step
    if agent_config and not params.get("config"):
        from nebuko.engine.paths import ConfigPathAnchor

        caller_root = ConfigPathAnchor.resolve_caller_root(os.environ)
        available = _available_agent_configs(caller_root)
        if agent_config not in available:
            listing = ", ".join(available) if available else "none found"
            return wf_cmd, (
                f"unknown agent config '{agent_config}' in dispatch payload; "
                f"available: {listing}"
            )
        params["config"] = agent_config
    return WorkflowCommand(action=wf_cmd.action, resource=wf_cmd.resource, params=params), None


_LANE_CHANGE_ACTIONS = frozenset({"takeover", "move_to_project", "switch_to_personal"})

#: Intents that run something. Lane stickiness guards only these: plain prose on
#: a personal thread is never refused (issue #1479).
_RUNNING_INTENTS = frozenset({"command", "agent_trigger"})


def _personal_run_actor(env: Mapping[str, str]) -> tuple[Optional[str], Optional[str]]:
    """The dispatching actor of the personal repo's run, or a refusal (issue #1479).

    ``client_payload.actor`` is taken as the actor only when it equals the owner
    GitHub reports for this run (``GITHUB_REPOSITORY_OWNER``, set by the runner,
    never by the caller workflow). Returns ``(actor, None)`` or ``(None, refusal)``.
    """
    from nebuko.engine.lib.workflow_state.workflow_state import dispatch_actor_from_event

    owner = (env.get("GITHUB_REPOSITORY_OWNER") or "").strip()
    actor = dispatch_actor_from_event(env)
    if actor and owner and actor.lower() == owner.lower():
        return actor, None
    who = actor or "an unknown actor"
    return None, (
        f"this personal run was dispatched by {who}, who is not the owner of this "
        f"personal repo ({owner or 'owner unknown'}). Only the owner runs on their own "
        "credential, so the run is refused."
    )


def _lane_env(action: str, actor: str) -> dict:
    """Lane facts for the credential guard and the state writer (issue #1479).

    Returned as result keys, so they reach the run's environment with the other
    resolved values. Read by ``lane_binding_from_env`` and the continuation guard.
    """
    if action in ("takeover", "switch_to_personal"):
        env = {"WORKFLOW_LANE": USER_REPO_LANE, "CLAUDE_CREDENTIAL_OWNER": actor}
    elif action == "move_to_project":
        env = {"WORKFLOW_LANE": SHARED_LANE, "CLAUDE_CREDENTIAL_OWNER": ""}
    else:
        return {}
    env["NEBUKO_LANE_ACTION"] = action
    return env


def _is_path_like(name: str) -> bool:
    """True when a config value names a path rather than a bare file name."""
    return os.path.isabs(name) or "/" in name or os.sep in name


def _agent_config_selection(
    trigger_body: str,
    wf_cmd: Optional[WorkflowCommand],
    prev_state,
    *,
    requested_config: Optional[str],
    lane_action: str,
    personal_lane: bool,
) -> dict:
    """Agent-config keys for this run (issues #1389, #1479).

    Name order: ``--config``, the previous state's name (dropped on a takeover
    or lane switch), the personal manifest (personal lane only), the project
    manifest, then the shipped default. The personal lane resolves names only
    in the personal checkout and the shipped defaults, never in the project's
    ``.nebuko/agents``, and checks the config's secrets against the personal
    environment. Returns ``{}`` to keep the workflow's own AGENT_CONFIG_PATH.
    """
    from nebuko.engine.lib.default_paths import checkout_root
    from nebuko.engine.models import PersonalManifest
    from nebuko.engine.paths import ConfigPathAnchor, load_manifest

    caller_root = ConfigPathAnchor.resolve_caller_root(os.environ)
    personal_root = caller_root / PERSONAL_CHECKOUT_DIR
    # The personal manifest was read once before the run's first step (issue #1485).
    snapshot = current_personal_snapshot() if personal_lane else None
    personal_manifest_name = None
    if personal_lane and snapshot is not None:
        ignored = list(snapshot.manifest_ignored)
        personal_manifest_name = snapshot.config_name
        if snapshot.note:
            print(f"Warning: {snapshot.note}", file=sys.stderr)
    elif personal_lane:
        manifest, ignored = PersonalManifest.load(personal_root / ".nebuko" / "nebuko.yaml")
        personal_manifest_name = manifest.agent_config
    if personal_lane:
        for key in ignored:
            print(
                f"Warning: ignoring '{key}' in the personal manifest; only agent_config is read there",
                file=sys.stderr,
            )
    personal_present = personal_lane and (snapshot is not None or personal_root.is_dir())
    try:
        project_manifest_name = load_manifest(caller_root / ".nebuko" / "nebuko.yaml").agent_config
    except Exception as exc:  # noqa: BLE001 - a bad manifest must not crash routing
        print(f"Warning: unreadable .nebuko/nebuko.yaml ({exc}); ignoring agent_config", file=sys.stderr)
        project_manifest_name = None

    resolver = AgentConfigResolver.for_run(
        "personal" if personal_lane else "project",
        caller_root,
        personal_root if personal_present else None,
        checkout_root() / "defaults" / "agents",
    )
    checkout_notice = ""
    if personal_lane and not personal_present:
        # Without the checkout the personal manifest and overrides cannot be read,
        # so whether they would have changed the config is unknowable: say so.
        checkout_notice = (
            f"the personal repo checkout ({PERSONAL_CHECKOUT_DIR}) was not found: its "
            "manifest and agent configs were not read, so this run uses the shipped "
            "defaults"
        )
    previous_name = prev_state.agent_config if prev_state is not None else None
    carry_previous = lane_action not in _LANE_CHANGE_ACTIONS
    name, source = select_config_name(
        flag=requested_config,
        previous=previous_name,
        personal_manifest=personal_manifest_name,
        project_manifest=project_manifest_name,
        carry_previous=carry_previous,
    )
    if source == "previous" and name and not _is_path_like(name):
        try:
            resolver.resolve(name)
        except AgentConfigNotFound:
            # A persisted name that no longer resolves falls back to the manifests
            # and shipped default, in both lanes, instead of failing the resume.
            print(
                f"Warning: persisted agent config '{name}' not found; falling back to the manifest or default",
                file=sys.stderr,
            )
            name, source = select_config_name(
                flag=None,
                previous=None,
                personal_manifest=personal_manifest_name,
                project_manifest=project_manifest_name,
                carry_previous=False,
            )
    if name is None and personal_lane:
        name = Path(os.environ.get("AGENT_CONFIG_PATH") or "agent-config.yaml").name
        source = "shipped"
    if name is None:
        return {}

    if _is_path_like(name):
        if personal_lane:
            return {"task": "", "config_command_error": f"agent config '{name}' must be a file name in the personal lane"}
        resolved_path = _resolve_agent_config_value(name, caller_root)
        if resolved_path is None:
            return {"task": "", "config_command_error": f"agent config file not found: '{name}'"}
        return {
            "agent_config_path": resolved_path,
            "agent_config_raw": name,
            "agent_config_layer": "project",
            "checkout_notice": checkout_notice,
        }

    try:
        resolved = resolver.resolve(name)
    except AgentConfigNotFound as exc:
        if source == "flag" and not personal_lane:
            available = _available_agent_configs(caller_root)
            listing = ", ".join(available) if available else "none found"
            usage = (
                "/nebuko run workflow --name <workflow> "
                "[--step <step[.step...]>] [--config <agent-config>]"
            )
            return {
                "task": "",
                "config_command_error": (
                    f"unknown agent config '{name}'; available: {listing} — usage: {usage}"
                ),
            }
        return {"task": "", "config_command_error": str(exc)}

    if personal_lane:
        try:
            if resolved.layer == "personal" and snapshot is not None and name == snapshot.config_name:
                required = list(snapshot.secret_names)
            else:
                required = required_secret_envs(resolved.path)
        except Exception as exc:  # noqa: BLE001 - reported to the thread, not a crash
            return {"task": "", "config_command_error": f"agent config '{name}' is unreadable: {exc}"}
        message = check_personal_secrets(name, required, os.environ)
        if message is not None:
            return {"task": "", "config_command_error": message}
    return {
        "agent_config_path": str(resolved.path),
        "agent_config_raw": name,
        "agent_config_layer": resolved.layer,
        "checkout_notice": checkout_notice,
    }


def resolve_config(
    trigger_body: str,
    prompt_base: str,
    workflows_dir: str,
    is_bot: Optional[bool] = None,
    repo: Optional[str] = None,
    issue_number: Optional[int] = None,
    debug: bool = False,
    local_workflows_dir: Optional[str] = None,
    dispatch_step: Optional[str] = None,
    dispatch_agent_config: Optional[str] = None,
) -> dict:
    """Resolve complete agent configuration from trigger body.

    Orchestrates helper methods to determine agent task and parameters:
    1. Parse trigger body for workflow state and dispatch (_parse_trigger_body).
    2. Bot detection (if not pre-determined).
    3. If no state in body, fetch from issue comment history.
    4. Route based on what was found (human commands take precedence):
       - /nebuko resume [--feedback <text>] → _resolve_resume_path / next step.
       - /nebuko force-step step --name <step> → _resolve_human_path (regardless of state).
       - /nebuko delete comments [--issue <n>] [--from <id>] (human) →
         _delete_comments_env (self-contained contract; no agent runs).
       - /nebuko clean comments [--issue <n>] (human) → same agent-free
         comment-maintenance contract, matched via classify_comment
         (issues #674/#679).
       - /nebuko command by human → _resolve_human_path.
       - Human @@mention, or CI plain-mention trigger (universal bot marker
         + mention-only body) → _resolve_human_path (parity between the two:
         the mention wins over an active dispatch).
       - State carrying a turn outcome → _resolve_outcome_path.
       - State without an outcome → _resolve_workflow_next_step.
       - Human trigger without state → _resolve_human_path.
       - Bot fallback → _resolve_bot_fallback_path.
    5. Build result dict with all env vars (_build_result).

    Args:
        trigger_body: Raw trigger body text.
        prompt_base: Path to prompt files directory.
        workflows_dir: Path to workflows directory.
        is_bot: Pre-determined bot status (None = auto-detect).
        repo: Repository in owner/name format (for fetching comments).
        issue_number: Issue/PR number (for fetching comments).
        local_workflows_dir: Optional consumer-local workflows dir that takes
            precedence over ``workflows_dir`` for workflow lookups (issue
            #529).
        dispatch_step: ``client_payload.step`` of a repository_dispatch run;
            seeds the run's entry step like ``--step``. ``None`` means the
            workflow's first step.
        dispatch_agent_config: ``client_payload.agent_config``; selects the agent
            config like ``--config`` and is stamped into the state.

    Returns:
        Dict with all environment variables to set.
    """
    # A continuation part of a split answer is inert by construction (issue
    # #1002): its trigger payload was escaped at the publish seam because the
    # payload belongs to part 1 alone. Should one ever reach the resolver — a
    # hand-posted body, a newer gate — it must resolve to nothing rather than
    # boot a second run for the same logical answer (issue #994).
    if is_answer_continuation(trigger_body):
        return {
            "SKIP_AGENT": "true",
            "REASON": "Answer continuation part; trigger payload lives on part 1",
        }

    if is_bot is None:
        is_bot = is_bot_comment(trigger_body)

    # Parse trigger body for workflow state and its turn outcome
    prev_state, turn_outcome = _parse_trigger_body(trigger_body)

    # Check for human commands early — they take precedence over workflow state
    # regardless of whether prev_state exists (they need to resolve the command)
    command_parse = parse_command(trigger_body)
    wf_cmd = command_parse.command
    if not is_bot:
        wf_cmd, dispatch_error = _apply_dispatch_fields(
            wf_cmd, dispatch_step, dispatch_agent_config
        )
        if dispatch_error:
            return {"SKIP_AGENT": "true", "REASON": dispatch_error, "COMMAND_ERROR": "true"}

    # Centralized fallback: if no state from current body, try comment history
    if not prev_state:
        prev_state = _fetch_workflow_state_from_history(repo, issue_number)
        if prev_state:
            turn_outcome = prev_state.turn_outcome

    if os.environ.get("DEBUG") or debug:
        print(f"DEBUG prev_state: {prev_state}", file=sys.stderr)

    # /nebuko delete comments is a sole deterministic command, outside the
    # agent/workflow machinery: resolve it straight to its env contract and
    # return (issue #644). Bot-posted commands are ignored (human-only).
    if wf_cmd is not None and wf_cmd.action == "delete" and not is_bot:
        delete_env = _delete_comments_env(
            wf_cmd.params.get("issue"), wf_cmd.params.get("from"), issue_number
        )
        if delete_env is not None:
            return delete_env

    # /nebuko update circuit-breaker --value <n> — human-only per-issue
    # circuit-breaker policy update (issue #839). Agent-free: the deterministic
    # step posts the new state so the next run's guard reads the raised limit.
    # Must short-circuit before the agent-trigger cascade, otherwise the command
    # would boot an agent; the ``not is_bot`` gate is mandatory (a bot raising
    # its own budget is a self-rearming loop).
    if wf_cmd is not None and wf_cmd.action == "update" and not is_bot:
        return _update_circuit_breaker_env(wf_cmd.params.get("value"))

    # /nebuko clean comments — registry alias of the agent-free comment
    # maintenance path (issues #674/#679). `clean` is a registered local
    # command but is not part of the core /nebuko grammar (ACTIONS), so it is
    # matched via the comment classifier. This short-circuit must run *before*
    # the agent-trigger cascade below, otherwise `/nebuko clean comments`
    # falls through and boots context/architect (issue #590). Human-only, like delete.
    # ``--from`` is forwarded just as for `delete`, so `--issue <n>` targets
    # that issue and `--from <id>` scopes the deletion (issue #867) rather than
    # silently falling back to the invoking issue / whole thread.
    # Lane D (issue #1479): a --personal command is the broker's to dispatch.
    # The project side skips it with no comment and no label change; the
    # personal repo's own run (and bot bodies) consume the flag instead.
    personal_lane = personal_run_from_env(os.environ)
    command_intent = classify_comment(
        trigger_body, consume_lane_flag=personal_lane or bool(is_bot)
    )
    if command_intent.kind == "personal_dispatch":
        return {"SKIP_AGENT": "true", "REASON": "personal dispatch: handled by the personal lane"}
    # Lane stickiness (issue #1479) guards a comment that would run something
    # (a nebuko command or an agent trigger). The personal repo's own run is a
    # repository_dispatch: its actor is checked against the owner GitHub reports.
    lane_action = "allow"
    lane_notice = ""
    lane_env: dict = {}
    personal_actor = None
    if personal_lane and not is_bot:
        personal_actor, refusal = _personal_run_actor(os.environ)
        if refusal is not None:
            return {"SKIP_AGENT": "true", "COMMAND_ERROR": "true", "REASON": refusal}
    human_thread_comment = (
        not is_bot
        and not personal_lane
        and (os.environ.get("GITHUB_EVENT_NAME") or "").strip() == "issue_comment"
    )
    if prev_state is not None and command_intent.kind in _RUNNING_INTENTS:
        if personal_lane and not is_bot:
            lane_decision = decide_lane(
                prev_state.lane, prev_state.credential_owner, "personal", personal_actor or ""
            )
        elif human_thread_comment:
            lane_decision = decide_lane(
                prev_state.lane,
                prev_state.credential_owner,
                wf_cmd.lane_flag if wf_cmd is not None else None,
                build_author_identity_from_env(trigger_body).login,
            )
        else:
            lane_decision = None
        if lane_decision is not None:
            lane_action = lane_decision.action
            lane_notice = lane_decision.notice or ""
            if lane_decision.action == "refuse":
                return {"SKIP_AGENT": "true", "COMMAND_ERROR": "true", "REASON": lane_decision.message}
            lane_env = _lane_env(lane_action, personal_actor or build_author_identity_from_env(trigger_body).login)
    if (
        command_intent.kind == "command"
        and command_intent.command == "clean"
        and not is_bot
    ):
        clean_cmd = wf_cmd
        clean_env = _delete_comments_env(
            clean_cmd.params.get("issue") if clean_cmd is not None else None,
            clean_cmd.params.get("from") if clean_cmd is not None else None,
            issue_number,
        )
        if clean_env is not None:
            return clean_env

    # A malformed /nebuko invocation is a *command attempt*: the grammar alone
    # decides it (issue #867), so it is answered with the usage hint (ADR-0004
    # contract) and never reaches the workflow-continuation cascade below. This
    # is what stops an active dispatch from absorbing a mistyped command and
    # booting an agent (the #822 `clean comments --from <id>` regression).
    # Human-only: a bot body keeps its normal fallback.
    if command_intent.kind == "command_attempt" and not is_bot:
        return {
            "SKIP_AGENT": "true",
            "COMMAND_ERROR": "true",
            "REASON": command_intent.usage or "",
        }

    # Route to appropriate handler based on what was found
    # Human commands (explicit intent) MUST be evaluated before auto-dispatch,
    # otherwise an active workflow's dispatch object absorbs all commands.
    # Check for human commands early (independent of is_bot) so they take
    # priority even when prev_state+turn_outcome exist.
    human_mention = not is_bot and extract_mention_agent(trigger_body)
    # CI-posted command triggers (sub-issue bootstrap "@@architect") now carry
    # the universal bot marker and so classify as bot. They are plain
    # mention-only triggers (no prose after the mention) that must still route
    # to the mentioned agent, even when an active workflow dispatch exists in
    # the comment history — matching the pre-marker behavior where their bare
    # mention set human_mention.
    # Only the universal marker qualifies: legacy bot-agent- comments keep
    # their historical routing (skip unless they embed state/dispatch).
    ci_mention = has_universal_bot_marker(trigger_body) and is_plain_mention_trigger(
        trigger_body
    )
    if wf_cmd is not None and wf_cmd.action in ("approve", "reject"):
        # Engine-owned gate release (/nebuko approve|reject gate, issue #648).
        # Placed before the resume branches: a human_gate hold is released
        # only here, never by /nebuko resume. Handled inline — never
        # dispatched to an agent. Identity is sourced from the CI event env
        # (author login/type/association), never from body markers.
        config = _resolve_gate_decision_path(
            prev_state,
            wf_cmd,
            build_author_identity_from_env(trigger_body),
            workflows_dir,
            local_workflows_dir=local_workflows_dir,
        )
    elif (
        wf_cmd is not None
        and wf_cmd.action == "resume"
        and prev_state
        and prev_state.workflow
        and _is_resumable_outcome(prev_state.turn_outcome)
    ):
        # /nebuko resume — re-run the held step with human input. A
        # soft-kill exhaustion halt (issue #704) and an exhausted agent
        # checkpoint (issue #965) are resumable exactly like a human-gate hold:
        # the explicit human resume overrides the pending error status and
        # re-runs the interrupted step.
        config = _resolve_resume_path(
            prev_state, trigger_body, workflows_dir, local_workflows_dir=local_workflows_dir,
            human_input=normalize_resume_input(wf_cmd.params.get("feedback", "")),
        )
    elif (
        wf_cmd is not None
        and wf_cmd.action == "resume"
        and prev_state
        and prev_state.workflow
        and prev_state.turn_outcome is None
    ):
        # /nebuko resume with no dispatch — advance to next step, carrying
        # human input forward (issue #420: resolution-only state). A bare
        # resume maps to the BARE_RESUME_INPUT sentinel so the human_gate
        # interceptor (issue #506) treats this run as human-triggered.
        human_input = normalize_resume_input(wf_cmd.params.get("feedback", ""))
        config = _resolve_workflow_next_step(prev_state, turn_outcome, workflows_dir, local_workflows_dir=local_workflows_dir)
        config["config_human_resume_input"] = human_input
    elif wf_cmd is not None and wf_cmd.action == "force-step":
        # Human explicit step jump (/nebuko force-step step --name <step>) —
        # takes priority over auto-dispatch. Must go through _resolve_human_path
        # for validation/error handling. prompt_base lets the force-step branch
        # resolve sub-workflow targets (issue #599).
        config = _resolve_human_path(
            trigger_body, repo, issue_number, workflows_dir, prev_state,
            local_workflows_dir=local_workflows_dir, prompt_base=prompt_base,
            workflow_command=wf_cmd,
        )
    elif wf_cmd is not None and wf_cmd.action == "run" and not is_bot:
        # Human /nebuko run workflow --name <name> override — use human path
        # to honor the command.
        config = _resolve_human_path(
            trigger_body, repo, issue_number, workflows_dir, prev_state,
            local_workflows_dir=local_workflows_dir,
            workflow_command=wf_cmd,
        )
    elif human_mention or ci_mention:
        # Human @@mention (e.g. @@context) takes priority over auto-dispatch
        # so standalone agent calls work even when a workflow is active.
        # ci_mention covers CI-posted plain mention triggers (see above):
        # their universal marker makes them bot bodies, so extract_mention
        # agent returns None — pin the agent from the raw shape instead.
        ci_agent = (
            extract_plain_mention_agent(trigger_body)
            if ci_mention and not human_mention
            else None
        )
        config = _resolve_human_path(
            trigger_body, repo, issue_number, workflows_dir,
            pinned_task=ci_agent,
            local_workflows_dir=local_workflows_dir,
        )
    elif (
        prev_state
        and prev_state.function_call
        and not is_self_function_call(prev_state.function_call, getattr(prev_state, "agent", ""))
    ):
        # Imperative agent-to-agent hand-off (call_agent): explicit pinning of
        # the next agent, beats workflow continuation (same tier as human
        # mention). Consumed once — the fresh state built for the called agent
        # clears the field, so a re-trigger no-ops. A *self* hand-off is
        # neutralized here (issue #618, defect 2b): routing falls through to
        # the dispatch / workflow-next path below.
        handoff = _resolve_handoff_decision(
            prev_state, workflows_dir, local_workflows_dir=local_workflows_dir
        )
        if _armed_gate(prev_state) is not None:
            # Consult from an armed gate (issue #1003): the gate blocks the
            # workflow advance, never a delegation — route the hand-off through
            # the outcome path, which applies the same guard, spends one
            # consult round and returns the callee config. This holds for a
            # *forward-step* hand-off (``function_call``) exactly as for the
            # ``gate_hold`` shape: both must respect the consult budget, so a
            # spent budget degrades to the benign hold instead of bypassing it.
            config = _resolve_outcome_path(
                prev_state, workflows_dir, local_workflows_dir=local_workflows_dir
            )
        elif handoff.reason_code in ("gate_hold", "dispatch"):
            # Gate precedence (issue #733): a `human_gate` successor outranks
            # the hand-off. No forward step for the callee (`dispatch`) is a
            # no-op. In both cases continue the workflow so the gated/successor
            # step runs and no consumed step is re-entered.
            config = (
                _resolve_outcome_path(
                    prev_state, workflows_dir, local_workflows_dir=local_workflows_dir
                )
                if turn_outcome
                else _resolve_workflow_next_step(
                    prev_state, turn_outcome, workflows_dir, local_workflows_dir=local_workflows_dir
                )
            )
        else:
            config = _resolve_function_call_path(prev_state.function_call, workflows_dir, prev_state, local_workflows_dir=local_workflows_dir)
    elif extract_machine_trigger(trigger_body):
        # CI-posted machine trigger token (bot-trigger) — replaces the legacy
        # machine-posted @@architect comments (issue #478).
        config = _resolve_machine_trigger_path(trigger_body, workflows_dir)
    elif prev_state and turn_outcome:
        # Auto-dispatch: workflow state with a turn outcome — route on it.
        config = _resolve_outcome_path(prev_state, workflows_dir, local_workflows_dir=local_workflows_dir)
    elif prev_state and prev_state.workflow and not is_bot:
        # Legacy state (no turn outcome) from a *human* trigger: continue the
        # workflow forward edge (issue #420 resolution-only resume).
        config = _resolve_workflow_next_step(prev_state, turn_outcome, workflows_dir, local_workflows_dir=local_workflows_dir)
    elif prev_state and prev_state.workflow:
        # Notification (issue #978): a bot trigger whose parsed body carries
        # workflow state but no dispatch information — no turn outcome, no
        # pending hand-off (both handled above), no workflow command — is a
        # plain message. The engine performs no action on it: no route, no
        # successor step, no re-trigger. Before this guard the state alone was
        # enough to follow the forward edge, which is how a crashed run's
        # notification advanced `implement -> review`.
        config = {
            "SKIP_AGENT": "true",
            "REASON": "Notification: no dispatch information",
        }
    elif not is_bot:
        config = _resolve_human_path(
            trigger_body, repo, issue_number, workflows_dir,
            local_workflows_dir=local_workflows_dir,
        )
    else:
        config = _resolve_bot_fallback_path(trigger_body, workflows_dir, local_workflows_dir=local_workflows_dir)

    if config.get("SKIP_AGENT") == "true":
        return config

    # Per-run agent config selection (issue #1389): ``/nebuko run workflow
    # --config <file>`` picks the agent config for this run only. The raw value
    # is persisted in workflow state, so every later hand-off comment (a new
    # job with no flag) re-applies it; an explicit flag overrides it. An unknown
    # *explicit* value is a terminal command error listing the available configs
    # — never a silent fallback to the workflow's own config (ADR-0011). A
    # *persisted* value that no longer resolves only warns: a deleted file must
    # not kill the run that is already in flight.
    requested_config = (
        _agent_config_param(wf_cmd)
        if wf_cmd is not None and wf_cmd.action == "run" and not is_bot
        else None
    )
    config.update(
        _agent_config_selection(
            trigger_body,
            wf_cmd,
            prev_state,
            requested_config=requested_config,
            lane_action=lane_action,
            personal_lane=personal_lane,
        )
    )

    # Branch-mounted child fetch-on-miss (issue #604): carry the state's
    # scaffold_branch into result-building so sub-workflow entry resolution
    # can fetch children absent from the local (default-branch) checkout.
    if prev_state is not None and prev_state.scaffold_branch:
        config["config_scaffold_branch"] = prev_state.scaffold_branch

    # Carry-forward position (issue #812): the previous run's full mount
    # chain is the authoritative base every transition mutates — an
    # intra-workflow step move replaces its leaf, a mount boundary splices or
    # pops. Without it, emission would rebuild the stack from the lossy flat
    # parent pair and truncate the ancestors of a nested run.
    if prev_state is not None and prev_state.position:
        config["config_position"] = WorkflowPosition(refs=list(prev_state.position))

    # Complexity governance (issue #869): resolve the run's policy once, pin the
    # snapshot for auditability, and evaluate a boundary drift adjustment over
    # the prior history. Best-effort — a policy/config failure must never break
    # routing, so anything unexpected degrades to "no adjustment".
    try:
        from nebuko.engine.lib.complexity_governor.integration import (
            compute_boundary_adjustment,
            resolve_run_policy,
        )

        policy = resolve_run_policy()
        config["config_complexity_policy"] = policy.model_dump_json()
        if prev_state is not None:
            adjustment = compute_boundary_adjustment(prev_state, policy)
            if adjustment is not None:
                config["config_complexity_adjustment"] = adjustment
    except Exception as exc:  # noqa: BLE001 - governance must not break routing
        if os.environ.get("DEBUG") or debug:
            print(f"DEBUG complexity governor: {exc}", file=sys.stderr)

    if lane_notice:
        config["config_lane_notice"] = lane_notice
    if lane_env:
        config["config_lane_env"] = lane_env

    # A malformed or unknown human slash command yields no valid task; keep its
    # usage hint on the config so _build_result can turn the terminal skip into
    # an actionable notice (issue #804). Bot bodies are excluded so the engine's
    # own notices can never re-enter this path.
    if command_parse.status == "malformed" and not is_bot:
        config["config_command_error"] = command_parse.usage or ""

    result = _build_result(config, prompt_base, workflows_dir, local_workflows_dir=local_workflows_dir)
    if os.environ.get("DEBUG") or debug:
        print(f"DEBUG _build_result: {result}", file=sys.stderr)
    return result


def main():
    import nebuko.engine.lib.default_paths as default_paths

    parser = argparse.ArgumentParser(
        description="Resolve agent configuration from trigger body"
    )
    parser.add_argument(
        "--trigger-body",
        default="-",
        help="Path to trigger body file, or '-' for stdin",
    )
    parser.add_argument(
        "--prompt-base",
        default=default_paths.config_dir("prompts", "PROMPT_BASE"),
        help="Path to prompt files directory",
    )
    parser.add_argument(
        "--workflows-dir",
        default=default_paths.config_dir("workflows", "WORKFLOWS_DIR"),
        help="Path to workflows directory",
    )
    parser.add_argument(
        "--local-workflows-dir",
        default=None,
        help="Consumer-local workflows dir taking precedence over "
        "--workflows-dir (issue #529)",
    )
    parser.add_argument(
        "--is-bot-comment",
        choices=["true", "false"],
        default=None,
        help="Pre-determine bot comment status",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Path to GITHUB_ENV file to append results",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Repository in owner/name format (for fetching comments)",
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        default=None,
        help="Issue/PR number (for fetching comments)",
    )

    args = parser.parse_args()

    # Read trigger body
    if args.trigger_body == "-":
        trigger_body = sys.stdin.read()
    else:
        with open(args.trigger_body, "r", encoding="utf-8") as f:
            trigger_body = f.read()

    is_bot = None
    if args.is_bot_comment == "true":
        is_bot = True
    elif args.is_bot_comment == "false":
        is_bot = False
    
    debug_flag = extract_debug_flag(trigger_body),

    result = resolve_config(
        trigger_body=trigger_body,
        prompt_base=args.prompt_base,
        workflows_dir=args.workflows_dir,
        is_bot=is_bot,
        repo=args.repo,
        issue_number=args.issue_number,
        debug=debug_flag,
        local_workflows_dir=args.local_workflows_dir,
    )

    # Output as KEY=value pairs
    for key, value in result.items():
        print(f"{key}={value}")

    # Append to GITHUB_ENV if specified
    if args.env_file:
        with open(args.env_file, "a") as f:
            for key, value in result.items():
                f.write(f"{key}={value}\n")

    print(
        f"Resolved agent: ROLE_NAME={result.get('ROLE_NAME', '?')} "
        f"COMPLEXITY={result.get('COMPLEXITY', '?')}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
