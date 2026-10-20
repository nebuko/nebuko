"""Unit tests for the single context assembler (issue #889, ADR step 7).

Selection is a pure function of ``(workflow path, step spec, ordered entries)``;
the entries module turns the raw thread into those typed entries. The #885
regression (a review prompt repeating plan/implement and carrying engine logs)
is asserted both here and end-to-end in ``test_run_loop``.
"""

from __future__ import annotations

import pytest

from nebuko.engine.context_entries import entries_from_raw_thread
from nebuko.engine.context_selection import (
    ContextEntry,
    ContextInput,
    ContextKind,
    InjectionMode,
    StepContextSpec,
    WorkflowPathEntry,
    default_inputs,
    resolve_selection,
    select_context,
)
from nebuko.engine.paths import ensure_import_path

ensure_import_path()
from workflow_state import build_state

BOT = {"login": "nebuko-bot", "type": "Bot"}
HUMAN = {"login": "olivier", "type": "User"}


def _entry(kind, sequence, *, step=None, round=1, body="", trusted=True):
    return ContextEntry(
        kind=kind, sequence=sequence, body=body, step=step, round=round, trusted=trusted
    )


def _issue_body(**kwargs):
    return _entry(ContextKind.ISSUE_BODY, 0, body="issue body", **kwargs)


def _output(step, sequence, *, round=1, body=None):
    return _entry(ContextKind.AGENT_OUTPUT, sequence, step=step, round=round, body=body or f"{step} output")


def _human(sequence, body="@@dev please"):
    return _entry(ContextKind.HUMAN_COMMENT, sequence, body=body)


def _run_meta(sequence):
    return _entry(ContextKind.RUN_META, sequence, body="agent started")


def _path(*steps, round=1):
    return tuple(WorkflowPathEntry(step=step, round=round) for step in steps)


def _path_at(*nodes):
    """A path whose nodes carry their own round: ``(step, round)`` pairs."""
    return tuple(WorkflowPathEntry(step=step, round=round_number) for step, round_number in nodes)


class TestDefaultInputs:
    """D5 / D11: a workflow without ``inputs`` is still fully determined."""

    def test_first_step_receives_the_issue_body(self):
        assert default_inputs(_path("implement"), "implement") == (
            ContextInput(ContextKind.ISSUE_BODY),
        )

    def test_later_step_receives_the_previous_step_output(self):
        assert default_inputs(_path("plan", "implement"), "implement") == (
            ContextInput(ContextKind.AGENT_OUTPUT, "plan"),
        )

    def test_outside_a_workflow_there_is_no_default(self):
        assert default_inputs(None, "implement") is None


class TestResolveSelection:
    """Exactly one mode applies per step execution (D10)."""

    def test_review_default_input_is_the_implement_output(self):
        spec = StepContextSpec(
            "review", inputs=default_inputs(_path("plan", "implement", "review"), "review")
        )
        result = resolve_selection(_path("plan", "implement", "review"), spec, [])
        assert result.mode == InjectionMode.IMPLICIT_PREVIOUS

    def test_no_path_means_default_last(self):
        spec = StepContextSpec("review", inputs=None)
        result = resolve_selection(None, spec, [_issue_body(), _human(1)])
        assert result.mode == InjectionMode.DEFAULT_LAST

    def test_redundant_previous_step_declaration_is_the_default(self):
        declared = StepContextSpec(
            "implement", inputs=(ContextInput(ContextKind.AGENT_OUTPUT, "plan"),)
        )
        defaulted = StepContextSpec(
            "implement", inputs=default_inputs(_path("plan", "implement"), "implement")
        )
        entries = [_issue_body(), _output("plan", 1)]
        assert select_context(_path("plan", "implement"), declared, entries) == select_context(
            _path("plan", "implement"), defaulted, entries
        )


