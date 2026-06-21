# Comparative Analysis: git-flow-ai vs Google ADK

> **Last updated:** 2026-06-21
>
> This document compares git-flow-ai's YAML-driven, GitHub-native multi-agent orchestration against Google's Agent Development Kit (ADK), a code-first Python SDK for building deployable agent applications. It covers paradigm differences, runtime models, workflow composition, handoff mechanisms, human-in-the-loop patterns, state management, and observability — concluding that the two tools serve different domains rather than competing directly.

---

## Executive Summary

These two projects solve the same problem (multi-agent orchestration) from fundamentally different angles. **git-flow-ai is a configuration-driven, YAML-based orchestration layer that runs on GitHub Actions.** **Google ADK is a code-first Python SDK for building agent applications as deployable services.** They occupy different layers of the stack.

---

## 1. Paradigm: Declaration vs Code

**git-flow-ai** declares workflows as YAML DAGs:

```yaml
steps:
  - name: analyze
    agent: genius
    next: context
  - name: context
    agent: ctx
    next: plan
```

**Google ADK** defines workflows programmatically in Python:

> **Note:** The Google ADK code snippets throughout this document are illustrative and approximate the conceptual equivalent. They may not reflect the exact ADK API surface — refer to [Google ADK documentation](https://adk.dev/) and the [official repository](https://github.com/google/adk-python) for authoritative usage.

```python
from google.adk.agent import SequentialAgent, ConditionalAgent
from google.adk.flow import Graph, Node

graph = Graph()
graph.add_node("analyze", agent_genius)
graph.add_node("context", agent_ctx)
graph.add_edge("analyze", "context")
```

**Trade-off:** YAML is accessible to non-developers and version-controlled naturally alongside infrastructure. Python offers dynamic branching, type safety, and reuse through composition.

---

## 2. Execution Runtime

| Dimension | git-flow-ai | Google ADK |
|-----------|-------------|------------|
| **Runtime** | GitHub Actions (ephemeral containers) | Python process / Cloud Run / GKE |
| **Trigger Model** | GitHub events (issue comments, PRs) | HTTP API, event pub/sub, CLI |
| **Persistence** | GitHub issue comment thread = state | In-memory state + optional vector DB / Firestore |
| **Lifecycle** | Per-issue, finite execution | Long-lived service |
| **Scaling** | GitHub Actions concurrency limits | Horizontal pod scaling (GKE) or serverless (Cloud Run) |

---

## 3. Agent Definition

**git-flow-ai** defines agents as prompt files + tool allowlists + model bindings:

```yaml
agents:
  genius:
    model: "${llms.qwen-3-6-27b}"
    prompt_file: ".agents/prompts/genius.md"
    tools: ["think", "finish"]
  dev:
    model: "${llms.qwen-3-6-27b}"
    prompt_file: ".agents/prompts/dev.md"
    tools: ["terminal", "file_editor", "task_tracker", "think", "finish"]
```

**Google ADK** defines agents as Python objects with configurable parameters:

```python
from google.adk.agent import Agent, ToolSet

genius_agent = Agent(
    name="genius",
    model="gemini-2.0-flash",
    system_instruction=read_prompt(".agents/prompts/genius.md"),
    tools=ToolSet(["think", "finish"]),
)
```

**Key difference:** git-flow-ai separates agent definition from workflow logic entirely (YAML config + Markdown prompts). ADK embeds both in code. git-flow-ai's three-layer `${ref}` resolver (providers → LLMs → agents) is more flexible for swapping models/providers without touching workflow definitions.

---

## 4. Workflow Composition

**git-flow-ai** supports sub-workflows via YAML references:

```yaml
- name: stub_phase
  workflow: spec-stub   # invokes another YAML file
  next: human_approval
```

The resolver (`resolve_workflow.py`) loads the child workflow, finds its first step, and injects parent context (`PARENT_WORKFLOW`, `PARENT_WORKFLOW_STEP`) so the child can return.

**Google ADK** supports sub-graphs via `Graph.compose()` or nested `StateGraph` objects:

```python
stub_subgraph = Graph()
# ... define stub steps ...

main_graph = Graph()
main_graph.add_node("stub_phase", stub_subgraph)
```

**Similarity:** Both support DAG composition.  
**Difference:** git-flow-ai's approach is file-based (natural Git branching per sub-workflow); ADK's is in-memory object composition (easier to parameterize dynamically).

---

## 5. Handoff Mechanism

**git-flow-ai** uses XML-tagged JSON in agent output to signal transitions:

```xml
<agentRunConfig>
{"task": "ctx", "complexity": "medium"}
</agentRunConfig>
```

The GitHub Actions workflow parses these tags to determine the next agent. This is a **coarse-grained, text-based protocol** — the agent produces output, the runner parses it, spins up a new job.

**Google ADK** uses in-process control flow. Agents call tools or return structured results that the graph engine routes to the next node. No parsing required — it's native Python object passing.

**Implication:** git-flow-ai's handoff is inherently slower (new GitHub Action job per step, cold start) but more isolated (crash in one agent doesn't corrupt others). ADK's in-process routing is faster but shares memory/state risks.

