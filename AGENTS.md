
## Dev environement
You can use `nix develop` from the root of the project to get the dev environment.
Add missing dependencies here for next run.

## Code language
This project is in Python.

## Pushing CI workflow files
The checkout's GitHub App token lacks the `workflows` permission, so git pushes that
add/modify `.github/workflows/*` are rejected by GitHub. The `GH_TOKEN` env var has the
`workflow` scope: clear the global `http.https://github.com/.extraheader` extraheader,
run `gh auth setup-git`, push, then restore the saved gitconfig.