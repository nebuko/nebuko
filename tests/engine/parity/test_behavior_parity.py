"""Behavior-parity net for the typed driver (issue #590, plan section 3.7).

Each fixture is a *recorded* ``issue_comment`` / ``issues`` event payload plus the
issue comment history that surrounded it, and the decisions the legacy
``run-task`` bash job took on that input (``ROLE_NAME``, ``WORKFLOW_STEP``,
``SKIP_AGENT``, prompt selection, and whether the agent step ran at all).

Every case is checked twice over:

* against the **recorded** expectations committed next to the payload, and
* against an **independent replay** of the legacy decision path
  (``resolve_config`` + the guard blocks + ``circuit_breaker``), so a fixture
  whose recording drifts from the legacy scripts fails loudly.

Nothing here touches a workflow file: the corpus is authored from recorded run
payloads and the legacy scripts themselves.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from nebuko.adapters.base import PlatformAdapter
from nebuko.adapters.local import LocalAdapter
from nebuko.engine.models import ExitCode
from nebuko.engine.paths import repo_root
from nebuko.engine.run_loop import Engine

FIXTURES_DIR = Path(__file__).parent / "fixtures"
REPO_ROOT = repo_root()
WORKFLOWS_DIR = REPO_ROOT / "defaults" / "workflows"
PROMPT_BASE = REPO_ROOT / "defaults" / "prompts"

# The subset of the resolver's output the legacy job copies into its env and the
# downstream steps key off.
_RESOLVED_KEYS = (
    "ROLE_NAME",
    "WORKFLOW_NAME",
    "WORKFLOW_STEP",
    "WORKFLOW_POSITION_PATH",
    "SKIP_AGENT",
    "REASON",
)
# Guard *decisions* only: the human-readable REASON is the engine's own wiring;
# the legacy bash folds it into the posted warning text instead of an env var.
_GUARD_KEYS = ("SKIP_AGENT", "AGENT_CALLS", "CIRCUIT_BREAKER_TRIPPED")

# The bash reads the caller with ``sed -n 's/.*<!-- bot-agent-\([a-z]*\) -->.*/\1/p'``.
# The replay applies it to the header region only (issue #1361), matching
# ``run_loop._caller_agent``.
_LEGACY_CALLER_RE = re.compile(r"<!-- bot-agent-([a-z]*) -->")

# Deterministic agent result stand-in, exactly as the plan's parity harness
# specifies: a valid answer plus an approving turn outcome. ``status`` is
# engine-measured and must never be agent-supplied (issue #1033), so it is not
# part of the agent's payload.
_STUB_AGENT_RESULT: Mapping[str, Any] = {
    "answer": "*Agent answer*",
    "turn_outcome": {"appreciation": "approved"},
}


def _fixture_files() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("*.json"))


def _load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


FIXTURES = [_load_fixture(path) for path in _fixture_files()]
FIXTURE_IDS = [fixture["name"] for fixture in FIXTURES]


def _prompt_selection(decision: Mapping[str, Any]) -> dict:
    """The ``PROMPT_LOCATION`` / ``STEP_PROMPT_FILE`` pair, repo-relative."""
    selection: dict[str, str] = {}
    for key, name in (("PROMPT_LOCATION", "prompt"), ("STEP_PROMPT_FILE", "step_prompt")):
        location = decision.get(key) or ""
        path = Path(location) if location else None
        # Step prompts are rendered to a temp file outside the checkout.
        selection[name] = (
            str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path)
            if path
            else ""
        )
    return selection


def _seed_journal(run_dir: Path, history: Sequence[Mapping[str, Any]]) -> None:
    """Write the recorded thread into the adapter's journal."""
    entries = [
        f"<!-- journal-entry @ {comment['created_at']} -->\n{comment['body']}"
        for comment in history
    ]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "journal.md").write_text("\n\n".join(entries), encoding="utf-8")


