"""Tests for resolve_llm_config — reasoning effort routing into chat_template_kwargs."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import litellm


from resolve_llm_config import (
    LLM_TIMEOUT_S,
    LLMConfigError,
    normalize_base_url,
    normalize_reasoning_effort,
    resolve_api_url,
    resolve_llm_config,
)
import resolve_llm_config as _rlc

from nebuko.engine.lib.agent_script.features.resolve_provider_runtime.resolve_provider_runtime import (
    build_provider_runtime,
)
from nebuko.engine.lib.config import ProviderConfig


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


class TestNormalizeBaseUrl(unittest.TestCase):
    """Pure I/O tests for normalize_base_url() — issue #717."""

    def test_bare_api_root_passes_through(self):
        self.assertEqual(
            normalize_base_url("https://openrouter.ai/api/v1"),
            "https://openrouter.ai/api/v1",
        )

    def test_chat_completions_suffix_is_stripped(self):
        self.assertEqual(
            normalize_base_url("https://openrouter.ai/api/v1/chat/completions"),
            "https://openrouter.ai/api/v1",
        )

    def test_trailing_slash_after_suffix_is_stripped(self):
        self.assertEqual(
            normalize_base_url("http://host:30080/v1/chat/completions/"),
            "http://host:30080/v1",
        )

    def test_unrelated_route_is_untouched(self):
        self.assertEqual(
            normalize_base_url("https://host.example.com/v1/responses"),
            "https://host.example.com/v1/responses",
        )

    def test_unset_url_passes_through(self):
        self.assertIsNone(normalize_base_url(None))
        self.assertEqual(normalize_base_url(""), "")


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
        # The top-level SDK default ("high") must be actively killed, not merely
        # neutralized by absence: the provider reads a missing param as its own
        # default, which is reasoning-on (issues #941, #958).
        self.assertEqual(cfg["reasoning_effort"], "none")

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

    def test_llm_timeout_raised_to_900(self):
        """300s aborts a high-reasoning turn mid-think; 900s gives headroom (#775)."""
        cfg = self._resolve(reasoning="xhigh")
        self.assertEqual(cfg["timeout"], LLM_TIMEOUT_S)
        self.assertEqual(LLM_TIMEOUT_S, 900)

    def test_streaming_requested(self):
        """Streaming keeps the request warm across the long reasoning phase (#775)."""
        cfg = self._resolve(reasoning="xhigh")
        self.assertIs(cfg["stream"], True)

    def test_sdk_derives_stream_idle_timeout_from_llm_timeout(self):
        """The stream idle guard must match the raised LLM timeout (#775)."""
        from openhands.sdk import LLM

        llm = LLM(**self._resolve(reasoning="xhigh"))

        self.assertEqual(llm.timeout, LLM_TIMEOUT_S)
        self.assertEqual(llm.stream_idle_timeout, float(LLM_TIMEOUT_S))

    def test_llm_uses_brand_attribution(self):
        """The assembled settings carry the Nebuko brand, not the SDK default."""
        cfg = self._resolve()
        self.assertEqual(cfg["openrouter_app_name"], "Nebuko")
        self.assertEqual(
            cfg["openrouter_site_url"], "https://github.com/nebuko/nebuko"
        )
        self.assertNotEqual(cfg["openrouter_app_name"], "OpenHands")


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
        self._env_keys = ["TEST_LLM_API_KEY", "AGENT_CONFIG_PATH", "ROLE_NAME", self.ENV]
        self._saved = {k: os.environ.get(k) for k in self._env_keys}
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, "agent-config.yaml")
        os.environ["TEST_LLM_API_KEY"] = "test-key"
        os.environ["ROLE_NAME"] = "dev"
        os.environ["AGENT_CONFIG_PATH"] = self._config_path
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

    def test_chat_completions_suffix_in_config_url_is_stripped(self):
        cfg = self._resolve("https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(cfg["base_url"], "https://openrouter.ai/api/v1")

    def test_unset_env_var_fails_fast(self):
        with self.assertRaises(ValueError):
            self._resolve(self.ENV)

    def test_non_url_env_value_fails_fast(self):
        os.environ[self.ENV] = "garbage"
        with self.assertRaises(ValueError):
            self._resolve(self.ENV)


class TestBaseUrlNormalizedFromConfig(unittest.TestCase):
    """The config-file base_url is normalized before it reaches ``LLM(**cfg)``.

    ``base_url`` now comes only from the provider's ``api_url`` (the legacy
    ``LLM_BASE_URL`` env fallback was removed, issue #925); the redundant
    ``/chat/completions`` route is still stripped (issue #717).
    """

    CONFIG_TEMPLATE = """\
providers:
  test_provider:
    api_url: "@API_URL@"
    api_token_env: "TEST_LLM_API_KEY"
llms:
  test-model:
    model_path: "test/test-model"
    provider: ${providers.test_provider}
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

    def _resolve(self, api_url: str) -> dict:
        with open(self._config_path, "w") as f:
            f.write(self.CONFIG_TEMPLATE.replace("@API_URL@", api_url))
        cfg, _, _ = resolve_llm_config(None, "prompt")
        return cfg

    def test_chat_completions_suffix_is_stripped(self):
        cfg = self._resolve("http://litellm:4000/v1/chat/completions")
        self.assertEqual(cfg["base_url"], "http://litellm:4000/v1")

    def test_bare_url_passes_through(self):
        cfg = self._resolve("http://litellm:4000/v1")
        self.assertEqual(cfg["base_url"], "http://litellm:4000/v1")


class TestLLMConfigError(unittest.TestCase):
    """Every resolution failure is a typed, actionable LLMConfigError (#925).

    The agent-config file is the sole source of LLM configuration; the legacy
    ``LLM_API_KEY`` / ``LLM_MODEL`` / ``LLM_BASE_URL`` / ``LLM_TEMPERATURE``
    environment variables are no longer read.
    """

    CONFIG_TEMPLATE = """\
providers:
  test_provider:
    api_url: "http://test/v1"
    api_token_env: "TEST_LLM_API_KEY"
llms:
  test-model:
    model_path: "test/test-model"
    provider: ${providers.test_provider}
agents:
  dev:
    model: ${llms.test-model}
    temperature: 0.7
    prompt_file: "dev.md"
"""

    def setUp(self):
        self._env_keys = [
            "TEST_LLM_API_KEY",
            "LLM_API_KEY",
            "LLM_MODEL",
            "LLM_BASE_URL",
            "LLM_TEMPERATURE",
            "AGENT_CONFIG_PATH",
            "ROLE_NAME",
        ]
        self._saved = {k: os.environ.get(k) for k in self._env_keys}
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, "agent-config.yaml")
        with open(self._config_path, "w") as f:
            f.write(self.CONFIG_TEMPLATE)
        os.environ["TEST_LLM_API_KEY"] = "test-key"
        os.environ["ROLE_NAME"] = "dev"
        os.environ["AGENT_CONFIG_PATH"] = self._config_path
        for k in ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "LLM_TEMPERATURE"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_missing_config_file_raises_config_file(self):
        os.environ["AGENT_CONFIG_PATH"] = os.path.join(self._tmpdir, "nope.yaml")
        with self.assertRaises(LLMConfigError) as context:
            resolve_llm_config(None, "prompt")
        self.assertEqual(context.exception.missing, "config_file")
        self.assertIn("nope.yaml", str(context.exception))

    def test_unloadable_config_raises_config_load(self):
        with open(self._config_path, "w") as f:
            f.write("foo: bar\n")
        with self.assertRaises(LLMConfigError) as context:
            resolve_llm_config(None, "prompt")
        self.assertEqual(context.exception.missing, "config_load")
        self.assertTrue(context.exception.cause)

    def test_unknown_role_raises_agent_role(self):
        os.environ["ROLE_NAME"] = "unknown"
        with self.assertRaises(LLMConfigError) as context:
            resolve_llm_config(None, "prompt")
        self.assertEqual(context.exception.missing, "agent_role")
        self.assertIn("unknown", str(context.exception))

    def test_unset_provider_secret_names_the_env_var(self):
        os.environ.pop("TEST_LLM_API_KEY", None)
        with self.assertRaises(LLMConfigError) as context:
            resolve_llm_config(None, "prompt")
        self.assertEqual(context.exception.missing, "api_token_env")
        self.assertEqual(context.exception.api_token_env, "TEST_LLM_API_KEY")
        self.assertIn("TEST_LLM_API_KEY", str(context.exception))

    def test_stale_legacy_llm_api_key_is_ignored(self):
        """A stale LLM_API_KEY must not mask the config-declared secret (#925)."""
        os.environ.pop("TEST_LLM_API_KEY", None)
        os.environ["LLM_API_KEY"] = "stale-legacy-key"
        with self.assertRaises(LLMConfigError) as context:
            resolve_llm_config(None, "prompt")
        self.assertEqual(context.exception.missing, "api_token_env")

    def test_error_is_a_value_error_for_existing_callers(self):
        os.environ["ROLE_NAME"] = "unknown"
        with self.assertRaises(ValueError):
            resolve_llm_config(None, "prompt")


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
  low_provider:
    api_url: "http://low/v1"
    api_token_env: "LOW_LLM_API_KEY"
llms:
  base-model:
    model_path: "test/base-model"
    provider: ${providers.base_provider}
    default_temperature: 0.7
    input_price: 2.0
    output_price: 8.0
    thinking_enabled: true
    preserve_thinking: true
    thinking_token_budget: 4096
    max_output_tokens: 8192
    stop_sequences: ["<base-stop>"]
  high-model:
    model_path: "test/high-model"
    provider: ${providers.high_provider}
    input_price: 10.0
    output_price: 40.0
    thinking_enabled: true
    preserve_thinking: false
    thinking_token_budget: 65536
    max_output_tokens: 32768
    stop_sequences: ["<high-stop>"]
  low-model:
    model_path: "test/low-model"
    provider: ${providers.low_provider}
    thinking_enabled: false
    max_output_tokens: 2048
agents:
  dev:
    model: ${llms.base-model}
    temperature: 0.7
    prompt_file: "dev.md"
    complexity_overrides:
      low:
        model: ${llms.low-model}
        temperature: 0.0
        reasoning: "xhigh"
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
            "LOW_LLM_API_KEY",
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
        os.environ["LOW_LLM_API_KEY"] = "low-key"
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

    def test_low_tier_model_override(self):
        cfg = self._resolve(complexity="low")
        self.assertEqual(cfg["model"], "test/low-model")
        self.assertEqual(cfg["base_url"], "http://low/v1")
        self.assertEqual(cfg["api_key"], "low-key")

    def test_low_tier_non_thinking_swap_suppresses_reasoning(self):
        """A swap to a non-thinking model must drop enable_thinking and reasoning."""
        cfg = self._resolve(complexity="low")
        kwargs = cfg["litellm_extra_body"]["chat_template_kwargs"]
        self.assertIs(kwargs["enable_thinking"], False)
        self.assertNotIn("reasoning_effort", kwargs)

    def test_high_tier_thinking_params_follow_swapped_model(self):
        cfg = self._resolve(complexity="high")
        kwargs = cfg["litellm_extra_body"]["chat_template_kwargs"]
        self.assertIs(kwargs["enable_thinking"], True)
        self.assertIs(kwargs["preserve_thinking"], False)
        self.assertEqual(cfg["litellm_extra_body"]["thinking_token_budget"], 65536)

    def test_high_tier_pricing_follows_swapped_model(self):
        """The swapped model's prices are no longer forwarded to the SDK (#714)."""
        cfg = self._resolve(complexity="high")
        self.assertNotIn("input_cost_per_token", cfg)
        self.assertNotIn("output_cost_per_token", cfg)

    def test_high_tier_max_output_tokens_follows_swapped_model(self):
        cfg = self._resolve(complexity="high")
        self.assertEqual(cfg["max_output_tokens"], 32768)

    def test_high_tier_stop_sequences_follow_swapped_model(self):
        cfg = self._resolve(complexity="high")
        self.assertEqual(cfg["litellm_extra_body"]["stop"], ["<high-stop>"])

    def test_medium_tier_keeps_base_model_attributes(self):
        cfg = self._resolve(complexity="medium")
        self.assertNotIn("input_cost_per_token", cfg)
        self.assertEqual(cfg["max_output_tokens"], 8192)
        self.assertEqual(cfg["litellm_extra_body"]["stop"], ["<base-stop>"])
        self.assertEqual(cfg["litellm_extra_body"]["thinking_token_budget"], 4096)


class TestPriceForwarding(unittest.TestCase):
    """Config prices are NOT forwarded to the SDK (issue #714).

    The SDK's custom-cost path is cache-blind: forwarding only input/output
    prices would make LiteLLM price cache tokens at the full input rate and its
    ``accumulated_cost`` would shadow our cache-aware fallback. Session cost is
    computed our-side in ``metrics_collector`` instead, so no ``*_cost_per_token``
    keys must ever reach ``LLM(**llm_config)``.
    """

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

    def test_configured_prices_are_not_forwarded_to_sdk(self):
        cfg = self._resolve("2.0", "8.0")
        self.assertNotIn("input_cost_per_token", cfg)
        self.assertNotIn("output_cost_per_token", cfg)

    def test_unset_prices_leave_keys_absent(self):
        cfg = self._resolve("null", "null")
        self.assertNotIn("input_cost_per_token", cfg)
        self.assertNotIn("output_cost_per_token", cfg)

    def test_partial_prices_leave_keys_absent(self):
        cfg = self._resolve("2.0", "null")
        self.assertNotIn("input_cost_per_token", cfg)
        self.assertNotIn("output_cost_per_token", cfg)


class TestProviderHeaderDecoration(unittest.TestCase):
    """The provider runtime's headers land in the dict ``LLM(**llm_config)`` gets.

    ``extra_headers`` is an SDK-native field, so the merged map is passed through
    unchanged; the engine-stamped per-issue session id wins a static collision.
    """

    CONFIG_TEMPLATE = """\
providers:
  opencode_go:
    api_url: "https://opencode.ai/zen/go/v1"
    api_token_env: "OPENCODE_GO_API_KEY"
@IDENTITY@
llms:
  go-model:
    model_path: "openai/deepseek/deepseek-v4.1-flash"
    provider: "${providers.opencode_go}"
agents:
  dev:
    model: "${llms.go-model}"
    temperature: 0.1
    prompt_file: "dev.md"
"""

    _ENV_KEYS = (
        "AGENT_CONFIG_PATH",
        "ROLE_NAME",
        "CONFIG_MODEL",
        "CONFIG_TEMPERATURE",
        "OPENCODE_GO_API_KEY",
        "LLM_SESSION_ID",
    )

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._ENV_KEYS}
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, "agent-config.yaml")
        os.environ["ROLE_NAME"] = "dev"
        os.environ["AGENT_CONFIG_PATH"] = self._config_path
        os.environ["OPENCODE_GO_API_KEY"] = "secret"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _resolve(self, identity: str):
        with open(self._config_path, "w") as f:
            f.write(self.CONFIG_TEMPLATE.replace("@IDENTITY@", identity))
        cfg, _, _ = resolve_llm_config(None, "prompt")
        return cfg

    _FULL_IDENTITY = (
        '    user_agent: "nebuko/1.0"\n'
        '    session_header: "x-opencode-session"\n'
    )

    def test_merges_static_identity_and_issue_session_headers(self):
        os.environ["LLM_SESSION_ID"] = "854"
        cfg = self._resolve(self._FULL_IDENTITY)
        self.assertEqual(
            cfg["extra_headers"],
            {"User-Agent": "nebuko/1.0", "x-opencode-session": "854"},
        )

    def test_no_session_id_keeps_only_the_static_headers(self):
        cfg = self._resolve(self._FULL_IDENTITY)
        self.assertEqual(cfg["extra_headers"], {"User-Agent": "nebuko/1.0"})

    def test_engine_session_value_wins_a_static_collision(self):
        os.environ["LLM_SESSION_ID"] = "854"
        cfg = self._resolve(
            '    user_agent: "nebuko/1.0"\n'
            '    session_header: "x-opencode-session"\n'
            "    extra_headers:\n"
            '      x-opencode-session: "static"\n'
        )
        self.assertEqual(cfg["extra_headers"]["x-opencode-session"], "854")

    def test_provider_without_identity_fields_sets_no_headers(self):
        cfg = self._resolve("")
        self.assertNotIn("extra_headers", cfg)

    def test_extra_headers_is_a_valid_sdk_llm_field(self):
        """Guard against a typo breaking ``LLM(**llm_config)`` at runtime."""
        from openhands.sdk import LLM

        self.assertIn("extra_headers", LLM.model_fields)


class TestProviderCacheKeyDecoration(unittest.TestCase):
    """The provider's cache-key body param lands in ``litellm_extra_body``.

    Mistral's ``prompt_cache_key`` is a JSON body field, not a header: it rides
    ``litellm_extra_body`` so litellm's per-route param whitelist cannot drop it.
    The engine-stamped issue id is the value; the engine value wins a collision
    with any static body entry.
    """

    CONFIG_TEMPLATE = """\
providers:
  mistral:
    api_url: "https://api.mistral.ai/v1"
    api_token_env: "MISTRAL_API_KEY"
@IDENTITY@
llms:
  small-model:
    model_path: "mistral/mistral-small-2603"
    provider: "${providers.mistral}"
agents:
  dev:
    model: "${llms.small-model}"
    temperature: 0.1
    prompt_file: "dev.md"
"""

    _ENV_KEYS = (
        "AGENT_CONFIG_PATH",
        "ROLE_NAME",
        "CONFIG_MODEL",
        "CONFIG_TEMPERATURE",
        "MISTRAL_API_KEY",
        "LLM_SESSION_ID",
    )

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._ENV_KEYS}
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, "agent-config.yaml")
        os.environ["ROLE_NAME"] = "dev"
        os.environ["AGENT_CONFIG_PATH"] = self._config_path
        os.environ["MISTRAL_API_KEY"] = "secret"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _resolve(self, identity: str):
        with open(self._config_path, "w") as f:
            f.write(self.CONFIG_TEMPLATE.replace("@IDENTITY@", identity))
        cfg, _, _ = resolve_llm_config(None, "prompt")
        return cfg

    _DECLARED = '    cache_key_param: "prompt_cache_key"\n'

    def test_merges_the_engine_id_into_the_request_body(self):
        os.environ["LLM_SESSION_ID"] = "1068"
        cfg = self._resolve(self._DECLARED)
        self.assertEqual(cfg["litellm_extra_body"]["prompt_cache_key"], "1068")

    def test_no_session_id_sends_no_cache_key(self):
        cfg = self._resolve(self._DECLARED)
        self.assertNotIn("prompt_cache_key", cfg.get("litellm_extra_body", {}))

    def test_provider_without_a_declared_param_sends_nothing(self):
        os.environ["LLM_SESSION_ID"] = "1068"
        cfg = self._resolve("")
        self.assertNotIn("prompt_cache_key", cfg.get("litellm_extra_body", {}))

    def test_preserves_static_body_params(self):
        from nebuko.engine.lib.agent_script.features.resolve_provider_runtime.resolve_provider_runtime import (
            IssueCacheKeyRuntime,
            StaticProviderRuntime,
        )

        provider = ProviderConfig(
            api_url="https://api.mistral.ai/v1",
            api_token_env="MISTRAL_API_KEY",
            cache_key_param="prompt_cache_key",
        )
        static_body = {"chat_template_kwargs": {"enable_thinking": True}}
        merged = dict(static_body)
        for runtime in (StaticProviderRuntime(provider), IssueCacheKeyRuntime(
            "prompt_cache_key", "1068"
        )):
            merged.update(runtime.extra_body())
        self.assertEqual(
            merged,
            {
                "chat_template_kwargs": {"enable_thinking": True},
                "prompt_cache_key": "1068",
            },
        )

    def test_engine_value_wins_a_static_body_collision(self):
        """The per-issue runtime is last, so its body entry overwrites a static one."""
        os.environ["LLM_SESSION_ID"] = "1068"
        provider = ProviderConfig(
            api_url="https://api.mistral.ai/v1",
            api_token_env="MISTRAL_API_KEY",
            cache_key_param="prompt_cache_key",
        )
        body: dict = {"prompt_cache_key": "static"}
        for runtime in build_provider_runtime(provider, os.environ):
            body.update(runtime.extra_body())
        self.assertEqual(body["prompt_cache_key"], "1068")

    def test_litellm_extra_body_is_a_valid_sdk_llm_field(self):
        """Guard against a typo breaking ``LLM(**llm_config)`` at runtime."""
        from openhands.sdk import LLM

        self.assertIn("litellm_extra_body", LLM.model_fields)


