<!-- bot-workflow-terminal -->
<!-- bot-agent-context -->
<!-- workflow-continue -->
<details>
<summary><picture><source srcset="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-dark.svg" media="(prefers-color-scheme: dark)"><img src="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-light.svg" alt="Nebuko" width="16" height="16"></picture> Workflow State (run 2)</summary>

```json
<!-- WORKFLOW_STATE_START -->
{
  "run_id": 2,
  "schema_version": 4,
  "workflow": "default",
  "step": "context",
  "agent": "context",
  "phase": "analysis",
  "history": [
    {
      "step": "analyze",
      "agent": "architect",
      "summary": "## \ud83e\udded Architect Analysis \u2014 human_gate on the `plan` step is recorded but not honored (issue #885)\n\n### Summary of the bug\nThe `human_gate: true` on the `plan` step did **not** block execution: the run advanced to `implement` immediately after the architect posted its plan. The gate *was* recognized \u2014",
      "position": [
        {
          "workflow": "default",
          "step": "analyze"
        }
      ],
      "dispatch": {
        "task_hint": "context",
        "complexity": "high",
        "reason": "Fresh bug report; delegate high-depth context collection on human_gate handling, resolution/gate ordering, resume routing, workflow YAML, and persistence before planning a fix."
      },
      "comment_ids": [6117391863],
      "metrics": {
        "run_id": "00df6a6b-5a53-411d-8dac-ee1a5634acd0",
        "model_name": "default",
        "agent_role": "architect",
        "input_tokens": 33415,
        "output_tokens": 5354,
        "tool_call_latency_ms": 15078.480323155722,
        "agent_runtime_ms": 45235.44,
        "nb_tool_calls": 4,
        "start_time": "2026-10-12T03:47:56.233639",
        "end_time": "2026-10-12T03:48:42.014167+00:00",
        "agent_runtimes_per_call_ms": [25392.09, 7492.05, 12351.3],
        "tool_calls_per_tool": {
          "dispatch_next": 2,
          "finish": 2
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 11679,
            "output": 1459,
            "cache_hit": 0,
            "reasoning": 107
          }
        },
        "tokens_per_second": 133.36486261136272,
        "time_to_first_token": 1.524595326045528
      }
    },
    {
      "step": "context",
      "agent": "context",
      "summary": "## \ud83d\udcda Context Report \u2014 issue #885: `human_gate` on `plan` recorded but not honored\n\nRead-only collection from `/runner/_work/nebuko/nebuko` @ `HEAD` (`0819271ef0a9eabb719309e5dc1178d40db6623b`, \"add .nebuko/workflow/default-with-cmd workflow\"). Facts only \u2014 no analysis or recommendations.\n\n### FILES ",
      "position": [
        {
          "workflow": "default",
          "step": "context"
        }
      ],
      "dispatch": {
        "reason": "no-action default: agent emitted no decision and the AGENT_RUN_CONFIG sentinel could not be parsed (unparseable JSON (Expecting property name enclosed in double quotes at line 1 column 33)); returning to caller 'architect'",
        "origin": "engine_fallback"
      },
      "metrics": {
        "run_id": "a64cc167-f74c-42a2-ad63-3e95dacddbfe",
        "model_name": "default",
        "agent_role": "context",
        "input_tokens": 5408715,
        "output_tokens": 76762,
        "tool_call_latency_ms": 9171.377592086792,
        "agent_runtime_ms": 687853.32,
        "nb_tool_calls": 105,
        "tool_call_error_rate": 1.9230769230769231,
        "complexity": "high",
        "start_time": "2026-10-12T03:49:26.947437",
        "end_time": "2026-10-12T04:01:28.007383+00:00",
        "agent_runtimes_per_call_ms": [22094.14, 19674.36, 3297.36, 3059.32, 3819.81, 17469.48, 22504.77, 24924.68, 5774.53, 27390.89, 25964.24, 4648.89, 6547.34, 3782.11, 2968.03, 5835.13, 4955.74, 3579.49, 6796.08, 3214.52, 6241.17, 3049.9, 21140.82, 5040.59, 10005.49, 2120.28, 15025.83, 22624.61, 3134.42, 5634.79, 2653.97, 5833.06, 3106.44, 2862.01, 25956.14, 5480.36, 9814.52, 1853.95, 8594.4, 5823.67, 3251.43, 3007.23, 9579.77, 1849.53, 15602.99, 12931.76, 2656.25, 33847.82, 43487.66, 16935.37],
        "tool_calls_per_tool": {
          "terminal": 99,
          "file_editor": 4,
          "finish": 1,
          "dispatch_next": 1
        },
        "errors_per_tool": {
          "unknown": 2
        },
        "tokens_per_tool": {
          "unknown": {
            "input": 6568,
            "output": 2265,
            "cache_hit": 0,
            "reasoning": 730
          }
        },
        "tokens_per_second": 158.58832831407935,
        "time_to_first_token": 1.7934141204585448
      }
    }
  ],
  "current_context": {
    "last_run_status": "success",
    "step_outputs": [
      {
        "step": "analyze",
        "comment_id": "6117391863"
      },
      {
        "step": "context"
      }
    ]
  },
  "dispatch": {
    "reason": "no-action default: agent emitted no decision and the AGENT_RUN_CONFIG sentinel could not be parsed (unparseable JSON (Expecting property name enclosed in double quotes at line 1 column 33)); returning to caller 'architect'",
    "origin": "engine_fallback"
  },
  "resolution": {
    "task": "context",
    "input_workflow": "default",
    "input_step": "context",
    "input_complexity": "high",
    "role_name": "context",
    "prompt_location": "/runner/_work/nebuko/nebuko/defaults/prompts/context.md",
    "agent_selected": "true",
    "selected_workflow": "default",
    "selected_step": "context",
    "next_step": "plan",
    "reasoning": "none",
    "matched_rule": "workflow:default/step:context",
    "timestamp": "2026-10-12T04:01:28.009467+00:00"
  },
  "callback": "architect",
  "workflow_ref": "nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@refs/heads/main",
  "position": [
    {
      "workflow": "default",
      "step": "context"
    }
  ],
  "complexity_policy": {
    "bands": {
      "low": {
        "min_score": 1,
        "guidance": "LOW \u2014 localized, single-concern change: one file or a tight cluster, no design ambiguity, no cross-module contract change, no open questions. If it fits in a single focused edit, it is LOW."
      },
      "medium": {
        "min_score": 1,
        "guidance": "MEDIUM \u2014 the default working tier: a bounded multi-file change with a clear approach, a couple of touch points, no unresolved design choice. Most feature work and bug fixes belong here."
      },
      "high": {
        "min_score": 3,
        "any_of": ["multi-issue-linkage", "reopened-issue", "high-token-burn", "large-scope", "human-friction"],
        "guidance": "HIGH \u2014 reserved for genuinely heavy work: multiple linked issues or reopened problem, cross-module/architectural change, sustained large context burn, or repeated human change-requests. Reach for HIGH only when the evidence below actually fires; an unproven HIGH erodes the cost/value of the tier."
      }
    },
    "signal_rules": [
      {
        "signal": "multi-issue-linkage",
        "family": "context",
        "field": "linked_issues",
        "threshold": 2.0
      },
      {
        "signal": "reopened-issue",
        "family": "context",
        "field": "reopened",
        "threshold": 1.0
      },
      {
        "signal": "high-token-burn",
        "family": "llm",
        "field": "input_tokens",
        "threshold": 150000.0
      },
      {
        "signal": "large-scope",
        "family": "llm",
        "field": "nb_tool_calls",
        "threshold": 40.0
      },
      {
        "signal": "human-friction",
        "family": "human",
        "field": "changes_requested",
        "threshold": 2.0
      }
    ],
    "target_distribution": {
      "low": 0.35,
      "medium": 0.5,
      "high": 0.15
    }
  }
}
<!-- WORKFLOW_STATE_END -->
```

