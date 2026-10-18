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
    CommentSizePolicy,
    is_self_function_call,
    load_workflow,
    load_workflow_with_dirs,
    parse_position_env,
    resolve_workflow_reference,
)
from github_client import get_issue_comments
from metrics_schema import Metrics
from workflow_state import (
    Appreciation,
    FunctionCall,
    GateDecision,
    GateLock,
    GateStatus,
    HoldKind,
    IncompleteReason,
    IssueInfo,
    PullRequestInfo,
    RunPrefs,
    StepInputRecord,
    StepResolution,
    TurnOutcome,
    TurnStatus,
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
    get_recorded_run_prefs,
    get_recorded_turn_outcome,
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


def _soft_kill_outcome() -> TurnOutcome:  # @stub-protected
    """Build the soft-kill outcome for a gracefully timed-out run (#704).

    A soft time-kill (the ~20 min wind-down) interrupts the running step
    without completing it. The outcome handed to the workflow engine must be
    INCOMPLETE so the router re-runs the interrupted step (bounded by
    ``MAX_SOFT_RETRIES``) instead of advancing to the next one.
    ``build_output`` uses this in place of the agent's own outcome whenever the
    run status is ``OutcomeStatus.GRACEFULLY_TIMED_OUT``.

    Returns:
        An INCOMPLETE/SOFT_TIMEOUT outcome with the timeout note.
    """
    return TurnOutcome.for_incomplete(
        IncompleteReason.SOFT_TIMEOUT,
        note=(
            "Soft time-kill: the run was interrupted by the wind-down timeout "
            "before completing the step. Retry this step in place; halt for a "
            "human once MAX_SOFT_RETRIES is exhausted."
        ),
    )


def _agent_checkpoint_outcome() -> TurnOutcome:  # @stub-protected
    """Build the checkpoint outcome for a folded run (issue #965).

    The agent called the ``handoff`` tool with ``fold: true``: it saved its
    state and asked for a fresh start. The step did not complete, so the
    outcome must not carry a verdict — it is INCOMPLETE/AGENT_CHECKPOINT,
    which the router treats exactly like a soft kill (re-run in place, bounded
    by ``MAX_SOFT_RETRIES``, halt for a human once spent).

    Returns:
        An INCOMPLETE/AGENT_CHECKPOINT outcome.
    """
    return TurnOutcome.for_incomplete(
        IncompleteReason.AGENT_CHECKPOINT,
        note=(
            "Agent checkpoint: the run saved a session handoff and asked to "
            "fold (start fresh). Retry this step in place; halt for a human "
            "once MAX_SOFT_RETRIES is exhausted."
        ),
    )


def _parse_step_input_records_env() -> list[StepInputRecord]:
    """Parse this step's materialized-input records from ``STEP_INPUT_RESULTS``.

    Emitted by the engine before the agent is invoked (issue #872) so the
    advancing step's ``HistoryEntry`` records what deterministic inputs were
    wired to it. Absent/empty yields no records; a malformed value is ignored
    (logged) rather than aborting the run.
    """
    raw = os.environ.get("STEP_INPUT_RESULTS", "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed STEP_INPUT_RESULTS env value")
        return []
    records: list[StepInputRecord] = []
    for item in payload if isinstance(payload, list) else []:
        try:
            records.append(StepInputRecord.model_validate(item))
        except Exception:
            logger.warning("Ignoring malformed STEP_INPUT_RESULTS entry")
    return records


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


def _parse_gate_released_env() -> bool:
    """True when this run releases the root-level gate lock (issue #1003).

    Set by the gate-decision resolution in ``resolve_config`` alongside
    ``GATE_DECISION``: the decision is the only authorized release, so the
    armed :class:`GateLock` is marked ``RELEASED`` rather than carried forward.
    """
    return os.environ.get("GATE_RELEASED", "").strip().lower() in (
        "true",
        "1",
        "yes",
    )


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


def _no_action_outcome(diagnostic: str = "") -> TurnOutcome:
    """Synthesize the factual outcome of a run that emitted no verdict (#864).

    An agent that ends its run without calling ``dispatch_next`` has stated no
    appreciation at all: that is contract drift, not a completion. The outcome
    is therefore INCOMPLETE/NO_OUTCOME — the router derives the edge from the
    workflow topology and pings a human when nothing matches, instead of
    inventing a navigation status (issue #964).

    ``diagnostic`` is kept as the human-facing note so the reason a verdict is
    missing ("the legacy sentinel could not be parsed") survives on the thread.
    """
    return TurnOutcome.for_incomplete(IncompleteReason.NO_OUTCOME, note=diagnostic)


# @stub-protected
def _load_gated_step_def(
    workflow_name: str,
    workflow_step: str,
) -> bool:
    """Return True if the given step is defined with human_gate: true.

    Loads the workflow YAML via the config package. Any missing/invalid
    reference (no workflow name, missing file, parse error) degrades to
    False — the gate must never crash the run, and a broken YAML should
    fall back to legacy auto-advance rather than fail the whole post-step.

    Resolution is per file, not per directory (issue #987): a repo-local
    ``.nebuko/workflows/`` must not hide the shipped ``defaults/workflows/``,
    so name references go through the layered ``load_workflow_with_dirs``.
    """
    if not workflow_name or not workflow_step:
        return False
    try:
        import default_paths

        shipped_dir, local_dir = default_paths.workflow_dirs()
        if workflow_name.lower().endswith((".yaml", ".yml")) and not workflow_name.startswith(
            "workflows/"
        ):
            ref = resolve_workflow_reference(workflow_name, shipped_dir)
        else:
            name = workflow_name.removeprefix("workflows/")
            for suffix in (".yaml", ".yml"):
                name = name.removesuffix(suffix)
            ref = load_workflow_with_dirs(name, shipped_dir, local_dir or "")
        wf_def = load_workflow(str(ref))
    except Exception as e:
        logger.warning(f"Human gate check: could not load workflow '{workflow_name}': {e}")
        return False
    for step in wf_def.steps:
        if step.name == workflow_step:
            return bool(step.human_gate)
    return False


def _is_human_gate_hold(outcome: Optional[TurnOutcome]) -> bool:
    """True when an outcome is the human-gate intercept's hold.

    The intercept (:func:`apply_human_gate_intercept`) is the sole producer of
    a ``HOLD``/``AWAITING_APPROVAL`` outcome, and ``resolve_route`` detects the
    same shape, so this mirror is what keeps the persisted state and the router
    in agreement (issue #887).
    """
    return bool(
        outcome is not None
        and outcome.appreciation is Appreciation.HOLD
        and outcome.hold_kind is HoldKind.AWAITING_APPROVAL
    )


def apply_human_gate_intercept(
    outcome: Optional[TurnOutcome],
    workflow_name: str,
    workflow_step: str,
    gate: Optional[GateLock] = None,
) -> tuple[Optional[TurnOutcome], Optional[GateLock]]:
    """Enforce the human_gate security boundary (issues #506, #648, #1003).

    A step marked ``human_gate: true`` in the workflow YAML may never advance
    on its own. There is **no** second release path: a completed turn (or a
    missing outcome, which routes on topology downstream) is overridden to hold
    ``awaiting_approval``, and the only way past the gate is a recorded
    :class:`GateDecision` written by an identity-verified
    ``/nebuko approve gate`` / ``/nebuko reject gate`` command — never by a
    re-invocation of the agent. A hold, an incomplete turn, and a soft kill are
    never intercepted: on a gated step the gate wins and the run holds for a
    human instead of auto-retrying.

    The lock itself is *root-level metadata* (issue #1003): it is raised here,
    independently of the turn outcome and of the workflow position, and it
    blocks the workflow advance only — an agent-to-agent ``call_agent`` from
    the gated step stays legal. An already-armed lock for the same step is
    preserved verbatim (a re-run of the gated step must not reset the consult
    budget); a lock for a different step is re-armed for this one.

    Args:
        outcome: Turn outcome resolved from the agent run (may be None).
        workflow_name: Active workflow name (WORKFLOW_NAME env).
        workflow_step: Step just executed (WORKFLOW_STEP env).
        gate: The previous root-level lock carried by the workflow state, if
            any (issue #1003).

    Returns:
        The ``(outcome, gate)`` pair: the possibly-overridden TurnOutcome and
        the root-level :class:`GateLock` (``None`` when this step is not gated).
    """
    if outcome is not None and not _gate_intercepts(outcome):
        return outcome, gate
    if not _load_gated_step_def(workflow_name, workflow_step):
        return outcome, gate
    logger.info(
        f"Human gate: step '{workflow_step}' requires approval — "
        f"intercepting {(outcome.appreciation.value if outcome and outcome.appreciation else 'no verdict')} as hold"
    )
    if gate is None or gate.step != workflow_step or not gate.armed():
        # A fresh step re-arms; a lock already armed for this step is preserved
        # verbatim so a re-run cannot reset the consult budget. A *released*
        # lock (the human's decision was consumed) is re-armed: the step is
        # still gated, so entering it must raise the lock again.
        gate = GateLock(step=workflow_step)
    return (
        TurnOutcome(
            status=TurnStatus.COMPLETED,
            appreciation=Appreciation.HOLD,
            hold_kind=HoldKind.AWAITING_APPROVAL,
            note=f"human_gate:{workflow_step}",
        ),
        gate,
    )


def _gate_intercepts(outcome: TurnOutcome) -> bool:
    """True when the gate must override this outcome.

    Only a *completing* turn is intercepted: its verdict is discarded in favour
    of the approval hold. An INCOMPLETE turn (soft kill, checkpoint, failure)
    keeps its own outcome so the router can retry or halt it (issue #704).
    """
    return outcome.status is TurnStatus.COMPLETED


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


def _comment_split_length() -> int:
    """The part-splitting threshold, from the shared comment-size policy (#913).

    Single source of truth with the publish seam
    (``run_loop.Engine._comment_size_policy``): the debug part files here and
    the multi-comment post there share one ``max_comment_chars``, so they can
    never disagree about where an answer gets split.
    """
    return CommentSizePolicy().resolved_max_comment_chars()


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

    Turn-outcome resolution intentionally does NOT use this: the recall turn is
    exactly where the missing verdict is supplied.
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


def _resolve_outcome(
    conversation,
) -> tuple[TurnOutcome, RunPrefs, Optional[FunctionCall]]:
    """Resolve the run's factual turn outcome, run prefs and typed hand-off.

    Resolution priority (issue #524, extended by #876/#964):
      1. The recorded ``dispatch_next`` tool call — a boolean check on
         structured data. Its payload carries the agent-supplied verdict; the
         engine-measured status comes from the run outcome (:func:`_observed_status`).
      2. A typed ``call_agent`` tool call with no ``dispatch_next`` sibling —
         an explicit agent-to-agent hand-off, which is itself a decision.
      3. Neither — no verdict was stated: INCOMPLETE/NO_OUTCOME.

    The legacy ``<!-- AGENT_RUN_CONFIG ... -->`` sentinel is **not** read
    (issue #964): it is prose the agent rendered, and routing is now engine
    truth. A stale echo of one is inert.

    Returns:
        The ``(turn_outcome, prefs, function_call)`` triple for this run. The
        prefs are the non-routing knobs recorded with the outcome (issue #964);
        they are empty when no tool call stated them.
    """
    function_call: Optional[FunctionCall] = None
    prefs = RunPrefs()
    conv_id = getattr(conversation, "id", None)
    outcome = (
        get_recorded_turn_outcome(conv_id) if conv_id is not None else None
    )
    typed_function_call = _typed_function_call(conversation)

    if outcome is not None:
        recorded_prefs = (
            get_recorded_run_prefs(conv_id) if conv_id is not None else None
        )
        if recorded_prefs is not None:
            prefs = recorded_prefs
        if typed_function_call is not None:
            function_call = typed_function_call
        elif outcome.required_agent:
            # Lazy import: dispatch_check imports from this module at top
            # level, so a module-level import here would be circular.
            from agent_script.features.dispatch_check import (
                agent_to_function_call,
            )

            # Carry the declared tier (issue #787) so a hand-off without one
            # inherits the callee step's declared tier instead of masking it
            # with a hardcoded default.
            function_call = FunctionCall(
                **agent_to_function_call(outcome.required_agent),
                complexity=prefs.complexity,
            )
        logger.info(
            "Turn outcome resolved via dispatch_next tool: "
            f"status={outcome.status.value}, "
            f"appreciation={outcome.appreciation or '-'}"
        )
    elif typed_function_call is not None:
        # A typed call_agent without a dispatch_next sibling is itself the
        # turn's verdict: the callee approved the routed work (issue #876).
        outcome = TurnOutcome(
            status=TurnStatus.COMPLETED,
            appreciation=Appreciation.APPROVED,
            required_agent=typed_function_call.agent_name,
        )
        prefs = RunPrefs(complexity=typed_function_call.complexity or "")
        function_call = typed_function_call
        logger.info(
            "Turn outcome resolved via typed call_agent tool: "
            f"agent={typed_function_call.agent_name}"
        )
    else:
        # No-action default (issue #765, #864): a run that emitted neither a
        # verdict nor a hand-off is not a completion. The engine records the
        # absence as a fact and lets the router ping a human when topology
        # matches nothing (issue #964).
        outcome = _no_action_outcome()
        logger.warning(
            "No turn outcome emitted — recording "
            f"{outcome.status.value}/{outcome.incomplete_reason.value}"
        )

    return _observed_status(conversation, outcome), prefs, function_call


def _observed_status(conversation, outcome: TurnOutcome) -> TurnOutcome:
    """Fold the engine-measured run status into the recorded outcome (#964).

    A run that timed out, crashed, was interrupted, or folded itself into a
    session handoff did not complete, whatever the agent said: the verdict is
    replaced by the measured INCOMPLETE reason. ``from_agent_payload`` owns
    that precedence; a completed run keeps its verdict untouched.
    """
    run_outcome = _get_run_outcome(conversation)
    observed = run_outcome.status if run_outcome is not None else OutcomeStatus.SUCCESS
    if observed is OutcomeStatus.SUCCESS:
        return outcome
    return TurnOutcome.from_agent_payload(
        {"appreciation": outcome.appreciation}, observed=observed
    )


def _neutralize_self_and_pin(
    outcome: TurnOutcome,
    prefs: RunPrefs,
    function_call: Optional[FunctionCall],
    role_name: str,
) -> tuple[TurnOutcome, RunPrefs, Optional[FunctionCall]]:
    """Neutralize a self hand-off and force the PO escalation pin (issue #876).

    Two pure decisions applied to the resolved ``(outcome, prefs, function_call)``:

    1. **Self hand-off (issue #618, defect 2a):** when the ``function_call``
       targets the running agent it must not outrank the plain outcome /
       workflow-next routing. Drop the ``function_call`` *and* the promoted
       ``required_agent`` the typed ``call_agent`` branch copied onto the
       outcome — otherwise a re-derived hand-off would re-route to the running
       agent.
    2. **PO escalation pin (issue #876 review):** the typed ``call_agent`` tool
       exposes no ``pin`` field, so an imperatively escalating PO would lose the
       verbatim pin its spec-comment contract requires. Force ``prefs.pin``
       engine-side on a PO hand-off.

    Returns the possibly-updated triple; no input is mutated (``model_copy``).
    """
    if function_call is not None and is_self_function_call(function_call, role_name):
        logger.info(
            f"Self function_call neutralized (agent={role_name}); "
            f"keeping plain outcome for workflow-next routing"
        )
        function_call = None
        if outcome.required_agent == role_name:
            outcome = outcome.model_copy(update={"required_agent": None})

    if (
        role_name == "po"
        and function_call is not None
        and function_call.function == "call_agent"
    ):
        logger.info("PO imperative hand-off — forcing the pin preference (issue #876)")
        prefs = prefs.model_copy(update={"pin": True})

    return outcome, prefs, function_call


def _resolve_pin(prefs: Optional[RunPrefs]) -> bool:
    """Resolve whether this step's output comment(s) should be pinned.

    Two sources, both opt-in:
      * ``PIN_COMMENT`` env — a workflow-level override mirroring the
        ``INJECT_CONTEXT`` knob. Parsed with an allow-list (empty/unset ⇒
        False) because the flag defaults to off.
      * ``prefs.pin`` — a per-run request from the agent's ``dispatch_next``
        call, used to pin this very step's output.
    """
    pin_str = (os.environ.get("PIN_COMMENT") or "false").strip().lower()
    pin_from_env = pin_str in ("true", "1", "yes")
    return pin_from_env or bool(prefs and prefs.pin)


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

    # ── Extract the turn outcome + function_call from agent output ─────
    turn_outcome, prefs, function_call = _resolve_outcome(conversation)

    # Self function_call neutralization (issue #618, defect 2a) + PO escalation
    # pin (issue #876 review): both decisions live in the pure helper so they
    # are directly unit-testable.
    turn_outcome, prefs, function_call = _neutralize_self_and_pin(
        turn_outcome, prefs, function_call, role_name
    )

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
        turn_outcome = _soft_kill_outcome()
        # The interrupted step re-runs in place; the agent's run preferences
        # belonged to the turn that never completed (parity with the legacy
        # soft-kill dispatch, which carried none).
        prefs = RunPrefs()
        logger.info("Graceful timeout — outcome overridden with SOFT_TIMEOUT")

    # ── Agent-checkpoint status (issue #965) ─────────────────────
    # A folded run (the ``handoff`` tool called with ``fold: true``) saved its
    # document and asked for a fresh start: it is an incomplete checkpoint, not
    # a completion. Override the agent's own dispatch with the explicit
    # ``agent_checkpoint`` status, which the router treats exactly like
    # ``soft_kill`` (re-run the step in place, bounded by MAX_SOFT_RETRIES).
    if (
        run_outcome is not None
        and run_outcome.status is OutcomeStatus.AGENT_CHECKPOINT
    ):
        turn_outcome = _agent_checkpoint_outcome()
        prefs = RunPrefs()
        logger.info("Handoff fold — outcome overridden with AGENT_CHECKPOINT")

    # ── Human gate intercept (issues #506, #648, #1003) ──────────
    # A gated step's dispatch is always overridden to hold: the gate is
    # released ONLY by an engine-owned GateDecision recorded from an
    # identity-verified `/nebuko approve gate` / `/nebuko reject gate`.
    # The run's trigger source is no longer an authorization input, so an
    # agent (or a human-echoed command) can never release the gate here.
    # The intercept also raises the root-level GateLock (issue #1003) — the
    # lock is workflow metadata, independent of the turn outcome and of the
    # position, and it blocks the workflow *advance* only: a ``call_agent``
    # from the gated step stays legal.
    is_human_resume = bool(os.environ.get("HUMAN_RESUME_INPUT", "").strip())
    # Engine soft-kill retry marker (issue #704): emitted only by the
    # soft-kill retry resolution. Forwarded to build_state so the per-step
    # retry budget is consumed for genuine retries and never for a human or
    # manual trigger (which carries no marker).
    soft_retry_attempt = (
        os.environ.get("SOFT_RETRY_ATTEMPT", "").strip().lower()
        in ("true", "1", "yes")
    )
    turn_outcome, gate = apply_human_gate_intercept(
        turn_outcome,
        workflow_name=workflow_name,
        workflow_step=workflow_step,
        gate=prev_state.gate if prev_state is not None else None,
    )
    if _parse_gate_released_env() and gate is not None:
        # A recorded gate decision is the only authorized release (issue
        # #1003): mark the lock released so it stops blocking the advance while
        # the audit trail (gate_history) keeps the decision itself.
        gate = gate.model_copy(update={"status": GateStatus.RELEASED})
    # Consistency fix (issue #887): when the intercept converts a completing
    # dispatch to a human_gate hold, the persisted resolution must not keep
    # advertising the ungated forward edge (``next_step`` from WORKFLOW_NEXT).
    # The hold is released only by a gate command, so no reader may follow that
    # edge; the routing guard in resolve_route no longer trusts it either.
    if _is_human_gate_hold(turn_outcome):
        resolution = resolution.model_copy(update={"next_step": ""})
        # The gate supersedes any workflow *advance* (issue #887 round 2), but
        # it must NOT destroy the agent's imperative hand-off (issue #1003):
        # ``call_agent`` is orthogonal to the outcome, so a gated agent that
        # consults another agent keeps its ``function_call``. The resolver
        # routes the consult and returns to the gated step with the gate armed.

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

    pin = _resolve_pin(prefs)

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
        turn_outcome=turn_outcome,
        prefs=prefs,
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
        gate=gate,
        workflow_ref=workflow_ref_from_env(os.environ),
        complexity_adjustment=_parse_complexity_adjustment_env(),
        complexity_review_request=complexity_review_request,
        complexity_policy=_parse_complexity_policy_env(),
        inputs=_parse_step_input_records_env(),
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
    # The posted answer is split at the publish seam into up to
    # ``CommentSizePolicy.max_parts`` comments (issue #913); these part files
    # are debug artifacts only. Their threshold is driven by the same policy so
    # the two stages cannot drift — a long context report is no longer reduced
    # to a single 60k comment here, and no role gets a smaller 10k split.
    max_split_length = _comment_split_length()
    message_parts = split_message(final_message, max_split_length) if final_message else []

    # ── Write output files ───────────────────────────────────────
    _write_output_files(final_message, message_parts, new_state)

    logger.info("Task completed successfully")

    return {
        "workflow_state": new_state,
        "scaffold_branch": scaffold_branch,
        "final_message": final_message,
    }
