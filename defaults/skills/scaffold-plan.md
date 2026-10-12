# Scaffold-Plan Mode

You are in plan-scaffolding mode for a branch-mounted dynamic plan workflow (issue #462). Your job is to convert the approved `<planWorkflow>` block into a runnable per-step sub-workflow committed on the feature branch. Follow these instructions strictly.

## Inputs

- The issue number `N` (from `$TARGET_NUMBER` or the issue context).
- The **approved structured plan**: find the `<planWorkflow>` JSON block in the @@architect comment on this issue:
  ```
  <planWorkflow>
  {"issue": N, "steps": [{"title": "...", "instructions": "...", "complexity": "medium", "model": null}, ...]}
  </planWorkflow>
  ```

## What You MUST Do

1. **Create/checkout the feature branch** using the existing convention from your git workflow rules: `feature/issue-{N}-{short-name}`. First check for an existing open PR linked to this issue and reuse its head branch if one exists.
2. **Create the `tmp_impl/` directory structure** at the repository root:
   - `tmp_impl/workflow.yaml`
   - `tmp_impl/skills/step_1.md`, `tmp_impl/skills/step_2.md`, ... (one file per plan step)
3. **Generate `tmp_impl/workflow.yaml`** — one step per plan item, in plan order:
   ```yaml
   name: tmp-impl
   description: "Dynamically generated per-step execution workflow for issue #N"
   steps:
     - name: step_1
       workflow: dev-review-loop
       skill: [tmp_impl/skills/step_1]
       next: step_2
     - name: step_2
       workflow: dev-review-loop
       skill: [tmp_impl/skills/step_2]
       next: null
   ```
   Rules:
   - Each step references `workflow: dev-review-loop` and `skill: [tmp_impl/skills/step_N]` (N = 1-based plan index).
   - Optional fields: copy `complexity` (and `model` if non-null) from the corresponding plan step entry into the step definition.
   - The **last** step has `next: null`.
4. **Write each `tmp_impl/skills/step_N.md`** with clear, self-contained content:
   - Step title (from the plan).
   - Detailed instructions (verbatim from the plan's `instructions`, expanded with concrete file paths and context where helpful).
   - Expected outcome (how to verify the step is done).
   - Relevant file context (which files to read/modify for this step).
5. **Commit** with message: `chore: scaffold plan workflow for issue #N`
6. **Push** to the branch.
7. **Report the pushed branch to the engine** by calling the `report_scaffold_branch` tool **immediately after pushing**, with the exact pushed branch name as the `branch` argument (issue #636 — the engine persists it as `scaffold_branch` so later runs can fetch `tmp_impl/workflow.yaml` from this branch). Call it **once**, before `dispatch_next`. If the tool returns a validation error, correct the ref and re-call. Do NOT rely on printing a `SCAFFOLD_BRANCH:` marker in your answer — that prose marker is only parsed as a legacy fallback for older runs.
8. **Dispatch**: end your message with — **no `agent` field, no self `function_call`** (the engine resolves the next step from the workflow definition; a self-hand-off re-triggers the same-agent guard, issue #618):
   ```
<!-- AGENT_RUN_CONFIG {"dispatch": {"status": "advance"}} -->
   ```

## What You MUST NOT Do

- Implement any of the plan steps yourself — you only scaffold; execution happens in the `dev-review-loop` sub-workflow.
- Modify any file outside `tmp_impl/` (and the feature-branch setup itself).
- Invent plan steps or reorder them — the `<planWorkflow>` block is authoritative.
- Skip the commit/push — the sub-workflow mounts `tmp_impl/workflow.yaml` from the branch.

## Escalation Protocol

If the `<planWorkflow>` block is missing, malformed, or references an unclear step:
→ Stop and report the blocker to @@architect. Do NOT guess at step content.
