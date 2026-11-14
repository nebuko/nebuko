"""Wind-down prompt — injected when the agent run trips its soft time budget.

Instead of a hard kill at the GitHub Actions timeout, the in-flight LLM call
is interrupted and this prompt asks the agent to gracefully conclude:
summarize progress, document what remains, and checkpoint the work in a PR
(issue #489).
"""

WIND_DOWN_PROMPT = """\
⏱️ SOFT TIMEOUT REACHED — GRACEFUL WIND-DOWN REQUIRED

You have hit your allocated execution time budget. Stop doing new work
immediately and complete the following wind-down steps IN ORDER. Be concise;
you are on a short time budget.

1. STOP — Do not start any new task, refactor, or exploration. Finish only
   the single edit you are currently in the middle of, if any, then stop.

2. COMMIT & CHECKPOINT — Run `git status` and `git diff`. If there are
   uncommitted changes, commit them on the current branch with a clear
   message describing the work done so far, and push the branch to origin.

3. OPEN / UPDATE THE PR — If no PR exists for the current branch, open one
   (link it to the target issue, describe the work done and what remains).
   If a PR already exists, update its description instead.

4. FINAL MESSAGE — End your execution with a finish message that contains:
   - **Work completed**: what was done in this run (concrete deliverables).
   - **Current state**: branch/PR names and URLs.
   - **Blocking point**: what exactly is stuck or incomplete right now.
   - **Remaining work**: an ordered list of next steps so the next run can
     resume without redoing anything.

Do NOT restate the full history; be factual and actionable.
"""