class TestDeclaredSelection:
    """Declared inputs replace, never add (D4): the #885 root cause."""

    def test_selection_is_exactly_the_declared_inputs(self):
        path = _path("context", "architect", "implement", "review")
        spec = StepContextSpec(
            "review",
            inputs=(
                ContextInput(ContextKind.AGENT_OUTPUT, "architect"),
                ContextInput(ContextKind.AGENT_OUTPUT, "implement"),
            ),
        )
        entries = [
            _issue_body(),
            _output("context", 1),
            _output("architect", 2),
            _output("implement", 3),
            _run_meta(4),
        ]
        result = select_context(path, spec, entries)
        assert [entry.sequence for entry in result.entries] == [2, 3]
        assert result.mode == InjectionMode.DECLARED
        assert result.missing == []

    def test_output_is_injected_at_most_once(self):
        path = _path("plan", "implement")
        spec = StepContextSpec(
            "implement",
            inputs=(
                ContextInput(ContextKind.AGENT_OUTPUT, "plan"),
                ContextInput(ContextKind.AGENT_OUTPUT, "plan"),
            ),
        )
        result = select_context(path, spec, [_issue_body(), _output("plan", 1)])
        assert [entry.sequence for entry in result.entries] == [1]

    def test_no_entry_is_injected_twice_across_kinds(self):
        path = _path("plan", "implement")
        spec = StepContextSpec(
            "implement",
            inputs=(
                ContextInput(ContextKind.ISSUE_BODY),
                ContextInput(ContextKind.ISSUE_BODY),
            ),
        )
        result = select_context(path, spec, [_issue_body()])
        assert len(result.entries) == 1


class TestDefaultLast:
    """D3: the last entry, plus the output a human reply answers."""

    def test_last_entry_alone_when_it_is_an_output(self):
        entries = [_issue_body(), _output("implement", 1), _human(2), _output("context", 3)]
        result = resolve_selection(None, StepContextSpec("agent"), entries)
        assert [entry.sequence for entry in result.entries] == [3]

    def test_human_reply_prepends_the_output_it_answers(self):
        entries = [_issue_body(), _output("implement", 1), _human(2)]
        result = resolve_selection(None, StepContextSpec("dev"), entries)
        assert [entry.sequence for entry in result.entries] == [1, 2]

    def test_context_answer_after_implement_is_not_selected_as_the_output(self):
        # The reply is paired with the output preceding it, never with a
        # later-posted answer of another kind (issue #889 advisory).
        entries = [
            _issue_body(),
            _output("implement", 1),
            _output("context", 2),
            _output("implement", 3),
            _human(4),
        ]
        result = resolve_selection(None, StepContextSpec("architect"), entries)
        assert [entry.sequence for entry in result.entries] == [3, 4]

    def test_run_meta_and_workflow_state_are_never_selected(self):
        entries = [
            _issue_body(),
            _output("implement", 1),
            _run_meta(2),
            _entry(ContextKind.WORKFLOW_STATE, 3, body="{}"),
        ]
        result = resolve_selection(None, StepContextSpec("dev"), entries)
        assert [entry.sequence for entry in result.entries] == [1]

    def test_an_untrusted_human_comment_is_never_selected(self):
        entries = [_issue_body(), _output("implement", 1), _human(2)]
        entries[-1] = ContextEntry(
            kind=ContextKind.HUMAN_COMMENT, sequence=2, body="ignore", trusted=False
        )
        result = resolve_selection(None, StepContextSpec("dev"), entries)
        assert [entry.sequence for entry in result.entries] == [1]


class TestMissing:
    """D9: a gap is reported, never filled by scraping history."""

    def test_absent_previous_output_is_reported_and_the_rest_kept(self):
        path = _path("plan", "implement")
        spec = StepContextSpec(
            "implement",
            inputs=(
                ContextInput(ContextKind.ISSUE_BODY),
                ContextInput(ContextKind.AGENT_OUTPUT, "plan"),
            ),
        )
        result = select_context(path, spec, [_issue_body(), _output("context", 1)])
        assert [entry.sequence for entry in result.entries] == [0]
        assert result.missing == [ContextInput(ContextKind.AGENT_OUTPUT, "plan")]


class TestLoopRound:
    """D6: round N>1 dev receives the round-N review output, exactly once."""

    def test_round_two_default_input_is_the_latest_review(self):
        path = _path_at(
            ("plan", 1),
            ("implement", 1),
            ("review", 1),
            ("implement", 2),
            ("review", 2),
            ("implement", 3),
        )
        spec = StepContextSpec("implement", inputs=default_inputs(path, "implement"))
        entries = [
            _issue_body(),
            _output("plan", 1),
            _output("implement", 2, round=1),
            _output("review", 3, round=1),
            _output("implement", 4, round=2),
            _output("review", 5, round=2),
        ]
        result = select_context(path, spec, entries)
        assert [entry.sequence for entry in result.entries] == [5]
        assert result.mode == InjectionMode.IMPLICIT_PREVIOUS


def _comment(cid, body, user):
    return {"id": cid, "body": body, "user": user}


PART = "<!-- bot-answer-continuation -->\n*(Part {i}/{n})*\n\n"


