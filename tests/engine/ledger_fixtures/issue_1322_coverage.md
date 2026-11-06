| ID | Covered by | Verified by |
|---|---|---|
| R1 stop before validate/routing; no workflow advance | Decision §3, step 4 | `tests/engine/test_provider_failure.py::test_first_call_provider_failure_stops_before_validate` (no `[phase:validate]` log, no continue token) |
| R2 single comment: starter ping, agent phase, provider, model, error; secrets out | Decision §3/§5, steps 4–5 | `test_failure_comment_names_provider_error_and_phase` (+ api-key redaction assertion) |
| R3 `engine_failure` names the same error and phase as the comment | Decision §3, step 4 | `test_engine_failure_state_matches_comment` (asserts `error`/`phase`/`provider` equality) |
| R4 run ends failed; no `Task completed successfully` | Decision §2/§3, steps 2, 4 | `test_run_exits_failed_and_never_logs_success` (`exit_code is ExitCode.ERROR`) |
| R5 resume re-runs the step; no retry budget; not an agent failure | Decision §3, step 4 | `test_resume_reruns_step_without_retry_budget` (extends the `TestResumeAfterRealFailure` pattern; asserts `soft_retry` untouched) |
| R6 mid-run failure stops the same way | Decision §2, steps 2–3 | `test_midrun_provider_failure_stops` |
| R7 error matrix: connectivity/5xx/429/401/403/404 stop; request-specific 400s don't | Decision §1, step 1 | `test_run_failure.py::TestClassifyFailure::test_provider_error_matrix` |
| R8 wrapped 404 classified as provider, not UNKNOWN | Decision §1, step 1 | `test_wrapped_404_classified_as_config_error` (reproduces the `ConversationRunError` wrapper from run 42049492853) |
| R9 mid-run changes committed/pushed once; comment says what | Decision §3, step 4 | `test_midrun_changes_pushed_and_reported` |
| R10 exit codes compared with `is` (#1310) | Decision §3, step 4 | review check: no `==`/`!=` on `ExitCode` in new/changed tests |
