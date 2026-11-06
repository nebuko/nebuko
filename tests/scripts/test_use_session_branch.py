"""Contract tests for ``use-session-branch.sh`` and the red-test loop's tests.

``red-test-review-loop``'s ``run_tests`` step used to test the bare feature
branch head, while CI tests GitHub's PR merge commit (branch head merged into
``main``). The two runs therefore exercised different code and reported
different failure counts (issue #1344).

These tests pin the fix:

- ``NEBUKO_MERGE_MAIN=1`` makes the script merge ``origin/main`` into the
  detached branch head, locally and without pushing.
- The flag defaults to off, and ``git_diff`` never sets it.
- A conflict aborts the merge, keeps the branch head and still exits 0.
- Every run prints one ``tested:`` line naming the tree it tested.

RED against the current script and workflow: the tests fail until the merge
block lands in ``.nebuko/scripts/use-session-branch.sh`` and the flag is set in
``.nebuko/workflows/red-test-review-loop.yaml``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".nebuko" / "scripts" / "use-session-branch.sh"
WORKFLOW = REPO_ROOT / ".nebuko" / "workflows" / "red-test-review-loop.yaml"

ISSUE_NUMBER = 1344


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class SessionRepo:
    """Temp bare origin + clone with main and feature/issue-<n>-x branches."""

    root: Path
    origin: Path
    issue_number: int

    # @stub-protected: signature defined by architect — do not modify
    def __init__(self, tmp_path: Path, issue_number: int) -> None:
        self.root = tmp_path / "clone"
        self.origin = tmp_path / "origin.git"
        self.issue_number = issue_number

        subprocess.run(
            ["git", "init", "--quiet", "--bare", str(self.origin)], check=True
        )
        subprocess.run(
            ["git", "clone", "--quiet", str(self.origin), str(self.root)], check=True
        )
        _git(self.root, "config", "user.name", "test")
        _git(self.root, "config", "user.email", "test@invalid")
        _git(self.root, "checkout", "--quiet", "-b", "main")
        _git(self.root, "commit", "--quiet", "--allow-empty", "-m", "root")
        _git(self.root, "push", "--quiet", "origin", "main")
        _git(self.root, "checkout", "--quiet", "-b", self.branch, "main")

    @property
    def branch(self) -> str:
        return f"feature/issue-{self.issue_number}-x"

    # @stub-protected: signature defined by architect — do not modify
    def commit_on(self, branch: str, path: str, content: str) -> str:
        _git(self.root, "checkout", "--quiet", branch)
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _git(self.root, "add", path)
        _git(self.root, "commit", "--quiet", "-m", f"{branch}: {path}")
        _git(self.root, "push", "--quiet", "origin", branch)
        return _git(self.root, "rev-parse", "HEAD")

    # @stub-protected: signature defined by architect — do not modify
    def run_script(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        event = self.root / "event.json"
        event.write_text(
            json.dumps({"issue": {"number": self.issue_number}}), encoding="utf-8"
        )
        full_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.root.parent),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GITHUB_EVENT_PATH": str(event),
            **env,
        }
        return subprocess.run(
            ["sh", str(SCRIPT)],
            cwd=self.root,
            env=full_env,
            capture_output=True,
            text=True,
        )

    # @stub-protected: signature defined by architect — do not modify
    def head_file(self, path: str) -> str | None:
        result = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        return result.stdout if result.returncode == 0 else None

    def head_sha(self) -> str:
        return _git(self.root, "rev-parse", "HEAD")

    def origin_sha(self, branch: str) -> str:
        return _git(self.origin, "rev-parse", f"refs/heads/{branch}")


def _workflow_command(step_name: str) -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(s for s in workflow["steps"] if s["name"] == step_name)
    args = step["script"]["args"]
    return "\n".join(args)


class TestUseSessionBranchMergeMain:
    def test_without_flag_checks_out_branch_head_only(
        self, tmp_path: Path
    ) -> None:
        """Given a branch ahead of main, when the script runs without the flag,
        then it tests the branch head and says so."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "main_only.txt", "main")
        branch_head = repo.commit_on(repo.branch, "branch_only.txt", "branch")

        result = repo.run_script({})

        assert result.returncode == 0, result.stderr
        assert repo.head_sha() == branch_head
        assert repo.head_file("branch_only.txt") == "branch"
        assert repo.head_file("main_only.txt") is None
        assert f"tested: branch head {branch_head}" in result.stdout

    def test_with_flag_tree_contains_main_and_branch_changes(
        self, tmp_path: Path
    ) -> None:
        """Given main and the branch each add a file, when the script runs with
        NEBUKO_MERGE_MAIN=1, then HEAD is a merge commit carrying both."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "main_only.txt", "main")
        repo.commit_on(repo.branch, "branch_only.txt", "branch")

        result = repo.run_script({"NEBUKO_MERGE_MAIN": "1"})

        assert result.returncode == 0, result.stderr
        assert repo.head_file("main_only.txt") == "main"
        assert repo.head_file("branch_only.txt") == "branch"
        parents = _git(repo.root, "rev-list", "--parents", "-n", "1", "HEAD").split()
        assert len(parents) == 3, f"HEAD is not a merge commit: {parents}"

    def test_with_flag_prints_tested_merge_line(self, tmp_path: Path) -> None:
        """Given the flag is set, when the script runs, then it names the head
        and the origin/main sha it merged."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "main_only.txt", "main")
        branch_head = repo.commit_on(repo.branch, "branch_only.txt", "branch")
        main_sha = repo.origin_sha("main")

        result = repo.run_script({"NEBUKO_MERGE_MAIN": "1"})

        assert result.returncode == 0, result.stderr
        assert f"tested: merge of {branch_head} into {main_sha}" in result.stdout

    def test_conflict_aborts_merge_and_keeps_branch_head(
        self, tmp_path: Path
    ) -> None:
        """Given both sides edit the same file, when the script merges with the
        flag, then it aborts, keeps the branch head and exits 0."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "shared.txt", "main")
        branch_head = repo.commit_on(repo.branch, "shared.txt", "branch")

        result = repo.run_script({"NEBUKO_MERGE_MAIN": "1"})

        assert result.returncode == 0, result.stderr
        assert "merge: conflict with origin/main; testing branch head" in result.stdout
        assert f"tested: branch head {branch_head}" in result.stdout
        assert repo.head_sha() == branch_head
        assert repo.head_file("shared.txt") == "branch"
        assert not (repo.root / ".git" / "MERGE_HEAD").exists()

    def test_merge_commit_is_not_pushed(self, tmp_path: Path) -> None:
        """Given the flag is set, when the script merges, then origin still
        points at the branch head."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "main_only.txt", "main")
        branch_head = repo.commit_on(repo.branch, "branch_only.txt", "branch")

        result = repo.run_script({"NEBUKO_MERGE_MAIN": "1"})

        assert result.returncode == 0, result.stderr
        assert repo.head_sha() != branch_head
        assert repo.origin_sha(repo.branch) == branch_head


class TestRedTestReviewLoopRunTests:
    def test_run_tests_command_sets_merge_main_flag(self) -> None:
        """Given the workflow, when run_tests runs the script, then the flag is
        set and the pytest selection is unchanged."""
        command = _workflow_command("run_tests")

        assert "NEBUKO_MERGE_MAIN=1 sh .nebuko/scripts/use-session-branch.sh" in command
        assert "pytest tests src/nebuko/engine/lib -q --tb=short -rf" in command

    def test_git_diff_step_does_not_set_merge_main_flag(self) -> None:
        """Given the workflow, when git_diff runs the script, then it does not
        merge main into the diff — only run_tests opts in."""
        command = _workflow_command("git_diff")

        assert "use-session-branch.sh" in command
        assert "NEBUKO_MERGE_MAIN" not in command
        # The flag exists and is opt-in: the script reads it, git_diff omits it.
        assert "NEBUKO_MERGE_MAIN" in SCRIPT.read_text(encoding="utf-8")
