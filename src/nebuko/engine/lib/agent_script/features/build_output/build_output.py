"""Build output feature — workflow state building and output file writing."""

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

from nebuko.engine.engine_tools import DEFAULT_ENGINE_TOOLS

from config import (
    is_self_function_call,
    load_workflow,
    parse_position_env,
    resolve_workflow_reference,
)
from config.sub_domains.agent_run_config.agent_run_config import DispatchConfig
from github_client import get_issue_comments
from metrics_schema import Metrics
from validate_output import (
    extract_dispatch_resilient,
    has_unterminated_agent_run_config,
    strip_agent_run_config,
)
from workflow_state import (
    Dispatch,
    FunctionCall,
    GateDecision,
    IssueInfo,
    PullRequestInfo,
    StepResolution,
    WorkflowState,
    workflow_ref_from_env,
)
from workflow_state import extract_function_call
from workflow_state import extract_latest_state_from_comments
from workflow_state.features.build_state.build_state import build_state
from workflow_state.features.to_json.to_json import state_to_json
from workflow_state.state_codec import WorkflowStateCodec

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
from agent_script.features.call_agent_tool import (
    clear_run as clear_call_agent_run,
    get_recorded_function_call,
)
from agent_script.features.complexity_review_tool import (
    clear_complexity_review_run,
    get_recorded_review_request,
)
from agent_script.features.scaffold_branch_tool.scaffold_branch_tool import (
    clear_scaffold_branch_run,
    get_recorded_scaffold_branch,
)
from complexity_governor.complexity_governor import ComplexityAdjustment
from complexity_governor.integration import ADJUSTMENT_ENV, POLICY_ENV

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


def _soft_kill_dispatch() -> Dispatch:  # @stub-protected
    """Build the soft-kill error dispatch for a gracefully timed-out run (#704).

    A soft time-kill (the ~20 min wind-down) interrupts the running step
    without completing it. The dispatch handed to the workflow engine must
    carry the explicit error status ``soft_kill`` so the router re-runs the
    interrupted step (bounded by ``MAX_SOFT_RETRIES``) instead of advancing to
    the next one. ``build_output`` uses this in place of the agent's own
    dispatch whenever the run outcome is ``OutcomeStatus.GRACEFULLY_TIMED_OUT``.

    Returns:
        A Dispatch with status ``soft_kill`` and the timeout reason.
    """
    return Dispatch(
        status="soft_kill",
        reason=(
            "Soft time-kill: the run was interrupted by the wind-down timeout "
            "before completing the step. Retry this step in place; halt for a "
            "human once MAX_SOFT_RETRIES is exhausted."
        ),
    )


def _parse_gate_decision_env() -> Optional[GateDecision]:
    """Parse the engine-owned gate decision from the ``GATE_DECISION`` env.

    Emitted by the gate-decision resolution in ``resolve_config`` (issue
    #648) as a serialized :class:`GateDecision`. Parsed here so
    ``build_state`` can persist it in workflow state — the decision is
    recorded outside the agent loop and is the only artifact that releases a
    gate. A malformed value is ignored (logged) rather than aborting the run.
    """
    raw = os.environ.get("GATE_DECISION", "").strip()
    if not raw:
        return None
    try:
        return GateDecision.model_validate_json(raw)
    except Exception:
        logger.warning("Ignoring malformed GATE_DECISION env value")
        return None


def _parse_complexity_adjustment_env() -> Optional[ComplexityAdjustment]:
    """Parse the boundary complexity adjustment from ``COMPLEXITY_ADJUSTMENT``.

    Emitted by ``resolve_config`` (issue #869) as a serialized
    :class:`ComplexityAdjustment` computed from run history single source. A
    malformed value is ignored (logged loudly) rather than aborting the run.
    """
    raw = os.environ.get(ADJUSTMENT_ENV, "").strip()
    if not raw:
        return None
    try:
        return ComplexityAdjustment.from_json(raw)
    except ValueError as exc:
        logger.warning(f"Ignoring malformed {ADJUSTMENT_ENV} env value: {exc}")
        return None


