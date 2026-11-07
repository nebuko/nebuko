---
title: "Remote workflows (zero-copy)"
audience: operator
covers:
  - src/nebuko/engine/lib/remote_workflow.py
sidebar_group: "Concepts"
sidebar_label: "Remote workflows"
sidebar_order: 25
---

# Remote workflows (zero-copy)

Consumer repositories do not need to vendor the workflow YAML files from this
repo. `remote_workflow.py`
(`engine/lib/remote_workflow.py`) provides zero-copy fetching:

```python
from remote_workflow import fetch_remote_workflow
path = fetch_remote_workflow(
    repo="https://github.com/org/shared-workflows.git",
    ref="v1.2.3",
    workflow_name="deploy.yaml",
)
```

- **Mechanism** — shallow git clone/fetch of the pinned commit or tag, then
  locate the requested workflow YAML inside it.
- **Caching** — previously fetched repos are cached under
  `~/.gh-workflows/cache/`; re-fetches of the same ref are no-ops.
- **Precedence** — a local `workflows/<name>.yaml` always wins over a remotely
  fetched workflow of the same name, so consumers can fork any built-in
  workflow by dropping a local copy.
- **Update flow** — consumers pick up workflow fixes/features by bumping the
  ref, without any file changes in their own repository.

The consumer-side entry point is `workflow.yaml` — the thin `workflow_call`
wrapper each consumer repo writes to invoke this repo's reusable orchestrator
with their `agent_config_path` and secrets (a ready-to-paste example lives in
[adoption.md](adoption.md)).
