"""Deterministic, no-LLM replay of the engine's per-invocation decisions (issue #1314).

Answering "what tier / agent / config would the engine resolve for this step?"
used to require hand-written scratch scripts: extract the ``WORKFLOW_STATE``
block from the issue, hand-build ``prev_state`` / ``turn_outcome`` inputs, and
clear gate state by trial and error before ``resolve_config`` returned anything
useful.

This module is the read-only replacement. Given a workflow state (from an issue
number or a local JSON file) it rebuilds, for one history entry or all of them,
the ``prev_state`` as it stood *before* that invocation — through the same code
path the live run used, including the human-gate lock — and then runs
``resolve_config`` over it. One row per invocation reports the step, agent, base
tier, recorded delta, trigger + rationale, final ``CONFIG_COMPLEXITY`` and the
next step/agent.

Usage::

    uv run python -m nebuko.engine.lib.replay --issue 1299 --entry 14
    uv run python -m nebuko.engine.lib.replay --state state.json --all-entries
    uv run python -m nebuko.engine.lib.replay --state state.json --json

Non-goals: no LLM calls, no agent execution, no GitHub mutations. ``--state``
runs fully offline.

Fidelity limits (all deterministic, all reported in the header):

* Entry 0 is the seed invocation — the live run started from a human command
  and a trigger body, neither of which is recorded in state, so it is listed
  without a resolution.
* A pending ``function_call`` hand-off and the per-step ``soft_retry`` counter
  are not recorded per history entry, so they cannot be reconstructed.
* ``parent_workflow``/``parent_workflow_step`` are derived from the recorded
  position chain rather than from the (unrecorded) state fields.
* The replay uses the *checkout's* complexity policy, which may differ from the
  one pinned when the run happened; the header says so when it does.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from nebuko.engine.lib.complexity_governor.complexity_governor import (
    ComplexityAdjustment,
    tier_index,
)
from nebuko.engine.lib.config.sub_domains.complexity_policy.complexity_policy import (
    TIERS,
)
from nebuko.engine.lib.resolve_config import resolve_config
from nebuko.engine.lib.workflow_state import (
    GateDecision,
    GateLock,
    WorkflowState,
)
from nebuko.engine.lib.workflow_state.features.get_last_state.get_last_state import (
    extract_latest_state_from_comments,
)
from nebuko.engine.lib.workflow_state.workflow_state import (
    STATE_MARKER_END,
    STATE_MARKER_START,
    STATE_SCHEMA_VERSION,
)

__all__ = [
    "ReplayRow",
    "PolicyProvenance",
    "load_state_from_text",
    "load_state_from_issue",
    "rebuild_prev_state",
    "replay_entry",
    "replay_entries",
    "render_table",
    "render_provenance",
    "main",
]

#: Env vars that change what a replay resolves. Reported in the header so a
#: discrepancy against a live run can be attributed to the environment.
_PROVENANCE_ENV = (
    "AGENT_CONFIG_PATH",
    "WORKFLOWS_DIR",
    "LOCAL_WORKFLOWS_DIR",
    "MAX_SOFT_RETRIES",
    "COMPLEXITY_CONTEXT_SIGNALS",
)

#: The engine's own marker, so the replayed body classifies as a bot comment
#: exactly like the comment the live run was triggered from.
_TRIGGER_MARKER = "<!-- bot-workflow-terminal -->"


# ── State loading ─────────────────────────────────────────────────────────


def load_state_from_text(text: str) -> Optional[WorkflowState]:
    """Parse a workflow state from a marked comment body or a bare JSON file.

    Accepts every shape a user is likely to have at hand: a full issue/comment
    body carrying the ``WORKFLOW_STATE`` markers, a bare state document, or a
    wrapper object with a ``state`` key.
    """
    if not text or not text.strip():
        return None
    from_body = extract_latest_state_from_comments([{"body": text}])
    if from_body is not None:
        return from_body
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("state"), dict):
        data = data["state"]
    if not isinstance(data, dict):
        return None
    try:
        return WorkflowState.model_validate(data)
    except Exception:  # noqa: BLE001 - an unparseable file is a user error
        return None


def load_state_from_issue(
    issue_number: int,
    repo: Optional[str] = None,
    *,
    transport: Any = None,
) -> Optional[WorkflowState]:
    """Fetch an issue's body + comments and extract the latest workflow state.

    The state lives in the newest comment carrying the ``WORKFLOW_STATE``
    markers; the issue body itself is included so a state seeded in the
    description is found too. Network access happens only here.
    """
    from nebuko.engine.lib.github_client import get_issue, get_issue_comments

    repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise ValueError(
            "--repo (or GITHUB_REPOSITORY) is required to replay from an issue"
        )
    issue = get_issue(repo, issue_number, transport=transport) or {}
    comments = get_issue_comments(repo, issue_number, transport=transport)
    bodies = [{"body": issue.get("body") or ""}, *comments]
    return extract_latest_state_from_comments(bodies)


# ── prev_state reconstruction ─────────────────────────────────────────────


def _gate_was_released(
    decisions: Sequence[GateDecision], entry_comment_ids: Sequence[int]
) -> bool:
    """True when a recorded decision consumed the hold this entry raised.

    Decisions are ordered against the entry's own comment ids (monotonic on
    GitHub). A decision without a comment id cannot be ordered, so it is read
    as having released the hold — a spurious hold would misreport the routing,
    while a released reading only reproduces what the live run did next.
    """
    if not decisions:
        return False
    ids = [cid for cid in entry_comment_ids if cid]
    if not ids:
        return True
    latest = max(ids)
    return any(
        decision.comment_id is None or decision.comment_id > latest
        for decision in decisions
    )


def _gate_before(
    state: WorkflowState,
    entry,
    workflow: str,
    position: list,
    workflows_dir: str,
    local_workflows_dir: Optional[str],
) -> Optional[GateLock]:
    """The root gate lock as it stood before the invocation that followed ``entry``.

    Mirrors the live arming rule: a ``human_gate: true`` step raises the lock
    when it completes, and only a recorded :class:`GateDecision` for that exact
    hold releases it. ``consult_rounds``/``last_handoff`` are not recorded per
    entry, so an armed reconstruction starts with a fresh budget.
    """
    from nebuko.engine.lib.resolve_config.features.resolve_route.resolve_route import (
        _is_human_gate_step,
    )

    step = entry.step or (position[-1].step if position else "")
    if not (workflow and step):
        return None
    if not _is_human_gate_step(workflow, step, workflows_dir, local_workflows_dir):
        return None
    decisions = [
        decision
        for decision in (state.gate_history or [])
        if decision.is_for(workflow, step, position)
    ]
    if _gate_was_released(decisions, list(entry.comment_ids or [])):
        return None
    return GateLock(step=step)


def rebuild_prev_state(
    state: WorkflowState,
    index: int,
    workflows_dir: str,
    local_workflows_dir: Optional[str] = None,
) -> Optional[WorkflowState]:
    """The state as it stood before invocation ``index`` (0-based).

    ``history`` is the replayable log of every completed invocation, so the
    state consumed by invocation ``index`` is exactly ``history[:index]`` plus
    the facts the previous entry recorded: its step/agent/position, its turn
    outcome and its run preferences. Returns ``None`` for the seed invocation
    (index 0), which had no prior state.
    """
    if index <= 0:
        return None
    history = list(state.history)
    if index > len(history):
        raise IndexError(
            f"entry {index} out of range: state has {len(history)} history entries"
        )
    prior = history[:index]
    last = prior[-1]
    position = list(last.position or [])
    workflow = position[-1].workflow if position else state.workflow
    # A legacy entry can carry an empty scalar step while its position still
    # names the step that ran; recover it the way ``build_state`` does. The
    # gate reconstruction reads the same recovered step.
    step = last.step or (position[-1].step if position else "")
    parent_workflow = position[-2].workflow if len(position) >= 2 else None
    parent_step = position[-2].step if len(position) >= 2 else None
    return WorkflowState(
        run_id=index,
        schema_version=STATE_SCHEMA_VERSION,
        workflow=workflow,
        step=step,
        agent=last.agent,
        history=prior,
        position=position,
        parent_workflow=parent_workflow,
        parent_workflow_step=parent_step,
        turn_outcome=last.turn_outcome,
        prefs=last.prefs,
        gate=_gate_before(
            state, last, workflow, position, workflows_dir, local_workflows_dir
        ),
        gate_decision=state.gate_decision,
        gate_history=list(state.gate_history or []),
        scaffold_branch=state.scaffold_branch,
        issue=state.issue,
        pull_request=state.pull_request,
        started_by=state.started_by,
        workflow_ref=state.workflow_ref,
        circuit_breaker=state.circuit_breaker,
        complexity_policy=state.complexity_policy,
        pinned_comment_ids=list(state.pinned_comment_ids or []),
        current_context=dict(state.current_context or {}),
        thread=list(state.thread or []),
    )


def trigger_body_for(prev_state: WorkflowState) -> str:
    """The trigger body the live run consumed: bot marker + embedded state."""
    payload = prev_state.model_dump(mode="json", exclude_defaults=True)
    return (
        _TRIGGER_MARKER
        + "\n"
        + STATE_MARKER_START
        + "\n"
        + json.dumps(payload)
        + "\n"
        + STATE_MARKER_END
        + "\n"
    )


# ── One invocation ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReplayRow:
    """One replayed invocation, ready for the table."""

    entry: int
    step: str
    agent: str
    base_tier: str
    recorded_delta: Optional[int]
    replay_delta: Optional[int]
    trigger: str
    rationale: str
    config_complexity: str
    next_step: str
    next_agent: str
    resolved_step: str = ""
    resolved_agent: str = ""
    skip_kind: str = ""
    note: str = ""

    @property
    def matches(self) -> bool:
        """True when the replay resolved the step/agent the run actually executed."""
        if not self.resolved_step and not self.resolved_agent:
            return True
        return (
            self.resolved_step == self.step and self.resolved_agent == self.agent
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry": self.entry,
            "step": self.step,
            "agent": self.agent,
            "base_tier": self.base_tier,
            "recorded_delta": self.recorded_delta,
            "replay_delta": self.replay_delta,
            "trigger": self.trigger,
            "rationale": self.rationale,
            "config_complexity": self.config_complexity,
            "next_step": self.next_step,
            "next_agent": self.next_agent,
            "resolved_step": self.resolved_step,
            "resolved_agent": self.resolved_agent,
            "skip_kind": self.skip_kind,
            "matches": self.matches,
            "note": self.note,
        }


def _base_tier(effective: str, adjustment: Optional[ComplexityAdjustment]) -> str:
    """Invert the governor overlay: the tier the workflow itself declared."""
    if not effective:
        return ""
    if adjustment is None:
        return effective
    index = max(0, min(len(TIERS) - 1, tier_index(effective) - adjustment.delta))
    return TIERS[index]


def _next_agent(
    workflow: str,
    step_name: str,
    workflows_dir: str,
    local_workflows_dir: Optional[str],
) -> str:
    """Best-effort agent of ``step_name`` in ``workflow`` (empty when unknown)."""
    if not (workflow and step_name):
        return ""
    try:
        from nebuko.engine.lib.resolve_workflow import _load_workflow_by_reference

        wf_def = _load_workflow_by_reference(
            workflow, workflows_dir, local_workflows_dir
        )
    except Exception:  # noqa: BLE001 - a lookup miss must not break the table
        return ""
    for step in getattr(wf_def, "steps", None) or []:
        if step.name == step_name:
            return step.agent or ""
    return ""


def replay_entry(
    state: WorkflowState,
    index: int,
    *,
    workflows_dir: str,
    prompt_base: str,
    local_workflows_dir: Optional[str] = None,
) -> ReplayRow:
    """Replay the engine's decision for the invocation at ``index``."""
    history = list(state.history)
    if index < 0 or index >= len(history):
        raise IndexError(
            f"entry {index} out of range: state has {len(history)} history entries"
        )
    recorded = history[index]
    recorded_adjustment = recorded.complexity_adjustment
    recorded_delta = (
        recorded_adjustment.delta if recorded_adjustment is not None else None
    )
    recorded_tier = getattr(recorded.metrics, "complexity", "") or ""
    # The tier the workflow itself declared for this step, recovered by
    # inverting the overlay the live run recorded (issue #869 records the
    # *effective* tier on the entry).
    recorded_base = _base_tier(recorded_tier, recorded_adjustment)
    recorded_next = history[index + 1] if index + 1 < len(history) else None

    if index == 0:
        return ReplayRow(
            entry=0,
            step=recorded.step,
            agent=recorded.agent,
            base_tier=recorded_base,
            recorded_delta=recorded_delta,
            replay_delta=None,
            trigger="",
            rationale="",
            config_complexity=recorded_tier,
            next_step=recorded_next.step if recorded_next else "",
            next_agent=recorded_next.agent if recorded_next else "",
            note="seed invocation (no prior state to replay)",
        )

    prev_state = rebuild_prev_state(state, index, workflows_dir, local_workflows_dir)
    assert prev_state is not None  # index > 0
    result = resolve_config(
        trigger_body_for(prev_state),
        prompt_base,
        workflows_dir,
        is_bot=True,
        local_workflows_dir=local_workflows_dir,
    )

    if result.get("SKIP_AGENT") == "true":
        skip_kind = result.get("SKIP_KIND", "")
        reason = result.get("REASON", "")
        return ReplayRow(
            entry=index,
            step=recorded.step,
            agent=recorded.agent,
            base_tier=recorded_base,
            recorded_delta=recorded_delta,
            replay_delta=None,
            trigger="",
            rationale="",
            config_complexity=result.get("CONFIG_COMPLEXITY", ""),
            next_step=recorded_next.step if recorded_next else "",
            next_agent=recorded_next.agent if recorded_next else "",
            skip_kind=skip_kind,
            note=f"skipped: {reason}",
        )

    raw_adjustment = result.get("COMPLEXITY_ADJUSTMENT", "")
    replay_adjustment = (
        ComplexityAdjustment.from_json(raw_adjustment) if raw_adjustment else None
    )
    effective = result.get("CONFIG_COMPLEXITY", "")
    resolved_step = result.get("WORKFLOW_STEP", "")
    resolved_agent = result.get("ROLE_NAME", "")
    next_step = result.get("WORKFLOW_NEXT", "")
    if recorded_next is not None:
        next_agent = recorded_next.agent
        # The run recorded where it actually went; prefer that over an empty
        # forward edge (a terminal step still has a recorded successor when the
        # engine advanced through a parent return).
        next_step = next_step or recorded_next.step
    else:
        next_agent = _next_agent(
            result.get("WORKFLOW_NAME", ""), next_step, workflows_dir, local_workflows_dir
        )

    # The recorded decision is the live run's fact; the replay's is the
    # recomputation. Report the recorded one when it exists so a disagreement
    # is visible in the delta columns instead of being papered over.
    shown = recorded_adjustment or replay_adjustment
    note = ""
    if result.get("RUN_SCRIPT") == "true":
        note = "script step"
    elif not (resolved_step == recorded.step and resolved_agent == recorded.agent):
        note = f"replay resolved {resolved_agent}/{resolved_step}"

    return ReplayRow(
        entry=index,
        step=recorded.step,
        agent=recorded.agent,
        base_tier=recorded_base or _base_tier(effective, replay_adjustment),        recorded_delta=recorded_delta,
        replay_delta=replay_adjustment.delta if replay_adjustment else None,
        trigger=shown.trigger if shown is not None else "",
        rationale=shown.rationale if shown is not None else "",
        config_complexity=effective,
        next_step=next_step,
        next_agent=next_agent,
        resolved_step=resolved_step,
        resolved_agent=resolved_agent,
        note=note,
    )


