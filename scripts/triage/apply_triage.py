"""Appliers for the #827 triage manifest.

Two operations, both driven by :mod:`triage.triage_manifest`:

* :func:`apply_entries` — additively apply each entry's labels;
* :func:`transfer_entries` — transfer the ``migrate`` rows to the archive repo.

Hard invariant (issue #827, human amendment): dev performs **no deletions and no
content edits**.  ``apply_entries`` only *adds* labels, and ``transfer_entries``
raises :class:`PermissionError` for any ``delete``/``scrub`` row.  External calls
are isolated behind the :data:`Runner` port so the logic is testable without
touching GitHub.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

try:
    from triage.triage_manifest import (
        ARCHIVE_REPO,
        DESTRUCTIVE_DISPOSITIONS,
        LABELS,
        MANIFEST,
        LabelSpec,
        TriageEntry,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from triage.triage_manifest import (
        ARCHIVE_REPO,
        DESTRUCTIVE_DISPOSITIONS,
        LABELS,
        MANIFEST,
        LabelSpec,
        TriageEntry,
    )


class CommandError(RuntimeError):
    """A shelled-out ``gh`` command returned a non-zero exit code."""


Runner = Callable[[Sequence[str]], str]


def run_command(args: Sequence[str]) -> str:
    """Default :data:`Runner` adapter: shell out to ``gh``."""
    completed = subprocess.run(list(args), capture_output=True, text=True)
    if completed.returncode != 0:
        raise CommandError(f"{' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def ensure_labels(repo: str, labels: Sequence[LabelSpec], runner: Runner = run_command) -> None:
    """Create/refresh each label (idempotent thanks to ``--force``)."""
    for spec in labels:
        runner(
            [
                "gh",
                "label",
                "create",
                spec.name,
                "--repo",
                repo,
                "--color",
                spec.color,
                "--description",
                spec.description,
                "--force",
            ]
        )


def label_args(entry: TriageEntry) -> list[str]:
    """Build the ``--add-label`` flags for one entry."""
    args: list[str] = []
    for label in entry.labels:
        args += ["--add-label", label]
    return args


def apply_entries(
    repo: str,
    entries: Sequence[TriageEntry],
    runner: Runner = run_command,
) -> None:
    """Additively apply every entry's labels.

    This is a label-only pass: it never edits issue bodies, comments, or state.
    Rows without labels are skipped.
    """
    for entry in entries:
        if not entry.labels:
            continue
        runner(
            ["gh", "issue", "edit", str(entry.issue_number), "--repo", repo, *label_args(entry)]
        )


def destructive_entry_numbers(entries: Sequence[TriageEntry]) -> tuple[int, ...]:
    """Issue numbers whose disposition is destructive (``delete``/``scrub``)."""
    return tuple(e.issue_number for e in entries if e.disposition in DESTRUCTIVE_DISPOSITIONS)


def migrate_entries(entries: Sequence[TriageEntry]) -> tuple[TriageEntry, ...]:
    """The ``migrate`` subset of ``entries`` — the only rows safe to transfer."""
    return tuple(entry for entry in entries if entry.disposition == "migrate")


def transfer_entries(
    repo: str,
    dest: str,
    entries: Sequence[TriageEntry],
    runner: Runner = run_command,
) -> dict[int, int]:
    """Transfer ``migrate`` rows from ``repo`` to ``dest``.

    Returns the old -> new issue-number map.  Pass :func:`migrate_entries` output:
    a ``delete``/``scrub`` row anywhere in ``entries`` raises
    :class:`PermissionError` (they carry secrets or internal endpoints and are
    human-owned), so a stray flag can never move a sensitive row.
    """
    refused = destructive_entry_numbers(entries)
    if refused:
        raise PermissionError(f"refusing to transfer destructive rows: {refused}")
    owner, _, name = dest.partition("/")
    mapping: dict[int, int] = {}
    for entry in entries:
        if entry.disposition != "migrate":
            continue
        output = runner(
            [
                "gh",
                "api",
                f"repos/{repo}/issues/{entry.issue_number}/transfer",
                "-f",
                f"new_owner={owner}",
                "-f",
                f"new_repo_name={name}",
            ]
        )
        mapping[entry.issue_number] = json.loads(output)["number"]
    return mapping


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="nebuko/nebuko")
    parser.add_argument("--dest", default=ARCHIVE_REPO)
    parser.add_argument(
        "--apply-labels",
        action="store_true",
        help="create the manifest labels and additively apply them",
    )
    parser.add_argument(
        "--transfer",
        action="store_true",
        help="transfer migrate rows to --dest (gated on human review)",
    )
    parser.add_argument(
        "--migration-map",
        type=Path,
        default=Path(__file__).with_name("migration_map.json"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.apply_labels:
        ensure_labels(args.repo, LABELS)
        apply_entries(args.repo, MANIFEST)
    if args.transfer:
        mapping = transfer_entries(args.repo, args.dest, migrate_entries(MANIFEST))
        args.migration_map.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")
    if not (args.apply_labels or args.transfer):
        print(
            f"{len(MANIFEST)} entries | "
            f"{len(destructive_entry_numbers(MANIFEST))} destructive (human-owned)"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
