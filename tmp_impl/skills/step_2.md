# Step 2: Resolver cascade — `delete` arm emitting the DELETE_COMMENTS env contract

> Status note: this step was already implemented and committed in a previous
> dev run as `[step 2/4] Resolver cascade: delete arm emitting DELETE_COMMENTS
> env contract`. If the branch already carries it, verify the outcome below
> and confirm the tests pass instead of redoing the work.

## Instructions

Add the `delete` arm to both resolver cascades, mirroring the `run`/`force-step` precedent. Files: (1) `.github/scripts/resolve_config/resolve_config.py` — in the facade cascade (next to the `wf_cmd.action == "run"` / `"force-step"` arms), add an `elif wf_cmd.action == "delete":` arm that: reads `issue = wf_cmd.params.get("issue")` and `from_id = wf_cmd.params.get("from")`; if `issue` is missing or non-numeric, substitute the current `args.issue_number` (the CLI `--issue-number` argument — this is the 'issue ID added on invocation' behavior: mandatory on the CLI surface, auto-filled when invoked from the issue); validates `from_id` numeric if present; emits the env contract lines exactly: `DELETE_COMMENTS=true`, `DELETE_COMMENTS_ISSUE=<int>`, `DELETE_COMMENTS_FROM=<id or empty>`, plus a short human-readable `TASK=` summary (e.g. `Delete comments on issue <n> (from <id>|all)`); does NOT set AGENT_SELECTED (no agent runs). (2) `.github/scripts/resolve_config/features/resolve_route/resolve_route.py` — `_resolve_human_path` (the human-path cascade mirroring the facade, ~lines 36–146) gets the matching `delete` arm so local/dry-run routing agrees with the facade; the Resolution it produces must carry the same contract keys (use the same emission shape the `run` arm uses). (3) Tests: extend the existing resolve_config / resolve_route test suites with cases for the delete arm — flag omitted (defaults to --issue-number), flag present (wins over current issue), invalid flag → command treated as absent, and from_id passthrough/empty. Run the full resolve_config test suite; all tests must pass.

## Expected outcome

- `resolve_config.py` facade cascade has a `delete` arm emitting exactly `DELETE_COMMENTS=true`, `DELETE_COMMENTS_ISSUE=<int>`, `DELETE_COMMENTS_FROM=<id or empty>` + `TASK=` summary, without `AGENT_SELECTED`.
- `--issue` omitted/non-numeric → defaults to `args.issue_number`; `--issue` present → wins.
- `_resolve_human_path` in `resolve_route.py` has the matching `delete` arm (dry-run/local routing agrees with the facade).
- Full resolve_config test suite passes, including the new delete-arm cases.

## Relevant files

- `.github/scripts/resolve_config/resolve_config.py` — CLI facade; cascade dispatch on `workflow_command.action` (`run` → ~lines 179–223, `force-step` → ~224–250); `args.issue_number` is the auto-fill source.
- `.github/scripts/resolve_config/features/resolve_route/resolve_route.py` — `_resolve_human_path()` (~lines 36–146) mirrors the facade cascade.
- Existing resolve_config / resolve_route test suites (extend, do not rewrite).

## Verification

Run the full resolve_config test suite; all tests must pass (including the new delete-arm cases: default issue, explicit issue wins, invalid flag → command absent, `from_id` passthrough/empty).