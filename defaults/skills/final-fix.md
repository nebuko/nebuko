# Final-Fix Mode

You are in final-fix mode for a branch-mounted dynamic plan workflow (issue #462). The final PR review was rejected. Your job is to address the reviewer's feedback across the whole PR. Follow these instructions strictly.

## What You MUST Do

1. **Read the final review comment** on this issue — it contains specific feedback about issues across the combined PR (all plan steps).
2. **Fix ALL identified issues.** For each:
   - Locate the affected files (the review cites file paths).
   - Apply a minimal, focused fix consistent with the project's coding standards.
   - Verify with the appropriate test suite where one exists.
3. **Commit** with message: `fix: address final review feedback`
4. **Push** to the existing PR branch (use the existing PR detection logic from your git workflow rules).
5. **Dispatch**: call the `dispatch_next` tool with `status="advance"` (do not write an `AGENT_RUN_CONFIG` sentinel yourself — the engine renders it from typed state, issue #876).

## What You MUST NOT Do

- Refactor beyond the scope of the review feedback.
- Re-open or re-add `tmp_impl/` scaffolding — it was intentionally removed after plan execution.
- Merge or close the PR. Wait for the next final review round.

## Escalation Protocol

If a piece of feedback conflicts with the architect plan or the issue requirements → note the conflict in your summary and implement the interpretation most consistent with the original plan; flag it for the reviewer in your message.
If a fix would require an architectural change → stop and report to @@architect instead of guessing.
