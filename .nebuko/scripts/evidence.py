#!/usr/bin/env python3
"""Evidence oracle for the root-cause workflow (issue #1348).

A root-cause investigation leaves an executable oracle in
``.nebuko/evidence/issue-<n>/v<k>/``:

* ``repro.sh``       - reproduces the symptom. Prints one JSON object on its
                       last stdout line: ``{"<delta>": <observed value>, ...}``.
* ``expected.json``  - ``{"deltas": {"<delta>": {"symptom": V, "healthy": W}}}``.
                       ``healthy`` is optional: when absent, any value other
                       than ``symptom`` counts as healthy.
* ``manifest.json``  - sha256 of every other file in the directory.
* any frozen inputs (log excerpts, SHAs, ...) that ``repro.sh`` reads.

The oracle is judged **per delta**, not as one pass/fail, so a fix that removes
only some of the reported differences is reported as ``partial``.

Verdicts (``run``): ``present`` (every delta shows the symptom), ``absent``
(every delta is healthy), ``partial`` (a mix) and ``inconclusive`` (the repro
failed, timed out, printed garbage, omitted a delta or returned a value that is
neither ``symptom`` nor ``healthy``). ``inconclusive`` never counts as fixed.

Exit codes of ``run --expect X``: 0 when the verdict is X, 2 when
inconclusive, 3 when partial, 1 for any other mismatch.

Stdlib only: it runs on a bare CI runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPRO = "repro.sh"
EXPECTED = "expected.json"
MANIFEST = "manifest.json"
DEFAULT_ROOT = ".nebuko/evidence"

VERDICTS = ("present", "absent", "partial", "inconclusive")
_VERSION_DIR = re.compile(r"^v(\d+)$")


class EvidenceError(Exception):
    """The evidence directory is unusable (missing file, bad JSON, ...)."""


# --- locating the evidence ---------------------------------------------------


def issue_number_from_event() -> int:
    """Issue number of the triggering GitHub event (as use-session-branch.sh)."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        raise EvidenceError("--issue auto needs GITHUB_EVENT_PATH")
    with open(path, encoding="utf-8") as fh:
        return int(json.load(fh)["issue"]["number"])


def latest_version_dir(issue_dir: Path) -> Path:
    versions = []
    if issue_dir.is_dir():
        for child in issue_dir.iterdir():
            match = _VERSION_DIR.match(child.name)
            if match and child.is_dir():
                versions.append((int(match.group(1)), child))
    if not versions:
        raise EvidenceError(f"no v<k> evidence directory under {issue_dir}")
    return max(versions)[1]


def resolve_dir(target: str | None, issue: str | None, root: str) -> Path:
    """A version dir, or the newest ``v<k>`` of an issue dir / ``--issue``."""
    if issue is not None:
        number = issue_number_from_event() if issue == "auto" else int(issue)
        return latest_version_dir(Path(root) / f"issue-{number}")
    if target is None:
        raise EvidenceError("give an evidence directory or --issue")
    path = Path(target)
    if (path / REPRO).is_file():
        return path
    return latest_version_dir(path)


# --- the oracle --------------------------------------------------------------


def load_expected(directory: Path) -> dict[str, dict]:
    path = directory / EXPECTED
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read {path}: {exc}") from exc
    deltas = data.get("deltas") if isinstance(data, dict) else None
    if not isinstance(deltas, dict) or not deltas:
        raise EvidenceError(f"{path}: 'deltas' must be a non-empty object")
    for name, spec in deltas.items():
        if not isinstance(spec, dict) or "symptom" not in spec:
            raise EvidenceError(f"{path}: delta {name!r} needs a 'symptom' value")
        if "healthy" in spec and spec["healthy"] == spec["symptom"]:
            raise EvidenceError(f"{path}: delta {name!r} has symptom == healthy")
    return deltas


