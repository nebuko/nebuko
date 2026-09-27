"""Build output feature — workflow state building, split plan handling, and output file writing."""

import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

from openhands.sdk import get_logger
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm.message import content_to_str
from openhands.sdk.tool.builtins.finish import FinishTool
from pydantic import ValidationError

from config import (
    is_self_function_call,
    load_workflow,
    parse_position_env,
    resolve_workflow_reference,
)
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
from agent_script.sub_domains.scaffold_branch.scaffold_branch import (
    extract_scaffold_branch_any,
    scaffold_branch_summary,
    strip_scaffold_branch,
)
from agent_script.sub_domains.message_splitter.message_splitter import split_message
from agent_script.features.extract_recovery_trace.extract_recovery_trace import (
    extract_recovery_trace,
    recovery_trace_to_markdown,
)
from agent_script.features.run_agent.run_failure import RunFailure, RunFailureKind
from agent_script.features.run_agent.run_outcome import OutcomeStatus, RunOutcome
from agent_script.features.ensure_pr.ensure_pr import (
    EnsurePrResult,
    ensure_pr,
)
from agent_script.features.dispatch_next_tool import (
    clear_run,
    get_recorded_dispatch,
)
from agent_script.features.scaffold_branch_tool.scaffold_branch_tool import (
    clear_scaffold_branch_run,
    get_recorded_scaffold_branch,
)

logger = get_logger(__name__)

# Artifact file holding the agent's latest full answer (issue #522).
# Written by the capture layer in build_output(); consumed by the ETL layer
# in prepare_comment.py as the preferred posted-comment body.
LATEST_ANSWER_FILE = "latest_answer.md"

# Tool names whose turns carry the agent's text in ``ActionEvent.thought``
# instead of a standalone ``MessageEvent`` (issue #553/#555). Agents place
# their full deliverable on the same turn as these control-plane calls.
FINISH_TOOL_NAME = FinishTool.name
DISPATCH_NEXT_TOOL_NAME = "dispatch_next"


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


_UNREACHABLE_RESUME_HINT = (
    "**To resume the workflow once the provider is reachable, "
    "post this comment:**"
)
_GENERIC_RESUME_HINT = "**To resume the workflow, post this comment:**"

_GENERIC_FAILURE_SUMMARY = (
    "> \u26a0\ufe0f **The agent run failed before producing any output.** "
    "Check the full logs in the workflow run for details."
)


def _resume_hint(kind: RunFailureKind) -> str:
    """Recovery instruction tailored to the failure kind."""
    if kind is RunFailureKind.LLM_UNREACHABLE:
        return _UNREACHABLE_RESUME_HINT
    return _GENERIC_RESUME_HINT


def _render_failure_detail(detail: str) -> list[str]:
    """Render the raw exception text inside a collapsed block, if any."""
    if not detail:
        return []
    return [
        "",
        "<details><summary>Technical detail</summary>",
        "",
        f"`{detail}`",
        "",
        "</details>",
    ]


def _build_failure_summary(run_outcome: RunOutcome) -> str:  # @stub-protected
    """Synthesize a final message for a FAILURE run that produced none.

    Names the cause of the failure — e.g. an unreachable LLM API provider —
    and prints the command that resumes the workflow once the provider is
    reachable again (issue #668).

    Args:
        run_outcome: The observed run outcome, carrying the RunFailure.

    Returns:
        Markdown summary for the terminal comment.
    """
    failure = run_outcome.failure or RunFailure(
        kind=RunFailureKind.UNKNOWN,
        summary=_GENERIC_FAILURE_SUMMARY,
        detail="",
    )
    lines = [
        failure.summary,
        "",
        _resume_hint(failure.kind),
        "",
        "```",
        failure.resume_command,
        "```",
    ]
    lines.extend(_render_failure_detail(failure.detail))
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


