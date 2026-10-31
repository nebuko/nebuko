"""Isolate: what does the guard actually see in the failing fixture?"""
from pathlib import Path
import subprocess
import shutil
import pytest
from nebuko.engine.lib.git.idempotency import GitIdempotency

ISSUE_NUMBER = 1242
ISSUE_TITLE = "Find a better way to handle diffs in PRs"
BRANCH = "feature/issue-1242-find-a-better-way-to-handle-diffs-in-prs"

def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()

def _commit(repo, filename, content, message):
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", message)

@pytest.fixture()
def checkout(tmp_path: Path) -> Path:
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _git(seed, "config", "user.name", "t"); _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "remote", "add", "origin", remote.as_uri())
    _commit(seed, "base.txt", "base\n", "base"); _git(seed, "push", "-q", "origin", "main")
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _git(work, "config", "user.name", "t"); _git(work, "config", "user.email", "t@example.com")
    _git(work, "remote", "add", "origin", remote.as_uri())
    _git(work, "fetch", "-q", "origin", "main")
    _git(work, "checkout", "-q", "-B", "main", "origin/main")
    return work

def test_debug(checkout):
    _git(checkout, "checkout", "-q", "-b", BRANCH)
    _commit(checkout, "base.txt", "branch\n", "branch changes base.txt")
    seed = checkout.parent / "seed"
    _git(seed, "checkout", "-q", "main")
    _commit(seed, "base.txt", "main\n", "main changes base.txt")
    _git(seed, "push", "-q", "origin", "main")
    # Look at what refs exist at probe time
    print("REFS:", subprocess.run(["git","-C",str(checkout),"show-ref"],capture_output=True,text=True).stdout)
    print("BARE REFS:", subprocess.run(["git","--git-dir",str(checkout.parent/'origin.git'),"show-ref"],capture_output=True,text=True).stdout)
    guard = GitIdempotency(str(checkout), lambda args: "")
    # bypass run_git entirely: replicate the body manually
    r = subprocess.run(["git","-C",str(checkout),"merge-tree","--write-tree","--name-only","--no-messages","origin/main",BRANCH],capture_output=True,text=True)
    print("RC", r.returncode, repr(r.stdout))