def run_repro(directory: Path, timeout_s: int) -> tuple[dict | None, str | None]:
    """Run ``repro.sh``; return (observed deltas, error)."""
    script = directory / REPRO
    if not script.is_file():
        return None, f"{script} is missing"
    try:
        proc = subprocess.run(
            ["sh", REPRO],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return None, f"{REPRO} timed out after {timeout_s}s"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        return None, f"{REPRO} exited {proc.returncode}: " + " | ".join(tail)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return None, f"{REPRO} printed nothing"
    try:
        observed = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None, f"{REPRO} last stdout line is not JSON: {lines[-1][:120]!r}"
    if not isinstance(observed, dict):
        return None, f"{REPRO} last stdout line is not a JSON object"
    return observed, None


def classify(expected: dict[str, dict], observed: dict) -> dict[str, str]:
    """Per delta: ``symptom`` | ``healthy`` | ``other`` | ``missing``."""
    statuses: dict[str, str] = {}
    for name, spec in expected.items():
        if name not in observed:
            statuses[name] = "missing"
        elif observed[name] == spec["symptom"]:
            statuses[name] = "symptom"
        elif "healthy" not in spec or observed[name] == spec["healthy"]:
            statuses[name] = "healthy"
        else:
            statuses[name] = "other"
    return statuses


def verdict_of(statuses: dict[str, str], error: str | None) -> str:
    if error is not None or any(s in ("missing", "other") for s in statuses.values()):
        return "inconclusive"
    values = set(statuses.values())
    if values == {"symptom"}:
        return "present"
    if values == {"healthy"}:
        return "absent"
    return "partial"


def exit_code(verdict: str, expect: str) -> int:
    if verdict == expect:
        return 0
    return {"inconclusive": 2, "partial": 3}.get(verdict, 1)


def cmd_run(args: argparse.Namespace) -> int:
    directory = resolve_dir(args.dir, args.issue, args.root)
    expected = load_expected(directory)
    observed, error = run_repro(directory, args.timeout)
    statuses = classify(expected, observed or {}) if observed is not None else {}
    verdict = verdict_of(statuses, error)
    report = {
        "dir": str(directory),
        "verdict": verdict,
        "expect": args.expect,
        "met": verdict == args.expect,
        "error": error,
        "deltas": {
            name: {
                "status": statuses.get(name, "not-run"),
                "observed": (observed or {}).get(name),
                "symptom": spec["symptom"],
                "healthy": spec.get("healthy", "<anything but symptom>"),
            }
            for name, spec in expected.items()
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"EVIDENCE_VERDICT={verdict} expected={args.expect}")
    return exit_code(verdict, args.expect)


# --- integrity ---------------------------------------------------------------


def hash_tree(directory: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name == MANIFEST and path.parent == directory:
            continue
        files[path.relative_to(directory).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return files


def cmd_manifest(args: argparse.Namespace) -> int:
    directory = resolve_dir(args.dir, args.issue, args.root)
    load_expected(directory)  # refuse to seal an unusable oracle
    files = hash_tree(directory)
    (directory / MANIFEST).write_text(
        json.dumps({"files": files}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"manifest: {len(files)} files in {directory}")
    return 0


def git_tree_id(directory: Path) -> str | None:
    """Git tree object id of ``directory`` at HEAD, if committed."""
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=directory, capture_output=True, text=True, check=True,
        ).stdout.strip()
        rel = directory.resolve().relative_to(Path(top).resolve()).as_posix()
        return subprocess.run(
            ["git", "rev-parse", f"HEAD:{rel}"],
            cwd=top, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


def cmd_verify(args: argparse.Namespace) -> int:
    directory = resolve_dir(args.dir, args.issue, args.root)
    try:
        recorded = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))["files"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"cannot read {MANIFEST} in {directory}: {exc}", file=sys.stderr)
        return 1
    actual = hash_tree(directory)
    changed = sorted(n for n in recorded if n in actual and recorded[n] != actual[n])
    removed = sorted(set(recorded) - set(actual))
    added = sorted(set(actual) - set(recorded))
    tree = git_tree_id(directory)
    if tree:
        print(f"tree: {tree}")
    if args.sealed_tree and tree != args.sealed_tree:
        print(f"tree {tree} != sealed {args.sealed_tree}", file=sys.stderr)
        return 1
    if changed or removed or added:
        print(f"changed={changed} removed={removed} added={added}", file=sys.stderr)
        return 1
    print(f"verified: {len(actual)} files match {MANIFEST}")
    return 0


# --- cli ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("dir", nargs="?", help="a v<k> dir, or an issue dir (newest v<k> wins)")
        p.add_argument("--issue", help="issue number, or 'auto' (from GITHUB_EVENT_PATH)")
        p.add_argument("--root", default=DEFAULT_ROOT, help=f"default {DEFAULT_ROOT}")

    run = sub.add_parser("run", help="run repro.sh and judge it per delta")
    common(run)
    run.add_argument("--expect", choices=("present", "absent"), default="present")
    run.add_argument("--timeout", type=int, default=600, help="seconds for repro.sh")
    run.set_defaults(func=cmd_run)

    manifest = sub.add_parser("manifest", help="write manifest.json")
    common(manifest)
    manifest.set_defaults(func=cmd_manifest)

    verify = sub.add_parser("verify", help="check files against manifest.json")
    common(verify)
    verify.add_argument("--sealed-tree", help="git tree id recorded at seal time")
    verify.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except EvidenceError as exc:
        print(f"evidence: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
