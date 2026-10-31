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
        import importlib
        import subprocess

        from nebuko.engine.lib.git import branch_scaffold, git_session

        recorder = self
        real_run = subprocess.run

        class _RecordingSubprocess:
            """Delegates to the real ``subprocess`` module, recording git argv."""

            def __getattr__(self, name: str) -> Any:
                return getattr(subprocess, name)

            def run(self, args: Any, *rest: Any, **kwargs: Any) -> Any:
                argv = [args] if isinstance(args, (str, bytes)) else list(args)
                if argv and str(argv[0]) == "git":
                    recorder.commands.append(" ".join(str(part) for part in argv))
                return real_run(args, *rest, **kwargs)

        proxy = _RecordingSubprocess()
        for module in (
            git_session,
            branch_scaffold,
            importlib.import_module("agent_script.features.ensure_pr.ensure_pr"),
        ):
            monkeypatch.setattr(module, "subprocess", proxy)

    # @stub-protected: signature defined by architect — do not modify
    def matching(self, fragment: str) -> list[str]:
        """Recorded command lines containing `fragment`, in order."""
        return [command for command in self.commands if fragment in command]


class ScriptedDevConversation:
    """Replays one scripted turn per run()/arun() call through the real finalize machinery."""

    turns: list[list[Any]]
    prompts: list[str]
    state: SimpleNamespace
    rejections: list[str]

    # @stub-protected: signature defined by architect — do not modify
    def __init__(self, turns: list[list[Any]]) -> None:
        self.id = "conv-e2e-dev-step"
        self.turns = list(turns)
        self.prompts = []
        self.state = SimpleNamespace(events=[])
        self.rejections = []
        self.execution_status = SimpleNamespace(value="running")

    # @stub-protected: signature defined by architect — do not modify
    def send_message(self, prompt: str) -> None:
        self.prompts.append(prompt)

    # @stub-protected: signature defined by architect — do not modify
    def run(self) -> None:
        """Pop the next turn, extend state.events, run the real FinalizeExecutor
        over its finalize actions (errors land in self.rejections as observations),
        and set execution_status to finished when no turns remain."""
        if self.turns:
            events = self.turns.pop(0)
            self.state.events.extend(events)
            self._execute_finalize(events)
        if not self.turns:
            self.execution_status = SimpleNamespace(value="finished")

    def _execute_finalize(self, events: list[Any]) -> None:
        """Run the real ``finalize`` executor over the turn's terminal calls."""
        from nebuko.engine.lib.agent_script.features.finalize_tool.finalize_tool import (
            FinalizeExecutor,
        )

        executor = FinalizeExecutor(conversation_id=self.id)
        for event in events:
            if getattr(event, "tool_name", None) != "finalize":
                continue
            action = getattr(event, "action", None)
            if action is None:
                continue
            observation = executor(action, self)
            if getattr(observation, "is_error", False):
                self.rejections.append(observation.text)

    # @stub-protected: signature defined by architect — do not modify
    async def arun(self) -> None:
        """Async twin of run() (the host/_run_supervised path may use either)."""
        self.run()

    # @stub-protected: signature defined by architect — do not modify
    def interrupt(self) -> None:
        # Scripted turns are instantaneous: there is never a turn to interrupt.
        return None

    # @stub-protected: signature defined by architect — do not modify
    def rejection_texts(self) -> list[str]:
        """The finalize rejections recorded so far."""
        return list(self.rejections)


def _finalize_turn(*, report: str, turn_outcome: dict[str, Any] | None) -> list[Any]:
    """One turn's events: a finalize ActionEvent whose action carries
    _structured_output={"turn_outcome": turn_outcome} and the report text.
    turn_outcome=None or a dict missing required keys (e.g. {"note": "done"})
    produces the malformed first-turn finalize."""
    from agent_script.features.finalize_tool.finalize_tool import FinalizeAction
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm.message import MessageToolCall, TextContent

    action = FinalizeAction(report=report)
    action._structured_output = {"turn_outcome": turn_outcome}
    return [
        ActionEvent(
            source="agent",
            tool_name="finalize",
            thought=[TextContent(text=report)],
            action=action,
            tool_call_id="tc-finalize",
            tool_call=MessageToolCall(
                id="tc-finalize",
                name="finalize",
                arguments="{}",
                origin="completion",
            ),
            llm_response_id="r-finalize",
        )
    ]


