#!/usr/bin/env python3
"""
Resolve agent configuration from trigger body.

Replaces the 223-line 'Resolve Agent Configuration' bash block.
Extracts agentRunConfig JSON, parses fields, routes via resolve_workflow.py,
and outputs environment variables.

Usage:
    python resolve_config.py \\
        --trigger-body <file|-> \\
        --prompt-base <path> \\
        --workflows-dir <path> \\
        --is-bot-comment <true|false>
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

# Import resolve_workflow directly (same directory)
sys.path.insert(0, os.path.dirname(__file__))
from config import load_workflow
from resolve_workflow import resolve_workflow_routing, TASK_TO_PROMPT

# Regex for safe workflow names: alphanumeric, hyphens, underscores only
_SAFE_WF_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def extract_agent_run_config(trigger_body: str) -> Optional[dict]:
    """Extract agentRunConfig JSON block from trigger body text.

    First tries the structured <agentRunConfig> block.
    Falls back to trailing JSON object with "task" field.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Parsed dict or None.
    """
    # Strategy 1: <agentRunConfig> JSON block
    match = re.search(r"<agentRunConfig>(.*?)</agentRunConfig>", trigger_body, re.DOTALL)
    if match:
        json_str = match.group(1).strip().replace("\n", "")
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # Strategy 2: Trailing JSON object with valid task field
    task_names = r"(?:issue-reviewer|ctx|genius|dev|review|issue)"
    match = re.search(
        r"\{[^{}]*\"task\"\s*:\s*\"" + task_names + r"\"[^{}]*\}",
        trigger_body,
    )
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def is_bot_comment(trigger_body: str) -> bool:
    """Check if trigger body starts with a bot-agent marker.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        True if first 5 lines contain <!-- bot-agent-.
    """
    first_lines = "\n".join(trigger_body.split("\n")[:5])
    return "<!-- bot-agent-" in first_lines


def extract_mention_agent(trigger_body: str) -> Optional[str]:
    """Extract the last @@mention agent name from trigger body.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Agent name string or None.
    """
    agents = re.findall(
        r"@@(?:issue-reviewer|genius|ctx|dev|review|issue)",
        trigger_body,
    )
    if agents:
        return agents[-1].replace("@@", "")
    return None


def extract_workflow_command(trigger_body: str) -> Optional[str]:
    """Extract /workflow command from the first line of trigger body.

    Only matches if the first non-empty line starts with "/workflow <name>".
    Returns the workflow name or None.

    Args:
        trigger_body: Raw trigger body text.

    Returns:
        Workflow name string or None.
    """
    first_line = trigger_body.split("\n", 1)[0].strip()
    match = re.match(r"^/workflow\s+(\S+)", first_line)
    if match:
        return match.group(1)
    return None


def resolve_config(
    trigger_body: str,
    prompt_base: str,
    workflows_dir: str,
    is_bot: Optional[bool] = None,
) -> dict:
    """Resolve complete agent configuration from trigger body.

    This replaces the 223-line bash block. Steps:
    1. Extract agentRunConfig JSON
    2. Bot detection
    3. Parse config or fallback to @@mention
    4. Route via resolve_workflow.py or case-based fallback
    5. Build result dict with all env vars

    Args:
        trigger_body: Raw trigger body text.
        prompt_base: Path to prompt files directory.
        workflows_dir: Path to workflows directory.
        is_bot: Pre-determined bot status (None = auto-detect).

    Returns:
        Dict with all environment variables to set.
    """
    if is_bot is None:
        is_bot = is_bot_comment(trigger_body)

    # Step 1: Extract agentRunConfig JSON
    agent_config = extract_agent_run_config(trigger_body)

    if agent_config:
        # JSON-based invocation (agent→agent)
        task = agent_config.get("task", "")
        config_complexity = agent_config.get("complexity", "medium")
        config_reasoning = agent_config.get("reasoning", "none")
        config_temperature = agent_config.get("temperature", "")
        config_round = agent_config.get("round", "")
        config_model = agent_config.get("model", "")
        config_skills = agent_config.get("skills", "")
        config_top_p = agent_config.get("top_p", "")
        config_top_k = agent_config.get("top_k", "")
        config_repetition_penalty = agent_config.get("repetition_penalty", "")
        config_workflow = agent_config.get("workflow", "")
        config_step = agent_config.get("step", "")
        config_branch = agent_config.get("branch", "")
        config_parent_workflow = agent_config.get("parent_workflow", "")
        config_parent_workflow_step = agent_config.get("parent_workflow_step", "")
    elif not is_bot:
        # /workflow command normalization (human only): first line "/workflow <name>"
        wf_name = extract_workflow_command(trigger_body)
        wf_resolved = False
        task = ""

        if wf_name and _SAFE_WF_NAME_RE.match(wf_name):
            wf_file = Path(workflows_dir) / f"{wf_name}.yaml"
            try:
                wf_def = load_workflow(str(wf_file))
                task = wf_def.trigger.agents[0] if wf_def.trigger.agents else ""
                if task:
                    wf_resolved = True
            except FileNotFoundError:
                print(
                    f"Warning: workflow '{wf_name}' not found at {wf_file}, "
                    f"falling back to @@mention",
                    file=sys.stderr,
                )
            except ValueError as e:
                print(
                    f"Warning: workflow '{wf_name}' invalid ({e}), "
                    f"falling back to @@mention",
                    file=sys.stderr,
                )

        if not wf_resolved:
            # Fall through to @@mention if /workflow was invalid/unresolvable
            task = extract_mention_agent(trigger_body) or ""
            wf_name = None

        config_complexity = "medium"
        config_reasoning = "none"
        config_temperature = ""
        config_round = ""
        config_model = ""
        config_skills = ""
        config_top_p = ""
        config_top_k = ""
        config_repetition_penalty = ""
        config_workflow = wf_name if wf_name and wf_resolved else ""
        config_step = ""
        config_branch = ""
        config_parent_workflow = ""
        config_parent_workflow_step = ""
    else:
        # Bot comment without agentRunConfig — skip
        return {"SKIP_AGENT": "true", "REASON": "Bot comment without agentRunConfig"}

    if not task:
        return {"SKIP_AGENT": "true", "REASON": "No valid task found"}

    # wait_for_human is an intentional stop — skip gracefully
    if task == "wait_for_human":
        return {
            "SKIP_AGENT": "true",
            "REASON": "Intentional wait_for_human (no automated next step)",
            "TASK": task,
        }

    # Validate task
    if task not in TASK_TO_PROMPT:
        return {"SKIP_AGENT": "true", "REASON": f"Unknown task: {task}"}

    # Step 5: Workflow-aware routing
    wf_result = resolve_workflow_routing(
        {
            "task": task,
            "complexity": config_complexity,
            "reasoning": config_reasoning,
            "model": config_model,
            "temperature": config_temperature,
            "round": config_round,
            "skills": config_skills,
            "top_p": config_top_p,
            "top_k": config_top_k,
            "repetition_penalty": config_repetition_penalty,
            "workflow": config_workflow,
            "step": config_step,
            "branch": config_branch,
            "parent_workflow": config_parent_workflow,
            "parent_workflow_step": config_parent_workflow_step,
        },
        workflows_dir,
        prompt_base,
    )

    if wf_result.get("SKIP_AGENT") == "true":
        return wf_result

    role_name = wf_result["ROLE_NAME"]
    prompt_location = wf_result["PROMPT_LOCATION"]

    # Determine complexity for dev/ctx agents
    complexity = "N/A"
    if role_name in ("dev", "ctx"):
        comp_lower = config_complexity.lower() if config_complexity else "medium"
        complexity = comp_lower if comp_lower in ("low", "high") else "medium"

    # Build result
    result = {
        "TASK": task,
        "ROLE_NAME": role_name,
        "PROMPT_LOCATION": prompt_location,
        "AGENT_SELECTED": "true",
        "COMPLEXITY": complexity,
        "LLM_REASONING": config_reasoning,
        "AGENT_ROUND": config_round or "",
        "AGENT_SKILLS": config_skills or "",
        "CONFIG_MODEL": config_model or "",
        "CONFIG_TEMPERATURE": config_temperature or "",
        "CONFIG_TOP_P": config_top_p or "",
        "CONFIG_TOP_K": config_top_k or "",
        "CONFIG_REPETITION_PENALTY": config_repetition_penalty or "",
        "WORKFLOW_NAME": wf_result.get("WORKFLOW_NAME", ""),
        "WORKFLOW_STEP": wf_result.get("WORKFLOW_STEP", ""),
        "WORKFLOW_NEXT": wf_result.get("WORKFLOW_NEXT", ""),
        "PARENT_WORKFLOW": wf_result.get("PARENT_WORKFLOW", ""),
        "PARENT_WORKFLOW_STEP": wf_result.get("PARENT_WORKFLOW_STEP", ""),
        "SKIP_AGENT": "false",
    }

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

    result = resolve_config(
        trigger_body=trigger_body,
        prompt_base=args.prompt_base,
        workflows_dir=args.workflows_dir,
        is_bot=is_bot,
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
