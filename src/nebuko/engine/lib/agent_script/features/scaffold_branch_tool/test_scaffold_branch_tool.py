"""Tests for the report_scaffold_branch tool (issue #636 rework)."""

import unittest

from nebuko.engine.lib.agent_script.features.scaffold_branch_tool import (
    ScaffoldBranchReportAction,
    ScaffoldBranchReportExecutor,
    ScaffoldBranchReportTool,
    clear_scaffold_branch_run,
    get_recorded_scaffold_branch,
    record_scaffold_branch,
    reset_registry,
)


class TestScaffoldBranchRegistry(unittest.TestCase):
    """Test the run-scoped registry lifecycle."""

    def setUp(self):
        reset_registry()

    def test_record_and_get(self):
        record_scaffold_branch("run-1", "feature/issue-635-workflow-command")
        self.assertEqual(
            get_recorded_scaffold_branch("run-1"),
            "feature/issue-635-workflow-command",
        )

    def test_absent_run_returns_none(self):
        self.assertIsNone(get_recorded_scaffold_branch("missing"))

    def test_clear_drops_entry_only(self):
        record_scaffold_branch("run-1", "feature/issue-1-a")
        record_scaffold_branch("run-2", "feature/issue-2-b")
        clear_scaffold_branch_run("run-1")
        self.assertIsNone(get_recorded_scaffold_branch("run-1"))
        self.assertEqual(get_recorded_scaffold_branch("run-2"), "feature/issue-2-b")

    def test_clear_absent_run_is_noop(self):
        clear_scaffold_branch_run("never-recorded")

    def test_reset_drops_everything(self):
        record_scaffold_branch("run-1", "feature/issue-1-a")
        record_scaffold_branch("run-2", "feature/issue-2-b")
        reset_registry()
        self.assertIsNone(get_recorded_scaffold_branch("run-1"))
        self.assertIsNone(get_recorded_scaffold_branch("run-2"))


class TestScaffoldBranchReportExecutor(unittest.TestCase):
    """Test executor validation, retention, and observations."""

    def setUp(self):
        reset_registry()
        self.executor = ScaffoldBranchReportExecutor(conversation_id="run-x")

    def test_valid_ref_stored_and_success_observation(self):
        obs = self.executor(ScaffoldBranchReportAction(branch="feature/issue-635-x"))
        self.assertFalse(obs.is_error)
        self.assertEqual(get_recorded_scaffold_branch("run-x"), "feature/issue-635-x")

    def test_whitespace_trimmed_before_store(self):
        self.executor(ScaffoldBranchReportAction(branch="  feature/issue-635-x  "))
        self.assertEqual(get_recorded_scaffold_branch("run-x"), "feature/issue-635-x")

    def test_invalid_ref_rejected_with_error_observation(self):
        obs = self.executor(ScaffoldBranchReportAction(branch="feature|evil"))
        self.assertTrue(obs.is_error)
        self.assertIsNone(get_recorded_scaffold_branch("run-x"))

    def test_invalid_leading_dash_rejected(self):
        obs = self.executor(ScaffoldBranchReportAction(branch="-main"))
        self.assertTrue(obs.is_error)
        self.assertIsNone(get_recorded_scaffold_branch("run-x"))

    def test_invalid_empty_rejected(self):
        obs = self.executor(ScaffoldBranchReportAction(branch="   "))
        self.assertTrue(obs.is_error)
        self.assertIsNone(get_recorded_scaffold_branch("run-x"))

    def test_latest_valid_call_wins(self):
        self.executor(ScaffoldBranchReportAction(branch="feature/issue-1-first"))
        self.executor(ScaffoldBranchReportAction(branch="feature/issue-1-second"))
        self.assertEqual(
            get_recorded_scaffold_branch("run-x"), "feature/issue-1-second"
        )

    def test_invalid_call_never_clobbers_stored_valid_ref(self):
        self.executor(ScaffoldBranchReportAction(branch="feature/issue-1-good"))
        self.executor(ScaffoldBranchReportAction(branch="feature<bad>"))
        self.assertEqual(get_recorded_scaffold_branch("run-x"), "feature/issue-1-good")

    def test_default_conversation_id(self):
        executor = ScaffoldBranchReportExecutor()
        executor(ScaffoldBranchReportAction(branch="feature/issue-9-default"))
        self.assertEqual(
            get_recorded_scaffold_branch("default"), "feature/issue-9-default"
        )


class TestScaffoldBranchReportToolCreation(unittest.TestCase):
    """Test create() parameter rejection and registry key binding."""

    def setUp(self):
        reset_registry()

    def test_rejects_extra_params(self):
        with self.assertRaises(ValueError):
            ScaffoldBranchReportTool.create(foo="bar")

    def test_default_conversation_id_key(self):
        tools = ScaffoldBranchReportTool.create()
        self.assertEqual(len(tools), 1)
        tools[0].executor(ScaffoldBranchReportAction(branch="feature/issue-1-a"))
        self.assertEqual(get_recorded_scaffold_branch("default"), "feature/issue-1-a")
        clear_scaffold_branch_run("default")

    def test_real_sdk_resolution_keys_registry_by_state_id(self):
        """Mirror of the dispatch_next real-SDK seam: ``resolve_tool`` must
        bind ``str(state.id)`` so the read side (keyed under
        ``str(conversation.id)``) retrieves the same value.
        """
        import uuid

        from openhands.sdk.conversation.state import ConversationState
        from openhands.sdk.tool import Tool
        from openhands.sdk.tool.registry import register_tool, resolve_tool

        register_tool("ScaffoldBranchReportTool", ScaffoldBranchReportTool)
        state = ConversationState.model_construct(
            id=uuid.uuid4(), agent=None, workspace=None
        )
        tools = resolve_tool(Tool(name="ScaffoldBranchReportTool"), state)
        tools[0].executor(ScaffoldBranchReportAction(branch="feature/issue-635-x"))

        # Read side keys under str(conversation.id) == str(state.id).
        retrieved = get_recorded_scaffold_branch(str(state.id))
        self.assertEqual(retrieved, "feature/issue-635-x")
        clear_scaffold_branch_run(str(state.id))


if __name__ == "__main__":
    unittest.main()