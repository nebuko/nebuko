"""Tests for the bounded dispatch-recall loop (issue #524).

Covers the reviewer's required scenarios via the injected doubles (no LLM,
no event loop): recall-then-dispatch, exhausted recalls, no recall when a
decision already exists, and graceful degradation when the conversation is
dead.
"""

import unittest

from agent_script.features.run_agent.dispatch_recall import (
    DISPATCH_RECALL_PROMPT,
    MAX_DISPATCH_RECALLS,
    perform_dispatch_recalls,
)


class _FakeConversation:
    def __init__(self, fail_send=False):
        self.sent_messages = []
        self._fail_send = fail_send

    def send_message(self, prompt):
        if self._fail_send:
            raise RuntimeError("conversation closed")
        self.sent_messages.append(prompt)


class TestPerformDispatchRecalls(unittest.TestCase):
    def test_no_recall_when_dispatch_already_recorded(self):
        """A decision already present (tool call OR parseable sentinel)
        must cost zero extra turns."""
        conv = _FakeConversation()
        count = perform_dispatch_recalls(
            conv, lambda: True, lambda n: True
        )
        self.assertEqual(count, 0)
        self.assertEqual(conv.sent_messages, [])

    def test_recall_then_dispatch_stops_after_first_turn(self):
        """No dispatch on first pass → recall injected → model dispatches
        on the recall turn → loop stops (round-2 required test 2)."""
        conv = _FakeConversation()
        has = [False]

        def run_pass(n):
            # The model complies on the first recall turn.
            has[0] = True
            return True

        count = perform_dispatch_recalls(conv, lambda: has[0], run_pass)
        self.assertEqual(count, 1)
        self.assertEqual(len(conv.sent_messages), 1)
        self.assertIn("dispatch_next", conv.sent_messages[0])
        self.assertIs(conv.sent_messages[0], DISPATCH_RECALL_PROMPT)

    def test_exhausted_recalls_hit_the_hard_cap(self):
        """Never a dispatch: exactly MAX_DISPATCH_RECALLS turns, no more
        (round-2 required test 3 — the 3-strike hold boundary)."""
        conv = _FakeConversation()
        count = perform_dispatch_recalls(
            conv, lambda: False, lambda n: True
        )
        self.assertEqual(count, MAX_DISPATCH_RECALLS)
        self.assertEqual(len(conv.sent_messages), MAX_DISPATCH_RECALLS)

    def test_dead_conversation_degrades_gracefully(self):
        """send_message raising (closed/condenser failure after a timeout)
        must not escape the termination path — log and stop instead.
        The failed turn still counts as an attempt (it consumed a recall
        slot), but nothing was actually delivered."""
        conv = _FakeConversation(fail_send=True)
        count = perform_dispatch_recalls(conv, lambda: False, lambda n: True)
        self.assertEqual(count, 1)
        self.assertEqual(len(conv.sent_messages), 0)

    def test_budget_exhausted_pass_stops_early(self):
        """A recall pass that overruns its budget aborts further recalls."""
        conv = _FakeConversation()
        count = perform_dispatch_recalls(
            conv, lambda: False, lambda n: False
        )
        self.assertEqual(count, 1)
        self.assertEqual(len(conv.sent_messages), 1)


if __name__ == "__main__":
    unittest.main()