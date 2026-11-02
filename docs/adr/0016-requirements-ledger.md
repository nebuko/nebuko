# ADR-0016: The Requirements Ledger and Plan Coverage Table

**Status:** Accepted — 2026-11-02
**Decider:** olivier-norture
**Related:** #1257 (this change), [ADR-0012](./0012-thread-ledger-context.md) (thread ledger), DeepHalluBench (arXiv:2601.22984)

## Context

No step between `analyze` and `plan` records *what the plan must satisfy*. Constraints stated once in the issue ("must fit the free tier", "no new service", "keep the CLI flags stable") are easy to lose by planning time: the human gate has to rebuild the requirements in their head to judge a plan, and the spec, review and critique steps have nothing objective to check coverage against. DeepHalluBench evaluated deep-research agents step by step and found that planning-stage errors came mostly from **incomplete coverage of the user's constraints**, not from wrong actions.

## Decision

1. **The ledger travels as a markdown table inside an existing section-marker output field.** The `analyze` step gains a `requirements` output field (`collapsed: true, required: true`); the `plan` step gains a `coverage` field. No schema migration, no new structured store — the same `output.fields` machinery every other field uses.

   Ledger row grammar: `R<n>` requirement, `K<n>` constraint, `NG<n>` non-goal, `Q<n>` quality goal. Each row is one atomic, checkable statement with a source (short quote or comment link) and an origin (`explicit` or `inferred`). Inferred entries are highlighted for the human gate, not hard-gated; ambiguities become `## Questions` with the default the plan proceeds with.

2. **Only missing R/K rows are engine-refused.** The engine parses the IDs from the `requirements` field of the analyze output and the `coverage` field of the plan output. When a plan `finalize` leaves an enforced (`R`/`K`) ID uncovered, the finalize is refused in the same turn with `coverage missing: R3, K1` (sorted, comma-joined) — the same pattern as refusing a `finalize` that has no report. NG and Q rows are prompt/critique-checked, never engine-refused; Q entries are optional and used only to rank the options in `## Approaches considered`.

3. **Fail-open.** If the plan step declares no `coverage` field, or no parseable `requirements` section exists in run state, the check is a no-op: default and legacy workflows keep working. An unparseable ledger logs a warning; it is not a refusal.

4. **Metrics.** The run records the ledger size, the number of `inferred` entries (the human gate corrects them by editing the ledger), and the number of coverage refusals per run.

**Deferral syntax (per-ID).** A coverage row defers exactly its own ledger ID with an in-cell `deferred: <reason>` marker in the `Covered by` cell, e.g. `| R3 | deferred: lives in a future lint rule | — |`. Deferral never exempts IDs absent from the table: each enforced ID must be either covered or explicitly deferred. A future lint rule over workflow configs is deferred (out of scope here); no new dependencies.

## Consequences

- Every R/K in an issue has at least one named plan decision and one named test or check, or an explicit `deferred` reason.
- The human gate reviews and corrects the ledger — the cheapest place to catch a misunderstanding, before any design or code exists.
- Downstream, spec red tests name the ledger IDs they lock (`Covers: R2, K1`), critique checks ledger coverage first, and review checks that each R/K has a green test and no NG was violated.