def _engine_for(fixture: Mapping[str, Any], run_dir: Path, monkeypatch) -> Engine:
    """Build the engine over the recorded payload with a run-dir-backed adapter."""
    import nebuko.engine.lib.event_normalizer as event_normalizer
    import nebuko.engine.lib.resolve_config as resolve_config_package

    trigger = event_normalizer.normalize_trigger(fixture["payload"])
    adapter = LocalAdapter(run_dir, trigger=trigger)
    _seed_journal(run_dir, fixture["history"])

    # The legacy REST fetch is the only external dependency of resolve_config;
    # hand it the adapter's view of the recorded thread.
    monkeypatch.setattr(
        resolve_config_package,
        "get_issue_comments",
        lambda *args, **kwargs: list(adapter.load_history()),
    )
    # The oracle replays decisions recorded when the legacy pipeline injected the
    # whole thread; the bounded recency window (issue #844) is engine behavior
    # change, not part of the recorded contract, so it is pinned off here. The
    # window itself is covered by tests/engine/test_run_loop.py.
    return Engine(
        adapter=adapter,
        repo_root=REPO_ROOT,
        env=dict(fixture.get("env", {})),
        history_limit=None,
    )


def _driver_decisions(engine: Engine) -> dict:
    """Drive the engine's own phases over the recorded input."""
    trigger = engine.adapter.read_trigger()
    resolved = engine.resolve(trigger)
    history = list(engine.adapter.load_history())

    if resolved.get("SKIP_AGENT") == "true":
        # The engine short-circuits on the resolver's verdict, exactly as the job
        # does (issue #458): neither the loop guard nor the breaker runs.
        return {
            "resolved": {key: resolved.get(key) for key in _RESOLVED_KEYS},
            "guard": None,
            "prompt": _prompt_selection(resolved),
            "agent_ran": False,
        }

    guard = engine.guard(trigger, history)
    return {
        "resolved": {key: resolved.get(key) for key in _RESOLVED_KEYS},
        "guard": {key: guard.get(key) for key in _GUARD_KEYS},
        "prompt": _prompt_selection(resolved),
        "agent_ran": guard.get("SKIP_AGENT") != "true",
    }


# ── legacy oracle: an independent replay of the bash decision path ──────────


def _legacy_caller(body: str) -> str:
    # Header-only read (issue #1361), mirroring ``run_loop._caller_agent``: the
    # bash replay reads the caller from the comment header — line 2 for answer
    # comments, line 1 for continuation parts — so a marker quoted inside the
    # agent's own text is not a caller. This is a deliberate divergence from
    # the original bash, which scanned the whole body.
    for line in (body or "").splitlines()[:2]:
        matches = _LEGACY_CALLER_RE.findall(line)
        if matches:
            return matches[-1]
    return ""


def _legacy_resolve(body: str, number: int) -> dict:
    from nebuko.engine.lib.resolve_config.resolve_config import resolve_config

    return resolve_config(
        trigger_body=body,
        prompt_base=str(PROMPT_BASE),
        workflows_dir=str(WORKFLOWS_DIR),
        repo="olivier-norture/git-flow-ai",
        issue_number=number or None,
        debug=False,
    )


def _legacy_loop_verdict(resolved: Mapping[str, Any], body: str) -> dict:
    """The legacy loop-guard block (``openhands-multi-agent.yml`` 495-563).

    Kept separate from ``Engine._loop_verdict`` so the two readings of the bash
    guard can be compared directly — ``test_loop_verdict_matches_the_legacy_replay``
    sweeps the branch space and fails loudly if either side drifts.
    """
    from nebuko.engine.lib.loop_guard import check_same_agent_advance, detect_human_plus_agent_ping

    caller = _legacy_caller(body)
    task = resolved.get("TASK") or ""
    if caller and task:
        if caller == task:
            return dict(
                check_same_agent_advance(
                    body,
                    resolved.get("WORKFLOW_POSITION_PATH") or "",
                    resolved.get("WORKFLOW_STEP") or "",
                )
            )
        return {"SKIP_AGENT": "false"}
    if not caller and task:
        if detect_human_plus_agent_ping(body):
            return {"SKIP_AGENT": "true", "REASON": "Human ping + agent ping detected"}
        return {"SKIP_AGENT": "false"}
    return {"SKIP_AGENT": "false"}


