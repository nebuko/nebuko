---
title: "Step instructions (prompt)"
audience: user
sidebar_group: "Guides"
sidebar_label: "Step instructions"
sidebar_order: 11
---

# Step instructions (prompt)

A step can carry a `prompt:` field with a standing instruction that is
injected into that step's agent context every time the step runs — useful for
repetitive per-workflow directives such as output-format contracts or analysis
requirements.

```yaml
steps:
  - name: analyze
    agent: architect
    prompt: |
      Analyze the transcription and provide me:
      a short summary (~5-10 sentences)
      5 Key points
      A possible link/impact/benefits to the https://github.com/your/repo project
    next: context
```

**Semantics & precedence.** The step prompt is a *standing instruction* for
that step — it governs the format and behavior of that step. The issue body
remains the *task-specific* content and wins on task specifics; the role prompt
stays the base identity. Injection order in the agent context:
**role identity → step instruction → issue body/history** (section header:
`### 🔴 STEP INSTRUCTION (from workflow.yaml)`).

**Mechanics.** The resolver writes the prompt verbatim to
`/tmp/step_prompt.md` and emits `STEP_PROMPT_FILE` (path only — the prompt may
contain newlines). When the step has no `prompt`, the variable is empty and any
stale file is removed, so existing workflows are unaffected. Multi-line YAML
block scalars (`|` / `>`) are supported.

**Replacement mode (`prompt_replace: true`).** By default the step prompt is
*additive*: the role prompt stays the base identity. Setting
`prompt_replace: true` alongside `prompt:` makes the step prompt the **entire**
agent context — the role prompt (e.g. `dev.md`) and its generic
Understand / Plan / Implement / Verify flow are suppressed. Use it to
structurally scope a step to a single job that the role's default flow would
otherwise broaden (e.g. a scaffold step that must materialize a plan and not
implement it). It is ignored when `prompt:` is unset, so existing workflows are
unaffected.

**`prompt:` vs `skill:`.** Use `prompt:` for short, self-contained,
per-step directives that should live inline in the workflow. Use
`skill: [...]` for richer, multi-file, or reusable knowledge files in
`defaults/skills/`. They compose: a step may have both.

**`inject` vs `discover`.** An injected skill's whole body is rendered into the
step prompt, so it costs context on every turn — use it for rules the agent must
always have (the posting contract, a mode's absolute constraints). A discovered
skill only shows its name and one-line summary; the agent pulls the body in with
`invoke_skill` when it needs it (see
[agents-and-skills.md](../../../../../../../docs/agents-and-skills.md#the-skills-system)). A bare
`- name` entry is injected.
