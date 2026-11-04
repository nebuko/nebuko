# Engine failure handling

What happens when a workflow step's *engine* fails — the agent answered, but
the run crashed before the answer's commits and state were safely stored
(issue #1288, seen on #1286 via the `persist()` crash in #1287).

## What happens when a run fails

The engine publishes the answer comment **last**: after the answer is
validated, the step result is persisted and the git session is finalized
(committed and pushed). If persist or the git finalize raises, the run ends
with `[phase:error]` and **nothing is published** — the answer comment never
carries the `<!-- workflow-continue -->` token, so the workflow gate cannot
fire and no next step starts. A stopped workflow means nothing was published.

A residual failure *after* publication (for example a crash during the
publish call itself) is handled by a backstop that neutralizes the posted
continue token, so a failed run still cannot advance the workflow. A crash
*during* publish — after validation passed, with commits already pushed —
keeps the #766 contract: the run routes on the agent's own dispatch.

## The failure notice

When a run fails after the agent phase, the engine posts a comment marked
`<!-- bot-engine-failure -->` containing:

- the workflow and step that failed;
- the phase the run was in when it failed;
- the exception message;
- a link to the CI run URL;
- whether the branch was pushed (`branch pushed: yes/no`);
- a ping of the workflow starter, like the human-action notice.

## How to resume

Fix the underlying failure, then re-run the step with the existing `/nebuko`
commands (e.g. `/nebuko resume`). The failure notice includes the resume
hint. No manual state editing is needed.

## Engine facts for critique/review

The engine stamps an `engine_failure` entry into the step's
`current_context`, so the next agent reads engine-side facts instead of
guessing: whether the previous step's run succeeded, whether the branch was
pushed, the branch HEAD SHA vs base, and the PR URL.

Rule for critique and review steps (enforced in the workflow prompts, e.g.
`.nebuko/workflows/spec-tdd.yaml`): check the `engine_failure` facts **before**
judging a missing diff. A missing diff plus an engine failure is reported as
an *engine failure* — never as agent fabrication, and never routed to
`plan` (the architect cannot fix a push failure).