def _legacy_guard(
    resolved: Mapping[str, Any],
    body: str,
    number: int,
    env: Mapping[str, str],
    history: Sequence[Any],
) -> dict:
    """The job's loop-guard block (495-563) plus its circuit-breaker block (596-632)."""
    from nebuko.engine.lib.circuit_breaker import check_circuit_breaker

    verdict = _legacy_loop_verdict(resolved, body)

    if verdict.get("SKIP_AGENT") == "true":
        return verdict

    verdict.update(
        check_circuit_breaker(
            number,
            int(env.get("CIRCUIT_BREAKER_THRESHOLD") or 25),
            repo="olivier-norture/git-flow-ai",
            dry_run=True,
            history=history,
        )
    )
    if verdict.get("CIRCUIT_BREAKER_TRIPPED") == "true":
        verdict["SKIP_AGENT"] = "true"
    return verdict


def _legacy_decisions(adapter: PlatformAdapter, fixture: Mapping[str, Any]) -> dict:
    """Replay the recorded input through the legacy scripts, decision for decision."""
    trigger = adapter.read_trigger()
    resolved = _legacy_resolve(trigger.body, trigger.number)

    if resolved.get("SKIP_AGENT") == "true":
        return {
            "resolved": {key: resolved.get(key) for key in _RESOLVED_KEYS},
            "guard": None,
            "prompt": _prompt_selection(resolved),
            "agent_ran": False,
        }

    guard = _legacy_guard(
        resolved,
        trigger.body,
        trigger.number,
        fixture.get("env", {}),
        list(adapter.load_history()),
    )
    return {
        "resolved": {key: resolved.get(key) for key in _RESOLVED_KEYS},
        "guard": {key: guard.get(key) for key in _GUARD_KEYS},
        "prompt": _prompt_selection(resolved),
        "agent_ran": guard.get("SKIP_AGENT") != "true",
    }


# ── the parity assertions ───────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", FIXTURES, ids=FIXTURE_IDS)
def test_trigger_normalization_matches_the_recorded_event(fixture, tmp_path, monkeypatch):
    engine = _engine_for(fixture, tmp_path / fixture["name"], monkeypatch)
    trigger = engine.adapter.read_trigger()

    assert trigger.type == fixture["expected"]["trigger"]["type"]
    assert trigger.author == fixture["expected"]["trigger"]["author"]
    assert trigger.number == fixture["expected"]["trigger"]["number"]


@pytest.mark.parametrize("fixture", FIXTURES, ids=FIXTURE_IDS)
def test_driver_reproduces_the_recorded_legacy_decisions(fixture, tmp_path, monkeypatch):
    engine = _engine_for(fixture, tmp_path / fixture["name"], monkeypatch)
    decisions = _driver_decisions(engine)

    assert decisions["resolved"] == fixture["expected"]["resolved"]
    assert decisions["guard"] == fixture["expected"]["guard"]
    assert decisions["prompt"] == fixture["expected"]["prompt"]
    assert decisions["agent_ran"] is fixture["expected"]["agent_ran"]


@pytest.mark.parametrize("fixture", FIXTURES, ids=FIXTURE_IDS)
def test_driver_decisions_match_the_legacy_replay(fixture, tmp_path, monkeypatch):
    """Second, independent check: the live legacy scripts, replayed."""
    engine = _engine_for(fixture, tmp_path / fixture["name"], monkeypatch)

    assert _driver_decisions(engine) == _legacy_decisions(engine.adapter, fixture)


