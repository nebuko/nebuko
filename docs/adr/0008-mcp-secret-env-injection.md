# ADR-0008: MCP Secret Injection via Declarative NAME/VALUE Slots

**Status:** Accepted — 2026-10-09
**Decider:** olivier-norture
**Related:** #834 (this change), #823 (search tool / MCP wiring), #832 (Brave Search MCP), `docs/adoption.md`

## Context

#823 wired MCP servers into Nebuko, so an agent can call an external tool (the
Brave Search MCP was the first). Such an MCP needs a **private token**, which
is sensitive and cannot live in the code or in a plain (`vars`) value.

Before this ADR, adding one meant editing the reusable workflow twice —
declaring the secret under `on.workflow_call.secrets` *and* threading it into
the engine step's `env:`. Every adopter who wanted a different MCP therefore had
to fork `nebuko-workflow.yaml`, stranding them on a stale copy of the engine and
re-introducing the exact problem the reusable workflow exists to avoid.

GitHub's reusable-workflow semantics constrain the possible fixes:

- A caller's workflow-level `env:` is **not** propagated into a called reusable
  workflow, and `with:` cannot reference `env` (`Unrecognized named-value:
  'env'`). A wrapper cannot "just inject env".
- `secrets: inherit` forwards *all* secrets, but only for **same
  organization/enterprise** callers — a dead end for public adopters.
- A `${{ toJSON(secrets) }}` blob would expose every secret (including the
  powerful `GH_PAT`) to the materializer step; filtering inside a script only
  limits *export*, not *exposure*, because the expression already materialized
  the full set before any code runs.
- Secret **values** must not travel through `with:` inputs: inputs land in
  `github.event.inputs`, which the engine serializes into agent context.

## Decision

**Declare nine generic, declarative NAME/VALUE secret slots on the reusable
workflow, and materialize the caller-mapped subset into env vars before the
engine step — with no `toJSON(secrets)` anywhere.**

- `on.workflow_call.inputs` gains `EXTRA_SECRET_1_NAME` … `EXTRA_SECRET_9_NAME`
  (`type: string`, `default: ''`): the **env var name** the MCP expects.
- `on.workflow_call.secrets` gains `EXTRA_SECRET_1_VALUE` …
  `EXTRA_SECRET_9_VALUE` (`required: false`): the **value**.
- `EXTRA_SECRET_n_NAME` (input) and `EXTRA_SECRET_n_VALUE` (secret) are linked
  by their index `n`; the pair is self-describing, so there is no remap grammar
  to learn or keep in sync.
- A new step, "Materialize extra secret slots as env vars", runs *before* "Run
  orchestration engine". Its environment holds **exactly the 18 generic slot
  refs** — `GH_PAT` and the named engine secrets never enter it. It invokes
  `python -m nebuko.engine.lib.secret_env`, which pairs the slots and appends
  `NAME<<DELIM…` heredoc blocks to `$GITHUB_ENV`.
- `SecretEnvironment` (`src/nebuko/engine/lib/secret_env.py`, frozen dataclass)
  owns the pairing: it drops empty names/values, drops reserved names (exact
  `GITHUB_TOKEN`/`GH_TOKEN`/`PATH`/`HOME`/… and the `GITHUB_*`/`ACTIONS_*`/
  `RUNNER_*` prefixes), and keeps the first populated slot on a duplicate name.
  It logs names and counts only — never values.

The caller recipe becomes two declarative lines per token, in the caller's own
entrypoint:

```yaml
    with:
      EXTRA_SECRET_1_NAME: "BRAVE_API_KEY"
    secrets:
      EXTRA_SECRET_1_VALUE: ${{ secrets.BRAVE_API_KEY }}
```

Existing named wiring (`GITHUB_TOKEN ← GH_PAT`, `GH_TOKEN`,
`LOCAL_VLLM_API_TOKEN`) is untouched. The named `BRAVE_API_KEY` alias is moved
from the engine step's `env:` to the **job-level `env:`** so that slot
materialization (which updates `$GITHUB_ENV` for subsequent steps) can fill or
override it: a step-level `env:` binding shadows `$GITHUB_ENV` and would clobber
the materialized value when the named secret is absent.

## Consequences

- **No fork to add an MCP.** Adding a token = add a repository secret + two
  lines in the caller entrypoint. `nebuko-workflow.yaml` does not change.
- **Uniform across callers.** The same recipe works same-organization and
  cross-organization; `secrets: inherit` is not required.
- **Least-privilege materializer.** Only the nine generic slots reach the
  materialize step's environment; `GH_PAT` never does.
- **Capacity is fixed at nine** (all single-digit indices). Raising it later is
  an additive upstream change (`EXTRA_SECRET_10…`) that pinning callers receive
  automatically via `@main`.
- **Precedence rule.** A slot-materialized value (written to `$GITHUB_ENV`)
  overrides the same-named job-level alias — this is why the named
  `BRAVE_API_KEY` alias lives at job level. A name the engine step binds
  explicitly (`GITHUB_TOKEN`, `GH_TOKEN`, `LOCAL_VLLM_API_TOKEN`) shadows
  `$GITHUB_ENV`, so a slot mapped onto such a name has no effect; map slots to
  names the step does not bind.
- **Values stay in the `secrets:` channel**, never the inputs channel, so they
  never reach agent context through `github.event.inputs`.

## Alternatives rejected

- **`secrets: inherit` only** — same-organization/enterprise callers only; a
  dead end for public adopters.
- **Export-all-secrets** — materializes every secret, including `GH_PAT`, into
  the env of semi-trusted MCP servers and child agents: a least-privilege
  regression.
- **`toJSON(secrets)` blob (filtered or not)** — the blob is materialized before
  any filter runs, so filtering limits export, not exposure.
- **`secret_env_map` remap grammar** — an extra artifact to parse and keep in
  sync with the slot assignments; rejected in favor of the self-describing
  NAME/VALUE pair.
- **A JSON value blob through an input** — secret values would land in
  `github.event.inputs`, which the engine serializes into agent context.
- **Copy `nebuko-workflow.yaml` per adopter** — forks the engine and strands
  adopters on a stale version, the very pain this ADR removes.
