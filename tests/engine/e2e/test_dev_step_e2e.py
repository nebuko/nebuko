"""End-to-end: one full dev step over real git with a scripted LLM (issue #1207).

The test drives the whole ten-phase :meth:`Engine.run` for a dev step and locks
the three things a real run depends on: the engine creates the feature branch,
merges the base into it and pushes it (in that order, with real git effects on a
local bare remote); the run completes; and a malformed ``finalize`` (missing
``appreciation``) is rejected and retried until the second, correct call lands.

Only the LLM (``agent_runner._load_pipeline``) and PR creation
(``ensure_pr.ensure_pr``) are replaced — the git machinery, the finalize
executor and the run loop all run for real. See ``docs/testing-e2e.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nebuko.adapters.local import LocalAdapter
from nebuko.engine.lib.git.branch_scaffold import feature_branch_name
from nebuko.engine.models import ExitCode, Trigger
from nebuko.engine.run_loop import PHASES, Engine

from workflow_state import Appreciation

ISSUE_NUMBER = 1207
ISSUE_TITLE = "End-to-end test for a running dev agent"
BRANCH = feature_branch_name(ISSUE_NUMBER, ISSUE_TITLE)
REPORT = "## Dev step\n\nImplemented the e2e harness (issue #1207).\n"


class GitCommandRecorder:
    """Records every git argv the engine's subprocess seams invoke, then runs real git."""

    commands: list[str]

    def __init__(self) -> None:
        self.commands = []

    # @stub-protected: signature defined by architect — do not modify
    def install(self, monkeypatch) -> None:
        """Wrap subprocess.run in git_session, branch_scaffold and ensure_pr:
        record " ".join(args) when args[0] == "git", then delegate to the real run."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def matching(self, fragment: str) -> list[str]:
        """Recorded command lines containing `fragment`, in order."""
        raise NotImplementedError


class ScriptedDevConversation:
    """Replays one scripted turn per run()/arun() call through the real finalize machinery."""

    turns: list[list[Any]]
    prompts: list[str]
    state: SimpleNamespace
    rejections: list[str]

    # @stub-protected: signature defined by architect — do not modify
    def __init__(self, turns: list[list[Any]]) -> None:
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def send_message(self, prompt: str) -> None:
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def run(self) -> None:
        """Pop the next turn, extend state.events, run the real FinalizeExecutor
        over its finalize actions (errors land in self.rejections as observations),
        and set execution_status to finished when no turns remain."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    async def arun(self) -> None:
        """Async twin of run() (the host/_run_supervised path may use either)."""
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def interrupt(self) -> None:
        raise NotImplementedError

    # @stub-protected: signature defined by architect — do not modify
    def rejection_texts(self) -> list[str]:
        """The finalize rejections recorded so far."""
        raise NotImplementedError


def _finalize_turn(*, report: str, turn_outcome: dict[str, Any] | None) -> list[Any]:
    """One turn's events: a finalize ActionEvent whose action carries
    _structured_output={"turn_outcome": turn_outcome} and the report text.
    turn_outcome=None or a dict missing required keys (e.g. {"note": "done"})
    produces the malformed first-turn finalize."""
    raise NotImplementedError


def _install_scripted_pipeline(
    monkeypatch, conversation: ScriptedDevConversation
) -> None:
    """Fake agent_runner._load_pipeline: build_output writes real artefact files
    (pattern: tests/engine/test_agent_runner_contract.py::_install_fake_pipeline),
    resolve_llm_config returns (object(), prompt, object()),
    run_agent returns `conversation`."""
    raise NotImplementedError


def _dev_checkout(tmp_path) -> Path:
    """Bare remote at tmp_path/'origin.git' with seeded main, plus a work
    checkout at tmp_path/'work' on main tracking origin (pattern:
    tests/engine/git/test_branch_scaffold.py::checkout).

    The remote also carries a previous round's ``BRANCH`` and the base moves on
    after it was cut, so the scaffold takes its existing-branch path (checkout
    the branch, merge the base) — what a second dev turn on the issue really
    does.
    """
    raise NotImplementedError


def _dev_engine(tmp_path, root, trigger) -> Engine:
    """Engine(adapter=LocalAdapter(run_dir, trigger), repo_root=root, env={
    CIRCUIT_BREAKER_THRESHOLD, GITHUB_REPOSITORY}); resolver patched via the
    established _patch_resolver/_resolved_with_hooks pattern with TASK=dev,
    ROLE_NAME=dev, WORKFLOW_STEP=implement, SKIP_AGENT=false, PROMPT_LOCATION=''.
    The three git engine seams are NOT stubbed — that is the point."""
    raise NotImplementedError


