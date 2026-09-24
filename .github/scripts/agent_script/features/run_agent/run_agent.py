"""Run agent feature — Agent/Conversation creation and supervised execution.

The conversation runs via the async ``arun()`` API so that the soft-timeout
watchdog can call ``conversation.interrupt()``, which cancels the in-flight
task mid-LLM-call. When the soft budget elapses the agent is asked (via a
wind-down prompt) to conclude its work instead of being hard-killed by the
GitHub Actions timeout (issue #489).
"""

import asyncio
import dataclasses
import os

from openhands.sdk import Agent, Conversation, LLM, get_logger
from openhands.sdk.conversation import get_agent_final_response
from openhands.tools.preset.default import get_default_condenser
from openhands.sdk.tool.spec import Tool
from openhands.sdk.tool.registry import register_tool

from agent_script.features.dispatch_check import has_dispatch_decision
from agent_script.features.run_agent.dispatch_recall import (
    MAX_DISPATCH_RECALLS,
    RECALL_PASS_BUDGET_S,
    perform_dispatch_recalls,
)
from agent_script.features.run_agent.run_outcome import (
    OutcomeStatus,
    RunOutcome,
    _STATUS_FINISHED,
    classify_run,
    status_value,
)
from agent_script.features.run_agent.run_watchdog import RunWatchdog
from agent_script.features.run_agent.timeout_budget import (
    TimeoutBudget,
    resolve_timeout_budget,
)
from agent_script.features.run_agent.wind_down_prompt import WIND_DOWN_PROMPT

logger = get_logger(__name__)

# SDK sentinel for the built-in security policy (agent/base.py).
_DEFAULT_SECURITY_POLICY = "security_policy.j2"

# Module-level guard: register_tool logs a warning on re-registration, so in
# long-lived processes (or repeated test runs) we register only once.
_dispatch_tool_registered = False


def _ensure_dispatch_tool_registered() -> None:
    """Register the ``dispatch_next`` ToolDefinition with the SDK registry.

    Idempotent — safe to call on every agent run.
    """
    global _dispatch_tool_registered
    if _dispatch_tool_registered:
        return
    from agent_script.features.dispatch_next_tool.dispatch_next_tool import (
        DispatchNextTool,
    )

    register_tool("DispatchNextTool", DispatchNextTool)
    _dispatch_tool_registered = True


# Module-level guard: register_tool logs a warning on re-registration, so in
# long-lived processes (or repeated test runs) we register only once.
_scaffold_branch_tool_registered = False


def _ensure_scaffold_branch_tool_registered() -> None:
    """Register the ``report_scaffold_branch`` ToolDefinition with the SDK registry.

    Idempotent — safe to call on every agent run.
    """
    global _scaffold_branch_tool_registered
    if _scaffold_branch_tool_registered:
        return
    from agent_script.features.scaffold_branch_tool.scaffold_branch_tool import (
        ScaffoldBranchReportTool,
    )

    register_tool("ScaffoldBranchReportTool", ScaffoldBranchReportTool)
    _scaffold_branch_tool_registered = True


def _resolve_security_policy() -> str:
    """Route skills through the system channel; fall back to the SDK default.

    ``SKILL_CONTEXT_FILE`` (populated by CI) points at the concatenated skill
    content. When present and non-empty it is passed as
    ``security_policy_filename`` so the SDK inserts the skills verbatim into
    the system prompt — higher authority than the old user-prompt appendix.
    """
    skill_file = os.environ.get("SKILL_CONTEXT_FILE", "")
    if skill_file and os.path.isfile(skill_file) and os.path.getsize(skill_file) > 0:
        logger.info(f"Injecting skills via system channel: {skill_file}")
        return skill_file
    return _DEFAULT_SECURITY_POLICY


def _run_supervised(
    conversation: Conversation,
    budget_s: float,
    label: str,
) -> tuple[RunWatchdog, Exception | None]:
    """Run one bounded pass of ``arun()`` under a watchdog.

    The watchdog interrupts the conversation when ``budget_s`` elapses.
    Returns the watchdog (so the caller can check whether it fired) and the
    exception raised by ``arun()`` — if any — so the caller can tell a hard
    failure apart from a natural completion or a watchdog interrupt.
    """
    watchdog = RunWatchdog(budget_s, conversation.interrupt).arm()
    error: Exception | None = None
    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(conversation.arun())
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"{label} raised: {e}")
        error = e
    finally:
        watchdog.cancel()
    return watchdog, error


