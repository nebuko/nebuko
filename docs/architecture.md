# Architecture

High-level system architecture of nebuko: how a GitHub event becomes an
agent run, the 8-agent model, the orchestration loop, state management, and
the routing cascade. For the internal DDD package layout, see
[architecture_code.md](./architecture_code.md). For step-by-step details of
the GitHub Actions job, see [default-workflow.md](./default-workflow.md).

---

## System Overview

nebuko is a multi-agent AI orchestration system built on GitHub Actions +
the OpenHands SDK. All collaboration happens inside GitHub issues: agents post
comments, persist state in those comments, and trigger each other through
dispatch sentinels. There is no server or database — the issue thread *is* the
state store.

```
┌────────────┐   ┌────────────────────────── GHA Orchestrator ──────────────────────────┐   ┌──────────────┐
│            │   │                                                                      │   │              │
│  GitHub    │   │  event_normalizer.py → resolve_config.py → loop_guard /             │   │  Issue       │
│  Event     ├──►│  circuit_breaker → query_context.py → agent_script.py               ├──►│  Comment     │
│ (issue /   │   │  (OpenHands Agent + Conversation via arun())                        │   │ (bot marker, │
│  comment)  │   │  → prepare_comment.py → github_client.py                            │   │  state JSON, │
│            │   │                                                                      │   │  metrics)    │
└────────────┘   └──────────────────────────────────────────────────────────────────────┘   └──────────────┘
```

