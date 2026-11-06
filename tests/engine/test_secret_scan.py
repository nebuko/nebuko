"""Secret scanning and redaction before anything leaves the sandbox (#1347).

A pasted CI log used to reach the pushed history and the issue comment
unchanged: the only redaction was ``redact_secrets``, an exact-string replace
for known engine keys. These tests pin the rule-based scan, the allowlist for
the repo's own fake fixtures, and the ``<REDACTED:{rule}>`` marker.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from nebuko.engine.lib.redaction import (
    REDACTION_PREFIX,
    is_evidence_path,
    redact_text,
    redact_worktree,
    scan_git_range,
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


def _fake_gitleaks(tmp_path: Path, monkeypatch, body: str) -> Path:
    """Install a fake ``gitleaks`` on PATH and return its directory."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "gitleaks"
    script.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return bin_dir


class TestGitleaksBinary:
    """The binary path (CI/dev shell) parses gitleaks' JSON report."""

    def test_uses_the_binary_and_parses_its_report(self, tmp_path: Path, monkeypatch):
        bin_dir = _fake_gitleaks(
            tmp_path,
            monkeypatch,
            "\n".join(
                [
                    "import json, sys",
                    "args = sys.argv[1:]",
                    "open(sys.argv[0] + '.args', 'w').write(' '.join(args))",
                    "report = args[args.index('--report-path') + 1]",
                    "json.dump([{'File': 'console.txt', 'StartLine': 4,",
                    "            'RuleID': 'github-oauth'}], open(report, 'w'))",
                    "sys.exit(1)",
                ]
            ),
        )

        findings = scan_git_range(tmp_path, "origin/main..HEAD")

        assert [(f.file, f.line, f.rule) for f in findings] == [
            ("console.txt", 4, "github-oauth")
        ]
        argv = (bin_dir / "gitleaks.args").read_text(encoding="utf-8")
        assert argv.split()[0] == "git"
        assert "--log-opts origin/main..HEAD" in argv
        assert "--report-format json" in argv

    def test_a_binary_without_a_report_falls_back_to_the_builtin_rules(
        self, tmp_path: Path, monkeypatch
    ):
        _fake_gitleaks(tmp_path, monkeypatch, "import sys\nsys.exit(2)\n")
        repo = tmp_path / "repo"
        repo.mkdir()
        git = ["git", "-C", str(repo)]
        subprocess.run([*git, "init", "-b", "main"], check=True, capture_output=True)
        subprocess.run(
            [*git, "config", "user.email", "t@example.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [*git, "config", "user.name", "t"], check=True, capture_output=True
        )
        (repo / "console.txt").write_text("clean\n", encoding="utf-8")
        subprocess.run([*git, "add", "."], check=True, capture_output=True)
        subprocess.run([*git, "commit", "-m", "base"], check=True, capture_output=True)
        (repo / "console.txt").write_text(f"leak {FAKE_OAUTH}\n", encoding="utf-8")
        subprocess.run([*git, "commit", "-am", "leak"], check=True, capture_output=True)

        findings = scan_git_range(repo, "HEAD~1..HEAD")

        assert [(f.file, f.line, f.rule) for f in findings] == [
            ("console.txt", 1, "github-oauth")
        ]

    def test_an_unparseable_report_falls_back(self, tmp_path: Path, monkeypatch):
        _fake_gitleaks(
            tmp_path,
            monkeypatch,
            "\n".join(
                [
                    "import sys",
                    "args = sys.argv[1:]",
                    "open(args[args.index('--report-path') + 1], 'w').write('not json')",
                    "sys.exit(1)",
                ]
            ),
        )
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "-C", str(repo), "init", "-b", "main"],
            check=True,
            capture_output=True,
        )

        # No commits yet: the fallback finds nothing and must not raise.
        assert scan_git_range(repo, "HEAD~1..HEAD") == []


class TestEvidencePath:
    @pytest.mark.parametrize(
        "path", ["console.txt", "nebuko-task-logs/console.txt", "build/run.log"]
    )
    def test_evidence_paths(self, path):
        assert is_evidence_path(path)

    @pytest.mark.parametrize("path", ["src/app.py", "tests/test_app.py"])
    def test_source_paths(self, path):
        assert not is_evidence_path(path)
