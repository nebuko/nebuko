# git-flow-ai vs LangGraph — Comparison

> **Purpose:** Permanent reference answering "why git-flow-ai and not LangGraph?" for newcomers coming from the LangGraph ecosystem.
> **Companion threads:** [#122](https://github.com/olivier-norture/git-flow-ai/issues/122) (Continue or Pivot?), [#227](https://github.com/olivier-norture/git-flow-ai/issues/227) (competitor analysis), [#484](https://github.com/olivier-norture/git-flow-ai/issues/484) (deep comparison, source of this doc).
> **Data snapshot:** GitHub API, 2026-08-25.

---

## 1. What Each Project Is

**LangGraph** (40.4k ⭐, ~6.8k forks, MIT, created 2023-08) is a **code-first agent runtime**: a Pregel-style state-machine library. You build a `StateGraph` in Python/JS, define nodes/edges in code, and get durable state via checkpointers (Postgres, etc.). Its commercial stack is LangGraph OSS + LangSmith Deployment + LangSmith observability.

**git-flow-ai** is a **GitHub-native multi-agent development product**: GitHub Actions is the runtime. Workflows are declarative YAML files (`workflows/*.yaml`) describing specialized agent roles (genius / ctx / dev / review / issue), conditional routing, and human approval gates. Adoption is zero-copy — copy one `workflow.yaml` into any repo (see `README.md`).

## 2. Capability Matrix

| Capability | LangGraph | git-flow-ai |
|---|---|---|
| Workflow definition | **Code** (StateGraph / `@entrypoint`, Python/JS) | **Declarative YAML** (`workflows/*.yaml`) |
| Portable, shareable workflow artifact | ❌ None in core — code only; community-built DIY YAML→graph patterns exist (e.g., GCP blog), not a framework feature | ✅ Single YAML file, fetchable from *any* git repo at any ref (`remote_workflow.py`), versioned & reviewable independently of app code |
| Human-in-the-loop gates | ⚠️ Manual `interrupt()` / `Command(resume=...)` code; first-class `ApprovalNode` still an **open feature request** ([langgraph #8026](https://github.com/langchain-ai/langgraph/issues/8026)) | ✅ First-class: `human_gate: true` with `approved`/`rejected` routing; the gate is literally a GitHub comment reply |
| Execution host | Your infrastructure (serverless, Docker, LangSmith Deployment) | GitHub Actions native — the CI/CD pipeline *is* the runtime |
| Multi-agent specialization | DIY via subgraphs | 5 specialized agents with per-step `skill:` attachment |
| Model agnosticism | Yes (via LangChain integrations) | Yes (layered `agent-config.yaml`: providers → LLMs incl. local vLLM → agents) |
| Intra-agent logic expressiveness | ✅ Pregel model: parallelism, conditional subgraphs, retries with state branching | Linear steps + conditions (per-step); richer intra-agent graphs are LangGraph's edge |
| State persistence | ✅ Checkpointer: durable, queryable, time-travelable | Workflow state persisted in issue comments (auditable, self-healing, coarser granularity) |
| Observability / managed platform | ✅ LangSmith tracing, evals, hosting | Metrics & circuit-breaker posted in GitHub comments (transparent, less polished for enterprise) |
| Non-GitHub shops (GitLab, Bitbucket, internal) | ✅ Works anywhere | ❌ GitHub-only today |

## 3. The Key Questions

### Could LangGraph challenge via shareable YAML workflows?

**Realistically, no — their own signals say so.**

- Their tracker has **zero issues** requesting a YAML/declarative workflow format (verified 2026-08-25: `gh search issues --repo langchain-ai/langgraph "YAML workflow"` → no matches). Roadmap energy goes to runtime robustness and platform features.
- A YAML layer would constrain the fine-grained programmatic control that is LangGraph's core value proposition ("mix deterministic hand-coded steps with LLM-driven agentic steps"). Their declarative surface lives in **LangGraph Platform (managed deployment)**, not in a portable definition file.
- Closest they have: shared subgraphs/templates as **Python packages** — reuse within one team's Python codebase, not language-agnostic "copy one file into any repo" adoption.
- Even a hypothetical YAML subset would describe **agent logic graphs**, not **GitHub-native development workflows**: no issue-comment triggers, no `@@mention` routing, no PR lifecycle, no human-gate-as-comment. They'd still need what git-flow-ai already embeds.

> From prior analysis (#227): *"No other project embeds itself directly as a GitHub repository workflow. git-flow-ai's position of 'copy one YAML file into any repo' has no equivalent in the competitive landscape."*

### Is there a LangGraph integration GitHub issue?

**No.** Verified both directions on 2026-08-25:

- Issues in `langchain-ai/langgraph` mentioning "git-flow-ai": only [#547](https://github.com/langchain-ai/langgraph/issues/547), a 2-year-old closed error-message bug — a false-positive substring hit, not an integration request.
- No PRs referencing git-flow-ai in either direction.
- Nearest-adjacent threads live in *this* repo: #122, #227, #318 (competitor search), #319 (Kestra).

### Where could LangGraph win?

1. **Complex intra-agent logic** — DAG-level parallelism, conditional subgraphs, and stateful retries *inside one agent's reasoning*. Possible complement: run a LangGraph graph *inside* a single step, not as a replacement.
2. **Ecosystem & talent gravity** — 40k stars, company backing, large docs/community. Onboarding to our ~20k-line Actions orchestrator is heavier.
3. **Managed deployment & observability** — LangSmith gives production-grade tracing/evals/hosting out of the box.
4. **Non-GitHub shops** — LangGraph runs anywhere; we are GitHub-native.
5. **State persistence semantics** — durable, queryable, time-travelable conversation state vs. our comment-based state.

Where they **cannot** win us: zero-infrastructure GitHub-native operation, mention-triggered off-by-default activation, per-token cost, human gates as plain GitHub comments, and workflow-as-portable-YAML.

## 4. FAQ for LangGraph Users

> **"I already use LangGraph — why not build this on top of it?"**
> LangGraph is a *library for agent logic*; git-flow-ai is a *product*: GitHub event ingestion, `@@mention` routing, 5-agent prompt system, branch/PR/review lifecycle, circuit breakers, and one-file portability. Building on LangGraph means rebuilding all of that yourself.

> **"Why YAML instead of code? Isn't code more flexible?"**
> For *agent logic*, yes. For *orchestration across specialists* with human approval points, declarative wins: the workflow is reviewable in a PR, shareable across repos/teams without a language dependency, and readable by the humans who approve its gates.

> **"How do I do human-in-the-loop in LangGraph?"**
> Today: manual `interrupt()` + `Command(resume=...)` in Python; a first-class approval-node request is still open ([#8026](https://github.com/langchain-ai/langgraph/issues/8026)). In git-flow-ai it is one line: `human_gate: true`, and the gate is a GitHub comment reply.

> **"Can I have both?"**
> That's the sensible end-state: keep the GitHub-native shell + portable YAML + human gates, and if a single step needs rich intra-agent DAG control, run a LangGraph graph *inside* that step. The orchestration layer is an implementation detail; the product is the GitHub-native, mention-triggered, portable workflow.

## 5. Verdict

LangGraph is a strong **complement**, not a **challenger**. It competes on agent-logic expressiveness and ecosystem gravity; it structurally cannot match zero-infra GitHub-native workflows, mention-triggered multi-agent roles with approval gates, or copy-one-YAML adoption — and its roadmap shows no appetite for a declarative shareable-workflow layer. Main strategic risks: talent/onboarding asymmetry and enterprise observability polish, not workflow portability.