---

## 6. Human-in-the-Loop

**git-flow-ai** has explicit human gates in YAML:

```yaml
- name: human_approval
  requires_human_approval: true
  next: implement
```

The workflow pauses; the GitHub issue comment thread becomes the approval channel. Humans reply with `@@genius` to resume.

**Google ADK** provides `human_tool_confirmation` for tool-level confirmation and `interrupt()` for node-level pausing:

```python
node = graph.add_node("approve", interrupt=True)
```

**Comparison:** git-flow-ai's human gates are workflow-schema-level and natural for GitHub-native teams. ADK's is more granular (can confirm individual tool calls) but requires custom UI integration.

---

## 7. State Management

**git-flow-ai** has **no explicit state store**. State lives in:
- GitHub issue body and comments (conversation history)
- Git branches and PRs (artifact state)
- Phase detection via HTML comment markers (`<!-- genius-state: phase=proposal -->`)

**Google ADK** provides `StateGraph` with typed state schemas:

```python
from google.adk.flow import State, StateGraph

class AppState(State):
    current_phase: str
    proposal_approved: bool
    context_collected: dict

graph = StateGraph(AppState)
```

**This is a significant architectural gap.** git-flow-ai relies on LLMs parsing conversation history to reconstruct state, which is fragile. ADK enforces state shape at compile time.

---

## 8. Observability & Evaluation

| Feature | git-flow-ai | Google ADK |
|---------|-------------|------------|
| Metrics | `agent-metrics` HTML comments (tokens, latency, tool calls) | Built-in tracing via LangFuse/OpenTelemetry |
| Circuit Breaker | Custom `.github/scripts/circuit_breaker.py` | Not built-in (use infrastructure-level) |
| Loop Detection | Custom `loop_guard.py` | Not built-in |
| Evaluation | Manual review via `review` agent | Built-in evaluation framework with metrics |
| Debugging | `debug` agent with artifact download | Python debugger, ADK CLI replay |

**Interesting finding:** git-flow-ai implements patterns (circuit breaker, loop guard) that ADK leaves to infrastructure, likely because GitHub Actions is an uncontrolled environment where agents can run indefinitely.

---

## 9. Concrete Example: Converting `spec-driven-design` to ADK

The `spec-driven-design` workflow from the issue would look roughly like this in ADK:

```python
from google.adk.agent import Agent
from google.adk.flow import StateGraph, State

class SpecDesignState(State):
    phase: str = "analyze"
    user_request: str
    context: dict = {}
    proposal: str = ""
    approved: bool = False
    stub_pr_url: str = ""
    implementation_complete: bool = False

graph = StateGraph(SpecDesignState)

# Phase 1: Analysis & Planning
graph.add_node("analyze", genius_agent, next_node="context")
graph.add_node("context", ctx_agent, next_node="plan")
graph.add_node("plan", genius_agent, next_node="stub_subgraph")

# Phase 1b: Stub Generation (sub-workflow)
stub_graph = StateGraph(SpecDesignState)
stub_graph.add_node("stub", dev_agent.with_tools(["terminal", "file_editor"]), next_node="stub_review")
stub_graph.add_node("stub_review", review_agent, terminal=True)

graph.add_subgraph("stub_phase", stub_graph, next_node="human_approval")

# Human Gate
graph.add_node("human_approval", interrupt=True, next_node="implement")

# Phase 2: Implementation
graph.add_node("implement", dev_agent, next_node="review_impl")
graph.add_node("review_impl", review_agent, terminal=True)
```

**What's lost in translation:**
- The GitHub-native trigger model (issue comments → workflow execution)
- The natural approval channel (GitHub issue thread)
- The Git branch/PR lifecycle management
- The YAML config indirection (swapping providers/models without code changes)

**What's gained:**
- Typed state enforcement
- Faster in-process routing
- Built-in evaluation framework
- Dynamic programmatic branching (no YAML schema limitation)

---

## 10. When to Use Which

| Scenario | Prefer |
|----------|--------|
| GitHub-native SDLC, code repos | **git-flow-ai** |
| Product/service with user-facing agents | **Google ADK** |
| Team prefers config-over-code | **git-flow-ai** |
| Need typed state + evaluation pipelines | **Google ADK** |
| Want agents to read/write code via Git | **git-flow-ai** |
| Need sub-second inter-agent routing | **Google ADK** |
| Zero infra setup (runs on GitHub) | **git-flow-ai** |
| Deploy to GCP with autoscaling | **Google ADK** |

---

## 11. Assessment

This is not a competition between equivalents. **git-flow-ai is a DevOps-specific orchestration pattern** that leverages GitHub's existing infrastructure (Actions, issues, PRs, Git) as its runtime. **Google ADK is a general-purpose agent SDK** for building applications. The conversion above shows they can express similar workflows, but each is optimized for its domain.
