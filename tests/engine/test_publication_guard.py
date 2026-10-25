"""Publication guard: approved write steps must not end unpublished.

An approved dev step can finish with work that was never committed, pushed or
opened as a PR (run 39622244543). The guard in ``run_agent`` checks publication,
recalls the agent a bounded number of times and finally checkpoints with
``ensure_pr``; these tests pin each result and the scope gates, plus the
surfacing of the result in the workflow state.
"""

from __future__ import annotations

import pytest

from agent_script.features.ensure_pr.ensure_pr import (
    ENGINE_OWNED_PATHS,
    PublicationState,
    check_publication,
)
from agent_script.features.run_agent.publication_guard import (
    MAX_PUBLICATION_RECALLS,
    is_benign_skip,
    perform_publication_recalls,
    publication_recall_prompt,
)

_CLEAN = PublicationState(published=True)
_DIRTY = PublicationState(published=False, reasons=("dirty_tree",))


# ── Bounded recall loop ──────────────────────────────────────────


class _Conversation:
    def __init__(self, fail_send: bool = False) -> None:
        self.sent: list[str] = []
        self._fail = fail_send

    def send_message(self, prompt: str) -> None:
        if self._fail:
            raise RuntimeError("conversation closed")
        self.sent.append(prompt)


def test_prompt_names_the_missing_steps_and_the_workflow():
    prompt = publication_recall_prompt(
        ["dirty_tree", "unpushed_commits", "missing_pr"]
    )
    for token in ("uncommitted", "not pushed", "no pull request", "engine opens the pull request"):
        assert token in prompt


def test_prompt_keeps_the_agent_in_control_of_the_workflow():
    """The agent keeps control of branch, commit message and PR body."""
    for token in ("Commit", "push", "pull request", "finalize"):
        assert token in publication_recall_prompt(["dirty_tree"])


def test_prompt_passes_unknown_reasons_through():
    assert "mystery" in publication_recall_prompt(["mystery"])


def test_published_work_triggers_no_recall():
    conv = _Conversation()
    assert perform_publication_recalls(conv, lambda: (), lambda n: True) == 0
    assert conv.sent == []


def test_recall_stops_once_the_work_is_published():
    conv = _Conversation()
    state = {"reasons": ("dirty_tree",)}

    def run_pass(n: int) -> bool:
        state["reasons"] = ()
        return True

    assert perform_publication_recalls(conv, lambda: state["reasons"], run_pass) == 1
    assert len(conv.sent) == 1


def test_recalls_are_capped():
    conv = _Conversation()
    count = perform_publication_recalls(conv, lambda: ("missing_pr",), lambda n: True)
    assert count == MAX_PUBLICATION_RECALLS == len(conv.sent)


def test_dead_conversation_breaks_the_loop():
    conv = _Conversation(fail_send=True)
    assert perform_publication_recalls(conv, lambda: ("dirty_tree",), lambda n: True) == 1
    assert conv.sent == []


def test_budget_exhausted_pass_stops_early():
    conv = _Conversation()
    count = perform_publication_recalls(conv, lambda: ("missing_pr",), lambda n: False)
    assert count == 1
    assert len(conv.sent) == 1


# ── Publication check ───────────────────────────────────────────


def _git(status="", upstream="0", base="0", branch="feature/x"):
    def run(args, **kwargs):
        joined = " ".join(args)
        if joined == "remote":
            return "origin"
        if joined.startswith("rev-parse --abbrev-ref HEAD"):
            return branch
        if joined.startswith("rev-parse --verify"):
            return "sha1"
        if joined.startswith("status --porcelain"):
            return status
        if joined.startswith("rev-list --count @{u}..HEAD"):
            if upstream is None:
                raise RuntimeError("no upstream configured")
            return upstream
        return base

    return run


def _check(monkeypatch, git, pr=None):
    from agent_script.features.ensure_pr import ensure_pr as module

    monkeypatch.setattr(module, "_run_git", git)
    monkeypatch.setattr(module, "_find_pr_for_branch", lambda *a, **k: pr)
    monkeypatch.setattr(module, "_default_base", lambda *a, **k: "main")
    return check_publication()


def test_clean_published_branch(monkeypatch):
    state = _check(monkeypatch, _git(), pr="https://github.com/acme/repo/pull/1")
    assert state.published and state.reasons == () and not state.skip_reason


def test_engine_owned_handoff_file_is_not_dirty(monkeypatch):
    assert ".nebuko/handoff.md" in ENGINE_OWNED_PATHS
    state = _check(
        monkeypatch,
        _git(status="?? .nebuko/handoff.md"),
        pr="https://github.com/acme/repo/pull/1",
    )
    assert state.published


@pytest.mark.parametrize(
    "git_kwargs,pr,expected",
    [
        (
            {"status": " M logo/nebuko-logo.py"},
            "https://github.com/acme/repo/pull/1",
            ("dirty_tree",),
        ),
        ({}, "https://github.com/acme/repo/pull/1", ()),
    ],
)
def test_dirty_tree_is_the_only_reason_when_unpushed(monkeypatch, git_kwargs, pr, expected):
    kwargs = {"upstream": "2", **git_kwargs}
    state = _check(monkeypatch, _git(**kwargs), pr=pr)
    assert "unpushed_commits" in state.reasons
    if expected:
        assert "dirty_tree" in state.reasons


def test_never_pushed_branch_with_work_and_no_pr(monkeypatch):
    state = _check(monkeypatch, _git(upstream=None, base="3"), pr=None)
    assert state.reasons == ("unpushed_commits", "missing_pr")


def test_empty_branch_is_clean_not_a_wip_pr(monkeypatch):
    state = _check(monkeypatch, _git(upstream=None, base="0"), pr=None)
    assert state.published


@pytest.mark.parametrize(
    "git_kwargs,skip",
    [({}, "no git remote"), ({"branch": "HEAD"}, "detached HEAD")],
)
def test_guard_skips_without_a_publishable_branch(monkeypatch, git_kwargs, skip):
    git = _git(**git_kwargs)

    def no_remote(args, **kwargs):
        if " ".join(args) == "remote":
            return ""
        return git(args)

    state = _check(monkeypatch, no_remote if not git_kwargs else git)
    assert state.published
    assert state.skip_reason == skip


def test_check_never_raises(monkeypatch):
    def boom(args, **kwargs):
        raise RuntimeError("git exploded")

    state = _check(monkeypatch, boom)
    assert state.published and state.skip_reason


# ── Skip taxonomy (issue #1089) ──────────────────────────────────


def test_no_remote_and_detached_head_are_benign_skips():
    """Only an unpublishable checkout stands the guard down."""
    assert is_benign_skip("no git remote")
    assert is_benign_skip("detached HEAD")


@pytest.mark.parametrize("reason", ["pr lookup failed", "error:boom", ""])
def test_an_inspection_failure_is_not_a_benign_skip(reason):
    """Reading a failed inspection as benign is what let a run end green."""
    assert not is_benign_skip(reason)