def _parse_complexity_policy_env():
    """Parse the pinned complexity policy snapshot from ``COMPLEXITY_POLICY``.

    Emitted by ``resolve_config`` (issue #869) at run start for auditability.
    A malformed value is ignored (logged) — the snapshot is provenance only.
    """
    raw = os.environ.get(POLICY_ENV, "").strip()
    if not raw:
        return None
    try:
        from config import ComplexityPolicy

        return ComplexityPolicy.model_validate_json(raw)
    except Exception:
        logger.warning(f"Ignoring malformed {POLICY_ENV} env value")
        return None


def _no_action_dispatch(diagnostic: str = "", callback: str = "") -> Dispatch:
    """Synthesize the decision-less fallback for a run with no decision (#864).

    An agent that ends its run without emitting any decision (no ``dispatch``
    and no ``function_call``) is contract drift, not a normal completion. When
    the run carries a **return address** (``callback``, i.e. it was reached
    through a caller's ``function_call``) the bounded continuation is to
    ``advance`` back to the caller, which re-enters with the report and decides.
    Only a rudderless run holds for a human.

    The canonical ``DispatchConfig.fallback_for`` factory owns the fallback
    status/precedence; this adapter maps it onto the ``Dispatch`` the workflow
    engine consumes, carrying ``origin='engine_fallback'`` so the synthesized
    decision is distinguishable from an agent-explicit one (#864).
    """
    fallback = DispatchConfig.fallback_for(callback, diagnostic)
    return Dispatch(
        status=fallback.status,
        reason=fallback.reason,
        origin=fallback.origin,
    )


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
            complexity=data.get("complexity") or "",
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
            pin=bool(data.get("pin", False)),
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
        import default_paths

        workflows_dir = default_paths.config_dir("workflows", "WORKFLOWS_DIR")
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
) -> Optional[Dispatch]:
    """Enforce the human_gate security boundary (issues #506, #648).

    A step marked ``human_gate: true`` in the workflow YAML may never advance
    on its own. There is **no** second release path: an ``advance`` dispatch
    (or a missing dispatch, which resolves to advance downstream) is
    overridden to ``hold``, and the only way past the gate is a recorded
    :class:`GateDecision` written by an identity-verified
    ``/nebuko approve gate`` / ``/nebuko reject gate`` command — never by a
    re-invocation of the agent. Loop, hold, and terminate outcomes are never
    intercepted. A ``soft_kill`` dispatch (issue #704) is treated like
    ``advance``: on a gated step the gate wins and the run holds for a human
    instead of auto-retrying. An agent-supplied ``branch`` is inert on a gated
    step — it is cleared so it cannot leak into the held state.

    Args:
        dispatch: Dispatch extracted from the agent output (may be None).
        workflow_name: Active workflow name (WORKFLOW_NAME env).
        workflow_step: Step just executed (WORKFLOW_STEP env).

    Returns:
        The (possibly overridden) Dispatch.
    """
    if dispatch is not None and dispatch.status not in ("advance", "soft_kill"):
        return dispatch
    if not _load_gated_step_def(workflow_name, workflow_step):
        return dispatch
    logger.info(
        f"Human gate: step '{workflow_step}' requires approval — "
        f"intercepting {(dispatch.status if dispatch else 'advance')} as hold"
    )
    if dispatch is None:
        dispatch = Dispatch()
    return dispatch.model_copy(
        update={
            "status": "hold",
            "reason": f"human_gate:{workflow_step}",
            "branch": None,
        }
    )


def _load_prev_state() -> WorkflowState | None:
    """Load previous workflow state from the ``$RUNNER_TEMP`` disk hand-off.

    Disk-only by design so the core builder stays pure and unit-testable. The
    production entry point (``agent_script.main``) supplies the recovered state
    via :func:`load_prev_state_or_recover`, which adds the comments fallback for
    a missing hand-off (issue #671, anomaly B of #648).
    """
    load = WorkflowStateCodec().load_prev()
    if load.status == "ok":
        return load.state
    # A fresh run legitimately has no hand-off; only an unreadable or
    # undecodable file (``invalid``) is worth a warning.
    log = logger.debug if load.status == "absent" else logger.warning
    log(f"Previous workflow state hand-off {load.status}: {load.detail}")
    return None


