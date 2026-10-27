"""Resolve configuration facade.

Package-by-feature DDD layout:
  sub_domains/      -- domain value objects (TriggerCommands)
  features/         -- behavioral features (parse_trigger, fetch_state, resolve_route, build_result)
  resolve_config.py -- facade (resolve_config() orchestrator + main() CLI)
"""

import argparse
import os
import sys
from typing import Optional

from .features.build_result.build_result import _build_result
from .features.fetch_state.fetch_state import _fetch_workflow_state_from_history
from .sub_domains.workflow_command.workflow_command import (
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
from .features.resolve_route.resolve_route import (
    build_author_identity_from_env,
    _armed_gate,
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


def resolve_config(
    trigger_body: str,
    prompt_base: str,
    workflows_dir: str,
    is_bot: Optional[bool] = None,
    repo: Optional[str] = None,
    issue_number: Optional[int] = None,
    debug: bool = False,
    local_workflows_dir: Optional[str] = None,
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
    command_intent = classify_comment(trigger_body)
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
    from nebuko.engine.lib import default_paths

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
