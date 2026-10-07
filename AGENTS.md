
## Dev environement
You can use `nix develop` from the root of the project to get the dev environment.
Add missing dependencies here for next run.

## Code language
This project is in Python.

## Running the test suite
The full suite is `pytest` with **no path argument**: it collects `tests/` and the
runtime tests under `engine/lib/**` (relocated out of `.github/scripts/` in issue #769).
`pytest tests/` alone only runs a subset. The suite needs `pytest-mock` (declared in
`flake.nix`); the `mocker` fixture errors out when it is missing. Without `nix develop`,
install them into the checked-in venv: `uv pip install --python .venv/bin/python pytest
pytest-mock`, then run `.venv/bin/python -m pytest`.

## Pushing CI workflow files
The checkout's GitHub App token lacks the `workflows` permission, so git pushes that
add/modify `.github/workflows/*` are rejected by GitHub. The `GH_TOKEN` env var has the
`workflow` scope: clear the global `http.https://github.com/.extraheader` extraheader,
run `gh auth setup-git`, push, then restore the saved gitconfig.