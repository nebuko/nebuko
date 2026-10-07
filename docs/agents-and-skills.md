# Agents & Skills

The system ships with 8 agents and 5 reusable skills. Each agent is declared
in [`agent-config.yaml`](../defaults/agents/agent-config.yaml) (model, prompt file, tool
whitelist) and defined by a system prompt in `.agents/prompts/{agent}.md`.
Skills are standalone prompt files in `skills/` that can be attached to a
workflow step via `skill: [...]` — see
[workflows.md](./workflows.md#how-declarative-workflows-work).

---

## Agent Roster

| Agent | Role (one line) | Tools | Prompt | Typical workflow steps |
|-------|-----------------|-------|--------|------------------------|
| `genius` | Principal engineer — plans and delegates, never codes | think, finish | `genius.md` | analyze, plan, architect, spec gates |
| `ctx` | Read-only context collector | terminal, file_editor, task_tracker, think, finish | `ctx.md` | context, ctx_architect |
| `dev` | Execution developer — implements and opens PRs | terminal, file_editor, task_tracker, think, finish | `dev.md` | implement, scaffold, sub_clean, final_fix |
| `review` | Senior code reviewer — approves/rejects | think, finish | `review.md` | review, stub_review, final_review |
| `issue` | Refines issue drafts with humans | think, finish | `issue.md` | invoked directly via `@@issue` |
| `issue-reviewer` | Reviews completed issues for workflow optimizations | think, finish | `issue-reviewer.md` | ad-hoc |
| `debug` | Investigates past agent-run logs/artifacts | terminal, file_editor, think, finish | `debug.md` | debug workflow, recover path |
| `po` | Product owner — WHEN/IF/THEN specs | think, finish | `po.md` | spec (spec-driven) |

Behavior notes per agent live at the top of each prompt file; the issue agent's
behavior contract (question format, state markers, delegation rules) is also
documented in [`AGENTS.md`](../AGENTS.md).

---

## Per-Agent Notes

### genius — Principal Engineer
Analyzes requests, designs architecture, drafts plans, and hands off to
`@@ctx` and `@@dev`. Its prompt opens with a **critical override** telling it to
ignore the SDK's generic coding instructions: it is *not* a coding agent and
must not explore repos or edit files (the tool whitelist enforces this
regardless). Plans end in an `AGENT_RUN_CONFIG` dispatch that routes the next
step — including `<splitPlan>` blocks for task decomposition
([features.md — Task Decomposition](./features.md#task-decomposition)).

### ctx — Context Collection Agent
Strictly read-only information gathering: locate files, extract snippets with
line numbers, search the web, and report in a fixed template (files found,
relevant snippets, external references). Analysis and solution proposals
belong exclusively to `@@genius` — its job is the raw material.

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
`@@genius` — accuracy over cost.

### issue — Issue Refinement Agent
Iteratively refines issue drafts with the human: rephrases first, asks
1–5 grouped questions per round with proposed answers, and may delegate context
collection to `@@ctx` (max one call per round). Tracks progress across stateless
runs via `<!-- issue-agent-state -->` markers (see
[reference.md — Bot Marker Conventions](./reference.md#bot-marker-conventions)).

### issue-reviewer
Analyzes *completed* issues and their resolution trajectories — agent metrics,
collaboration patterns, resolution quality — and produces recommendations for
prompt and process improvements.

### debug
Investigates unusual agent behavior: extracts run IDs from comments, downloads
GHA artifacts, analyzes `console.log` files, and posts structured checkpoint
comments so an analysis survives interruptions and can be resumed.

### po — Product Owner
Reads a feature request and produces a structured natural-language
specification using WHEN/IF/THEN scenarios. Used by the `spec-driven`
workflow; the spec is reviewed by a human before any code is written.

---

## The Skills System

A skill is a Markdown prompt file in `skills/`. A workflow step attaches one or
more with `skill: [name]`; the skill content is injected into that step's agent
prompt, narrowing or constraining the agent's behavior for that step only.

| Skill | File | Purpose |
|-------|------|---------|
| `stub-only` | [`stub-only.md`](../defaults/skills/stub-only.md) | Constrains `dev` to fill existing function stubs + write spec-matching tests. No new logic, no new files beyond helpers/tests. Used by `spec-driven` and `spec-driven-impl`. |
| `scaffold-plan` | [`scaffold-plan.md`](../defaults/skills/scaffold-plan.md) | Converts the approved `<planWorkflow>` JSON block into a runnable per-step `tmp_impl/workflow.yaml` sub-workflow. Used by `plan-orchestration` (issue #462). |
| `plan-cleanup` | [`plan-cleanup.md`](../defaults/skills/plan-cleanup.md) | Removes the `tmp_impl/` scaffolding after all plan steps are done. Used by `plan-orchestration`. |
| `final-fix` | [`final-fix.md`](../defaults/skills/final-fix.md) | Addresses the final whole-PR review feedback across all plan steps. Used by `plan-orchestration`. |
| `python` | [`python.md`](../defaults/skills/python.md) | Generic Python style/testing guidance (PEP 8, type hints, pytest). |

Agents can also carry default `skills` in their `agent-config.yaml` stanza;
step-level skills override/augment them.

---

## Tool Permission Model

Tools form a hard security boundary — the OpenHands SDK only instantiates the
tools whitelisted in the agent's config.

| Tool | genius | ctx | dev | review | issue | issue-reviewer | debug | po |
|------|:------:|:---:|:---:|:------:|:-----:|:--------------:|:-----:|:--:|
| `terminal` | | ✓ | ✓ | | | | ✓ | |
| `file_editor` | | ✓ | ✓ | | | | ✓ | |
| `task_tracker` | | ✓ | ✓ | | | | | |
| `think` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `finish` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

(`think` and `finish` are built-in defaults present for every agent.)

Rationale: planning and judgment roles (`genius`, `review`, `issue`,
`issue-reviewer`, `po`) can *only reason and reply* — they cannot
accidentally modify the repository. Context/execution roles (`ctx`, `dev`,
`debug`) need full tool access. Note `ctx` has shell access but its prompt
forbids modification — read-only-by-policy, unlike `genius`'s read-only-by-
enforcement.

---

## Prompt File Convention

Prompts live at `.agents/prompts/{agent}.md` and are loaded by the
`load_prompt` feature of the `agent_script` package. The path is resolved from
the agent's `prompt_file` field in `agent-config.yaml`; the workflow step's
context (workflow name, current step, history, issue context) is appended to
the system prompt at runtime — the prompt file itself stays static and
reusable across workflows.
