"""Secret scanning and redaction for what leaves the sandbox (issue #1347).

Two channels publish agent output: the git history (commits) and the issue
comment (answer bodies). Both are public, and agents routinely paste raw tool
output — CI logs, env dumps, artifacts — into them. ``redact_secrets`` in
``run_failure`` only replaces *known* engine keys, so an unknown credential in a
pasted log reaches the pushed history and the thread.

This module is the thin port the engine and the git guard share:

* :func:`scan_text` / :func:`redact_text` — pure, rule-based scanning of a
  string; a hit is replaced by ``<REDACTED:{rule}>``.
* :func:`scan_git_range` — the pre-push scan over unpublished commits
  (``origin/<branch>..HEAD``), delegating to the ``gitleaks`` binary when it is
  installed and falling back to the builtin rules otherwise.
* :func:`redact_worktree` — rewrite the files an unpublished commit touched so
  the re-committed history carries the marker instead of the value.

The value itself is never returned to a caller that persists it: findings carry
the file, the line and the rule only.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

from nebuko.engine.lib.workflow_state.sub_domains.secret_scan.secret_scan import (
    SecretFinding,
)

__all__ = [
    "REDACTION_PREFIX",
    "Finding",
    "redact_text",
    "redact_worktree",
    "scan_git_range",
    "scan_text",
]

#: Marker written in place of a matched value. The rule name rides inside it so
#: a human can tell what was removed without ever seeing the value.
REDACTION_PREFIX = "<REDACTED:"

#: Builtin rules, used when ``gitleaks`` is not installed (and as the fallback
#: for the markdown/comment channel, which gitleaks does not scan).
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("github-oauth", re.compile(r"gho_[A-Za-z0-9]{36}")),
    ("github-app-token", re.compile(r"(?:ghu|ghs)_[A-Za-z0-9]{36}")),
    ("github-personal-token", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("github-refresh-token", re.compile(r"ghr_[A-Za-z0-9]{36}")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    ),
    ("openai-key", re.compile(r"sk-(?:ant-)?[A-Za-z0-9_-]{20,}")),
)

#: Paths whose content is evidence (logs, artifacts) rather than source. A
#: redaction there is expected; one in source code is louder (issue #1347).
_EVIDENCE_SUFFIXES = (".log", ".jsonl", ".out", ".err", ".txt")
_EVIDENCE_DIRS = ("nebuko-task-logs/",)


@dataclass(frozen=True)
class Finding:
    """One match: where it was and which rule caught it (never the value)."""

    file: str
    line: int
    rule: str
    start: int = 0
    end: int = 0

    def as_state(self) -> SecretFinding:
        return SecretFinding(file=self.file, line=self.line, rule=self.rule)


def is_evidence_path(path: str) -> bool:
    """True for log/artifact paths, False for source files."""
    normalized = path.replace("\\", "/").lstrip("./")
    if any(normalized.startswith(d) or f"/{d}" in f"/{normalized}" for d in _EVIDENCE_DIRS):
        return True
    return normalized.endswith(_EVIDENCE_SUFFIXES)


def _allowlist(root: Optional[Path]) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]]]:
    """``(path_patterns, regex_patterns)`` from ``<root>/.gitleaks.toml``.

    The repo's allowlist is the single source of truth for known fixtures, so
    the builtin fallback honours it too. A missing or unparseable config
    allowlists nothing.
    """
    if root is None:
        return [], []
    config = Path(root) / ".gitleaks.toml"
    if not config.is_file():
        return [], []
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return [], []
    allowlist = data.get("allowlist") or data.get("allowlists") or {}
    if isinstance(allowlist, list):
        allowlist = allowlist[0] if allowlist else {}
    paths = allowlist.get("paths") or []
    regexes = allowlist.get("regexes") or []
    return (
        [re.compile(p) for p in paths],
        [re.compile(r) for r in regexes],
    )


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _allowed(
    match: re.Match[str],
    path: str,
    path_patterns: Sequence[re.Pattern[str]],
    regex_patterns: Sequence[re.Pattern[str]],
) -> bool:
    if any(p.search(path) for p in path_patterns):
        return True
    return any(r.search(match.group(0)) for r in regex_patterns)


def _scan(
    text: str, path: str, root: Optional[Path]
) -> list[Finding]:
    path_patterns, regex_patterns = _allowlist(root)
    findings: list[Finding] = []
    for rule, pattern in RULES:
        for match in pattern.finditer(text):
            if _allowed(match, path, path_patterns, regex_patterns):
                continue
            findings.append(
                Finding(
                    file=path,
                    line=_line_of(text, match.start()),
                    rule=rule,
                    start=match.start(),
                    end=match.end(),
                )
            )
    findings.sort(key=lambda f: f.start)
    return findings


def scan_text(text: str, path: str = "", root: Optional[Path] = None) -> list[Finding]:
    """Every rule match in ``text``, in order of appearance."""
    return _scan(text, path, root)


def redact_text(
    text: str, path: str = "", root: Optional[Path] = None
) -> tuple[str, list[Finding]]:
    """Replace every match in ``text`` with ``<REDACTED:{rule}>``.

    Returns the redacted text and the findings (file/line/rule only).
    """
    findings = _scan(text, path, root)
    if not findings:
        return text, []
    pieces: list[str] = []
    cursor = 0
    for finding in findings:
        pieces.append(text[cursor : finding.start])
        pieces.append(f"{REDACTION_PREFIX}{finding.rule}>")
        cursor = finding.end
    pieces.append(text[cursor:])
    return "".join(pieces), findings


def _git(repo_root: Path, args: list[str], timeout: int = 60) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def _gitleaks_binary() -> Optional[str]:
    return shutil.which("gitleaks")


def _gitleaks_findings(repo_root: Path, rev_range: str) -> Optional[list[Finding]]:
    """Findings from the ``gitleaks`` binary, or ``None`` when unavailable."""
    binary = _gitleaks_binary()
    if binary is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "gitleaks.json"
        try:
            subprocess.run(
                [
                    binary,
                    "git",
                    "--log-opts",
                    rev_range,
                    "--report-format",
                    "json",
                    "--report-path",
                    str(report),
                    "--no-banner",
                ],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if not report.is_file():
            return None
        try:
            payload = json.loads(report.read_text(encoding="utf-8") or "[]")
        except (OSError, json.JSONDecodeError):
            return None
    findings: list[Finding] = []
    for entry in payload if isinstance(payload, list) else []:
        if not isinstance(entry, dict):
            continue
        findings.append(
            Finding(
                file=str(entry.get("File") or ""),
                line=int(entry.get("StartLine") or 0),
                rule=str(entry.get("RuleID") or "gitleaks"),
            )
        )
    return findings


def _fallback_git_findings(repo_root: Path, rev_range: str) -> list[Finding]:
    """Scan the added lines of ``rev_range`` with the builtin rules.

    Line numbers are the new-file numbers of the diff, which is what a human
    needs to find the redaction marker afterwards.
    """
    try:
        patch = _git(
            repo_root,
            ["log", "-p", "--no-color", "--unified=0", "--format=%H", rev_range],
        )
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return []
    findings: list[Finding] = []
    path = ""
    new_line = 0
    for raw in patch.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[len("+++ b/") :]
            continue
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            new_line = int(match.group(1)) if match else 0
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            for finding in _scan(raw[1:], path, repo_root):
                findings.append(
                    Finding(
                        file=finding.file,
                        line=new_line,
                        rule=finding.rule,
                        start=finding.start,
                        end=finding.end,
                    )
                )
            new_line += 1
        elif raw.startswith(" "):
            new_line += 1
    return findings


def scan_git_range(repo_root: Path, rev_range: str) -> list[Finding]:
    """Scan the commits in ``rev_range`` (e.g. ``origin/main..HEAD``).

    ``gitleaks`` is used when installed (the CI/dev-shell case); otherwise the
    builtin rules scan the added diff lines, so a host without the binary still
    refuses to publish a credential.
    """
    if not rev_range or ".." not in rev_range:
        return []
    gitleaks = _gitleaks_findings(Path(repo_root), rev_range)
    if gitleaks is not None:
        return gitleaks
    return _fallback_git_findings(Path(repo_root), rev_range)


def redact_worktree(
    repo_root: Path, paths: Iterable[str], root: Optional[Path] = None
) -> list[Finding]:
    """Redact every match in ``paths`` in place; return the findings.

    A path that no longer exists (a deleted file in the commit range) is
    skipped. Binary files are skipped: a match there is not a pasted log.
    """
    repo = Path(repo_root)
    allow_root = root if root is not None else repo
    findings: list[Finding] = []
    for relative in paths:
        target = repo / relative
        if not target.is_file():
            continue
        try:
            raw = target.read_bytes()
            if b"\x00" in raw:
                continue
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        redacted, hits = redact_text(text, relative, allow_root)
        if not hits:
            continue
        try:
            target.write_text(redacted, encoding="utf-8")
        except OSError:
            continue
        findings.extend(hits)
    return findings
