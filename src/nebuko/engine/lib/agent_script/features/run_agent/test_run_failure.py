"""Tests for the run-failure model and classifier (issue #668)."""

import unittest

from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMContentPolicyViolationError,
    LLMContextWindowExceedError,
    LLMError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)

from nebuko.engine.lib.agent_script.features.run_agent.run_failure import (
    RESUME_COMMAND,
    ProviderFailure,
    RunFailure,
    RunFailureKind,
    classify_failure,
    is_provider_failure,
    redact_secrets,
)


class TestRunFailureModel(unittest.TestCase):
    def test_resume_command_constant(self):
        # Single source of truth for the recovery command.
        self.assertEqual(RESUME_COMMAND, "/nebuko resume")

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

    def test_provider_error_matrix(self):
        """Connectivity/5xx/429/401/403/404 stop; request-specific 400s don't (#1322)."""
        stopping = {
            "connectivity": LLMServiceUnavailableError("Cannot connect to host h:1"),
            "timeout": LLMTimeoutError("Request timed out"),
            "rate limit": LLMRateLimitError("Error code: 429 - rate limit"),
            "auth": LLMAuthenticationError("Error code: 401 - invalid api key"),
            "forbidden": RuntimeError("Error code: 403 - forbidden"),
            "not found": RuntimeError("Error code: 404 - model not found"),
        }
        for label, error in stopping.items():
            with self.subTest(label):
                failure = classify_failure(error)
                self.assertTrue(is_provider_failure(failure), label)
                self.assertIn(RESUME_COMMAND, failure.resume_command)

        request_specific = {
            "context window": LLMContextWindowExceedError("context too long"),
            "content policy": LLMContentPolicyViolationError("content rejected"),
        }
        for label, error in request_specific.items():
            with self.subTest(label):
                failure = classify_failure(error)
                self.assertFalse(is_provider_failure(failure), label)
                self.assertIs(failure.kind, RunFailureKind.LLM_ERROR)

    def test_config_errors_require_a_config_fix(self):
        failure = classify_failure(LLMAuthenticationError("Error code: 401"))
        self.assertIs(failure.kind, RunFailureKind.LLM_CONFIG_ERROR)
        self.assertTrue(failure.requires_config_fix)
        self.assertIn("won't help", failure.summary)

    def test_wrapped_404_classified_as_config_error(self):
        """Run 42049492853: the 404 arrived inside ``ConversationRunError``."""
        from openhands.sdk.conversation.exceptions import ConversationRunError

        inner = RuntimeError("litellm.NotFoundError: Error code: 404 - not found")
        wrapped = ConversationRunError("conv-1", inner)

        failure = classify_failure(wrapped)

        self.assertIs(failure.kind, RunFailureKind.LLM_CONFIG_ERROR)
        self.assertTrue(failure.requires_config_fix)
        self.assertIn("404", failure.detail)

    def test_wrapped_connectivity_error_is_unreachable(self):
        from openhands.sdk.conversation.exceptions import ConversationRunError

        inner = LLMServiceUnavailableError("Cannot connect to host h:1")
        failure = classify_failure(ConversationRunError("conv-1", inner))

        self.assertIs(failure.kind, RunFailureKind.LLM_UNREACHABLE)


class TestProviderFailure(unittest.TestCase):
    def test_carries_the_provider_facts(self):
        failure = ProviderFailure(
            "Error code: 404",
            provider="https://example.test/v1",
            model="glm-5",
            requires_config_fix=True,
        )
        self.assertEqual(failure.provider_error, "Error code: 404")
        self.assertEqual(failure.provider, "https://example.test/v1")
        self.assertEqual(failure.model, "glm-5")
        self.assertEqual(failure.agent_phase, "agent")
        self.assertTrue(failure.requires_config_fix)

    def test_redact_secrets_removes_configured_values(self):
        text = "Error code: 401 - invalid api key sk-secret-123 for model m"
        self.assertNotIn("sk-secret-123", redact_secrets(text, ["sk-secret-123"]))
        self.assertIn("[redacted]", redact_secrets(text, ["sk-secret-123"]))
        # Empty secrets are ignored rather than matching everything.
        self.assertEqual(redact_secrets(text, [""]), text)


if __name__ == "__main__":
    unittest.main()
