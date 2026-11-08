---
covers:
  - src/nebuko/engine/lib/replay.py
  - src/nebuko/engine/lib/replay_context.py
  - src/nebuko/engine/lib/fixtures/*
sidebar_group: "Reference"
sidebar_label: "Replay engine decisions"
sidebar_order: 30
---

# Replaying engine decisions

`nebuko.engine.lib.replay` answers "what tier / agent / config would the engine
resolve for this step?" from a recorded workflow state — deterministically, with
no LLM call, no agent execution and no GitHub mutation. It replaces the
throwaway `/tmp/*.py` scripts that used to be needed to extract the
`WORKFLOW_STATE` block, hand-build `prev_state` / `turn_outcome` inputs and clear
gate state by trial and error before `resolve_config` returned anything useful.

| | |
|---|---|
| **Entry point** | `uv run python -m nebuko.engine.lib.replay …` |
| **Source** | `src/nebuko/engine/lib/replay.py` |
| **Tests** | `src/nebuko/engine/lib/test_replay.py` (co-located, not collected by a bare `pytest`) |
| **Exit codes** | `0` replayed · `1` no state / empty history · `2` usage or I/O failure |
| **Network** | only for `--issue`; `--state` runs fully offline |

---

## Usage

```bash
# Replay one invocation of an issue's run (fetches the issue + comments)
uv run python -m nebuko.engine.lib.replay --issue 1299 --entry 14

# Replay every invocation from a saved state file, offline
uv run python -m nebuko.engine.lib.replay --state state.json --all-entries

# Machine-readable rows (no header block)
uv run python -m nebuko.engine.lib.replay --state state.json --json

# Replay against a different policy/workflow checkout
uv run python -m nebuko.engine.lib.replay --state state.json \
    --workflows-dir defaults/workflows --local-workflows-dir .nebuko/workflows
```

`--all-entries` is the default when `--entry` is absent. `--issue` and
`--state` are mutually exclusive; `--repo` (default `GITHUB_REPOSITORY`) names
the repository for `--issue`.

`--state` accepts three shapes: a full issue/comment body carrying the
`WORKFLOW_STATE` markers, a bare state JSON document, or a wrapper object with a
`state` key.

## Output

A provenance header, then one row per invocation:

```
Replay: state file state.json
  state: run_id=8 workflow=spec-tdd step=spec history=8 entries
  policy: builtin only (…/agent-config.yaml); auto_adjust=True max_auto_delta=1
  env: AGENT_CONFIG_PATH=… WORKFLOWS_DIR=… LOCAL_WORKFLOWS_DIR=… …

entry  step     agent      base    recΔ  replayΔ  CONFIG_COMPLEXITY  next               trigger / rationale
-----  -------  ---------  ------  ----  -------  -----------------  -----------------  --------------------------------
0      analyze  architect  N/A     -     -        N/A                context (context)  - [seed invocation …]
2      plan     architect  low     +1    +1       high               spec (architect)   evidence-drift: … 
```

- `base` is the tier the workflow declared, `recΔ` the delta the live run
  recorded, `replayΔ` the delta this replay computed — a mismatch is the signal
  you are looking for.
- `CONFIG_COMPLEXITY` is the effective tier the agent would have run at; `next`
  is the resolved next step/agent.
- `trigger / rationale` names the governor trigger and its rationale, plus a
  `[resume trigger]` / `[gate trigger]` marker when the invocation was
  reconstructed as a human command.
- A `*` after the step name means the replay resolved a different step/agent
  than the run recorded.

`--json` prints the same rows as objects (`entry`, `step`, `agent`,
`base_tier`, `recorded_delta`, `replay_delta`, `trigger`, `rationale`,
`config_complexity`, `next_step`, `next_agent`, `resolved_step`,
`resolved_agent`, `skip_kind`, `trigger_kind`, `matches`, `note`).

## Input context and recorded output (`--context`)

`--context` adds, for each replayed invocation, the **prompt plan** the engine
would assemble (ordered sections and where each came from, see
[the prompt plan](../src/nebuko/engine/prompt_plan.md)), the **rendered prompt**,
and the **output the live run recorded** for that step.

```bash
# plan + prompt + recorded output, offline, with a recorded thread
uv run python -m nebuko.engine.lib.replay --state state.json --entry 2 \
    --context --thread thread.json

# only the section list and sizes; machine-readable
uv run python -m nebuko.engine.lib.replay --issue 1299 --entry 2 \
    --context --no-content --json
```

```
== entry 2: plan (architect) ==

Prompt plan (order = prompt order):
   1 - scaffold_conflict omitted  <- branch scaffold
   2 + role_prompt       414 chars  <- defaults/prompts/architect.md
   ...
  13 + thread            812 chars  <- typed thread (context selection)
        . agent-output 6656093 agent output — context
Recorded output: comments [6656093725] (1204 chars)
```

- `--issue` fetches the issue and comments once and uses them for both the state
  and the context. `--state` has no thread: pass `--thread FILE`
  (`{"issue": {...}, "comments": [...]}` in GitHub REST shape, or a bare comment
  list), otherwise the plan is built with an empty thread and a note says so.
- The thread is cut at the invocation: only comments posted before the entry's
  own output comments are input. Those output comments are the **recorded
  output** (state-stripped bodies); without a thread only their ids are shown.
- Nothing is executed: step `inputs:` providers are not run (the `inputs`
  section is empty and a note says so) and the session handoff is not
  reconstructed. A role/step prompt is read from the checkout, so a changed
  prompt file replays differently from the live run.

## Policy provenance

The header reports which complexity policy the replay resolved (builtin only,
or builtin + repo layer), its config path, `auto_adjust` / `max_auto_delta`, and
the env overrides that change a resolution (`AGENT_CONFIG_PATH`,
`WORKFLOWS_DIR`, `LOCAL_WORKFLOWS_DIR`, `MAX_SOFT_RETRIES`,
`COMPLEXITY_CONTEXT_SIGNALS`).

The replay always uses the **checkout's** policy, which may differ from the one
pinned when the run happened. When the state's pinned policy differs, the
header prints a warning — treat a tier disagreement in that case as
inconclusive until you replay with the original policy.

## Fidelity limits

All deterministic, and all reported in the header or per row:

- Entry 0 is the seed invocation: the live run started from a human command and
  a trigger body that are not recorded in state, so it is listed without a
  resolution.
- Later triggers are reconstructed from the previous entry — a recorded
  `GateDecision` means `/nebuko approve|reject gate`, a parked outcome only a
  human can clear (`hold`, crashed run) means `/nebuko resume`, everything else
  is the engine's bot trigger.
- A pending `function_call` hand-off and the per-step `soft_retry` counter are
  not recorded per history entry and cannot be reconstructed.
- `parent_workflow` / `parent_workflow_step` come from the recorded position
  chain rather than the (unrecorded) state fields.
- A legacy snapshot recording neither a gate lock nor a gate decision is
  replayed without a lock, matching the live writer.

## Regression harness

Feed a saved state and assert the expected tiers — the run-19 state from #1299
is checked in as `src/nebuko/engine/lib/fixtures/workflow_state_issue1299_spec_tdd.json`
and pinned by `TestRun19Regression` in `test_replay.py`:

```bash
uv run python -m nebuko.engine.lib.replay \
    --state src/nebuko/engine/lib/fixtures/workflow_state_issue1299_spec_tdd.json
uv run pytest src/nebuko/engine/lib/test_replay.py
```

When a replay and the live run disagree, either the replay is not faithful or
the live path has a bug — both are worth reporting.

## Non-goals

No LLM calls, no agent execution, no writes to tracked files, no GitHub
mutations. It replays the engine's decisions, not the agent's work.