</details>

*Context answer*

## 📚 Context Report — issue #885: `human_gate` on `plan` recorded but not honored

Read-only collection from `/runner/_work/nebuko/nebuko` @ `HEAD` (`0819271ef0a9eabb719309e5dc1178d40db6623b`, "add .nebuko/workflow/default-with-cmd workflow"). Facts only — no analysis or recommendations.

<details>
<summary>FILES FOUND — Workflow definition</summary>

### FILES FOUND

**Workflow definition**
- `defaults/workflows/default.yaml` — the `default` workflow; `plan` step is `human_gate: true`.
- `.nebuko/workflows/default-with-cmd.yaml` — consumer-local workflow at HEAD; name `default-with-command` (adds `git_diff`/`run_tests` script steps).
- `defaults/prompts/architect.md` — documents the single planning artifact behind the `human_gate:plan` approval.

**Gate interception (writer side)**
- `src/nebuko/engine/lib/agent_script/features/build_output/build_output.py` — `apply_human_gate_intercept` (line 390), `_load_gated_step_def` (line 362), call site (line 1144), `build_state(...)` invocation (line ~1155), resolution env capture (lines 559-580).

**Routing / control flow (reader side)**
- `src/nebuko/engine/lib/resolve_config/resolve_config.py` — `resolve_config()` facade + the routing cascade (lines 249-384).
- `src/nebuko/engine/lib/resolve_config/features/resolve_route/resolve_route.py` — `_resolve_dispatch_path` (602), `_resolve_resume_path` (141), `_resolve_gate_decision_path` (~317), `_resolve_bot_fallback_path` (1704), `_resolve_workflow_next_step` (1823), `_is_human_gate_hold` (312), `_is_human_gate_step` (1030), `_callback_return_target` (1503).
- `src/nebuko/engine/lib/resolve_workflow.py` — `resolve_next_from_workflow` (2446); linear-`next` resolution (2560-2592); marker emission (`WORKFLOW_STEP`, line 1787).
- `src/nebuko/engine/models.py` — `to_routing_decision` (357), `RunOutcome`/`SkipKind` (100-160).
- `src/nebuko/engine/run_loop.py` — `_EXIT_CODE_BY_STATUS` (91-97), `_exit_code_for` (154), `route` (1569), `publish` (1498), `persist` (1536).

