# ADR-0006: Nested Workflow Position Is Carried Forward, Not Rebuilt

**Status:** Accepted — 2026-10-08
**Decider:** olivier-norture
**Related:** #812 (this fix), #648 (the run that exposed it), #638 (flat `WorkflowPosition`), #618/#646 (position emission), #657/#675 (state-routing / unwind tests)

## Context

A run's **position** is its complete nested call-stack of workflow steps —
`plan-orchestration/sub_impl → tmp-impl/step_1 → dev-review-loop/implement` —
persisted as `WorkflowState.position`, a flat, outermost-first list of
`StepRef`. It is the single address the engine uses to know *where the run
is*: to resume it, to route a hand-off, and — when an inner workflow reaches
its terminal step — to unwind **one level at a time** back into the enclosing
mount.

During issue #648, the `plan-orchestration` workflow (a 10-step plan mounted
as a branch-generated sub-workflow) executed **step 1 of 10** and then jumped
straight to `sub_clean`, skipping `tmp-impl/step_2..step_10`. The captured
state showed the position faithful at `dev-review-loop/implement` (3 frames)
but truncated to a bare `dev-review-loop/review` at the very next
intra-workflow move:

```
dev-review-loop/implement  pos = [plan-orchestration/sub_impl, tmp-impl/step_1, dev-review-loop/implement]  ✅
dev-review-loop/review     pos = [dev-review-loop/review]                                                    ❌
```

Two compounding defects caused the collapse:

1. **The stack was rebuilt from a lossy source on every transition.**
   `build_result._attach_position` reconstructed the position from the flat
   `(parent_workflow, parent_workflow_step)` pair instead of carrying the
   previous run's stack forward. On a *state-based* transition (no dispatch
   object — exactly `implement → review`) the pair was seeded only from the
   dispatch, so the emitted `PARENT_WORKFLOW` was empty and reconstruction
   fell back to the bare single ref. A single flat pair can encode exactly one
   parent link; the run was three levels deep.
2. **The terminal unwind could not re-enter the branch-mounted level.** With
   a faithful stack, the exit recovers the ancestor chain — but it fetched the
   level by its *declared name* (`tmp-impl`), while the branch mount is only
   discoverable under the reference its mount step declares
   (`tmp_impl/workflow.yaml`; the file's `name:` is `tmp-impl`). The lookup
   missed, the level was dropped, and the unwind fell through to the outer
   workflow's next step — `sub_clean` — collapsing two levels at once.

## Decision

**Make the previous run's `WorkflowPosition` the authoritative base and carry
it forward: every transition mutates the stack by exactly one of three
primitives, and the stack is never rebuilt from the flat pair while a base
exists.**

- Entry into a mounted sub-workflow — `with_appended(ref)`: push a leaf frame.
- Intra-workflow step move — `with_step(ref)` *(new, issue #812)*: replace the
  leaf frame; every enclosing frame is carried forward unchanged.
- Return to the enclosing parent — `parent()`: pop the leaf frame.

A mount boundary whose freshly resolved tail is longer than the flat pair
records is handled by **splicing** the rebuilt tail beneath the base frames
that still enclose it (`_carry_forward_position` + `_deepest_workflow_index`),
so the branch-mounted intermediate level survives even though the pair only
names the outermost one.

The branch-mounted ancestor is located by its **declared mount reference**, not
its declared name: `WorkflowLoadContext.name_refs` carries name → repo-relative
reference (derived by `_enclosing_mount_refs` from the enclosing parent step),
and the fetch is keyed on that reference.

The flat `(parent_workflow, parent_workflow_step)` pair is retained as a
**derived view** (the second-to-last frame) for backward compatibility and for
the legacy/absent-base fallback, where `build_workflow_position` still
rebuilds.

## Alternatives considered

1. **Patch only the empty-string seeding** — make the config builders fall
   back to `prev_state.parent_workflow` and write the resolved parent pair
   back. *Rejected:* a flat pair cannot represent 3+ levels deterministically;
   reconstruction would depend on history archaeology, so it fixes this
   symptom but not the class of bug.
2. **Reconstruct the chain from `history[].position` on every transition** —
   generalize `_parent_chain_from_history` into the emission path.
   *Rejected:* O(n) archaeology per transition, and silent failure when history
   is sampled or truncated — it re-introduces "derive live state from
   fragments", the exact fragility being removed.
3. **Authoritative carry-forward stack (chosen)** — the full stack already
   round-trips in state; make it the single source of truth. No schema change,
   fixes both defects at once, scales to arbitrary nest depth.

## Consequences

- **Correct nested unwind.** A terminal inner workflow returns to its
  *immediate* parent step (`tmp-impl/step_2`), not to the outer workflow — the
  invariant the `plan-orchestration` lifecycle depends on. Pinned by
  `test_position_emission.py::TestCarryForwardEmission` and
  `test_transition_scaffold_unwind.py::TestBranchMountUnwindByDeclaredRef`
  (both fail on pre-fix code, pass with the fix).
- **State schema unchanged.** `WorkflowState.position` and
  `HistoryEntry.position` already persisted and round-trip; the flat pair
  becomes a derived view. Top-level (single-ref) runs are unaffected.
- **One owner, three transitions.** The `WorkflowPosition` value object owns
  its own mutations; the engine glue (`_attach_position`, the config builders)
  is a thin dispatcher. No "position manager" god-object.
- **Trust boundary unchanged.** The position is engine-derived state — agents
  do not author it — so carry-forward cannot be steered from the comment
  stream.
- **Deferred, not precluded.** A fully history-independent unwind (retiring
  the flat-pair fallback once legacy states age out) can follow later; it
  would warrant its own issue.
