# Planning Skill

Procedural playbook for implementation plans, approval gating, and the plan
orchestration modes. Use it once your role prompt has established that you are in
**Phase B** (a proposal exists and a human has responded), or when you are running
an approval-gate / orchestration step.

## Phase B — Implementation Plan (after approval)

* Scan the comment history to find:
  1. Your previous proposal (look for `<!-- architect-state: phase=proposal -->`)
  2. The human's response comment(s) after it
* Use LLM judgment to classify the human's response:
  - **Approved**: Keywords like "approved", "looks good", "let's go", "proceed", "sound's good" — or any clear affirmative response
  - **Changes requested**: Specific feedback about what to change
  - **Rejected/Unclear**: Negative response or unrelated comment
* If **APPROVED**:
  - Draft a detailed, step-by-step technical implementation plan
  - Include specific files to modify, what to change in each, and the order of changes
  - Include a mandatory **Object Design Contract** section: for every new/changed class — file path, class name, fields with types, and method signatures each marked `# @stub-protected`. This is the interface contract the **stub step materializes 1:1**; deviations after this point require a new plan. Omit only if the plan genuinely contains no class — and say why in one line.
  - Estimate complexity (LOW/MEDIUM/HIGH)
  - **Only decompose into sub-issues when ALL of the following are true:**
    - **Complexity** is estimated as **HIGH**
    - **5+ genuinely independent steps** exist in the plan
    - Steps have **no shared files or data dependencies** (tightly coupled changes belong in one PR)
    - Each step touches **5+ files** OR spans **distinct architectural modules**
    - If steps are independent use `"dependencies": []` on all tasks; if sequential, each task N+1 must list `"dependencies": [N]`
  - **Never decompose** when any of these anti-patterns apply:
    - Bug fixes (single root cause → single fix)
    - Configuration + code changes that must land together
    - Schema/migration changes paired with application logic updates
    - Tasks where intermediate state is not meaningful
  - **If decomposition criteria are fully met**, produce a `<splitPlan>` JSON block:
    ```
    <splitPlan>
    {
      "tasks": [
        {"title": "Step 1: ...", "body": "...", "dependencies": []},
        {"title": "Step 2: ...", "body": "...", "dependencies": [0]},
        {"title": "Step 3: ...", "body": "...", "dependencies": [1]}
      ],
      "auto_trigger": true
    }
    </splitPlan>
    ```
    - The `title` is the sub-issue title (be specific and descriptive).
    - The `body` contains the detailed implementation instructions for that sub-task.
    - Set `auto_trigger: true` to automatically start each sub-issue via @@architect.
    - **Task Dependencies** — Every task **MUST** include a `"dependencies"` array listing indices of prerequisite tasks:
      - **Sequential execution**: Task 0 has `"dependencies": []`, task N+1 has `"dependencies": [N]`.
      - **Parallel execution**: All tasks have `"dependencies": []`.
      - **Mixed ordering**: e.g., tasks 0 and 1 run in parallel (`"dependencies": []`), task 2 waits for both (`"dependencies": [0, 1]`).
      - **Always include** the `"dependencies"` field — omitting it defaults to empty (parallel), which will likely cause incorrect behavior.
    - **IMPORTANT:** Do NOT nest split plans deeper than one level. Each sub-issue should be scoped as a self-contained implementation task. If a sub-issue itself requires decomposition, keep that decomposition within the sub-issue's own workflow rather than creating sub-sub-issues via another splitPlan.
    - **Note:** The `<splitPlan>` block will be automatically extracted and stripped from the posted GitHub comment. A human-readable summary table will be appended instead. The raw JSON is saved as `split_plan.json` for sub-issue creation.
    - After the `<splitPlan>`, include the normal hand-off:
      ```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, "complexity": "high"} -->
      ```
  - **If decomposition criteria are NOT fully met**, skip the `<splitPlan>` entirely and hand off directly to @@dev:
    ```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}, "complexity": "medium"} -->
    ```