def load_prev_state_or_recover(fetch_comments=None) -> WorkflowState | None:
    """Load previous state from disk, recovering from issue comments on miss.

    The ``$RUNNER_TEMP`` hand-off written by ``query_context`` is the fast path.
    When it is absent or invalid (e.g. a container boundary hides the file) the
    state is re-extracted from the issue comments — the same authority
    ``query_context`` reads — so a missing hand-off no longer silently resets the
    version/history (issue #671, anomaly B of #648).
    """
    state = _load_prev_state()
    if state is not None:
        return state
    return _load_prev_state_from_comments(fetch_comments)


def _load_prev_state_from_comments(fetch_comments=None) -> WorkflowState | None:
    """Re-extract the latest workflow state from the issue comments."""
    repo = os.environ.get("REPO_NAME", "")
    issue_number = os.environ.get("TARGET_NUMBER", "")
    if not repo or not issue_number.isdigit():
        return None
    try:
        if fetch_comments is not None:
            comments = fetch_comments(repo, int(issue_number))
        else:
            # A bounded tail read (single page) is enough to recover the state
            # without re-paginating the whole thread (issue #805, Pillar C).
            from thread_snapshot import state_tail_limit

            comments = get_issue_comments(
                repo, int(issue_number), limit=state_tail_limit()
            )
        return extract_latest_state_from_comments(comments)
    except Exception as e:
        logger.warning(f"Failed to recover workflow state from comments: {e}")
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


def _write_output_files(final_message: str, message_parts: list[str], new_state: WorkflowState) -> None:
    """Write final message parts and workflow state JSON to output files."""
    if not message_parts:
        logger.warning("No final agent message found in events")
        with open("workflow_state.json", "w", encoding="utf-8") as f:
            f.write(state_to_json(new_state))
        with open("final_message.txt", "w", encoding="utf-8") as f:
            f.write("__NO_FINAL_MESSAGE__\n")
        logger.info("Saved sentinel (no final message)")
        return

    # Add part indicators for multi-part messages
    if len(message_parts) > 1:
        total = len(message_parts)
        for i in range(total):
            message_parts[i] = f"*(Part {i + 1}/{total})*\n\n{message_parts[i]}"

    # Write part 1
    with open("final_message.txt", "w", encoding="utf-8") as f:
        f.write(message_parts[0])
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
            logger.info(f"Saved message part {i} ({len(part)} characters)")

        logger.info(f"Message split into {len(message_parts)} parts (was {len(final_message)} characters)")
    else:
        logger.info(f"Final agent message saved ({len(message_parts[0])} characters, no splitting needed)")


def _terminal_turn_answer(event, tool_name: str) -> str:
    """Return the answer text of a terminal (control-plane) turn's action event.

    When the LLM emits the full answer and a ``finish`` / ``dispatch_next``
    tool call in the same turn (the issue #553/#555 regression path), the SDK
    stores the text in ``ActionEvent.thought`` and emits no standalone
    ``MessageEvent``. Where the deliverable lives differs by tool:

      * ``finish`` — the typed ``message`` argument is the deliverable (issue
        #822/#830: the wind-down contract and the engine-elicited report turn
        put the full report there, while the turn text is a one-line lead-in).
        The turn text is only a fallback for a finish call whose action or
        message is absent/empty (the #553 shape, and ``get_agent_final_response``'s
        own guard).
      * ``dispatch_next`` — its arguments are one-sentence routing metadata, so
        the turn text stands in for the deliverable (the #555 shape).

    Action events of any other tool return ``""`` so mid-run commentary
    attached to other tool calls is never mistaken for the answer.
    """
    if not isinstance(event, ActionEvent) or event.tool_name != tool_name:
        return ""
    if tool_name == FINISH_TOOL_NAME:
        typed = _typed_finish_message(event)
        if typed:
            return typed
    return "".join(content_to_str(event.thought)).strip()


