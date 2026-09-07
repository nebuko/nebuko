"""Build output feature — workflow state building, split plan handling, and output file writing."""

import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

from openhands.sdk import get_logger
from openhands.sdk.conversation import get_agent_final_response
from pydantic import ValidationError

from config import load_workflow, resolve_workflow_reference
from metrics_schema import Metrics
from validate_output import (
    extract_dispatch_resilient,
    has_unterminated_agent_run_config,
    strip_agent_run_config,
)
from workflow_state import (
    Dispatch,
    FunctionCall,
    IssueInfo,
    PullRequestInfo,
    StepResolution,
    WorkflowState,
)
from workflow_state import extract_function_call
from workflow_state.features.build_state.build_state import build_state
from workflow_state.features.to_json.to_json import state_to_json

from agent_script.sub_domains.split_plan.split_plan import (
    extract_split_plan,
    strip_split_plan,
    split_plan_summary,
)
from agent_script.sub_domains.message_splitter.message_splitter import split_message
from agent_script.features.extract_recovery_trace.extract_recovery_trace import (
    extract_recovery_trace,
    recovery_trace_to_markdown,
)
from agent_script.features.run_agent.run_outcome import OutcomeStatus
from agent_script.features.ensure_pr.ensure_pr import (
    EnsurePrResult,
    ensure_pr,
)

logger = get_logger(__name__)


# Tag patterns whose imbalance indicates a truncated tool-call block.
_MALFORMED_TAG_PAIRS: list[tuple[str, str]] = [
    ("<antThinking>", "</antThinking>"),
    ("<thinking>", "</thinking>"),
    ("<tool_call>", "</tool_call>"),
    ("<tool_code>", "</tool_code>"),
    ("<function_calls>", "</function_calls>"),
]


def is_malformed_final_message(message: str) -> bool:
    """Return True if the message contains unbalanced tag pairs.

    Checks for common XML-style delimiters used by agents during tool calls.
    An unbalanced pair indicates the response was likely truncated mid-block.

    Args:
        message: The final message extracted from the agent response.

    Returns:
        True if any tag pair is unbalanced, False otherwise.
    """
    for open_tag, close_tag in _MALFORMED_TAG_PAIRS:
        if message.count(open_tag) != message.count(close_tag):
            return True
    return False


def _get_run_outcome(conversation):
    """Read the RunOutcome attached to the conversation by run_agent (or None)."""
    return getattr(conversation, "run_outcome", None)


def _build_timeout_summary(pr_result: EnsurePrResult | None) -> str:
    """Synthesize a final message for a graceful timeout that produced none."""
    lines = [
        "_⏱ The agent reached its soft time budget and execution was "
        "gracefully interrupted (issue #489). It was asked to conclude its "
        "work but did not produce a closing summary._",
        "",
        "**Checkpoint:** pending changes were committed and pushed, and a PR "
        "was opened or updated with the current content. The next run will "
        "resume from the recovery log instead of starting over.",
    ]
    if pr_result is not None:
        if pr_result.pr_url:
            lines.append(f"- Checkpoint PR: {pr_result.pr_url}")
        if pr_result.error:
            lines.append(f"- PR fallback issue: {pr_result.message}")
        elif pr_result.skipped:
            lines.append(f"- PR fallback skipped: {pr_result.message}")
    return "\n".join(lines)


def agent_run_config_to_dispatch(json_str: str) -> Optional[Dispatch]:
    """Convert an AGENT_RUN_CONFIG JSON string to a Dispatch object.

    Maps AGENT_RUN_CONFIG payload fields (task, complexity, etc.) to Dispatch
    (task_hint, complexity, etc.). Defaults status to 'advance'.

    Args:
        json_str: Raw JSON string from an AGENT_RUN_CONFIG sentinel.

    Returns:
        Dispatch instance, or None if parsing or validation fails.
    """
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    # Auto-unwrap nested {"dispatch": {...}} structure (agents emit this shape)
    if "dispatch" in data and isinstance(data["dispatch"], dict):
        data.update(data["dispatch"])

    # Map 'task' -> 'task_hint', default status to 'advance'
    try:
        return Dispatch(
            status=data.get("status", "advance"),
            task_hint=data.get("task"),
            complexity=data.get("complexity", "medium"),
            reasoning=data.get("reasoning", "none"),
            model=data.get("model"),
            temperature=data.get("temperature"),
            top_p=data.get("top_p"),
            top_k=data.get("top_k"),
            min_p=data.get("min_p"),
            presence_penalty=data.get("presence_penalty"),
            repetition_penalty=data.get("repetition_penalty"),
            workflow=data.get("workflow"),
            step=data.get("step"),
            branch=data.get("branch"),
            parent_workflow=data.get("parent_workflow"),
            parent_workflow_step=data.get("parent_workflow_step"),
            skills=data.get("skills"),
        )
    except ValidationError as e:
        logger.warning(f"AGENT_RUN_CONFIG payload failed Dispatch validation: {e}")
        return None


