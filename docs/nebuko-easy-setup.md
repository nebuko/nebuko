# Nebuko easy setup (`nebuko init` and web onboarding)

Nebuko onboarding sets up everything a repository needs to run the Nebuko
workflow: an agent config at `.nebuko/agents/agent-config.yaml`, a pinned
workflow at `.github/workflows/nebuko.yaml`, and the provider API secrets
(plus `GH_PAT` in PAT mode). Secrets can be stored at **repository level** or
**organization level** (GitHub Actions org secrets).

## Overview

| Artifact | Destination |
|---|---|
| Agent config | `.nebuko/agents/agent-config.yaml` (single home; provider switching overwrites it, no stale per-provider files) |
| Workflow | `.github/workflows/nebuko.yaml` |
| Provider secrets | GitHub Actions secrets, repo or org scope |

The provider catalog is fixed: `openrouter`, `commandcode`, `opencode`,
`mistral`. Each provider ships a template (`defaults/agents/agent-config-<provider>.yaml`)
and a list of secret names. The template is the **fetch source only** — it is
fetched at the pinned ref and rendered to the single destination above.

## CLI usage

```
nebuko init [--provider NAME] [--scope repo|org] [--org NAME]
            [--force] [--pat-mode] [--non-interactive]
```

| Flag | Meaning |
|---|---|
| `--provider` | One of `openrouter` (default), `commandcode`, `opencode`, `mistral`. |
| `--scope` | `repo` (default) stores secrets on the repository; `org` stores them on the organization. |
| `--org` | Organization name; **required** when `--scope org`, and must equal the repo's owner. |
| `--force` | Overwrite existing config and existing secrets without asking. Skips the secret probe. Repo scope only. |
| `--pat-mode` | Use a personal access token (`GH_PAT`) instead of the GitHub App. |
| `--non-interactive` | Never prompt; fail instead of asking. Requires the provider's secret values in the environment. |

**Exit codes.** `0` success; `2` usage error — e.g. `--force` with `--scope org`
(non-empty org secrets cannot be force-updated; org secret updates are not
supported in this version, see #1315) — raised **before any prompt and before
any write**; `1` everything else.

**Conflicts and `--force`.** Before writing, init checks which target files and
secrets already exist. Without `--force`, existing files/secrets are reported
and (interactively) confirmed; declining aborts. With `--force` (repo scope),
existing config is overwritten and existing secrets are rewritten.

## Web onboarding usage

The Worker (Cloudflare) offers the same flow without a local CLI:

1. Visit **`/setup`** — you are redirected to the GitHub App installation /
   OAuth authorization page.
2. **`/auth/callback`** — the Worker receives the installation + OAuth result
   and establishes your session.
3. **Provider selection** — pick one of the four providers.
4. **Public-key fetch** — `GET /api/public-key?scope=&repo=&org=` returns the
   repository's (or organization's) actions public key. `org` is present only
   when `scope=org`; otherwise the request is rejected with `400`.
5. **Secret sealing** — your secret values are encrypted client-side with the
   public key (libsodium sealed boxes) and never leave the browser in
   plaintext. `GET /api/secrets?scope=&repo=&org=&provider=` lists which
   secrets already exist so they can be skipped.
6. **PR** — the Worker upserts the `nebuko/init` branch and opens (or reuses)
   a pull request carrying the agent config and the pinned workflow.

## How it works

- **Manifest.** A packaged JSON manifest (`nebuko-init.json`) describes the
  providers, the workflow filename (`nebuko.yaml`) and
  `caller_workflow_template` (`nebuko-caller.yaml`). The template path is
  relative to the manifest's own directory: the CLI resolves it against the
  packaged copy under `src/nebuko/engine/init/defaults/`; the Worker resolves
  it against the repo checkout at the resolved release tag. Provider
  `template_path` values stay repo-root-relative because they are always
  fetched at `pin_ref`. A drift-guard test
  (`tests/test_manifest.py::test_providers_match_providers_py`) pins the
  manifest's providers to `providers.py` verbatim.
- **Plan/execute split.** `plan_init` is a pure module-level function: given
  the request, the manifest, the templates and the fetched
  `existing_files`/`existing_secrets`, it produces an `InitPlan` — rendered
  files, per-secret actions (`create`/`update`/`skip_existing`) and conflicts.
  `execute_init` takes the plan, an I/O-only `GitHubInitClient` and the secret
  values, and never prompts or prints. Per-secret failures record
  `type(exc).__name__` in the status detail.
- **Single-home agent config (D1).** The rendered agent config lands at
  `.nebuko/agents/agent-config.yaml` regardless of provider.
- **Sealed secrets.** In web mode secrets are passed as `SealedSecret`
  (`encrypted_value` + `key_id`); in PAT mode the CLI passes plaintext values
  directly to `put_secret`.

## Troubleshooting

- **Secret listing fails** — listing existing secrets is authoritative
  (decision 9): a listing failure raises instead of guessing. Fix credentials
  (App private key, `GH_PAT`) and retry.
- **Existing-file conflicts** — re-run with `--force` (repo scope) or accept
  the interactive confirmation.
- **Provider drift** — if `providers.py` and the manifest disagree,
  `test_providers_match_providers_py` fails; regenerate the manifest.
- **Contributors** — mypy is not installed in the project venv; type checks
  are not part of this flow's gate.
