"""Tests for the #827 triage manifest applier (``scripts/triage``).

The external ``gh`` calls are isolated behind the ``Runner`` port, so the tests
inject a recording double for the port and assert on the emitted commands.  The
``run_command`` adapter itself is exercised against real ``echo``/``false``
binaries.
"""

from __future__ import annotations

import json

import pytest

from triage.apply_triage import (
    CommandError,
    apply_entries,
    destructive_entry_numbers,
    ensure_labels,
    label_args,
    main,
    migrate_entries,
    run_command,
    transfer_entries,
)
from triage.triage_manifest import (
    ARCHIVE_REPO,
    DESTRUCTIVE_DISPOSITIONS,
    LABELS,
    MANIFEST,
    TriageEntry,
)

ALLOWED_DISPOSITIONS = {"keep", "label", "migrate", "delete", "scrub"}
ALLOWED_CATEGORIES = {"competitive", "research", "strategy", "ideation", "secret", "junk"}


class RecordingRunner:
    """Test double for the ``Runner`` port: records commands, replays canned output."""

    def __init__(self, outputs=("{}",)):
        self.commands: list[list[str]] = []
        self._outputs = list(outputs)

    def __call__(self, args):
        self.commands.append(list(args))
        return self._outputs.pop(0) if self._outputs else "{}"


# --- run_command adapter (real subprocess, no mocks) -------------------------


def test_run_command_returns_stdout() -> None:
    assert run_command(["echo", "hi"]).strip() == "hi"


def test_run_command_raises_on_nonzero_exit() -> None:
    with pytest.raises(CommandError):
        run_command(["false"])


# --- ensure_labels -----------------------------------------------------------


def test_ensure_labels_creates_each_label_idempotently() -> None:
    runner = RecordingRunner()
    ensure_labels("nebuko/nebuko", LABELS, runner)

    assert len(runner.commands) == len(LABELS)
    for spec, command in zip(LABELS, runner.commands, strict=True):
        assert command[:4] == ["gh", "label", "create", spec.name]
        assert "--force" in command
        assert command[command.index("--color") + 1] == spec.color
        assert command[command.index("--description") + 1] == spec.description


# --- apply_entries: additive labels only -------------------------------------


def test_apply_entries_only_adds_labels_never_mutates_content() -> None:
    runner = RecordingRunner()
    entries = (
        TriageEntry(1, "keep", ("research",), "research", "why"),
        TriageEntry(2, "migrate", ("competitive-research", "needs-scrub"), "competitive", "why"),
        TriageEntry(3, "keep", (), "ideation", "no labels to apply"),
    )

    apply_entries("nebuko/nebuko", entries, runner)

    assert [c[3] for c in runner.commands] == ["1", "2"]
    for command in runner.commands:
        assert command[:3] == ["gh", "issue", "edit"]
        flags = {a for a in command if a.startswith("--")}
        assert flags <= {"--repo", "--add-label"}, "applier must not mutate issue content"


def test_apply_entries_labels_destructive_rows_without_deleting() -> None:
    """The human queue is a label: destructive rows are labelled, never actioned."""
    runner = RecordingRunner()
    entries = (TriageEntry(739, "delete", ("needs-scrub",), "secret", "why"),)

    apply_entries("nebuko/nebuko", entries, runner)

    assert len(runner.commands) == 1
    command = runner.commands[0]
    assert command[:3] == ["gh", "issue", "edit"]
    assert "739" in command
    assert "needs-scrub" in command


def test_label_args_expands_every_label() -> None:
    entry = TriageEntry(7, "migrate", ("research", "strategy"), "research", "why")
    assert label_args(entry) == ["--add-label", "research", "--add-label", "strategy"]


# --- destructive guard -------------------------------------------------------


def test_destructive_entry_numbers_selects_delete_and_scrub() -> None:
    entries = (
        TriageEntry(1, "delete", ("needs-scrub",), "secret", "why"),
        TriageEntry(2, "scrub", ("needs-scrub",), "secret", "why"),
        TriageEntry(3, "keep", ("research",), "research", "why"),
    )
    assert destructive_entry_numbers(entries) == (1, 2)


def test_transfer_refuses_destructive_rows_without_calling_runner() -> None:
    runner = RecordingRunner()
    entries = (
        TriageEntry(721, "delete", ("needs-scrub",), "secret", "why"),
        TriageEntry(697, "migrate", ("competitive-research",), "competitive", "why"),
    )

    with pytest.raises(PermissionError):
        transfer_entries("nebuko/nebuko", ARCHIVE_REPO, entries, runner)

    assert runner.commands == []


def test_migrate_entries_selects_only_transfer_safe_rows() -> None:
    subset = migrate_entries(MANIFEST)
    assert subset
    assert all(entry.disposition == "migrate" for entry in subset)
    assert destructive_entry_numbers(subset) == ()


def test_main_transfer_path_passes_only_migrate_rows(monkeypatch, tmp_path) -> None:
    seen: list[TriageEntry] = []

    def fake_transfer(repo, dest, entries, runner=run_command):
        seen.extend(entries)
        return {}

    monkeypatch.setattr("triage.apply_triage.transfer_entries", fake_transfer)
    out = tmp_path / "migration_map.json"

    assert main(["--transfer", "--migration-map", str(out)]) == 0

    assert seen and all(entry.disposition == "migrate" for entry in seen)
    assert json.loads(out.read_text()) == {}


# --- transfer_entries --------------------------------------------------------


def test_transfer_migrates_only_migrate_rows_and_builds_api_call() -> None:
    runner = RecordingRunner(outputs=[json.dumps({"number": 42})])
    entries = (
        TriageEntry(697, "migrate", ("competitive-research",), "competitive", "why"),
        TriageEntry(130, "keep", ("research",), "research", "keep in place"),
    )

    mapping = transfer_entries("nebuko/nebuko", "nebuko/nebuko-archive", entries, runner)

    assert mapping == {697: 42}
    assert len(runner.commands) == 1
    command = runner.commands[0]
    assert command[:3] == ["gh", "api", "repos/nebuko/nebuko/issues/697/transfer"]
    assert "new_owner=nebuko" in command
    assert "new_repo_name=nebuko-archive" in command


# --- manifest integrity ------------------------------------------------------


def test_manifest_dispositions_and_categories_are_valid() -> None:
    for entry in MANIFEST:
        assert entry.disposition in ALLOWED_DISPOSITIONS
        assert entry.category in ALLOWED_CATEGORIES
        assert entry.rationale


def test_manifest_issue_numbers_are_unique() -> None:
    numbers = [entry.issue_number for entry in MANIFEST]
    assert len(numbers) == len(set(numbers))


def test_manifest_only_uses_known_labels() -> None:
    known = {spec.name for spec in LABELS} | {"research"}
    for entry in MANIFEST:
        assert set(entry.labels) <= known


def test_destructive_rows_carry_the_scrub_label() -> None:
    for entry in MANIFEST:
        if entry.disposition in DESTRUCTIVE_DISPOSITIONS:
            assert "needs-scrub" in entry.labels


def test_label_specs_use_valid_hex_colors_and_do_not_recreate_research() -> None:
    for spec in LABELS:
        assert len(spec.color) == 6
        assert all(char in "0123456789ABCDEFabcdef" for char in spec.color)
    assert "research" not in {spec.name for spec in LABELS}