**Trigger re-parse / hand-off sentinel**
- `src/nebuko/engine/lib/prepare_comment.py` — `render_handoff_sentinel` (160), `_DISPATCH_SENTINEL_FIELDS` (137), `_continue_token` (498), `_LIVE_STATUSES` (479), `_format_answer_header` (504), `escape_agent_triggers` (361).
- `src/nebuko/engine/lib/resolve_config/features/parse_trigger/parse_trigger.py` — `_parse_trigger_body` (295), `MACHINE_TOKENS` (45), `is_machine_comment` (106).
- `src/nebuko/engine/agent_host.py` — `_routing_payload` (226), `_state_routing_fields` (252).

**Persistence (schema v4)**
- `src/nebuko/engine/lib/workflow_state/workflow_state.py` — `STATE_SCHEMA_VERSION = 4` (line 134), `WorkflowState`.
- `src/nebuko/engine/lib/workflow_state/features/build_state/build_state.py` — `build_state` / `_build_state`.
- `src/nebuko/engine/lib/workflow_state/sub_domains/step_resolution/step_resolution.py` — `StepResolution` (`next_step` line 61, `selected_step` line 60).
- `src/nebuko/engine/lib/workflow_state/features/get_last_state/get_last_state.py` — `extract_state_from_body` (19).
- `src/nebuko/engine/lib/workflow_state/sub_domains/dispatch/features/get_dispatch_from_state/get_dispatch_from_state.py` — `extract_dispatch_from_state` (7).

**Tests & fixtures**
- `src/nebuko/engine/lib/test_resolve_config.py` — resume/hold tests (`_build_state_json`, line 2369; `test_resume_reruns_held_step`; `test_resume_with_advance_dispatch_advances_to_next_step`).
- `src/nebuko/engine/lib/resolve_config/features/resolve_route/test_resolve_route.py` — `_is_human_gate_hold`, callback/gate precedence (`test_hold_outranks_callback` 619; `test_engine_fallback_advance_passes_the_gate` 634).
- `src/nebuko/engine/lib/resolve_config/features/resolve_route/test_gate_decision.py` — `_resolve_gate_decision_path` / `_resolve_resume_path`; `_held_state()` helper.
- `src/nebuko/engine/lib/test_full_state_routing.py` — full-`workflow_state` replay harness through `resolve_config()` (the exact shape a regression test would use).
- `src/nebuko/engine/lib/fixtures/` — `workflow_state_issue648_scaffold.json`, `workflow_state_issue657_step1_review_approved.json`, `workflow_state_issue793_soft_kill.json`, `workflow_state_issue864_context_hold.json`.

