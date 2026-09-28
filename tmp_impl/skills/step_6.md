# Step 6: behavior-parity net

## Instructions

Build a parity corpus under `tests/parity/`: recorded real GHA event payloads
(captured from actual run logs) paired with the legacy bash's observed decisions
(`ROLE_NAME`, `WORKFLOW_STEP`, `SKIP_AGENT`, prompt selection) as expectations.

- Drive `Engine` against `GitHubAdapter`-backed fixtures and assert the driver
  reproduces **every** legacy decision exactly.
- Drive `LocalAdapter` for the same scenarios and assert equivalence.
- This corpus is the regression guard for the whole migration and **must run on
  every merge**.
- The old workflow remains untouched and is **never invoked** by the tests.

No workflow-file changes.

## Expected outcome

- `tests/parity/` contains recorded payload fixtures and expectation files for
  the legacy decisions (`ROLE_NAME`, `WORKFLOW_STEP`, `SKIP_AGENT`, prompt
  selection).
- Driving `Engine` with `GitHubAdapter` over the fixtures reproduces each
  recorded decision exactly.
- The same scenarios driven with `LocalAdapter` are equivalent to the
  `GitHubAdapter` run.
- The parity suite passes and can run on every merge.
- No diff outside `tests/parity/` (+ any small fixture additions); no changes to
  `.github/workflows/`.

## Relevant file context

- Driver: `engine/run_loop.py` (Step 5); adapters: `adapters/github.py`,
  `adapters/local.py`.
- Legacy decisions to capture: `ROLE_NAME`, `WORKFLOW_STEP`, `SKIP_AGENT`, prompt
  selection — as resolved by `.github/scripts/resolve_config/resolve_config.py`,
  `.github/scripts/resolve_workflow.py`, and the prompt-building path in
  `.github/scripts/agent_script/`.
- Recorded real payloads: mirror the shape consumed by
  `.github/scripts/event_normalizer.py` (see `.github/scripts/fixtures/`).
- Reference (read-only, never executed by tests):
  `.github/workflows/openhands-multi-agent.yml`.
- New files: `tests/parity/` corpus + test module(s).
