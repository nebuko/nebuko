"""A base merge must keep the base's own ``.github/workflows/*`` changes.

Regression for PRs #1256 / #1277: when a conflict forced a merge of
``origin/main`` into the branch and ``main`` had changed a workflow file, the
scaffold's pre-commit hook refused the merge commit, the agent unstaged the
workflow path as told, and the branch kept its stale copy, so the PR diff
silently reverted ``main``'s workflow change. The publisher did the same when it
concluded the merge itself (unstage, then deliver an empty patch).

Real git against a local bare remote, same harness style as
``test_conflict_only_publish.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import nebuko.engine.lib.agent_script.features.ensure_pr.ensure_pr as ep
from nebuko.engine.lib.git.branch_scaffold import WORKFLOW_PRE_COMMIT_HOOK

BRANCH = "feature/issue-1-thing"
WF = ".github/workflows/trigger.yaml"
WF_BRANCH = "uses: x/y/.github/workflows/w.yaml@004061e8\n"
WF_MAIN = "uses: x/y/.github/workflows/w.yaml@main\n"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _write(repo: Path, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _init(path: Path, remote: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    _git(path, "config", "user.name", "t")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "remote", "add", "origin", remote.as_uri())


@pytest.fixture()
def merging(tmp_path: Path, monkeypatch) -> Path:
    """A pushed branch mid-merge of a ``main`` that changed the workflow.

    ``shared.txt`` conflicts (that is what forces the merge) and is resolved;
    the workflow file merged cleanly to ``main``'s copy and sits staged.
    """
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    _init(seed, remote)
    _write(seed, "shared.txt", "v1\n")
    _write(seed, WF, WF_BRANCH)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "base")
    _git(seed, "push", "-q", "origin", "main")

    work = tmp_path / "work"
    _init(work, remote)
    _git(work, "fetch", "-q", "origin", "main")
    _git(work, "checkout", "-q", "-b", BRANCH, "origin/main")
    _write(work, "shared.txt", "branch\n")
    _git(work, "commit", "-qam", "branch work")
    _git(work, "push", "-q", "-u", "origin", BRANCH)

    _write(seed, "shared.txt", "main\n")
    _write(seed, WF, WF_MAIN)
    _git(seed, "commit", "-qam", "main moves the workflow ref")
    _git(seed, "push", "-q", "origin", "main")

    hook = work / ".git" / "hooks" / "pre-commit"
    hook.write_text(WORKFLOW_PRE_COMMIT_HOOK, encoding="utf-8")
    hook.chmod(0o755)

    _git(work, "fetch", "-q", "origin")
    merge = subprocess.run(
        ["git", "-C", str(work), "merge", "origin/main"], capture_output=True, text=True
    )
    assert merge.returncode != 0, "the fixture needs a conflicted merge"
    _write(work, "shared.txt", "resolved\n")
    _git(work, "add", "shared.txt")
    assert _git(work, "diff", "--cached", "--name-only", "--", WF) == WF

    monkeypatch.setenv("GFA_REPO_ROOT", str(work))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.delenv("TARGET_NUMBER", raising=False)
    return work


@pytest.fixture()
def gh_calls(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def _fake_gh(args, **kw):
        calls.append(args)
        if args[:2] == ["pr", "list"]:
            return ""
        if args[:1] == ["api"]:
            return "main" if "comments" not in args[1] else "https://c/1"
        return "https://github.com/o/r/pull/1"

    monkeypatch.setattr(ep, "_run_gh", _fake_gh)
    return calls


def _remote_wf(work: Path) -> str:
    _git(work, "fetch", "-q", "origin")
    return _git(work, "show", f"origin/{BRANCH}:{WF}") + "\n"


class TestHook:
    def test_allows_a_merge_commit_carrying_the_bases_workflow_change(self, merging):
        _git(merging, "commit", "-q", "--no-edit")

        assert _git(merging, "show", f"HEAD:{WF}") + "\n" == WF_MAIN
        assert _git(merging, "diff", "origin/main", "HEAD", "--", WF) == ""

    def test_still_refuses_an_agent_edit_during_the_merge(self, merging):
        _write(merging, WF, "uses: agent-edit\n")
        _git(merging, "add", WF)

        refused = subprocess.run(
            ["git", "-C", str(merging), "commit", "-q", "--no-edit"],
            capture_output=True,
            text=True,
        )

        assert refused.returncode != 0
        assert WF in refused.stderr


class TestPublisher:
    def test_concludes_the_merge_with_the_bases_workflow_change(
        self, merging, gh_calls
    ):
        result = ep.ensure_pr(issue_title="thing")

        assert not result.error, result.message
        assert result.pushed
        assert result.patch_paths == ()
        assert len(_git(merging, "rev-parse", "HEAD^@").split()) == 2
        assert _remote_wf(merging) == WF_MAIN
        assert _git(merging, "diff", "origin/main", f"origin/{BRANCH}", "--", WF) == ""

    def test_pushes_an_agent_committed_merge_without_rebuilding_it(
        self, merging, gh_calls
    ):
        _git(merging, "commit", "-q", "--no-edit")
        merge_commit = _git(merging, "rev-parse", "HEAD")

        result = ep.ensure_pr(issue_title="thing")

        assert not result.error, result.message
        assert result.pushed
        assert _git(merging, "rev-parse", f"origin/{BRANCH}") == merge_commit
        assert _remote_wf(merging) == WF_MAIN

    def test_holds_back_and_reports_when_the_token_refuses_the_workflow(
        self, merging, gh_calls, monkeypatch
    ):
        real_push = ep._push_branch
        upstream = _git(merging, "rev-parse", f"origin/{BRANCH}")
        attempts: list[str] = []

        def _push(branch, remote, *, force=False):
            attempts.append(branch)
            if len(attempts) == 1:
                raise RuntimeError(
                    "git push failed (1): ! [remote rejected] (refusing to allow a "
                    f"GitHub App to create or update workflow `{WF}` without "
                    "`workflows` permission)"
                )
            real_push(branch, remote, force=force)

        monkeypatch.setattr(ep, "_push_branch", _push)

        result = ep.ensure_pr(issue_title="thing")

        assert not result.error, result.message
        assert result.pushed and len(attempts) == 2
        assert _remote_wf(merging) == WF_BRANCH
        parents = _git(merging, "rev-parse", "HEAD^@").split()
        assert parents == [upstream, _git(merging, "rev-parse", "origin/main")]
        assert _git(merging, "show", f"origin/{BRANCH}:shared.txt") == "resolved"
        notes = [a for a in gh_calls if a[:1] == ["api"] and "comments" in a[1]]
        assert notes and "reverted" in " ".join(notes[-1])
        assert WF in " ".join(notes[-1])