def _load_gated_step_def(
    workflow_name: str,
    workflow_step: str,
) -> bool:
    """Return True if the given step is defined with human_gate: true.

    Loads the workflow YAML via the config package. Any missing/invalid
    reference (no workflow name, missing file, parse error) degrades to
    False — the gate must never crash the run, and a broken YAML should
    fall back to legacy auto-advance rather than fail the whole post-step.
    """
    if not workflow_name or not workflow_step:
        return False
    try:
        workflows_dir = os.environ.get("WORKFLOWS_DIR", "workflows")
        ref = resolve_workflow_reference(workflow_name, workflows_dir)
        wf_def = load_workflow(str(ref))
    except Exception as e:
        logger.warning(f"Human gate check: could not load workflow '{workflow_name}': {e}")
        return False
    for step in wf_def.steps:
        if step.name == workflow_step:
            return bool(step.human_gate)
    return False


def apply_human_gate_intercept(
    dispatch: Optional[Dispatch],
    workflow_name: str,
    workflow_step: str,
    is_human_resume: bool,
) -> Optional[Dispatch]:
    """Enforce the human_gate security boundary (issue #506).

    A step marked ``human_gate: true`` in the workflow YAML may never advance
    on its own: an ``advance`` dispatch (or missing dispatch, which resolves
    to advance downstream) is overridden to ``hold`` unless the run was
    triggered by a human ``/workflow resume`` (``is_human_resume``). Loop,
    hold, and terminate outcomes are never intercepted.

    Args:
        dispatch: Dispatch extracted from the agent output (may be None).
        workflow_name: Active workflow name (WORKFLOW_NAME env).
        workflow_step: Step just executed (WORKFLOW_STEP env).
        is_human_resume: True when the run was triggered by /workflow resume.

    Returns:
        The (possibly overridden) Dispatch.
    """
    if dispatch is not None and dispatch.status != "advance":
        return dispatch
    if is_human_resume:
        return dispatch
    if not _load_gated_step_def(workflow_name, workflow_step):
        return dispatch
    logger.info(
        f"Human gate: step '{workflow_step}' requires approval — "
        f"intercepting advance as hold"
    )
    if dispatch is None:
        dispatch = Dispatch()
    return dispatch.model_copy(
        update={"status": "hold", "reason": f"human_gate:{workflow_step}"}
    )


def _load_prev_state() -> WorkflowState | None:
    """Load previous workflow state from disk, or return None."""
    prev_state_path = os.path.join(
        os.environ.get("RUNNER_TEMP", "/tmp"), "prev_workflow_state.json"
    )
    if not os.path.isfile(prev_state_path):
        return None
    try:
        with open(prev_state_path, "r", encoding="utf-8") as f:
            return WorkflowState.model_validate(json.load(f))
    except Exception as e:
        logger.warning(f"Failed to load previous state: {e}")
        return None


def _derive_phase(workflow_step: str) -> str:
    """Derive phase name from workflow step using keyword matching."""
    if not workflow_step:
        return ""
    tokens = set(re.split(r"[_\-]", workflow_step.lower()))
    if tokens & {"analyze", "context"}:
        return "analysis"
    if "plan" in tokens:
        return "planning"
    if tokens & {"stub", "human_approval"}:
        return "stub"
    if "implement" in tokens:
        return "execution"
    if "review" in tokens:
        return "review"
    return ""