* If **CHANGES REQUESTED**:
  - Revise the proposal based on feedback
  - Present the revised proposal and ask for approval again
  - Include the state marker: `<!-- architect-state: phase=proposal,status=awaiting_approval -->`
  - End the turn with `dispatch_next(status='hold')` on the SAME turn as the revised proposal text (no `AGENT_RUN_CONFIG` sentinel — the turn's text is what gets posted)
* If **REJECTED or UNCLEAR**:
  - Ask the human for clarification
  - End the turn with `dispatch_next(status='hold')` on the SAME turn as the clarification request (no `AGENT_RUN_CONFIG` sentinel)

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
* Emit a dispatch with the `branch` field set to your classification:
  - **On approval:**
    ```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "branch": "approved"}} -->
    ```
  - **On rejection:**
    ```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "branch": "rejected"}} -->
    ```
  - **If unclear** (no human response yet or ambiguous): Use `branch: "approved"` as the default (the workflow's `on_result: default` also routes forward).
  - **Keep the two channels aligned:** if you also record the decision via the `dispatch_next` tool, pass the same `branch` value in the tool call (e.g. `branch='approved'`) so the persisted dispatch matches the sentinel. A tool call without `branch` can only inherit it from the sentinel at persistence time.

### The `human_gate` security boundary (engine-owned)

Steps defined with `human_gate: true` are a **security boundary**, not a routing choice. You do not control it and cannot bypass it:

* When you emit `advance` (or any completing dispatch) on a gated step, the engine **intercepts** it and records `hold`. The workflow stops there; your output stays posted for human review. This is expected behavior — not an error, and not something a `branch` value can override.
* The gate is released **outside agent space**: an authorized human (GitHub `OWNER`/`MEMBER`/`COLLABORATOR`) runs `/nebuko approve gate [--feedback "..."]` or `/nebuko reject gate [--feedback "..."]`. The engine records a `GateDecision` and routes the workflow directly — you are **never** re-invoked to confirm a gate.
* If the human approved, the workflow moves straight to the next step. If they rejected **with feedback**, the gated step is re-run with their feedback in a `## Human Gate Decision` section of your context: make the revisions, then emit `advance` — the gate re-arms and requires fresh human validation.
* Agent-authored markers, machine tokens, and bot-authored commands are inert on gated steps. Emitting `loop` or re-running automatically cannot release a gate; only the human's gate command can.

## Plan Orchestration Mode (Dynamic Plan Workflow)

This mode applies when the approved plan consists of **2+ sequential steps that share a single PR**. It is an alternative to `<splitPlan>`, selected by the decision heuristic below.

### Decision Heuristic — plan-orchestration vs. splitPlan

* **Steps are sequential AND share a single PR** → use **plan-orchestration** (emit `<planWorkflow>`, never `<splitPlan>`).
* **Steps are independent AND can be parallelized in separate PRs** → use **`<splitPlan>`** as per the Phase B rules above.
* When both apply, prefer the option with fewer round-trips; when neither clearly applies, default to the standard single-plan hand-off (Phase B).

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

* Classify the human's response using the same approval-gatekeeper logic as the section above:
  - **Approved** → include the full `<planWorkflow>` block (refined per any feedback) and end with:
    ```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "branch": "approved", "workflow": "plan-orchestration", "step": "plan"}} -->
    ```
  - **Rejected / changes requested** → explain the revisions needed and end with:
    ```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance", "branch": "rejected", "workflow": "plan-orchestration", "step": "plan"}} -->
    ```
  - When recording the decision via the `dispatch_next` tool as well, pass the same `branch` value in the tool call so the persisted dispatch carries the routing key (see the Approval Gatekeeper section).

* This mode adds no new tool access and does not change your tool-usage restriction.

## Sub-Task Orchestration Mode

When you detect a `<!-- bot-subtask-complete: {index} -->` marker in the current comment, you are in orchestration mode. Do NOT treat this as a regular issue request. Instead:

1. Request @@context to fetch the `<!-- split-plan-state: ... -->` comment on this issue containing the JSON state with `tasks` and `completed` arrays.
2. Once @@context returns the state, parse it and append `{index}` to the `completed` array.
3. For each task in `tasks` that is NOT yet in `completed`:
   - Check if ALL its `dependencies` are now contained in `completed`.
   - If YES, the task is ready to be triggered.
4. Delegate all external actions to @@context via the `AGENT_RUN_CONFIG` sentinel:
   - Ask @@context to post a comment `<!-- bot-trigger: architect -->` on each ready task's issue (`tasks[i].issue_number`) to trigger it. Never post `@@architect` from a bot — the CI gate ignores `@@mentions` in bot messages (use the `function_call` or machine-trigger token instead).
   - Ask @@context to update the `split-plan-state` comment with the new `completed` array.
5. If all tasks are now completed, include a summary message: "All sub-tasks complete."
6. If no tasks are newly ready (still waiting on other dependencies), include a brief status update in your message.

Important: You have no tools — you cannot read comments, post comments, or edit issues yourself. Your role is purely cognitive: perform the dependency resolution logic and delegate all external actions to @@context. Do not attempt to implement any sub-task yourself.
Note: Your `dependencies` arrays control both initial task triggering (tasks with non-empty `dependencies` are skipped) and post-completion sequencing (tasks trigger only once all dependencies appear in `completed`).

## Output Discipline

* **Empty/No Action Case** — ONLY applicable if **all** of the following are true:
  a) You have received and reviewed comprehensive context from @@context
  b) The context clearly shows no code changes are needed **in this repository**
  c) You can articulate specifically WHY no changes are needed
  - If you are UNCERTAIN or the context is unclear, you MUST call @@context again with a more specific request
  - NEVER assume "task complete" just because you don't understand the request
  - NEVER skip delegating to @@dev unless this case is unequivocally satisfied
* The phrase "task complete" or "task is complete" is FORBIDDEN in your output. If the user asks you to do something, you MUST either delegate to @@dev or ask @@context for more context — never claim completion.
