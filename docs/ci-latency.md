# CI latency: how the workflow installs and runs the engine

Every agent run pays a setup cost between the triggering comment and the
engine's first action. This page documents the fast path the reusable
workflow (`.github/workflows/nebuko-workflow.yaml`) uses, how to operate it,
and what the historical cache warnings meant.

Measured baseline (run
[41037420019](https://github.com/nebuko/nebuko/actions/runs/41037420019),
`run-task` job): ~26 s of setup after checkout —

| Step | Before | After |
|---|---|---|
| Install OpenHands dependencies | ~8 s | removed |
| Materialize extra secret slots | ~11 s | <1 s |
| Engine start (`uv run` resolution) | ~5 s | <1 s |
| `uv sync` (locked, warm cache) | — | ~3–4 s |
| **Total setup after checkout** | **~26 s** | **~8–10 s** |

End to end, comment → first engine comment drops from ~40 s to ~20–25 s;
the remaining ~10 s is runner spin-up, which is the floor.

## How it works

**One locked sync instead of two installs.** The workflow previously installed
the OpenHands SDK twice: once unpinned from `@main` ("Install OpenHands
dependencies") and again implicitly when `uv run --extra runtime` re-resolved
the runtime extra. It now runs a single
`uv sync --project "$NEBUKO_ROOT" --extra runtime --locked --no-dev`, which
installs exactly the commit-SHA-pinned set recorded in `uv.lock` (issue #982).
The unpinned-`@main` install was both a duplicate and a reproducibility
violation, and it is deleted.

**`--no-sync` on every `uv run`.** After the sync, the secret-materialization
step and the engine start run as `uv run --no-sync --project "$NEBUKO_ROOT"
python -m …`. Without `--no-sync`, `uv run` re-resolves the whole environment
on each invocation — that alone was ~16 s across the two steps for a job
whose dependencies were already installed.

**Why not the release binary in CI.** The PyInstaller binary is built only on
`v*` tags, while the engine always runs at `job.workflow_sha` (main HEAD).
Using the binary in CI would silently execute stale engine code and break the
reproducibility guarantee of issue #982, so it stays the client-install path
only.

**Cache write scope.** setup-uv saves the uv cache in a post step; the job
declares `actions: write` and passes an explicit `github-token` to the
setup-uv step so the save never runs with the broker's App installation token,
which has no cache-write scope. The `ENABLE_UV_CACHE` input still gates
caching on/off.

## How to use

**Bumping the OpenHands SDK SHA.** There is now a single place: the
`runtime` extra in `pyproject.toml` (and its mirror in
`.nebuko-engine/pyproject.toml`, which the engine checkout uses in CI). Edit
the SHA there and re-resolve with `uv lock`. CI has no separate install pin
any more — the workflow syncs whatever `uv.lock` records.

**Disabling caching.** Pass `enable_uv_cache: false` to the reusable workflow
(or set `ENABLE_UV_CACHE=false`); setup-uv will neither restore nor save the
uv cache. Useful for debugging cache-related failures.

**Reading the old cache warnings.** The two warnings former runs printed were
both cache-save failures, not correctness problems:

- `Cache reservation failed: cache write denied: token has no writable
  scopes` — the post-save ran with the broker's App installation token, which
  lacks the `actions: write` scope needed to write to the Actions cache.
- `Failed to save: Unable to reserve cache with key setup-uv-2-…` — the
  consequence: the cache entry could not be created (or was being created by
  a concurrent job), so the next run started cold again.
