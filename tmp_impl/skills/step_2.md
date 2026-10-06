# Step 2 / 4 — `lint-config` CLI + `run_loop` dispatch

## Instructions

Goal: the `python -m nebuko.engine lint-config` command, mirroring
`src/nebuko/engine/lint_cli.py`'s contract exactly.

Create `src/nebuko/engine/config_lint_cli.py`:

- `EXIT_OK = 0`, `EXIT_ERRORS = 1`, `EXIT_USAGE = 2`.
- `_build_parser()`: `prog="python -m nebuko.engine lint-config"`, description
  naming issue #774; positional `paths` `nargs="*"` defaulting to the shipped
  agent config resolved through the layered lookup (`NebukoPaths` /
  `ConfigKind.AGENT_CONFIG`); `--strict` (promote warnings to errors, which
  flips the exit code); `--format {text,json}` defaulting to `text`.
- `main(argv: Sequence[str] | None = None) -> int`: expand directory targets to
  `*.yaml`/`*.yml` the way `lint_cli` does; run
  `ConfigLinter(strict=args.strict).lint_all(targets)`; on `OSError`,
  `ValueError`, or `yaml.YAMLError` print `lint-config: {exc}` to stderr and
  return `EXIT_USAGE` (mirror `src/nebuko/engine/lint_cli.py`).
  `--format json` prints **only** `json.dumps([asdict(i) for i in issues],
  indent=2)` — nothing else may contaminate the JSON stream (the kubeval
  exit-code/output lesson). Text format: import `_format_issue` / `_print_text`
  from `nebuko.engine.lint_cli` so both linters render identically (they already
  format `LintIssue` generically). Return `EXIT_ERRORS if any(i.severity ==
  "error" for i in issues) else EXIT_OK` — uniform severity→exit mapping.

Wire the dispatch in `src/nebuko/engine/run_loop.py` `main()`, right beside the
existing `lint` branch (the `raw_args[0] == "lint"` block that imports
`nebuko.engine.lint_cli.main`): add the equivalent branch for
`raw_args[0] == "lint-config"` that imports `nebuko.engine.config_lint_cli.main`
and returns `lint_config_main(raw_args[1:])`. Match the existing branch style
exactly.

Sanity checks:

```bash
PYTHONPATH=src python -m nebuko.engine lint-config --help
PYTHONPATH=src python -m nebuko.engine lint-config defaults/agents/   # exit 0, C6 warnings only
PYTHONPATH=src python -m nebuko.engine lint-config missing.yaml       # exit 2
```

## Constraints

- The exit-code contract is the CI interface: `0` clean or warnings only,
  `1` at least one error, `2` usage/load failure. Keep it uniform — no
  warning may ever yield `1` unless `--strict` was given.
- Do not touch the existing `lint` branch beyond adding the sibling branch.
- Prefer reusing `lint_cli`'s formatting helpers over duplicating them.

## Expected outcome

- Both commands are dispatachable from the single `python -m nebuko.engine`
  entry point.
- `--format json` output parses as a JSON list with no leading/trailing text.
- A nonexistent path and a malformed YAML file each yield exit `2` with a
  message on stderr.

## State on this branch

The CLI and dispatch already landed on this branch. Treat this step as
verify-and-complete: run the sanity checks above, fix any gap you find, and
commit only if something changed.

## Relevant files

- `src/nebuko/engine/lint_cli.py` (the workflow-linter CLI — the model)
- `src/nebuko/engine/run_loop.py` (`main()` subcommand dispatch)
- `src/nebuko/engine/config_lint.py` (from step 1)
- `src/nebuko/engine/paths.py` (`NebukoPaths`, `ConfigKind`, layered default)
- `src/nebuko/engine/__main__.py` (module entry point)

## Commit message

`[step 2/4] lint-config CLI + run_loop dispatch (issue #774)`
