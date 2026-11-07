---
title: "Human commands"
audience: user
sidebar_group: "Concepts"
sidebar_label: "Human commands"
sidebar_order: 21
---

# Human commands

A human steers the workflow by writing commands in an issue body or comment.
Commands are parsed by the `trigger_commands` sub-domain
(`engine/lib/resolve_config/sub_domains/trigger_commands/trigger_commands.py`)
into a frozen `TriggerCommands` dataclass: `is_bot`, `mention_agent`,
`workflow_cmd`, `force_next`, `resume`, `debug`.

| Command | Effect |
|---------|--------|
| `@@<agent>` | Run the named agent directly (`architect`, `context`, `dev`, `review`, ...). To start a workflow, use `/nebuko run workflow --name <name>` — which enters at the workflow's first step. |
| `/nebuko run workflow --name <name> [--step <step>[.<step>...]]` | Start (or switch to) the named workflow, regardless of current state. `--step` seeds a **fresh** run directly at `<step>` instead of the first step (issue #789) — useful to enter `plan-orchestration` at `plan`/`scaffold` without replaying `architect`. A **dotted** selector (`sub_impl.step_3`, `sub_impl.step_3.review`) descends into mounted sub-workflows to address a non-first nested step (issue #822). An unknown, malformed, or not-yet-scaffolded target fails loudly. Human-only. |
| `/nebuko resume [--feedback <text>]` | Resume the held workflow step, or advance if there is no active dispatch. Bare, it resumes the held step when no feedback is required; with `--feedback`, the text is delivered as the human's input to the recalled step. `--feedback` is a multiword flag — positional feedback is not parsed. On a `human_gate:*` hold it is **inert** (points at the gate commands) — use `/nebuko approve gate` / `/nebuko reject gate` there. |
| `/nebuko approve gate [--feedback "..."]` | Release a `human_gate` hold: record an engine-owned **`GateDecision`** (`approved`) and route via the gated step's `on_result: approved` successor. Handled inline by the engine, never routed to an agent. Authorized humans only (`OWNER`/`MEMBER`/`COLLABORATOR`), always-on regardless of `CALLER_AUTH` (issue #648). |
| `/nebuko reject gate [--feedback "..."]` | Release a `human_gate` hold as **rejected**: with `--feedback` the gated step is **re-run** with the feedback injected (gate re-arms on completion); without feedback it routes via `on_result: rejected`. Same identity rules as `approve gate`. |
| `/nebuko force-step step --name <step>[.<step>...]` | Jump directly to `<step>` of the **active** workflow, regardless of state. A dotted selector descends mounted sub-workflows (issue #822), exactly as for `run --step`. Unlike `run --step`, this replays/forks within the current run (it requires an active workflow). |
| `/nebuko delete comments [--issue <id>] [--from <comment-id>]` | Human-only batch deletion of issue comments. `--issue <id>` selects the target issue (mandatory on the CLI surface; auto-filled with the invoking issue when the command is posted in that issue). Optional `--from <comment-id>` deletes that comment **and every later one**; omit it to delete **all** comments. Terminal command: it runs the deterministic deletion step and no agent. Requires a GitHub token with `issues:write` (the App installation token by default; `GH_PAT` in PAT fallback mode). |
| `/nebuko clean comments [--issue <id>] [--from <comment-id>]` | Alias of `/nebuko delete comments` (issue #679), accepting the same optional `--issue`/`--from` flags. Terminal; runs the deterministic deletion step and no agent. |
| `/nebuko update circuit-breaker --value <n>` | Human-only, per-issue circuit-breaker override (issue #839). Persists `limit = <n>` in the issue's `workflow_state`; the guard then uses `state override → CIRCUIT_BREAKER_THRESHOLD env → 25`. Raising the value above the current agent-call count **un-trips** the issue; lowering it tightens a runaway issue. Terminal, agent-free: it consumes no call budget. Bots are ignored. |
| `DEBUG=true` | Enables verbose debug logging for the run. |

**Human-actionable stops stay green and explain themselves (issue #1057).** A
run that stops for a human — an armed `human_gate` hold, a malformed/unknown
command, a missing workflow or an invalid `force-step` name — posts the reason
(and the next command to run) as a `@author` notice on the thread and exits the
host process **0**. Only `ExitCode.ERROR` (an uncaught engine failure) fails the
CI job; `RunOutcome.exit_code` still carries the run-outcome code
(`HOLD` 10 / `TERMINAL` 20) for machine callers. See
[ADR-0015](../../../../../../../docs/adr/0015-human-actionable-stops-do-not-fail-ci.md).

**Multiline `--feedback`:** the command line is the grammar line, but a terminal
`--feedback` flag spans the remainder of the comment body verbatim, newlines
preserved (issue #919). So a bare `--feedback` with the payload on following
lines is valid:

```
/nebuko approve gate --feedback
1. Flat
2. Remove this section
3. cache_hit_token is fine
```

A `--feedback` with nothing after it (only blank lines) is still rejected as a
bare flag value.

**Precedence:** human commands always override state-based routing (see the
cascade in [architecture.md — Routing Cascade](../../../../../../../docs/architecture.md#routing-cascade)):
`approve/reject gate` > `resume` > `force-step` > `delete comments` >
`update circuit-breaker` > `run workflow` > `@@mention` > dispatch-based
routing > next-step > bot fallback. This guarantee exists so explicit human
intent is never absorbed by an active workflow's `dispatch` object.
`delete comments` and `update circuit-breaker` are **terminal** human commands:
they run a deterministic step and never invoke an
agent.

Note: **bot agents cannot use `@@mention`** (only humans may); bots hand off via
the `AGENT_RUN_CONFIG` sentinel ([agent-run-config.md](../../../../../../../docs/agent-run-config.md)).
The loop guard additionally skips a run when a human is pinged *alongside* an
agent, so the human gets to orchestrate.

An invalid invocation — an unknown flag (e.g. `/nebuko run workflow --default`),
missing resource, or a leading `/` that matches no command — replies on the
thread with a usage hint instead of failing silently in the CI log. The reply
costs no LLM call and never re-triggers the engine (decision recorded in
[ADR-0004](../../../../../../../docs/adr/0004-propagate-human-command-errors-to-thread.md), issue #804).

A malformed `/nebuko` invocation is a **command attempt**: the grammar alone
decides it, so it is answered with its usage hint *before* the routing cascade
and can never be absorbed by an active workflow `dispatch` (issue #867,
[ADR-0011](../../../../../../../docs/adr/0011-command-attempts-resolved-by-grammar.md)). This is what
makes a mistyped command such as `/nebuko clean comments --from <id>` safe: it
either executes (with `--from`, the scoped deletion) or gets an answer — it
never boots an agent. The `--from <comment-id>` scope deletes that comment and
every later one on the target issue; omit it to delete all comments.
