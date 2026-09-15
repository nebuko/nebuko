# Stage A / Step 6: Behavior-parity net + final verification

## Instructions

Build the parity corpus and harness, then run the whole verification gate.

1. **Corpus**: create `parity/cases/*.json` — each file:
   ```json
   {"name": "...", "event_payload": {"<recorded real GHA event payload>": null}, "history": ["<recorded comment dicts>"], "expected": {"ROLE_NAME": "...", "WORKFLOW_STEP": "...", "SKIP_AGENT": "...", "PROMPT_LOCATION": "...", "REASON": "..."}}
   ```
   Harvest payloads and histories from real run artifacts (`console.log`, `output/metrics-*.json`, and actual issue comment threads carrying `WORKFLOW_STATE` snapshots and marker sequences). Expectations are the legacy bash's OBSERVED decisions recorded as constants (the bash layer cannot execute in the test environment).

2. **Required minimum case set**:
   - a plain `@agent` mention trigger
   - a comment with NO valid task (expect `SKIP_AGENT` with the legacy "No valid task found" reason wording)
   - a bot-authored comment that is ignored
   - a loop-guard skip (same-agent marker in trigger body)
   - a circuit-breaker abort at the marker threshold
   - an `on_result` workflow routing case
   - a sub-task-completion marker trigger
   - a plan-orchestration step routing case

3. **Harness**: `parity/run_parity.py`, pytest-compatible: for each case construct a `GitHubAdapter` (or in-memory provider) with the recorded payload/history, run `engine.run_loop.run()` with `dry_run=True` (no network, no `gh` subprocess, no LLM) up to the routing decision, and assert the observed decisions equal the expected dict EXACTLY.

4. **Verification gate, all must pass**:
   - full existing test suite in `.github/scripts/` (including updated seam tests)
   - all new tests in `engine/`, `adapters/`, `parity/`
   - `git status` / `git diff --name-only` proving ZERO changed files under `.github/workflows/`, `workflows/`, and `agent-config.yaml`
   - a diff check that the seven seam scripts' default-path CLI outputs are unchanged for the recorded fixtures

5. **Document the corpus** in `parity/README.md` (how to record a new case) — this corpus is the green-on-every-merge regression guard for the whole A→B→C→D migration.

## Constraints

- No workflow file changes.
- No behavior changes to existing defaults.
- The old workflow remains the source of truth (read-only reference).
- **A parity failure is a hard stop** — the PR must not merge with a red corpus.

## Expected outcome

- `parity/cases/` holds ≥8 recorded cases with observed-decision expectations.
- `parity/run_parity.py` runs all cases green under `dry_run=True`.
- Verification gate passes: full suite green, zero diff under `.github/workflows/`, `workflows/`, `agent-config.yaml`.
- `tmp_impl/` remains on the branch (it is consumed by the workflow engine; cleanup happens in the `sub_clean` step).

## Relevant files

- `engine/run_loop.py`, `engine/models.py` (steps 1 & 5)
- `adapters/github.py`, `adapters/local.py` (step 4)
- `.github/scripts/event_normalizer.py` (`normalize_trigger`)
- `.github/scripts/resolve_config/resolve_config.py` (result dict = parity expectation keys)
- `.github/scripts/loop_guard.py`, `.github/scripts/circuit_breaker.py`
- `.github/workflows/openhands-multi-agent.yml` (read-only reference for loop semantics and observed decisions)
- `console.log`, `output/metrics-*.json` (parity fixture sources)
- `workflows/plan-orchestration.yaml`, `workflow.yaml` (routing fixtures)

## Commit message

`[step 6/6] Stage A / Step 6: Behavior-parity net + final verification`