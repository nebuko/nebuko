# `nebuko init` — manifest + core refactor (issue #1272)

How the `nebuko init` command is used after the refactor, and how the new
`manifest` and `core` modules work. This replaces the broker-based flow
described in `docs/nebuko-init.md`.

---

## How to use

```bash
nebuko init                      # interactive
NEBUKO_SECRET_OPENROUTER_API_KEY=... \
  nebuko init --repo acme/widgets --provider openrouter --non-interactive
nebuko init --force              # rewrite existing files and secrets
```

The interactive flow asks, in order:

1. **Target scope** — repository secrets or organization secrets. Org scope
   requires `--org` (or prompts for the org name); without it `init` refuses
   to plan.
2. **Provider** — one of the four shipped profiles (`openrouter`,
   `commandcode`, `opencode`, `mistral`), read from the packaged manifest.
3. **Secret values** — each secret the chosen provider needs is prompted for
   unless it already exists at the target (then it is skipped, or rewritten
   with `--force`) or is supplied via `NEBUKO_SECRET_<NAME>`.
4. **PAT vs web mode** — in PAT mode the client is asked for a GitHub personal
   access token and `GH_PAT` is stored alongside the provider secrets. In web
   (App) mode no PAT is collected and `GH_PAT` is **not** written.

`--force` rewrites files and secrets that already exist instead of skipping
them; without it, existing target files are reported as conflicts and `init`
asks before overwriting.

## Behaviour changes

- `init` **no longer writes a local workflow file** — the caller workflow
  comes from the packaged manifest (`nebuko-caller.yaml`) and is committed to
  the `nebuko/init` branch.
- `--broker-url`, the broker repository variable (`NEBUKO_BROKER_URL`) and the
  whole `RepoVariableWriter` path are **removed**.
- `GH_PAT` is set **only in PAT mode** — never in web mode.
- Org-scope targets **require `--org`**; a missing org is a hard error.

## Manifest schema

The packaged manifest (`nebuko.engine.init.manifest`) is JSON with:

| Field | Meaning |
|---|---|
| `schema_version` | integer schema version |
| `workflow_filename` | always `"nebuko.yaml"` |
| `caller_workflow_template` | e.g. `"nebuko-caller.yaml"`, **relative to the manifest's own directory** |
| `providers` | map of provider name → `{description, template_path, secret_names}` |

`parse_manifest` validates strictly and raises `ManifestError` on unknown
fields or an empty `providers` map. Path resolution rules:

- `caller_workflow_template` is resolved relative to the manifest's directory:
  both the manifest (`nebuko-init.json`) and the caller template
  (`nebuko-caller.yaml`) live in `src/nebuko/engine/init/defaults/` — the repo
  file *is* the packaged copy. The CLI resolves both through
  `importlib.resources.files("nebuko.engine.init.defaults")`; the Worker
  fetches `src/nebuko/engine/init/defaults/nebuko-init.json` at the resolved
  release tag and resolves the template as a sibling at the same ref. On 404
  (any tag cut before PR 1 merged) the Worker returns a clear error naming the
  resolved tag — never a silent fallback. **PR 2 gate:** the first release tag
  containing `defaults/` must exist before PR 2 step 10 ships.
- `template_path` stays **repo-root-relative** (e.g.
  `defaults/agents/agent-config-openrouter.yaml`) because it is always fetched
  at `pin_ref` — never packaged.

The manifest is a static, committed file (not generated from `providers.py` at
build time): the Worker fetches raw JSON at runtime with no Python in its
build, and a generated manifest would still need an equality check in CI. The
drift guard
`tests/engine/init/test_manifest.py::test_providers_match_providers_py`
imports `PROVIDER_PROFILES` and fails on any divergence, and stays in force
until `providers.py` is deleted (at which point the manifest becomes the sole
source).

## How it works

The engine splits planning from I/O:

- **`plan_init(request, manifest, caller_template, agent_config,
  existing_files, existing_secrets)`** — a pure module-level function. It
  renders the agent config and `.github/workflows/nebuko.yaml`, computes
  `secret_actions` (exactly the chosen provider's `secret_names`, plus
  `GH_PAT` only when `pat_mode=True`), and records `conflicts` for target
  files that already exist when `force=False`. Per-name action:
  `skip_existing` if the name is in `existing_secrets`, else `create`;
  upgraded to `update` for existing names when `force=True`. Raises
  `ValueError` for an org-scope target without an org.
- **`execute_init(plan, client, secrets)`** — module-level, takes the
  `GitHubInitClient` as an argument, never prompts or prints. It force-updates
  branch `nebuko/init`, reuses the open PR (or opens one), and calls
  `put_secret` **only for names present in `plan.secret_actions`** — passing
  the value through verbatim: a plaintext `str` in PAT mode, a `SealedSecret`
  in web mode.
- **`GitHubInitClient`** is the I/O-only Protocol:
  `get_existing_files`, `existing_secrets` (raises on listing failure — never
  a silent empty set), `upsert_branch_and_commit`,
  `get_or_open_pull_request`, `put_secret`. Its `SecretTarget.scope` selects
  repo vs org endpoints.

Per-secret statuses in the outcome are `created`, `updated`,
`skipped_existing` or `failed`, each with a human-readable detail line.

**`setup.py`** stays the thin interactive wrapper: prompts informed by
`existing_secrets`, delegates to `plan_init` / `execute_init`, asks for
`GH_PAT` only in PAT mode, and contains no broker/variable-writing path.
