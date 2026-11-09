"""Contract tests for ``use-session-branch.sh`` and the red-test loop's tests.

``red-test-review-loop``'s ``run_tests`` step used to test the bare feature
branch head, while CI tests GitHub's PR merge commit (branch head merged into
``main``). The two runs therefore exercised different code and reported
different failure counts (issue #1344).

These tests pin the fix:

- ``merge_main`` in ``.nebuko/nebuko.yaml`` decides whether the script merges
  ``origin/main`` into the detached branch head, locally and without pushing.
  It defaults to **true**; ``NEBUKO_MERGE_MAIN=0/1`` overrides it per step.
- ``git_diff`` sets ``NEBUKO_MERGE_MAIN=0`` so the diff never pulls main in.
- A conflict aborts the merge, keeps the branch head and still exits 0.
- Every run prints one ``tested:`` line naming the tree it tested.
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
DEV_REVIEW_WORKFLOW = REPO_ROOT / ".nebuko" / "workflows" / "dev-review-loop.yaml"
MANIFEST = REPO_ROOT / ".nebuko" / "nebuko.yaml"

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

    def write_manifest(self, content: str) -> None:
        """Write ``.nebuko/nebuko.yaml`` in the checkout the script runs in."""
        manifest = self.root / ".nebuko" / "nebuko.yaml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(content, encoding="utf-8")


def _workflow_command(step_name: str, path: Path = WORKFLOW) -> str:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    step = next(s for s in workflow["steps"] if s["name"] == step_name)
    args = step["script"]["args"]
    return "\n".join(args)


class TestUseSessionBranchMergeMain:
    def test_without_manifest_or_env_merges_main(self, tmp_path: Path) -> None:
        """Given neither a manifest nor the env var, when the script runs, then
        the default is on: it tests the branch head merged with origin/main."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "main_only.txt", "main")
        repo.commit_on(repo.branch, "branch_only.txt", "branch")

        result = repo.run_script({})

        assert result.returncode == 0, result.stderr
        assert repo.head_file("main_only.txt") == "main"
        assert repo.head_file("branch_only.txt") == "branch"
        assert "tested: merge of " in result.stdout

    def test_env_flag_off_checks_out_branch_head_only(self, tmp_path: Path) -> None:
        """Given NEBUKO_MERGE_MAIN=0, when the script runs, then it tests the
        branch head and says so."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "main_only.txt", "main")
        branch_head = repo.commit_on(repo.branch, "branch_only.txt", "branch")

        result = repo.run_script({"NEBUKO_MERGE_MAIN": "0"})

        assert result.returncode == 0, result.stderr
        assert repo.head_sha() == branch_head
        assert repo.head_file("branch_only.txt") == "branch"
        assert repo.head_file("main_only.txt") is None
        assert f"tested: branch head {branch_head}" in result.stdout

    def test_manifest_false_opts_out(self, tmp_path: Path) -> None:
        """Given `merge_main: false` in .nebuko/nebuko.yaml, when the script
        runs, then it tests the branch head."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "main_only.txt", "main")
        branch_head = repo.commit_on(repo.branch, "branch_only.txt", "branch")
        repo.write_manifest("merge_main: false\n")

        result = repo.run_script({})

        assert result.returncode == 0, result.stderr
        assert repo.head_sha() == branch_head
        assert f"tested: branch head {branch_head}" in result.stdout

    def test_manifest_true_merges(self, tmp_path: Path) -> None:
        """Given `merge_main: true`, when the script runs, then HEAD is a merge
        commit carrying both main's and the branch's changes."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "main_only.txt", "main")
        repo.commit_on(repo.branch, "branch_only.txt", "branch")
        repo.write_manifest("merge_main: true\n")

        result = repo.run_script({})

        assert result.returncode == 0, result.stderr
        assert repo.head_file("main_only.txt") == "main"
        assert repo.head_file("branch_only.txt") == "branch"
        parents = _git(repo.root, "rev-list", "--parents", "-n", "1", "HEAD").split()
        assert len(parents) == 3, f"HEAD is not a merge commit: {parents}"

    def test_env_flag_overrides_the_manifest(self, tmp_path: Path) -> None:
        """Given `merge_main: true` and NEBUKO_MERGE_MAIN=0, when the script
        runs, then the env var wins and the branch head is tested."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "main_only.txt", "main")
        branch_head = repo.commit_on(repo.branch, "branch_only.txt", "branch")
        repo.write_manifest("merge_main: true\n")

        result = repo.run_script({"NEBUKO_MERGE_MAIN": "0"})

        assert result.returncode == 0, result.stderr
        assert repo.head_sha() == branch_head
        assert f"tested: branch head {branch_head}" in result.stdout

    def test_merge_prints_tested_merge_line(self, tmp_path: Path) -> None:
        """Given the merge runs, when it succeeds, then it names the head and
        the origin/main sha it merged."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "main_only.txt", "main")
        branch_head = repo.commit_on(repo.branch, "branch_only.txt", "branch")
        main_sha = repo.origin_sha("main")

        result = repo.run_script({})

        assert result.returncode == 0, result.stderr
        assert f"tested: merge of {branch_head} into {main_sha}" in result.stdout

    def test_conflict_aborts_merge_and_keeps_branch_head(
        self, tmp_path: Path
    ) -> None:
        """Given both sides edit the same file, when the script merges, then it
        aborts, keeps the branch head and exits 0."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "shared.txt", "main")
        branch_head = repo.commit_on(repo.branch, "shared.txt", "branch")

        result = repo.run_script({})

        assert result.returncode == 0, result.stderr
        assert "merge: conflict with origin/main; testing branch head" in result.stdout
        assert f"tested: branch head {branch_head}" in result.stdout
        assert repo.head_sha() == branch_head
        assert repo.head_file("shared.txt") == "branch"
        assert not (repo.root / ".git" / "MERGE_HEAD").exists()

    def test_merge_commit_is_not_pushed(self, tmp_path: Path) -> None:
        """Given the merge runs, when it completes, then origin still points at
        the branch head."""
        repo = SessionRepo(tmp_path, ISSUE_NUMBER)
        repo.commit_on("main", "main_only.txt", "main")
        branch_head = repo.commit_on(repo.branch, "branch_only.txt", "branch")

        result = repo.run_script({})

        assert result.returncode == 0, result.stderr
        assert repo.head_sha() != branch_head
        assert repo.origin_sha(repo.branch) == branch_head


class TestWorkflowsOptOutOfTheMergeForTheDiff:
    def test_run_tests_uses_the_manifest_default(self) -> None:
        """Given the workflow, when run_tests runs the script, then it leaves the
        merge to the manifest default and keeps the pytest selection."""
        command = _workflow_command("run_tests")

        assert "NEBUKO_MERGE_MAIN" not in command
        assert "pytest tests src/nebuko/engine/lib -q --tb=short -rf" in command

    def test_git_diff_steps_opt_out_of_the_merge(self) -> None:
        """Given the workflows, when a git_diff step runs the script, then it
        sets NEBUKO_MERGE_MAIN=0 — a merge would hide the change under review."""
        for workflow in (WORKFLOW, DEV_REVIEW_WORKFLOW):
            command = _workflow_command("git_diff", workflow)

            assert "NEBUKO_MERGE_MAIN=0 sh .nebuko/scripts/use-session-branch.sh" in command

    def test_the_repo_manifest_declares_the_default(self) -> None:
        """Given this repo's manifest, then `merge_main` is declared true."""
        manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))

        assert manifest["merge_main"] is True

