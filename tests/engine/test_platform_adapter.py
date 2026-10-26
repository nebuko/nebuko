"""Spec-scenario tests for the platform port (issue #590, plan section 3.2).

The port is the single seam the engine is allowed to know about, so these
tests pin its *surface* and the local/GitHub implementations' conformance.
"""

from __future__ import annotations

import dataclasses

import pytest

from nebuko.adapters import build_adapter
from nebuko.adapters.base import HistoryProvider, PlatformAdapter, Poster
from nebuko.adapters.github import GitHubAdapter
from nebuko.adapters.local import LocalAdapter

PORT_METHODS = (
    "read_trigger",
    "load_history",
    "post",
    "capabilities",
    "edit_comment",
    "add_reaction",
    "load_history_page",
    "load_state",
    "save_state",
    "artifact_dir",
    "write_artifact",
)


class TestPlatformAdapterProtocol:
    @pytest.mark.parametrize("name", PORT_METHODS)
    def test_declares_the_port_method(self, name):
        assert hasattr(PlatformAdapter, name), f"port must expose {name}"

    def test_local_adapter_conforms(self):
        assert issubclass(LocalAdapter, PlatformAdapter)

    def test_github_adapter_conforms(self):
        assert issubclass(GitHubAdapter, PlatformAdapter)

    def test_base_module_exports_the_seam_callables(self):
        import nebuko.adapters.base as base

        assert set(base.__all__) == {
            "HistoryPage",
            "HistoryProvider",
            "PlatformAdapter",
            "PostedComment",
            "Poster",
            "PosterCapabilities",
        }
        assert Poster is not None
        assert HistoryProvider is not None


class TestBuildAdapter:
    def test_selects_the_local_adapter(self, tmp_path):
        adapter = build_adapter("local", run_dir=tmp_path)
        assert isinstance(adapter, LocalAdapter)

    def test_selects_the_github_adapter(self):
        adapter = build_adapter("github", repo="olivier-norture/git-flow-ai")
        assert isinstance(adapter, GitHubAdapter)

    def test_rejects_an_unknown_kind(self, tmp_path):
        with pytest.raises(ValueError):
            build_adapter("bitbucket", run_dir=tmp_path)


class TestAdapterContract:
    """Only data classes cross the port, so adapters must not leak filesystem objects."""

    def test_local_adapter_returns_model_instances(self, tmp_path):
        from nebuko.engine.models import Trigger

        adapter = LocalAdapter(
            tmp_path / "runs" / "run-1",
            trigger=Trigger(author="a", body="b", type="issue_comment", number=1),
        )
        trigger = adapter.read_trigger()
        assert dataclasses.is_dataclass(trigger)
        assert not isinstance(trigger, (str, dict))