def _typed_finish_message(event) -> str:
    """The typed ``finish.message`` of a terminal turn, or ``""`` (issue #830).

    The control plane is classified declaratively by
    :data:`~nebuko.engine.engine_tools.DEFAULT_ENGINE_TOOLS`: ``finish``'s
    ``message`` argument is the canonical final message (the engine-elicited
    report turn fills it). Reading the typed payload structurally — instead of
    guessing from prose placement — is what makes the posted answer
    deterministic (the #822 truncation bug was a report left in
    ``finish.message`` while the extractor read the one-line turn text).
    """
    if not isinstance(event, ActionEvent) or event.tool_name != FINISH_TOOL_NAME:
        return ""
    payload = DEFAULT_ENGINE_TOOLS.harvest(event.tool_name, getattr(event, "action", None))
    if payload is None or payload.payload_kind != "final_message":
        return ""
    return (payload.fields.get("message") or "").strip()


def extract_latest_answer(events: list) -> str:
    # @stub-protected: signature defined by architect — do not modify
    """Extract the agent's latest full text answer from conversation events.

    Contract (capture layer, issue #522; regression fixes for issues #553,
    #555 and #830):
      * **Latest-wins scan** — the text of the last assistant
        ``MessageEvent`` with non-empty content, or the answer of the agent's
        ``finish`` / ``dispatch_next`` turns. When the full answer and one of
        those tool calls share a turn, the SDK emits no standalone
        ``MessageEvent`` for it — previously such answers were lost and
        posting fell back to the short finish message.
      * **``finish`` turns answer with their typed ``finish.message``
        (issues #822/#830).** The wind-down contract and the engine-elicited
        report turn put the full deliverable in that argument while the turn
        text is a one-line lead-in; reading the argument is what stops the
        posted answer being truncated. The turn text is used only when the
        action or its message is absent/empty.
      * **``dispatch_next`` turns answer with their turn text** — their
        arguments are routing metadata and must never be posted as the answer.
      * All other tool actions are ignored — their accompanying text is
        mid-run commentary, not a deliverable.
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


def extract_standalone_answer(events: list) -> str:
    """The agent's latest standalone text answer (latest-wins), or ``""``.

    Unlike :func:`extract_latest_answer`, control-plane turns are ignored: this
    is the prose channel the scaffold-plan skill historically writes the
    ``SCAFFOLD_BRANCH`` marker on. It is kept as its own source so the marker
    fallback keeps seeing that turn even though ``extract_latest_answer`` now
    prefers a terminator's typed message (issues #822/#636).
    """
    latest = ""
    for event in events or []:
        if not isinstance(event, MessageEvent) or event.source != "agent":
            continue
        text = "".join(content_to_str(event.llm_message.content)).strip()
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

    **Frozen as compatibility-only (issue #876).** Agents no longer emit the
    sentinel; it survives only as a legacy input. Each merge logs a deprecation
    warning — the phase-3 gate metric for removing this path entirely.
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
    logger.warning(
        "[deprecation] merged legacy AGENT_RUN_CONFIG sentinel routing fields "
        f"into the tool dispatch: {updates} (issue #876 — the sentinel is a "
        "compat input only)"
    )
    return tool_dispatch.model_copy(update=updates)


def _typed_function_call(conversation) -> Optional[FunctionCall]:
    """The run's recorded typed ``call_agent`` hand-off, if the tool was called.

    The structured replacement for the sentinel-carried ``function_call``
    (issue #876, PR-2). Returns ``None`` when the tool was not called, so
    resolution falls back to the ``dispatch_next`` target / legacy sentinel.
    """
    conv_id = getattr(conversation, "id", None)
    if conv_id is None:
        return None
    return get_recorded_function_call(conv_id)


def _resolve_dispatch(
    conversation, final_message: str
) -> tuple[Optional[Dispatch], Optional[FunctionCall], str]:
    """Resolve the run's dispatch decision from the structured signal first.

    Resolution priority (issue #524, extended by #876):
      1. ``dispatch_next`` tool call — a boolean check on structured data,
         immune to long-context format drops (the failure mode of the
         free-text sentinel).
      2. Typed ``call_agent`` tool call — an explicit agent-to-agent hand-off
         with no ``dispatch_next`` sibling still *is* a decision (advance to
         the named agent).
      3. Legacy ``<!-- AGENT_RUN_CONFIG ... -->`` sentinel — silent compat
         fallback during the dual-mode migration window.
      4. Neither — a decision-less run: the fallback advances back to the
         registered ``callback`` when there is a return address, else holds
         (issue #765, #864, :func:`_no_action_dispatch`) so the engine never
         auto-advances an unknown state.

    When the tool call wins, ``branch``/``workflow``/``step`` left unset by
    the tool are merged in from a co-emitted sentinel (issue #618) so the
    richer sentinel can no longer be silently truncated.

    The ``function_call`` is typed-first (issue #876): a recorded ``call_agent``
    call wins over the ``dispatch_next`` target, which in turn wins over the
    sentinel-scraped hand-off.

    Sentinels are always stripped from ``final_message`` so they never leak
    into the visible comment body.
    """
    dispatch: Optional[Dispatch] = None
    function_call: Optional[FunctionCall] = None
    diagnostic = ""

    conv_id = getattr(conversation, "id", None)
    tool_dispatch = get_recorded_dispatch(conv_id) if conv_id is not None else None
    typed_function_call = _typed_function_call(conversation)

    if tool_dispatch is not None:
        dispatch = _merge_sentinel_routing_fields(tool_dispatch, final_message)
        # Explicit cross-agent targeting flows through function_call in the
        # routing chain. The typed call_agent tool wins; otherwise the
        # dispatch_next target is promoted to a call_agent hand-off. The shared
        # helper validates the name against the agent roster and logs a warning
        # on typos (issue #524).
        if typed_function_call is not None:
            function_call = typed_function_call
        elif dispatch.task_hint:
            # Lazy import: dispatch_check imports from this module at top
            # level, so a module-level import here would be circular.
            from agent_script.features.dispatch_check import (
                agent_to_function_call,
            )

            # Carry the explicit dispatch tier (issue #787) so a hand-off
            # without one inherits the callee step's declared tier instead of
            # masking it with a hardcoded default.
            function_call = FunctionCall(
                **agent_to_function_call(dispatch.task_hint),
                complexity=dispatch.complexity,
            )
        logger.info(
            f"Dispatch resolved via dispatch_next tool: status={dispatch.status}, "
            f"agent={dispatch.task_hint or '-'}"
        )
    elif typed_function_call is not None:
        # A typed call_agent without a dispatch_next sibling is itself the
        # decision: advance to the named agent (issue #876). The hand-off
        # carries the caller's declared complexity.
        dispatch = Dispatch(
            status="advance",
            task_hint=typed_function_call.agent_name,
            complexity=typed_function_call.complexity or "",
        )
        function_call = typed_function_call
        logger.info(
            "Dispatch resolved via typed call_agent tool: "
            f"agent={typed_function_call.agent_name}"
        )
    elif final_message:
        if has_unterminated_agent_run_config(final_message):
            diagnostic = "unterminated AGENT_RUN_CONFIG sentinel (truncated output)"
            logger.warning("Unterminated AGENT_RUN_CONFIG sentinel detected (truncated output)")
        extract_result = extract_dispatch_resilient(final_message)
        if extract_result.payload is not None:
            dispatch = agent_run_config_to_dispatch(extract_result.payload)
            # The extractor only returns payloads it already json-parsed,
            # so this re-parse cannot fail (valid-JSON guarantee).
            function_call = extract_function_call(json.loads(extract_result.payload))
            if dispatch is not None:
                diagnostic = ""
                logger.warning(
                    "[deprecation] Dispatch resolved via legacy AGENT_RUN_CONFIG "
                    "sentinel (dispatch_next/call_agent tools not called); the "
                    "sentinel is a compat input only (issue #876)"
                )
            else:
                diagnostic = "AGENT_RUN_CONFIG payload failed Dispatch validation"
        elif extract_result.parse_error is not None:
            # A sentinel marker was present but no payload could be parsed
            # (issue #512): capture an explicit diagnostic instead of failing
            # silently with dispatch=function_call=None.
            diagnostic = str(extract_result.parse_error)
            logger.warning(
                f"AGENT_RUN_CONFIG sentinel found but could not be parsed: "
                f"{extract_result.parse_error}"
            )

    # Strip AGENT_RUN_CONFIG sentinels so they don't appear in the visible
    # comment body — dispatch is stored structurally in workflow_state.dispatch.
    final_message = strip_agent_run_config(final_message)

    # No-action default (issue #765, #864): a run that emitted neither a
    # dispatch nor a function_call is contract drift, not a completion. With a
    # return address (``CONFIG_CALLBACK``, set when this run was reached via a
    # caller's function_call) the bounded continuation is to advance back to
    # the caller; a rudderless run holds for a human. Either way the decision
    # is tagged ``engine_fallback``.
    if dispatch is None and function_call is None:
        callback = os.environ.get("CONFIG_CALLBACK", "")
        dispatch = _no_action_dispatch(diagnostic, callback=callback)
        logger.warning(f"No action emitted — defaulting to {dispatch.status}: {dispatch.reason}")

    return dispatch, function_call, final_message


def _neutralize_self_and_pin(
    dispatch: Optional[Dispatch],
    function_call: Optional[FunctionCall],
    role_name: str,
) -> tuple[Optional[Dispatch], Optional[FunctionCall]]:
    """Neutralize a self hand-off and force the PO escalation pin (issue #876).

    Two pure decisions applied to the resolved ``(dispatch, function_call)``:

    1. **Self hand-off (issue #618, defect 2a):** when the ``function_call``
       targets the running agent it must not outrank the plain dispatch /
       workflow-next routing. Drop the ``function_call`` *and* the promoted
       ``task_hint`` the typed ``call_agent`` branch copied onto the dispatch —
       otherwise ``agent_host._routing_payload`` would re-derive an ``agent``
       target from the neutralized hand-off and re-route to the running agent.
    2. **PO escalation pin (issue #876 review):** the typed ``call_agent`` tool
       exposes no ``pin`` field, so an imperatively escalating PO would lose the
       verbatim pin its spec-comment contract requires. Force ``dispatch.pin``
       engine-side on a PO hand-off.

    Returns the possibly-updated ``(dispatch, function_call)`` pair; neither
    input is mutated (``model_copy``).
    """
    if function_call is not None and is_self_function_call(function_call, role_name):
        logger.info(
            f"Self function_call neutralized (agent={role_name}); "
            f"keeping plain dispatch for workflow-next routing"
        )
        function_call = None
        if dispatch is not None and dispatch.task_hint == role_name:
            dispatch = dispatch.model_copy(update={"task_hint": None})

    if (
        role_name == "po"
        and function_call is not None
        and function_call.function == "call_agent"
        and dispatch is not None
    ):
        logger.info("PO imperative hand-off — forcing dispatch pin (issue #876)")
        dispatch = dispatch.model_copy(update={"pin": True})

    return dispatch, function_call


def _resolve_pin(dispatch: Optional[Dispatch]) -> bool:
    """Resolve whether this step's output comment(s) should be pinned.

    Two sources, both opt-in:
      * ``PIN_COMMENT`` env — a workflow-level override mirroring the
        ``INJECT_CONTEXT`` knob. Parsed with an allow-list (empty/unset ⇒
        False) because the flag defaults to off.
      * ``dispatch.pin`` — a per-run request from the agent's structured
        dispatch (``dispatch_next`` tool or the ``AGENT_RUN_CONFIG``
        sentinel), used to pin this very step's output.
    """
    pin_str = (os.environ.get("PIN_COMMENT") or "false").strip().lower()
    pin_from_env = pin_str in ("true", "1", "yes")
    return pin_from_env or bool(dispatch and dispatch.pin)


def build_output(
    conversation,
    metrics: Metrics,
    args=None,
    prev_state: WorkflowState | None = None,
    scaffold_branch: str | None = None,
) -> dict:
    """
    Build workflow state and write output files.

    Orchestrates: final response extraction, phase derivation, issue/PR info
    building, WorkflowState creation, message splitting, and output file
    writing.

    Args:
        conversation: The completed OpenHands Conversation object.
        metrics: Collected Metrics object.
        args: Parsed argparse namespace (unused, retained for compatibility).
        prev_state: Previous WorkflowState, or None to load from disk.
        scaffold_branch: Pre-extracted scaffold branch ref (issue #604), or
            None to extract the SCAFFOLD_BRANCH marker from the message.

    Returns:
        Dict with keys: 'workflow_state', 'final_message', 'scaffold_branch'.
    """
    # ── Final response ───────────────────────────────────────────
    final_message = get_agent_final_response(conversation.state.events)

    # ── Latest-answer capture (issue #522) ────────────────────────
    # The full deliverable is the agent's last answer: a standalone message,
    # or — for a ``finish`` terminator — its typed ``finish.message`` (the
    # #822/#830 shape), or the turn text for ``dispatch_next``.
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
    recall_events = _recall_aware_events(conversation.state.events, run_outcome)
    latest_answer = extract_latest_answer(recall_events)
    # The ``SCAFFOLD_BRANCH`` prose marker is written on a standalone text turn
    # (the scaffold-plan skill's channel), which the deliverable above may no
    # longer represent once the finish terminator's typed message takes
    # precedence — keep that turn as its own source so the marker fallback
    # stays intact (issue #636).
    standalone_answer = extract_standalone_answer(recall_events)
    # Prose marker source for the SCAFFOLD_BRANCH search/strip below: the
    # standalone turn when present, else the deliverable.
    marker_answer = standalone_answer or latest_answer

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
            final_message, marker_answer, tool_branch=tool_branch
        )
        scaffold_branch = result.branch
        if scaffold_branch:
            logger.info(
                f"SCAFFOLD_BRANCH persisted from {result.source}: {scaffold_branch}"
            )
        elif "SCAFFOLD_BRANCH:" in (final_message or "") or "SCAFFOLD_BRANCH:" in (
            marker_answer or ""
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
    # A caller-supplied state is authoritative and never overwritten. The
    # production entry point (``agent_script.main``) supplies the state after
    # :func:`load_prev_state_or_recover` (disk hand-off → issue comments, issue
    # #671 / anomaly B of #648); the disk-only fallback below keeps the builder
    # pure and unit-testable for direct callers.
    if prev_state is None:
        prev_state = _load_prev_state()

    # ── Environment-derived fields ───────────────────────────────
    role_name = os.environ.get("ROLE_NAME", "")
    workflow_name = os.environ.get("WORKFLOW_NAME", "")
    workflow_step = os.environ.get("WORKFLOW_STEP", "")
    parent_workflow = os.environ.get("PARENT_WORKFLOW", "") or None
    parent_workflow_step = os.environ.get("PARENT_WORKFLOW_STEP", "") or None
    # Callback return address (issue #765): set by the resolver when this run
    # was reached through a caller's function_call, so a callee that finishes
    # with no decision of its own routes back to the caller.
    callback = os.environ.get("CONFIG_CALLBACK", "").strip() or None
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

    # Self function_call neutralization (issue #618, defect 2a) + PO escalation
    # pin (issue #876 review): both decisions live in the pure helper so they
    # are directly unit-testable.
    dispatch, function_call = _neutralize_self_and_pin(dispatch, function_call, role_name)

    # Clear the run-scoped dispatch registry on every exit path (issue #524):
    # build_output runs exactly once per agent run, so this must not leak into
    # the next run in a long-lived process.
    clear_run(getattr(conversation, "id", None))
    # Typed call_agent hand-off registry (issue #876) — same lifecycle.
    clear_call_agent_run(getattr(conversation, "id", None))
    clear_scaffold_branch_run(getattr(conversation, "id", None))
    # Complexity review request recorded by the agent this run (issue #869).
    # Captured before clearing the registry so it lands on this step's entry.
    complexity_review_request = get_recorded_review_request(
        getattr(conversation, "id", None)
    )
    clear_complexity_review_run(getattr(conversation, "id", None))

    # ── Soft-kill error status (issue #704) ──────────────────────
    # A gracefully timed-out run was interrupted mid-step, so it is an error
    # outcome, not a completion. Override whatever dispatch the agent emitted
    # with the explicit ``soft_kill`` status so the router retries this step in
    # place (bounded by MAX_SOFT_RETRIES) instead of advancing to the next one.
    # Applied before the human-gate intercept so a gated step still holds.
    if is_graceful_timeout:
        dispatch = _soft_kill_dispatch()
        logger.info("Graceful timeout — dispatch overridden with soft_kill status")

    # ── Human gate intercept (issues #506, #648) ─────────────────
    # A gated step's dispatch is always overridden to hold: the gate is
    # released ONLY by an engine-owned GateDecision recorded from an
    # identity-verified `/nebuko approve gate` / `/nebuko reject gate`.
    # The run's trigger source is no longer an authorization input, so an
    # agent (or a human-echoed command) can never release the gate here.
    is_human_resume = bool(os.environ.get("HUMAN_RESUME_INPUT", "").strip())
    # Engine soft-kill retry marker (issue #704): emitted only by the
    # soft-kill retry resolution. Forwarded to build_state so the per-step
    # retry budget is consumed for genuine retries and never for a human or
    # manual trigger (which carries no marker).
    soft_retry_attempt = (
        os.environ.get("SOFT_RETRY_ATTEMPT", "").strip().lower()
        in ("true", "1", "yes")
    )
    dispatch = apply_human_gate_intercept(
        dispatch,
        workflow_name=workflow_name,
        workflow_step=workflow_step,
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

    pin = _resolve_pin(dispatch)

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
        callback=callback,
        started_by=os.environ.get("TRIGGER_AUTHOR") or None,
        inject_context=inject_context,
        pin=pin,
        scaffold_branch=scaffold_branch,
        position=position,
        soft_retry_attempt=soft_retry_attempt,
        is_human_resume=is_human_resume,
        gate_decision=_parse_gate_decision_env(),
        workflow_ref=workflow_ref_from_env(os.environ),
        complexity_adjustment=_parse_complexity_adjustment_env(),
        complexity_review_request=complexity_review_request,
        complexity_policy=_parse_complexity_policy_env(),
    )
    logger.info(f"Built workflow state run {new_state.run_id} for step={workflow_step}")

    # ── Scaffold branch marker strip (issue #604; #636) ──────────
    # Strip the raw marker from both the posted comment and the
    # latest-answer file so users never see it. The summary line
    # replaces a stripped marker — when the branch came from the
    # report_scaffold_branch tool (no marker in the prose), there is
    # nothing to strip and nothing to append.
    if scaffold_branch:
        marker_in_prose = (
            "SCAFFOLD_BRANCH:" in (final_message or "")
            or "SCAFFOLD_BRANCH:" in (marker_answer or "")
        )
        latest_answer = strip_scaffold_branch(latest_answer)
        final_message = strip_scaffold_branch(final_message)
        if marker_in_prose and final_message:
            final_message = final_message.rstrip() + f"\n\n{scaffold_branch_summary(scaffold_branch)}\n"
        logger.info(f"Persisted scaffold_branch={scaffold_branch}")

    _write_latest_answer_file(latest_answer)

    # ── Message splitting ────────────────────────────────────────
    # context reports are posted as a single collapsed comment (issue #557):
    # raise the split threshold to the 60k comment cap so they fit in one
    # part; other agents keep the 10k multi-part split.
    max_split_length = 60000 if role_name == "context" else 10000
    message_parts = split_message(final_message, max_split_length) if final_message else []

    # ── Write output files ───────────────────────────────────────
    _write_output_files(final_message, message_parts, new_state)

    logger.info("Task completed successfully")

    return {
        "workflow_state": new_state,
        "scaffold_branch": scaffold_branch,
        "final_message": final_message,
    }