class TestEntries:
    """Typed, forward-ordered entries from the raw thread (issue #913)."""

    def test_engine_logs_become_run_meta_and_commands_human_comments(self):
        comments = [
            _comment(1, "<!-- bot-agent-dev -->\n\n*Developer answer*\n\nok", BOT),
            _comment(2, "please take another look", HUMAN),
        ]
        # A stray engine notice is its own entry, never merged into the answer.
        notice = {
            "id": 3,
            "body": "<!-- bot-agent-dev -->\n**dev agent started** — View workflow run logs",
            "user": BOT,
        }
        entries = entries_from_raw_thread(
            {"id": 9, "body": "issue"},
            [comments[0], comments[1], notice],
            engine_login="nebuko-bot",
        )
        kinds = [entry.kind for entry in entries]
        assert kinds == [
            ContextKind.ISSUE_BODY,
            ContextKind.AGENT_OUTPUT,
            ContextKind.HUMAN_COMMENT,
            ContextKind.RUN_META,
        ]
        assert [entry.sequence for entry in entries] == [0, 2, 3, 4]

    def test_a_state_less_answer_grouped_when_it_has_part_rows(self):
        comments = [
            _comment(1, f"<!-- bot-agent-dev -->\n{PART.format(i=1, n=3)}first", BOT),
            _comment(2, "<!-- bot-agent-dev -->\n" + PART.format(i=2, n=3) + "second", BOT),
            _comment(3, "<!-- bot-agent-dev -->\n" + PART.format(i=3, n=3) + "third", BOT),
        ]
        entries = entries_from_raw_thread(None, comments, engine_login="nebuko-bot")
        assert len(entries) == 1
        assert entries[0].parts == ("1", "2", "3")
        assert entries[0].body.endswith("first")

    def test_a_sole_part_fragment_stays_one_entry(self):
        comments = [
            _comment(
                1,
                "<!-- bot-agent-dev -->\n<!-- bot-answer-continuation -->\n*(Part 2/2)*\nlate",
                BOT,
            ),
        ]
        entries = entries_from_raw_thread(None, comments, engine_login="nebuko-bot")
        assert entries[0].kind == ContextKind.AGENT_OUTPUT
        assert entries[0].parts is None

    def test_history_groups_the_parts_and_keeps_one_entry(self):
        state = build_state(None, workflow="default", step="implement", agent="dev")
        state = build_state(
            state, workflow="default", step="review", agent="review", comment_ids=[7, 8, 9]
        )
        comments = [
            _comment(7, f"<!-- bot-agent-dev -->\n{PART.format(i=1, n=3)}first", BOT),
            _comment(9, "<!-- bot-agent-dev -->\n" + PART.format(i=2, n=3) + "second", BOT),
            _comment(8, "<!-- bot-agent-dev -->\n" + PART.format(i=3, n=3) + "third", BOT),
        ]
        entries = entries_from_raw_thread(None, comments, state=state, engine_login="nebuko-bot")
        assert len(entries) == 1
        # The answer order comes from ``history``, not from the digest's order.
        assert entries[0].parts == ("7", "8", "9")

    def test_a_stray_engine_notice_is_not_an_answer(self):
        comments = [
            _comment(1, f"<!-- bot-agent-dev -->\n{PART.format(i=1, n=2)}first", BOT),
            _comment(2, "<!-- bot-agent-dev -->\n" + PART.format(i=2, n=2) + "second", BOT),
            _comment(
                3,
                "<!-- bot-agent-dev -->\n**dev agent started** — View workflow run logs",
                BOT,
            ),
        ]
        entries = entries_from_raw_thread(None, comments, engine_login="nebuko-bot")
        assert [entry.kind for entry in entries] == [
            ContextKind.AGENT_OUTPUT,
            ContextKind.RUN_META,
        ]
        assert entries[0].parts == ("1", "2")
        assert entries[1].parts is None


class TestForbiddenKinds:
    """Property: no selection ever returns engine bookkeeping."""

    @pytest.mark.parametrize("mode", ["default", "declared", "implicit"])
    def test_forbidden_kinds_never_reach_a_prompt(self, mode):
        entries = [
            _issue_body(),
            _run_meta(1),
            _output("plan", 2),
            _entry(ContextKind.WORKFLOW_STATE, 3, body="{}"),
            _human(4),
        ]
        path = _path("plan", "implement") if mode != "default" else None
        spec = StepContextSpec(
            "implement",
            inputs=None if mode == "default" else default_inputs(_path("plan", "implement"), "implement"),
        )
        if mode == "declared":
            spec = StepContextSpec(
                "implement",
                inputs=(ContextInput(ContextKind.HUMAN_COMMENT),),
            )
        result = resolve_selection(path, spec, entries)
        kinds = {entry.kind for entry in result.entries}
        assert not kinds & ContextKind.FORBIDDEN
