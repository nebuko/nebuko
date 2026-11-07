---
title: "Built-in workflows"
audience: user
covers:
  - defaults/workflows/*.yaml
sidebar_group: "Guides"
sidebar_label: "Built-in workflows"
sidebar_order: 17
---

# Built-in workflows

| Workflow | File | One-line description |
|----------|------|---------------------|
| `default` | [`default.yaml`](../defaults/workflows/default.yaml) | Standard dev chain: analyze → context → plan → implement → review |
| `think` | [`think.yaml`](../defaults/workflows/think.yaml) | Thinking only — analyze + context + plan, no implementation |
| `review-only` | [`review-only.yaml`](../defaults/workflows/review-only.yaml) | Standalone single-shot code review |
| `dev-review-loop` | [`dev-review-loop.yaml`](../defaults/workflows/dev-review-loop.yaml) | Reusable sub-workflow: one plan step with review, loops on rejection |
| `plan-orchestration` | [`plan-orchestration.yaml`](../defaults/workflows/plan-orchestration.yaml) | Full multi-step plan lifecycle sharing one PR |
| `spec-driven` | [`spec-driven.yaml`](../defaults/workflows/spec-driven.yaml) | Spec → stub (human-gated) → implement |
| `spec-driven-impl` | [`spec-driven-impl.yaml`](../defaults/workflows/spec-driven-impl.yaml) | Plan → stub (human-gated) → implement |
| `demo-composition` | [`demo-composition.yaml`](../defaults/workflows/demo-composition.yaml) | Demo of sub-workflow composition |

## default

```
analyze (architect) → context (context) → plan (architect) ──approved──► implement (dev) → review (review) ◆
                                                    │rejected
                                                    └────────► context (loop)
```

Trigger: `@@architect`. The standard development cycle used when a human hands a
problem to the system. The `plan` step can loop back to `context` when the
plan is rejected (insufficient context).

The `plan` step is a single `human_gate:plan`: @@architect emits **one** planning
artifact — visible `Context (root cause)`, `Decision` (design + Object Design
Anchor), an `Approaches considered` table, and the `Object Design Contract`, with
the `Implementation Plan` collapsed in a `<details>` block — and ends with
`advance`. The engine intercepts that advance into the gate hold, so
the human runs exactly **one** `/nebuko approve gate` before @@dev is routed.

Example trigger:

```
@@architect implement retry logic in the circuit breaker
```

## think

```
analyze (architect) → context (context) → plan (architect) ◆
                                └─ rejected ─► context (loop)
```

Trigger: `@@architect`. Pure analysis — ends after the plan/answer is produced;
nothing is implemented. Use for "help me understand / define X" requests.

## review-only

```
review (review) ◆
```

Trigger: `@@review`. Standalone review of code without a prior implementation
step in the same workflow — used for ad-hoc review requests and nightly review
(issue #161).

## dev-review-loop

```
implement (dev) → review (review) ──approved──► ◆
                              └─rejected─► implement (loop)
```

Trigger: `@@dev`. A **reusable sub-workflow**: it executes a *single* plan
step with review and loops until the reviewer approves. It is not typically
started directly — it is referenced per-step from a dynamically generated
`tmp_impl/workflow.yaml` (see `plan-orchestration`).

## plan-orchestration

```
architect (architect) → context (context) → plan (architect) ──approved──► scaffold (dev, prompt_replace)
                                                   │rejected                │
                                                   └────► plan (loop)       ▼
                                                            sub_impl (workflow: tmp_impl/workflow.yaml)
                                                                         │
                                                                         ▼
                                                       sub_clean (dev, skill: plan-cleanup)
                                                                         │
                                                    ┌────────────────────▼────────────────────┐
                                                    ▼                                         │
                                             final_review (review) ──rejected──► final_fix (dev, skill: final-fix)
                                                                    │approved          │
                                                                    └►◆◄────────────────┘
```

Trigger: `@@architect`. The full lifecycle for a **multi-step plan that shares a
single PR** (issue #462):

1. `architect` / `context` / `plan` — architect produces a structured plan; a
   human approves it (reject → re-plan loop).
2. `scaffold` — dev converts the approved `<planWorkflow>` JSON into
   `tmp_impl/workflow.yaml`, a per-step sub-workflow.
   The step sets `prompt_replace: true`, so the `dev.md` role prompt and its
   generic Implement flow are suppressed and the step scope is exactly the
   scaffold instruction.
3. `scaffold_review` (review) verifies `tmp_impl/workflow.yaml`
   faithfully materializes the approved `<planWorkflow>` block (same steps,
   same order; nothing implemented outside `tmp_impl/`); a mismatch loops back
   to `scaffold`.
4. `sub_impl` — **mounts** the generated sub-workflow as one step. Each plan
   step runs through `dev-review-loop` (implement → review, loop on
   rejection).
5. `sub_clean` — dev removes the `tmp_impl/` scaffolding (skill:
   `plan-cleanup`).
6. `final_review` ↔ `final_fix` — one whole-PR review; rejections are
   addressed across all plan steps (skill: `final-fix`) and re-reviewed.

This is the main demonstration of **sub-workflow composition** — a step
declaring `workflow:` instead of `agent:`, with a runtime-generated path.

**Entering mid-lifecycle (issue #789).** When a plan already exists on the
issue (for example from an earlier deep-thinking run), you can enter this
workflow without replaying `architect`:

```
/nebuko run workflow --name plan-orchestration --step plan
```

Use `--step plan` to have architect convert/re-validate the existing plan and
re-arm the human gate; `--step scaffold` converts it directly (bypassing the
gate — `run` is human-only, so that is an explicit human choice). `--step
sub_impl` requires `tmp_impl/workflow.yaml` to be scaffolded already, else the
entry fails loudly rather than starting from the first step.

**Restarting at a nested plan step (issue #822).** A dotted selector descends
the mount chain, so you can re-enter the scaffolded plan at a specific item
without replaying the ones already done. For issue #648 — where plan `step_1`
and `step_2` are complete — restart at `step_3`:

```
/nebuko run workflow --name plan-orchestration --step sub_impl.step_3
```

This seeds a fresh run at `plan-orchestration/sub_impl → tmp-impl/step_3 →
dev-review-loop/implement`, so it runs `step_3…N` then `sub_clean` →
`final_review`; `step_1` and `step_2` are never executed. Add one more segment
to re-enter at a leaf inside the per-step loop — e.g. re-run only `step_3`'s
review once its implementation is done:

```
/nebuko run workflow --name plan-orchestration --step sub_impl.step_3.review
```

Each segment must match the scaffolded step name verbatim (`step_3`, not
`step3`). An unknown segment, a malformed path, or a mount whose
`tmp_impl/workflow.yaml` has not been scaffolded fails loudly rather than
silently starting from the first step.

## spec-driven

```
spec (po) ──rejected──► spec
     │approved
     ▼
architect_prepare_context (architect) → context_architect (context) → architect_review_context (architect)
                                                        │rejected
                                                        └► context_architect (loop)
     │approved/default
     ▼
stub (dev, skill: stub-only) ◄──rejected── stub_review (review)
     │                                       │approved/default
     ▼                                       ▼
                          stub_approval (architect, human_gate) ◄──rejected
     │approved/default                            │
     ▼                                            ▼
implement (dev) ──rejected── review (review) → ◆
                ▲______________________________│
```

Trigger: `@@po`. Two **human gates**: the spec must be approved before any
architecture work, and the stubs must be approved before real implementation.
The `stub` step is constrained by the `stub-only` skill (stubs/tests only, no
logic). Review rejects send the dev back to fixing stubs or implementation.

## spec-driven-impl

```
analyze (architect) → context (context) → plan (architect) ──approved──► stub (dev, stub-only)
                                                   │rejected          │
                                                   └► context         ▼
                                                            stub_review (review)
                                                                     │
                                                     stub_approval (architect, human_gate)
                                                                     │approved
                                                                     ▼
                                                          implement (dev) ◆
```

Trigger: `@@architect`. Same gate structure as `spec-driven`, but starts from the
standard analyze/context/plan chain instead of a PO spec. The stub review +
human approval is the quality gate before implementation.

## demo-composition

```
analyze (architect) → subtask (workflow: review-only) → finalize (architect) ◆
```

Trigger: `@@architect`. Minimal example of **sub-workflow composition**: the
`subtask` step mounts the entire `review-only` workflow as one step of the
parent.
