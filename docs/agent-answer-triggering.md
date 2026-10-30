# Agent answer triggering

How a comment the nebuko bot itself publishes — an agent's answer — can
re-enter the engine and advance the workflow, while every other bot comment
stays inert.

## How it works

The bot prefixes every comment it posts with the universal terminal marker
(``<!-- bot-workflow-terminal -->``). Agent answers additionally carry an
agent-answer marker (``<!-- bot-agent-<role> -->``). Historically the engine's
routing guard (`run_loop._is_bot_trigger`) dropped *any* bot-classified
comment, so the bot's own agent answer could never trigger the next step.

The fix is a narrow routing exemption in `_is_bot_trigger` only:

| Body carries | Example | Routing |
|---|---|---|
| Universal terminal marker only | `<!-- bot-workflow-terminal -->` | Inert (terminal notice) |
| Agent-answer marker, nothing inert | `<!-- bot-agent-dev -->` | **Routable** (answer part 1) |
| Agent-answer + continuation marker | `<!-- bot-agent-dev -->` + `<!-- bot-answer-continuation -->` | Inert (answer tail) |
| Agent-answer + `AGENT_RUN_CONFIG` | — | Inert (stale sentinel echo) |
| Continuation marker only | `<!-- bot-answer-continuation -->` | Inert |

The predicate lives in
`src/nebuko/engine/lib/resolve_config/features/parse_trigger/parse_trigger.py`
as `is_routable_agent_answer()`, and `run_loop._is_bot_trigger` composes it:

```
bot trigger  =  is_bot_comment(body)  and  not is_routable_agent_answer(body)
```

Only the routing choke point honors the exemption. `is_bot_comment()` itself
and all gate-decision consumers (`resolve_config.py`) are unchanged, so a bot
comment still can never carry a gate decision — the gates stay bot-blind.

## How to use

For a bot-published agent answer to advance the workflow:

1. Part 1 of the answer must carry the agent-answer marker
   (`<!-- bot-agent-<role> -->`).
2. Part 1 must not contain the `<!-- bot-answer-continuation -->` token or the
   literal `AGENT_RUN_CONFIG`.
3. The CI ingestion workflow (issue-comment GHA pre-filter) must admit
   bot-authored comments; it was aligned with this change.

If any of these fail, the comment is treated as an ordinary bot notice and the
engine does not run.

## Loop-safety invariants

- Gates remain bot-blind: `is_bot_comment` / `is_machine_comment` semantics
  are unchanged, so no bot comment can ever carry a gate decision.
- Terminal notices and continuation parts can never route.
- Not protected: a human echoing the `bot-agent-` marker can trigger a step —
  the same power as any `/nebuko run` command. Gate decisions are still
  rejected for such comments.

## Troubleshooting

Symptom: *"agent comment published by nebuko bot doesn't trigger next step."*

Check each layer in order:

1. **CI filter** — does the issue-comment workflow's pre-filter drop
   bot-authored comments before the engine runs? It must admit bodies
   carrying the agent-answer marker.
2. **`_is_bot_trigger`** — confirm the body contains
   `<!-- bot-agent-` and neither `<!-- bot-answer-continuation -->` nor
   `AGENT_RUN_CONFIG`; then the guard must return False (not a bot trigger).
3. **Routing log** — look for the `[phase:guards]` bot-marker check lines; a
   warning there means detection crashed and the fallback classified the
   comment as "not a bot" (posting-safe default).