</details>

<details>
<summary>RELEVANT SNIPPETS — 1. Workflow YAML — the gated `plan` step (`defaults/workflows/default.yaml:21-30`)</summary>

### RELEVANT SNIPPETS

**1. Workflow YAML — the gated `plan` step (`defaults/workflows/default.yaml:21-30`)**
```yaml
  - name: plan
    agent: architect
    inputs: [context]
    next: implement
    human_gate: true
    conditions:
      - on_result: approved
        next: implement
      - on_result: rejected
        next: context
```
HEAD variant `.nebuko/workflows/default-with-cmd.yaml` is identical for `plan` (lines 21-30); its workflow is named `default-with-command`.

**2. Gate intercept is applied before `build_state` (`build_output.py:1127-1150`)**
```python
    # Applied before the human-gate intercept so a gated step still holds.
    if is_graceful_timeout:
        dispatch = _soft_kill_dispatch()
        ...
    # ── Human gate intercept (issues #506, #648) ─────────────────
    ...
    is_human_resume = bool(os.environ.get("HUMAN_RESUME_INPUT", "").strip())
    soft_retry_attempt = (...)
    dispatch = apply_human_gate_intercept(
        dispatch,
        workflow_name=workflow_name,
        workflow_step=workflow_step,
    )
```

**3. The intercept itself (`build_output.py:390-428`)** — converts a completing dispatch to `hold`:
```python
    if dispatch is not None and dispatch.status not in ("advance", "soft_kill"):
        return dispatch
    if not _load_gated_step_def(workflow_name, workflow_step):
        return dispatch
    ...
    return dispatch.model_copy(
        update={
            "status": "hold",
            "reason": f"human_gate:{workflow_step}",
            "branch": None,
        }
    )
```
The **overridden `dispatch`** is what is handed to `build_state(..., dispatch=dispatch, ...)` (line ~1173) — this is why `history[plan].dispatch` and top-level `dispatch` show the hold in the pasted state.

**4. `resolution` is captured verbatim from the resolver env, not recomputed (`build_output.py:559-580`)**
```python
        selected_step=os.environ.get("WORKFLOW_STEP", ""),
        next_step=os.environ.get("WORKFLOW_NEXT", ""),
        ...
        matched_rule=os.environ.get("MATCHED_RULE", ""),
        reasoning=os.environ.get("LLM_REASONING", "none"),
```
`WORKFLOW_NEXT=implement` originates from the workflow YAML (`defaults/workflows/default.yaml:24`) at the `plan` step's *entry* resolution — i.e. `resolution.next_step` is the ungated forward edge.

**5. The dispatch is persisted but not consulted on the state-only path.** `get_last_state.extract_state_from_body` (line 19) reconstructs `prev_state.dispatch`; `resolve_config` cascade (`resolve_config.py:373-377`):
```python
    elif prev_state and dispatch_obj:
        # Auto-dispatch: workflow state with dispatch object — follow the workflow.
        config = _resolve_dispatch_path(prev_state, dispatch_obj, workflows_dir, local_workflows_dir=local_workflows_dir)
    elif prev_state and prev_state.workflow:
        config = _resolve_workflow_next_step(prev_state, dispatch_obj, workflows_dir, local_workflows_dir=local_workflows_dir)
```

**6. `_resolve_dispatch_path` gates step-transition on `resolve_next_from_workflow` succeeding (`resolve_route.py:684-740`)**
```python
        wf_result = resolve_next_from_workflow(
            prev_state.workflow, prev_state.step, workflows_dir,
            agent_hint=None, branch_result=branch_result, ...
        )
        if wf_result:
            ...
            config["task"] = wf_result["task"]
            config["config_step"] = wf_result.get("step", config["config_step"])
        else:
            dispatch_status = dispatch_obj.status or ""
            if dispatch_status == "hold":
                return {"SKIP_AGENT": "true", "REASON": "Dispatch status: hold"}
```
Because `plan → implement` resolves successfully, the `else` (hold) branch is **never reached** for a `default` state. `_is_human_gate_hold` is not checked in this branch — it is checked only in `_resolve_resume_path` (line 182) and `_is_human_gate_step` is checked only in the `function_call` hand-off path (`_resolve_handoff_decision`, lines 1070-1090).

