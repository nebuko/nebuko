# Posting Contract — What Gets Posted to the Issue

Applies to **every agent**. The orchestrator posts your **last text answer of the
session** (not the `finish` message) as the comment on the GitHub issue. This is a
uniform ETL rule for all agents: latest text answer wins; the `finish` message is
used only as a fallback when no text answer exists.

## Rules

1. **Last text answer = full deliverable.**
   Your last free-form message before calling `finish` must be the complete,
   self-contained deliverable for this run — the report, analysis, change summary,
   or review findings. Do not leave the real content in earlier messages: only the
   *last* text answer is captured. Structure it for humans reading the issue
   thread (headings, lists, exact snippets where useful).

2. **`finish` message = short status note only.**
   The `finish` tool's `message` argument must be a concise 2–3 sentence status
   update plus the required hand-off sentinel. It is used for workflow state
   (summary) and dispatch — never put the deliverable there.

3. **Hand-off sentinel goes in the `finish` message.**
   End the `finish` message with the single-line `AGENT_RUN_CONFIG` sentinel
   (per `call-agent` / your agent rules). The orchestrator extracts dispatch from
   it; the ETL layer also re-appends the sentinel to the posted body exactly once.

4. **Size is handled for you.**
   Long answers are automatically split into multiple comments (10k chars per
   part, 60k cap). Write at the length the content needs; do not artificially
   compress just to fit.

## Anti-patterns

- Putting the full deliverable in `finish` and a pointer in the last message.
- Ending the session with chatter ("I'll now finish…") after the real answer —
  that chatter becomes the posted comment.
- Re-posting the deliverable in `finish` "just in case" — it duplicates nothing
  and bloats the state snapshot.