"""Secret scanning and redaction before anything leaves the sandbox (#1347).

A pasted CI log used to reach the pushed history and the issue comment
unchanged: the only redaction was ``redact_secrets``, an exact-string replace
for known engine keys. These tests pin the rule-based scan, the allowlist for
the repo's own fake fixtures, and the ``<REDACTED:{rule}>`` marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nebuko.engine.lib.redaction import (
    REDACTION_PREFIX,
    is_evidence_path,
    redact_text,
    redact_worktree,
    scan_text,
)

# Built from parts so this file never trips a scanner on itself.
FAKE_OAUTH = "gho_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
FAKE_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_KEY = "sk-" + "proj-abcdefghijklmnopqrstuvwxyz012345"


class TestScanText:
    def test_finds_a_github_oauth_token(self):
        findings = scan_text(f"token={FAKE_OAUTH}\n", "log.txt")

        assert [f.rule for f in findings] == ["github-oauth"]
        assert findings[0].file == "log.txt"
        assert findings[0].line == 1

    def test_reports_the_line_not_the_value(self):
        findings = scan_text(f"a\nb\n{FAKE_AWS}\n", "env.txt")

        assert findings[0].line == 3
        assert FAKE_AWS not in repr(findings[0].as_state().model_dump())

    def test_clean_text_has_no_findings(self):
        assert scan_text("nothing to see here\n") == []


class TestRedactText:
    def test_replaces_the_value_with_the_rule_marker(self):
        redacted, findings = redact_text(f"token={FAKE_OAUTH}", "log.txt")

        assert FAKE_OAUTH not in redacted
        assert f"{REDACTION_PREFIX}github-oauth>" in redacted
        assert len(findings) == 1

    def test_redacts_every_match(self):
        text = f"{FAKE_OAUTH} and {FAKE_KEY}"

        redacted, findings = redact_text(text)

        assert FAKE_OAUTH not in redacted and FAKE_KEY not in redacted
        assert len(findings) == 2

    def test_leaves_clean_text_untouched(self):
        text = "plain output\n"

        assert redact_text(text) == (text, [])


class TestAllowlist:
    def test_a_configured_fixture_path_is_allowlisted(self, tmp_path: Path):
        (tmp_path / ".gitleaks.toml").write_text(
            "[allowlist]\npaths = ['''tests/fixtures/.*''']\n", encoding="utf-8"
        )

        assert scan_text(FAKE_OAUTH, "tests/fixtures/token.txt", tmp_path) == []

    def test_a_non_allowlisted_path_still_matches(self, tmp_path: Path):
        (tmp_path / ".gitleaks.toml").write_text(
            "[allowlist]\npaths = ['''tests/fixtures/.*''']\n", encoding="utf-8"
        )

        assert scan_text(FAKE_OAUTH, "src/app.py", tmp_path)

    def test_a_regex_allowlist_skips_the_placeholder(self, tmp_path: Path):
        (tmp_path / ".gitleaks.toml").write_text(
            "[allowlist]\nregexes = ['''<REDACTED:[a-z-]+>''']\n", encoding="utf-8"
        )

        assert scan_text(f"{REDACTION_PREFIX}github-oauth>", "a.md", tmp_path) == []

    def test_the_repo_config_allowlists_its_own_tests(self):
        root = Path(__file__).resolve().parents[2]

        assert scan_text(FAKE_OAUTH, "tests/engine/test_secret_scan.py", root) == []


class TestRedactWorktree:
    def test_rewrites_the_file_in_place(self, tmp_path: Path):
        target = tmp_path / "console.txt"
        target.write_text(f"leaked {FAKE_OAUTH}\n", encoding="utf-8")

        findings = redact_worktree(tmp_path, ["console.txt"])

        assert FAKE_OAUTH not in target.read_text(encoding="utf-8")
        assert [f.rule for f in findings] == ["github-oauth"]

    def test_skips_a_missing_file(self, tmp_path: Path):
        assert redact_worktree(tmp_path, ["gone.txt"]) == []

    def test_skips_binary_files(self, tmp_path: Path):
        target = tmp_path / "blob.bin"
        target.write_bytes(b"\x00" + FAKE_OAUTH.encode())

        assert redact_worktree(tmp_path, ["blob.bin"]) == []
        assert FAKE_OAUTH.encode() in target.read_bytes()


class TestEvidencePath:
    @pytest.mark.parametrize(
        "path", ["console.txt", "nebuko-task-logs/console.txt", "build/run.log"]
    )
    def test_evidence_paths(self, path):
        assert is_evidence_path(path)

    @pytest.mark.parametrize("path", ["src/app.py", "tests/test_app.py"])
    def test_source_paths(self, path):
        assert not is_evidence_path(path)
