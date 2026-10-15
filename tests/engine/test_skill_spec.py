"""The skill-attachment entry grammar (issue #955).

One grammar serves both levels: the agent's ``skills:`` and the workflow step's
``skill:``. Each entry is either the legacy bare name (``- planning``) or the
mode-object shape (``- inject: [...]`` / ``- discover: [...]``), normalized to
``mode`` + ``names``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import AgentConfig, LLMConfig, ProviderConfig, SkillSpec, WorkflowStep


def _llm() -> LLMConfig:
    return LLMConfig(
        model_path="hosted_vllm/m",
        provider=ProviderConfig(api_url="http://example.test/v1", api_token_env="T"),
    )


class TestEntryCoercion:
    def test_bare_name_is_inject(self):
        spec = SkillSpec.model_validate("planning")

        assert (spec.mode, spec.names) == ("inject", ["planning"])

    def test_inject_mapping(self):
        spec = SkillSpec.model_validate({"inject": ["a", "b"]})

        assert (spec.mode, spec.names) == ("inject", ["a", "b"])

    def test_discover_mapping(self):
        spec = SkillSpec.model_validate({"discover": ["complexity"]})

        assert (spec.mode, spec.names) == ("discover", ["complexity"])

    def test_normalized_object_form(self):
        spec = SkillSpec.model_validate({"mode": "discover", "names": ["x"]})

        assert (spec.mode, spec.names) == ("discover", ["x"])

    def test_scalar_mode_value_is_wrapped(self):
        spec = SkillSpec.model_validate({"inject": "solo"})

        assert spec.names == ["solo"]

    def test_unknown_key_is_rejected(self):
        with pytest.raises(ValidationError):
            SkillSpec.model_validate({"inject": ["a"], "extra": True})

    def test_both_modes_in_one_entry_is_rejected(self):
        with pytest.raises(ValidationError):
            SkillSpec.model_validate({"inject": ["a"], "discover": ["b"]})

    def test_empty_name_list_is_rejected(self):
        with pytest.raises(ValidationError):
            SkillSpec.model_validate({"inject": []})

    def test_unknown_mode_is_rejected(self):
        with pytest.raises(ValidationError):
            SkillSpec.model_validate({"mode": "watch", "names": ["a"]})

    def test_non_string_non_mapping_is_rejected(self):
        with pytest.raises(ValidationError):
            SkillSpec.model_validate(42)

    def test_names_must_be_strings(self):
        with pytest.raises(ValidationError):
            SkillSpec.model_validate({"inject": [1]})


class TestParseAll:
    def test_mixed_shapes_parse_in_order(self):
        specs = SkillSpec.parse_all(
            ["bare", {"inject": ["a"]}, {"discover": ["b", "c"]}]
        )

        assert [(spec.mode, spec.names) for spec in specs] == [
            ("inject", ["bare"]),
            ("inject", ["a"]),
            ("discover", ["b", "c"]),
        ]

    def test_none_and_empty_are_empty(self):
        assert SkillSpec.parse_all(None) == []
        assert SkillSpec.parse_all([]) == []

    def test_unparsable_entries_are_skipped(self):
        specs = SkillSpec.parse_all(["ok", {"bogus": 1}, 7, {"discover": ["y"]}])

        assert [spec.names for spec in specs] == [["ok"], ["y"]]


class TestIterNames:
    def test_walks_every_entry_shape(self):
        raw = [
            "bare",
            {"inject": ["a"]},
            {"discover": ["b", "c"]},
            {"mode": "inject", "names": ["z"]},
            SkillSpec(mode="discover", names=["obj"]),
        ]

        assert list(SkillSpec.iter_names(raw)) == ["bare", "a", "b", "c", "z", "obj"]

    def test_ignores_entries_it_cannot_read(self):
        assert list(SkillSpec.iter_names([42, {"bogus": 1}, {"inject": [1]}])) == []

    def test_none_is_empty(self):
        assert list(SkillSpec.iter_names(None)) == []


class TestModelWiring:
    def test_agent_config_accepts_both_shapes(self):
        agent = AgentConfig(
            model=_llm(),
            prompt_file="p.md",
            skills=["legacy", {"inject": ["a"]}, {"discover": ["b"]}],
        )

        assert [(spec.mode, spec.names) for spec in agent.skills] == [
            ("inject", ["legacy"]),
            ("inject", ["a"]),
            ("discover", ["b"]),
        ]

    def test_step_accepts_both_shapes(self):
        step = WorkflowStep(name="implement", agent="dev", skill=[{"discover": ["b"]}])

        assert [(spec.mode, spec.names) for spec in step.skill] == [
            ("discover", ["b"])
        ]

    def test_step_skill_defaults_to_none(self):
        assert WorkflowStep(name="implement", agent="dev").skill is None

    def test_agent_config_rejects_a_malformed_entry(self):
        with pytest.raises(ValidationError):
            AgentConfig(model=_llm(), prompt_file="p.md", skills=[{"inject": []}])
