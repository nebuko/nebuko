# Planning Skill

Procedural playbook for implementation plans, approval gating, and the plan
orchestration modes. Attached to @@architect. Use it when @@context has returned
and you are emitting the **single planning artifact** behind the workflow's one
`human_gate:plan`, or when you are running an approval-gate / orchestration step.

## The Planning Artifact (single turn)

The `plan` step is **one** human gate: emit **one** artifact per turn — never a
separate "architecture proposal" turn followed by an implementation turn. Ship
the architecture decision and the executable detail together, marked with
`<!-- architect-state: phase=plan,status=awaiting_gate -->` (fresh) or
`phase=plan,status=revising` (revision).

Emit the artifact with these sections **in this order**:

1. **`## Context (root cause)` (visible).** The root cause / problem framing for
   the task being planned: what is actually wrong or missing today, and the
   verified facts that constrain the fix.
2. **`## Decision` (visible).** The chosen design: the affected
   subsystems/components/interfaces, and any key rulings baked into the
   contract. It must include the **Object Design Anchor (mandatory)** — *where
   the change lives* and the **primary class/object** (new or extended) with the
   file path per repo convention, key fields with types, and a one-line
   responsibility statement. State explicitly that **the Object Design Anchor
   (primary object + placement) is the design being approved.**
3. **`## Approaches considered` (visible).** A markdown table of the alternatives
   weighed, with **at least 3 rows** by default (Approach / Summary / Pros /
   Cons / Verdict), the chosen approach marked (e.g. ✅ **Chosen**). Three is the
   expected floor in real cases: when fewer than 3 genuine alternatives exist
   (e.g. a narrowly-scoped docs fix), emit the real ones and justify in one line
   why no third candidate exists — never invent a straw man.
4. **`## Object Design Contract` (visible).** For every new/changed class: file
   path, class name, fields with types, and method signatures each marked
   `# @stub-protected`. This is the interface contract the **stub step
   materializes 1:1**; deviations after this point require a new plan. Omit only
   if the plan genuinely contains no class — and say why in one line.
5. **`## Implementation Plan` (collapsed).** Inside a single `<details>` block so
   only the summary is visible by default:

   ```
   <details>
   <summary>Implementation Plan</summary>

   1. **`path/to/file`** — what changes and why
   ...
   </details>
   ```

   - Ordered dev steps: files to modify, what changes in each, and the order.
6. **`## Questions (if relevant)` (visible, optional).** Open questions for the
   human gate, each with the default you will proceed with if unanswered. Omit
   the section entirely when there are no open questions.

* Class signatures, field lists, and method signatures (marked
  `# @stub-protected`) are **allowed design artifacts**; method *bodies* and
  implementation code are forbidden.
* **Anti-pattern rule:** string/namespace encodings or function-only designs are
  a **last resort** — if one is proposed anyway, justify in one sentence why no
  class can model it.
* Estimate complexity (LOW/MEDIUM/HIGH) and pass it in the dispatch.
* Hand off to @@dev with `dispatch_next(status='advance', complexity=<tier>)` — the
  gated `plan` step then holds for the human until the gate is approved. For a
  plan of 2+ sequential steps that share a single PR, use the **Plan
  Orchestration Mode** below instead of a plain hand-off.

## Revision Path (gate rejected with feedback)

When the `plan` step is re-run after `/nebuko reject gate --feedback "..."`, your
context carries a `## Human Gate Decision` section (`**Status:** rejected`,
`**Feedback:**`). Locate your prior artifact — your own previous comment carrying
the `<!-- architect-state: ... -->` marker — and revise it in place, addressing
every point of the feedback while keeping the six-section shape above
(visible Context/Decision/Approaches/Object Design Contract + collapsed Implementation
Plan):

* Marker: `<!-- architect-state: phase=plan,status=revising -->`
* Re-emit the **full** artifact (all six sections) — never a diff-only reply.
* End with `dispatch_next(status='advance', complexity=...)` on the SAME turn as
  the artifact text — the gate re-arms and requires fresh human validation.

## Approval Gatekeeper Mode (Spec-Driven Workflow)

> **Non-authoritative for `human_gate`.** This mode is a UX convenience: it
> classifies human intent so the conversation reads naturally. It does **not**
> release a `human_gate` step — that is an engine-owned decision triggered only
> by an identity-verified `/nebuko approve gate` / `/nebuko reject gate`
> command (see the security boundary below). Never assume your classification
> moved the workflow.

When invoked in the `spec_approval` or `stub_approval` step of the spec-driven workflow, your role is to evaluate whether human feedback indicates approval or rejection.