def _resolved() -> dict[str, str]:
    """The dev-step resolver contract the run routes and prompts on."""
    return {
        "SKIP_AGENT": "false",
        "TASK": "dev",
        "ROLE_NAME": "dev",
        "WORKFLOW_NAME": "wf",
        "WORKFLOW_STEP": "implement",
        "PROMPT_LOCATION": "",
    }


def _pin_resolver(monkeypatch) -> None:
    """Pin the resolver facade's output (the ``_patch_resolver`` pattern)."""
    import resolve_config  # noqa: F401 - ensures the submodule is imported

    monkeypatch.setattr(
        sys.modules["resolve_config.resolve_config"],
        "resolve_config",
        lambda **_: _resolved(),
    )


def _dev_trigger() -> Trigger:
    return Trigger(
        author="olivier-norture",
        body="@@dev implement the e2e harness",
        type="issue",
        number=ISSUE_NUMBER,
        payload={
            "issue": {"title": ISSUE_TITLE},
            "repository": {"full_name": "nebuko/nebuko"},
        },
    )


def _git(repo: Path, *args: str) -> str:
    """Run one git command in ``repo`` and return its stripped stdout."""
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_dev_step_e2e_branch_merge_push_and_finalize_retry(tmp_path, monkeypatch) -> None:
    """The single red-then-green e2e test."""
    # given: a real checkout on a bare remote and a wrong-then-right scripted LLM
    root = _dev_checkout(tmp_path)
    recorder = GitCommandRecorder()
    recorder.install(monkeypatch)
    conversation = ScriptedDevConversation(
        [
            _finalize_turn(report="", turn_outcome={"note": "done"}),
            _finalize_turn(
                report=REPORT,
                turn_outcome={"appreciation": "approved", "note": "e2e dev step done"},
            ),
        ]
    )
    _install_scripted_pipeline(monkeypatch, conversation)

    def _fake_ensure_pr(
        dry_run: bool = False, *, issue_title: str = "", interrupted: bool = False
    ):
        from agent_script.features.ensure_pr.ensure_pr import EnsurePrResult

        return EnsurePrResult(
            pr_url="https://github.com/nebuko/nebuko/pull/1207",
            message="PR opened: https://github.com/nebuko/nebuko/pull/1207",
        )

    monkeypatch.setattr(
        "agent_script.features.ensure_pr.ensure_pr.ensure_pr", _fake_ensure_pr
    )
    _pin_resolver(monkeypatch)
    engine = _dev_engine(tmp_path, root, _dev_trigger())

    # when: the whole engine run executes
    outcome = engine.run()

    # then: the full run_loop completed and routed on the second, correct finalize
    assert outcome.exit_code is ExitCode.ADVANCE
    assert outcome.turn_outcome is not None
    assert outcome.turn_outcome.appreciation is Appreciation.APPROVED
    assert tuple(outcome.phases) == PHASES

    # then: branch created, base merged, branch pushed — in that order
    created = recorder.matching(f"checkout -b {BRANCH}")
    merged = recorder.matching("merge --no-edit origin/main")
    pushed = recorder.matching(f"push origin HEAD:refs/heads/{BRANCH}")
    assert created, recorder.commands
    assert merged, recorder.commands
    assert pushed, recorder.commands
    assert recorder.commands.index(created[0]) < recorder.commands.index(merged[0])
    assert recorder.commands.index(merged[0]) < recorder.commands.index(pushed[0])

    # then: the effects are real — the remote branch is the session HEAD, merged
    remote_sha = _git(tmp_path / "origin.git", "rev-parse", f"refs/heads/{BRANCH}")
    assert remote_sha == _git(root, "rev-parse", "HEAD")
    _git(root, "merge-base", "--is-ancestor", "origin/main", "HEAD")

    # then: the first finalize was rejected, the second was accepted
    rejections = conversation.rejection_texts()
    assert len(rejections) == 1
    assert "finalize rejected" in rejections[0]

    # then: the run directory holds the run's artefacts
    run_dir = tmp_path / "runs" / "e2e"
    assert (run_dir / "journal.md").is_file()
    assert (run_dir / "state.json").is_file()
    assert (run_dir / "latest_answer.md").is_file()
