"""Tests for the run-failure model and classifier (issue #668)."""

import unittest

from openhands.sdk.llm.exceptions import LLMError, LLMServiceUnavailableError

from agent_script.features.run_agent.run_failure import (
    RESUME_COMMAND,
    RunFailure,
    RunFailureKind,
    classify_failure,
)


class TestRunFailureModel(unittest.TestCase):
    def test_resume_command_constant(self):
        # Single source of truth for the recovery command.
        self.assertEqual(RESUME_COMMAND, "/workflow resume")

    def test_kind_values_are_stable_strings(self):
        self.assertEqual(RunFailureKind.LLM_UNREACHABLE.value, "llm_unreachable")
        self.assertEqual(RunFailureKind.LLM_ERROR.value, "llm_error")
        self.assertEqual(RunFailureKind.UNKNOWN.value, "unknown")

    def test_resume_command_defaults_to_constant(self):
        failure = RunFailure(
            kind=RunFailureKind.UNKNOWN, summary="s", detail="d"
        )
        self.assertEqual(failure.resume_command, RESUME_COMMAND)

    def test_frozen_rejects_mutation(self):
        failure = RunFailure(
            kind=RunFailureKind.UNKNOWN, summary="s", detail="d"
        )
        with self.assertRaises(Exception):
            failure.summary = "changed"


class TestClassifyFailure(unittest.TestCase):
    def test_service_unavailable_is_llm_unreachable(self):
        error = LLMServiceUnavailableError(
            "Cannot connect to host 192.168.1.64:31728"
        )
        failure = classify_failure(error)
        self.assertIs(failure.kind, RunFailureKind.LLM_UNREACHABLE)
        self.assertIn("provider", failure.summary.lower())
        self.assertIn(RESUME_COMMAND, failure.resume_command)

    def test_connection_token_heuristic_is_llm_unreachable(self):
        # litellm wrapping may hide the typed exception; the raw text must
        # still classify as unreachable.
        error = RuntimeError(
            "litellm.InternalServerError: Hosted_vllmException - "
            "Cannot connect to host 192.168.1.64:31728"
        )
        self.assertIs(classify_failure(error).kind, RunFailureKind.LLM_UNREACHABLE)

    def test_detail_carries_raw_exception_text(self):
        error = RuntimeError("connect call failed ('192.168.1.64', 31728)")
        self.assertIn("connect call failed", classify_failure(error).detail)

    def test_non_service_llm_error_is_llm_error(self):
        failure = classify_failure(LLMError("bad request payload"))
        self.assertIs(failure.kind, RunFailureKind.LLM_ERROR)

    def test_generic_error_is_unknown_without_false_claim(self):
        failure = classify_failure(RuntimeError("llm exploded"))
        self.assertIs(failure.kind, RunFailureKind.UNKNOWN)
        self.assertNotIn("unreachable", failure.summary.lower())
        self.assertIn(RESUME_COMMAND, failure.resume_command)


if __name__ == "__main__":
    unittest.main()
