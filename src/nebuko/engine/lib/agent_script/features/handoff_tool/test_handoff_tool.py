"""Tests for the handoff tool, its document and its fold marker (issue #965)."""

import unittest
from unittest.mock import MagicMock

from agent_script.features.handoff_tool import (
    HANDOFF_DESCRIPTION,
    HANDOFF_BLOCK_HEADER,
    HANDOFF_RELATIVE_PATH,
    HandoffAction,
    HandoffDocument,
    HandoffExecutor,
    HandoffTool,
    record_fold,
    render_handoff_block,
    reset_registry,
    take_fold,
)


class TestHandoffDocumentRender(unittest.TestCase):
    """The document renders with the fixed headings, whatever the input."""

    def _document(self, **overrides):
        fields = dict(
            completed=("wired the store",),
            state="branch feature/x, PR #1 open",
            verified_facts=("`pytest -q` → 21 passed",),
            hypotheses=("the resolver may cache the path",),
            blockers=("none",),
            next_steps=("write the prompt block", "run the full suite"),
            plan_changes=("dropped the issue-comment backend",),
            updated_at="2026-01-02T03:04:05Z",
        )
        fields.update(overrides)
        return HandoffDocument(**fields)

    def test_render_emits_every_fixed_heading(self):
        rendered = self._document().render()

        self.assertTrue(rendered.startswith("# Session handoff"))
        for heading in (
            "## Completed",
            "## State",
            "## Verified facts (claims to re-check)",
            "## Hypotheses (unverified)",
            "## Blockers",
            "## Next steps",
            "## Plan changes",
        ):
            self.assertIn(heading, rendered)
        self.assertIn("- updated: 2026-01-02T03:04:05Z", rendered)

    def test_next_steps_render_ordered(self):
        rendered = self._document().render()

        self.assertIn("1. write the prompt block", rendered)
        self.assertIn("2. run the full suite", rendered)

    def test_render_states_the_no_secrets_rule(self):
        rendered = self._document().render()

        self.assertIn("Never store secrets, tokens or private customer content", rendered)

    def test_empty_sections_render_a_placeholder(self):
        rendered = self._document(
            completed=(),
            verified_facts=(),
            hypotheses=(),
            blockers=(),
            plan_changes=(),
            state="",
        ).render()

        self.assertIn("_none recorded_", rendered)
        self.assertIn("_not recorded_", rendered)

    def test_overwrite_drops_stale_claims(self):
        """A second write is a complete replacement — nothing is appended."""
        first = self._document(verified_facts=("STALE CLAIM",)).render()
        second = self._document(verified_facts=("fresh claim",)).render()

        self.assertIn("STALE CLAIM", first)
        self.assertNotIn("STALE CLAIM", second)
        self.assertIn("fresh claim", second)


class TestRenderHandoffBlock(unittest.TestCase):
    """The prompt block frames the document as claims to re-check."""

    def test_empty_body_renders_nothing(self):
        self.assertEqual(render_handoff_block(""), "")
        self.assertEqual(render_handoff_block("   \n "), "")

    def test_block_carries_header_framing_and_body(self):
        block = render_handoff_block("# Session handoff\n\n- updated: x")

        self.assertTrue(block.startswith(HANDOFF_BLOCK_HEADER))
        self.assertIn("*claim* to re-check", block)
        self.assertIn("Hypotheses are unverified guesses", block)
        self.assertIn("# Session handoff", block)


class TestHandoffAction(unittest.TestCase):
    """Schema-level checks on the action the model fills in."""

    def test_all_fields_default_to_empty(self):
        action = HandoffAction()

        self.assertEqual(action.completed, [])
        self.assertEqual(action.state, "")
        self.assertEqual(action.next_steps, [])
        self.assertFalse(action.fold)

    def test_description_states_the_no_secrets_rule_and_overwrite(self):
        self.assertIn("NEVER put secrets, tokens, credentials", HANDOFF_DESCRIPTION)
        self.assertIn("OVERWRITES", HANDOFF_DESCRIPTION)
        self.assertIn("agent_checkpoint", HANDOFF_DESCRIPTION)


class TestHandoffExecutor(unittest.TestCase):
    """The executor delegates the git work to the store and records folds."""

    def setUp(self):
        reset_registry()

    def _executor(self, store):
        return HandoffExecutor(conversation_id="run-1", store=store)

    def test_write_commits_the_document_and_reports_it(self):
        store = MagicMock()
        store.write_and_commit.return_value = "abc1234def"
        executor = self._executor(store)

        observation = executor(
            HandoffAction(completed=["did the thing"], state="branch x")
        )

        self.assertFalse(observation.is_error)
        document = store.write_and_commit.call_args.args[0]
        self.assertEqual(document.completed, ("did the thing",))
        self.assertTrue(document.updated_at.endswith("Z"))
        self.assertIn("abc1234", observation.text)
        self.assertIsNone(take_fold("run-1"))

    def test_non_fold_call_does_not_record_a_fold(self):
        store = MagicMock()
        store.write_and_commit.return_value = "abc1234def"

        self._executor(store)(HandoffAction(state="still working"))

        self.assertIsNone(take_fold("run-1"))

    def test_fold_records_the_marker_and_tells_the_agent_to_stop(self):
        store = MagicMock()
        store.write_and_commit.return_value = "abc1234def"
        executor = self._executor(store)

        observation = executor(HandoffAction(state="context is exhausted", fold=True))

        self.assertFalse(observation.is_error)
        # The document is saved first, even when folding.
        store.write_and_commit.assert_called_once()
        self.assertIsNotNone(take_fold("run-1"))
        self.assertIn("STOP NOW", observation.text)

    def test_git_failure_returns_an_error_observation(self):
        store = MagicMock()
        store.write_and_commit.side_effect = RuntimeError("no remote")
        executor = self._executor(store)

        observation = executor(HandoffAction(state="x", fold=True))

        self.assertTrue(observation.is_error)
        self.assertIn("no remote", observation.text)
        # A failed write must not fold the run: the checkpoint was not saved.
        self.assertIsNone(take_fold("run-1"))

    def test_write_targets_the_conventional_path(self):
        self.assertEqual(HANDOFF_RELATIVE_PATH, ".nebuko/handoff.md")


class TestFoldRegistry(unittest.TestCase):
    """``record_fold``/``take_fold`` are run-scoped and take-and-clear."""

    def setUp(self):
        reset_registry()

    def test_take_is_scoped_per_run(self):
        record_fold("run-a", "note a")
        record_fold("run-b", "note b")

        self.assertEqual(take_fold("run-a"), "note a")
        self.assertIsNone(take_fold("run-a"))
        self.assertEqual(take_fold("run-b"), "note b")

    def test_missing_marker_returns_none(self):
        self.assertIsNone(take_fold("nobody"))


class TestHandoffToolCreate(unittest.TestCase):
    """Definition-level wiring of the tool."""

    def test_create_returns_one_tool_with_the_action_schema(self):
        tools = HandoffTool.create(conversation_id="run-9")

        self.assertEqual(len(tools), 1)
        self.assertIs(tools[0].action_type, HandoffAction)
        self.assertEqual(tools[0].description, HANDOFF_DESCRIPTION)

    def test_create_rejects_parameters(self):
        with self.assertRaises(ValueError):
            HandoffTool.create(unexpected=True)