def _parse_position_env() -> Optional[dict]:
    """Parse the flat v3 position from the WORKFLOW_POSITION env var.

    The resolver emits a single-line v3 JSON (``WorkflowPosition.
    to_state_dict`` shape) when the run is inside (or on) a sub-workflow
    (issue #638; legacy v2 JSON is converted by the same reader). Returns
    the block for ``build_state``, or ``None`` when the var is
    unset/empty/malformed (v1 states stay position-less). Delegates to the
    shared ``config.parse_position_env`` so the script path
    (``run_script``) behaves identically.
    """
    return parse_position_env(os.environ.get("WORKFLOW_POSITION", ""))


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


def _terminal_turn_answer(event, tool_name: str) -> str:
    """Return the answer text of a terminal (control-plane) turn's action event.

    When the LLM emits the full answer and a ``finish`` / ``dispatch_next``
    tool call in the same turn (the issue #553/#555 regression path), the SDK
    stores the text in ``ActionEvent.thought`` and emits no standalone
    ``MessageEvent``. The action's own arguments are never consulted: under
    the posting-contract skill the ``finish`` message is a short status note
    and the ``dispatch_next`` reason is one-sentence routing metadata, while
    the turn's text carries the full deliverable. Action events of any other
    tool return ``""`` so mid-run commentary attached to other tool calls is
    never mistaken for the answer.
    """
    if not isinstance(event, ActionEvent) or event.tool_name != tool_name:
        return ""
    return "".join(content_to_str(event.thought)).strip()


def extract_latest_answer(events: list) -> str:
    # @stub-protected: signature defined by architect — do not modify
    """Extract the agent's latest full text answer from conversation events.

    Contract (capture layer, issue #522; regression fixes for issues #553
    and #555):
      * Return the text of the LAST assistant ``MessageEvent`` whose text
        content is non-empty after stripping (latest-wins: each newer
        answer replaces the previous one).
      * Additionally consider the text of the agent's ``finish`` and
        ``dispatch_next`` turns (carried in ``ActionEvent.thought``): when
        the full answer and one of those tool calls share a turn, the SDK
        emits no standalone ``MessageEvent`` for it — previously such
        answers were lost and posting fell back to the short finish
        message. Agents commonly place their full deliverable on the same
        turn as the mandatory pre-finish ``dispatch_next`` call.
      * All other tool actions are ignored — their accompanying text is
        mid-run commentary, not a deliverable. Agents may call these
        tools mid-flow or emit a final status text after them; answers are
        considered in order.
      * Return ``""`` when no agent text answer exists; callers then fall
        back to the finish message (byte-identical legacy behavior).

    Args:
        events: Conversation event list (``conversation.state.events``).

    Returns:
        The latest agent text answer, or ``""`` if none.
    """
    # Scan forward with latest-wins: every newer agent answer replaces the
    # previous one, so the last non-empty answer is kept. Finish and
    # dispatch_next turns are candidates like any other answer — under the
    # posting-contract skill agents may post the deliverable before calling
    # them, and the report must still be captured then.
    latest = ""
    for event in events or []:
        if not isinstance(event, (MessageEvent, ActionEvent)):
            continue
        if isinstance(event, MessageEvent):
            if event.source != "agent":
                continue
            text = "".join(content_to_str(event.llm_message.content)).strip()
        else:
            text = (
                _terminal_turn_answer(event, FINISH_TOOL_NAME)
                or _terminal_turn_answer(event, DISPATCH_NEXT_TOOL_NAME)
            )
        if text:
            latest = text
    return latest


def _recall_aware_events(events: list, run_outcome) -> list:
    """Exclude engine-injected recall-turn events from answer selection (#558).

    When ``run_agent`` recorded a pre-recall event offset, everything from
    that index onward belongs to the recall pass — its short routing/status
    text must not overwrite the agent's real deliverable. Without a recall
    the full list is returned unchanged (byte-identical legacy behavior,
    keeping the #553/#555 regression suite green).

    Dispatch resolution intentionally does NOT use this: the recall turn is
    exactly where the missing dispatch decision is supplied.
    """
    offset = (
        run_outcome.dispatch_recall_event_offset
        if run_outcome is not None
        else None
    )
    if offset is None:
        return events
    logger.info(
        f"Recall-aware capture: excluding {len(events) - offset} "
        f"post-recall events (offset={offset})"
    )
    return events[:offset]


