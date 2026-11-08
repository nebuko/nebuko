# Claims verification (issue #1250)

@@architect attaches the external facts its plan depends on to the
@@context hand-off via `AgentCall.verify`.

## How it works

1. While planning, @@architect writes each dependency as a falsifiable
   `VerificationClaim` (id, claim, kind, category, if_wrong).
2. The engine validates the set: only a hand-off to `context` may carry
   `verify`, the count is capped by complexity (low 3, medium 5, high 8),
   at least one third of the claims must be `kind="refute"`, duplicate IDs
   are rejected and every `if_wrong` must be at least 10 characters.
3. `ClaimTaskRenderer` renders the claims as a fixed `### CLAIM VERIFICATION`
   block prepended to the callee's task, so @@context fills in a verdict
   (confirmed / refuted / contested / inconclusive) per claim with evidence.

## Metrics

The engine counts claims per run and the verdict distribution; a `refuted`
verdict that changes the plan is the signal that the cost paid off.