@pytest.mark.parametrize("fixture", FIXTURES, ids=FIXTURE_IDS)
def test_run_end_to_end_reproduces_the_legacy_agent_gate(fixture, tmp_path, monkeypatch):
    engine = _engine_for(fixture, tmp_path / fixture["name"], monkeypatch)
    prompts: list[str] = []

    def _invoke_agent(self, prompt: str) -> Mapping[str, Any]:
        prompts.append(prompt)
        return dict(_STUB_AGENT_RESULT)

    monkeypatch.setattr(Engine, "invoke_agent", _invoke_agent)
    outcome = engine.run()

    assert ("agent" in outcome.phases) is fixture["expected"]["agent_ran"]
    if fixture["expected"]["agent_ran"]:
        assert len(prompts) == 1
        assert outcome.posted_body
        assert outcome.exit_code is ExitCode.ADVANCE
    else:
        assert prompts == []
        assert outcome.posted_body is None
        assert outcome.exit_code is ExitCode.TERMINAL


# ── loop-guard drift check (issue #590 round-4, finding 3) ──────────────────
#
# ``Engine._loop_verdict`` and ``_legacy_loop_verdict`` are two readings of the
# same bash block (495-563). The recorded fixtures pin a handful of real inputs;
# this sweeps the branch space so an edit to either reading fails loudly instead
# of hiding behind a fixture that happens not to reach the branch.

_LOOP_VERDICT_BODIES = (
    "",
    "please fix this",
    "<!-- bot-agent-dev -->\n@@dev continue",
    "<!-- bot-agent-dev -->\n@@architect continue",
    "@@dev continue",
    "@@architect continue",
    "<!-- bot-agent-dev -->\n@@architect continue\n@@dev you too",
    "<!-- bot-agent-dev -->\n<!-- bot-agent-architect -->\n@@dev go",
)


@pytest.mark.parametrize("body", _LOOP_VERDICT_BODIES)
@pytest.mark.parametrize("task", ["dev", "architect", "po", ""])
def test_loop_verdict_matches_the_legacy_replay(body, task):
    """Drift check: ``Engine._loop_verdict`` ≡ the legacy replay, branch for branch."""
    resolved = {
        "TASK": task,
        "WORKFLOW_POSITION_PATH": "spec-driven-impl/implement",
        "WORKFLOW_STEP": "implement",
    }

    engine = Engine._loop_verdict(
        body, task, resolved["WORKFLOW_POSITION_PATH"], resolved["WORKFLOW_STEP"]
    )

    assert engine == _legacy_loop_verdict(resolved, body)


# ── quoted-marker loop guard (issue #1361) ──────────────────────────────────
#
# The caller marker is read from the comment header only (first 2 lines), not
# from the agent's own text: a dev report quoting ``<!-- bot-agent-unknown -->``
# in a test failure must not read as a cross-agent call and re-trigger dev.


def test_a_quoted_marker_in_the_body_is_not_a_caller():
    """A dev answer quoting an unknown-role marker still skips as same-agent."""
    body = (
        "<!-- bot-workflow-terminal -->\n"
        "<!-- bot-agent-dev -->\n"
        "*Developer answer*\n\n"
        "E   AssertionError: assert '<!-- workflow-continue -->' in '<!-- bot-agent-unknown -->'"
    )

    verdict = Engine._loop_verdict(body, "dev", "dev-review-loop/implement", "implement")

    assert str(verdict["SKIP_AGENT"]).lower() == "true"


def test_a_cross_agent_header_still_reaches_the_target_despite_a_quoted_marker():
    """An architect-headed comment quoting a dev marker is a cross-agent call."""
    body = (
        "<!-- bot-workflow-terminal -->\n"
        "<!-- bot-agent-architect -->\n"
        "*Architect answer*\n\n"
        "dev reported: <!-- bot-agent-dev -->"
    )

    verdict = Engine._loop_verdict(body, "context", "root-cause/investigate", "investigate")

    assert str(verdict["SKIP_AGENT"]).lower() == "false"


def test_a_continuation_part_marker_on_line_1_is_read():
    """Continuation parts carry the marker at the top (``format_bot_marker``)."""
    body = "<!-- bot-agent-dev -->\n\n*(Part 2/2)*\nrest of the answer"

    verdict = Engine._loop_verdict(body, "dev", "dev-review-loop/implement", "implement")

    assert str(verdict["SKIP_AGENT"]).lower() == "true"