class TestMessageNameDrop(unittest.TestCase):
    """Provider opt-in that strips the message-level ``name`` field (issue #881).

    ``drop_params`` never touches fields nested in ``messages``; a gateway that
    rejects the tool-message ``name`` needs the dedicated pre-call seam.
    """

    CONFIG_TEMPLATE = """\
providers:
  opencode_go:
    api_url: "https://opencode.ai/zen/go/v1"
    api_token_env: "OPENCODE_GO_API_KEY"
    drop_message_name: true
llms:
  go-model:
    model_path: "openai/deepseek/deepseek-v4.1-flash"
    provider: "${providers.opencode_go}"
agents:
  dev:
    model: "${llms.go-model}"
    temperature: 0.1
    prompt_file: "dev.md"
"""

    _ENV_KEYS = (
        "AGENT_CONFIG_PATH",
        "ROLE_NAME",
        "CONFIG_MODEL",
        "CONFIG_TEMPERATURE",
        "OPENCODE_GO_API_KEY",
    )

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._ENV_KEYS}
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, "agent-config.yaml")
        os.environ["ROLE_NAME"] = "dev"
        os.environ["AGENT_CONFIG_PATH"] = self._config_path
        os.environ["OPENCODE_GO_API_KEY"] = "secret"
        self._saved_stripper = _rlc._MESSAGE_NAME_STRIPPER
        if self._saved_stripper in litellm.callbacks:
            litellm.callbacks.remove(self._saved_stripper)

    def tearDown(self):
        stripper = _rlc._MESSAGE_NAME_STRIPPER
        if stripper is not None and stripper in litellm.callbacks:
            litellm.callbacks.remove(stripper)
        _rlc._MESSAGE_NAME_STRIPPER = self._saved_stripper
        if self._saved_stripper is not None and self._saved_stripper not in litellm.callbacks:
            litellm.callbacks.append(self._saved_stripper)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _resolve(self, config: str = None):
        with open(self._config_path, "w") as f:
            f.write(self.CONFIG_TEMPLATE if config is None else config)
        return resolve_llm_config(None, "prompt")[0]

    def test_strip_message_name_is_pure_and_role_scoped(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "c1", "name": "terminal", "content": "out"},
            {"role": "assistant", "content": "ok"},
        ]
        result = _rlc._strip_message_name(messages)
        self.assertNotIn("name", result[1])
        self.assertEqual(result[1]["tool_call_id"], "c1")
        self.assertEqual(result[0], messages[0])
        self.assertEqual(result[2], messages[2])
        # Input untouched (pure).
        self.assertIn("name", messages[1])

    def test_pre_call_hook_rewrites_the_outgoing_body(self):
        stripper = _rlc._MessageNameStripper()
        complete_input = {
            "model": "x",
            "messages": [
                {"role": "tool", "tool_call_id": "c1", "name": "terminal", "content": "out"},
            ],
        }
        stripper.log_pre_api_call(
            model="x",
            messages=complete_input["messages"],
            kwargs={"additional_args": {"complete_input_dict": complete_input}},
        )
        self.assertNotIn("name", complete_input["messages"][0])

    def test_pre_call_hook_tolerates_missing_payload(self):
        _rlc._MessageNameStripper().log_pre_api_call(
            model="x", messages=[], kwargs={}
        )

    def test_opt_in_registers_the_stripper_idempotently(self):
        self._resolve()
        self.assertIn(_rlc._MESSAGE_NAME_STRIPPER, litellm.callbacks)
        self._resolve()
        self.assertEqual(
            litellm.callbacks.count(_rlc._MESSAGE_NAME_STRIPPER), 1
        )

    def test_provider_without_opt_in_registers_nothing(self):
        self._resolve(self.CONFIG_TEMPLATE.replace(
            "    drop_message_name: true\n", ""
        ))
        self.assertIsNone(_rlc._MESSAGE_NAME_STRIPPER)


