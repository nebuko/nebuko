# ADR-0012: The Workflow-State Thread Ledger Is the Source of Truth for Agent Context

**Status:** Accepted — 2026-10-11
**Decider:** olivier-norture
**Related:** #872 (this change), #844 (raw-thread guard, relaxed here), #813 (typed step inputs, persisted here), [ADR-0003](./0003-context-management.md) (context management), [ADR-0005](./0005-github-api-budget.md) (comment API budget), #648 / #671 (state hand-off and recovery)

## Context

An agent's context was assembled from two independent mechanisms:

1. **History window (default):** the last `HISTORY_COMMENT_COUNT` (default 5)
   comments of the thread, verbatim. This is a *recency* slice, not a
   *relevance* slice — engine bookkeeping (agent-started notices, human-action
   notices, runner errors) consumes window slots, and a comment older than the
   tail is dropped even when it is the meaningful human feedback in the thread.
2. **Workflow inputs/outputs:** the workflow's deterministic wiring. Resolved
   step inputs were materialized and used for the run but **not persisted**, so
   the deterministic I/O contract of a step was invisible in workflow state.

The issue asks for `workflow_state` to be the single source of truth: classify
*every* artifact on the issue, keep the relevant flow, and record the step's
resolved inputs where the rest of the run's provenance already lives.

GitHub threads mix several artifact kinds, and only some are meaningful as
context: the issue body (what was asked), `/nebuko` commands, engine output
(agent started / human notification / runner error), agent output, and human
feedback. Engine noise should not reach the prompt; the meaningful flow should,
in full, rather than a fixed tail.

Two hazards are in scope for the security posture:

1. **Edited comments.** A comment can be edited between runs, changing the
   text the agent has already seen or will see.
2. **Prompt injection via a new human comment.** An arbitrary account can post
   instructions between two agent runs.

## Decision

1. **A typed thread ledger lives in workflow state.** `WorkflowState.thread`
   is a list of `ThreadRecord` — one immutable, metadata-only record per issue
   artifact: `kind` (`issue-body | command | engine-output | agent-output |
   human-feedback`), the GitHub REST `id`, `url` (`html_url`), `user_id`,
   `user_login`, `user_name`, `created_at`, `author_association`, `trusted`,
   `deleted`. **Bodies are not copied into state** — the thread remains the
   single source of truth for text; state holds provenance and classification
   only, so the full-snapshot state written every step does not balloon.

2. **Reconciliation runs between runs over the full comment list.** A pure
   `reconcile_thread` folds the complete comment list (plus the issue body
   first) into the ledger: append missing records in API order, dedup by `id`,
   recompute `trusted`, and keep vanished ids with `deleted=True` (frozen for
   audit, never rendered). Capturing everything guarantees nothing is silently
   dropped; *classification* decides what is meaningful.

3. **The prompt renders the relevant flow, not a raw tail.** The `## Recent
   thread` section is rendered from the relevant records (commands, agent
   output, and trusted human feedback) in chronological order, with engine
   output and the issue body excluded. Untrusted human comments are surfaced
   separately as one-line, explicitly-unverified summaries so they inform the
   agent without reading as instructions.

4. **Full relevant flow by default; a cap is an explicit opt-down.**
   `HISTORY_COMMENT_COUNT` changes meaning for the ledger: unset or
   non-positive → the full relevant flow; a positive N → the last N relevant
   records (pinned comments always added on top). This deliberately relaxes the
   issue #844 fail-safe ("never inject the whole *raw* thread") into "inject the
   whole *relevant* flow", which is the issue's stated goal; the guard survives
   as the opt-down knob and the loop guard's raw-history fallback keeps a
   positive default. Pre-migration states (no ledger) fall back to the legacy
   tail slice, so behaviour is never worse than before.

5. **Resolved step inputs are persisted.** Each materialization is recorded as
   a `StepInputRecord` (`name`, `binding`, `ok`, `detail`, `value` capped at
   `MAX_VALUE_CHARS`, `truncated`) on the advancing `HistoryEntry`, so the
   deterministic I/O contract is auditable in the same state snapshot.

6. **Prompt-injection posture.** Human feedback is `trusted` only when its
   `author_association ∈ {OWNER, MEMBER, COLLABORATOR}` **and** its author
   matches the run's trigger author (by `user_id`, falling back to
   `user_login`); the rule fails closed when neither is known. Untrusted human
   comments are recorded but never rendered as trusted context — they appear
   only in the flagged "unverified" appendix.

## Edited comments — explicitly out of scope

**We do not handle edited comments in this feature.** It would over-complexify
the design, and it is viable to ignore today only because of the PAT auth path;
a dedicated GitHub App would make bot-comment edits near-impossible anyway. The
residual risk is a human editing an old human comment between runs, which we
accept. `updated_at` is present on both the issue and comment objects (and
`updated_at != created_at` is a feasible future edit-detector), but the list
endpoint exposes no delta/`changes` field, so any reconciliation of edits is
deferred.

## Alternatives considered and rejected

1. **Overload `HistoryEntry` with comment provenance.** Rejected: a
   `HistoryEntry` is a per-step record with required `step`/`agent` fields that
   do not describe a comment; blurring step records and thread records corrupts
   both. `HistoryEntry` instead gains a nested `inputs` list.
2. **Snapshot full comment bodies into workflow state.** Rejected: the state is
   re-written into a comment every step; duplicating every body would balloon it
   and drift from GitHub.
3. **A GraphQL node-id event log.** Rejected as over-engineering: the REST
   `id` + `html_url` are sufficient and the existing `gh api` transport already
   returns them.

## Consequences

- **Relevance replaces recency.** Engine noise no longer consumes the context
  window; the meaningful flow is available in full by default.
- **State grows by metadata, not text.** The ledger scales with the number of
  artifacts (about ten small fields each), not with their size.
- **Deletion is a soft signal.** A deleted comment is frozen with
  `deleted=True` rather than erased, preserving the audit trail; the renderer
  skips it.
- **Injection surface is bounded.** Only the run's initiator (with a trusted
  association) contributes trusted human text; every other human comment is
  quarantined in the unverified appendix.
- **Edits remain an accepted residual risk** under the current PAT auth path
  (see above).
- **Schema generation 5.** `STATE_SCHEMA_VERSION` moves 4 → 5; older snapshots
  deserialize with `thread=[]` and `history[].inputs=[]`, so no backfill is
  required.