def replay_entries(
    state: WorkflowState,
    *,
    workflows_dir: str,
    prompt_base: str,
    local_workflows_dir: Optional[str] = None,
    entries: Optional[Sequence[int]] = None,
) -> list[ReplayRow]:
    """Replay every history entry (or just ``entries``) of ``state``."""
    indices = (
        list(range(len(state.history))) if entries is None else list(entries)
    )
    return [
        replay_entry(
            state,
            index,
            workflows_dir=workflows_dir,
            prompt_base=prompt_base,
            local_workflows_dir=local_workflows_dir,
        )
        for index in indices
    ]


# ── Policy provenance ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class PolicyProvenance:
    """Which policy and environment the replay resolved against."""

    config_path: str
    repo_layer: bool
    auto_adjust: bool
    max_auto_delta: int
    env: dict[str, str]
    pinned_policy_differs: bool


def policy_provenance(state: Optional[WorkflowState] = None) -> PolicyProvenance:
    """Resolve the checkout's policy and record where it came from."""
    from nebuko.engine.lib.complexity_governor.integration import (
        load_repo_policy_layer,
        resolve_run_policy,
    )

    repo_layer = load_repo_policy_layer()
    policy = resolve_run_policy(repo_layer)
    differs = False
    pinned = getattr(state, "complexity_policy", None) if state is not None else None
    if pinned is not None:
        try:
            differs = json.loads(pinned.model_dump_json()) != json.loads(
                policy.model_dump_json()
            )
        except Exception:  # noqa: BLE001 - provenance must never break a replay
            differs = False
    return PolicyProvenance(
        config_path=os.environ.get("AGENT_CONFIG_PATH") or "agent-config.yaml",
        repo_layer=repo_layer is not None,
        auto_adjust=policy.auto_adjust,
        max_auto_delta=policy.max_auto_delta,
        env={name: os.environ.get(name, "") for name in _PROVENANCE_ENV},
        pinned_policy_differs=differs,
    )