class TestThinkingEnabledDisablesReasoning(unittest.TestCase):
    """``thinking_enabled: false`` must remove every reasoning-enabling signal (#941).

    Regression: the llm-level flag reached ``chat_template_kwargs`` but the SDK's
    independent top-level ``reasoning_effort='high'`` default still reasoned.
    """

    CONFIG_TEMPLATE = """\
providers:
  test_provider:
    api_url: "http://test/v1"
    api_token_env: "TEST_LLM_API_KEY"
llms:
  test-model:
    model_path: "test/test-model"
    provider: ${providers.test_provider}
    thinking_enabled: false
    preserve_thinking: true
agents:
  dev:
    model: ${llms.test-model}
    prompt_file: "dev.md"
"""

    def setUp(self):
        self._env_keys = [
            "LLM_REASONING",
            "TEST_LLM_API_KEY",
            "CONFIG_COMPLEXITY",
            "AGENT_CONFIG_PATH",
            "ROLE_NAME",
        ]
        self._saved = {k: os.environ.get(k) for k in self._env_keys}
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, "agent-config.yaml")
        os.environ["TEST_LLM_API_KEY"] = "test-key"
        os.environ["ROLE_NAME"] = "dev"
        os.environ["AGENT_CONFIG_PATH"] = self._config_path
        os.environ.pop("LLM_REASONING", None)
        os.environ.pop("CONFIG_COMPLEXITY", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _resolve(self, config_yaml=None):
        with open(self._config_path, "w") as f:
            f.write(config_yaml or self.CONFIG_TEMPLATE)
        cfg, _, _ = resolve_llm_config(None, "prompt")
        return cfg

    def _emitted_options(self, cfg):
        """The options the SDK will actually emit for the resolved config."""
        from openhands.sdk import LLM
        from openhands.sdk.llm.options.chat_options import select_chat_options

        return select_chat_options(LLM(**cfg), {}, has_tools=True)

    def test_enable_thinking_false_reaches_extra_body(self):
        cfg = self._resolve()
        kwargs = cfg["litellm_extra_body"]["chat_template_kwargs"]
        self.assertIs(kwargs["enable_thinking"], False)

    def test_thinking_off_sends_reasoning_effort_none(self):
        """The resolved config must carry the ``"none"`` kill switch."""
        cfg = self._resolve()
        self.assertEqual(cfg["reasoning_effort"], "none")

    def test_reasoning_effort_none_reaches_the_wire(self):
        """End-to-end: the SDK emits the ``"none"`` kill switch, not nothing.

        Uses a model the SDK knows supports ``reasoning_effort`` — for models that
        do not, the SDK's model-feature gate correctly drops the param.
        """
        cfg = self._resolve(
            self.CONFIG_TEMPLATE.replace(
                'model_path: "test/test-model"',
                'model_path: "openai/deepseek-v4.1-flash"',
            )
        )
        options = self._emitted_options(cfg)
        self.assertEqual(options.get("reasoning_effort"), "none")
        self.assertNotIn("thinking", options)

    def test_tier_override_can_reenable_thinking(self):
        """``complexity_overrides.low.thinking_enabled: true`` wins over llm-false.

        Regression: the tier-level key was silently dropped by the schema.
        """
        os.environ["CONFIG_COMPLEXITY"] = "low"
        cfg = self._resolve(
            self.CONFIG_TEMPLATE.replace(
                '    prompt_file: "dev.md"\n',
                '    prompt_file: "dev.md"\n'
                "    complexity_overrides:\n"
                "      low:\n"
                "        thinking_enabled: true\n",
            )
        )
        kwargs = cfg["litellm_extra_body"]["chat_template_kwargs"]
        self.assertIs(kwargs["enable_thinking"], True)
        self.assertNotIn("reasoning_effort", cfg)


class TestRealOpencodeConfigRoundTrip(unittest.TestCase):
    """Resolve the shipped ``opencode`` agent-config as written (#941).

    Locks the exact production snippet that reproduced the bug: an llm-level
    ``thinking_enabled: false`` plus an ``architect@low`` exception.
    """

    ROLE = "dev"
    CONFIG = (
        Path(__file__).parents[7]
        / ".nebuko"
        / "agents"
        / "agent-config-opencode.yaml"
    )

    def setUp(self):
        self._env_keys = ["OPENCODE_GO_API_KEY", "CONFIG_COMPLEXITY", "ROLE_NAME"]
        self._saved = {k: os.environ.get(k) for k in self._env_keys}
        os.environ["OPENCODE_GO_API_KEY"] = "test-key"
        os.environ["ROLE_NAME"] = self.ROLE
        os.environ["AGENT_CONFIG_PATH"] = str(self.CONFIG)
        os.environ.pop("CONFIG_COMPLEXITY", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _resolve(self, role, complexity):
        os.environ["ROLE_NAME"] = role
        os.environ["CONFIG_COMPLEXITY"] = complexity
        cfg, _, _ = resolve_llm_config(None, "prompt")
        return cfg

    def test_config_file_exists(self):
        self.assertTrue(self.CONFIG.is_file(), f"missing {self.CONFIG}")

    def test_dev_thinking_is_fully_disabled(self):
        """``dev`` on the thinking-off llm sends the ``"none"`` kill switch."""
        cfg = self._resolve("dev", "medium")
        kwargs = cfg["litellm_extra_body"]["chat_template_kwargs"]
        self.assertIs(kwargs["enable_thinking"], False)
        self.assertEqual(cfg["reasoning_effort"], "none")

    def test_architect_low_exception_reenables_thinking(self):
        """The tier override is no longer silently dropped (issue #941).

        The llm-level ``reasoning_effort: "none"`` kill switch (#958) still
        rides the top-level config: the tier override re-enables thinking via
        ``chat_template_kwargs`` but does not lift the explicit effort.
        """
        cfg = self._resolve("architect", "low")
        kwargs = cfg["litellm_extra_body"]["chat_template_kwargs"]
        self.assertIs(kwargs["enable_thinking"], True)
        self.assertEqual(cfg["reasoning_effort"], "none")


if __name__ == "__main__":
    unittest.main()
