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

## Plan-Orchestration & the `tmp_impl/` Convention

For multi-step issues, the `plan-orchestration` workflow drives a dynamically
mounted plan. The scaffold step generates a repo-root-relative
`tmp_impl/workflow.yaml` (plus `tmp_impl/skills/step_N`) in the checked-out
PR branch; the engine then loads it as a **path-like workflow reference**
(resolved relative to the working directory) rather than a plain name from
the static `workflows/` directory.

Key behaviors:

- **Path-like refs** (`tmp_impl/workflow.yaml`, `tmp_impl/skills/step_N`) are
  honored by routing/resolution (`is_path_like_reference`) exactly like
  plain-name workflows; single-level flows are unaffected.
- **Missing file** produces a clear, actionable error ("Plan workflow file
  not found at tmp_impl/workflow.yaml. Was the scaffold step executed
  successfully?") instead of a silent crash or a generic stall.
- **Malformed YAML** in `tmp_impl/` surfaces a parse error (filename/line) to
  the issue instead of crashing the run.
- **Nested depth:** a plan may itself reference further sub-workflows (e.g.
  per-step `dev-review-loop`); an ordered `parent_chain` of reloadable refs
  persists across resumes so the engine can bubble back through every tier.
- **Cleanup:** `tmp_impl/` is removed by the `sub_clean` step before final review.

Detailed lifecycle documentation lives in `docs/default-workflow.md`.
- **Entry/exit:** on entry the engine descends nested `workflow:` refs so the
  first dispatched task is a real agent (`_effective_entry_step`); on terminal
  exit it bubbles through the full `parent_chain` back to `plan-orchestration.sub_clean`.

E2E coverage: `.github/scripts/test_plan_orchestration_e2e.py` (full lifecycle,
rejection loop, resume, malformed/missing-file error paths).

>>>>>> b52aa2c (test+docs(#470): plan-orchestration E2E scenarios, docs, tmp_impl convention)
