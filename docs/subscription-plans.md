# Subscription Plans (CommandCode, OpenCode Go)

Run git-flow-ai on a **subscription plan** instead of a metered API key. If you
already pay for a coding subscription, pointing the agents at it lowers the
barrier to adoption — the same economics that make Claude Code attractive: a
flat monthly fee instead of per-token billing.

This page documents:

- **CommandCode** — works today, using the root
  [`agent-config-commandcode.yaml`](../agent-config-commandcode.yaml) as the
  template.
- **OpenCode Go** — **not wired yet**; documented so the same pattern is ready
  when it lands.

> **Scope:** documentation only. No runtime or schema changes — a subscription
> reaches the agents through the same `providers → llms → agents` config as any
> other backend.

---

## The concept: subscription backend vs. metered API

Every provider is just an **OpenAI-compatible endpoint + an auth token read from
an environment variable**:

```yaml
providers:
  commandcode:
    api_url: "https://api.commandcode.ai/provider/v1"
    api_token_env: "COMMANDCODE_API_KEY"
```

A subscription provider differs from a metered one only in **who pays** and
**how the key is obtained**: you subscribe on the provider's site, copy an API
key, store it as a repository secret, and point the config at the subscription
endpoint. The agent runtime is unchanged.

One trade-off to keep in mind: the subscription's usage limits become **shared
with the automation** (see [Limitations](#limitations)).

---

## CommandCode (works today)

### 1. Pick a plan with API access

CommandCode's **Go plan has no API access** — you need **GOAT or higher**
(Pro, Max, Team, Provider). The GOAT plan ($10/month, ≈$70 of credits) covers
the models used by the example config, including `deepseek-v4.1-flash` and
`glm-5.3-flash`. See the
[CommandCode Provider API docs](https://commandcode.ai/docs/provider) and the
[GOAT plan details](https://commandcode.ai/docs/plans/goat).

### 2. Get an API key

Subscribe, then create your API key from CommandCode Studio. The **same key
authenticates the CLI and the API**.

### 3. Store the secret

Add a repository (or organization) secret named `COMMANDCODE_API_KEY` and pass
it to the reusable workflow:

```yaml
jobs:
  agent:
    uses: olivier-norture/git-flow-ai/.github/workflows/openhands-multi-agent.yml@main
    with:
      agent_config_path: "agent-config-commandcode.yaml"
    secrets:
      COMMANDCODE_API_KEY: ${{ secrets.COMMANDCODE_API_KEY }}
      GH_PAT: ${{ secrets.GH_PAT }}
```

`COMMANDCODE_API_KEY` is an optional secret of the reusable workflow (like
`OPENROUTER_API_KEY`); it is forwarded to the run as an environment variable,
which is exactly what `api_token_env` resolves.

### 4. Point the config at the subscription

Copy this repo's
[`agent-config-commandcode.yaml`](../agent-config-commandcode.yaml) to your repo
root and keep the provider stanza:

```yaml
providers:
  commandcode:
    api_url: "https://api.commandcode.ai/provider/v1"
    api_token_env: "COMMANDCODE_API_KEY"

llms:
  deepseek-v4_1-flash:
    model_path: "openai/deepseek/deepseek-v4.1-flash"
    provider: "${providers.commandcode}"
```

Then reference the LLM from each agent exactly as in the full file. The
[configuration reference](./configuration.md) covers the schema.

### 5. Verify it is working

- The run's *Resolve Agent Configuration* step should log
  `provider=https://api.commandcode.ai/provider/v1` and the model you chose.
- If the key is missing the run fails fast with
  `LLM_API_KEY environment variable is not set.` (the `api_token_env` variable
  was empty and no fallback key was available).
- A quick sanity call from your machine:

  ```bash
  curl https://api.commandcode.ai/provider/v1/chat/completions \
    -H "Authorization: Bearer $COMMANDCODE_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"deepseek/deepseek-v4-flash","messages":[{"role":"user","content":"ping"}]}'
  ```

---

## OpenCode Go (planned — not wired yet)

OpenCode Go is the parallel case: a subscription that fronts open coding models.
**It is not wired in this repository yet**, but it will have the **same shape**
as the CommandCode config — one provider entry plus one LLM entry. Treat the
section below as the target configuration, not a working setup.

- **Plans:** Go ($10/month) and Go Plus ($40/month).
- **Endpoint (OpenAI-compatible):**
  `https://opencode.ai/zen/go/v1/chat/completions`.
- **Key:** subscribe in the OpenCode Console and copy the API key.

The target config would look like:

```yaml
# Not active yet — shown for shape parity with CommandCode.
providers:
  opencode_go:
    api_url: "https://opencode.ai/zen/go/v1"
    api_token_env: "OPENCODE_GO_API_KEY"

llms:
  some-go-model:
    model_path: "openai/<model-id>"
    provider: "${providers.opencode_go}"
```

> **Client-identity requirement:** OpenCode Go is designed for coding agents and
> monitors traffic for abuse. Clients should identify themselves with their own
> user agent and send a stable `x-opencode-session` header per conversation.
> This tool does not do that today, so OpenCode Go is documented as a future
> option rather than a supported one — see the
> [OpenCode Go docs](https://opencode.ai/docs/go/).

Once a subscription's limits are reached, the metered **OpenCode Zen** provider
(`https://opencode.ai/zen/v1/chat/completions`) is the drop-in fallback — see the
[OpenCode Zen docs](https://opencode.ai/docs/zen/).

---

## Limitations

- **Subscriptions are not unlimited.** CommandCode and OpenCode both apply
  rolling 5-hour and weekly windows on top of the monthly credit pool. Because
  CI automation draws on the same pool, a busy agent day can eat into the
  allowance you wanted for interactive use. See
  [CommandCode pricing & limits](https://commandcode.ai/docs/resources/pricing-limits).
- **CommandCode's Go plan has no API access** — use GOAT or higher.
- **Cost metrics read `0` unless prices are set.** A subscription is flat-rate,
  so there is no per-token price to report. `input_price` / `output_price` in
  the `llms:` stanza drive the cost columns; leave them unset and the metrics
  report token counts with zero cost. See
  [features.md — Metrics](./features.md#metrics--summarization).
- **OpenCode Go is not wired yet** — see the client-identity requirement above.

---

## See also

- [configuration.md](./configuration.md) — provider / LLM / agent schema.
- [getting-started.md](./getting-started.md) — secrets and first run.
- [`agent-config-commandcode.yaml`](../agent-config-commandcode.yaml) — the
  working CommandCode example.
