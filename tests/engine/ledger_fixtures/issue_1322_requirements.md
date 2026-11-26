| ID | Requirement / Constraint / Question | Source | Origin |
|---|---|---|---|
| R1 | Provider errors (connection/timeout; 5xx or 429 after SDK retries; 401/403/404) stop the run — no finalize attempts, recalls, empty-report check, or routing; workflow does not advance. | Issue "Expected" | olivier-norture |
| R2 | A single failure comment pings the run starter and states agent phase, provider, model, and the provider error; secrets are kept out. | Issue "Expected" | olivier-norture |
| R3 | `engine_failure` records the provider error and the agent phase, matching the failure comment. | Issue "Expected" | olivier-norture |
| R4 | The Actions run ends failed; nothing logs `Task completed successfully`. | Issue "Expected" | olivier-norture |
| R5 | `/nebuko resume` re-runs the same step once the provider works; consumes no retry budget and is not counted as an agent failure. | Issue "Expected" | olivier-norture |
| R6 | Request-specific errors (context too long, content rejected) keep existing handling. | Issue "Which errors count" | olivier-norture |
| R7 | `classify_failure` in `run_failure.py` recognizes provider errors, including a 404 wrapped as `Conversation run failed…` (currently classed `UNKNOWN`). | Issue "Which errors count" | olivier-norture |
| R8 | Applies whether the failure is on the first call or mid-run. | Issue "Expected" | olivier-norture |
| R9 | Exit codes compared with `is`, not `==` (`ExitCode` is an enum; `==` can't fail). | Issue "Tests" / #1310 | olivier-norture |
| NG1 | No duplicate or contradictory failure notices: the provider-error comment is the one failure report; later steps must not overwrite the cause. | Issue "What happened" | olivier-norture (derived) |
| K1 | Tests: first-call stop before `validate` with provider error named; mid-run stop; state matches comment; no workflow advance + resumable; request-specific errors unchanged; wrapped 404 classified as provider error. | Issue "Tests" | olivier-norture |
| Q1 | Mid-run crash with files already changed: commit+push that one step (author's lean) or discard? Comment must say what was pushed. | Issue "Decide during planning" | olivier-norture — open, decide in planning |