def _perform_wind_down(
    conversation: Conversation,
    budget: TimeoutBudget,
) -> bool:
    """Inject the wind-down prompt and run one bounded second pass.

    A short watchdog still guards this pass (so a hung LLM call cannot
    consume the GitHub Actions hard timeout), but there is no recursion —
    if it trips we rely on the deterministic PR fallback instead.

    Returns:
        True if the wind-down completed without tripping its own budget.
    """
    logger.info("Soft timeout reached — starting graceful wind-down phase")
    conversation.send_message(WIND_DOWN_PROMPT)
    wind_down_watchdog, _ = _run_supervised(
        conversation, budget.wind_down_timeout_s, "Wind-down run"
    )
    completed = not wind_down_watchdog.fired
    if not completed:
        logger.warning(
            "Wind-down budget also exhausted — relying on deterministic PR fallback"
        )
    return completed


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
    dispatch_tool_enabled = False

    for tool_name in agent_tools:
        if tool_name in sdk_tool_names:
            sdk_tools.append(Tool(name=tool_name))
        elif tool_name in builtin_tool_map:
            include_default_tools.append(builtin_tool_map[tool_name])
        elif tool_name == "dispatch_next":
            _ensure_dispatch_tool_registered()
            sdk_tools.append(Tool(name="DispatchNextTool"))
            dispatch_tool_enabled = True
        elif tool_name == "report_scaffold_branch":
            _ensure_scaffold_branch_tool_registered()
            sdk_tools.append(Tool(name="ScaffoldBranchReportTool"))
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
        security_policy_filename=_resolve_security_policy(),
        condenser=get_default_condenser(llm=llm),
    )

    # ── Conversation setup & supervised execution ────────────────
    conversation = Conversation(
        agent=agent,
        workspace=cwd,
    )

    budget = resolve_timeout_budget()
    logger.info("Starting task execution...")
    logger.info(f"Prompt: {prompt[:200]}...")
    logger.info(
        f"Soft timeout budget: {budget.soft_timeout_s:.0f}s "
        f"(wind-down: {budget.wind_down_timeout_s:.0f}s)"
    )

    conversation.send_message(prompt)
    first_watchdog, main_error = _run_supervised(
        conversation, budget.soft_timeout_s, "Main run"
    )

    soft_timeout_fired = first_watchdog.fired
    main_status = status_value(conversation)

    wind_down_completed = False
    if soft_timeout_fired and main_status != _STATUS_FINISHED:
        # Our watchdog interrupted the run mid-flight: ask the agent to
        # conclude its work while the conversation history is still live.
        # The overall outcome stays "gracefully_timed_out" whether or not
        # the wind-down itself completed; the deterministic PR fallback
        # (see ensure_pr) covers the uncooperative case.
        wind_down_completed = _perform_wind_down(conversation, budget)
        outcome = OutcomeStatus.GRACEFULLY_TIMED_OUT
    elif main_error is not None:
        # A non-cancellation crash escaped arun(): whatever status the
        # conversation reports, this is a hard failure — never classify
        # it as success.
        logger.error("Main run crashed — classifying as failure")
        outcome = OutcomeStatus.FAILURE
    else:
        outcome = classify_run(main_status, False)

    run_outcome = RunOutcome(
        status=outcome,
        soft_timeout_fired=soft_timeout_fired,
        wind_down_completed=wind_down_completed,
    )

    # ── Bounded dispatch recall (issue #524) ────────────────────
    # The exact failure mode this fixes: the agent finished its turn but
    # never made a next-step decision (no dispatch_next call, no sentinel).
    # Since that is now programmatically detectable, ask the model for the
    # cognitive decision — a short focused turn, max MAX_DISPATCH_RECALLS.
    dispatch_recall_count = 0
    if dispatch_tool_enabled and outcome is not OutcomeStatus.FAILURE:
        # Shared with build_output._resolve_dispatch so the two can't drift:
        # a parseable legacy sentinel counts as a decision (dual-mode window),
        # not just a recorded dispatch_next call.
        def _has_dispatch() -> bool:
            return has_dispatch_decision(
                conversation,
                get_agent_final_response(conversation.state.events),
            )

        def _run_recall_pass(n: int) -> bool:
            logger.warning(
                f"Run ended without a dispatch_next call — recall "
                f"{n}/{MAX_DISPATCH_RECALLS}"
            )
            watchdog, _ = _run_supervised(
                conversation, RECALL_PASS_BUDGET_S, f"Dispatch recall {n}"
            )
            if watchdog.fired:
                logger.warning(
                    f"Dispatch recall {n} exhausted its budget — giving up"
                )
            return not watchdog.fired

        # Capture the pre-recall event boundary BEFORE any recall pass:
        # events from this index onward belong to engine-injected recall
        # turns and must not overwrite the agent's real deliverable at
        # latest-answer capture time (issue #558). Only meaningful when a
        # recall actually fires — the count gate below keeps the
        # no-recall path byte-identical to the legacy behavior.
        pre_recall_offset = len(conversation.state.events)
        dispatch_recall_count = perform_dispatch_recalls(
            conversation, _has_dispatch, _run_recall_pass
        )
    if dispatch_recall_count:
        run_outcome = dataclasses.replace(
            run_outcome,
            dispatch_recall_count=dispatch_recall_count,
            dispatch_recall_event_offset=pre_recall_offset,
        )

    # Attach to the conversation so build_output can consume it without a
    # signature change.
    conversation.run_outcome = run_outcome  # type: ignore[attr-defined]

    logger.info(
        f"Run outcome: {outcome.value} "
        f"(soft_timeout_fired={run_outcome.soft_timeout_fired}, "
        f"wind_down_completed={run_outcome.wind_down_completed})"
    )

    return conversation
