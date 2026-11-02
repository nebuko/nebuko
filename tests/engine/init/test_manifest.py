"""Manifest contract for ``nebuko init`` (issue #1272)."""

from __future__ import annotations

import json

import pytest

from nebuko.engine.init.manifest import ManifestError, load_packaged_manifest, parse_manifest

REPO_ROOT_PROVIDERS = {
    "openrouter": (
        "defaults/agents/agent-config-openrouter.yaml",
        ("OPENROUTER_API_KEY",),
    ),
    "commandcode": (
        "defaults/agents/agent-config-commandcode.yaml",
        ("COMMANDCODE_API_KEY",),
    ),
    "opencode": (
        "defaults/agents/agent-config-opencode.yaml",
        ("OPENCODE_GO_API_KEY",),
    ),
    "mistral": (
        "defaults/agents/agent-config-mistral.yaml",
        ("MISTRAL_API_KEY",),
    ),
}


# Given a manifest with the four shipped providers, when parsed, then the
# provider set matches providers.py verbatim (paths and secret names included)
def test_providers_match_providers_py():
    manifest = load_packaged_manifest()
    got = {p.name: (p.template_path, p.secret_names) for p in manifest.providers.values()}
    assert got == REPO_ROOT_PROVIDERS


# Given a packaged manifest, when loaded, then the caller template is
# relative to the manifest's own directory and template_path is repo-root-relative
def test_packaged_manifest_paths():
    manifest = load_packaged_manifest()
    assert manifest.caller_workflow_template == "nebuko-caller.yaml"
    assert manifest.workflow_filename == "nebuko.yaml"
    for spec in manifest.providers.values():
        assert not spec.template_path.startswith("/")


# Given a valid manifest JSON, when parsed, then a Manifest is returned
def test_parse_manifest_returns_manifest():
    raw = json.dumps(
        {
            "schema_version": 1,
            "workflow_filename": "nebuko.yaml",
            "caller_workflow_template": "nebuko-caller.yaml",
            "providers": {
                "mistral": {
                    "description": "Mistral",
                    "template_path": "defaults/agents/agent-config-mistral.yaml",
                    "secret_names": ["MISTRAL_API_KEY"],
                }
            },
        }
    )
    manifest = parse_manifest(raw)
    assert manifest.providers["mistral"].secret_names == ("MISTRAL_API_KEY",)


# Given a manifest with an unknown field, when parsed, then ManifestError is raised
def test_parse_manifest_rejects_unknown_fields():
    raw = json.dumps(
        {
            "schema_version": 1,
            "workflow_filename": "nebuko.yaml",
            "caller_workflow_template": "nebuko-caller.yaml",
            "surprise": True,
            "providers": {},
        }
    )
    with pytest.raises(ManifestError):
        parse_manifest(raw)


# Given a manifest with no providers, when parsed, then ManifestError is raised
def test_parse_manifest_rejects_empty_providers():
    raw = json.dumps(
        {
            "schema_version": 1,
            "workflow_filename": "nebuko.yaml",
            "caller_workflow_template": "nebuko-caller.yaml",
            "providers": {},
        }
    )
    with pytest.raises(ManifestError):
        parse_manifest(raw)
