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

from .features.team_context.team_context import build_team_env, load_agent_skills
from .features.build_result.build_result import _build_result
from .features.fetch_state.fetch_state import _fetch_workflow_state_from_history
from .features.parse_trigger.parse_trigger import (
    is_bot_comment,
    extract_mention_agent,
    extract_workflow_command,
    extract_force_next_command,
    extract_resume_command,
    extract_debug_flag,
    _parse_trigger_body,
)
from .features.resolve_route.resolve_route import (
    _resolve_resume_path,
    _resolve_dispatch_path,
    _resolve_human_path,
    _resolve_bot_fallback_path,
    _resolve_workflow_next_step,
)


def _find_agent_config() -> Optional[str]:
    """Locate agent-config.yaml from the resolver's own location.

    The resolver is invoked from a variety of working directories
    (``.github/scripts``, the repo root, caches), so a plain CWD-relative
    lookup is not portable. Walk up from this file to the nearest
    ``agent-config.yaml``; fall back to the CWD name.

    Returns:
        Resolved path string, or None when no config file is found.
    """
    from pathlib import Path

    current = Path(__file__).resolve().parent
    for _ in range(10):
        candidate = current / "agent-config.yaml"
        if candidate.is_file():
            return str(candidate)
        parent = current.parent
        if parent == current:
            break
        current = parent
    fallback = Path("agent-config.yaml")
    return str(fallback) if fallback.is_file() else None


def _fail_fast_validate_roster(prompt_base: str, skills_dir: Optional[str] = None) -> None:
    """Validate the agent roster at resolve time and exit non-zero on errors.

    Checks every agent's prompt_file exists, tools are known, skill files
    exist, and each agent has a role. Fail fast so a bad config never runs.

    Args:
        prompt_base: Directory containing the prompt files.
        skills_dir: Directory containing ``<skill>.md`` files. Defaults to the
            ``skills`` directory next to the resolved agent-config.yaml
            (the CWD is not reliable — the resolver runs from various places).
    """
    try:
        from pathlib import Path
        from config import (
            load_config,
            build_agent_roster,
            validate_roster,
        )
    except ImportError as e:
        print(f"Warning: roster validation skipped (import failed: {e})", file=sys.stderr)
        return

    # Only validate when the config file is present; otherwise the resolver
    # already falls back to the static roster, so hard-failing here would
    # wrongly skip a legitimate run in stripped-down/check-less environments.
    config_path = _find_agent_config()
    if config_path is None:
        print("Warning: agent-config.yaml not found; skipping roster validation",
              file=sys.stderr)
        return

    # Anchor the skills directory to the config file's location (repo root)
    # instead of the CWD.
    if skills_dir is None:
        skills_dir = str(Path(config_path).resolve().parent / "skills")

    try:
        config = load_config(config_path)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: could not parse agent-config.yaml for validation: {e}", file=sys.stderr)
        sys.exit(1)

    agents = config.agents or {}
    roster = build_agent_roster(config)
    roles = {name: getattr(agent, "role", None) for name, agent in agents.items()}
    tools_by_agent = {name: list(getattr(agent, "tools", []) or []) for name, agent in agents.items()}
    skills_by_agent = {name: list(getattr(agent, "skills", []) or []) for name, agent in agents.items()}

    problems = validate_roster(
        roster,
        roles=roles,
        tools_by_agent=tools_by_agent,
        skills_by_agent=skills_by_agent,
        prompt_base=prompt_base,
        skills_dir=skills_dir,
    )
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        sys.exit(1)

def resolve_config(
    trigger_body: str,
    prompt_base: str,
    workflows_dir: str,
    is_bot: Optional[bool] = None,
    repo: Optional[str] = None,
    issue_number: Optional[int] = None,
    debug: bool = False
) -> dict:
    """Resolve complete agent configuration from trigger body.

    Orchestrates helper methods to determine agent task and parameters:
    1. Parse trigger body for workflow state and dispatch (_parse_trigger_body).
    2. Bot detection (if not pre-determined).
    3. If no state in body, fetch from issue comment history.
    4. Route based on what was found (human commands take precedence):
       - State + dispatch → _resolve_dispatch_path.
       - /workflow force-next-step → _resolve_human_path (regardless of state).
       - /workflow command by human → _resolve_human_path.
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
    if resume_cmd is not None and prev_state and prev_state.workflow and \
       prev_state.dispatch and prev_state.dispatch.status == "hold":
        # /workflow resume — re-run the held step with human input
        config = _resolve_resume_path(prev_state, trigger_body, workflows_dir)
    elif resume_cmd is not None and prev_state and prev_state.workflow and \
         (prev_state.dispatch is None):
        # /workflow resume with no dispatch — advance to next step, carrying
        # human input forward (issue #420: resolution-only state).
        human_input = trigger_body.replace("/workflow resume", "").strip()
        config = _resolve_workflow_next_step(prev_state, dispatch_obj, workflows_dir)
        config["config_human_resume_input"] = human_input
    elif force_next:
        # Human explicit step jump — takes priority over auto-dispatch.
        # Must go through _resolve_human_path for validation/error handling.
        config = _resolve_human_path(
            trigger_body, repo, issue_number, workflows_dir, prev_state
        )
    elif wf_override and not is_bot and resume_cmd is None:
        # Human /workflow override — use human path to honor the command.
        # Guard: /workflow resume sets both resume_cmd ("") and wf_override ("resume"),
        # but should NOT go through the override path. Check is None since
        # extract_resume_command returns "" (empty, not None) for bare resume.
        config = _resolve_human_path(
            trigger_body, repo, issue_number, workflows_dir, prev_state
        )
    elif human_mention:
        # Human @@mention (e.g. @@ctx) takes priority over auto-dispatch
        # so standalone agent calls work even when a workflow is active.
        config = _resolve_human_path(trigger_body, repo, issue_number, workflows_dir)
    elif prev_state and dispatch_obj:
        # Auto-dispatch: workflow state with dispatch object — follow the workflow.
        config = _resolve_dispatch_path(prev_state, dispatch_obj, workflows_dir)
    elif prev_state and prev_state.workflow:
        config = _resolve_workflow_next_step(prev_state, dispatch_obj, workflows_dir)
    elif not is_bot:
        config = _resolve_human_path(trigger_body, repo, issue_number, workflows_dir)
    else:
        config = _resolve_bot_fallback_path(trigger_body, workflows_dir)

    if config.get("SKIP_AGENT") == "true":
        return config

    result = _build_result(config, prompt_base, workflows_dir)

    # Export team context (roster roles, tool whitelist, caller marker) so the
    # workflow can inject the shared "Your Team" + exit-rules section into the
    # agent prompt (issue #476). Degrades to empty values when the roster is
    # unavailable; no-op when the run is skipped.
    if result.get("SKIP_AGENT") != "true" and "ROLE_NAME" in result:
        result.update(build_team_env(trigger_body, role_name=result["ROLE_NAME"]))
        # Flow the agent's default skills (agent-config.yaml) into AGENT_SKILLS
        # so the workflow's skill-file plumbing loads them by default (issue
        # #476). An explicit per-run/step skill list (from the dispatch
        # payload) takes precedence.
        if not result.get("AGENT_SKILLS"):
            result["AGENT_SKILLS"] = load_agent_skills(result["ROLE_NAME"])

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

    # Fail fast: validate the agent roster (prompt files, tools, skills, roles)
    _fail_fast_validate_roster(args.prompt_base, skills_dir="skills")

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
        debug=debug_flag
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
