# End-to-end tests

## Overview

`tests/engine/e2e/` holds the end-to-end tests that drive a *whole* engine run
instead of one seam. The first one runs a complete dev step — all ten phases of
`Engine.run()` — over a real git checkout with a scripted LLM, and it exists to
catch roughly 90% of the bugs that would otherwise only surface on a real run
(branch not created, merge skipped, nothing pushed, a malformed `finalize` that
the engine fails to retry).

The point is fidelity: the engine's own git machinery, its own finalize
executor and its own run loop all run for real. Only the two things that cannot
exist in CI are replaced — the LLM (a scripted conversation) and `gh`/GitHub
(the PR creation call).

## How to run

```bash
uv run pytest tests/engine/e2e
```

The test lives in `tests/engine/e2e/test_dev_step_e2e.py`. It is a normal
`tmp_path` test: every run gets its own bare remote, work checkout and run
directory, so nothing touches the repository you are working in.

## How it works

Three seams are in play.

### 1. Real git, recorded

The test does **not** fake a `git` binary. `GitCommandRecorder` wraps
`subprocess.run` in the three modules that shell out to git
(`nebuko.engine.lib.git.git_session`, `...branch_scaffold` and
`agent_script.features.ensure_pr.ensure_pr`), appends every `git …` argv to
`commands`, and then delegates to the real `subprocess.run`. Git therefore
really runs, against a local **bare remote** in `tmp_path`:

- `origin.git` — the bare remote, seeded with `main` and with a previous
  round's feature branch;
- `work/` — the session checkout the engine treats as its caller workspace
  (`GITHUB_WORKSPACE`), on `main` tracking `origin`.

Because the effects are real, the test can assert both the *order* of the git
calls and their *result* (the remote ref, the merge commit).

### 2. A scripted pipeline instead of an LLM

`agent_runner._load_pipeline` is the engine's established pipeline seam: it
returns the legacy assembly (`resolve_llm_config`, `run_agent`,
`collect_and_save_metrics`, `build_output`, …). `_install_scripted_pipeline`
replaces it with fakes whose `run_agent` returns a `ScriptedDevConversation`.

That conversation replays one scripted turn per `run()` call. Each turn is a
list of SDK events, and the conversation runs the **real** `FinalizeExecutor`
over the turn's `finalize` actions, so a malformed outcome is rejected by the
production code and its error observation is recorded in
`conversation.rejections` — the engine's retry is exercised, not simulated.

The default scenario is "wrong, then right":

| turn | finalize call | result |
|------|---------------|--------|
| 1 | `turn_outcome={"note": "done"}` — no `appreciation`, no report | rejected: `finalize rejected: …` |
| 2 | `turn_outcome={"appreciation": "approved"}` + full report | accepted, report posted |

Turn 1 carries no report on purpose: the host retries the closing turn when it
harvests no report text, which is exactly the retry path this test locks.

To add a turn, append another `_finalize_turn(...)` to the conversation's
`turns` list.

### 3. Mocked PR creation

`gh` and GitHub do not exist in CI, so `ensure_pr.ensure_pr` is monkeypatched
(the same pattern as `tests/engine/git/test_pr_finalize.py`) to return a canned
`EnsurePrResult` with a PR URL. The push itself is *not* mocked: it is asserted
through `GitSessionGuard.finalize` against the bare remote.

## What it asserts

- **Run outcome** — the run completed (`ExitCode.ADVANCE`) with an `approved`
  turn outcome, and every phase in `PHASES` ran.
- **Branch create → merge → push order** — a `checkout -b feature/…` line comes
  before a `merge --no-edit origin/main` line, which comes before a
  `push origin HEAD:refs/heads/feature/…` line.
- **Real effects** — the bare remote's `feature/…` ref equals the session HEAD,
  and `origin/main` is an ancestor of it (the base was really merged).
- **Finalize retry** — exactly one rejection was recorded and it mentions
  `finalize rejected`; the second turn's finalize was accepted.
- **Run artefacts** — the `LocalAdapter` run directory holds `journal.md`,
  `state.json` and `latest_answer.md`.

## Extending

- **A still-wrong second finalize**: script turn 2 with another malformed
  `turn_outcome` and assert two rejections plus the fallback deliverable.
- **A declared output contract**: add `STEP_OUTPUT` to the resolved mapping and
  script the report with section markers, to cover the section-salvage path
  instead of the plain-report path.
- **Another step**: change `WORKFLOW_STEP`/`TASK` in `_resolved_with_hooks()` and
  the scripted turns; the git, pipeline and PR seams stay the same.