def _install_scripted_pipeline(
    monkeypatch, conversation: ScriptedDevConversation
) -> None:
    """Fake agent_runner._load_pipeline: build_output writes real artefact files
    (pattern: tests/engine/test_agent_runner_contract.py::_install_fake_pipeline),
    resolve_llm_config returns (object(), prompt, object()),
    run_agent returns `conversation`."""
    import json

    from nebuko.engine import agent_runner

    def _build_output(conversation, metrics, args, prev_state=None):
        Path("latest_answer.md").write_text(REPORT, encoding="utf-8")
        Path("final_message.txt").write_text("finish note", encoding="utf-8")
        Path("workflow_state.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "workflow": "wf",
                    "step": "implement",
                    "agent": "dev",
                }
            ),
            encoding="utf-8",
        )

    def _run_agent(
        llm_config, prompt, args, agent_cfg=None, workspace=None, context_bodies=None
    ):
        return conversation

    pipeline = SimpleNamespace(
        build_output=_build_output,
        collect_and_save_metrics=lambda conversation, agent_cfg: object(),
        load_prev_state_or_recover=lambda: None,
        resolve_llm_config=lambda args, prompt: (object(), prompt, object()),
        run_agent=_run_agent,
    )
    monkeypatch.setattr(agent_runner, "_load_pipeline", lambda: pipeline)


def _dev_checkout(tmp_path) -> Path:
    """Bare remote at tmp_path/'origin.git' with seeded main, plus a work
    checkout at tmp_path/'work' on main tracking origin (pattern:
    tests/engine/git/test_branch_scaffold.py::checkout).

    The remote also carries a previous round's ``BRANCH`` and the base moves on
    after it was cut, so the scaffold takes its existing-branch path (checkout
    the branch, merge the base) — what a second dev turn on the issue really
    does.
    """
    import subprocess

    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _git(seed, "config", "user.name", "t")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "remote", "add", "origin", remote.as_uri())

    # The in-process agent seam only checks that the legacy entry point exists
    # (it imports the assembly, never executes this file).
    entry_point = seed / "src/nebuko/engine/lib/agent_script/agent_script.py"
    entry_point.parent.mkdir(parents=True, exist_ok=True)
    entry_point.write_text("# imported, never executed\n", encoding="utf-8")
    (seed / "base.txt").write_text("base\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "push", "-q", "origin", "main")

    # A previous round's branch, cut before the base moves on.
    _git(seed, "checkout", "-q", "-b", BRANCH)
    (seed / "round.txt").write_text("previous round\n", encoding="utf-8")
    _git(seed, "add", "round.txt")
    _git(seed, "commit", "-q", "-m", "previous round")
    _git(seed, "push", "-q", "origin", BRANCH)
    _git(seed, "checkout", "-q", "main")
    (seed / "base.txt").write_text("base\nmoved on\n", encoding="utf-8")
    _git(seed, "commit", "-q", "-am", "base moves on")
    _git(seed, "push", "-q", "origin", "main")

    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "remote", "add", "origin", remote.as_uri())
    _git(work, "fetch", "-q", "origin", "main")
    _git(work, "checkout", "-q", "-B", "main", "origin/main")
    return work


def _dev_engine(tmp_path, root, trigger) -> Engine:
    """Engine(adapter=LocalAdapter(run_dir, trigger), repo_root=root, env={
    CIRCUIT_BREAKER_THRESHOLD, GITHUB_REPOSITORY}); resolver patched via the
    established _patch_resolver/_resolved_with_hooks pattern with TASK=dev,
    ROLE_NAME=dev, WORKFLOW_STEP=implement, SKIP_AGENT=false, PROMPT_LOCATION=''.
    The three git engine seams are NOT stubbed — that is the point."""
    adapter = LocalAdapter(tmp_path / "runs" / "e2e", trigger=trigger)
    env = {
        "CIRCUIT_BREAKER_THRESHOLD": "3",
        "GITHUB_REPOSITORY": "nebuko/nebuko",
        # The engine's git seams act on the caller workspace; without this the
        # anchor would fall back to the pytest cwd (the real repository).
        "GITHUB_WORKSPACE": str(root),
    }
    return Engine(adapter=adapter, repo_root=root, env=env)


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
