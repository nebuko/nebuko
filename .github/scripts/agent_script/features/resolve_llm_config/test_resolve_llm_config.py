"""Tests for resolve_llm_config — reasoning effort routing into chat_template_kwargs."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[3]))

from resolve_llm_config import normalize_reasoning_effort, resolve_llm_config


class TestNormalizeReasoningEffort(unittest.TestCase):
    """Pure I/O tests for normalize_reasoning_effort()."""

    def test_valid_values_pass_through(self):
        self.assertEqual(normalize_reasoning_effort("xhigh"), "xhigh")
        self.assertEqual(normalize_reasoning_effort("medium"), "medium")
        self.assertEqual(normalize_reasoning_effort("low"), "low")

    def test_high_is_alias_for_xhigh(self):
        self.assertEqual(normalize_reasoning_effort("high"), "xhigh")

    def test_none_and_unset_return_none(self):
        self.assertIsNone(normalize_reasoning_effort("none"))
        self.assertIsNone(normalize_reasoning_effort(None))
        self.assertIsNone(normalize_reasoning_effort(""))

    def test_unrecognized_value_is_dropped(self):
        self.assertIsNone(normalize_reasoning_effort("ultra"))

    def test_normalization_is_case_and_whitespace_insensitive(self):
        self.assertEqual(normalize_reasoning_effort(" High "), "xhigh")
        self.assertEqual(normalize_reasoning_effort("NONE"), None)


class TestReasoningEffortRouting(unittest.TestCase):
    """resolve_llm_config() must emit reasoning_effort via chat_template_kwargs."""

    CONFIG_TEMPLATE = """\
providers:
  test_provider:
    api_url: "http://test/v1"
    api_token_env: "TEST_LLM_API_KEY"
llms:
  test-model:
    model_path: "test/test-model"
    provider: ${providers.test_provider}
    default_temperature: 0.7
    thinking_enabled: @THINKING@
agents:
  dev:
    model: ${llms.test-model}
    temperature: 0.7
    prompt_file: "dev.md"
"""

    def setUp(self):
        self._env_keys = [
            "LLM_REASONING",
            "TEST_LLM_API_KEY",
            "LLM_MODEL",
            "CONFIG_COMPLEXITY",
            "CONFIG_TEMPERATURE",
            "CONFIG_TOP_P",
            "CONFIG_TOP_K",
            "CONFIG_MIN_P",
            "CONFIG_PRESENCE_PENALTY",
            "CONFIG_REPETITION_PENALTY",
            "AGENT_CONFIG_PATH",
            "ROLE_NAME",
        ]
        self._saved = {k: os.environ.get(k) for k in self._env_keys}
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, "agent-config.yaml")
        os.environ["TEST_LLM_API_KEY"] = "test-key"
        os.environ["ROLE_NAME"] = "dev"
        os.environ["AGENT_CONFIG_PATH"] = self._config_path
        for k in (
            "LLM_REASONING",
            "CONFIG_COMPLEXITY",
            "CONFIG_TEMPERATURE",
            "CONFIG_TOP_P",
            "CONFIG_TOP_K",
            "CONFIG_MIN_P",
            "CONFIG_PRESENCE_PENALTY",
            "CONFIG_REPETITION_PENALTY",
        ):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_config(self, thinking: str) -> None:
        with open(self._config_path, "w") as f:
            f.write(self.CONFIG_TEMPLATE.replace("@THINKING@", thinking))

    def _resolve(self, reasoning=None, thinking="true"):
        self._write_config(thinking)
        if reasoning is not None:
            os.environ["LLM_REASONING"] = reasoning
        cfg, _, _ = resolve_llm_config(None, "prompt")
        return cfg

    def _chat_kwargs(self, llm_config):
        extra = llm_config.get("litellm_extra_body", {})
        return extra.get("chat_template_kwargs", {})

    def test_effort_routed_into_chat_template_kwargs(self):
        cfg = self._resolve(reasoning="low")
        self.assertEqual(self._chat_kwargs(cfg)["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", cfg)

    def test_high_normalized_to_xhigh_in_chat_template_kwargs(self):
        cfg = self._resolve(reasoning="high")
        self.assertEqual(self._chat_kwargs(cfg)["reasoning_effort"], "xhigh")

    def test_none_omits_param(self):
        cfg = self._resolve(reasoning="none")
        self.assertNotIn("reasoning_effort", self._chat_kwargs(cfg))
        self.assertNotIn("reasoning_effort", cfg)

    def test_unset_omits_param(self):
        cfg = self._resolve()
        self.assertNotIn("reasoning_effort", self._chat_kwargs(cfg))

    def test_unrecognized_value_is_dropped(self):
        cfg = self._resolve(reasoning="ultra")
        self.assertNotIn("reasoning_effort", self._chat_kwargs(cfg))
        self.assertNotIn("reasoning_effort", cfg)

    def test_effort_suppressed_when_thinking_disabled(self):
        cfg = self._resolve(reasoning="low", thinking="false")
        kwargs = self._chat_kwargs(cfg)
        self.assertIs(kwargs.get("enable_thinking"), False)
        self.assertNotIn("reasoning_effort", kwargs)
        self.assertNotIn("reasoning_effort", cfg)

    def test_effort_coexists_with_enable_thinking(self):
        cfg = self._resolve(reasoning="medium")
        kwargs = self._chat_kwargs(cfg)
        self.assertEqual(kwargs["reasoning_effort"], "medium")
        self.assertIs(kwargs["enable_thinking"], True)

    def test_base_llm_config_unchanged(self):
        cfg = self._resolve(reasoning="xhigh")
        self.assertEqual(cfg["usage_id"], "agent_script")
        self.assertTrue(cfg["drop_params"])
        self.assertEqual(cfg["api_key"], "test-key")

    def test_missing_api_key_error_references_provider_env(self):
        os.environ.pop("TEST_LLM_API_KEY", None)
        self._write_config("true")
        with self.assertRaises(ValueError) as ctx:
            resolve_llm_config(None, "prompt")
        self.assertIn("TEST_LLM_API_KEY", str(ctx.exception))

    def test_missing_api_key_without_config_uses_generic_hint(self):
        os.environ.pop("TEST_LLM_API_KEY", None)
        with self.assertRaises(ValueError) as ctx:
            resolve_llm_config(None, "prompt")
        self.assertIn("api_token_env", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()