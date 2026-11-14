# ADR-0010: Agent File Structure — Config Roster, Personality-Only Prompts, Generated Team Context

**Status:** Accepted — 2026-10-09
**Decider:** olivier-norture
**Related:** #476 (this decision), #477 (superseded implementation), #828/#835 (agent rename), #478 (bots may not `@@`-mention), #765 (callback contract), #774 (agent-config linter), #570 (progressive disclosure), #446 (shareable agents); ADR-0007 (OpenHands as Layer 1), ADR-0003 (context management)

## Context

An agent used to be defined by a single **fat prompt file**: role, personality,
procedural playbooks (search strategy, git/PR mechanics, planning, review
modes), duplicated exit-rules blocks, and prose restating its own tool
whitelist. The same operational content lived in several prompts; the exit
rules were copy-pasted into every one; tool restrictions were described in
prose *and* enforced by the config `tools:` list.

issue #476 asked to keep only **personality and core rules** in the agent file
and move everything procedural into **skills**, while giving each agent
awareness that it is part of a team it can delegate to and must answer back.
PR #477 implemented an early version (with per-file YAML frontmatter); it was
never merged, and by the time it was revisited the repository had been
restructured and the roster renamed (#828/#835), leaving most of its goals
already implemented on `main` in evolved form, and its paths extinct.

## Decision

We adopt five decisions. Together they define the current agent-file structure.

**D1 — The layered `agent-config.yaml` is the single source of truth for agent
identity. No YAML frontmatter in prompt files.**
The `agents:` map key is the agent name; a new optional `role` field carries the
one-line description. Routable agents, `@@`-mention parsing, and the team roster
are all derived from this map (`config.load_agent_names`), so adding an agent is
a config edit, not a code edit.
*Rejected alternative:* the Claude Code subagent model — one Markdown file per
agent with YAML frontmatter (`name`, `description`, `tools`, `model`). Its one
real advantage is per-file **portability** (a future "shareable agents"
story, #446). We are not there yet; for a single owned team, a central YAML is
more structured, reviewable, and fail-fast: the whole roster is validated at
once by the offline linter (C1–C8, #774), and the prompt files stay pure
content. Portability can later be layered on top with an export format without
betting on frontmatter now.

**D2 — Prompt files hold personality and core rules only; procedural knowledge
lives in skills.**
Playbooks move to `defaults/skills/` (`context-research`, `git-pr-workflow`,
`planning`, `plan-review`, `retrospective`, `issue-refinement`) and are attached
per agent via the config `skills:` field. The generic exit-rules block is
removed from every prompt and injected once at run time. Duplicated prose
restating the tool whitelist is deleted — `tools:` enforces it structurally.
This is the progressive-disclosure shape recommended for Agent Skills (metadata
loaded up front, procedural body loaded on demand).

**D3 — Team awareness is generated, not written.**
At system-prompt build time, `resolve_config/features/team_context` renders a
"Your Team" section from the live roster: the agent's own role, each teammate's
`role` from config, the delegation rule, the call-back reminder, and — when the
run was bot-invoked — the caller's name. It is pure text generation over the
parsed config and degrades to no section (never a failed run) when the roster is
unreadable, mirroring `config.load_agent_names`.

**D4 — `@@<name>` is human-facing vocabulary; bots delegate through
`call_agent` / the `callback` contract.**
Bot messages may not use `@@` mentions (#478); the injected team section says so
explicitly and teaches the `call_agent` `function_call` mechanism instead. The
call-back rule in the prompt is a **reminder** layered on the engine-enforced
callback contract (#765) — it adds no new mechanism and no loop-guard change.

**D5 — The config map key is the agent identity; legacy aliases are accepted
only at the trigger boundary.**
`genius` → `architect` and `ctx` → `context` (#828/#835) remain accepted as
trigger mentions through `parse_trigger.LEGACY_AGENT_NAMES`, but never re-enter
config, prompts, or the team roster. This records the rename's naming scheme,
for which no prior ADR existed.

## Consequences

- **Adding or renaming an agent** touches `agent-config.yaml` (+ one prompt
  file) and, for a rename, the legacy-alias map and the CI trigger guard — not a
  dozen hardcoded lists.
- **Every built prompt contains exactly one exit-rules section and exactly one
  "Your Team" section**, enforced by tests, replacing nine duplicated copies.
- **A missing `role`** is a lint **warning** (`C8-missing-role`) and renders a
  placeholder — it never breaks a run.
- The prompt↔skill split must be kept honest: the plan-orchestration step skills
  (`scaffold-plan`, `plan-cleanup`, `final-fix`) and `planning` overlap in
  territory, and content must not be maintained in two places.
- `prompt_file` values keep the shipped `defaults/prompts/{agent}.md` form;
  only the basename is used at run time, so deployment-level prompt overrides
  stay possible through the layered prompt lookup.
- The `TASK_TO_PROMPT` map becomes a thin roster-derived lookup with a logged
  static fallback, consistent with the non-fatal contract of
  `load_agent_names`.
