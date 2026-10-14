# Configuration

The system is configured by a single layered YAML file (`agent-config.yaml`):
**providers → llms / mcp_servers → agents**. Cross-layer `${ref}` resolution lets each layer
reference the one below it, so adding an agent is a short stanza with no other
file changes.

For the machine-readable dispatch overrides applied at invocation time, see
[reference.md — AGENT_RUN_CONFIG](./reference.md#agent_run_config).

---

## The 3-Layer Model

```
┌─────────────────────────────────────────────────────────────┐
│ agents        architect / dev / context / review / issue / ...     │
│               model: "${llms.qwen-3-6-27b}"                 │
└──────────────────────────────┬──────────────────────────────┘
                               │ ${ref}
┌──────────────────────────────▼──────────────────────────────┐
│ llms          qwen-3-6-27b / deepseek-v4-pro / ...          │
│               provider: "${providers.openrouter}"           │
│ mcp_servers   brave-search / ...  (agent opt-in via ${ref}) │
└──────────────────────────────┬──────────────────────────────┘
                               │ ${ref}
┌──────────────────────────────▼──────────────────────────────┐
│ providers     openrouter / local_vllm                       │
│               api_url + api_token_env                       │
└─────────────────────────────────────────────────────────────┘
```

References resolve bottom-up: a provider has no dependencies; an LLM points at
a provider; a `mcp_servers` catalog entry is standalone; an agent points at an
LLM (and, optionally, at one or more catalog entries). A ref that does not
resolve is an error at load time (fail-fast, before any agent runs).

The config is loaded and validated by the `config/` package
(`load_config`, `load_workflow`, `resolve_refs` — see
[architecture_code.md](./architecture_code.md)). `agent-config.yaml` is the
**sole** source of LLM configuration: the workflow no longer reads the legacy
`LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` / `LLM_TEMPERATURE` environment
variables. If the file is missing, a role is absent, or a provider's
`api_token_env` is unset, the run fails fast with an actionable
`LLMConfigError` naming the path (or the env var to set).

### Where a relative `AGENT_CONFIG_PATH` resolves

`AGENT_CONFIG_PATH` (the workflow's `agent_config_path` input) is relative to
the **caller's repository** — the checked-out workspace, not the Nebuko
checkout. The base is `$GITHUB_WORKSPACE` when set, otherwise the working
directory the engine started in. An absolute value is used unchanged, and a bare
file name (no separator) is looked up in `<base>/.nebuko/agents/` first, then in
Nebuko's shipped `defaults/agents/`. This is what makes a cross-repo
`uses: nebuko/nebuko/...` caller's `.nebuko/agents/agent-config.yaml` resolve
against the caller's own workspace rather than the imported engine checkout.

---

## Provider Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `api_url` | string | yes | Chat-completions endpoint URL, or the name of an environment variable holding that URL (e.g. `LOCAL_VLLM_API_URL`), resolved at config-load time — mirrors `api_token_env`. |
| `api_token_env` | string | yes | Name of the environment variable holding the API token. |

```yaml
providers:
  openrouter:
    api_url: "https://openrouter.ai/api/v1"
    api_token_env: "OPENROUTER_API_KEY"
  local_vllm:
    # api_url may also be an env var name (resolved at load time):
    api_url: "LOCAL_VLLM_API_URL"
    api_token_env: "LOCAL_VLLM_API_TOKEN"
```

---

## LLM Schema

| Field | Type | Description |
|-------|------|-------------|
| `model_path` | string | Model identifier on the provider (e.g., `openrouter/anthropic/claude-sonnet-5`). |
| `provider` | `${ref}` | Provider entry from the `providers:` layer. |
| `default_temperature` | float | Base temperature for the model. |
| `input_price` / `output_price` | float | Cost per **1M tokens** (USD) — seeds metrics/cost accounting. |
| `cache_read_price` / `cache_write_price` | float | Cost per **1M tokens** (USD) for cached prompt tokens; falls back to `input_price` when unset. |
| `thinking_enabled` | bool | Enables the model's thinking mode (e.g., Qwen flexible thinking). |
| `preserve_thinking` | bool | Keeps thinking content across turns. |
| `max_output_tokens` | int | Max output tokens per completion. |
| `top_p` / `top_k` / `min_p` | float/int | Sampling parameters (see table below). |
| `presence_penalty` / `repetition_penalty` | float | Repetition control (see table below). |
| `stop_sequences` | string[] | Tokens that stop generation. |

> **Cost precedence.** A provider/SDK-reported cost (a genuinely billed amount,
> cache-aware) always wins over local estimation, so for a model litellm already
> knows the SDK's cost is used and these configured prices do **not** override
> it. The `*_price` fields only seed the local four-tier estimate, which applies
> when no provider/SDK cost is reported — e.g. a subscription backend, or a
> self-hosted model. Where the estimate applies, each tier falls back per field
> to the default price service (an OpenRouter-backed table; see
> [features.md — Metrics](./features.md#metrics)) and then to `input_price`.
>
> The issue-close **`agent-metrics` job** additionally fills entries whose
> recorded cost is still `0` at report time, pricing their stored tokens
> against that same default price service by the model identity each run
> recorded (issue #928). The job has no agent-config, so this report-time
> fill uses the table only; configured `*_price` precedence applies at run
> time (the local estimate written into state). State is never rewritten —
> rerunning the job recomputes.

Sampling parameters and their typical ranges:

| Parameter | Type | Description | Typical range |
|-----------|------|-------------|---------------|
| `top_p` | float | Nucleus sampling — lower = more deterministic | 0.1–1.0 |
| `top_k` | int | Limits the candidate token pool | 10–100 |
| `min_p` | float | Filters tokens below an absolute probability threshold | 0.0–1.0 |
| `presence_penalty` | float | Discourages repeating tokens based on presence | -2.0–2.0 |
| `repetition_penalty` | float | > 1.0 discourages repetition | 1.0–1.5 |

> **Note:** `presence_penalty` must match the model's operating mode: `0.0` for
> thinking-mode operation, ~`1.5` for instruct-only (per official Qwen model card).
> PR #225 originally set 1.5 on the Qwen entries while they ran with
> `thinking_enabled: true`, i.e., under the wrong regime.

Example:

```yaml
llms:
  claude-sonnet-5:
    model_path: "openrouter/anthropic/claude-sonnet-5"
    provider: "${providers.openrouter}"
```

---

## Agent Schema

| Field | Type | Description |
|-------|------|-------------|
| `model` | `${ref}` | LLM entry from the `llms:` layer. |
| `role` | string | One-line role description, rendered into every other agent's "Your Team" section. Optional; a missing role renders a placeholder and raises the `C8-missing-role` lint warning. |
| `temperature` | float | Per-agent temperature override (floor for complexity adjustment). |
| `prompt_file` | string | Path to the agent's system prompt (convention: `defaults/prompts/{agent}.md`). Only the basename is used at run time — the layered lookup re-resolves it, so deployments can override a prompt by name. |
| `skills` | string[] | Skills attached to the agent by default. |
| `complexity_overrides` | mapping<`low` \| `medium` \| `high`, object> | Per-tier partial overrides of model/sampling/reasoning. See [Complexity Overrides](#complexity-overrides-per-tier-configuration). |
| `tools` | string[] | Tool whitelist: `terminal`, `file_editor`, `task_tracker` (SDK tools) plus `think`, `finish`, `dispatch_next` (built-in defaults). See [agents-and-skills.md](./agents-and-skills.md). |
| `mcp_servers` | object[] | MCP servers attached to the agent's conversation. Each entry is either an inline spec or a `${mcp_servers.<name>}` ref into the root catalog (see [MCP Servers](#mcp-servers)). |
| `token_budget` | object | Compaction / prompt-leanness policy (see [Token Budget](#token-budget)). |

Example:

```yaml
agents:
  dev:
    model: "${llms.qwen-3-6-27b}"
    role: "Implementation engineer — writes code, runs tests, manages branches, commits, and PRs."
    prompt_file: "defaults/prompts/dev.md"
    skills: ["posting-contract", "git-pr-workflow"]
    tools: ["terminal", "file_editor", "task_tracker", "think", "finish", "dispatch_next"]
```

---

## Token Budget

`token_budget` controls how an agent's context is compacted and how lean its
per-call prompt surface is (issue #851). Compaction is a **late safety valve**
that runs on the agent's own model: it is never triggered early by a small
event count, and the summarizer's spend is metered under `usage_id="condenser"`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `compaction_max_size` | int \| null | `240` | Event count that triggers compaction. |
| `compaction_max_tokens` | int \| null | 80% of the route input limit | Token count that triggers compaction. |
| `compaction_keep_first` | int | `4` | Leading events compaction always preserves. |
| `compaction_model` | string \| null | agent's model | Summarizer model (default shares the conversation's prompt cache). |
| `prompt_profile` | `full` \| `lean` | `full` | `lean` trims per-call tool/prompt overhead. |
| `require_batched_recon` | bool | `false` | Require reconnaissance to be batched into single multi-action turns. |

Example:

```yaml
agents:
  dev:
    model: "${llms.qwen-3-6-27b}"
    token_budget:
      prompt_profile: "lean"
      require_batched_recon: true
```

---

## MCP Servers

`mcp_servers` attaches [Model Context Protocol](https://modelcontextprotocol.io)
servers to an agent's conversation. Each entry is a stdio process spec; the
server's tools become available to the agent alongside its `tools` whitelist.

There are two levels, mirroring `providers`/`llms`:

- **Root catalog** — a top-level `mcp_servers:` mapping, keyed by server name,
  holding the reusable server specs. The key **must equal** the entry's `name`.
- **Agent opt-in** — each agent's `mcp_servers:` list is a list of refs into the
  catalog (`- "${mcp_servers.<name>}"`). Inline specs are still accepted for a
  genuinely one-off server.

A ref resolves to the referenced entry dict at load time, so the runtime wiring
is unchanged: the agent still ends up with plain `MCPServerConfig` instances.

Catalog/server spec fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Stable handle for the server (must equal its catalog key). |
| `command` | string | Executable to launch (`npx`, `uvx`, `docker`, or a binary path). |
| `args` | string[] | Command arguments (default `[]`). |
| `env` | mapping<string, string> | Child-process env var → **name** of the OS env var holding the value (never the literal secret). |
| `transport` | string | Always `stdio` for now (default `stdio`). |

Unresolved `env` references (the named OS env var is unset) cause the server to
be skipped with a warning — a missing key degrades that one capability rather
than failing the run.

Example — one Brave Search MCP definition reused by the `context` agent:

```yaml
mcp_servers:
  brave-search:
    name: "brave-search"
    command: "npx"
    args:
      - "-y"
      - "@brave/brave-search-mcp-server@2.1.4"
      - "--enabled-tools"
      - "brave_web_search"
      - "brave_news_search"
    env:
      BRAVE_API_KEY: "BRAVE_API_KEY"   # child var ← OS env var name

agents:
  context:
    tools: ["terminal", "file_editor", "task_tracker", "think", "finish"]
    mcp_servers:
      - "${mcp_servers.brave-search}"
```

Any number of agents can reference the same catalog entry. Set `BRAVE_API_KEY`
in the environment (or as a CI secret) to enable it.

---

## `${ref}` Cross-Layer Resolution

Syntax: `${layer.key}` where `layer` is `providers`, `llms`, or `mcp_servers`.

Rules:
- A value that is exactly a single ref (e.g., `"${llms.qwen-3-6-27b}"`) is
  replaced by the full referenced object (dict).
- Inline partial refs also work (e.g., `"prefix_${llms.x.model_path}_suffix"`):
  the ref path may descend into a field (`layer.key.subkey`), and its scalar
  value is substituted in place of the ref expression.
- Resolution is recursive and runs at load time; an undefined ref is a hard
  error at load time.
- Refs can only point at a *lower* layer (`agents → llms / mcp_servers → providers`).

Inline partial example:

```yaml
agents:
  dev:
    model: "${llms.qwen-3-6-27b}"       # full-string ref → the whole LLM dict
    # A hypothetical inline form would be written as:
    # model_tag: "gfa-${llms.qwen-3-6-27b.model_path}"
    # → resolves to "gfa-hosted_vllm/qwen-3-6-27b"
```

Chain example (as found in this repo's `agent-config.yaml`):

```yaml
agents:
  architect:
    model: "${llms.qwen-3-6-27b}"      # → resolves to the qwen-3-6-27b LLM dict
    temperature: 1.0
    prompt_file: "defaults/prompts/architect.md"
    tools: ["think", "finish"]

llms:
  qwen-3-6-27b:
    model_path: "hosted_vllm/qwen-3-6-27b"
    provider: "${providers.local_vllm}"   # → resolves to the local_vllm provider dict
    default_temperature: 0.7

providers:
  local_vllm:
    api_url: "http://192.168.1.64:31728/v1"
    api_token_env: "LOCAL_VLLM_API_TOKEN"
```

---

## Full Annotated Example (Abbreviated)

```yaml
# ── Layer 1: Providers — API endpoints + auth env var name ──
providers:
  openrouter:
    api_url: "https://openrouter.ai/api/v1"
    api_token_env: "OPENROUTER_API_KEY"

# ── Layer 2: LLMs — models referencing providers, with sampling params ──
llms:
  qwen-3-6-30b:
    model_path: "hosted_vllm/qwen-3-6-30b-a3b"
    provider: "${providers.local_vllm}"
    default_temperature: 1.0
    thinking_enabled: true
    max_output_tokens: 32768
    top_p: 0.95
    top_k: 20

# ── Layer 3: Agents — role → model + prompt + tools ──
agents:
  architect:
    model: "${llms.qwen-3-6-30b}"
    temperature: 1.0
    prompt_file: "defaults/prompts/architect.md"
    skills: []
    tools: ["think", "finish"]   # planners never touch files

  dev:
    model: "${llms.qwen-3-6-30b}"
    prompt_file: "defaults/prompts/dev.md"
    complexity_overrides:
      low:
        temperature: 0.0
      high:
        temperature: 0.9
        reasoning: "xhigh"
    tools: ["terminal", "file_editor", "task_tracker", "think", "finish"]
```

See this repo's [`agent-config.yaml`](../defaults/agents/agent-config.yaml) for the full
5-agent configuration.

---

## Runtime Override

Per-invocation dispatch fields override the YAML cascade. Precedence
(highest first):

1. **Per-invocation** — dispatch fields in the `AGENT_RUN_CONFIG` sentinel
   (`model`, `temperature`, `top_p`, `top_k`, `min_p`, `presence_penalty`,
   `repetition_penalty`, `complexity`).
2. **Complexity tier override** — `agents.<name>.complexity_overrides.<tier>`
   for the dispatch `complexity` tier (`low`/`medium`/`high`). See
   [Complexity Overrides](#complexity-overrides-per-tier-configuration).
3. **Per-agent** — `agents.<name>` stanza in `agent-config.yaml`.
4. **Per-LLM** — `llms.<name>` stanza in `agent-config.yaml`.

The `complexity` tier used for a run is resolved as **explicit dispatch
`complexity` > `steps[].complexity` (workflow YAML) > `"medium"`** (issue
#787) and exported as `CONFIG_COMPLEXITY`. A workflow step can therefore
declare a default tier for its own invocation — see
[workflows.md](./workflows.md#per-step-complexity).

### Dynamic Complexity-Based Temperature

When `complexity` is set in the dispatch and no explicit `temperature` override
is provided, temperature is auto-adjusted:

| Complexity | Temperature |
|------------|-------------|
| `low` | 0.0 (most deterministic) |
| `medium` | 0.1 |
| `high` | 0.3 (more creative) |

The agent's base temperature from `agent-config.yaml` serves as a **floor** —
complexity only increases temperature, never decreases it.

**Reasoning effort** is independent of `complexity`: the `reasoning` dispatch
field (`none`/`low`/`medium`/`xhigh`) maps to the provider's thinking-mode
parameter and only applies while `thinking_enabled` is true for the LLM.

### Complexity Overrides (per-tier configuration)

An agent can swap its model, temperature, reasoning effort, and sampling
parameters per resolved `complexity` tier — an explicit dispatch `complexity`,
else the workflow step's `steps[].complexity`, else `medium`. This enables
cost/quality tiering — cheap settings for `low`, escalate to a stronger model
for `high`.

Keys are exactly `low`, `medium`, or `high`; each value is a **partial**
override reusing the agent-stanza field names (`model`, `temperature`,
`reasoning`, `top_p`, `top_k`, `min_p`, `presence_penalty`,
`repetition_penalty`). Unset fields fall through to the per-agent value.

```yaml
agents:
  dev:
    model: "${llms.qwen-3-6-27b}"
    temperature: 0.7
    complexity_overrides:
      low:
        temperature: 0.0
      high:
        model: "${llms.claude-sonnet-5}"
        temperature: 0.9
        reasoning: "xhigh"
        top_p: 0.8
```

Precedence is per-invocation (`CONFIG_*` / `AGENT_RUN_CONFIG`) > tier override
> per-agent > per-LLM. A tier `temperature` **replaces** the agent's base
temperature, and the per-tier floor from
[Dynamic Complexity-Based Temperature](#dynamic-complexity-based-temperature)
is then applied on top with `max()`; so a `high` override of `0.1` still
resolves to `0.3`. `reasoning` only applies while thinking is enabled for the
resolved model.

### Repo Complexity Policy (`complexity_policy`)

The app config may carry an optional **repo layer** of the complexity policy —
the same config source that already carries `complexity_overrides`. It merges on
top of the builtin layer (`builtin` -> `repo`); unset sections inherit the lower
layer. The resolved policy is pinned into run state for auditability.

```yaml
complexity_policy:
  scope: repo
  target_distribution: { low: 0.70, medium: 0.25, high: 0.05 }
  max_auto_delta: 1
  auto_adjust: true
  # Optional: retune the signal thresholds the governor evaluates.
  signal_rules:
    - signal: multi-issue-linkage
      family: context
      field: linked_issues
      threshold: 2
      direction: gte
```

The base tier chain is unchanged (`dispatch > step > "medium"`, ADR-0005). On top
of it the `ComplexityGovernor` overlays a clamped drift adjustment
(`effective = clamp(base + delta)`, `|delta| <= max_auto_delta`), computed from a
typed `EvidenceBundle` (LLM metrics + human signals + context signals). Agents
may request a re-tier via the `complexity_review` tool; the request requires a
populated `evidence` bundle and a non-blank `feeling`. The drift check is
evaluated against the recorded *base* tier, so sustained evidence yields the same
clamped correction each boundary (no ratchet) and decays once evidence stops.
The `context` signal family (`linked_issues`, `reopened`) reads an optional
`COMPLEXITY_CONTEXT_SIGNALS` blob from the intake surface; no call-site publishes
it yet, so those rules stay dormant until a publisher lands. See
[ADR-0012](./adr/0012-complexity-governance.md).
