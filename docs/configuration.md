# Repository configuration: `.nebuko/nebuko.yaml`

The optional manifest holds the non-secret engine settings of one repository.
It is read from the caller's checkout; secrets never go in it. Every key is
optional. Unknown keys are ignored, so a newer manifest does not break an older
engine.

## Keys

| Key | Env var | Default | Meaning |
|-----|---------|---------|---------|
| `base_branch` | `NEBUKO_BASE_BRANCH` | `main` | The default branch. The engine resolves it once per run, with its commit `NEBUKO_BASE_SHA`; `git_diff` diffs against that commit, and the evidence checkout resolves against the branch. Letters, digits and `._/-`, at most 200 characters, no `..`, no leading `-`; anything else fails the run with a clear error. |
| `test_command` | `NEBUKO_TEST_COMMAND` | none | Shell command that runs the repo's tests. The shipped `run_tests` step runs it with `eval` from the workspace. Blank counts as unset. |
| `test_timeout_minutes` | `NEBUKO_TEST_TIMEOUT_MINUTES` | `20` | Minutes allowed for `run_tests`. |
| `merge_main` | | `true` | Script steps merge `origin/<base_branch>` first, so they test what CI tests. A step opts out with its own `NEBUKO_MERGE_MAIN=0`. The shipped `git_diff` and `run_tests` opt out, so they test the branch head: a semantic conflict with newer base commits (no textual conflict) shows up in CI on the merge commit, not in `run_tests`. Other script steps, such as the red-test loop's `run_tests`, still merge. |
| `default_workflow` | | `default` | Workflow used when none is named. |
| `agent_config` | `AGENT_CONFIG_PATH` | | Agent config file. |
| `circuit_breaker_threshold` | `CIRCUIT_BREAKER_THRESHOLD` | | Agent calls allowed per issue. |
| `history_comment_count` | `HISTORY_COMMENT_COUNT` | | Comments read as history. |
| `pin_comment` | `PIN_COMMENT` | `false` | Pin the bot's comment. |
| `features.secret_scan` | | `false` | Opt-in secret scanning. |

Precedence is explicit env or workflow input, then the manifest, then the
built-in default. An empty input counts as unset.

## Script steps read the committed manifest

The engine resolves the base once per run, before the first step: the base name,
its commit (`NEBUKO_BASE_SHA`) and the manifest committed at that commit. Script
steps read these from that snapshot. In the single-job loop an agent step shares
the checkout, so no script step reads refs after the snapshot. A script step takes
`test_command` and `test_timeout_minutes` from the committed manifest,
`origin/<base_branch>:.nebuko/nebuko.yaml`, not from the working tree. The session checkout is the branch under review, and
the dev agent can edit it. Reading the working tree would let a branch change the
command that runs on its own code.

- A `test_command` changed on the branch does not change what `run_tests` runs.
  It takes effect once it is merged to the base branch.
- The base ref is found without the working tree: the engine's `NEBUKO_BASE_BRANCH`,
  else the remote's default branch (`origin/HEAD`), else `main`. A working-tree
  `base_branch` is ignored, so a branch cannot point the lookup at a ref it made.
- A repo with no committed manifest on the base branch gets no manifest settings
  in script steps. `run_tests` then reports `no test_command configured`.
- If the committed manifest cannot be read (`origin/<base>` not fetched, or git
  fails) or is invalid, `run_tests` says so, exits 0 so review still runs, and
  logs a warning. It does not report `no test_command configured`.

**Trust model.** `test_command` runs with `eval` inside the engine job. That job
holds the provider secrets and the GitHub token. Whoever can merge to the base
branch can therefore run arbitrary commands in the job, as with any CI
workflow. The committed-manifest rule keeps a branch under review from changing
the command by editing it; it does not sandbox the command. Treat the base
branch's protection as the control for this setting. Repo-local workflow
overrides under `.nebuko/workflows/` have the same property.

## `nebuko init`

`nebuko init` asks for the test command (blank for none, or `--test-command`) and
the default branch (`main`, or `--base-branch`). It writes both keys to
`.nebuko/nebuko.yaml` in the setup pull request.

- If the file exists, only the two keys change. Other keys, comments and their
  order are kept. The result is parsed again and must equal the old mapping with
  just these keys changed.
- If that edit is not safe (for example the key's line has a trailing comment,
  or the file is not a mapping), init refuses before writing and prints the
  lines to add by hand. It never overwrites the file.
- A file that cannot be read for a reason other than 404 also stops init.
