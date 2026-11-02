"""Manifest contract for ``nebuko init`` (issue #1272)."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest

from nebuko.engine.init.manifest import (
    Manifest,
    ManifestError,
    load_packaged_manifest,
    parse_manifest,
)
from nebuko.engine.init.providers import PROVIDER_PROFILES


# Given the packaged manifest, when loaded, then a valid Manifest with
# non-empty providers is returned (pyproject package-data gate)
def test_load_packaged_manifest():
    manifest = load_packaged_manifest()
    assert isinstance(manifest, Manifest)
    assert manifest.providers


# Given the four shipped providers, when the manifest is compared against
# providers.py, then names, secret_names and template_path match verbatim
def test_providers_match_providers_py():
    manifest = load_packaged_manifest()
    expected = {p.name: (p.secret_names, p.template_path) for p in PROVIDER_PROFILES}
    got = {p.name: (p.secret_names, p.template_path) for p in manifest.providers.values()}
    assert got == expected


# Given a packaged manifest, when its paths are checked, then the caller
# template resolves to an existing file in the manifest's own directory and
# every template_path is repo-root-relative
def test_packaged_manifest_paths():
    manifest = load_packaged_manifest()
    assert manifest.caller_workflow_template == "nebuko-caller.yaml"
    resource = files("nebuko.engine.init").joinpath(manifest.caller_workflow_template)
    assert resource.is_file()
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
