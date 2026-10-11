# ADR-0004: Propagate Human Command Errors to the Thread

**Status:** Accepted — 2026-10-08
**Decider:** olivier-norture
**Related:** #804 (this change), #793 (soft-kill halt notice), #529 (enriched skip reason), #635 (command grammar), #679 (`clean` alias) — **extended by** [ADR-0011](./0011-command-attempts-resolved-by-grammar.md) (#867), which resolves a command attempt *before* the cascade

## Context

A human steers the workflow by posting `/nebuko …` commands. When the
invocation violates the grammar — an unknown flag (the issue's own example,
`/nebuko run workflow --default`), a missing resource, a missing required flag
— `parse_workflow_command` deliberately returns `None` so the router falls
through the normal cascade. With no parse result, the resolver produces no
task, the guards phase sets `SKIP_AGENT=true`, and the run exits
`ExitCode.TERMINAL` (20).

That terminal skip was intentionally silent: the only skip that posted a notice
was the exhausted soft-kill halt (ADR-0002 / issue #793). The consequence for a
malformed command was that the *only* feedback lived in the CI log:

```
INFO:nebuko.engine.run_loop:[phase:guards] skipping agent — No valid task found
Error: Process completed with exit code 20.
```

The human saw a red check with no explanation of what they typed wrong. Two
failure classes were affected:

1. **Malformed known command** — the first line is a `/nebuko` invocation that
   breaks the grammar.
2. **Unknown slash intent** — the first line starts with `/` but matches no
   command at all.

## Decision

1. **Typed parse surface.** `parse_command(body) -> CommandParseResult` is the
   single source of truth for whether a comment's first line is a well-formed
   `/nebuko` command and, if not, why. It carries a machine-stable `detail`
   token (e.g. `unknown_flag`, `unknown_command`, `missing_required_flag`) and
   a user-facing `usage` hint. `parse_workflow_command` remains a thin
   compatibility wrapper (`parse_command(body).command`), so every existing
   consumer — `classify_comment`, `build_trigger_commands`, the resolver facade
   — keeps its signature and its `None`-on-malformed contract.

2. **Terminal skips caused by a human command speak up.** The resolver
   transports the parse failure through the existing string-keyed channel
   (`config_command_error` → `REASON` + `COMMAND_ERROR=true`, alongside
   `SKIP_AGENT` and the #529 `config_skip_reason`). In the guards phase, a
   command-caused terminal skip from a **non-bot** trigger posts a neutralized
   `@author` notice via the generalized `Engine._post_terminal_notice()`
   (which `_post_soft_kill_halt` now delegates to). Routine resolver skips —
   auto-routing, mentions, scheduled triggers, bot comments — stay silent
   exactly as before.

3. **Exit-code contract unchanged.** A malformed command still exits
   `TERMINAL = 20`.

## Alternatives considered and rejected

1. **CI-level `if: failure()` comment step** in `nebuko-workflow.yaml` (the
   `continue-on-error-comment` pattern). Rejected: the notice is generic ("the
   job failed") with no knowledge of *which* command was wrong, it requires
   plumbing the event payload into yet another step, and it fires on genuine
   crashes where a raw dump is noise.
2. **String-encoding the parse failure into `REASON` at parse time.** Rejected:
   it conflates "why routing skipped" with "why parsing failed", gives no typed
   surface to test or extend, and is exactly the string/namespace
   anti-pattern the typed `CommandParseResult` avoids.

## Consequences

- **Zero LLM cost.** The feedback path is pure parsing + one adapter `post()`;
  no agent is invoked. This satisfies the issue's explicit "if no LLM is
  involved it costs nothing" requirement.
- **Loop safety.** The notice opens with `BOT_MARKER` and is neutralized via
  `_neutralize_agent_triggers`, so it classifies as a bot comment and can never
  re-enter the posting path.
- **Accepted fragility.** A non-command line that merely begins with `/` now
  receives an "unrecognized command" reply. This is the issue's acknowledged
  "fragile behaviour between a command and a trigger", tolerable precisely
  because the cost is a single deterministic comment.
- **Extensibility.** The closed `detail` token set is the stable contract that
  future command feedback (extra actions, new flags) extends, rather than
  re-parsing free text.
