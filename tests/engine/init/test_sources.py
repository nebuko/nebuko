"""Template-source contract for ``nebuko init`` (issue #991)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nebuko.engine.init.ports import SetupError
from nebuko.engine.init.sources import FileTemplateSource, HttpTemplateSource

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_file_source_reads_the_checkout():
    source = FileTemplateSource(REPO_ROOT)
    text = source.fetch("defaults/agents/agent-config-openrouter.yaml", "v0.0.2")
    assert "OPENROUTER_API_KEY" in text


def test_file_source_reports_a_missing_file():
    with pytest.raises(SetupError) as excinfo:
        FileTemplateSource(REPO_ROOT).fetch("defaults/agents/nope.yaml", "v0.0.2")
    assert "nope.yaml" in str(excinfo.value)


def test_http_source_builds_the_raw_url_at_the_pinned_ref():
    seen: list[str] = []

    def opener(url: str) -> str:
        seen.append(url)
        return "content"

    source = HttpTemplateSource(opener=opener)
    assert source.fetch("defaults/agents/agent-config-opencode.yaml", "v1.2.3") == "content"
    assert seen == [
        "https://raw.githubusercontent.com/nebuko/nebuko/v1.2.3/"
        "defaults/agents/agent-config-opencode.yaml"
    ]


def test_http_source_strips_a_trailing_slash_from_the_base_url():
    seen: list[str] = []

    def opener(url: str) -> str:
        seen.append(url)
        return "content"

    source = HttpTemplateSource(base_url="https://example.test/base/", opener=opener)
    assert source.fetch("a.yaml", "v1") == "content"
    assert seen == ["https://example.test/base/v1/a.yaml"]


def test_http_source_wraps_transport_failures():
    def opener(url: str) -> str:
        raise OSError("no route to host")

    with pytest.raises(SetupError) as excinfo:
        HttpTemplateSource(opener=opener).fetch("a.yaml", "v1")
    assert "no route to host" in str(excinfo.value)
