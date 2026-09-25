"""Tests for resolve_llm_config — reasoning effort routing into chat_template_kwargs."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[3]))

from resolve_llm_config import (
    normalize_reasoning_effort,
    resolve_api_url,
    resolve_llm_config,
)


class TestResolveApiUrl(unittest.TestCase):
    """Pure I/O tests for resolve_api_url()."""

    ENV = "LOCAL_VLLM_API_URL"

    def setUp(self):
        self._saved = os.environ.get(self.ENV)
        os.environ.pop(self.ENV, None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(self.ENV, None)
        else:
            os.environ[self.ENV] = self._saved

    def test_http_url_passes_through(self):
        self.assertEqual(
            resolve_api_url("http://192.168.1.64:31728/v1"),
            "http://192.168.1.64:31728/v1",
        )

    def test_https_url_passes_through_ignoring_env(self):
        os.environ[self.ENV] = "https://should-not-be-used.example.com"
        self.assertEqual(
            resolve_api_url("https://openrouter.ai/api/v1"),
            "https://openrouter.ai/api/v1",
        )

    def test_env_var_name_is_resolved(self):
        os.environ[self.ENV] = "http://localhost:8000/v1"
        self.assertEqual(resolve_api_url(self.ENV), "http://localhost:8000/v1")

    def test_env_var_value_whitespace_is_stripped(self):
        os.environ[self.ENV] = "  https://host.example.com/v1  "
        self.assertEqual(
            resolve_api_url(self.ENV), "https://host.example.com/v1"
        )

    def test_unset_env_var_raises(self):
        with self.assertRaises(ValueError):
            resolve_api_url(self.ENV)

    def test_empty_env_var_raises(self):
        os.environ[self.ENV] = "   "
        with self.assertRaises(ValueError):
            resolve_api_url(self.ENV)

    def test_non_url_env_value_raises(self):
        os.environ[self.ENV] = "not-a-url"
        with self.assertRaises(ValueError):
            resolve_api_url(self.ENV)


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


class TestApiUrlEnvVarRouting(unittest.TestCase):
    """api_url as an env var name is resolved end-to-end by resolve_llm_config()."""

    ENV = "LOCAL_VLLM_API_URL"
    CONFIG_TEMPLATE = """\
providers:
  test_provider:
    api_url: "@API_URL@"
    api_token_env: "TEST_LLM_API_KEY"
llms:
  test-model:
    model_path: "test/test-model"
    provider: ${providers.test_provider}
    default_temperature: 0.7
agents:
  dev:
    model: ${llms.test-model}
    temperature: 0.7
    prompt_file: "dev.md"
"""

    def setUp(self):
        self._env_keys = ["TEST_LLM_API_KEY", "LLM_BASE_URL", "AGENT_CONFIG_PATH", "ROLE_NAME", self.ENV]
        self._saved = {k: os.environ.get(k) for k in self._env_keys}
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, "agent-config.yaml")
        os.environ["TEST_LLM_API_KEY"] = "test-key"
        os.environ["ROLE_NAME"] = "dev"
        os.environ["AGENT_CONFIG_PATH"] = self._config_path
        os.environ.pop("LLM_BASE_URL", None)
        os.environ.pop(self.ENV, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_config(self, api_url: str) -> None:
        with open(self._config_path, "w") as f:
            f.write(self.CONFIG_TEMPLATE.replace("@API_URL@", api_url))

    def _resolve(self, api_url: str):
        self._write_config(api_url)
        cfg, _, _ = resolve_llm_config(None, "prompt")
        return cfg

    def test_literal_url_used_as_is(self):
        cfg = self._resolve("http://192.168.1.64:31728/v1")
        self.assertEqual(cfg["base_url"], "http://192.168.1.64:31728/v1")

    def test_env_var_name_resolved_to_base_url(self):
        os.environ[self.ENV] = "https://selfhosted.example.com/v1"
        cfg = self._resolve(self.ENV)
        self.assertEqual(cfg["base_url"], "https://selfhosted.example.com/v1")

    def test_unset_env_var_fails_fast(self):
        with self.assertRaises(ValueError):
            self._resolve(self.ENV)

    def test_non_url_env_value_fails_fast(self):
        os.environ[self.ENV] = "garbage"
        with self.assertRaises(ValueError):
            self._resolve(self.ENV)


class TestComplexityOverrideRouting(unittest.TestCase):
    """resolve_llm_config() applies per-agent complexity overrides."""

    CONFIG_TEMPLATE = """\
providers:
  base_provider:
    api_url: "http://base/v1"
    api_token_env: "BASE_LLM_API_KEY"
  high_provider:
    api_url: "http://high/v1"
    api_token_env: "HIGH_LLM_API_KEY"
llms:
  base-model:
    model_path: "test/base-model"
    provider: ${providers.base_provider}
    default_temperature: 0.7
  high-model:
    model_path: "test/high-model"
    provider: ${providers.high_provider}
agents:
  dev:
    model: ${llms.base-model}
    temperature: 0.7
    prompt_file: "dev.md"
    complexity_overrides:
      low:
        temperature: 0.0
      high:
        model: ${llms.high-model}
        temperature: 0.9
        reasoning: "xhigh"
        top_p: 0.8