def render_provenance(
    provenance: PolicyProvenance,
    state: WorkflowState,
    source: str,
) -> str:
    """Header block: what was replayed and against which policy/environment."""
    layer = "builtin + repo layer" if provenance.repo_layer else "builtin only"
    lines = [
        f"Replay: {source}",
        (
            f"  state: run_id={state.run_id} workflow={state.workflow or '-'} "
            f"step={state.step or '-'} history={len(state.history)} entries"
        ),
        (
            f"  policy: {layer} ({provenance.config_path}); "
            f"auto_adjust={provenance.auto_adjust} "
            f"max_auto_delta={provenance.max_auto_delta}"
        ),
    ]
    env_bits = [
        f"{name}={value or '(unset)'}" for name, value in provenance.env.items()
    ]
    lines.append("  env: " + " ".join(env_bits))
    if provenance.pinned_policy_differs:
        lines.append(
            "  WARNING: the state's pinned policy differs from this checkout's — "
            "the replay may not reproduce the live decisions"
        )
    return "\n".join(lines)


# ── Rendering ─────────────────────────────────────────────────────────────


def _delta(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return f"{value:+d}" if value else "0"


def render_table(rows: Sequence[ReplayRow]) -> str:
    """Fixed-width table, one row per invocation."""
    headers = [
        "entry",
        "step",
        "agent",
        "base",
        "recΔ",
        "replayΔ",
        "CONFIG_COMPLEXITY",
        "next",
        "trigger / rationale",
    ]
    body: list[list[str]] = []
    for row in rows:
        step = row.step or "-"
        if not row.matches:
            step = f"{step}*"
        next_bit = row.next_step or "(end)"
        if row.next_agent:
            next_bit = f"{next_bit} ({row.next_agent})"
        trigger = row.trigger or "-"
        if row.rationale:
            trigger = f"{trigger}: {row.rationale}"
        if row.note:
            trigger = f"{trigger} [{row.note}]"
        body.append(
            [
                str(row.entry),
                step,
                row.agent or "-",
                row.base_tier or "-",
                _delta(row.recorded_delta),
                _delta(row.replay_delta),
                row.config_complexity or "-",
                next_bit,
                trigger,
            ]
        )
    widths = [
        max(len(headers[i]), *(len(line[i]) for line in body)) if body else len(headers[i])
        for i in range(len(headers))
    ]
    def fmt(cells: Sequence[str]) -> str:
        return "  ".join(
            cell.ljust(widths[i]) if i < len(cells) - 1 else cell
            for i, cell in enumerate(cells)
        ).rstrip()
    lines = [fmt(headers), fmt(["-" * width for width in widths])]
    lines.extend(fmt(line) for line in body)
    if any(not row.matches for row in rows):
        lines.append("")
        lines.append("* replay resolved a different step/agent than the run recorded")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    import nebuko.engine.lib.default_paths as default_paths

    parser = argparse.ArgumentParser(
        prog="python -m nebuko.engine.lib.replay",
        description=(
            "Deterministically replay the engine's per-invocation routing and "
            "complexity decisions from a workflow state (no LLM, no writes)."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--issue", type=int, help="Issue number to fetch the state from"
    )
    source.add_argument(
        "--state", help="Local workflow-state JSON file (or a marked comment body)"
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="Repository owner/name for --issue (default: GITHUB_REPOSITORY)",
    )
    parser.add_argument(
        "--entry",
        type=int,
        default=None,
        help="History entry index to replay (0 = the seed invocation)",
    )
    parser.add_argument(
        "--all-entries",
        action="store_true",
        help="Replay every history entry (the default when --entry is absent)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit rows as JSON instead of a table"
    )
    parser.add_argument(
        "--workflows-dir",
        default=None,
        help="Shipped workflows dir (default: the checkout's defaults/workflows)",
    )
    parser.add_argument(
        "--local-workflows-dir",
        default=None,
        help="Repo-local workflows dir taking precedence over --workflows-dir",
    )
    parser.add_argument(
        "--prompt-base",
        default=None,
        help="Prompt files directory (default: the checkout's defaults/prompts)",
    )
    args = parser.parse_args(argv)
    if args.workflows_dir is None or args.local_workflows_dir is None:
        shipped, local = default_paths.workflow_dirs()
        if args.workflows_dir is None:
            args.workflows_dir = shipped
        if args.local_workflows_dir is None:
            args.local_workflows_dir = local
    if args.prompt_base is None:
        args.prompt_base = default_paths.config_dir("prompts", "PROMPT_BASE")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _parse_args(argv)

    if args.state:
        try:
            with open(args.state, "r", encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            print(f"error: cannot read {args.state}: {exc}", file=sys.stderr)
            return 2
        state = load_state_from_text(text)
        source = f"state file {args.state}"
    else:
        try:
            state = load_state_from_issue(args.issue, args.repo)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        source = f"issue #{args.issue} ({args.repo})"

    if state is None:
        print(f"error: no WORKFLOW_STATE found in {source}", file=sys.stderr)
        return 1
    if not state.history:
        print(f"error: {source} carries no history to replay", file=sys.stderr)
        return 1

    entries: Optional[list[int]] = None
    if args.entry is not None:
        if args.entry < 0 or args.entry >= len(state.history):
            print(
                f"error: --entry {args.entry} out of range "
                f"(state has {len(state.history)} entries, 0..{len(state.history) - 1})",
                file=sys.stderr,
            )
            return 2
        entries = [args.entry]

    rows = replay_entries(
        state,
        workflows_dir=args.workflows_dir,
        prompt_base=args.prompt_base,
        local_workflows_dir=args.local_workflows_dir,
        entries=entries,
    )

    if args.json:
        print(json.dumps([row.as_dict() for row in rows], indent=2))
        return 0

    provenance = policy_provenance(state)
    print(render_provenance(provenance, state, source))
    print()
    print(render_table(rows))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
