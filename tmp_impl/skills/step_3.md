# Step 3: Document pinned-context policy and update prompts/CI

## Title

Step 3: Document pinned-context policy and update prompts/CI

## Instructions

Update `docs/architecture.md` (pinning section, ~L95-110) and `docs/reference.md` (`WORKFLOW_STATE` v1 schema, ~L155-205) to state the new retention contract explicitly:

- only comments whose ids are in `pinned_comment_ids`, or that belong to a `pin=True` history entry, are injected **verbatim**;
- all other included comments are injected as **summaries** (or short excerpts).

Update `.agents/prompts/po.md` (L48-50) and `.agents/prompts/genius.md` (L39) so the pinning instructions match the implemented mechanism (dispatch `pin: true` / `PIN_COMMENT` env), removing any stale references.

Verify the workflow YAML env plumbing from Step 2 is documented.

Do **not** change behaviour in this step.

Add one integration-style test (if the harness exists) asserting that a thread with >30 comments yields a prompt containing at most the pinned bodies plus summaries.

## Expected outcome

- `docs/architecture.md` §Pinning and `docs/reference.md` describe the verbatim-vs-summary retention contract and the `pin`/`PIN_COMMENT` mechanism.
- `.agents/prompts/po.md` and `.agents/prompts/genius.md` instruct pinning via the implemented dispatch `pin` flag, with no stale references.
- The workflow YAML env plumbing is documented.
- If a suitable integration harness exists, an integration-style test covers the >30-comment case; the broader test suite remains green.

## Relevant file context

- `docs/architecture.md` — pinning semantics (~L95-110): "`pinned_comment_ids` marks comments whose full content should be re-fetched at runtime for high-fidelity context".
- `docs/reference.md` — `WORKFLOW_STATE` v1 schema table: `HistoryEntry.inject_context`, `HistoryEntry.pin`, `WorkflowState.pinned_comment_ids` (~L155-205).
- `.agents/prompts/po.md` — the only current `pin: true` emitter (L48-50).
- `.agents/prompts/genius.md` — a `pin=true` consumer reference (L39).
- `.github/scripts/query_context.py` — implemented read-side policy from Step 1 (reference for exact wording).
- `.github/scripts/workflow_state/sub_domains/dispatch/dispatch.py` — `Dispatch.pin` from Step 2.

## Notes

- Documentation-only plus prompt updates; behaviour must not change. Do not modify Step 1/Step 2 code except to fix documentation drift.
