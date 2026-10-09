# Posting Contract — What Gets Posted to the Issue

Applies to **every agent**. The engine owns the answer channel: the control
plane (`finish` / `dispatch_next`) is a *typed schema*, not a prose convention.
When your work loop ends, the engine opens one **report turn** and asks you for
the deliverable in the `finish` tool's `message` argument — that field is what
gets posted on the GitHub issue. Where you happened to write your prose during
the work loop no longer decides the posted answer (issue #830; the #822
truncation bug was a full report left in `finish.message` while the extractor
published a one-line lead-in).

## How the engine reads your answer

1. **The report turn's `finish.message` is the deliverable.**
   After the run ends (any exit reason) the engine sends a report request. Reply
   by calling `finish` with `message` set to the complete, self-contained
   deliverable — the report, analysis, change summary, or review findings.
   Structure it for humans reading the issue thread (headings, lists, exact
   snippets where useful). The engine harvests this typed field structurally and
   posts it verbatim; it supersedes everything else on the run.

2. **The work-loop `finish` message is an optional short note.**
   Calling `finish` during (or at the end of) the work loop is still how you
   signal completion to the SDK, but its `message` is **not** the deliverable and
   is not posted. Keep it to a one-line status note.

3. **`dispatch_next` fields are routing metadata.**
   `status` / `branch` / `complexity` / `reason` carry the next-step decision.
   Never put the deliverable in `reason` — it is one sentence of routing
   justification. The engine reads these fields directly.

4. **A standalone text answer is the fallback deliverable.**
   If the report turn cannot run (LLM error, no control call returned), the
   engine falls back, in this fixed order: a standalone agent text answer → the
   work-loop `finish.message` → the turn text. This path is deterministic and
   visibly marked in the run's answer snapshot — it is not a license to skip the
   report turn.

5. **Awaiting-human endings require an explicit hold.**
   Proposals, revised proposals, and clarification requests end with
   `dispatch_next(status='hold')`. Never end a run without a dispatch decision —
   doing so triggers an engine recall pass whose short status note can clobber a
   fallback answer (issue #558).

6. **The hand-off sentinel goes last.**
   End the posted deliverable with the single-line `AGENT_RUN_CONFIG` sentinel
   (per `call-agent` / your agent rules) when a hand-off is required. The ETL
   layer re-appends it to the posted body exactly once.

7. **Size is handled for you.**
   Long answers are automatically split into multiple comments (10k chars per
   part, 60k cap). Write at the length the content needs; do not artificially
   compress just to fit.

## Anti-patterns

- Treating the work-loop `finish` message as the place for the report — it is
  ignored; the report turn is the answer channel.
- Squeezing the deliverable into the `dispatch_next` `reason` field — the
  arguments are routing metadata.
- Ending the session with chatter ("I'll now finish…") after the real answer —
  on the fallback path that chatter becomes the posted comment.
- Ending a proposal or clarification turn without calling `dispatch_next`
  (engine recall fires and its short status note can clobber a fallback answer).