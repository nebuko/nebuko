"""The packaged init manifest (issue #1272).

``caller_workflow_template`` is relative to the manifest's own directory; the
CLI resolves it against the packaged copy's directory and the Worker against
the repo checkout at the resolved release tag. ``template_path`` stays
repo-root-relative because it is always fetched at ``pin_ref``.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    raise NotImplementedError


def load_packaged_manifest() -> Manifest:
    # @stub-protected: importlib.resources read of the packaged copy under nebuko.engine.init; no network
    raise NotImplementedError