**7. `_resolve_bot_fallback_path`'s hold branch is also unreachable when `advance` resolves (`resolve_route.py:1745-1795`)** — same structure: `if dispatch.status == "advance": ... elif ... elif dispatch.status == "hold": return {"SKIP_AGENT": "true", "REASON": "Dispatch status: hold"}`.

**8. `_resolve_workflow_next_step` ignores status entirely** and its docstring explicitly states (`resolve_route.py:1808-1811`):
```
    The stored ``resolution.next_step`` is deliberately NOT used: it denotes
    the *next* step, so consulting it would skip a step.
```
`grep` confirms `resolution.next_step` has **no reader** in non-test engine code besides this comment.

**9. Continue-token gating (writer side)** — `prepare_comment.py:479-501`:
```python
_LIVE_STATUSES = ("advance", "loop", "soft_kill")
...
def _continue_token(state: WorkflowState) -> str:
    """The continue token for live dispatches; empty for hold/terminate."""
    status = state.dispatch.status if state.dispatch else "advance"
    return CONTINUE_TOKEN if status in _LIVE_STATUSES else ""
```
`_format_answer_header` (504) prepends `CONTINUE_TOKEN` only for live statuses; `render_handoff_sentinel` (160) emits `` from the intercepted state. `escape_agent_triggers` (361) only neutralizes `@@​agent` tokens, not the sentinel.

**10. GHA trigger gate (`nebuko-workflow.yaml:186-204`)** — a bot comment re-triggers on `contains(... '"function_call"')`, `'<!-- workflow-continue -->'`, `'<!-- bot-trigger:'`; the gate-command path requires `author_association ∈ {OWNER,MEMBER,COLLABORATOR}`:
```yaml
                contains(github.event.comment.body, '"function_call"') ||
                contains(github.event.comment.body, '<!-- workflow-continue -->') ||
                contains(github.event.comment.body, '<!-- bot-trigger:'))) ||
              (contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), ...) &&
               ...
               (contains(github.event.comment.body, '/nebuko approve gate') ||
                contains(github.event.comment.body, '/nebuko reject gate')))
```

**11. Exit code mapping (`run_loop.py:91-97`)** — `"hold": ExitCode.HOLD` (distinct from `"advance": ExitCode.ADVANCE`).

**12. Reproduction (empirical).** Replaying the pasted `workflow_state` (verbatim, minus the optional `complexity_policy` block) through `resolve_config()` against `defaults/workflows/`:
```
--- bot: state only (no sentinel) (is_bot=True) ---
   TASK='dev'  ROLE_NAME='dev'  WORKFLOW_STEP='implement'  SKIP_AGENT='false'
--- bot: engine sentinel (hold) (is_bot=True) ---
   TASK='dev'  ROLE_NAME='dev'  WORKFLOW_STEP='implement'  SKIP_AGENT='false'
--- bot: no status (=> advance) (is_bot=True) ---
   TASK='dev'  ROLE_NAME='dev'  WORKFLOW_STEP='implement'  SKIP_AGENT='false'
--- human: /nebuko resume (is_bot=False) ---
   SKIP_AGENT='true'  REASON="resume: step 'plan' is held by a human gate — use `/nebuko approve gate` or `/nebuko reject gate` to release it"
--- human: approve gate (is_bot=False) ---
   TASK='dev'  WORKFLOW_STEP='implement'  GATE_DECISION='{"status":"approved", ... "target_step":"plan" ...}'
--- human: reject gate (is_bot=False) ---
   TASK='context'  WORKFLOW_STEP='context'  GATE_DECISION='{"status":"rejected", ... "target_step":"plan" ...}'
```
Observation: with `dispatch.status == "hold"` present in the state, bot routing still resolves `implement`/dev (gate not honored); `/nebuko resume` correctly holds; the gate commands correctly route (approved → implement, rejected → context). The prior run's `dispatch.status` on the hold-transition path only produces `SKIP_AGENT` in `_resolve_dispatch_path`/`_resolve_bot_fallback_path` when `resolve_next_from_workflow` fails to resolve a successor (terminal steps).