def _write_latest_answer_file(text: str) -> None:
    """Persist the latest-answer artifact consumed by prepare_comment.py.

    Writes ``text`` to ``LATEST_ANSWER_FILE`` in the current working
    directory. An empty string still writes the (empty) file so the ETL
    layer distinguishes "no answer captured" via content, not presence.
    """
    with open(LATEST_ANSWER_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info("Wrote latest answer artifact (%d chars)", len(text))

def _merge_sentinel_routing_fields(
    tool_dispatch: Dispatch, final_message: str
) -> Dispatch:
    """Merge branch/workflow/step from a co-emitted sentinel into a winning
    tool dispatch (issue #618, defense-in-depth).

    The tool call owns ``status``/``complexity``/``task_hint`` (issue #524
    precedence is preserved). When the tool's schema left a routing field
    unset (``None``), fill it in from the co-emitted sentinel so the two
    channels are complementary instead of the tool silently truncating a
    richer sentinel. A non-``None`` value the tool explicitly set is never
    overridden.
    """
    if not final_message:
        return tool_dispatch
    extract_result = extract_dispatch_resilient(final_message)
    if extract_result.payload is None:
        return tool_dispatch
    sentinel_dispatch = agent_run_config_to_dispatch(extract_result.payload)
    if sentinel_dispatch is None:
        return tool_dispatch

    updates = {}
    for field in ("branch", "workflow", "step"):
        tool_value = getattr(tool_dispatch, field)
        sentinel_value = getattr(sentinel_dispatch, field)
        if tool_value is None and sentinel_value is not None:
            updates[field] = sentinel_value
    if not updates:
        return tool_dispatch
    logger.info(f"Merged sentinel routing fields into tool dispatch: {updates}")
    return tool_dispatch.model_copy(update=updates)


def _resolve_dispatch(
    conversation, final_message: str
) -> tuple[Optional[Dispatch], Optional[FunctionCall], str]:
    """Resolve the run's dispatch decision from the structured signal first.

    Resolution priority (issue #524):
      1. ``dispatch_next`` tool call — a boolean check on structured data,
         immune to long-context format drops (the failure mode of the
         free-text sentinel).
      2. Legacy ``<!-- AGENT_RUN_CONFIG ... -->`` sentinel — silent compat
         fallback during the dual-mode migration window.
      3. Nothing — dispatch/function_call stay None.

    When the tool call wins, ``branch``/``workflow``/``step`` left unset by
    the tool are merged in from a co-emitted sentinel (issue #618) so the
    richer sentinel can no longer be silently truncated.

    When the tool call names an explicit ``agent`` target, a matching
    ``FunctionCall(call_agent)`` is also populated so the state's
    ``function_call`` field stays filled for every downstream routing path.

    Sentinels are always stripped from ``final_message`` so they never leak
    into the visible comment body.
    """
    dispatch: Optional[Dispatch] = None
    function_call: Optional[FunctionCall] = None

    conv_id = getattr(conversation, "id", None)
    tool_dispatch = get_recorded_dispatch(conv_id) if conv_id is not None else None

    if tool_dispatch is not None:
        dispatch = _merge_sentinel_routing_fields(tool_dispatch, final_message)
        # Explicit cross-agent targeting flows through function_call in the
        # routing chain; keep that field populated when the tool names a target.
        # The shared helper validates the name against the agent roster and
        # logs a warning on typos (issue #524).
        if dispatch.task_hint:
            # Lazy import: dispatch_check imports from this module at top
            # level, so a module-level import here would be circular.
            from agent_script.features.dispatch_check import (
                agent_to_function_call,
            )

            function_call = FunctionCall(**agent_to_function_call(dispatch.task_hint))
        logger.info(
            f"Dispatch resolved via dispatch_next tool: status={dispatch.status}, "
            f"agent={dispatch.task_hint or '-'}"
        )
    elif final_message:
        if has_unterminated_agent_run_config(final_message):
            logger.warning("Unterminated AGENT_RUN_CONFIG sentinel detected (truncated output)")
        extract_result = extract_dispatch_resilient(final_message)
        if extract_result.payload is not None:
            dispatch = agent_run_config_to_dispatch(extract_result.payload)
            # The extractor only returns payloads it already json-parsed,
            # so this re-parse cannot fail (valid-JSON guarantee).
            function_call = extract_function_call(json.loads(extract_result.payload))
            if dispatch is not None:
                logger.info(
                    "Dispatch resolved via legacy AGENT_RUN_CONFIG sentinel "
                    "(dispatch_next tool not called)"
                )
        elif extract_result.parse_error is not None:
            # A sentinel marker was present but no payload could be parsed
            # (issue #512): log an explicit diagnostic instead of failing
            # silently with dispatch=function_call=None.
            logger.warning(
                f"AGENT_RUN_CONFIG sentinel found but could not be parsed: "
                f"{extract_result.parse_error}"
            )

    # Strip AGENT_RUN_CONFIG sentinels so they don't appear in the visible
    # comment body — dispatch is stored structurally in workflow_state.dispatch.
    final_message = strip_agent_run_config(final_message)
    return dispatch, function_call, final_message


def build_output(
    conversation,
    metrics: Metrics,
    args=None,
    prev_state: WorkflowState | None = None,
    split_plan_json: str | None = None,
    scaffold_branch: str | None = None,
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
        scaffold_branch: Pre-extracted scaffold branch ref (issue #604), or
            None to extract the SCAFFOLD_BRANCH marker from the message.

    Returns:
        Dict with keys: 'workflow_state', 'split_plan_json', 'final_message',
        'scaffold_branch'.
    """
    # ── Final response ───────────────────────────────────────────
    final_message = get_agent_final_response(conversation.state.events)

    # ── Latest-answer capture (issue #522) ────────────────────────
    # The full deliverable is the agent's last text answer; the finish
    # message remains the status note used for dispatch/summary below.
    # Recall-aware capture (issue #558): events after the recorded
    # pre-recall offset belong to the engine-injected recall turns and are
    # excluded so their short routing/status text cannot clobber the
    # pre-recall deliverable. Dispatch resolution below intentionally
    # keeps the FULL event stream: the recall turn is exactly where the
    # missing dispatch decision is supplied, and hiding it would
    # re-introduce the stall.
    # The file write is deferred until after SCAFFOLD_BRANCH stripping
    # (issue #636) so the persisted answer contains clean text.
    run_outcome = _get_run_outcome(conversation)
    latest_answer = extract_latest_answer(
        _recall_aware_events(conversation.state.events, run_outcome)
    )

    # ── Scaffold branch marker (issue #604; dual-source since #636) ─
    # The marker contract targets the agent's latest answer, but the
    # finish message is checked first to preserve #604 behavior. A
    # malformed marker in one source never blocks a valid marker in
    # the other. Extracted before build_state so the value can be
    # persisted this run.
    # (None when pre-extracted by the caller; otherwise extracted here.)
    if scaffold_branch is None:
        # Primary source: the report_scaffold_branch tool registry (validated
        # at call time by the executor). Marker parsing stays as fallback so
        # legacy/in-flight runs keep working (issue #636).
        tool_branch = get_recorded_scaffold_branch(
            getattr(conversation, "id", None)
        )
        result = extract_scaffold_branch_any(
            final_message, latest_answer, tool_branch=tool_branch
        )
        scaffold_branch = result.branch
        if scaffold_branch:
            logger.info(
                f"SCAFFOLD_BRANCH persisted from {result.source}: {scaffold_branch}"
            )
        elif "SCAFFOLD_BRANCH:" in (final_message or "") or "SCAFFOLD_BRANCH:" in (
            latest_answer or ""
        ):
            # Marker present but malformed in every source — warn, never
            # crash the run.
            logger.warning("SCAFFOLD_BRANCH marker found but is malformed; not persisted")

    # ── Graceful timeout handling (issue #489) ────────────────────
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

    # ── Failure handling (issue #668) ─────────────────────────────
    # A run that crashed with no output would otherwise reach the opaque
    # "__NO_FINAL_MESSAGE__" sentinel. Sanity: never override a real answer.
    is_failure = (
        run_outcome is not None
        and run_outcome.status is OutcomeStatus.FAILURE
        and not final_message
    )
    if is_failure:
        logger.info("Failure detected — synthesizing failure summary")
        final_message = _build_failure_summary(run_outcome)

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
    position = _parse_position_env()
    phase = _derive_phase(workflow_step)

    # ── Issue / PR info ──────────────────────────────────────────
    issue_info = _build_issue_info()
    pr_info = _build_pr_info()
    resolution = _build_resolution()

    # ── Extract dispatch + function_call from agent output ─────────
    dispatch, function_call, final_message = _resolve_dispatch(
        conversation, final_message
    )

    # Self function_call neutralization (issue #618, defect 2a): a self
    # hand-off (agent dispatches to itself) must not outrank the plain
    # dispatch / workflow-next routing. Drop the function_call and keep
    # the plain dispatch so the router falls through to workflow-next.
    if function_call is not None and is_self_function_call(function_call, role_name):
        logger.info(
            f"Self function_call neutralized (agent={role_name}); "
            f"keeping plain dispatch for workflow-next routing"
        )
        function_call = None

    # Clear the run-scoped dispatch registry on every exit path (issue #524):
    # build_output runs exactly once per agent run, so this must not leak into
    # the next run in a long-lived process.
    clear_run(getattr(conversation, "id", None))
    clear_scaffold_branch_run(getattr(conversation, "id", None))

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
        scaffold_branch=scaffold_branch,
        position=position,
    )
    logger.info(f"Built workflow state v{new_state.version} for step={workflow_step}")

    # ── Split plan handling ──────────────────────────────────────
    if split_plan_json is None:
        split_plan_json = extract_split_plan(final_message) if final_message else None

    if split_plan_json:
        _save_split_plan(split_plan_json)
        final_message = _replace_split_plan_with_summary(final_message, split_plan_json)

    # ── Scaffold branch marker strip (issue #604; #636) ──────────
    # Strip the raw marker from both the posted comment and the
    # latest-answer file so users never see it. The summary line
    # replaces a stripped marker — when the branch came from the
    # report_scaffold_branch tool (no marker in the prose), there is
    # nothing to strip and nothing to append.
    if scaffold_branch:
        marker_in_prose = (
            "SCAFFOLD_BRANCH:" in (final_message or "")
            or "SCAFFOLD_BRANCH:" in (latest_answer or "")
        )
        latest_answer = strip_scaffold_branch(latest_answer)
        final_message = strip_scaffold_branch(final_message)
        if marker_in_prose and final_message:
            final_message = final_message.rstrip() + f"\n\n{scaffold_branch_summary(scaffold_branch)}\n"
        logger.info(f"Persisted scaffold_branch={scaffold_branch}")

    _write_latest_answer_file(latest_answer)

    # ── Message splitting ────────────────────────────────────────
    # ctx reports are posted as a single collapsed comment (issue #557):
    # raise the split threshold to the 60k comment cap so they fit in one
    # part; other agents keep the 10k multi-part split.
    max_split_length = 60000 if role_name == "ctx" else 10000
    message_parts = split_message(final_message, max_split_length) if final_message else []

    # ── Write output files ───────────────────────────────────────
    _write_output_files(final_message, message_parts, metrics, new_state)

    logger.info("Task completed successfully")

    return {
        "workflow_state": new_state,
        "split_plan_json": split_plan_json,
        "scaffold_branch": scaffold_branch,
        "final_message": final_message,
    }
