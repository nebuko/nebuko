# Configuration

The system is configured by a single layered YAML file (`agent-config.yaml`):
**providers → llms → agents**. Cross-layer `${ref}` resolution lets each layer
reference the one below it, so adding an agent is a short stanza with no other
file changes.

For the machine-readable dispatch overrides applied at invocation time, see
[reference.md — AGENT_RUN_CONFIG](./reference.md#agent_run_config).

---

## The 3-Layer Model

```
┌─────────────────────────────────────────────────────────────┐
│ agents        genius / dev / ctx / review / issue / ...     │
│               model: "${llms.qwen-3-6-27b}"                 │
└──────────────────────────────┬──────────────────────────────┘
                               │ ${ref}
┌──────────────────────────────▼──────────────────────────────┐
│ llms          qwen-3-6-27b / deepseek-v4-pro / ...          │
│               provider: "${providers.openrouter}"           │
└──────────────────────────────┬──────────────────────────────┘
                               │ ${ref}
┌──────────────────────────────▼──────────────────────────────┐
│ providers     openrouter / local_vllm                       │
│               api_url + api_token_env                       │
└─────────────────────────────────────────────────────────────┘
```

References resolve bottom-up: a provider has no dependencies; an LLM points at
a provider; an agent points at an LLM. A ref that does not resolve is an error
at load time (fail-fast, before any agent runs).

The config is loaded and validated by the `config/` package
(`load_config`, `load_workflow`, `resolve_refs` — see
[architecture_code.md](./architecture_code.md)). If `agent-config.yaml` is
absent, the workflow falls back to the legacy env-var-based approach for
backward compatibility.

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
| `input_price` / `output_price` | float | Cost per token (USD) — used for metrics/cost accounting. |
| `thinking_enabled` | bool | Enables the model's thinking mode (e.g., Qwen flexible thinking). |
| `preserve_thinking` | bool | Keeps thinking content across turns. |
| `max_output_tokens` | int | Max output tokens per completion. |
| `top_p` / `top_k` / `min_p` | float/int | Sampling parameters (see table below). |
| `presence_penalty` / `repetition_penalty` | float | Repetition control (see table below). |
| `stop_sequences` | string[] | Tokens that stop generation. |

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
| `temperature` | float | Per-agent temperature override (floor for complexity adjustment). |
| `prompt_file` | string | Path to the agent's system prompt (convention: `.agents/prompts/{agent}.md`). |
| `skills` | string[] | Skills attached to the agent by default. |
| `complexity_overrides` | mapping<`low` \| `medium` \| `high`, object> | Per-tier partial overrides of model/sampling/reasoning. See [Complexity Overrides](#complexity-overrides-per-tier-configuration). |
| `tools` | string[] | Tool whitelist: `terminal`, `file_editor`, `task_tracker` (SDK tools) plus `think`, `finish` (built-in defaults). See [agents-and-skills.md](./agents-and-skills.md). |

Example:

```yaml
agents:
  dev:
    model: "${llms.qwen-3-6-27b}"
    prompt_file: ".agents/prompts/dev.md"
    skills: []
    tools: ["terminal", "file_editor", "task_tracker", "think", "finish"]
```

---

## `${ref}` Cross-Layer Resolution

Syntax: `${layer.key}` where `layer` is `providers` or `llms`.

Rules:
- A value that is exactly a single ref (e.g., `"${llms.qwen-3-6-27b}"`) is
  replaced by the full referenced object (dict).
- Inline partial refs also work (e.g., `"prefix_${llms.x.model_path}_suffix"`):
  the ref path may descend into a field (`layer.key.subkey`), and its scalar
  value is substituted in place of the ref expression.
- Resolution is recursive and runs at load time; an undefined ref is a hard
  error at load time.
- Refs can only point at a *lower* layer (`agents → llms → providers`).

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
  genius:
    model: "${llms.qwen-3-6-27b}"      # → resolves to the qwen-3-6-27b LLM dict
    temperature: 1.0
    prompt_file: ".agents/prompts/genius.md"
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
  genius:
    model: "${llms.qwen-3-6-30b}"
    temperature: 1.0
    prompt_file: ".agents/prompts/genius.md"
    skills: []
    tools: ["think", "finish"]   # planners never touch files

  dev:
    model: "${llms.qwen-3-6-30b}"
    prompt_file: ".agents/prompts/dev.md"
    complexity_overrides:
      low:
        temperature: 0.0
      high:
        temperature: 0.9
        reasoning: "xhigh"
    tools: ["terminal", "file_editor", "task_tracker", "think", "finish"]
```

See this repo's [`agent-config.yaml`](../agent-config.yaml) for the full
9-agent configuration.

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
parameters per dispatch-declared `complexity` tier. This enables cost/quality
tiering — cheap settings for `low`, escalate to a stronger model for `high`.

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
> per-agent > per-LLM. Temperature remains a **floor** (`max`), so a tier never
lowers it below the base agent temperature, and `reasoning` only applies while
thinking is enabled for the LLM.
