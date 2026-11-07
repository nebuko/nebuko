---
title: "Step hooks (pre_hook / post_hook)"
audience: user
covers:
  - src/nebuko/engine/hook_runner.py
sidebar_group: "Guides"
sidebar_label: "Step hooks"
sidebar_order: 15
---

# Step hooks (pre_hook / post_hook)

An **agent** step can declare two deterministic hooks that run **in the session
workspace** around the agent invocation (issue #884): `pre_hook` before the
agent (e.g. initialise a dev environment) and `post_hook` after the agent
attempt (e.g. gather file/statistics). Each hook is a repo-local command — the
same shape as a script step — nested under its key:

```yaml
steps:
  - name: implement
    agent: dev
    pre_hook:
      path: scripts/init-dev-env.sh   # required, repo-relative
      args: ["--full"]                # optional argv list (no shell interpolation)
      timeout_minutes: 10             # optional, >= 1 (default 30, capped at 60)
    post_hook:
      path: scripts/collect-stats.sh
    next: review
```

Mechanics:

1. The resolver emits the step's declared hooks as a `STEP_HOOKS` JSON contract
   (separate from the step's task assignment); the engine decodes it into
   `HookSpec` values before the agent phase runs.
2. `pre_hook` runs after the pre-agent hand-off is staged and immediately before
   the agent is invoked, in the session workspace (the repo root) with the
   agent's environment. It is **fail-closed**: a non-zero exit or a timeout
   skips the agent entirely and fails the step through the normal failure path
   (the same handling as an agent exception).
3. `post_hook` runs after the agent attempt in a finally position — on success,
   on failure, and even when the agent raised or was skipped. It is
   **fail-open**: its failure never changes the step outcome or routing, but on
   a non-zero exit or timeout a `post_hook failed: ...` note (including the
   captured output tail) is recorded under `post_hook_failure` in the persisted
   workflow state, so a broken hook stays visible on the thread.
4. Hooks are only valid on `agent` steps. A `script` step has no agent session
   and a `workflow`/`repo` step dispatches elsewhere; declaring a hook on any of
   them is rejected at parse time.

Security notes:

- `path` is repo-relative; absolute paths and `..` segments are rejected at
  parse time, mirroring `script.path`.
- Hooks run in the same CI job with the job's environment and permissions — the
  same trust level as the workflow YAML itself. A future sandboxed session can
  move hook execution behind the agent host without touching the schema.

v1 boundaries (deferred): string shorthand (`pre_hook: scripts/x.sh`), ordered
lists of hooks, hooks on `script`/`workflow`/`repo` steps, an `on_fail` policy
switch, and posting hook output as separate comments.
