# git-flow-ai — Landscape

**Vision:** agents that delegate to each other (`@@`) become workflows that encode how a team actually works, which becomes a library of shared, reusable processes — from capability, to product, to platform.

git-flow-ai is a GitHub-native multi-agent development tool. Agents with defined roles
collaborate on real issues and pull requests, coordinate through the issue thread itself,
and follow declarative workflows that reflect your team's engineering process — including
points where a human must approve before the machine continues.

---

## The pain points

### 1. AI-generated code shifts the bottleneck to review

LLMs produce large pull requests quickly. Merging them requires a human to diff line by
line, which makes *review time*, not generation time, the limiting factor of the pipeline.
The cost is paid on every merge, by the person who already has too many open PRs.

### 2. Rigid automation cannot adapt

CI/CD pipelines are deterministic. When step 3 needs information that step 2 did not
produce, the pipeline either fails or moves forward blind. There is no way for a stage
to say "I'm missing something — ask the specialist who knows" without rewriting the
pipeline as a whole.

### 3. Team process lives in people's heads

How a good team works — spec, design review, stub review, implementation, verification —
is tribal knowledge carried by meetings and senior engineers. It is not versioned, not
shareable, not reproducible by a new team. When the people leave, the process leaves too.

### 4. Agent tools are disconnected

Today's AI coding assistants are single agents. They have no roles, no awareness of a
roster of specialists, and no mechanism to hand work to another agent when the task
exceeds what they are good at. Delegation — the core skill of any engineering team —
is missing.

### 5. Setup friction and credential risk

Wiring multi-agent automation into a repository today typically means long-lived personal
access tokens with broad scopes, pasted into configs, hard to scope, hard to revoke, and
unsafe to share. Onboarding a teammate or a new repository is disproportionately painful.

---

## The solution

Each pain point maps to a concrete mechanism in git-flow-ai.

### Agents with personas and `@@` delegation

Every agent is defined declaratively: role, personality, capabilities, model, and skills.
Each agent knows the roster — who else exists and what they are responsible for. During
any step, an agent can **delegate with a simple `@@mention`**: "I need context on X",
"@@dev, implement this stub", "@@review, check this diff". The result propagates back
into the same issue thread, where the calling agent picks up where it left off.

This answers pain points 1, 2, and 4 at once: a mid-step gap is filled by pinging the
right specialist instead of failing the pipeline, and humans watch the delegation happen
in the issue thread rather than an opaque dashboard.

### Workflows as the core product

A workflow is a versioned, declarative YAML document that encodes how work actually gets
done: ordered steps, each bound to an agent role, with context passed between steps and
**human approval gates** where required.

```yaml
name: spec-driven
steps:
  - name: spec
    agent: genius          # drafts architecture spec
  - name: spec_approval
    agent: genius
    human_gate: true       # a human must approve before anything is built
  - name: stubs
    agent: dev             # method stubs: injection points, call sites, test cases
  - name: stub_approval
    agent: genius
    human_gate: true       # review the design decisions, not the implementation
  - name: implement
    agent: dev             # fills implementations against approved stubs
  - name: verify
    agent: review          # runs tests, opens the PR
```

Human attention is spent where it is worth paying: on the architecture spec, the stubs
(injection points, call sites, test cases), and the verdict — not on full implementations.
That is the direct answer to pain point 1: the review burden drops from "every line of a
large AI PR" to "two design-level gates", which measurably reduces the time needed to
merge AI-generated code.

Workflows solve pain point 3 by making team process a **first-class artifact**: checked
into the repository, diffable, versioned, forkable, and shareable. A team's way of working
stops living in meetings and starts living next to the code it produces.

### Workflow-as-artifact: sharing without a registry

Because a workflow is just a versioned YAML file in a git repository, sharing works on day
one with zero infrastructure: point at a file in a public repo, copy it into yours, adapt
it. The project ships a small set of dogfooded workflows — flagship being
**spec-driven** (spec → human gate → stubs → human gate → implement → verify) — built from
real usage rather than imagined use cases. If those workflows prove their value, the
format is ready to carry a community library and, later, a managed marketplace on top.
The format is the open contract; distribution is a layer above it.

### GitHub-native by design

No new infrastructure: **GitHub Issues and Comments are the transport layer**. Agents
post progress, questions, and results as comments; humans approve gates and redirect work
by commenting — the same way they already work. The repo is the system.

For credentials (pain point 5), the direction is a **GitHub App installation** as the
primary setup — per-repo install, fine-grained permissions, scoped and revocable
installation tokens — with a PAT fallback retained for local and contributor dev mode.
Setup becomes "install the app on this repo" instead of "paste a long-lived token".

### Open core: self-hosted core, managed service on top

- **Open-source core:** the engine, agent definitions, `@@` delegation, workflow format
  and executor, and GitHub transport. Self-hosted, runnable with your own LLM key (the
  LLM adapter is pluggable — OpenRouter, local models, any provider), zero cloud
  dependency.
- **Managed SaaS (later):** hosted operation of the core — managed LLM routing and cost
  controls, observability, cross-repo agent visibility, and a workflow marketplace with
  discovery and curation. A distribution and convenience layer wrapping the open core,
  never forking it.

The core will be released under a copyleft-style license (AGPL v3 is currently the leading
candidate; final decision pending), chosen to keep the engine freely usable while
preventing closed re-service of unmodified copies.

### Shipped early and staged

The release philosophy is thin, end-to-end slices, each genuinely working:

| Stage | What lands |
|-------|-----------|
| **Alpha** | One true round-trip: two agents, one `@@` delegation on a real issue, result posted back. |
| **Beta** | Flagship spec-driven workflow end-to-end with both human gates; workflow format v1 freezes. |
| **0.1** | Stable tag, format documented as a public contract, workflows robust enough to fork. |

The project is built in public — progress, changes, and lessons are visible in the repo
as they happen. Contributors meet a project in motion, not a big reveal.

---

## In one paragraph

git-flow-ai turns engineering process into executable, shareable artifacts. Agents with
defined roles collaborate on GitHub issues, delegating to specialists with `@@` when a
step needs help. Declarative YAML workflows encode how a team works — including the human
approval gates that keep design decisions human-owned. The result: less line-by-line review
of AI code, process that survives personnel change, and a path from your private workflow
to a shared library others can fork.