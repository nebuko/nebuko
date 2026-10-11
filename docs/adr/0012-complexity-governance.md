# ADR-0012: Complexity Governance — Layered Policy and the `ComplexityGovernor`

**Status:** Accepted — 2026-10-11
**Decider:** olivier-norture
**Related:** extends [ADR-0005: Per-Step Complexity Selector](./0005-per-step-complexity.md) · issue #869 · #787 (base tier chain) · #659/#662 (`AgentComplexityOverride`) · #767/#772 (runtime tier application)

## Context

ADR-0005 introduced the per-step tier *selector* with the fallback chain
`dispatch > step > "medium"`, and the resolved tier is exported as
`CONFIG_COMPLEXITY`. That answers *which* tier a step runs at, but not *whether
that tier was right*. In practice, complexity drifted toward `HIGH` — an
unproven `HIGH` erodes both the cost budget and the signalling value of the
tier. Repos need to (a) retune the rubric per repo and (b) adjudicate evidence
about a step that was mis-tiered, instead of every agent making an ad-hoc call.

Two constraints shaped the design:

- **Human ground truth matters.** The strongest signal that a step was
  under-tiered is repeated human change-requests / feedback rounds — not an LLM
  self-assessment. LLM metrics and human/interaction metrics must be judged
  together.
- **Rules must be data, not code.** Repo policy evolves (a code repo targets
  35/50/15 low/medium/high; a docs repo targets 70/25/5) and running repo code
  as governance logic is an audit/security hazard.

## Decision

Introduce a **Complexity Management Subsystem**:

1. **Layered `ComplexityPolicy` (`builtin` -> `repo`).** POJO/pydantic layers
   (`ComplexityPolicyLayer`) merge at the same config site that already carries
   `complexity_overrides`. The resolved `ComplexityPolicy` (bands, `SignalRule`s,
   `target_distribution`, `auto_adjust`, `max_auto_delta`) is pinned into run
   state for auditability.
2. **Typed `EvidenceBundle`** mixing three families — `llm` (open vocabulary =
   `metrics_schema` fields), `human` (`HumanSignals`), `context`
   (`ContextSignals`). The `context` family (issue linkage / reopen counts) is
   **wired but not yet published**: it is read from an optional
   `COMPLEXITY_CONTEXT_SIGNALS` blob, and until an intake call-site writes that
   var the `multi-issue-linkage` / `reopened-issue` rules stay dormant. The
   `llm` and `human` families are live today.
3. **Declarative `SignalRule`** thresholds (`multi-issue-linkage`,
   `reopened-issue`, `high-token-burn`, `large-scope`, `human-friction`) that a
   repo layer can retune — never executable plugins.
4. **One adjudicator: `ComplexityGovernor`** (stateless). It turns evidence and
   agent review requests into a clamped tier adjustment:
   `effective = clamp(base + delta)`, with `|delta| <= max_auto_delta`
   (default 1). Decisions are recorded as immutable
   `ComplexityAdjustment` records on `HistoryEntry`.

The base tier chain from ADR-0005 is **unchanged**; the governor is an input
overlay on top of it and a feedback actuator for the next boundary.

## Consequences

- **Per-repo calibration.** A repo retunes `target_distribution` and
  `signal_rules` in config; the retrospective emits a
  `ComplexityDistributionReport` comparing effective-tier counts to target.
- **LLM + human adjudication.** A LOW step that burned `HIGH` tokens *and* drew
  two change-requests shifts up at the next boundary, with the evidence snapshot
  recorded on the advancing history entry.
- **Bounded blast radius.** `max_auto_delta` (default 1) plus `[low, high]`
  clamping means the governor can never jump a tier by more than one, and
  `auto_adjust: false` disables drift entirely. The drift check is evaluated
  against the entry's recorded **base** tier (`effective - adjustment`), so
  persistent evidence yields the same clamped correction at each boundary — it
  neither ratchets a tier per boundary nor oscillates — and decays back once the
  evidence stops.
- **Context signals await a publisher.** The `ContextSignals` family
  (`multi-issue-linkage`, `reopened-issue`) is read from
  `COMPLEXITY_CONTEXT_SIGNALS`; no call-site publishes that var yet, so those
  rules do not fire in production until an intake publisher lands. LLM and human
  evidence are live today.
- **Agent review is advisory.** The `complexity_review` tool requires a populated
  `evidence` bundle and a non-blank `feeling`; `suggested_tier` is input, not a
  command — the governor clamps it against policy.
- **Pure and replayable.** `ComplexityGovernor` holds no state; policy and
  evidence pass per call, so every decision is unit-testable and reproducible
  from the persisted bundle.