</details>

<details>
<summary>EXTERNAL REFERENCES</summary>

### EXTERNAL REFERENCES

- GitHub issue (this report): nebuko/nebuko#885.
- Referenced issues in code/docs: #648 (engine-owned `GateDecision`, gate release), #733 (gate precedence over `function_call` hand-off), #830 (typed dispatch / engine-rendered sentinel), #765 (callback / agent-as-tools), #864 (`origin='engine_fallback'` no-action default), #671 (state carry-forward), #704 (soft-kill), #795 (human-action notice), #478 (continue token).
- Docs: `docs/architecture.md` § "The `human_gate` intercept" (~lines 256-280); `docs/workflows.md` § "Human Gates" (~149-235).

```json
{
  "references": [
    {"type": "issue", "repo": "nebuko/nebuko", "number": 885, "reason": "Reported bug: human_gate on plan ignored; workflow advanced to implement"},
    {"type": "issue", "repo": "nebuko/nebuko", "number": 648, "reason": "Engine-owned GateDecision; /nebuko resume removed as gate bypass"},
    {"type": "issue", "repo": "nebuko/nebuko", "number": 733, "reason": "Gate precedence in function_call hand-off (_is_human_gate_step)"},
    {"type": "issue", "repo": "nebuko/nebuko", "number": 830, "reason": "Typed dispatch + engine-rendered AGENT_RUN_CONFIG sentinel"},
    {"type": "issue", "repo": "nebuko/nebuko", "number": 765, "reason": "Callback return address; _callback_return_target"},
    {"type": "issue", "repo": "nebuko/nebuko", "number": 864, "reason": "Engine fallback no-action default (origin=engine_fallback)"},
    {"type": "issue", "repo": "nebuko/nebuko", "number": 671, "reason": "Workflow-state carry-forward / codec"},
    {"type": "issue", "repo": "nebuko/nebuko", "number": 704, "reason": "Soft-kill retry route (holds for a human when exhausted)"},
    {"type": "issue", "repo": "nebuko/nebuko", "number": 478, "reason": "workflow-continue token gating by dispatch status"}
  ]
}
```

</details>

<details>
<summary>OTHER FILES</summary>

### OTHER FILES

- `src/nebuko/engine/lib/config/sub_domains/agent_run_config/agent_run_config.py` — `DispatchStatus` Literal (`"advance" | "loop" | "hold" | "terminate" | "soft_kill"`, line 27).
- `src/nebuko/engine/lib/workflow_state/sub_domains/dispatch/dispatch.py` — `Dispatch` model (`status`, `origin`, `task_hint`, `reason`, `branch`).
- `src/nebuko/engine/lib/config/sub_domains/human_action_notice/human_action_notice.py` — `classify()` (line 83) → `awaiting_approval` for a `hold` whose `reason` starts with `human_gate` (line 112).
- `src/nebuko/engine/agent_runner.py` — `run_legacy_agent` returns `{"answer", "dispatch", "final_message", "state"}` (the child→parent seam).
- `src/nebuko/adapters/github.py` — `save_state` (179) → `WorkflowStateCodec().save_prev(state)`; `load_state` (173); `artifact_dir` (202).
- `src/nebuko/engine/lib/workflow_state/test_state_codec.py` — codec round-trip; `GateDecision` import; reproduces #648 anomaly.
- `defaults/agents/agent-config.yaml` — agent roster referenced by `TASK_TO_PROMPT`.

---

**Collection status:** complete; all seven requested areas (gate handling in the run loop, resolution vs. gate ordering, resume path, workflow YAML, schema-v4 persistence, tests/fixtures, and pointers) are covered with verbatim snippets and line numbers. Returning to `architect` at high complexity for the gated planning artifact.

<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "reason": "no-action default: agent emitted no decision and the AGENT_RUN_CONFIG sentinel could not be parsed (unparseable JSON (Expecting property name enclosed in double quotes at line 1 column 33)); returning to caller 'architect'"}} -->
</details>