"""

    def setUp(self):
        self._env_keys = [
            "LLM_REASONING",
            "BASE_LLM_API_KEY",
            "HIGH_LLM_API_KEY",
            "LLM_MODEL",
            "CONFIG_MODEL",
            "CONFIG_COMPLEXITY",
            "CONFIG_TEMPERATURE",
            "CONFIG_TOP_P",
            "CONFIG_TOP_K",
            "AGENT_CONFIG_PATH",
            "ROLE_NAME",
        ]
        self._saved = {k: os.environ.get(k) for k in self._env_keys}
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, "agent-config.yaml")
        with open(self._config_path, "w") as f:
            f.write(self.CONFIG_TEMPLATE)
        os.environ["BASE_LLM_API_KEY"] = "base-key"
        os.environ["HIGH_LLM_API_KEY"] = "high-key"
        os.environ["ROLE_NAME"] = "dev"
        os.environ["AGENT_CONFIG_PATH"] = self._config_path
        for k in (
            "LLM_REASONING",
            "CONFIG_MODEL",
            "CONFIG_COMPLEXITY",
            "CONFIG_TEMPERATURE",
            "CONFIG_TOP_P",
            "CONFIG_TOP_K",
        ):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _resolve(self, complexity=None):
        if complexity is not None:
            os.environ["CONFIG_COMPLEXITY"] = complexity
        cfg, _, _ = resolve_llm_config(None, "prompt")
        return cfg

    def test_high_tier_model_override(self):
        cfg = self._resolve(complexity="high")
        self.assertEqual(cfg["model"], "test/high-model")
        self.assertEqual(cfg["base_url"], "http://high/v1")
        self.assertEqual(cfg["api_key"], "high-key")

    def test_high_tier_temperature_override(self):
        cfg = self._resolve(complexity="high")
        self.assertEqual(cfg["temperature"], 0.9)

    def test_low_tier_temperature_override(self):
        cfg = self._resolve(complexity="low")
        self.assertEqual(cfg["temperature"], 0.0)

    def test_medium_tier_falls_back_to_base(self):
        cfg = self._resolve(complexity="medium")
        self.assertEqual(cfg["model"], "test/base-model")
        # base 0.7 vs medium floor 0.1 -> floor never lowers it
        self.assertEqual(cfg["temperature"], 0.7)

    def test_reasoning_override_applied_when_unset(self):
        cfg = self._resolve(complexity="high")
        kwargs = cfg["litellm_extra_body"]["chat_template_kwargs"]
        self.assertEqual(kwargs["reasoning_effort"], "xhigh")

    def test_explicit_reasoning_wins_over_override(self):
        os.environ["LLM_REASONING"] = "low"
        cfg = self._resolve(complexity="high")
        kwargs = cfg["litellm_extra_body"]["chat_template_kwargs"]
        self.assertEqual(kwargs["reasoning_effort"], "low")

    def test_config_top_p_env_wins_over_override(self):
        os.environ["CONFIG_TOP_P"] = "0.5"
        cfg = self._resolve(complexity="high")
        self.assertEqual(cfg["litellm_extra_body"]["top_p"], 0.5)

    def test_override_top_p_wins_over_base(self):
        cfg = self._resolve(complexity="high")
        self.assertEqual(cfg["litellm_extra_body"]["top_p"], 0.8)


class TestPriceForwarding(unittest.TestCase):
    """Config prices (per million tokens) become per-token SDK costs."""

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
    input_price: @INPUT_PRICE@
    output_price: @OUTPUT_PRICE@
agents:
  dev:
    model: ${llms.test-model}
    temperature: 0.7
    prompt_file: "dev.md"
"""

    def setUp(self):
        self._env_keys = ["TEST_LLM_API_KEY", "AGENT_CONFIG_PATH", "ROLE_NAME"]
        self._saved = {k: os.environ.get(k) for k in self._env_keys}
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, "agent-config.yaml")
        os.environ["TEST_LLM_API_KEY"] = "test-key"
        os.environ["ROLE_NAME"] = "dev"
        os.environ["AGENT_CONFIG_PATH"] = self._config_path

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _resolve(self, input_price: str, output_price: str):
        with open(self._config_path, "w") as f:
            f.write(
                self.CONFIG_TEMPLATE
                .replace("@INPUT_PRICE@", input_price)
                .replace("@OUTPUT_PRICE@", output_price)
            )
        cfg, _, _ = resolve_llm_config(None, "prompt")
        return cfg

    def test_per_million_prices_normalized_to_per_token(self):
        cfg = self._resolve("2.0", "8.0")
        self.assertAlmostEqual(cfg["input_cost_per_token"], 2.0e-6)
        self.assertAlmostEqual(cfg["output_cost_per_token"], 8.0e-6)

    def test_unset_prices_leave_keys_absent(self):
        cfg = self._resolve("null", "null")
        self.assertNotIn("input_cost_per_token", cfg)
        self.assertNotIn("output_cost_per_token", cfg)


if __name__ == "__main__":
    unittest.main()