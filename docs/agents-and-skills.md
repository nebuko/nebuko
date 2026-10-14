# Agents & Skills

The system ships with 5 agents and 13 reusable skills. Each agent is declared
in [`agent-config.yaml`](../defaults/agents/agent-config.yaml) (model, one-line
`role`, prompt file, skills, tool whitelist) and defined by a system prompt in
`defaults/prompts/{agent}.md`. Skills are standalone prompt files in
`defaults/skills/` that can be attached to an agent by default (`skills:` in
its config stanza) or to a workflow step via `skill: [...]` — see
[workflows.md](./workflows.md#how-declarative-workflows-work).

The reasoning behind this structure — central config over per-file frontmatter,
personality-only prompts with procedure in skills, and the generated team
context — is recorded in
[ADR-0010: Agent File Structure](./adr/0010-agent-file-structure.md).

---

## Agent Roster

The roster is **derived from the config**, not from a prompt file: the
`agents:` map key is the agent name and its `role` field is the one-line
description below. Adding an agent to `agent-config.yaml` is enough to make it
routable, `@@`-mentionable, and visible in every other agent's team section —
no code or prompt edit required.

| Agent | Role (from config `role:`) | Tools | Prompt | Typical workflow steps |
|-------|-----------------|-------|--------|------------------------|
| `architect` | Principal software engineer — analysis, architecture, planning, and delegation; never writes code | think, finish | `architect.md` | analyze, plan, architect, spec gates |
| `context` | Read-only context collector | terminal, file_editor, task_tracker, think, finish | `context.md` | context, context_architect |
| `dev` | Implementation engineer — writes code, runs tests, manages branches, commits, and PRs | terminal, file_editor, task_tracker, think, finish | `dev.md` | implement, scaffold, sub_clean, final_fix |
| `review` | Code and plan reviewer — approves/rejects round by round; runs issue retrospectives | think, finish | `review.md` | review, stub_review, final_review |
| `po` | Product owner — WHEN/IF/THEN specs and issue refinement | think, finish | `po.md` | spec (spec-driven), issue refinement |

Behavior notes per agent live at the top of each prompt file (personality and
core rules only); the product owner's issue-refinement behavior contract
(question format, state markers, delegation rules) lives in the
[`issue-refinement`](../defaults/skills/issue-refinement.md) skill.

Prompts themselves hold only **personality and core rules**. Procedural
playbooks (search strategy, git/PR mechanics, planning, review modes, issue
refinement) are skills, read on demand — see
[The Skills System](#the-skills-system).

---

## Per-Agent Notes

### architect — Principal Engineer
Analyzes requests, designs architecture, drafts plans, and hands off to
`@@context` and `@@dev`. Its prompt opens with a **critical override** telling it to
ignore the SDK's generic coding instructions: it is *not* a coding agent and
must not explore repos or edit files (the tool whitelist enforces this
regardless). Plans route the next step via the typed `dispatch_next` tool. The
posted `AGENT_RUN_CONFIG` sentinel is engine-rendered, not agent-emitted
(issue #876).

### context — Context Collection Agent
Strictly read-only information gathering: locate files, extract snippets with
line numbers, search the web, and report in a fixed template (files found,
relevant snippets, external references). Analysis and solution proposals
belong exclusively to `@@architect` — its job is the raw material.

Its web-search phase reads each selected reference **in full** (3 for LOW,
5–6 for MEDIUM, up to 10 for HIGH complexity) and curates the result: verbatim
relevant excerpts (≤ ~2K tokens per article), a short relevance summary, and a
0.0–1.0 interest score — emitted as a fenced JSON block inside
`### EXTERNAL REFERENCES` alongside the human-readable entries
(schema: [reference.md — External Reference JSON](./reference.md#external-reference-json),
sharing contract: [features.md — External Reference Sharing](./features.md#external-reference-sharing)).

### dev — Execution Developer
Implements approved plans, writes and runs tests, and commits/pushes under
strict git rules (never push to `main`; mandatory rebase on `origin/main` as
the first action; single PR per issue; all review discussions in the issue,
not the PR). In skill-constrained modes (`stub-only`, `scaffold-plan`,
`plan-cleanup`, `final-fix`) its allowed surface shrinks to exactly that skill's
scope.

### review — Senior Code Reviewer
Inspects diffs for bugs, edge cases, regressions, security, and maintainability,
then returns an explicit verdict (`approved` / `rejected`) that drives
conditional workflow routing. Uses the same high-quality LLM class as
`@@architect` — accuracy over cost. Also owns **issue retrospectives**: it
analyzes *completed* issues and their resolution trajectories — agent metrics,
collaboration patterns, resolution quality — and produces recommendations for
prompt and process improvements.

### po — Product Owner
Reads a feature request and produces a structured natural-language
specification using WHEN/IF/THEN scenarios. Used by the `spec-driven`
workflow; the spec is reviewed by a human before any code is written. Also
owns **issue refinement**: it iteratively refines issue drafts with the human,
rephrases first, asks 1–5 grouped questions per round with proposed answers,
and may delegate context collection to `@@context` (max one call per round),
tracking progress across stateless runs via `<!-- po-agent-state -->`
markers (see
[reference.md — Bot Marker Conventions](./reference.md#bot-marker-conventions)).

---

## The Skills System

A skill is a Markdown prompt file in `defaults/skills/`. An agent attaches one or
more by default through the `skills:` field of its `agent-config.yaml` stanza; a
workflow step attaches one or more with `skill: [name]`. Skills are **not**
currently injected into the system prompt: each agent reads the skill files it
needs on demand (see the instruction at the top of `defaults/prompts/dev.md`).
The former system-channel injection (`SKILL_CONTEXT_FILE` →
`security_policy_filename`) was removed with the SDK prompt-section registry in
issue #943; re-routing skills through the SDK's `AgentContext(skills=[...])` is
tracked separately.

| Skill | File | Purpose |
|-------|------|---------|
| `call-agent` | [`call-agent.md`](../defaults/skills/call-agent.md) | Teaches delegation via the `function_call` / `call_agent` mechanism and the `callback` return address. Default for `architect`, `context`, `po`. |
| `posting-contract` | [`posting-contract.md`](../defaults/skills/posting-contract.md) | How the final answer is posted to the issue. Default for every agent. |
| `context-research` | [`context-research.md`](../defaults/skills/context-research.md) | Search strategies and complexity-based exploration depth for `context`. |
| `git-pr-workflow` | [`git-pr-workflow.md`](../defaults/skills/git-pr-workflow.md) | Rebase/PR mechanics, checkpoints, and git safety rules for `dev`. |
| `planning` | [`planning.md`](../defaults/skills/planning.md) | Plan artifact shape, `<planWorkflow>` mechanics, and orchestration modes for `architect`. |
| `plan-review` | [`plan-review.md`](../defaults/skills/plan-review.md) | Plan-workflow review modes for `review`. |
| `retrospective` | [`retrospective.md`](../defaults/skills/retrospective.md) | Issue retrospective mode for `review`. |
| `issue-refinement` | [`issue-refinement.md`](../defaults/skills/issue-refinement.md) | Issue refinement behavior contract (question format, state markers, delegation) for `po`. |
| `stub-only` | [`stub-only.md`](../defaults/skills/stub-only.md) | Constrains `dev` to fill existing function stubs + write spec-matching tests. No new logic, no new files beyond helpers/tests. Used by `spec-driven` and `spec-driven-impl`. |
| `scaffold-plan` | [`scaffold-plan.md`](../defaults/skills/scaffold-plan.md) | Converts the approved `<planWorkflow>` JSON block into a runnable per-step `tmp_impl/workflow.yaml` sub-workflow. Used by `plan-orchestration` (issue #462). |
| `plan-cleanup` | [`plan-cleanup.md`](../defaults/skills/plan-cleanup.md) | Removes the `tmp_impl/` scaffolding after all plan steps are done. Used by `plan-orchestration`. |
| `final-fix` | [`final-fix.md`](../defaults/skills/final-fix.md) | Addresses the final whole-PR review feedback across all plan steps. Used by `plan-orchestration`. |
| `python` | [`python.md`](../defaults/skills/python.md) | Generic Python style/testing guidance (PEP 8, type hints, pytest). |

Agents carry default `skills` in their `agent-config.yaml` stanza; step-level
skills override/augment them.

---

## Tool Permission Model

Tools form a hard security boundary — the OpenHands SDK only instantiates the
tools whitelisted in the agent's config.

| Tool | architect | context | dev | review | po |
|------|:------:|:---:|:---:|:------:|:--:|
| `terminal` | | ✓ | ✓ | | |
| `file_editor` | | ✓ | ✓ | | |
| `task_tracker` | | ✓ | ✓ | | |
| `dispatch_next` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `report_scaffold_branch` | | | ✓ | | |
| `think` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `finish` | ✓ | ✓ | ✓ | ✓ | ✓ |

(`think`, `finish` and `dispatch_next` are present for every agent;
`report_scaffold_branch` is `dev`-only.)

Rationale: planning and judgment roles (`architect`, `review`, `po`) can
*only reason and reply* — they cannot accidentally modify the repository.
Context/execution roles (`context`, `dev`) need full tool access. Note
`context` has shell access but its prompt forbids modification —
read-only-by-policy, unlike `architect`'s read-only-by-enforcement.

---

## Prompt File Convention

Prompts live at `defaults/prompts/{agent}.md` and are loaded by the
`load_prompt` feature of the `agent_script` package. The path is resolved from
the agent's `prompt_file` field in `agent-config.yaml`; the workflow step's
context (workflow name, current step, history, issue context) is appended to
the system prompt at runtime — the prompt file itself stays static and
reusable across workflows.

Runtimes re-resolve a configured prompt by its **basename** through the layered
prompt lookup (`paths.resolve(ConfigKind.PROMPT, ...)`), so a deployment can
override a shipped prompt by name without editing the config. Prompt files
carry **personality and core rules only**; every agent's shared sections —
generic exit discipline and the generated "Your Team" block — are injected
once at run time, not stored in the file.

---

## Team Context Injection

Every agent's system prompt carries a generated **"Your Team"** section,
assembled from the live roster at run time
(`engine/lib/resolve_config/features/team_context/team_context.py`):

```
# Your Team
You are <self> — <role>
Teammates:
- <name>: <role>            # one line per other agent, from config `role:`
Delegation rules:
- Only do work covered by your own tools (...). Work in a teammate's scope must
  be delegated to them via the `call_agent` mechanism (see the `call-agent`
  skill); `@@<name>` mentions are for humans only.
- When a teammate calls you, your answer is returned to them automatically (the
  engine's `callback` routes it) — treat it as a conversation turn.
[You were called by <caller> — your result returns to <caller>.]   # only when invoked by a bot
```

The same module injects one shared copy of the **generic exit rules** (removed
from the individual prompt files), so each built prompt contains exactly one
exit-rules section and exactly one team section. An unreadable roster degrades
to no team section rather than failing the run, mirroring
`config.load_agent_names`. See
[handoff-and-callback.md](./handoff-and-callback.md) for the delegation and
callback contract this section describes.
