# Step 4: GHA wiring in openhands-multi-agent.yml + docs

## Instructions

Wire the deletion step into the composite workflow and document the command. Files: (1) `.github/workflows/openhands-multi-agent.yml` — near the existing resolver-output parsing (~lines 319–348 where RUN_SCRIPT/SCRIPT_ARGS are extracted into $GITHUB_ENV), add extraction for `DELETE_COMMENTS`, `DELETE_COMMENTS_ISSUE`, `DELETE_COMMENTS_FROM` from `$RESOLVE_OUTPUT` (same `grep '^KEY=' | cut -d= -f2-` pattern, guarded against the 'RESOLVED=...' prefix the way existing lines do). Add a new job step 'Delete Comments' modeled on the existing 'Run Script Step' (~lines 696–710): `if: always() && !cancelled() && env.DELETE_COMMENTS == 'true'`, `timeout-minutes: 30`, `env: GH_TOKEN: ${{ secrets.GH_PAT }}` (plus DELETE_COMMENTS_REPO set from the repo context), running `python3 "$SCRIPTS/features/delete_comments/delete_comments.py"`. Ensure it is mutually exclusive with the agent/script execution steps (the resolver emits DELETE_COMMENTS in place of AGENT_SELECTED/RUN_SCRIPT, so the `if` guards are naturally disjoint — verify this holds against the actual step ordering and the posting pipeline so the deletion summary gets posted exactly like a script-step result). (2) `docs/features.md` — Human Commands table: add a row for `/workflow delete comments` documenting: deletes issue comments, `--issue <id>` (mandatory on the CLI; auto-filled with the current issue when invoked from the issue), optional `--from <comment-id>` (that comment + everything after; omit to delete ALL), and the permission requirement (GH_PAT needs issues:write). Update the Precedence line to include `delete` as a terminal human command (alongside resume/force-step/run). Verify `docs/architecture.md` Routing Cascade section only if it explicitly enumerates actions — update only if needed for consistency. Final check: run the full test suite for the repo's .github/scripts area; everything must pass.

## Expected outcome

- `openhands-multi-agent.yml` extracts `DELETE_COMMENTS` / `DELETE_COMMENTS_ISSUE` / `DELETE_COMMENTS_FROM` from the resolver output and has a 'Delete Comments' job step (guarded `env.DELETE_COMMENTS == 'true'`, `GH_TOKEN: ${{ secrets.GH_PAT }}`, `timeout-minutes: 30`) that runs the step 3 script and whose summary is posted through the normal pipeline.
- The delete path is mutually exclusive with the agent and script-step paths (resolver emits `DELETE_COMMENTS` in place of `AGENT_SELECTED`/`RUN_SCRIPT`).
- `docs/features.md` Human Commands table has a `delete comments` row (flag semantics + issues:write requirement) and the Precedence line includes `delete`; `docs/architecture.md` updated only if it enumerates actions.
- Full `.github/scripts` test suite passes.

## Relevant files

- `.github/workflows/openhands-multi-agent.yml` — composite workflow: resolver invocation (~lines 259–274), `RUN_SCRIPT`/`SCRIPT_ARGS` env propagation (~319–348), 'Run Script Step' (~696–710) to model the new step on.
- `docs/features.md` — Human Commands table (~lines 15–30) + Precedence line.
- `docs/architecture.md` — Routing Cascade section (~line 146); update only if it explicitly enumerates the `/workflow` actions.

## Verification

Run the full test suite for the repo's `.github/scripts` area; everything must pass. Also re-check the YAML step guards against the posting pipeline so the deletion summary is posted exactly once like a script-step result.