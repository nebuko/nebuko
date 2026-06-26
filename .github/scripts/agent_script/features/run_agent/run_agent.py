"""Run agent feature — Agent/Conversation creation and execution."""

import os

from openhands.sdk import Agent, Conversation, LLM, get_logger
from openhands.tools.preset.default import get_default_condenser
from openhands.sdk.tool.spec import Tool

logger = get_logger(__name__)


def run_agent(
    llm_config: dict,
    prompt: str,
    args=None,
    agent_cfg=None,
) -> Conversation:
    """
    Create an agent, start a conversation, send a prompt, and run the agent.

    Args:
        llm_config: Dictionary of LLM configuration parameters.
        prompt: The prompt string to send to the agent.
        args: Parsed argparse namespace (unused, retained for compatibility).
        agent_cfg: Optional AgentConfig object for tool resolution.
                   If None, default tool list is used.

    Returns:
        The completed Conversation object.
    """
    llm = LLM(**llm_config)

    cwd = os.getcwd()

    role_name = os.environ.get("ROLE_NAME", "unknown")

    # ── Tool resolution ──────────────────────────────────────────
    default_tool_list = ["terminal", "file_editor", "task_tracker", "think", "finish"]
    agent_tools = agent_cfg.tools if agent_cfg is not None else default_tool_list

    sdk_tool_names = {"terminal", "file_editor", "task_tracker"}
    builtin_tool_map = {"think": "ThinkTool", "finish": "FinishTool"}

    sdk_tools = []
    include_default_tools = []

    for tool_name in agent_tools:
        if tool_name in sdk_tool_names:
            sdk_tools.append(Tool(name=tool_name))
        elif tool_name in builtin_tool_map:
            include_default_tools.append(builtin_tool_map[tool_name])
        else:
            logger.warning(f"Unknown tool '{tool_name}' for agent '{role_name}' — skipping")

    logger.info(
        f"Creating {role_name} agent with tools: sdk={[t.name for t in sdk_tools]}, "
        f"builtins={include_default_tools}"
    )

    # ── System prompt config ─────────────────────────────────────
    system_prompt_kwargs = (
        {} if role_name in ("ctx", "dev") else {"cli_mode": True}
    )

    agent = Agent(
        llm=llm,
        tools=sdk_tools,
        include_default_tools=include_default_tools,
        system_prompt_kwargs=system_prompt_kwargs,
        condenser=get_default_condenser(llm=llm),
    )

    # ── Conversation setup & execution ───────────────────────────
    conversation = Conversation(
        agent=agent,
        workspace=cwd,
    )

    logger.info("Starting task execution...")
    logger.info(f"Prompt: {prompt[:200]}...")

    conversation.send_message(prompt)
    conversation.run()

    return conversation
