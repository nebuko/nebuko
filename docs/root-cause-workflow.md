# Root-cause workflow

`/nebuko run workflow --name root-cause` is for issues that ask **why**
("the loop's test result does not match CI", "this got slower"). It does not
plan, write tests or open a PR. It finds the cause and leaves an **executable
oracle**: a script the engine runs, judged **per delta**, that shows the symptom
now and is meant to show it gone after a fix.

```
investigate -> oracle_present -> confirm (human gate) -> seal -> handoff
     ^                                |
     +-------- rejected --------------+
```

| Step | Who | What |
|---|---|---|
| `investigate` | `dev` agent (read-only discipline) | Lists the deltas, measures cheapest first, stops at the first reproduction that matches the reported numbers, shows a counterfactual, writes the oracle to `.nebuko/evidence/issue-<n>/v<k>/` and pushes. |
| `oracle_present` | engine (script) | Verifies the manifest, runs the oracle, must read `present`. |
| `confirm` | `review` agent, gated | Reads the oracle with fresh eyes, re-runs the counterfactual or one other discriminating check, rejects a hard-coded or drifting repro. Rejection loops to `investigate` (writes `v<k+1>`). |
| `seal` | engine (script) | After the human released the gate: re-verifies and posts the git tree id of the evidence directory. |
| `handoff` | `architect` | Fix direction, the expected per-delta result after the fix, and the command for the next workflow. |

## The evidence directory

```
.nebuko/evidence/issue-<n>/v<k>/
  repro.sh        POSIX sh; prints ONE JSON object on its last stdout line
  expected.json   {"deltas": {"<id>": {"symptom": V, "healthy": W}}}
  manifest.json   sha256 of every other file (written by `evidence.py manifest`)
  ...             frozen inputs: log excerpts, SHAs, command output
```

Rules for `repro.sh`: read only files in its own directory or pinned git
objects (SHAs), never "latest" or live network state; fetch remote data once,
save it, read the copy. No credentials, no environment dumps.
`healthy` is optional (default: any value other than `symptom`); set it only
when it is stable, never for a number that drifts with `main`.

Revisions are **append-only**: a rejected diagnosis is followed by `v<k+1>`,
never by an edit of `v<k>`.

## The oracle (`.nebuko/scripts/evidence.py`)

```bash
python3 .nebuko/scripts/evidence.py run      <dir|--issue N|auto> [--expect present|absent] [--timeout S]
python3 .nebuko/scripts/evidence.py manifest <dir|--issue N|auto>
python3 .nebuko/scripts/evidence.py verify   <dir|--issue N|auto> [--sealed-tree ID]
```

`<dir>` may be a `v<k>` directory or an issue directory (the newest `v<k>`
wins). `--issue auto` reads the number from `GITHUB_EVENT_PATH`.

`run` judges each delta as `symptom`, `healthy`, `other` (neither) or
`missing`, and prints a JSON report plus `EVIDENCE_VERDICT=<verdict>`:

| Verdict | Meaning |
|---|---|
| `present` | every delta shows the symptom |
| `absent` | every delta is healthy |
| `partial` | a mix: the fix removed some deltas, not all |
| `inconclusive` | the repro failed, timed out, printed garbage, omitted a delta or returned a value that is neither symptom nor healthy. **Never counts as fixed.** |

Exit code of `run --expect X`: `0` the verdict is X, `2` inconclusive,
`3` partial, `1` any other mismatch.

## Using it after a fix

Add a script step to the build workflow (this is not wired into
`spec-tdd` or `dev-review-loop` yet):

```yaml
  - name: oracle_absent
    script:
      command: "sh"
      args:
        - "-c"
        - >-
          sh .nebuko/scripts/use-session-branch.sh &&
          python3 .nebuko/scripts/evidence.py verify --issue auto --sealed-tree <seal tree id> &&
          python3 .nebuko/scripts/evidence.py run --issue auto --expect absent
```

`UNCHANGED` (still `present`) means the fix did not touch the cause: go back
to `investigate`. `partial` names the deltas that remain.

## Worked example: issue #1344

The loop reported `31 failed, 4177 passed, 6 subtests`; CI on the same commit
reported `4 failed, 4292 passed, 14 subtests`. Three deltas:

`expected.json`
```json
{"deltas": {
  "failed":    {"symptom": 31,   "healthy": 4},
  "collected": {"symptom": 4208},
  "subtests":  {"symptom": 6}
}}
```

`repro.sh` (sketch): for the two pinned trees, `bc4625c` (what the loop tested)
and the merge `bc4625c` into `545b2c9` (what CI tested), run
`pytest --collect-only` and the frozen failing IDs, and print
`{"failed": ..., "collected": ..., "subtests": ...}` for the tree the loop
tests. Before the fix that tree is the branch head, so all three deltas show
the symptom (`present`). The counterfactual (test the merge tree) turns all
three healthy (`absent`).

## Limits and follow-ups

* The seal is a workflow step plus `verify --sealed-tree`; the engine does not
  yet record the sealed tree id in the workflow state and fail any later step
  that touches the directory. Tracked in the issue for engine-side seal
  enforcement.
* Secrets: the evidence directory is committed. Until the engine scans pushes
  (issue #1347) the investigator and the confirmer are the only guard.
* Not every issue has a reproducible symptom (intermittent failures, design
  questions). The investigator finalizes with `hold` instead of faking an
  oracle.
