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
from .features.parse_trigger.parse_trigger import (
    is_bot_comment,
    has_universal_bot_marker,
    is_plain_mention_trigger,
    extract_plain_mention_agent,
    extract_mention_agent,
    extract_workflow_command,
    extract_force_next_command,
    extract_resume_command,
    normalize_resume_input,
    extract_debug_flag,
    extract_machine_trigger,
    _parse_trigger_body,
)
from .features.resolve_route.resolve_route import (
    _resolve_resume_path,
    _resolve_dispatch_path,
    _resolve_function_call_path,
    _resolve_machine_trigger_path,
    _resolve_human_path,
    _resolve_bot_fallback_path,
    _resolve_workflow_next_step,
)

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
       - /workflow resume → _resolve_resume_path / next step.
       - /workflow force-next-step → _resolve_human_path (regardless of state).
       - /workflow command by human → _resolve_human_path.
       - Human @@mention, or CI plain-mention trigger (universal bot marker
         + mention-only body, e.g. sub-task completion notification) →
         _resolve_human_path (parity between the two: the mention wins over
         an active dispatch).
       - State + dispatch → _resolve_dispatch_path.
       - State without dispatch → _resolve_workflow_next_step.
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
    if is_bot is None:
        is_bot = is_bot_comment(trigger_body)

    # Parse trigger body for workflow state and dispatch
    prev_state, dispatch_obj = _parse_trigger_body(trigger_body)

    # Check for human commands early — they take precedence over workflow state
    # regardless of whether prev_state exists (they need to resolve the command)
    force_next = extract_force_next_command(trigger_body)
    resume_cmd = extract_resume_command(trigger_body)
    wf_override = extract_workflow_command(trigger_body)

    # Centralized fallback: if no state from current body, try comment history
    if not prev_state:
        prev_state = _fetch_workflow_state_from_history(repo, issue_number)
        if prev_state:
            dispatch_obj = prev_state.dispatch

    if os.environ.get("DEBUG") or debug:
        print(f"DEBUG prev_state: {prev_state}", file=sys.stderr)

    # Route to appropriate handler based on what was found
    # Human commands (explicit intent) MUST be evaluated before auto-dispatch,
    # otherwise an active workflow's dispatch object absorbs all commands.
    # Check for human commands early (independent of is_bot) so they take
    # priority even when prev_state+dispatch exist.
    human_mention = not is_bot and extract_mention_agent(trigger_body)
    # CI-posted command triggers (sub-issue bootstrap "@@genius", sub-task
    # completion notification) now carry the universal bot marker and so
    # classify as bot. They are plain mention-only triggers (no prose after
    # the mention) that must still route to the mentioned agent, even when
    # an active workflow dispatch exists in the comment history — matching
    # the pre-marker behavior where their bare mention set human_mention.
    # Only the universal marker qualifies: legacy bot-agent- comments keep
    # their historical routing (skip unless they embed state/dispatch).
    ci_mention = has_universal_bot_marker(trigger_body) and is_plain_mention_trigger(
        trigger_body
    )
    if resume_cmd is not None and prev_state and prev_state.workflow and \
       prev_state.dispatch and prev_state.dispatch.status == "hold":
        # /workflow resume — re-run the held step with human input
        config = _resolve_resume_path(
            prev_state, trigger_body, workflows_dir, local_workflows_dir=local_workflows_dir
        )
    elif resume_cmd is not None and prev_state and prev_state.workflow and \
         (prev_state.dispatch is None):
        # /workflow resume with no dispatch — advance to next step, carrying
        # human input forward (issue #420: resolution-only state). A bare
        # resume maps to the BARE_RESUME_INPUT sentinel so the human_gate
        # interceptor (issue #506) treats this run as human-triggered.
        human_input = normalize_resume_input(resume_cmd)
        config = _resolve_workflow_next_step(prev_state, dispatch_obj, workflows_dir, local_workflows_dir=local_workflows_dir)
        config["config_human_resume_input"] = human_input
    elif force_next:
        # Human explicit step jump — takes priority over auto-dispatch.
        # Must go through _resolve_human_path for validation/error handling.
        # prompt_base lets the force-next branch resolve sub-workflow targets
        # (issue #599).
        config = _resolve_human_path(
            trigger_body, repo, issue_number, workflows_dir, prev_state,
            local_workflows_dir=local_workflows_dir, prompt_base=prompt_base,
        )
    elif wf_override and not is_bot and resume_cmd is None:
        # Human /workflow override — use human path to honor the command.
        # Guard: /workflow resume sets both resume_cmd ("") and wf_override ("resume"),
        # but should NOT go through the override path. Check is None since
        # extract_resume_command returns "" (empty, not None) for bare resume.
        config = _resolve_human_path(
            trigger_body, repo, issue_number, workflows_dir, prev_state,
            local_workflows_dir=local_workflows_dir,
        )
    elif human_mention or ci_mention:
        # Human @@mention (e.g. @@ctx) takes priority over auto-dispatch
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
    elif prev_state and prev_state.function_call:
        # Imperative agent-to-agent hand-off (call_agent): explicit pinning of
        # the next agent, beats workflow continuation (same tier as human
        # mention). Consumed once — the fresh state built for the called agent
        # clears the field, so a re-trigger no-ops.
        config = _resolve_function_call_path(prev_state.function_call, workflows_dir, prev_state, local_workflows_dir=local_workflows_dir)
    elif extract_machine_trigger(trigger_body):
        # CI-posted machine trigger token (bot-subtask-complete /
        # bot-summarizer-trigger / bot-trigger) — replaces the legacy
        # machine-posted @@genius / @@summarize comments (issue #478).
        config = _resolve_machine_trigger_path(trigger_body, workflows_dir)
    elif prev_state and dispatch_obj:
        # Auto-dispatch: workflow state with dispatch object — follow the workflow.
        config = _resolve_dispatch_path(prev_state, dispatch_obj, workflows_dir, local_workflows_dir=local_workflows_dir)
    elif prev_state and prev_state.workflow:
        config = _resolve_workflow_next_step(prev_state, dispatch_obj, workflows_dir, local_workflows_dir=local_workflows_dir)
    elif not is_bot:
        config = _resolve_human_path(
            trigger_body, repo, issue_number, workflows_dir,
            local_workflows_dir=local_workflows_dir,
        )
    else:
        config = _resolve_bot_fallback_path(trigger_body, workflows_dir, local_workflows_dir=local_workflows_dir)

    if config.get("SKIP_AGENT") == "true":
        return config

    result = _build_result(config, prompt_base, workflows_dir, local_workflows_dir=local_workflows_dir)
    if os.environ.get("DEBUG") or debug:
        print(f"DEBUG _build_result: {result}", file=sys.stderr)
    return result


def main():
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
        default=".agents/prompts",
        help="Path to prompt files directory",
    )
    parser.add_argument(
        "--workflows-dir",
        default="workflows",
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
