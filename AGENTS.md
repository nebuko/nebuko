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
> doc when the work is complete. Branch `feature/issue-470-plan-orchestration-integration`,
> **PR #494 open** (do NOT open a second branch/PR). HEAD is even with origin/main.

### Landed (commit 8bbc839 — verified, 46 routing/load tests green)
1. Path-awareness in `resolve_route.py`: `_safe_workflow_ref` gate accepts
   path-like refs (`is_valid_workflow_reference`); `_resolve_resume_path`,
   `_resolve_human_path`, `_workflow_next_step_skip_reason` are path-aware.
2. Clear error for missing `tmp_impl/workflow.yaml`: *"Plan workflow file not
   found at tmp_impl/workflow.yaml. Was the scaffold step executed successfully?"*
   Malformed YAML → readable filename+line error (never silent crash).
3. GHA pre-check steps (post dedup'd validation comment + halt) for
   missing/malformed dynamic sub-workflow.
4. Skill-loading resolves repo-root-relative paths `tmp_impl/skills/step_N`
   (fallback `$GITHUB_WORKSPACE/${skill}.md`).

### ROOT CAUSE of "No valid task found" stall — CONFIRMED (multi-tier nesting)
The engine tracks sub-workflow depth with **single-level** `parent_workflow` /
`parent_workflow_step` only. Real plan-orchestration is **3 tiers deep**:
`plan-orchestration → tmp_impl/workflow.yaml → dev-review-loop`. Three compounding
bugs (reproduced by lifecycle probe `/tmp/probe_life.py` against the real scaffold):

- **Bug B** — `.github/scripts/resolve_workflow.py` `_sub_workflow_entry_marker`
  (L~538) records `"parent_workflow": parent_workflow.name` (the loaded def's
  `name:` field, e.g. `tmp-impl`), NOT the reloadable **reference**
  (`tmp_impl/workflow.yaml`). On the return leg `_return_to_parent_on_terminal`
  calls `_load_workflow_by_reference(parent_workflow)` with that name → fails for
  a path-mounted parent → `task=None` → GHA "No valid task found".
- **Bug C** — each nested entry **overwrites** `parent_workflow`, discarding the
  grandparent (`plan-orchestration`). Bubbling from `dev-review-loop` can only
  reach tier 1, never back to `plan-orchestration`'s `sub_clean`/`final_review`.
- **Bug D** — same as B at persistence: state stores the def name, not the ref, so
  resume re-resolution breaks for path-mounted parents.

Probe trace (happy path): `[2] scaffold→sub_impl {task:None}` ← BUG B; entry
`[3]` sets `parent_workflow="plan-orchestration"` correctly (tier 1), but entering
`dev-review-loop` would overwrite it → exit can't bubble to `plan-orchestration`.

### FIX DESIGN (implement next — minimal surface, backward compatible)
Core idea: persist an ordered **ancestor chain** of *reloadable references*
(newest-first) instead of a single `parent_workflow`.

1. **`WorkflowState`** (`.github/scripts/workflow_state/workflow_state.py`),
   **`Dispatch`** (`sub_domains/dispatch/dispatch.py`), **`StepResolution`
   (`sub_domains/step_resolution/step_resolution.py`)**: add optional
   `parent_chain: Optional[list[str]] = None` (refs, newest-first, e.g.
   `["tmp_impl/workflow.yaml","plan-orchestration"]`). Pydantic auto-parses JSON.
   Also thread through **`go_next`** + **`build_state`** (`features/build_state/
   build_state.py`): `new_chain = parent_chain if not None else base_chain`.
2. **`resolve_workflow.py`**:
   - `_sub_workflow_entry_marker`: set `"parent_workflow"` = the reloadable parent
     **reference** (pass the ref string in, not `parent_workflow.name`); add
     `"parent_chain"` = `[parent_ref, *prev_chain]`.
   - `_return_to_parent_marker` / `_return_to_parent_on_terminal`: after popping
     the nearest parent (index 0 of chain), if the resolved parent target step is
     **itself** a sub-workflow ref (`_resolve_sub_workflow_ref_target` non-None),
     chain into it (recursive entry) — otherwise return marker. Keep
     `PARENT_WORKFLOW`/`PARENT_WORKFLOW_STEP` for the *immediate* parent.
   - `resolve_next_from_workflow`: accept `parent_chain` param; thread through both
     terminal-return and mid-step-return paths.
3. **`resolve_route.py`**: at all three `resolve_next_from_workflow(...)` call
   sites pass `parent_chain=prev_state.parent_chain` / dispatch-derived;
   `_apply_wf_marker`: on `entered_sub_workflow` set `config_parent_chain` from the
   marker; on `exited_sub_workflow` clear it (mirror existing parent handling).
   Add `config["config_parent_chain"]` to the config dicts.
4. **`build_result.py`**: emit `PARENT_CHAIN` (compact JSON array string, e.g.
   `["a/b.yaml","c"]` or empty) so GHA can export it as env.
5. **GHA** (`openhands-multi-agent.yml`): parse `PARENT_CHAIN` from RESOLVE_OUTPUT,
   `echo "PARENT_CHAIN=$PARENT_CHAIN" >> $GITHUB_ENV` (next to PARENT_WORKFLOW).
   **`build_output.py`**: read `PARENT_CHAIN` env (JSON) → pass `parent_chain`
   into `build_state` when posting state (so the chain persists across resumes).
   Single-level flows keep working: empty/absent `parent_chain` ⇒ old behavior.

### STATUS CHECKPOINT (run of 2026-08-31, HEAD 799c371, tests 181 green)

**DONE (do NOT redo):** parent_chain threading (Bugs B/C/D) — `b947006` — and
PARENT_CHAIN end-to-end wiring build_result → GHA env → build_output state —
`799c371`. Routing/state suites pass (181). Branch rebased clean on origin/main.

**CONFIRMED NEW ROOT CAUSE (the "No valid task found" stall), distinct from B/C/D:**
- Marker path: `resolve_route` dispatch → `resolve_next_from_workflow` →
  `_sub_workflow_entry_marker` reads `child_workflow.steps[0].agent`. For a
  scaffold-generated `tmp_impl/workflow.yaml`, `steps[0]` (step_1) is itself a
  `workflow: dev-review-loop` ref **with no agent** → `task=None` →
  `build_result.py` bails with `{"SKIP_AGENT": "true", "REASON": "No valid task found"}`
  BEFORE the env path (`resolve_workflow_routing`) ever runs.
- The env path `_resolve_sub_workflow_entry` ALREADY descends correctly: given
  `(task, workflow="tmp_impl/workflow.yaml", step="step_1")` it re-derives
  `ROLE_NAME=dev, WORKFLOW_NAME=dev-review-loop, WORKFLOW_STEP=implement` and builds
  `PARENT_CHAIN=["tmp_impl/workflow.yaml","plan-orchestration"]`. Exit-side bubbling
  and chain popping also already work. So the ONLY blocker is the entry marker.

**FIX (minimal, single file: `.github/scripts/resolve_workflow.py`):**
1. Add helper `_effective_entry_step(child_wf, workflows_dir, max_depth=5)`:
   walk `steps[0]`; while it has `workflow`/`path` (no `repo`/agent leaf), load the
   child via `load_workflow`/`resolve_workflow_reference` and repeat; return the first
   step that has a real agent (or the deepest step reached on error/depth cap).
2. In `_sub_workflow_entry_marker`: set `"task"` to
   `_effective_entry_step(...).agent` (fall back to current behavior if None).
   **Keep `step`/`workflow` at the immediate child** (e.g. `"step_1"` /
   `"tmp_impl/workflow.yaml"`) — the env path re-descends from there and needs
   `step_name` to match a step in the child file. Do NOT point them at the leaf.
   Single-level case is unchanged (descent returns `steps[0]` immediately).
3. Regression tests in `test_resolve_workflow_step.py`: 3-tier fixture
   (plan-orchestration-like parent → `tmp_impl/workflow.yaml` whose steps are
   `workflow: dev-review-loop` refs → dev-review-loop). Assert R1 entry marker:
   `task="dev"` (non-None), `step="step_1"`, `workflow="tmp_impl/workflow.yaml"`,
   `parent_chain=["tmp_impl/workflow.yaml","plan-orchestration"]`-shape; then inner
   linear step; then terminal exit bubbling TWO levels to grandparent; single-level
   entry assertion unchanged.
4. NOTE: prior run's `/tmp/smoke_parent_chain.py` assertions were wrong (expected
   R1.WORKFLOW_STEP=="step_1"; the env path correctly yields "implement"). Discard it;
   add correct tests to the real suite instead.

**THEN:** full scripts suite green → E2E docs (`docs/default-workflow.md`,
permanent `tmp_impl/` note replacing this temp section) → push scoped commits to
PR #494 (OPEN; branch `feature/issue-470-plan-orchestration-integration`).

