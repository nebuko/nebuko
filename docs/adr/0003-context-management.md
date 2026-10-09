# ADR-0003: Context Management — Recency Window + Pinned Comments (No Summarizer)

**Status:** Accepted — 2026-10-07
**Decider:** olivier-norture
**Related:** #796 (this change), #731, #756 (future context building)

## Context

Agents invoked by the orchestrator need enough conversation history to act
correctly without an unbounded prompt. Historically the pipeline included a
dedicated `summarize` agent: a first-class LLM agent that produced a structured
narrative of the issue (decisions, files touched, pending actions) which was
then injected into later agents' prompts to keep long-running threads
navigable.

Two problems made that design unnecessary:

1. **Lossy and expensive.** Producing the summary cost an extra LLM call per
   refresh, and the compressed narrative could drop details that a later agent
   needed verbatim.
2. **Redundant with a recency window.** The current design injects the most
   recent mentionable thread comments directly, plus any explicitly pinned
   comment, which already covers the "what just happened" and "what must always
   be visible" needs.

## Decision

**No LLM summarization layer in the context pipeline.** Context is assembled
by the engine from three sources:

- **Recency window** — the last N thread comments are injected verbatim into
  the agent query context. N is controlled by `history_comment_count`
  (default **5**).
- **Pinned comments** — a comment explicitly pinned via `pin_comment` /
  `dispatch.pin` is always injected verbatim, regardless of its position in the
  thread. Pinning carries long-lived salient facts (decisions, constraints)
  that must not fall out of the recency window.
- **Step inputs** (amendment, issue #813) — a step's declared `inputs` are
  materialized by the engine immediately before prompt assembly and injected as
  labeled blocks (see the amendment below). This is the one source the *model*
  cannot influence: unlike a skill it is not optional, and unlike a pin it does
  not depend on an agent having asked for it.

The `summarize` agent, its prompt, its config stanzas, its roster entry, its
`@summarize` trigger parsing, its routing, and its GitHub trigger conditions
have been removed.

## Consequences

- **Bounded prompt size** is governed by comment count and pinning, not by a
  compression step.
- **Lower latency and cost** — no extra LLM call is spent building context.
- **No lossy-compression risk** — injected comments are verbatim.
- **Salient long-lived context is carried by pins**, not by a running summary;
  authors must pin what later agents must always see.
- Context building is expected to improve further via **#731** and **#756**;
  those efforts supersede this mechanism rather than resurrect a summarizer.

## Amendment — step inputs as a third source (issue #813)

ADR-0009 makes a workflow a typed function whose steps declare `inputs`. An
`exec` input is a **deterministic provider**: the engine, not the model, runs
the bound command at the consuming step's prompt-assembly seam and injects the
captured stdout as a labeled context block. This adds a **third, mandatory**
context source beside the recency window and pinned comments:

- It is **engine-guaranteed** — the model neither discovers, materializes, nor
  chooses it, and a `required` provider that fails terminates the step without
  invoking the agent.
- It is **just-in-time** — materialization happens immediately before the
  prompt is built, after the durable history is loaded, so the injected
  artifact is as fresh as the model call.
- It is **bounded by construction** — only declared inputs are injected, so it
  cannot grow unbounded the way an accumulating scratchpad could.

Both original consequences still hold: no summarizer, and no lossy compression.

