# Publication guard

## How to use

The publish phase of an agent run is best-effort: it never raises on failure.
If publication cannot complete, the phase records the outcome in the workflow
state and the run finishes with `ExitCode.ERROR` instead of propagating an
exception.

## How it works

- `run_agent` opens the run, then delegates to the publish phase.
- The publish phase resolves the target through `_resolve_phase_callable`
  (override registry first, default callable second).
- `PublicationState` carries the result: `published=True` on success, or
  `published=False` with `reasons` naming the blocking condition.
- Bounded recall: publication is retried at most `MAX_PUBLICATION_RECALLS`
  times before the guard checkpoints the run.

## Deviations from the Object Design Contract

`PhaseOverride` is **not materialized** as a runtime class. Grep confirms no
`class PhaseOverride` in `src/`; the override registry is a module-level dict,
which is sufficient for the current single-engine topology. If a future plan
lists `PhaseOverride` in its Object Design Contract, this doc must be updated
in the same change.
