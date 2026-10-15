---
description: "Plan-cleanup mode: remove the plan workflow's temporary scaffolding before final review."
---

# Plan-Cleanup Mode

You are in plan-cleanup mode for a branch-mounted dynamic plan workflow (issue #462). All plan steps have been executed and reviewed. Your job is to remove the temporary scaffolding before final review. Follow these instructions strictly.

## What You MUST Do

1. **Remove the entire `tmp_impl/` directory** from the repository root:
   ```
   rm -rf tmp_impl/
   ```
2. **Verify** the directory is gone (`ls tmp_impl/` should fail) and that nothing else was accidentally removed.
3. **Commit** with message: `chore: remove plan scaffolding`
4. **Push** to the existing PR branch (use the existing PR detection logic from your git workflow rules).
5. **Dispatch**: call the `dispatch_next` tool with `status="advance"` (do not write an `AGENT_RUN_CONFIG` sentinel yourself — the engine renders it from typed state, issue #876).

## What You MUST NOT Do

- Remove anything other than `tmp_impl/`.
- Modify, revert, or "clean up" implementation code committed by the plan steps — that is reviewed separately.
- Create new files or directories.

## Escalation Protocol

If `tmp_impl/` does not exist (already cleaned) → confirm the working tree is clean, and if there is nothing to commit, simply push (if needed) and dispatch `status: "advance"`.
If removing the directory would delete uncommitted work outside the scaffolding scope → stop and report the blocker clearly.