The orchestrator is `.github/workflows/nebuko-workflow.yaml` — a reusable
workflow (consumers call it from their own `workflow.yaml`) with three jobs:
`run-task` (the agent pipeline, ~24 steps), `agent-metrics` (runs on issue
close), and `subtask-completion-notifier` (notifies a parent issue when a
sub-issue's split plan completes). It runs on GitHub-hosted runners by default;
callers can select the runner via the `runs_on` workflow input (e.g.
`runs_on: "self-hosted"` for their own runners).

---

## The 5-Agent Model

| Agent | Role | Tools |
|-------|------|-------|
| `architect` | Principal engineer — analysis, architecture, planning; delegates everything else. Never writes code. | think, finish |
| `context` | Context collection — locates files, extracts snippets, reports sources. Read-only; never modifies code. | terminal, file_editor, task_tracker, think, finish |
| `dev` | Execution developer — implements plans, writes tests, opens PRs. | terminal, file_editor, task_tracker, think, finish |
| `review` | Senior code reviewer — inspects diffs, issues approved/rejected verdicts; also runs issue retrospectives. | think, finish |
| `po` | Product owner — writes WHEN/IF/THEN specs and refines issue drafts with humans. | think, finish |

Each agent = a prompt file (`.agents/prompts/{agent}.md`) + a model reference +
a tool whitelist, declared in [`agent-config.yaml`](../defaults/agents/agent-config.yaml).
Tools are a security boundary: planning agents (`architect`, `review`, `po`)
physically cannot touch files, while execution agents (`dev`, `context`)
can. Full details in [agents-and-skills.md](./agents-and-skills.md).

---

## The Orchestration Loop

Each trigger runs the following sequence inside the `run-task` job:

1. **Normalize** — `event_normalizer.py` converts the GitHub event payload
   into `TARGET_NUMBER`, `TRIGGER_BODY`, `TRIGGER_AUTHOR`, `EVENT_TYPE`.
2. **Resolve config** — `resolve_config.py` applies the routing cascade
   (below) and emits `ROLE_NAME`, `PROMPT_LOCATION`, `WORKFLOW_NAME`,
   `WORKFLOW_STEP`, `WORKFLOW_NEXT`.
3. **Guards** — `loop_guard.py` skips same-agent loops and human+agent pings;
   `circuit_breaker.py` aborts when bot-marker count exceeds the threshold
   (default 15).
4. **Action** — when the resolver emitted an agent-free deterministic step
   (`DELETE_COMMENTS=true` / `RUN_SCRIPT=true`), the engine executes it
   directly and ends the run with a success exit (issue #724); ordinary
   agent runs pass through unchanged.
5. **Build context** — `query_context.py` fetches issue comments, extracts
   the latest `WORKFLOW_STATE`, builds the history section, and formats the
   agent prompt against the template.
6. **Execute** — `agent_script.py` loads the prompt, resolves the LLM config,
   and runs an OpenHands `Agent` + `Conversation` under a soft-timeout watchdog
   (see [features.md — Execution Layer](./features.md#execution-layer)).
7. **Validate output** — `validate_output.py` checks the dispatch sentinel and
   forbidden sections.
8. **Post comment** — `prepare_comment.py` strips internal tags; the cleaned
   message is posted via `github_client.py` with bot markers prepended.
9. **Persist state** — a fresh full-snapshot `WORKFLOW_STATE` JSON is embedded
   in a collapsed section of the posted comment.
10. **Route next** — the `dispatch` field in the new state determines the next
   step (or ends the run). If a `<splitPlan>` was emitted, the
   **Process Split Plan** step creates sub-issues.

**Agent-start acknowledgment (issue #720).** Just before the agent is executed,
the engine posts the theme-adaptive Nebuko logo mark (issue #778) followed by
`**{role} agent started** — [View workflow run logs](…)` — the logo is a
single-line `<picture>` fragment whose `<source>` serves dark-theme viewers
`mark-dark.svg` and whose `<img>` fallback serves light-theme viewers
`mark-light.svg` (`alt="Nebuko"`, 16×16):

```html
<picture><source srcset="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-dark.svg" media="(prefers-color-scheme: dark)"><img src="https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-light.svg" alt="Nebuko" width="16" height="16"></picture> **{role} agent started** — [View workflow run logs](…)
```

The comment is posted through the platform adapter —
`Engine.notify_agent_started()` renders an `AgentStartNotice` and posts it,
tagged `<!-- bot-agent-{role} -->` exactly like the monolith's
`Post Acknowledgment Comment` step did. The notice is
**engine-owned**: an engine-driven workflow file must not run its own ack step
(that double-posts), and a failed notice is logged, never fatal to the run.

---

## State Management

State lives in issue comments as collapsed JSON between
`<!-- WORKFLOW_STATE_START -->` / `<!-- WORKFLOW_STATE_END -->` markers (schema:
[reference.md — WORKFLOW_STATE v1](./reference.md#workflow_state-v1)).

Key properties:
- **Full snapshots** — every step posts a complete state (with accumulated
  `history[]`), not a delta. Any comment therefore carries enough to
  reconstruct the run.
- **Last one wins** — the most recent state found in a comment body is
  authoritative.
- **Transitions are pure** — `go_next()` / `build_state()` take the previous
  state + inputs and return a *new* `WorkflowState` (Pydantic model); nothing
  is mutated in place.
- **Pinning** — comments whose full content must reach the next agent are
  injected **verbatim**; every other included comment is injected as a **summary**
  (or a short excerpt when no summary marker is present). A comment is verbatim
  when its id is listed in `pinned_comment_ids` **or** it belongs to a history
  entry with `pin=True`. The retention policy lives in
  `features/select_history_comments/` (`HistorySelection`); `query_context.py`
  consumes it when building the `## History` section. Summaries keep the context
  small (and its cost bounded) while pinned comments preserve high-fidelity text.

---

## Agent Answer & Posting Pipeline (issue #522)

A single uniform mechanism decides what gets posted to the issue, identical
for every agent (no per-agent code paths):

1. **Capture layer** (`build_output.py`) — after the run, the agent's **last
   substantive text answer** (latest assistant `MessageEvent`, latest-wins) is
   written to `latest_answer.md`. `finish` tool actions are excluded: under the
   shared `posting-contract` skill, the finish message is a short status note.
2. **ETL layer** (`prepare_comment.py`) — one parser for all agents:

   | Input | Posted body |
   |---|---|
   | `latest_answer.md` non-empty | The latest answer (the deliverable) |
   | missing/empty | The finish message → byte-identical legacy behavior |

   Invariants enforced regardless of agent:
   - **Hand-off sentinel preservation** — `AGENT_RUN_CONFIG` sentinels are
     merged from the answer and the finish message into the posted body exactly
     once (last payload wins), so dispatch/branching always reach the comment.
   - **Small state snapshots** — `WorkflowState.summary` stays derived from the
     concise finish message (`[:300]`), keeping snapshots compact.

   Size handling is unchanged: 10k-char split → part files → 60k truncate →
   multi-part posting.

**Shared contract** — the rule "last text answer = full deliverable; `finish`
message = 2–3 sentence status + hand-off sentinel" lives in a single skill
file, [`skills/posting-contract.md`](../defaults/skills/posting-contract.md), attached to
every agent via the `skills:` list in `agent-config.yaml` — no per-prompt edits.

> **TODO (follow-up):** a global `core_skills` key in `agent-config.yaml` that
> applies to every agent automatically, so new agents don't have to remember
> the list (tracked separately from #522).

---

## Routing Cascade

`resolve_config.py` decides which agent runs, in priority order
(human commands always beat auto-routing):

```
trigger body
│
├─ /nebuko resume ──────────────────► held step re-run with human input,
│                                     or advance if no active dispatch
├─ /nebuko force-step step --name <step>
│                                    ► jump to <step> of the active workflow
│                                     (replay/fork within the current run)
├─ /nebuko delete comments ─────────► deterministic comment deletion
│  /nebuko clean comments            (alias; terminal, no agent runs)
├─ /nebuko run workflow --name <name> [--step <step>]
│                                    ► start/switch to workflow <name>; a fresh
│                                     run entering at <step> (default: first step)
├─ @@mention (human, or plain-mention CI trigger)
│                                    ► run the mentioned agent
├─ state + dispatch ────────────────► dispatch-based routing
│                                     (advance / loop / hold / terminate)
├─ state without dispatch ──────────► next step of the active workflow
├─ human trigger, no state ─────────► direct task routing (backward compat)
└─ bot fallback ────────────────────► skip (prevent runaway loops)
```

Precedence rationale: explicit human intent must never be absorbed by an
active workflow's `dispatch` object. If no state is found in the current body,
the router fetches the last state from the issue comment history first.
`DEBUG=true` in a body enables verbose debug logging for that run instead.

### Entering a workflow at a dedicated step (issue #789)

`/nebuko run workflow --name <wf> [--step <step>]` seeds a **fresh** run whose
state points directly at `<step>` (default: the workflow's first step), so a
completed upstream plan can be handed to a downstream step without replaying
its predecessors. It is deliberately distinct from `/nebuko force-step`, which
jumps *inside* an already-active run (keeping that run's history/position) and
therefore requires an active workflow. The `run` branch resolves the entry via
the `WorkflowEntryPoint` value object, reusing the same shape-aware step
resolution as `force-step` — so `--step` can also target a sub-workflow mount
step, and an unknown, unsafe, or not-yet-scaffolded target fails loudly instead
of silently starting at the first step.

### The `human_gate` intercept

A security boundary applies **before** the workflow state is posted: when the
*completing* step is defined with `human_gate: true`, the engine (in
`build_output.py`) overrides the agent's `advance` dispatch to `hold`
(reason `human_gate:<step>`), so every subsequent trigger sees a held
workflow and the chain stops. The override is skipped when the completing run
was itself triggered by `/nebuko resume` — the human's resume is the
authorization that releases the gate. `terminate` is never intercepted, and
`loop` retries are not intercepted (the gate fires on step transitions). See
[workflows.md — Human Gates](./workflows.md#human-gates).

### Human-action notification (issue #795)

Notification is an orchestration concern, not an agent one: a run reports its
result and never pings a human. The engine — the only component that
deterministically knows a workflow stopped — posts a single
`HumanActionNotice` from a choke point in `run_loop.run()` after the agent's
output is published. `HumanActionNotice.classify()` maps the run outcome to
one of three kinds (`awaiting_approval` for a `human_gate` hold,
`awaiting_input` for any other hold, `workflow_complete` for a terminal step)
and `render()` emits the comment, tagged with the byte-stable
`<!-- bot-human-action -->` marker, mentioning the workflow starter.

The recipient resolves as `WorkflowState.started_by` → issue author → trigger
author. `started_by` is captured from the first trigger of a run and carried
forward verbatim, so a maintainer resuming someone else's workflow still pings
the person who started it. Engine-authored bodies are exempt from the publish
path's human-ping neutralizer; agent-authored bodies are not, so agent prose
can mention a login without notifying it. A failed notice is logged, never
fatal to the run, exactly like `notify_agent_started`.

### Deterministic `script` steps (issue #541)

A step can also be a **script**: instead of running an agent, the engine runs
a repo-local script deterministically and posts its raw output as a comment.

```
resolved step declares a `script:` source (nested `script: {path, args, timeout_minutes}`)
│
├─ resolve_config emits RUN_SCRIPT=true + SKIP_AGENT=true + SCRIPT_PATH /
│  SCRIPT_ARGS / SCRIPT_TIMEOUT_MINUTES instead of a task assignment,
│  with ROLE_NAME="script"
├─ the legacy GHA "Run Script Step" (removed with the legacy flow)
│  (bounded by GHA timeout-minutes and an executor-level timeout); no agent run
├─ executor writes the standard artifacts: final_message.txt (+ 10k split parts
│  on overflow) and workflow_state.json with history advanced and a synthetic
│  dispatch {status: advance, branch: pass|fail}
└─ the existing posting pipeline runs untouched: prepare_comment → bot marker
   (tag <!-- bot-agent-script -->) → peter-evans post → continuation trigger
```

Result semantics: exit 0 → synthetic `branch: "pass"`; non-zero exit or
timeout → `branch: "fail"`. Routing uses the step's `conditions[]` on that
branch if declared, otherwise the declared `next`. A failing script still
**advances** in v1 (does not hold or terminate) — the successor, usually an
agent, receives the raw failure output. Raw script output is wrapped in a
fenced code block in the posted comment so trigger-like strings in the output
(`@@agent`, `/nebuko`) stay inert. Scripts must live in the repo
(repo-relative path, no leading `/` or `..` segment); they run in the same
CI job with the job's full environment and permissions — same trust level as
the workflow YAML itself. Secret scrubbing / sandboxed execution are deferred
enhancements.

---

## Sub-Workflow Composition

A step can declare `workflow:` instead of `agent:`:

```yaml
steps:
  - name: sub_impl
    workflow: tmp_impl/workflow.yaml   # mount a sub-workflow as one step
    next: sub_clean
```

The mounted workflow runs to completion before the parent continues. The
engine tracks the **position** — the run's full nested call-stack of
`(workflow, step)` frames, outermost first, persisted as
`WorkflowState.position` and mirrored on every history entry. Frames are
added on entry, have their leaf replaced on an intra-workflow move, and are
popped on return, so a mount three levels deep
(`plan-orchestration/sub_impl → tmp_impl/step_1 → dev-review-loop/implement`)
keeps its enclosing frames while the innermost step changes. A terminal
sub-workflow returns to its **immediate** parent step, and the chain unwinds
one level per return. The `parent_workflow` / `parent_workflow_step` pair is
a derived view of the second-to-last frame.

The position is engine-owned state, **carried forward** from the previous run
rather than rebuilt from the flat parent pair — the latter is only the
fallback when no prior position exists. A branch-mounted sub-workflow whose
filename differs from its declared `name` (`tmp_impl/workflow.yaml` declaring
`tmp-impl`) is resolved by the mount reference its enclosing step declares,
so the unwind can re-enter it. See
[ADR-0006](./adr/0006-nested-workflow-position-carry-forward.md).

The sub-workflow path may be **runtime-generated**: the `plan-orchestration`
workflow has its `scaffold` step generate `tmp_impl/workflow.yaml` from the
approved plan, which `sub_impl` then mounts (see
[workflows.md — plan-orchestration](./workflows.md#plan-orchestration)).
Local `workflows/` files take precedence over zero-copy fetched ones; see
[features.md — Remote Workflows](./features.md#remote-workflows-zero-copy).
