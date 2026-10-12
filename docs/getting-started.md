# Getting Started

Set up nebuko in your repository and run your first multi-agent workflow.
This page assumes you know basic GitHub Actions; for the underlying concepts,
read [architecture.md](./architecture.md) and
[workflows.md](./workflows.md).

---

## Prerequisites

1. **A GitHub Actions runner** — the orchestrator runs on GitHub-hosted
   runners by default (`runs-on: ubuntu-latest`), so no setup is required.
   To run on your own infrastructure instead, pass `runs_on: "self-hosted"`
   to the reusable workflow and [set up a self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners).
2. **Python 3.13** and the **`uv`** package manager on the runner.
3. **GitHub secrets** configured at repo or organization level (at least one
   provider key is required):

   | Secret | Required | Purpose |
   |--------|----------|---------|
   | `GH_PAT` | yes | Fine-grained GitHub PAT (Contents, Pull requests, Issues: read and write) — used for git push, issue/PR API calls, and posting comments. |
   | `OPENROUTER_API_KEY` | one of | LLM provider token (OpenRouter). |
   | `LOCAL_VLLM_API_TOKEN` | one of | Local vLLM provider token. |
   | `COMMANDCODE_API_KEY` | one of | CommandCode subscription ([subscription-plans.md](./subscription-plans.md)). |

4. **A configuration file** — `agent-config.yaml` (see step 2 below) and,
   optionally, prompt overrides (step 3), committed to your repository.

---

## Step 1 — Add the Workflow

Add the consumer entrypoint `.github/workflows/nebuko.yaml`. It is a thin wrapper
that calls the reusable orchestrator from this repository:

```yaml
uses: nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@main
```

Pin the reusable workflow with `@<sha1|tag>` (e.g. `…nebuko-workflow.yaml@v1.2`)
for a reproducible workflow version. When the workflow is pinned **and** it
operates on its own repository — the workflow file and the checked-out code live
in the same repo, e.g. you vendored `nebuko-workflow.yaml` into your repo — the
workspace checkout automatically follows the same commit as the pinned workflow,
so the workflow version and the code it runs against stay in lockstep (issue
#862). For a cross-repo consumer (`uses: nebuko/nebuko/…` from a separate
project repo) the checkout keeps the event default, since the workflow's commit
does not exist in your repo. Pass the `checkout_ref` input
(`with: { checkout_ref: <sha/tag/branch> }`) to override the workspace checkout
explicitly in any case.