def _build_issue_info() -> IssueInfo | None:
    """Build IssueInfo from environment variables."""
    issue_number_str = os.environ.get("TARGET_NUMBER")
    issue_url = os.environ.get("ISSUE_URL")
    issue_title = os.environ.get("ISSUE_TITLE")
    issue_author = os.environ.get("ISSUE_AUTHOR")
    issue_repo = os.environ.get("REPO_NAME")

    if not (issue_number_str and issue_url and issue_title):
        return None

    return IssueInfo(
        number=int(issue_number_str),
        url=issue_url,
        title=issue_title,
        author=issue_author or "",
        repo=issue_repo or "",
    )


def _build_pr_info() -> PullRequestInfo | None:
    """Build PullRequestInfo from environment variables."""
    pr_number_str = os.environ.get("PR_NUMBER")
    pr_url = os.environ.get("PR_URL")
    pr_title = os.environ.get("PR_TITLE")
    pr_author = os.environ.get("PR_AUTHOR")
    pr_repo = os.environ.get("REPO_NAME")

    if not (pr_number_str and pr_url and pr_title):
        return None

    return PullRequestInfo(
        number=int(pr_number_str),
        url=pr_url,
        title=pr_title,
        author=pr_author or "",
        repo=pr_repo or "",
    )


def _build_resolution() -> StepResolution:
    """Build StepResolution from resolve_config environment variables."""
    return StepResolution(
        task=os.environ.get("TASK", ""),
        input_workflow=os.environ.get("WORKFLOW_NAME", ""),
        input_step=os.environ.get("WORKFLOW_STEP", ""),
        input_complexity=os.environ.get("COMPLEXITY", ""),
        role_name=os.environ.get("ROLE_NAME", ""),
        prompt_location=os.environ.get("PROMPT_LOCATION", ""),
        agent_selected=os.environ.get("AGENT_SELECTED", "false"),
        selected_workflow=os.environ.get("WORKFLOW_NAME", ""),
        selected_step=os.environ.get("WORKFLOW_STEP", ""),
        next_step=os.environ.get("WORKFLOW_NEXT", ""),
        skip_agent=os.environ.get("SKIP_AGENT", "false"),
        config_model=os.environ.get("CONFIG_MODEL", ""),
        config_temperature=os.environ.get("CONFIG_TEMPERATURE", ""),
        config_top_p=os.environ.get("CONFIG_TOP_P", ""),
        config_top_k=os.environ.get("CONFIG_TOP_K", ""),
        config_repetition_penalty=os.environ.get("CONFIG_REPETITION_PENALTY", ""),
        agent_round=os.environ.get("AGENT_ROUND", ""),
        agent_skills=os.environ.get("AGENT_SKILLS", ""),
        step_skills=os.environ.get("STEP_SKILLS", ""),
        reasoning=os.environ.get("LLM_REASONING", ""),
        matched_rule=os.environ.get("MATCHED_RULE", ""),
        parent_workflow=os.environ.get("PARENT_WORKFLOW", ""),
        parent_workflow_step=os.environ.get("PARENT_WORKFLOW_STEP", ""),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _save_split_plan(json_str: str) -> None:
    """Save split plan JSON to file."""
    with open("split_plan.json", "w", encoding="utf-8") as f:
        f.write(json_str)
    logger.info("Saved split_plan.json for workflow extraction")


def _replace_split_plan_with_summary(message: str, json_str: str) -> str:
    """Strip <splitPlan> from message and replace with human-readable summary."""
    summary = split_plan_summary(json_str)
    stripped = strip_split_plan(message)
    if summary:
        stripped = stripped.rstrip() + f"\n\n{summary}\n"
    logger.info("Stripped <splitPlan> from comment, replaced with summary")
    return stripped


def _write_output_files(final_message: str, message_parts: list[str], metrics: Metrics, new_state: WorkflowState) -> None:
    """Write final message parts, workflow state JSON, and metrics to output files."""
    MAX_MESSAGE_LENGTH = 10000

    if not message_parts:
        logger.warning("No final agent message found in events")
        with open("workflow_state.json", "w", encoding="utf-8") as f:
            f.write(state_to_json(new_state))
        metrics_json = metrics.to_json(compact=True)
        sentinel = f"__NO_FINAL_MESSAGE__\n<!-- agent-metrics: {metrics_json} -->"
        with open("final_message.txt", "w", encoding="utf-8") as f:
            f.write(sentinel)
        logger.info("Saved sentinel + metrics comment (no final message)")
        return

    metrics_json = metrics.to_json(compact=True)
    metrics_comment = f"\n\n<!-- agent-metrics: {metrics_json} -->"

    # Add part indicators for multi-part messages
    if len(message_parts) > 1:
        total = len(message_parts)
        for i in range(total):
            message_parts[i] = f"*(Part {i + 1}/{total})*\n\n{message_parts[i]}"

    # Write part 1
    with open("final_message.txt", "w", encoding="utf-8") as f:
        f.write(message_parts[0])
        if len(message_parts) == 1:
            f.write(metrics_comment)
    logger.info(f"Saved first message part ({len(message_parts[0])} characters)")

    # Save workflow state
    with open("workflow_state.json", "w", encoding="utf-8") as f:
        f.write(state_to_json(new_state))

    # Write additional parts if split
    if len(message_parts) > 1:
        with open("final_message_parts_count.txt", "w", encoding="utf-8") as f:
            f.write(str(len(message_parts)))

        for i, part in enumerate(message_parts[1:], start=2):
            filename = f"final_message_part_{i}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(part)
                if i == len(message_parts):
                    f.write(metrics_comment)
            logger.info(f"Saved message part {i} ({len(part)} characters)")

        logger.info(f"Message split into {len(message_parts)} parts (was {len(final_message)} characters)")
    else:
        logger.info(f"Final agent message saved ({len(message_parts[0])} characters, no splitting needed)")


def build_output(
    conversation,
    metrics: Metrics,
    args=None,
    prev_state: WorkflowState | None = None,
    split_plan_json: str | None = None,
) -> dict:
    """
    Build workflow state and write output files.

    Orchestrates: final response extraction, phase derivation, issue/PR info
    building, WorkflowState creation, split plan handling, message splitting,
    and output file writing.

    Args:
        conversation: The completed OpenHands Conversation object.
        metrics: Collected Metrics object.
        args: Parsed argparse namespace (unused, retained for compatibility).
        prev_state: Previous WorkflowState, or None to load from disk.
        split_plan_json: Pre-extracted split plan JSON, or None to extract from message.

    Returns:
        Dict with keys: 'workflow_state', 'split_plan_json', 'final_message'.
    """
    # ── Final response ───────────────────────────────────────────
    final_message = get_agent_final_response(conversation.state.events)

    # ── Graceful timeout handling (issue #489) ────────────────────
    run_outcome = _get_run_outcome(conversation)
    is_graceful_timeout = (
        run_outcome is not None
        and run_outcome.status is OutcomeStatus.GRACEFULLY_TIMED_OUT
    )
    pr_result: EnsurePrResult | None = None
    context_updates: dict[str, str] = {}
    if run_outcome is not None:
        context_updates["last_run_status"] = run_outcome.status.value
    if is_graceful_timeout:
        logger.info("Graceful timeout detected — running deterministic PR fallback")
        pr_result = ensure_pr()
        if not final_message:
            # The wind-down did not finish its work — synthesize a clear
            # summary so downstream doesn't treat this as a crashed run.
            final_message = _build_timeout_summary(pr_result)

    # ── Review fix request counting ──────────────────────────────
    review_fix_requests = final_message.count("@@dev fix this") if final_message else 0
    metrics.review_fix_requests = review_fix_requests

    # ── Load previous state if not provided ───────────────────────
    if prev_state is None:
        prev_state = _load_prev_state()

    # ── Environment-derived fields ───────────────────────────────
    role_name = os.environ.get("ROLE_NAME", "")
    workflow_name = os.environ.get("WORKFLOW_NAME", "")
    workflow_step = os.environ.get("WORKFLOW_STEP", "")
    parent_workflow = os.environ.get("PARENT_WORKFLOW", "") or None
    parent_workflow_step = os.environ.get("PARENT_WORKFLOW_STEP", "") or None
    phase = _derive_phase(workflow_step)

    # ── Issue / PR info ──────────────────────────────────────────
    issue_info = _build_issue_info()
    pr_info = _build_pr_info()
    resolution = _build_resolution()

    # ── Extract dispatch + function_call from agent output ─────────
    dispatch: Optional[Dispatch] = None
    function_call: Optional[FunctionCall] = None
    if final_message:
        if has_unterminated_agent_run_config(final_message):
            logger.warning("Unterminated AGENT_RUN_CONFIG sentinel detected (truncated output)")
        extract_result = extract_dispatch_resilient(final_message)
        if extract_result.payload is not None:
            dispatch = agent_run_config_to_dispatch(extract_result.payload)
            # The extractor only returns payloads it already json-parsed,
            # so this re-parse cannot fail (valid-JSON guarantee).
            function_call = extract_function_call(json.loads(extract_result.payload))
        elif extract_result.parse_error is not None:
            # A sentinel marker was present but no payload could be parsed
            # (issue #512): log an explicit diagnostic instead of failing
            # silently with dispatch=function_call=None.
            logger.warning(
                f"AGENT_RUN_CONFIG sentinel found but could not be parsed: "
                f"{extract_result.parse_error}"
            )
        # Strip AGENT_RUN_CONFIG sentinels from final_message so they don't
        # appear in the visible comment body — dispatch is now stored structurally
        # in workflow_state.dispatch instead.
        final_message = strip_agent_run_config(final_message)

    # ── Human gate intercept (issue #506) ────────────────────────
    # The trigger source is the sole authorization: a resume-triggered run
    # (HUMAN_RESUME_INPUT present) passes; anything else on a gated step is
    # held for human review.
    is_human_resume = bool(os.environ.get("HUMAN_RESUME_INPUT", "").strip())
    dispatch = apply_human_gate_intercept(
        dispatch,
        workflow_name=workflow_name,
        workflow_step=workflow_step,
        is_human_resume=is_human_resume,
    )

    # ── Recovery log: capture execution trace on failure ──────────
    if is_graceful_timeout or not final_message or is_malformed_final_message(final_message):
        trace = extract_recovery_trace(conversation)
        if trace:
            recovery_md = recovery_trace_to_markdown(trace)
            context_updates["recovery_log"] = recovery_md
            logger.info(f"Captured recovery trace ({trace.total_tool_calls} tool calls) for next run")
    elif prev_state and prev_state.current_context and "recovery_log" in prev_state.current_context:
        # Agent succeeded — clear stale recovery log so it doesn't persist forward
        context_updates["recovery_log"] = ""
        logger.info("Cleared stale recovery log from previous failed run")

    # ── Build workflow state ─────────────────────────────────────
    inject_context_str = os.environ.get("INJECT_CONTEXT", "true")
    inject_context = inject_context_str.lower() not in ("false", "0", "no")

    new_state = build_state(
        prev_state,
        workflow=workflow_name,
        step=workflow_step,
        agent=role_name,
        phase=phase,
        summary=final_message[:300] if final_message else "",
        metrics_dict=metrics.to_dict(),
        context_updates=context_updates or None,
        parent_workflow=parent_workflow,
        parent_workflow_step=parent_workflow_step,
        issue=issue_info,
        pull_request=pr_info,
        resolution=resolution,
        dispatch=dispatch,
        function_call=function_call,
        inject_context=inject_context,
    )
    logger.info(f"Built workflow state v{new_state.version} for step={workflow_step}")

    # ── Split plan handling ──────────────────────────────────────
    if split_plan_json is None:
        split_plan_json = extract_split_plan(final_message) if final_message else None

    if split_plan_json:
        _save_split_plan(split_plan_json)
        final_message = _replace_split_plan_with_summary(final_message, split_plan_json)

    # ── Message splitting ────────────────────────────────────────
    message_parts = split_message(final_message, 10000) if final_message else []

    # ── Write output files ───────────────────────────────────────
    _write_output_files(final_message, message_parts, metrics, new_state)

    logger.info("Task completed successfully")

    return {
        "workflow_state": new_state,
        "split_plan_json": split_plan_json,
        "final_message": final_message,
    }
