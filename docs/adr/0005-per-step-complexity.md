# ADR-0005: Per-Step Complexity Selector (Not a Generic Agent-Config Override)

**Status:** Accepted — 2026-10-08
**Decider:** olivier-norture
**Related:** #787 (this change), #659/#662 (`AgentComplexityOverride`), #767/#772 (runtime tier application)

## Context

Every workflow step ran at `complexity: "medium"` unless the agent explicitly
passed a `complexity` in its `dispatch_next`. The **first** step of a workflow
therefore never had a dispatch to consult and always burned a `medium`-tier
invocation — even when it only triages the request and drafts a context query.

Workflow authors needed to declare *"this step is cheap / this step is heavy"*
declaratively in the workflow YAML.

Agent configuration already had a per-tier mechanism:
`agents.<name>.complexity_overrides.<low|medium|high>` (added in #662, applied
at runtime in #772) can swap model, temperature, reasoning, and sampling
parameters per tier. What was missing was a way to *select* a tier from the
workflow definition. The mechanism was also dormant: `resolve_llm_config`
consumed `CONFIG_COMPLEXITY`, but nothing wrote it after the legacy bash
wrapper was removed.

Two designs were considered for the step-level knob:

1. **A single `complexity: low|medium|high` selector** that picks a tier —
   the tier's behavior stays in `complexity_overrides`.
2. **A generic `agent_config_override` object** on the step, able to replace
   `temperature`, `tools`, `skills`, `prompt`, and `model` directly.

## Decision

**Adopt option 1: a single optional `complexity` field on `WorkflowStep`,
resolved with the fallback chain `dispatch > step > "medium"`.**

- The workflow step **selects** the tier; `agent-config.yaml` **defines** what
  the tier means. Workflow structure and agent configuration stay cleanly
  separated.
- The resolved tier is exported as `CONFIG_COMPLEXITY` and drives the existing
  tier machinery — this also re-activates `complexity_overrides`, which had no
  writer in `src/`.
- An explicit dispatch `complexity` (the agent's runtime choice) overrides the
  step's declaration; an absent dispatch value counts as *unset*, not as an
  explicit `medium`, so it can never mask the step's tier.

The generic `agent_config_override` (option 2) is **rejected**.

## Consequences

- **Minimal, typed surface.** One `Literal["low", "medium", "high"]` field,
  validated by pydantic — invalid values are rejected at parse time.
- **No second override surface.** A free-form per-step override would overlap
  `complexity_overrides` and invite per-step model/parameter pinning sprawl
  that is hard to reason about and to lint. The tier mechanism already covers
  model swapping per tier.
- **Deterministic cost control.** A workflow author can lower the cost of the
  first (dispatch-less) step by declaring `complexity: low`; per-tier behavior
  is centralized in agent config, so one edit changes every step using that
  tier.
- **Dormant machinery re-activated.** Emitting `CONFIG_COMPLEXITY` restores
  the runtime tier application for all roles, not just `dev`/`ctx`.
- **Deferred, not precluded.** If per-step model/parameter pinning is ever
  needed, it can be added later as a strictly higher-precedence layer without
  breaking this design. It would warrant its own issue and ADR.
