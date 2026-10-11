# ADR-0014: Cache-Aware Session Cost and a Default Price Service

**Status:** Accepted — 2026-10-11
**Decider:** olivier-norture
**Related:** #714 / #721 (this decision), #651 (vLLM cache-hit rate), #700 (provider info in metrics), ADR-0001 (functional-DDD layout)

## Context

Cost metrics read `0` for subscription-backed providers (CommandCode, OpenCode):
the provider reports no per-token cost and the shipped agent-config sets no
`input_price`/`output_price`. Three structural gaps compounded the symptom:

1. **No default price source.** A model the operator never priced had no rate at
   all, so the token-count fallback multiplied by nothing. Hand-maintaining
   prices in four YAML files is exactly what #721 calls "horrible".
2. **A 2-rate model.** `SessionCost` priced only input and output tokens, so even
   a priced model ignored the cache-read/cache-write buckets the SDK already
   captures — a 5–10× overestimate on prompt-heavy cache usage.
3. **A discarded capture seam.** Our telemetry read a non-existent
   `cache_hit_tokens` field (always `0`) instead of the SDK's
   `cache_read_tokens`/`cache_write_tokens`, and the collector never fed cache
   buckets into cost.

The **OpenHands SDK v1.53** accepts only `input_cost_per_token` /
`output_cost_per_token` on `LLM`/`Telemetry` (extra fields ignored/forbidden) and
builds a two-key LiteLLM `CostPerToken`, even though LiteLLM itself honours
`cache_read_input_token_cost`/`cache_creation_input_token_cost`. Cache pricing
therefore cannot flow through the SDK in this version.

## Decision

**Our own `SessionCost` is the cost authority for priced sessions**, extended to
four rates, supported by a **default price service**.

### Precedence chain (the contract)

```
provider/SDK-reported cost (cache-aware)
  → local 4-rate math (per-field: agent-config > default price service)
  → 0.0 (documented fail-open)
```

Config overrides *estimates*; an actually-billed cost always wins. The SDK's
`accumulated_cost` is used only when it is `> 0`.

### Default price service

A new `pricing/` subsystem mirrors the `config/` exemplar: a `PriceTable`
pydantic entity plus two features (`load_price_table`, `resolve_model_rates`).
`load_price_table` fetches the OpenRouter `/models` table once, caches it under
`~/.nebuko/cache/prices/` (env `NEBUKO_PRICE_CACHE_DIR`, 24h TTL), and is
**fully fail-open** — fresh cache avoids the network, a fetch failure falls back
to a stale cache, and with no cache at all it returns `None` (the previous `0`
behaviour). Config and table prices are normalized to USD per 1M tokens and
merged **per field**, so a partial config is completed rather than discarded.

### Cost model semantics

- Cache-read and cache-write rates fall back to the input price when unset
  (LiteLLM-conservative; OpenRouter has `input_cache_write` for only 93/458
  models — none of ours).
- **Token-shape guard:** when `input_tokens ≥ cache_read + cache_write` the
  buckets are *inclusive* (subtract them to get regular input, the OpenAI
  shape); otherwise they are *exclusive* (price on top, the Anthropic shape).
  This is safe across both counting conventions.

### SDK price forwarding removed

The `input_cost_per_token`/`output_cost_per_token` forwarding in
`resolve_llm_config` is **deleted**. Because the SDK's custom-pricing math is
cache-blind, it would price cache tokens at the full input rate and its
`accumulated_cost` would *shadow* our accurate fallback with a large
overestimate. Provider-header costs are unaffected (they short-circuit before
custom pricing), and models present in LiteLLM's own map still get its
cache-aware map pricing.

## Alternatives considered

- **Forward cache prices through the SDK — rejected.** Not possible in v1.53
  (extra fields ignored/forbidden; only a two-key `CostPerToken` is built).
  Worth an upstream SDK issue.
- **Keep forwarding the two supported keys — rejected.** Cache-blind shadowing
  of the accurate fallback (see above).
- **Hand-maintained YAML prices — rejected.** #721's explicit anti-goal.
- **LiteLLM's cost map as the primary source — rejected.** All three CommandCode
  models are absent from it; it is the natural *secondary* source later.

## Consequences

- CommandCode/OpenCode costs become non-zero estimates out of the box, with zero
  hand-maintained config.
- `0` remains the outcome only when neither the config nor the service knows the
  model — still documented in `docs/subscription-plans.md`.
- Config gains `cache_read_price`/`cache_write_price` (USD per 1M tokens) and the
  `input_price`/`output_price` docs are corrected to "per 1M tokens".
- A small upstream-SDK issue should track forwarding cache-aware custom pricing.
