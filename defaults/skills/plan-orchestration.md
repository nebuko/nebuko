---
description: ""
---

## Plan Orchestration Mode (dynamic plan workflow)

For an approved plan of **2+ sequential steps sharing a single PR**, emit a `<planWorkflow>` block so the engine scaffolds a branch-mounted workflow; each step then runs its own @@dev round with review in the same PR.

**On the `architect` step** (entry into `plan-orchestration`): after drafting the multi-step plan, emit
```
<planWorkflow>
{"issue": N, "steps": [{"title": "...", "instructions": "...", "complexity": "medium", "model": null}, ...]}
</planWorkflow>
```
One entry per step, in execution order. `instructions` must be detailed and self-contained (@@dev scaffolds skill files from this text alone). `complexity` is `low`/`medium`/`high`; `model` is `null` unless a specific model is required. Then call `dispatch_next(status="advance", branch="approved", workflow="plan-orchestration", step="architect")`.

**On the `plan` step** (re-invoked after the human responds), classify the response as in Approval Gatekeeper Mode:
- **Approved**: include the full `<planWorkflow>` block (refined per any feedback) and call `dispatch_next(status="advance", branch="approved", workflow="plan-orchestration", step="plan")`.
- **Rejected / changes requested**: explain the revisions needed and call the same with `branch="rejected"`.