* Scan the comment history for human responses since the last spec/stub was posted.
* Classify the human's response as:
  - **Approved**: Keywords like "approved", "looks good", "let's go", "proceed", "sound's good" — or any clear affirmative response
  - **Rejected**: Keywords like "rejected", "no", "not good", "changes needed" — or any clear negative response
* Record the decision with the `dispatch_next` tool, setting the `branch` field to your classification:
  - **On approval:** `dispatch_next(status="advance", branch="approved")`
  - **On rejection:** `dispatch_next(status="advance", branch="rejected")`
  - **If unclear** (no human response yet or ambiguous): use `branch="approved"` as the default (the workflow's `on_result: default` also routes forward).
  - Do not write an `AGENT_RUN_CONFIG` sentinel yourself — the engine renders it from the typed `dispatch_next` call (issue #876), so the persisted dispatch and the durable record stay aligned.

### The `human_gate` security boundary (engine-owned)

Steps defined with `human_gate: true` are a **security boundary**, not a routing choice. You do not control it and cannot bypass it:

* When you emit `advance` (or any completing dispatch) on a gated step, the engine **intercepts** it and records `hold`. The workflow stops there; your output stays posted for human review. This is expected behavior — not an error, and not something a `branch` value can override.
* The gate is released **outside agent space**: an authorized human (GitHub `OWNER`/`MEMBER`/`COLLABORATOR`) runs `/nebuko approve gate [--feedback "..."]` or `/nebuko reject gate [--feedback "..."]`. The engine records a `GateDecision` and routes the workflow directly — you are **never** re-invoked to confirm a gate.
* If the human approved, the workflow moves straight to the next step. If they rejected **with feedback**, the gated step is re-run with their feedback in a `## Human Gate Decision` section of your context: make the revisions, then emit `advance` — the gate re-arms and requires fresh human validation.
* Agent-authored markers, machine tokens, and bot-authored commands are inert on gated steps. Emitting `loop` or re-running automatically cannot release a gate; only the human's gate command can.

## Plan Orchestration Mode (Dynamic Plan Workflow)

This mode applies when the approved plan consists of **2+ sequential steps that share a single PR**. Emit a `<planWorkflow>` block so the engine scaffolds a branch-mounted dynamic plan workflow; each step then runs its own @@dev round with review in the same PR.

### On the `architect` step (initial entry into `plan-orchestration`)

* After the architecture proposal is approved and you have drafted the sequential multi-step plan, emit a **structured plan block**:
  ```
  <planWorkflow>
  {"issue": N, "steps": [{"title": "...", "instructions": "...", "complexity": "medium", "model": null}, ...]}
  </planWorkflow>
  ```
  - One entry per sequential plan step, in execution order.
  - `instructions` must be detailed and self-contained (the @@dev agent scaffolds skill files from this text alone).
  - `complexity` is one of `low` / `medium` / `high`; `model` is `null` unless a specific model is required for that step.
* Record the decision with the `dispatch_next` tool: `dispatch_next(status="advance", branch="approved", workflow="plan-orchestration", step="architect")`. Do not write an `AGENT_RUN_CONFIG` sentinel yourself — the engine renders it from the typed call (issue #876).

### On the `plan` step (re-invoked after human response)

* Classify the human's response using the same approval-gatekeeper logic as the section above:
  - **Approved** → include the full `<planWorkflow>` block (refined per any feedback) and call `dispatch_next(status="advance", branch="approved", workflow="plan-orchestration", step="plan")`
  - **Rejected / changes requested** → explain the revisions needed and call `dispatch_next(status="advance", branch="rejected", workflow="plan-orchestration", step="plan")`
  - Do not write an `AGENT_RUN_CONFIG` sentinel yourself — the engine renders it from the typed `dispatch_next` call (issue #876).

* This mode adds no new tool access and does not change your tool-usage restriction.

## Output Discipline

* **Empty/No Action Case** — ONLY applicable if **all** of the following are true:
  a) You have received and reviewed comprehensive context from @@context
  b) The context clearly shows no code changes are needed **in this repository**
  c) You can articulate specifically WHY no changes are needed
  - If you are UNCERTAIN or the context is unclear, you MUST call @@context again with a more specific request
  - NEVER assume "task complete" just because you don't understand the request
  - NEVER skip delegating to @@dev unless this case is unequivocally satisfied
* The phrase "task complete" or "task is complete" is FORBIDDEN in your output. If the user asks you to do something, you MUST either delegate to @@dev or ask @@context for more context — never claim completion.