Copy the **complete** file — the `on:` triggers, `permissions:`, and the
`with:`/`secrets:` blocks — from
[adoption.md — The entrypoint (copy-paste)](./adoption.md#3-the-entrypoint-copy-paste).
You never vendor the workflow file itself (zero-copy, see
[features.md — Remote Workflows](./features.md#remote-workflows-zero-copy)).
All inputs are optional and listed in
[adoption.md — Inputs](./adoption.md#inputs).

---

## Step 2 — Create `agent-config.yaml`

Commit a layered config (providers → llms → agents). Nebuko resolves it through
its layered lookup — a repo override under `.nebuko/agents/` first, then the
copy shipped in `defaults/agents/` — so add yours at
`.nebuko/agents/agent-config.yaml` (or set `agent_config_path` to wherever you
keep it). Minimal working example with 3 agents:

```yaml
providers:
  openrouter:
    api_url: "https://openrouter.ai/api/v1"
    api_token_env: "OPENROUTER_API_KEY"

llms:
  my-model:
    model_path: "openrouter/deepseek/deepseek-chat-v3-0324"
    provider: "${providers.openrouter}"
    default_temperature: 0.7

agents:
  architect:
    model: "${llms.my-model}"
    temperature: 1.0
    prompt_file: "defaults/prompts/architect.md"
    tools: ["think", "finish"]

  dev:
    model: "${llms.my-model}"
    prompt_file: "defaults/prompts/dev.md"
    tools: ["terminal", "file_editor", "task_tracker", "think", "finish"]

  review:
    model: "${llms.my-model}"
    prompt_file: "defaults/prompts/review.md"
    tools: ["think", "finish"]
```

For the full schema (all fields, `${ref}` rules, sampling parameters), see
[configuration.md](./configuration.md). You can start from this repository's
[`defaults/agents/agent-config.yaml`](../defaults/agents/agent-config.yaml) as a
reference.

Using a subscription plan instead of a metered API key? See
[subscription-plans.md](./subscription-plans.md) for the provider stanza, the
`COMMANDCODE_API_KEY` secret, and a working example config.

---

## Step 3 — Create Agent Prompts

Nebuko ships a system prompt per agent in `defaults/prompts/` and resolves each
prompt by file name through the layered lookup (`.nebuko/prompts/` override →
shipped `defaults/prompts/`), so the shipped prompts work with no local files at
all. To customize one, add a file with the same agent name under
`.nebuko/prompts/`:

```
.nebuko/prompts/
├── architect.md
├── dev.md
└── review.md
```

Each file describes the agent's role, responsibilities, output contract (how it
signals its dispatch with the typed `dispatch_next` / `call_agent` control-plane
tools — see [reference.md](./reference.md#agent_run_config)), and constraints.
You can start from the shipped prompts in this repository's
[`defaults/prompts/`](../defaults/prompts/) and refine them iteratively.

---

## Step 4 — First Trigger

1. Open an issue in your repository.
2. In the issue body (or a comment), type:

   ```
   @@architect <your request, e.g. "add input validation to the parser">
   ```

That's it. The trigger condition matches the `@@architect` mention, the
orchestrator runs, and the `default` workflow starts
(see [workflows.md — default](./workflows.md#default)).

---

## What to Expect

You'll see a sequence of comments on the issue:

1. **Acknowledgment** — a short bot comment confirming the run started (it is
   minimized after the agent responds).
2. **Agent responses** — one comment per agent step, each tagged with a
   `<!-- bot-agent-{name} -->` marker, ending with the next-step dispatch.
3. **Workflow state** — a collapsed JSON section
   (`<!-- WORKFLOW_STATE_START -->`) inside each comment so you can inspect
   exactly where the chain is.
4. **Result** — for the `default` workflow: analysis → context report → plan →
   implementation (with a PR opened by `@@dev`) → review verdict.

Where to look when something goes wrong:

- **GHA run logs** — the run for the triggering event, job `run-task`.
- **Run artifacts** — each run publishes `console.txt` and `app.txt` as separate unzipped artifacts (direct fetch, no unzip; the `.txt` extension renders inline in a browser).
- **Metrics** — every agent comment's `workflow_state` snapshot carries the
  per-step metrics in its `history` entries; on issue close, `agent-metrics`
  posts an aggregated table.
  Details in [features.md — Metrics](./features.md#metrics).

---

## Caller authorization (`CALLER_AUTH`)

By default (`CALLER_AUTH: on`) the workflow gates the trigger on the author's
repository association: only `OWNER`, `MEMBER`, or `COLLABORATOR` authors can
start a run. On a **public** repo this stops an outsider from starting a full
agent run and burning your LLM tokens.

The workflow's `if:` condition requires the triggering author's
`author_association` (from the GitHub event payload) to be one of
`OWNER`, `MEMBER`, or `COLLABORATOR`. Everything else is skipped before any
job, runner time, or token is spent — a denied comment never reaches the LLM.

Set the `CALLER_AUTH` input to `off` to allow *any* GitHub user to trigger the
workflow:

```yaml
- uses: nebuko/nebuko/.github/workflows/nebuko-workflow.yaml@main
  with:
    CALLER_AUTH: off
```

| `author_association` | `CALLER_AUTH: on` (default) |
|---|---|
| `OWNER`, `MEMBER`, `COLLABORATOR` | ✅ allowed |
| `NONE`, `FIRST_TIMER`, `FIRST_TIME_CONTRIBUTOR`, `MANNEQUIN` | ❌ skipped |
| missing / `null` | ❌ skipped (fail-closed) |

Notes and trade-offs:

- **`workflow_call` is exempt** — the association field is not present on
  `workflow_call` events, so programmatic/Actions triggers are unaffected.
- **`COLLABORATOR` includes read-only invites** — a user invited with
  read-only access can still trigger. If you want "write access or above",
  that needs a permission-API check (out of scope for this gate).
- **On org repos, `MEMBER` = any org member**, not necessarily a repo
  collaborator — slightly broader than strict "repo registered user".
- **Fail-closed by construction** — if `author_association` is ever missing,
  the `contains(...)` check evaluates false and the job simply doesn't start.

**Recommendation: keep `CALLER_AUTH: on` (the default) for public repos** to
prevent outsider token burn. Only set it to `off` for a private/trusted repo
where any GitHub user is intended to trigger runs.

---

## Troubleshooting

| Symptom | Checklist |
|---------|-----------|
| No response at all | If using a self-hosted runner (`runs_on: "self-hosted"`), is it online? Are the secrets set? Does the issue body contain a recognized trigger (`@@agent` or `/nebuko`)? Check the run's `if:` condition result in the GHA run. |
| Wrong agent ran | Check the routing cascade ([architecture.md](./architecture.md#routing-cascade)) and the `WORKFLOW_STATE` section of the triggering comment. Use `/nebuko run workflow --name <name>` or `/nebuko force-step step --name <step>` to correct course. |
| Stuck in a loop | The circuit breaker (default 15 bot markers) should have stopped it and posted a warning. Review the last few comments, then use `/nebuko resume` with instructions. See [features.md — Safety Mechanisms](./features.md#safety-mechanisms). |
| Agent ran but did nothing useful | Your prompts are too thin — give the agent an explicit role and output contract (step 3). |
| Config errors at startup | The workflow fails fast on undefined `${ref}`s or missing prompt files — read the *Resolve Agent Configuration* step log. |
