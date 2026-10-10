## Dev environment
Python library (requires Python >= 3.12). Pick one, from the repo root:
- **uv (preferred):** `uv sync`. The default `dev` dependency group in
  `pyproject.toml` installs `pytest` + `pytest-mock`, so this is the *only*
  setup step. Run everything with `uv run ...`.
- **nix:** `nix develop`.

Do not install test tooling ad hoc (`pip install pytest`, ...): it is declared in
`pyproject.toml` and `flake.nix`.

## Code language
This project is in Python.

## Running the test suite
`pyproject.toml` sets `testpaths = ["tests"]`, so a bare `pytest` collects **only**
`tests/`. The flat runtime modules under `src/nebuko/engine/lib/**` hold their own
co-located `test_*.py` files and are **not** collected by default.

```bash
uv run pytest                              # fast gate: tests/ only
uv run pytest src/nebuko/engine/lib        # runtime engine/lib unit tests
uv run pytest tests src/nebuko/engine/lib  # everything
```

Both suites are green on `main` (they only need `defaults/` in the checkout), so a
failure is a real regression - do not spend turns proving a failure is "pre-existing"
without first diffing against `origin/main`.

## Where workflows live
Workflow definitions ship in `defaults/workflows/` (e.g. `default.yaml`); per-repo
overrides go in `.nebuko/workflows/`. Resolve them through
`engine/lib/default_paths.py::config_dir("workflows")` - do not hardcode a repo-root
`workflows/` path (it does not exist).

## Pushing CI workflow files
The checkout's GitHub App token lacks the `workflows` permission, so git pushes that
add/modify `.github/workflows/*` are rejected by GitHub. The `GH_TOKEN` env var has the
`workflow` scope: clear the global `http.https://github.com/.extraheader` extraheader,
run `gh auth setup-git`, push, then restore the saved gitconfig.