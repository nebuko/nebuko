"""The packaged init manifest (issue #1272).

``caller_workflow_template`` is relative to the manifest's own directory; the
CLI resolves it against the packaged copy's directory and the Worker against
the repo checkout at the resolved release tag. ``template_path`` stays
repo-root-relative because it is always fetched at ``pin_ref``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any


class ManifestError(ValueError):
    """Raised for unknown manifest fields or empty providers."""


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    description: str
    secret_names: tuple[str, ...]  # provider secrets only; GH_PAT is CLI-only via pat_mode
    template_path: str  # REAL path copied verbatim from providers.py; fetched at pin_ref, never moved


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    workflow_filename: str  # "nebuko.yaml"
    caller_workflow_template: str  # relative to the manifest's own directory, e.g. "nebuko-caller.yaml"
    providers: dict[str, ProviderSpec]


def parse_manifest(raw: str) -> Manifest:
    # @stub-protected: JSON parse + strict schema validation; ManifestError on unknown fields / empty providers
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ManifestError(f"invalid manifest JSON: {error}") from error
    if not isinstance(document, dict):
        raise ManifestError("manifest must be a JSON object")
    _check_fields(document, {"schema_version", "workflow_filename", "caller_workflow_template", "providers"})
    schema_version = document["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ManifestError("schema_version must be an integer")
    for field in ("workflow_filename", "caller_workflow_template"):
        if not isinstance(document[field], str) or not document[field]:
            raise ManifestError(f"{field} must be a non-empty string")
    providers_raw = document["providers"]
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise ManifestError("providers must be a non-empty object")
    providers: dict[str, ProviderSpec] = {}
    for name, spec in providers_raw.items():
        if not isinstance(spec, dict):
            raise ManifestError(f"provider {name!r} must be an object")
        _check_fields(spec, {"description", "template_path", "secret_names"})
        secret_names = spec["secret_names"]
        if not isinstance(secret_names, list) or not all(isinstance(n, str) for n in secret_names):
            raise ManifestError(f"provider {name!r}: secret_names must be a list of strings")
        providers[name] = ProviderSpec(
            name=name,
            description=spec["description"],
            secret_names=tuple(secret_names),
            template_path=spec["template_path"],
        )
    return Manifest(
        schema_version=schema_version,
        workflow_filename=document["workflow_filename"],
        caller_workflow_template=document["caller_workflow_template"],
        providers=providers,
    )


def load_packaged_manifest() -> Manifest:
    # @stub-protected: importlib.resources read of the packaged copy under nebuko.engine.init; no network
    resource = files("nebuko.engine.init.defaults").joinpath("nebuko-init.json")
    return parse_manifest(resource.read_text(encoding="utf-8"))


def _check_fields(document: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(document) - allowed
    if unknown:
        raise ManifestError(f"unknown manifest field(s): {', '.join(sorted(unknown))}")
    missing = allowed - set(document)
    if missing:
        raise ManifestError(f"missing manifest field(s): {', '.join(sorted(missing))}")
