---
description: "Review modes for the dynamic plan workflows (dev-review-loop, plan-orchestration)."
---

# Plan-Workflow Review Skill

Review modes that apply only inside the dynamic plan workflows (`dev-review-loop`
and `plan-orchestration`). Check the `workflow` / `step` fields of the
`workflow_state` section first; when neither matches, follow the standard review
flow in your role prompt unchanged (including its `appreciation` verdict dispatch).

## Per-Step Review (dev-review-loop)

When in the `review` step of the `dev-review-loop` sub-workflow:

* **Identify the current step** from `workflow_state` — use the parent step name (e.g., `step_2_rate`) or the skill reference of the step just executed to determine which plan step was implemented.
* **Scope of the diff:** review **ALL commits on the branch matching the `[step N/M]` prefix for the current step** — this includes the original implementation commit plus any fix commits from prior rejected rounds. Do NOT review other steps' commits; each step is reviewed independently.
* Evaluate with the same criteria as your standard review (correctness, edge cases, regressions, code quality, security, testing), but judged against **this step's scope and expected outcome** (as documented in its `tmp_impl/skills/step_N.md` content and instructions).
* **On approval:** call `dispatch_next(appreciation="approved")`.
* **On rejection:** describe the specific issues (file paths, what is wrong, how to fix), then call `dispatch_next(appreciation="rejected")`.
  The loop re-runs the `implement` step with the same skill file; your feedback becomes the fix context.

Do not write an `AGENT_RUN_CONFIG` sentinel yourself — the engine renders it from the typed `dispatch_next` call (issue #876).

## Final PR Review (plan-orchestration)

When in the `final_review` step of the `plan-orchestration` workflow:

* Review the **entire PR diff** (all plan steps combined), not just the last commit.
* Verify ALL of the following:
  - Every step of the architect/plan (`<planWorkflow>` block in the issue) is fully implemented.
  - The `tmp_impl/` directory has been removed (no scaffolding files in the diff/branch).
  - Code quality is consistent across steps (naming, error handling, patterns — steps were implemented by separate runs and must read as one coherent change).
  - Nothing required by the original architecture plan is missing.
* **On approval:**
  - Summarize what was verified across all steps.
  - Do **NOT** ping any human — the engine posts the human notification itself when the workflow stops for a human (issue #795).
  - Call `dispatch_next(appreciation="approved")`.
* **On rejection:** describe the specific issues found across the PR (with file paths), then call `dispatch_next(appreciation="rejected")`.
  The `final_fix` step will address your feedback and the review repeats.

Do not write an `AGENT_RUN_CONFIG` sentinel yourself — the engine renders it from the typed `dispatch_next` call (issue #876).
