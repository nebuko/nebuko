# `nebuko init` — manifest + core refactor (issue #1272)

This document is the user-facing spec for the `nebuko init` refactor: how the
command is used, and how the new `manifest` and `core` modules plug into the
engine. It replaces the broker-based flow described in `docs/nebuko-init.md`.

---

## Usage

```bash
nebuko init                      # interactive
NEBUKO_SECRET_OPENROUTER_API_KEY=... \
  nebuko init --repo acme/widgets --provider openrouter --non-interactive
```

What `init` does after the refactor:

1. Reads the packaged manifest (`nebuko.engine.init.manifest.load_packaged_manifest`).
2. Plans the change purely (`plan_init`): computes which secrets must be
   created vs updated, and which target files conflict with existing content.
3. Executes the plan (`execute_init`) through a `GitHubInitClient`, writing
   branch `nebuko/init`, upserting secrets and reusing the open PR if one
   exists.
4. Prints the outcome: PR URL, files written, per-secret statuses.

Secrets: in PAT mode the plaintext token is passed (`str`); in App/web mode a
`SealedSecret` from the broker is passed. `GH_PAT` is CLI-only.

---

## Mechanics — where it plugs in

New modules under `src/nebuko/engine/init/`:

- **`manifest.py`** — `ProviderSpec` and `Manifest` dataclasses,
  `parse_manifest(raw)` (strict JSON schema validation, `ManifestError` on
  unknown fields or empty providers) and `load_packaged_manifest()` (an
  `importlib.resources` read of the copy shipped under
  `nebuko.engine.init`; no network). `caller_workflow_template` is relative to
  the manifest's own directory (`"nebuko-caller.yaml"`); the CLI resolves it
  against the packaged copy's directory and the Worker against the repo
  checkout at the resolved release tag. `ProviderSpec.template_path` stays
  repo-root-relative — it is always fetched at `pin_ref` — and the four
  providers mirror `providers.py` verbatim (enforced by a parity test).
- **`core.py`** — the planning/execution engine:
  - `SecretTarget` (repo or org scope), `RenderInputs`, `InitRequest`,
    `SealedSecret`, `SecretValue`, `SecretStatus`, `InitPlan` (with
    `conflicts`), `InitOutcome`.
  - `GitHubInitClient` — a Protocol for I/O only: `get_existing_files`,
    `existing_secrets`, `upsert_branch_and_commit` (force-updates
    `nebuko/init`), `get_or_open_pull_request`, `put_secret`.
  - `plan_init(...)` — module-level, pure; receives `existing_files` and
    `existing_secrets` (the target's `scope` field decides repo vs org) and
    returns an `InitPlan`.
  - `execute_init(plan, client, secrets)` — module-level; never prompts or
    prints.
- **`setup.py`** stays the thin interactive wrapper: prompts informed by
  `existing_secrets`, delegates to `plan_init` / `execute_init`, asks for
  `GH_PAT` only in PAT mode.

Behaviour changes: `init` no longer writes a local workflow file (the caller
workflow comes from the packaged manifest), and the broker variables /
`RepoVariableWriter` path is removed — the Worker resolves the tag at runtime.
