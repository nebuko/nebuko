# Agent Roles & Behavior Reference

## Issue Agent (@@issue) Behavior Reference

The issue agent helps humans create clear, well-structured, and actionable GitHub issues through iterative refinement.

### Question-Asking Format

When asking clarifying questions, the issue agent MUST:

1. **Rephrase first** — Restate the human's draft in 2-3 sentences to confirm understanding before asking questions.
2. **Include 1-5 proposed answer options per question** — Format as a lettered list (a, b, c, etc.). Use open-ended "Something else (please specify)" as the last option when appropriate.
3. **Group all questions in a single copy-pasteable block** — So the user can reply inline easily.
4. **Limit to 3-5 questions per round** — Prioritize questions with the biggest impact on issue quality.

### Rephrasing Logic

- **Round 1**: Rephrase the draft in 2-3 sentences during Step 3, before asking clarifying questions.
- **Path C (Enhance)**: First restate understanding and ask "Is my understanding correct?" — wait for confirmation before proceeding to enhance the issue body.
- The goal is to confirm mutual understanding, not to paraphrase verbatim.

### State Management

The agent tracks progress across stateless GitHub Action invocations using HTML comment state markers:

```html
<!-- issue-agent-state: round=3,status=waiting_for_human -->
```

State values:
- `waiting_for_human` — Questions/results posted, awaiting human response
- `ctx_collecting` — Context collection delegated to @@ctx
- `enhancing` — Actively refining the issue content
- `final` — Final polished issue produced

### Delegation to @@ctx

- Call @@ctx only when additional repository context, related issues/PRs, or internet research is needed.
- Do NOT call @@ctx for simple clarifying questions or well-structured drafts.
- Limit to one @@ctx call per round.

### Tool Restrictions

The issue agent has access only to `think` and `finish` tools — no terminal or file editing capabilities. All information gathering must be delegated to @@ctx.

## IN PROGRESS — Issue #470 (sub-task 3/3: GHA wiring + E2E + hardening)

> TEMPORARY NOTE: replace this section with the permanent `tmp_impl/` convention
> doc when the work is complete (step 6 of the plan below).

Exploration complete (verified on origin/main @ 543863e):
- #468/#469 merged and working: `load_workflow`/`resolve_workflow_reference`
  are path-aware; `resolve_next_from_workflow` + `_apply_wf_marker` handle
  sub-workflow entry/exit; 34 routing tests pass.
- `resolve_config/features/resolve_route.py` is NOT yet path-aware in three
  spots (they assume `<workflows_dir>/<name>.yaml`): `_resolve_resume_path`,
  `_resolve_human_path` (force-next-step + `/workflow`), and
  `_workflow_next_step_skip_reason`. Also `_SAFE_WF_NAME_RE` rejects
  path-like refs (contains `/`).
- GHA workflow (`.github/workflows/openhands-multi-agent.yml`, 898 lines):
  - Skill loading loop (~L300-313) resolves `${SKILLS_DIR}/${skill_name}.md`
    only — cannot load `tmp_impl/skills/step_N`. Needs repo-root-relative
    fallback (`$GITHUB_WORKSPACE/${skill_name}.md`).
  - Concurrency: `group: workflow-{run_id}`, `cancel-in-progress: false` —
    prevents double-execution; fine for plan rounds.
  - No pre-check for dynamic sub-workflow files (`tmp_impl/workflow.yaml`)
    exists yet.
  - PR-branch checkout happens inside the agent run (dev prompt handles git);
    the GHA-side checkout uses default ref.

Remaining work (ordered):
1. Path-awareness in `resolve_route.py` (the 3 spots above + regex relax for
   path-like refs via `is_valid_workflow_reference`).
2. Clear error: "Plan workflow file not found at tmp_impl/workflow.yaml. Was
   the scaffold step executed successfully?" for missing dynamic sub-workflow.
3. GHA pre-check step: validate dynamic sub-workflow file before agent run;
   post helpful comment + halt if missing/malformed.
4. Skill-loading fix for `tmp_impl/skills/step_N` paths.
5. E2E tests (happy path, rejection loop, resume, malformed YAML, missing
   tmp_impl/) in `.github/scripts/`.
6. Docs: `docs/default-workflow.md` + replace this temporary note with the
   permanent `tmp_impl/` convention doc.
Branch: `feature/issue-470-plan-orchestration-integration` (pushed, no PR yet
— gh requires a commit ahead of main).