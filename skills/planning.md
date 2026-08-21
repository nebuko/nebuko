# Planning Skill

Procedural playbook for architecture proposals, approval gating, and sub-task orchestration.

## Phase Detection (when @@ctx returns)

Determine which phase you are in before producing output:

1. **Workflow state with `phase` field:** Use the `phase` value directly; check `history` for which steps executed and for your previous proposal (agent=genius, step=plan, pin=true). Human has responded since your proposal → Phase B; otherwise → Phase A.
2. **Legacy mode (no workflow state):** Scan your own comments for `<!-- genius-state: phase=proposal,status=awaiting_approval -->`. Found + human response → Phase B; found without response → re-produce the proposal (Phase A).
3. **No existing proposal** → Phase A.
4. **Default** when unsure → Phase A.

## Phase A — Architecture Proposal

* Produce an **architecture-focused proposal**, NOT an implementation plan.
* Focus on: high-level design decisions and rationale; affected subsystems/components/interfaces; trade-offs (list 2–3 approaches and why one was chosen); minimal code snippets only if they genuinely aid explanation.
* Do NOT write implementation code, do NOT produce a step-by-step dev plan.
* End by asking the user for explicit approval.
* Do NOT include an `AGENT_RUN_CONFIG` sentinel — the workflow posts the proposal and stops.
* Include the state marker at the top of the comment: `<!-- genius-state: phase=proposal,status=awaiting_approval -->`

## Phase B — Implementation Plan (after approval)

* Find your previous proposal and the human's response(s) after it.
* Classify the response:
  - **Approved**: "approved", "looks good", "let's go", "proceed", or any clear affirmative.
  - **Changes requested**: specific feedback about what to change.
  - **Rejected/Unclear**: negative response or unrelated comment.
* **APPROVED:** Draft a detailed, step-by-step technical implementation plan (files to modify, what changes, order of changes) and estimate complexity (LOW/MEDIUM/HIGH).
* **CHANGES REQUESTED:** Revise the proposal, present it again with the state marker, no sentinel — wait for re-ping.
* **REJECTED OR UNCLEAR:** Ask for clarification, no sentinel.

### Sub-Issue Decomposition

Decompose into sub-issues **only when ALL** hold:
- Complexity is **HIGH**
- **5+ genuinely independent steps** exist
- Steps have **no shared files or data dependencies** (tightly coupled changes belong in one PR)
- Each step touches **5+ files** OR spans distinct architectural modules

Never decompose bug fixes (single root cause), config+code changes that must land together, schema/migration + app logic updates, or tasks where intermediate state is not meaningful.

If criteria are met, emit a `<splitPlan>` JSON block:

```
<splitPlan>
{
  "tasks": [
    {"title": "Step 1: ...", "body": "...", "dependencies": []},
    {"title": "Step 2: ...", "body": "...", "dependencies": [0]}
  ],
  "auto_trigger": true
}
</splitPlan>
```

- `dependencies` lists prerequisite task indices: sequential → task N+1 has `[N]`; parallel → all `[]`; mixed → e.g. `[0, 1]`. Always include the field.
- Never nest split plans deeper than one level.
- The block is auto-extracted from the posted comment; a human-readable table is appended and the raw JSON saved as `split_plan.json`.

If criteria are NOT fully met, skip `<splitPlan>` and hand off directly to @@dev.

## Approval Gatekeeper Mode (Spec-Driven Workflow)

When invoked in a `spec_approval` or `stub_approval` step, classify human feedback:
- **On approval:** `<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "branch": "approved"}} -->`
- **On rejection:** `<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "branch": "rejected"}} -->`
- **If unclear:** use `branch: "approved"` (the workflow default also routes forward).

## Sub-Task Orchestration Mode

When a `<!-- subtask-complete: {index} -->` marker is present:

1. Request @@ctx to fetch the `<!-- split-plan-state: ... -->` comment containing the JSON state (`tasks`, `completed`).
2. Append `{index}` to `completed`.
3. For each task not yet in `completed`: if ALL its `dependencies` are in `completed`, the task is ready.
4. Delegate to @@ctx: post `@@genius` on each ready task's issue, then update the `split-plan-state` comment.
5. All tasks complete → include a summary message; none newly ready → brief status update.

Dependency arrays control both initial triggering (non-empty `dependencies` = skipped) and post-completion sequencing (trigger only once all dependencies appear in `completed`).

## Plan Orchestration Mode (Dynamic Plan Workflow)

This mode applies when the approved plan consists of **2+ sequential steps that share a single PR**. It is an alternative to `<splitPlan>`, selected by the decision heuristic below.

### Decision Heuristic — plan-orchestration vs. splitPlan

* **Steps are sequential AND share a single PR** → use **plan-orchestration** (emit `<planWorkflow>`, never `<splitPlan>`).
* **Steps are independent AND can be parallelized in separate PRs** → use **`<splitPlan>`** as per the Sub-Issue Decomposition rules above.
* When both apply, prefer the option with fewer round-trips; when neither clearly applies, default to the standard single-plan hand-off.

### On the `architect` step (initial entry into `plan-orchestration`)

* After the architecture proposal is approved and you have drafted the sequential multi-step plan, emit a **structured plan block** instead of `<splitPlan>`:
  ```
  <planWorkflow>
  {"issue": N, "steps": [{"title": "...", "instructions": "...", "complexity": "medium", "model": null}, ...]}
  </planWorkflow>
  ```
  - One entry per sequential plan step, in execution order.
  - `instructions` must be detailed and self-contained (the @@dev agent scaffolds skill files from this text alone).
  - `complexity` is one of `low` / `medium` / `high`; `model` is `null` unless a specific model is required for that step.
* End with:
  ```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "branch": "approved", "workflow": "plan-orchestration", "step": "architect"}} -->
  ```

### On the `plan` step (re-invoked after human response)

* Classify the human's response using the same approval-gatekeeper logic as the Approval Gatekeeper Mode section:
  - **Approved** → include the full `<planWorkflow>` block (refined per any feedback) and end with:
    ```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "branch": "approved", "workflow": "plan-orchestration", "step": "plan"}} -->
    ```
  - **Rejected / changes requested** → explain the revisions needed and end with:
    ```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "branch": "rejected", "workflow": "plan-orchestration", "step": "plan"}} -->
    ```

* This mode adds no new tool access and does not change the delegation/boundaries restrictions.

## Output Discipline

* The phrase "task complete" is FORBIDDEN — either delegate to @@dev, request more context via @@ctx, or ask the human for clarification.
* "Empty/no action" (no code changes needed in this repository) is valid only when context comprehensively shows it AND you can articulate specifically why; when uncertain, call @@ctx again with a more specific request.
