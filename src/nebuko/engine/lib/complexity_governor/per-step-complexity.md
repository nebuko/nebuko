---
title: "Per-step complexity"
audience: user
sidebar_group: "Guides"
sidebar_label: "Per-step complexity"
sidebar_order: 12
---

# Per-step complexity

A step can declare the complexity tier for its own agent invocation with
`complexity: low|medium|high`. This lets a workflow author cheaply price each
step instead of paying the default `medium` on every one — for example a
triage/`analyze` step that only drafts a context query can run `low`, while an
architecture `plan` step can run `high`:

```yaml
steps:
  - name: analyze
    agent: architect
    complexity: low      # triage + context-query drafting is cheap
    next: context

  - name: plan
    agent: architect
    complexity: high     # architecture work deserves the top tier
    next: stub
```

The step **selects** the tier; the agent config **defines** it. What a tier
means (model swap, temperature floor, reasoning effort) lives in
`agents.<name>.complexity_overrides.<tier>` — see
[configuration.md](../../../../../docs/configuration.md#complexity-overrides-per-tier-configuration).

**Precedence** (highest first):

1. an explicit `complexity` in the agent's `dispatch_next` (the agent in the
   loop knows best), then
2. the step's `steps[].complexity`, then
3. `medium`.

The resolved tier is exported to the run as `CONFIG_COMPLEXITY` and drives the
tier machinery at LLM-config resolution time. Note that the *first* step of a
workflow has no preceding dispatch, so its declared `complexity` is the only
thing that can lower its cost — this is the motivating case for the field
(issue #787).
